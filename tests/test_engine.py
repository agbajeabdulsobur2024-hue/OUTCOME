from datetime import date
from app.engine import assess_commitment


def commitment():
    return {'supplier':'Acme Supplies','quantity':100,'unit':'units','amount':500,'currency':'USD','due_date':'2026-12-01'}


def test_short_delivery_flags_discrepancy_and_exposure():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':80}],today=date(2026,11,10))
    assert r['status']=='DISCREPANCY'
    assert r['financial_exposure']==100
    assert r['human_review_required'] is True
    assert any('Hold payment approval' in x for x in r['next_actions'])


def test_full_delivery_is_not_a_quantity_discrepancy():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':100}],today=date(2026,11,10))
    assert r['status']=='ON_TRACK' and not r['issues']


def test_invoice_above_agreement_flags_discrepancy():
    r=assess_commitment(commitment(),[{'kind':'invoice_amount','value':550}],today=date(2026,11,10))
    assert r['status']=='DISCREPANCY'
    assert r['issues'][0]['code']=='INVOICE_ABOVE_AGREEMENT'


def test_unverified_full_quantity_does_not_clear_a_verified_shortfall():
    evidence = [
        {'kind': 'quantity_received', 'value': 42, 'review_status': 'VERIFIED'},
        {'kind': 'quantity_received', 'value': 50, 'review_status': 'UNREVIEWED'},
    ]
    paper = commitment()
    paper['quantity'] = 50
    paper['amount'] = 250
    result = assess_commitment(paper, evidence, today=date(2026, 11, 10))
    assert result['status'] == 'DISCREPANCY'
    assert result['financial_exposure'] == 40
    assert any(issue['code'] == 'QUANTITY_SHORTFALL' for issue in result['issues'])


def test_a_milestone_obligation_uses_the_same_assessment():
    agreement = {
        'counterparty': 'Jordan Lee', 'commitment_kind': 'milestone',
        'quantity': 3, 'unit': 'milestones', 'amount': 3000, 'currency': 'USD',
        'due_date': '2026-12-01',
    }
    result = assess_commitment(agreement, [
        {'kind': 'obligation_fulfilled', 'value': 2, 'review_status': 'VERIFIED'},
        {'kind': 'amount_claimed', 'value': 3000, 'review_status': 'VERIFIED'},
    ], today=date(2026, 11, 10))
    assert result['status'] == 'DISCREPANCY'
    assert result['financial_exposure'] == 1000
    assert any('Hold payment approval' in step for step in result['next_actions'])
    assert 'milestones' in result['issues'][0]['message']


def test_latest_receiving_evidence_is_used():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':70},{'kind':'quantity_received','value':100}],today=date(2026,11,10))
    assert r['status']=='ON_TRACK'


def test_missing_delivery_evidence_is_awaiting_not_on_track():
    r=assess_commitment(commitment(),[],today=date(2026,11,10))
    assert r['status']=='AWAITING_EVIDENCE'
    assert any('payment approval pending' in x for x in r['next_actions'])


def test_overdue_commitment_is_flagged():
    r=assess_commitment(commitment(),[],today=date(2026,12,2))
    assert r['status']=='OVERDUE'
    assert any(i['code']=='COMMITMENT_OVERDUE' for i in r['issues'])


def test_overdue_and_short_delivery_flags_coexist():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':80}],today=date(2026,12,2))
    codes={i['code'] for i in r['issues']}
    assert r['status']=='DISCREPANCY'
    assert {'QUANTITY_SHORTFALL','COMMITMENT_OVERDUE'} <= codes


def test_exposure_estimates_are_separate_when_they_may_overlap():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':80},{'kind':'invoice_amount','value':550}],today=date(2026,11,10))
    assert r['exposure_estimates']['quantity_shortfall']==100
    assert r['exposure_estimates']['invoice_overcharge']==50
    assert r['exposure_estimates']['may_overlap'] is True
    assert r['financial_exposure']==100
