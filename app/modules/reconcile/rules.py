from decimal import Decimal
from app.coerce import money


def balance(state):
    c = state['counts']
    accepted = len(state['records'])
    quarantined = len(state['quarantine'])
    explained = accepted + quarantined + c['blank'] + c['duplicate'] + c['repeated_header']
    # 原更正账：用于发现生效票自身的金额不守恒。
    final_amount = sum((Decimal(r['amount']) for r in state['final']), Decimal(0))
    final_paid = sum((Decimal(r['paid']) for r in state['final']), Decimal(0))
    final_due = sum((Decimal(r['due']) for r in state['final']), Decimal(0))
    # 可付款账：只取校准调整后判定为可付款的票，待复秤票不并入。
    payable = state.get('adjustments', [])
    amount = sum((Decimal(r['adjusted_amount']) for r in payable), Decimal(0))
    paid = sum((Decimal(r.get('paid', '0') or '0') for r in payable), Decimal(0))
    due = sum((Decimal(r.get('due', '0') or '0') for r in payable), Decimal(0))
    original = sum((Decimal(r['original_amount']) for r in payable), Decimal(0))
    pending_rows = state.get('calibration_pending', [])
    pending_amount = sum((Decimal(r['original_amount']) for r in pending_rows), Decimal(0))
    return {'input': c['input'], 'accepted': accepted, 'quarantined': quarantined, 'blank': c['blank'],
            'duplicate': c['duplicate'], 'repeated_header': c['repeated_header'],
            'row_balanced': explained == c['input'],
            'amount': money(amount), 'paid': money(paid), 'due': money(due),
            'original_amount': money(original), 'adjustment': money(amount - original),
            'pending_tickets': len(pending_rows), 'pending_amount': money(pending_amount),
            'money_balanced': final_amount == final_paid + final_due and amount == paid + due,
            'blocked': len(state['version_conflicts']),
            'final_amount': money(final_amount), 'final_due': money(final_due)}


def apply(state):
    state['balance'] = balance(state)
    state['views']['reconcile'] = {
        'title': '应付对账', 'rows': [state['balance']],
        'metrics': {'可付款_CNY': state['balance']['amount'], '已付_CNY': state['balance']['paid'],
                    '待付_CNY': state['balance']['due'], '待复秤票': state['balance']['pending_tickets']},
    }
