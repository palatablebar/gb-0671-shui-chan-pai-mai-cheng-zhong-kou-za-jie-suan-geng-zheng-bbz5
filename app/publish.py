"""校准调整的发布与回滚。

发布只写入 publications 表，绝不改动 runs 表里的原始快照，因此中途失败时
原始称重与成交证据不受影响；事务整体回滚，原生效版本继续有效。

发布按“原始数据来源”（原始字节的 sha256）分组：同一批称重/成交数据带不同
校准记录重新评估会产生不同 run_id，但属于同一来源，形成 v1→v2 的替代链，
旧版只转为已撤回，永远不删除。同一来源 + 同一校准记录只允许一条记录，
重跑不会重复累计调整额；曾撤回的校准再次发布时恢复原记录而不是新增版本。
"""
import base64, hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
  id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  run_id TEXT NOT NULL,
  calibration_key TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  adjustment TEXT NOT NULL,
  status TEXT NOT NULL,
  superseded_by TEXT,
  result TEXT NOT NULL,
  published_at TEXT NOT NULL,
  UNIQUE(source_key, calibration_key)
)
"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute(SCHEMA)
    return con


def source_key(result):
    """原始数据来源：只取原始字节，不含校准证据，因此同一批数据始终同组。"""
    snapshot = result.get('input_snapshot') or {}
    encoded = snapshot.get('bytes_b64')
    if not encoded:
        raise BusinessError('INPUT_SNAPSHOT_MISSING', '批次缺少源快照，无法发布')
    return hashlib.sha256(base64.b64decode(encoded)).hexdigest()[:16]


