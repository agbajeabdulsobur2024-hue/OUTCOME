from app.payments import evaluate_payment_proposal


def base():
    commitment = {"quantity": 100, "amount": 500, "currency": "USD"}
    evidence = [
        {"kind": "quantity_received", "value": 100, "review_status": "VERIFIED"},
        {"kind": "invoice_amount", "value": 500, "review_status": "VERIFIED"},
    ]
    policy = {
        "enabled": True,
        "max_amount": 500,
        "allowed_destination": "supplier-wallet-001",
        "require_full_delivery": True,
        "require_invoice_match": True,
    }
    return commitment, evidence, policy


def test_passing_proposal_still_requires_human_approval_and_never_executes():
    c, e, p = base()
    result = evaluate_payment_proposal(c, e, p, 500, "USD", "supplier-wallet-001")
    assert result["status"] == "AWAITING_HUMAN_APPROVAL"
    assert result["eligible_for_human_approval"] is True
    assert result["human_approval_required"] is True
    assert result["execution_mode"] == "SIMULATION_ONLY"
    assert result["transfer_executed"] is False
    assert result["transaction_hash"] is None


def test_short_delivery_blocks_payment():
    c, e, p = base()
    e[0]["value"] = 80
    result = evaluate_payment_proposal(c, e, p, 500, "USD", "supplier-wallet-001")
    assert result["status"] == "BLOCKED"
    assert "DELIVERY_SHORTFALL" in {x["code"] for x in result["reasons"]}


def test_unapproved_destination_and_excess_amount_are_blocked():
    c, e, p = base()
    result = evaluate_payment_proposal(c, e, p, 600, "USD", "attacker-wallet")
    codes = {x["code"] for x in result["reasons"]}
    assert {"SPENDING_LIMIT_EXCEEDED", "ABOVE_COMMITMENT_VALUE", "DESTINATION_NOT_ALLOWED"} <= codes


def test_missing_invoice_blocks_by_default():
    c, e, p = base()
    e.pop()
    result = evaluate_payment_proposal(c, e, p, 500, "USD", "supplier-wallet-001")
    assert "INVOICE_NOT_EVIDENCED" in {x["code"] for x in result["reasons"]}


def test_disabled_policy_blocks():
    c, e, p = base()
    p["enabled"] = False
    result = evaluate_payment_proposal(c, e, p, 500, "USD", "supplier-wallet-001")
    assert result["status"] == "BLOCKED"
    assert "POLICY_DISABLED" in {x["code"] for x in result["reasons"]}



def test_partial_payment_is_blocked_unless_owner_policy_enables_it():
    c, e, p = base()
    blocked = evaluate_payment_proposal(c, e, p, 250, "USD", "supplier-wallet-001")
    assert "PARTIAL_PAYMENT_NOT_ALLOWED" in {x["code"] for x in blocked["reasons"]}
    p["allow_partial_payments"] = True
    allowed_for_review = evaluate_payment_proposal(c, e, p, 250, "USD", "supplier-wallet-001")
    assert allowed_for_review["status"] == "AWAITING_HUMAN_APPROVAL"
    assert allowed_for_review["human_approval_required"] is True
    assert allowed_for_review["transfer_executed"] is False
