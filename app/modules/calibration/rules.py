"""校准影响票的可付款调整。

取舍：复秤是直接测量，最可靠；校准记录覆盖范围内的票用统一系数整段修正，
以便在日结前补款；两者都拿不到、或两类证据互相矛盾、或校准记录没覆盖到的票，
一律留在待复秤，不进入可付款结果。

领域说明：双次称重按 净重=毛重-皮重 计价，恒定加性偏差在相减时抵消，
不会改变净重；能改变净重的校准偏差是乘性比例误差，因此校准记录用系数 factor
（>1 表示校准前读数偏轻、需上调；<1 表示偏重、需下调）。

决策顺序：
1. 复秤净重唯一、版本匹配，且与校准模型不矛盾 -> 基准 RE_WEIGH。
2. 无复秤，票在校准记录覆盖范围 -> 按 factor 整段修正，基准 CALIBRATION_FACTOR。
3. 校准未覆盖 / 复秤互相冲突 / 复秤与校准模型矛盾 / 修正后为负 -> 待处理，不可付款。
"""
from decimal import Decimal, ROUND_HALF_UP
from app.coerce import money, number
from app.errors import BusinessError

BASIS_REWEIGH = 'RE_WEIGH'
BASIS_FACTOR = 'CALIBRATION_FACTOR'
BASIS_ORIGINAL = 'ORIGINAL'
BASIS_NONE = 'UNRESOLVED'

STATUS_PAYABLE = '可付款'
STATUS_PENDING = '待复秤'

# 复秤与校准模型之间的可接受差异：绝对 0.5kg 或相对 1% 取较大者。
TOLERANCE_KG = Decimal('0.5')
TOLERANCE_RATIO = Decimal('0.01')


def _weight(value, label):
    try:
        parsed = number(value)
    except BusinessError:
        raise BusinessError('BAD_EVIDENCE', f'{label}重量无法解析')
    if parsed < 0:
        raise BusinessError('BAD_EVIDENCE', f'{label}重量不能为负')
    return parsed


def _parse_calibration(calibration):
    """校准记录必须能界定影响范围；缺字段就视为未覆盖，不猜。"""
    if not isinstance(calibration, dict):
        return None, '缺少校准记录'
    scale = str(calibration.get('scale', '')).strip()
    if not scale:
        return None, '校准记录缺少秤台'
    try:
        factor = number(calibration.get('factor'))
    except BusinessError:
        return None, '校准系数无法解析'
    if factor <= 0:
        return None, '校准系数必须为正'
    window = calibration.get('tickets')
    if not isinstance(window, list) or not window:
        return None, '校准记录未列明影响票范围'
    return {'scale': scale, 'factor': factor,
            'tickets': {str(t).strip() for t in window if str(t).strip()},
            'calibration_id': str(calibration.get('id', '')).strip(),
            'calibrated_at': calibration.get('calibrated_at', '')}, None


def _reweigh_index(reweighs):
    """按 (票号, 版本) 归集复秤净重；无法解析的记为 None 以触发冲突。"""
    indexed = {}
    for item in reweighs or []:
        ticket = str(item.get('ticket', '')).strip()
        if not ticket:
            continue
        key = (ticket, str(item.get('version', '')).strip())
        try:
            indexed.setdefault(key, []).append(_weight(item.get('net_kg', item.get('weight_kg')), '复秤'))
        except BusinessError:
            indexed.setdefault(key, []).append(None)
    return indexed


def _resolve_reweigh(index, ticket, version):
    exact = index.get((ticket, str(version)), [])
    if exact:
        if any(v is None for v in exact):
            return None, 'REWEIGH_CONFLICT', '复秤净重无法解析'
        if len(set(exact)) > 1:
            return None, 'REWEIGH_CONFLICT', '同一票同版本复秤证据互相冲突'
        return exact[0], '', ''
    other = sorted({key[1] for key in index if key[0] == ticket})
    if other:
        return None, 'REWEIGH_VERSION_MISMATCH', f"复秤证据针对版本 {'/'.join(other)}，与生效版本不符"
    return None, '', ''


def _conflicts_with_calibration(reweigh_net, calibrated_net):
    gap = abs(reweigh_net - calibrated_net)
    return gap > max(TOLERANCE_KG, calibrated_net * TOLERANCE_RATIO)


def _summary(row):
    return {'ticket': row['ticket'], 'version': row['version'], 'fish': row.get('fish', ''),
            'price': row.get('price', ''), 'rate': row.get('rate', ''), 'lines': row.get('lines', []),
            'paid': row.get('paid', '')}


def _pending(row, code, detail, reason):
    return {**_summary(row), 'baseline': BASIS_NONE, 'basis': '',
            'gross_kg': row.get('gross_kg', ''), 'net_kg': row.get('net_kg', ''),
            'adjusted_net_kg': '', 'billable_kg': '',
            'original_amount': money(Decimal(row['amount'])), 'adjusted_amount': '',
            'adjustment': '', 'decision': STATUS_PENDING, 'calibration_id': '', 'due': '',
            'code': code, 'detail': detail, 'reason': reason}