def _evidence_digest(result):
    """复秤证据指纹：同一校准但复秤补充/变更后结果不同，应形成新版本而不是复用旧版。"""
    evidence = result.get('evidence') or {}
    reweighs = sorted(evidence.get('reweighs') or [], key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return hashlib.sha256(json.dumps(reweighs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def calibration_key(result):
    """同一来源 + 同一校准记录 + 同一复秤证据 = 同一次调整，重跑复用不重复累计。"""
    evidence = result.get('evidence') or {}
    calibration = evidence.get('calibration') or {}
    ident = str(calibration.get('id') or calibration.get('scale') or 'none')
    material = {'source': source_key(result), 'calibration': ident, 'reweighs': _evidence_digest(result)}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _body(result):
    """只保存调整结果与来源摘要；原始输入快照仍只在 runs 表，发布不复制也不覆盖它。"""
    return {'manifest': result['manifest'], 'revision': result.get('revision', 1),
            'calibration': result.get('calibration'), 'balance': result.get('balance'),
            'adjustments': result.get('adjustments', []), 'pending': result.get('calibration_pending', []),
            'evidence': result.get('evidence'), 'source_key': source_key(result)}


def publish(path, result, now=None):
    rows = result.get('adjustments') or []
    if not rows:
        raise BusinessError('NOTHING_TO_PUBLISH', '没有可付款的校准调整票，待复秤票需先复秤')
    if all(row.get('code') == 'UNAFFECTED' for row in rows):
        raise BusinessError('NOTHING_TO_PUBLISH', '该批次没有受校准影响的票，无需发布')
    if result.get('version_conflicts'):
        raise BusinessError('VERSION_BLOCKED', '存在版本冲突，不能发布校准调整')
    origin = source_key(result)
    key = calibration_key(result)
    stamp = now or datetime.now(timezone.utc).isoformat()
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT * FROM publications WHERE source_key=? AND calibration_key=?', (origin, key)).fetchone()
        if existing:
            if existing['status'] == '已发布':
                con.execute('ROLLBACK')
                return _view(existing, reused=True)
            # 同一校准曾发布又撤回：不新增调整，只把原记录恢复为生效版。
            current = con.execute("SELECT * FROM publications WHERE source_key=? AND status='已发布' ORDER BY version_no DESC LIMIT 1", (origin,)).fetchone()
            if current:
                con.execute("UPDATE publications SET status='已撤回', superseded_by=? WHERE id=?", (existing['id'], current['id']))
            con.execute("UPDATE publications SET status='已发布', superseded_by=NULL WHERE id=?", (existing['id'],))
            for hook in _fault_hooks.pop('before_commit', []):
                hook()
            con.execute('COMMIT')
            view = _view(existing, reused=True)
            view['status'] = '已发布'
            view['reactivated'] = True
            return view
        previous = con.execute("SELECT * FROM publications WHERE source_key=? AND status='已发布' ORDER BY version_no DESC LIMIT 1", (origin,)).fetchone()
        version_no = (previous['version_no'] if previous else 0) + 1
        pub_id = f'{origin}-p{version_no}'
        delta = (result.get('calibration') or {}).get('adjustment_total', '0.00')
        body = json.dumps(_body(result), ensure_ascii=False)
        con.execute('INSERT INTO publications(id,source_key,run_id,calibration_key,version_no,adjustment,status,result,published_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (pub_id, origin, result['manifest']['run_id'], key, version_no, delta, '已发布', body, stamp))
        if previous:
            con.execute("UPDATE publications SET status='已撤回', superseded_by=? WHERE id=?", (pub_id, previous['id']))
        # 测试注入点：发布中途失败时，调用方收到异常，事务整体回滚，旧版继续生效。
        for hook in _fault_hooks.pop('before_commit', []):
            hook()
        con.execute('COMMIT')
    except Exception:
        _safe_rollback(con)
        raise
    finally:
        con.close()
    return get(path, pub_id)


def _safe_rollback(con):
    try:
        con.execute('ROLLBACK')
    except sqlite3.Error:
        pass


# 仅用于故障注入测试，默认不启用。
_fault_hooks = {}


def _view(row, reused=False):
    return {'id': row['id'], 'source_key': row['source_key'], 'run_id': row['run_id'],
            'version_no': row['version_no'], 'adjustment': row['adjustment'], 'status': row['status'],
            'superseded_by': row['superseded_by'], 'published_at': row['published_at'],
            'calibration_key': row['calibration_key'], 'reused': reused}


def _decode(row):
    body = json.loads(row['result'])
    view = _view(row)
    view['rows'] = body.get('adjustments', [])
    view['pending'] = body.get('pending', [])
    view['manifest'] = body.get('manifest')
    view['calibration'] = body.get('calibration')
    view['evidence'] = body.get('evidence')
    return view


def get(path, pub_id):
    con = connect(path)
    try:
        row = con.execute('SELECT * FROM publications WHERE id=?', (pub_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise BusinessError('NOT_FOUND', '发布记录不存在', 404)
    return _decode(row)


def history(path, result):
    """按原始数据来源列出全部发布版本；换用另一校准重新评估的批次也能看到同一版本链。"""
    origin = source_key(result)
    con = connect(path)
    try:
        rows = con.execute('SELECT id,source_key,run_id,version_no,adjustment,status,superseded_by,published_at,calibration_key FROM publications WHERE source_key=? ORDER BY version_no DESC', (origin,)).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def active(path, result):
    origin = source_key(result)
    con = connect(path)
    try:
        row = con.execute("SELECT * FROM publications WHERE source_key=? AND status='已发布' ORDER BY version_no DESC LIMIT 1", (origin,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return _decode(row)


def restore(path, pub_id):
    """把旧版发布恢复为生效版；当前版转为已撤回，历史不删除。"""
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        target = con.execute('SELECT * FROM publications WHERE id=?', (pub_id,)).fetchone()
        if not target:
            con.execute('ROLLBACK')
            raise BusinessError('NOT_FOUND', '发布记录不存在', 404)
        if target['status'] == '已发布':
            con.execute('ROLLBACK')
            return _view(target, reused=True)
        current = con.execute("SELECT * FROM publications WHERE source_key=? AND status='已发布' ORDER BY version_no DESC LIMIT 1", (target['source_key'],)).fetchone()
        if current:
            con.execute("UPDATE publications SET status='已撤回', superseded_by=? WHERE id=?", (pub_id, current['id']))
        con.execute("UPDATE publications SET status='已发布', superseded_by=NULL WHERE id=?", (pub_id,))
        con.execute('COMMIT')
    except Exception:
        _safe_rollback(con)
        raise
    finally:
        con.close()
    return get(path, pub_id)
