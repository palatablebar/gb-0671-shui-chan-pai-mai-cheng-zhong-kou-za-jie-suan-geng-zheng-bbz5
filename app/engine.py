from importlib import import_module
from pathlib import Path
from copy import deepcopy
import json, time, base64
from app.config import ROOT, MODULES, MAX_BYTES
from app.errors import BusinessError
from app.manifest import digest, make_manifest

DATASETS = {'dirty', 'clean', 'legacy', 'calib'}


def _sample_evidence(dataset):
    """校准批的秤台校准记录与复秤证据随样例载入，不混入其他批次。"""
    if dataset != 'calib':
        return None
    path = ROOT / 'sample_data' / 'calibration.json'
    if not path.exists():
        raise BusinessError('BAD_EVIDENCE', '校准批缺少校准证据文件')
    return json.loads(path.read_text())


def _parse_evidence(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BusinessError('BAD_CONTEXT', '校准证据须为对象')
    calibration = raw.get('calibration')
    reweighs = raw.get('reweighs', [])
    if calibration is not None and not isinstance(calibration, dict):
        raise BusinessError('BAD_CONTEXT', '校准记录须为对象')
    if not isinstance(reweighs, list) or any(not isinstance(r, dict) for r in reweighs):
        raise BusinessError('BAD_CONTEXT', '复秤证据须为对象列表')
    if calibration is None and not reweighs:
        return None
    return {'calibration': deepcopy(calibration), 'reweighs': deepcopy(reweighs)}


def evaluate(payload=None, until=None, snapshot=None):
    payload = payload or {}
    dataset = payload.get('dataset', 'dirty')
    if dataset not in DATASETS:
        raise BusinessError('BAD_DATASET', '样例名称无效')
    if not isinstance(payload.get('overrides', {}), dict):
        raise BusinessError('BAD_CONTEXT', '参数须为对象')
    if snapshot is not None:
        raw = base64.b64decode(snapshot['bytes_b64'], validate=True)
        context = deepcopy(snapshot['context'])
        evidence = deepcopy(snapshot.get('evidence'))
    else:
        raw = str(payload['csv_text']).encode() if 'csv_text' in payload else (ROOT / 'sample_data' / f'{dataset}.csv').read_bytes()
        context = json.loads((ROOT / 'sample_data/context.json').read_text())
        provided = {k: payload[k] for k in ('calibration', 'reweighs') if k in payload}
        evidence = _parse_evidence(provided if provided else _sample_evidence(dataset))
    if len(raw) > MAX_BYTES:
        raise BusinessError('TOO_LARGE', '文件超过2MB', 413)
    context.update(deepcopy(payload.get('overrides', {})))
    start = time.perf_counter()
    state = {'raw_bytes': raw, 'context': context, 'records': [], 'quarantine': [], 'counts': {}, 'views': {},
             'source': dataset, 'evidence': evidence}
    for index, key in enumerate(MODULES, 1):
        if until is not None and index > until:
            break
        import_module(f'app.modules.{key}.rules').apply(state)
    state['input_snapshot'] = {'bytes_b64': base64.b64encode(raw).decode(), 'context': deepcopy(context),
                               'evidence': deepcopy(evidence)}
    state.pop('raw_bytes', None)
    state['manifest'] = make_manifest(digest(raw, context, evidence), dataset, time.perf_counter() - start)
    return state
