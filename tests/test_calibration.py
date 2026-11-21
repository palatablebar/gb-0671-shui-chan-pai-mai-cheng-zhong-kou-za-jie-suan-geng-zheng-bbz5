from decimal import Decimal
from copy import deepcopy

import pytest

from app.engine import evaluate
from app.errors import BusinessError
from app.modules.calibration.rules import build_adjustments, apply
from app import publish


@pytest.fixture
def calib():
    return evaluate({'dataset': 'calib'})


def by_ticket(rows):
    return {row['ticket']: row for row in rows}


def test_reweigh_beats_factor(calib):
    rows = by_ticket(calib['adjustments'])
    # 00002 有现场复秤 90kg，即使落在校准范围内也优先复秤。
    assert rows['00002']['baseline'] == 'RE_WEIGH'
    assert rows['00002']['adjusted_net_kg'] == '90.0'


def test_factor_applied_when_no_reweigh(calib):
    rows = by_ticket(calib['adjustments'])
    assert rows['00008']['baseline'] == 'CALIBRATION_FACTOR'
    # 净重 98 × 0.98 = 96.04，扣杂 5% 后 91.238kg × 12.30 = 1122.23
    assert rows['00008']['adjusted_net_kg'] == '96.04'
    assert rows['00008']['adjusted_amount'] == '1122.23'
    assert rows['00008']['adjustment'] == '-22.90'


def test_payable_excludes_uncovered(calib):
    tickets = {row['ticket'] for row in calib['adjustments']}
    assert '00019' not in tickets and '00020' not in tickets
    pending = by_ticket(calib['calibration_pending'])
    assert pending['00019']['code'] == 'CALIBRATION_UNCOVERED'
    assert pending['00019']['adjusted_amount'] == ''
    assert pending['00019']['decision'] == '待复秤'


def test_pending_conflicting_reweigh(calib):
    pending = by_ticket(calib['calibration_pending'])
    assert pending['00007']['code'] == 'REWEIGH_CONFLICT'
    assert pending['00006']['code'] == 'WEIGHT_EVIDENCE_CONFLICT'
    assert pending['00004']['code'] == 'REWEIGH_VERSION_MISMATCH'
    assert all(row['decision'] == '待复秤' for row in calib['calibration_pending'])


def test_payable_money_only_counts_payable(calib):
    total = sum(Decimal(row['adjusted_amount']) for row in calib['adjustments'])
    assert calib['balance']['amount'] == f'{total:.2f}'
    assert calib['balance']['pending_tickets'] == len(calib['calibration_pending'])
    assert calib['balance']['money_balanced'] is True


def test_archive_blocked_by_pending(calib):
    assert calib['archive']['status'] == '待处理'
    all_covered = evaluate({'dataset': 'calib', 'calibration': {
        'id': 'C2', 'scale': '3号秤', 'factor': '1', 'tickets': [f'{i:05d}' for i in range(1, 21)]}})
    assert all_covered['calibration_pending'] == []
    assert all_covered['archive']['status'] == '可归档'


def test_no_evidence_is_passthrough():
    s = evaluate()
    assert s['calibration_pending'] == []
    assert all(row['code'] == 'UNAFFECTED' for row in s['adjustments'])
    assert s['balance']['due'] == '22540.41'


def test_invalid_factor_is_uncovered(calib):
    s = evaluate({'dataset': 'calib', 'calibration': {'id': 'X', 'scale': '3号秤', 'factor': 'abc', 'tickets': ['00001']}})
    assert s['adjustments'] == []
    assert len(s['calibration_pending']) == len(s['final'])
    assert any('校准系数无法解析' in row['detail'] for row in s['calibration_pending'])


def test_rerun_same_calibration_same_batch_and_result(calib):
    again = evaluate({'dataset': 'calib'})
    assert again['manifest']['run_id'] == calib['manifest']['run_id']
    assert again['adjustments'] == calib['adjustments']


def test_publish_idempotent(tmp_path, calib):
    path = str(tmp_path / 'pub.sqlite')
    first = publish.publish(path, calib)
    second = publish.publish(path, calib)
    assert first['id'] == second['id']
    assert second['reused'] is True
    assert len(publish.history(path, calib)) == 1


