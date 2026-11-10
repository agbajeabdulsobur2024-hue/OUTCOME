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


def test_latest_receiving_evidence_is_used():
    r=assess_commitment(commitment(),[{'kind':'quantity_received','value':70},{'kind':'quantity_received','value':100}],today=date(2026,11,10))
    assert r['status']=='ON_TRACK'


def test_missing_delivery_evidence_keeps_payment_pending():
    r=assess_commitment(commitment(),[],today=date(2026,11,10))
    assert r['status']=='ON_TRACK'
    assert any('payment approval pending' in x for x in r['next_actions'])


def test_overdue_commitment_is_flagged():
    r=assess_commitment(commitment(),[],today=date(2026,12,2))
    assert r['status']=='OVERDUE'
