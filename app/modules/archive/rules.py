import hashlib, json


def archive_decision(state):
    calibration = state.get('calibration') or {}
    pending = state.get('calibration_pending', [])
    ready = (state['balance']['row_balanced'] and state['balance']['money_balanced']
             and not state['balance']['blocked'] and not pending)
    payload = {'final': state['final'], 'adjustments': state.get('adjustments', []),
               'pending': pending, 'calibration': calibration.get('record')}
    seal = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'seal': seal, 'status': '可归档' if ready else '待处理', 'tickets': len(state['final']),
            'payable': len(state.get('adjustments', [])), 'pending': len(pending),
            'reasons': len(state['quarantine'])}


def apply(state):
    state['archive'] = archive_decision(state)
    state['views']['archive'] = {
        'title': '结算封存', 'rows': state['version_audit'],
        'metrics': {'封存票': state['archive']['tickets'], '隔离证据': len(state['quarantine']),
                    '可付款票': state['archive']['payable'], '待复秤票': state['archive']['pending'],
                    '状态': state['archive']['status']},
    }