def _passthrough(row):
    """未提供任何校准证据的批次：不改价、不产生待处理票。"""
    return {**_summary(row), 'baseline': BASIS_ORIGINAL, 'basis': '未提供校准证据，按原成交价',
            'gross_kg': row.get('gross_kg', ''), 'net_kg': row.get('net_kg', ''),
            'adjusted_net_kg': row.get('net_kg', ''), 'billable_kg': row.get('billable_kg', ''),
            'original_amount': money(Decimal(row['amount'])),
            'adjusted_amount': money(Decimal(row['amount'])), 'adjustment': '0.00',
            'decision': STATUS_PAYABLE, 'calibration_id': '', 'due': row.get('due', ''),
            'code': 'UNAFFECTED', 'detail': '', 'reason': ''}


def _row(row, net, basis, note, calibration_id=''):
    rate = Decimal(row.get('rate', '0'))
    price = Decimal(row.get('price', '0'))
    billable = (net * (1 - rate)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
    adjusted = Decimal(money(billable * price))
    original = Decimal(row['amount'])
    return {**_summary(row), 'baseline': basis, 'basis': note,
            'gross_kg': row.get('gross_kg', ''), 'net_kg': row.get('net_kg', ''),
            'adjusted_net_kg': str(net), 'billable_kg': str(billable),
            'original_amount': money(original), 'adjusted_amount': money(adjusted),
            'adjustment': money(adjusted - original), 'decision': STATUS_PAYABLE,
            'calibration_id': calibration_id,
            'due': money(adjusted - Decimal(row.get('paid', '0'))),
            'code': 'CALIBRATED', 'detail': '', 'reason': ''}


def build_adjustments(final, evidence):
    """把更正后的生效票转成可付款调整结果，返回 (可付款行, 待处理行, 校准记录)。"""
    evidence = evidence if isinstance(evidence, dict) else {}
    if not evidence.get('calibration') and not evidence.get('reweighs'):
        return [_passthrough(row) for row in final], [], None
    calibration, calibration_error = _parse_calibration(evidence.get('calibration'))
    reweighs = _reweigh_index(evidence.get('reweighs'))
    rows, pending = [], []
    for row in final:
        total = Decimal(row.get('net_kg', '0'))
        reweigh, problem, problem_text = _resolve_reweigh(reweighs, row['ticket'], row['version'])
        covered = calibration is not None and row['ticket'] in calibration['tickets']
        calibrated_net = total * calibration['factor'] if covered else None

        if problem:
            pending.append(_pending(row, problem, problem_text, '复秤证据不可采信'))
            continue
        if reweigh is not None:
            if calibrated_net is not None and _conflicts_with_calibration(reweigh, calibrated_net):
                pending.append(_pending(row, 'WEIGHT_EVIDENCE_CONFLICT',
                                        f'复秤 {reweigh}kg 与校准推算 {calibrated_net.normalize():f}kg 差异过大',
                                        '复秤与校准证据冲突'))
                continue
            rows.append(_row(row, reweigh, BASIS_REWEIGH, '按复秤净重重算，采信现场复秤'))
            continue
        if calibration is None:
            pending.append(_pending(row, 'CALIBRATION_UNCOVERED', f'校准记录不可用：{calibration_error}',
                                    '缺少可用校准记录'))
            continue
        if not covered:
            pending.append(_pending(row, 'CALIBRATION_UNCOVERED', '校准记录未覆盖该票，不能整段修正',
                                    '校准范围未覆盖'))
            continue
        if calibrated_net < 0:
            pending.append(_pending(row, 'INVALID_RANGE', '校准修正后净重为负，保留原值待复核', '修正后净重为负'))
            continue
        rows.append(_row(row, calibrated_net, BASIS_FACTOR,
                         f"按校准系数 {calibration['factor']:f} 整段修正", calibration['calibration_id']))
    return rows, pending, calibration


def apply(state):
    evidence = state.get('evidence')
    rows, pending, calibration = build_adjustments(state.get('final', []), evidence)
    delta = sum((Decimal(r['adjustment']) for r in rows), Decimal(0))
    parsed = None
    if calibration:
        parsed = {'calibration_id': calibration['calibration_id'], 'scale': calibration['scale'],
                  'factor': str(calibration['factor']), 'tickets': sorted(calibration['tickets']),
                  'calibrated_at': calibration['calibrated_at']}
    state['adjustments'] = rows
    state['calibration_pending'] = pending
    state['calibration'] = {'record': (evidence or {}).get('calibration') if isinstance(evidence, dict) else None,
                            'parsed': parsed, 'pending': len(pending),
                            'payable': len(rows), 'adjustment_total': money(delta)}
    state['views']['calibration'] = {
        'title': '校准调整',
        'rows': rows + pending,
        'metrics': {'可付款票': len(rows), '待复秤票': len(pending), '调整额_CNY': money(delta)},
    }