def test_publish_nothing_raises(tmp_path):
    path = str(tmp_path / 'pub.sqlite')
    with pytest.raises(BusinessError) as error:
        publish.publish(path, evaluate())
    assert error.value.code == 'NOTHING_TO_PUBLISH'


def test_interrupted_publish_keeps_old_version(tmp_path, calib):
    path = str(tmp_path / 'pub.sqlite')
    old = publish.publish(path, calib)
    changed = evaluate({'dataset': 'calib', 'calibration': {'id': 'CAL-2', 'scale': '3号秤', 'factor': '0.9',
                                                             'tickets': ['00001']}})

    def explode():
        raise RuntimeError('模拟发布中断')

    publish._fault_hooks['before_commit'] = [explode]
    try:
        with pytest.raises(RuntimeError):
            publish.publish(path, changed)
    finally:
        publish._fault_hooks.clear()

    history = publish.history(path, calib)
    assert [row['id'] for row in history] == [old['id']]
    assert history[0]['status'] == '已发布'
    active = publish.active(path, calib)
    assert active['id'] == old['id']
    assert active['adjustment'] == old['adjustment']


def test_restore_old_version(tmp_path, calib):
    path = str(tmp_path / 'pub.sqlite')
    first = publish.publish(path, calib)
    second = publish.publish(path, evaluate({'dataset': 'calib', 'calibration': {
        'id': 'CAL-2', 'scale': '3号秤', 'factor': '0.95', 'tickets': ['00001']}}))
    assert second['version_no'] == 2
    restored = publish.restore(path, first['id'])
    assert restored['status'] == '已发布'
    history = {row['id']: row for row in publish.history(path, calib)}
    assert history[first['id']]['status'] == '已发布'
    assert history[second['id']]['status'] == '已撤回'
    # 历史不删除，旧版仍可回看。
    assert len(history) == 2


def test_publish_does_not_touch_source_evidence(tmp_path, calib):
    from app import store
    path = str(tmp_path / 'pub.sqlite')
    store.save(path, calib)
    before = store.get(path, calib['manifest']['run_id'])
    publish.publish(path, calib)
    after = store.get(path, calib['manifest']['run_id'])
    assert after['manifest']['sha256'] == before['manifest']['sha256']
    assert after['input_snapshot'] == before['input_snapshot']
    assert after['evidence'] == before['evidence']
    assert after['records'] == before['records']


def test_duplicate_weight_evidence_kept_pending():
    final = [{'ticket': '00001', 'version': 1, 'fish': '黄鱼', 'price': '10', 'rate': '0', 'net_kg': '100',
              'amount': '1000.00', 'paid': '0', 'lines': [1, 2], 'gross_kg': '110'}]
    rows, pending, _ = build_adjustments(final, {'calibration': None, 'reweighs': [
        {'ticket': '00001', 'version': 1, 'net_kg': '99'},
        {'ticket': '00001', 'version': 1, 'net_kg': '101'}]})
    assert rows == []
    assert pending[0]['code'] == 'REWEIGH_CONFLICT'


def test_negative_after_factor_kept_pending():
    final = [{'ticket': '00001', 'version': 1, 'fish': '黄鱼', 'price': '10', 'rate': '0', 'net_kg': '1',
              'amount': '10.00', 'paid': '0', 'lines': [1, 2], 'gross_kg': '11'}]
    calibration = {'id': 'X', 'scale': '3号秤', 'factor': '-1', 'tickets': ['00001']}
    rows, pending, parsed = build_adjustments(final, {'calibration': calibration, 'reweighs': []})
    assert rows == [] and parsed is None and pending[0]['code'] == 'CALIBRATION_UNCOVERED'


