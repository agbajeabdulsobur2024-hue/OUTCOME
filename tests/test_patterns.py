from datetime import date

from app.patterns import detect_supplier_patterns


def test_repeated_short_delivery_requires_two_verified_incidents():
    commitments = [
        {"id": "a", "supplier": "Acme", "quantity": 100, "amount": 500, "due_date": "2026-12-01"},
        {"id": "b", "supplier": "ACME", "quantity": 50, "amount": 250, "due_date": "2026-12-02"},
        {"id": "c", "supplier": "Acme", "quantity": 20, "amount": 100, "due_date": "2026-12-03"},
    ]
    evidence = {
        "a": [{"id": "ea", "kind": "quantity_received", "value": 80, "review_status": "VERIFIED"}],
        "b": [{"id": "eb", "kind": "quantity_received", "value": 50, "review_status": "VERIFIED"}],
        "c": [{"id": "ec", "kind": "quantity_received", "value": 10, "review_status": "UNREVIEWED"}],
    }
    result = detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10))
    short = [p for p in result if p["pattern_type"] == "REPEATED_SHORT_DELIVERY"]
    assert len(short) == 0


def test_two_verified_short_deliveries_create_early_signal_not_accusation():
    commitments = [
        {"id": "a", "supplier": "Acme", "quantity": 100, "amount": 500, "due_date": "2026-12-01"},
        {"id": "b", "supplier": "ACME", "quantity": 50, "amount": 250, "due_date": "2026-12-02"},
    ]
    evidence = {
        "a": [{"id": "ea", "kind": "quantity_received", "value": 80, "review_status": "VERIFIED"}],
        "b": [{"id": "eb", "kind": "quantity_received", "value": 40, "review_status": "VERIFIED"}],
    }
    result = detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10))
    assert len(result) == 1
    assert result[0]["pattern_type"] == "REPEATED_SHORT_DELIVERY"
    assert result[0]["confidence"] == "EARLY_SIGNAL"
    assert result[0]["occurrences"] == 2
    assert "does not establish intent" in result[0]["interpretation"]


def test_unreviewed_or_rejected_evidence_never_counts():
    commitments = [
        {"id": "a", "supplier": "Acme", "quantity": 100, "amount": 500, "due_date": "2026-12-01"},
        {"id": "b", "supplier": "Acme", "quantity": 50, "amount": 250, "due_date": "2026-12-02"},
    ]
    evidence = {
        "a": [{"id": "ea", "kind": "invoice_amount", "value": 700, "review_status": "VERIFIED"}],
        "b": [{"id": "eb", "kind": "invoice_amount", "value": 400, "review_status": "REJECTED"}],
    }
    assert detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10)) == []
