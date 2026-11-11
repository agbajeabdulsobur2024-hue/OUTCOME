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


def test_newer_unreviewed_quantity_does_not_hide_a_verified_short_delivery_signal():
    commitments = [
        {"id": "a", "supplier": "Acme", "quantity": 50, "amount": 250, "due_date": "2026-12-01"},
        {"id": "b", "supplier": "Acme", "quantity": 20, "amount": 100, "due_date": "2026-12-02"},
    ]
    evidence = {
        "a": [
            {"id": "ea", "kind": "quantity_received", "value": 42, "review_status": "VERIFIED"},
            {"id": "ea2", "kind": "quantity_received", "value": 50, "review_status": "UNREVIEWED"},
        ],
        "b": [
            {"id": "eb", "kind": "quantity_received", "value": 16, "review_status": "VERIFIED"},
            {"id": "eb2", "kind": "quantity_received", "value": 20, "review_status": "UNREVIEWED"},
        ],
    }
    result = detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10))
    short = next(item for item in result if item["pattern_type"] == "REPEATED_SHORT_DELIVERY")
    assert short["occurrences"] == 2


def test_milestone_shortfalls_use_the_same_signal_as_a_delivery():
    commitments = [
        {"id": "a", "supplier": "Jordan Lee", "quantity": 3, "amount": 3000, "due_date": "2026-12-01"},
        {"id": "b", "supplier": "Jordan Lee", "quantity": 2, "amount": 800, "due_date": "2026-12-02"},
    ]
    evidence = {
        "a": [{"id": "ea", "kind": "obligation_fulfilled", "value": 2, "review_status": "VERIFIED"}],
        "b": [{"id": "eb", "kind": "obligation_fulfilled", "value": 1, "review_status": "VERIFIED"}],
    }
    result = detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10))
    short = next(item for item in result if item["pattern_type"] == "REPEATED_SHORT_DELIVERY")
    assert short["occurrences"] == 2
    assert short["counterparty"] == "Jordan Lee"


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



def test_low_incident_rate_remains_early_signal_even_with_larger_sample():
    commitments = [
        {"id": str(i), "supplier": "Acme", "quantity": 100, "amount": 500, "due_date": "2026-12-01"}
        for i in range(10)
    ]
    evidence = {
        str(i): [{
            "id": "e" + str(i), "kind": "quantity_received",
            "value": 80 if i < 2 else 100, "review_status": "VERIFIED",
        }]
        for i in range(10)
    }
    result = detect_supplier_patterns(commitments, evidence, today=date(2026, 11, 10))
    short = next(p for p in result if p["pattern_type"] == "REPEATED_SHORT_DELIVERY")
    assert short["occurrences"] == 2
    assert short["observed_commitments"] == 10
    assert short["rate"] == 0.2
    assert short["confidence"] == "EARLY_SIGNAL"