def test_api_publish_and_restore(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'api.sqlite'))
    client = TestClient(main.app)
    run = client.post('/api/runs', json={'dataset': 'calib'}).json()
    rid = run['manifest']['run_id']
    first = client.post(f'/api/runs/{rid}/calibration/publish', json={})
    assert first.status_code == 200
    again = client.post(f'/api/runs/{rid}/calibration/publish', json={})
    assert again.json()['reused'] is True
    assert again.json()['id'] == first.json()['id']
    page = client.get(f'/calibration?run_id={rid}')
    assert page.status_code == 200
    assert 'CALIBRATION_FACTOR' in page.text and 'RE_WEIGH' in page.text
    assert '待复秤' in page.text
    # 改用另一份校准记录重新评估：新 run_id，但属于同一来源，形成 v2。
    other = client.post('/api/runs', json={'dataset': 'calib', 'calibration': {
        'id': 'CAL-2', 'scale': '3号秤', 'factor': '0.95', 'tickets': ['00001']}}).json()
    assert other['manifest']['run_id'] != rid
    second = client.post(f"/api/runs/{other['manifest']['run_id']}/calibration/publish", json={})
    assert second.status_code == 200
    assert second.json()['version_no'] == 2
    listing = client.get(f'/api/runs/{rid}/calibrations').json()
    assert listing['active']['version_no'] == 2
    restored = client.post(f"/api/runs/{rid}/calibration/restore/{first.json()['id']}", json={})
    assert restored.status_code == 200
    assert restored.json()['status'] == '已发布'
    listing = client.get(f'/api/runs/{rid}/calibrations').json()
    assert listing['active']['id'] == first.json()['id']
    assert len(listing['history']) == 2


def test_api_publish_without_effect_refused(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'api2.sqlite'))
    client = TestClient(main.app)
    rid = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = client.post(f'/api/runs/{rid}/calibration/publish', json={})
    assert response.status_code == 422
    assert response.json()['code'] == 'NOTHING_TO_PUBLISH'


def test_evidence_survives_rerun(calib):
    from app import store
    import tempfile, pathlib
    path = str(pathlib.Path(tempfile.mkdtemp()) / 'r.sqlite')
    store.save(path, calib)
    snapshot = store.get(path, calib['manifest']['run_id'])['input_snapshot']
    rerun = evaluate({'dataset': 'calib'}, snapshot=snapshot)
    assert rerun['manifest']['run_id'] == calib['manifest']['run_id']
    assert rerun['adjustments'] == calib['adjustments']
    assert rerun['calibration_pending'] == calib['calibration_pending']


def test_publish_blocked_by_version_conflict(tmp_path, calib):
    path = str(tmp_path / 'pub.sqlite')
    broken = deepcopy(calib)
    broken['version_conflicts'] = [{'ticket': '00001', 'code': 'STALE_CORRECTION'}]
    with pytest.raises(BusinessError) as error:
        publish.publish(path, broken)
    assert error.value.code == 'VERSION_BLOCKED'


def test_publish_refused_when_only_pending(tmp_path):
    path = str(tmp_path / 'pub.sqlite')
    only_pending = evaluate({'dataset': 'calib', 'calibration': {'id': 'BAD', 'scale': '3号秤', 'factor': 'x',
                                                                  'tickets': ['00001']},
                             'reweighs': []})
    assert only_pending['adjustments'] == []
    with pytest.raises(BusinessError) as error:
        publish.publish(path, only_pending)
    assert error.value.code == 'NOTHING_TO_PUBLISH'


def test_same_calibration_with_extra_reweigh_forms_new_version(tmp_path, calib):
    path = str(tmp_path / 'pub.sqlite')
    first = publish.publish(path, calib)
    # 同一校准记录，但补到一条 00008 的复秤证据：结果不同，应作为新版本而不是复用。
    import json
    from app.config import ROOT
    evidence = json.loads((ROOT / 'sample_data/calibration.json').read_text())
    evidence['reweighs'].append({'ticket': '00008', 'version': 1, 'net_kg': '95.0'})
    updated = evaluate({'dataset': 'calib', 'calibration': evidence['calibration'], 'reweighs': evidence['reweighs']})
    second = publish.publish(path, updated)
    assert second['id'] != first['id']
    assert second['version_no'] == 2
    assert second['reused'] is False
