from collections import defaultdict
from datetime import date, datetime
from typing import Any


def detect_supplier_patterns(
    commitments: list[dict[str, Any]],
    evidence_by_commitment: dict[str, list[dict[str, Any]]],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Surface repeated, evidence-backed supplier signals without inferring intent."""
    today = today or date.today()
    groups: dict[str, dict[str, Any]] = {}
    for commitment in commitments:
        supplier = str(commitment.get("supplier", "")).strip()
        key = supplier.casefold()
        if not key:
            continue
        groups.setdefault(key, {"supplier": supplier, "commitments": []})
        groups[key]["commitments"].append(commitment)

    output: list[dict[str, Any]] = []
    definitions = (
        ("REPEATED_SHORT_DELIVERY", "quantity_received"),
        ("REPEATED_INVOICE_OVERCHARGE", "invoice_amount"),
        ("REPEATED_LATE_DELIVERY", "quantity_received"),
    )

    for group in groups.values():
        supplier = group["supplier"]
        rows = group["commitments"]
        for pattern_type, evidence_kind in definitions:
            observed: list[dict[str, Any]] = []
            incidents: list[dict[str, Any]] = []
            for commitment in rows:
                cid = commitment["id"]
                records = [
                    item for item in evidence_by_commitment.get(cid, [])
                    if item.get("kind") == evidence_kind and item.get("value") is not None
                ]
                if not records:
                    continue
                latest = records[-1]
                # Never count unreviewed or rejected evidence as a pattern signal.
                if latest.get("review_status") != "VERIFIED":
                    continue
                observed.append(commitment)
                value = float(latest["value"])
                if pattern_type == "REPEATED_SHORT_DELIVERY" and value < float(commitment["quantity"]):
                    incidents.append({"commitment_id": cid, "evidence_id": latest["id"], "observed": value, "expected": float(commitment["quantity"])})
                elif pattern_type == "REPEATED_INVOICE_OVERCHARGE" and value > float(commitment["amount"]):
                    incidents.append({"commitment_id": cid, "evidence_id": latest["id"], "observed": value, "expected": float(commitment["amount"])})
                elif pattern_type == "REPEATED_LATE_DELIVERY" and value >= float(commitment["quantity"]):
                    try:
                        delivered_at = datetime.fromisoformat(str(latest["created_at"]).replace("Z", "+00:00")).date()
                        due = date.fromisoformat(str(commitment["due_date"])[:10])
                    except (ValueError, TypeError, KeyError):
                        continue
                    if delivered_at > due:
                        incidents.append({"commitment_id": cid, "evidence_id": latest["id"], "observed": delivered_at.isoformat(), "expected": due.isoformat()})

            if len(incidents) < 2:
                continue
            sample_size = len(observed)
            rate = len(incidents) / sample_size if sample_size else 0
            output.append({
                "supplier": supplier,
                "pattern_type": pattern_type,
                "occurrences": len(incidents),
                "observed_commitments": sample_size,
                "rate": round(rate, 3),
                "confidence": "RECURRING_PATTERN" if sample_size >= 5 and rate >= 0.3 else "EARLY_SIGNAL",
                "incidents": incidents,
                "interpretation": "Repeated evidence-backed signal. Review the underlying records and context; this does not establish intent, misconduct or fraud.",
                "as_of": today.isoformat(),
            })

    return sorted(output, key=lambda item: (-item["occurrences"], item["supplier"].casefold(), item["pattern_type"]))
