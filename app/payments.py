from typing import Any


def evaluate_payment_proposal(
    commitment: dict[str, Any],
    evidence: list[dict[str, Any]],
    policy: dict[str, Any],
    requested_amount: float,
    currency: str,
    destination: str,
) -> dict[str, Any]:
    """Evaluate a proposed payment against owner-defined controls.

    This function never executes, authorizes, or submits a transfer. A passing
    proposal is only eligible for a separate human approval step.
    """
    reasons: list[dict[str, str]] = []
    expected_currency = str(commitment.get("currency", "USD")).upper()
    requested_currency = str(currency).upper()

    if not policy.get("enabled", False):
        reasons.append({"code": "POLICY_DISABLED", "message": "Payment policy is disabled."})
    if requested_currency != expected_currency:
        reasons.append({"code": "CURRENCY_MISMATCH", "message": "Requested payment currency does not match the commitment."})
    if requested_amount <= 0:
        reasons.append({"code": "INVALID_AMOUNT", "message": "Requested payment amount must be positive."})
    cap = float(policy.get("max_amount", 0))
    if requested_amount > cap:
        reasons.append({"code": "SPENDING_LIMIT_EXCEEDED", "message": "Requested amount exceeds the owner-defined spending limit."})
    if requested_amount > float(commitment["amount"]):
        reasons.append({"code": "ABOVE_COMMITMENT_VALUE", "message": "Requested amount exceeds the recorded commitment value."})
    if not policy.get("allow_partial_payments", False) and round(float(requested_amount), 2) != round(float(commitment["amount"]), 2):
        reasons.append({"code": "PARTIAL_PAYMENT_NOT_ALLOWED", "message": "The proposal is not for the full committed amount and partial payments are not enabled by the owner policy."})

    approved_destination = str(policy.get("allowed_destination", "")).strip()
    if not approved_destination or destination.strip() != approved_destination:
        reasons.append({"code": "DESTINATION_NOT_ALLOWED", "message": "Destination does not exactly match the owner-approved destination."})

    def latest(kind: str):
        records = [e for e in evidence if e.get("kind") == kind and e.get("value") is not None]
        return records[-1] if records else None

    if policy.get("require_full_delivery", True):
        qty_record = latest("quantity_received")
        if not qty_record:
            reasons.append({"code": "DELIVERY_NOT_EVIDENCED", "message": "No receiving quantity evidence is recorded."})
        elif policy.get("require_verified_evidence", True) and qty_record.get("review_status") != "VERIFIED":
            reasons.append({"code": "DELIVERY_NOT_VERIFIED", "message": "The latest receiving evidence has not been verified by an authorized reviewer."})
        elif float(qty_record["value"]) < float(commitment["quantity"]):
            reasons.append({"code": "DELIVERY_SHORTFALL", "message": "Verified delivery is below the committed quantity."})
        elif float(qty_record["value"]) > float(commitment["quantity"]):
            reasons.append({"code": "DELIVERY_REQUIRES_REVIEW", "message": "Verified delivery exceeds the commitment and needs human review."})

    if policy.get("require_invoice_match", True):
        invoice_record = latest("invoice_amount")
        if not invoice_record:
            reasons.append({"code": "INVOICE_NOT_EVIDENCED", "message": "No invoice amount evidence is recorded."})
        elif policy.get("require_verified_evidence", True) and invoice_record.get("review_status") != "VERIFIED":
            reasons.append({"code": "INVOICE_NOT_VERIFIED", "message": "The latest invoice evidence has not been verified by an authorized reviewer."})
        elif round(float(invoice_record["value"]), 2) != round(float(commitment["amount"]), 2):
            reasons.append({"code": "INVOICE_MISMATCH", "message": "Latest verified invoice amount does not exactly match the recorded commitment."})

    blocked = bool(reasons)
    return {
        "status": "BLOCKED" if blocked else "AWAITING_HUMAN_APPROVAL",
        "eligible_for_human_approval": not blocked,
        "human_approval_required": True,
        "execution_mode": "SIMULATION_ONLY",
        "transfer_executed": False,
        "transaction_hash": None,
        "requested_amount": round(float(requested_amount), 2),
        "currency": requested_currency,
        "destination": destination,
        "reasons": reasons,
        "policy_checks_passed": not blocked,
        "message": (
            "Proposal is blocked. Resolve every listed policy/evidence issue before resubmitting."
            if blocked else
            "Policy checks passed. This is only a proposal; an authorized human must approve it. No funds have moved."
        ),
    }
