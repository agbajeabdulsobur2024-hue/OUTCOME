from datetime import date, datetime, timezone
from typing import Any


FULFILMENT_KINDS = {'quantity_received', 'obligation_fulfilled'}
AMOUNT_KINDS = {'invoice_amount', 'amount_claimed'}


def assess_commitment(commitment: dict[str, Any], evidence: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """Compare a recorded obligation with append-only evidence.

    The same rules apply to a delivery, a milestone, a repayment, or any other
    commitment that names a quantity, a unit, and an amount. Status is a
    summary. An overdue flag stays visible beside a shortfall.
    """
    today = today or datetime.now(timezone.utc).date()
    expected_qty = float(commitment['quantity'])
    expected_amount = float(commitment['amount'])
    currency = commitment.get('currency', 'USD')
    party = str(commitment.get('counterparty') or commitment.get('supplier') or 'the other party')
    unit = str(commitment.get('unit') or 'units')
    received_rows = [e for e in evidence if e.get('kind') in FULFILMENT_KINDS and e.get('value') is not None]
    invoice_rows = [e for e in evidence if e.get('kind') in AMOUNT_KINDS and e.get('value') is not None]
    received_record, quantity_unverified_newer = _governing_record(received_rows)
    invoice_record, invoice_unverified_newer = _governing_record(invoice_rows)
    received = received_rows
    issues: list[dict[str, Any]] = []
    actions: list[str] = []
    quantity_exposure = 0.0
    invoice_variance = 0.0
    review_required = False

    if received_record:
        actual = float(received_record['value'])
        if actual < expected_qty:
            shortfall = expected_qty - actual
            quantity_exposure = round(expected_amount * shortfall / expected_qty, 2) if expected_qty else 0.0
            issues.append({'code':'QUANTITY_SHORTFALL','severity':'high','message':f'Recorded fulfilment is {actual:g} of {expected_qty:g} {unit} ({shortfall:g} short).','expected':expected_qty,'observed':actual,'financial_exposure_estimate':quantity_exposure,'financial_exposure_estimate_basis':'Proportional estimate: agreed amount × unfulfilled quantity ÷ agreed quantity. It is not an invoice, verified loss, or amount payable.','currency':currency})
            actions.extend(['Hold payment approval until the shortfall is reconciled.', f'Ask {party} to complete the remaining {shortfall:g} {unit} or correct the amount.', 'Confirm the recorded fulfilment before any release.'])
        elif actual > expected_qty:
            issues.append({'code':'QUANTITY_OVER_DELIVERY','severity':'medium','message':f'Recorded fulfilment is {actual:g} {unit}, above the agreed {expected_qty:g}.','expected':expected_qty,'observed':actual})
            review_required = True
            actions.append('Confirm whether the additional quantity was authorized before updating the commitment.')
        else:
            actions.append('Recorded fulfilment matches the commitment; verify the claimed amount and every release condition.')
        if quantity_unverified_newer:
            actions.append('A newer unverified receiving record does not clear this result.')
    elif received_rows:
        review_required = True
        actions.append('Fulfilment evidence is not verified, so it does not clear a shortfall or show the obligation as complete.')
    else:
        actions.append('Record what was actually completed and attach the evidence.')

    if invoice_record:
        invoice = float(invoice_record['value'])
        if invoice > expected_amount:
            invoice_variance = round(invoice - expected_amount, 2)
            issues.append({'code':'INVOICE_ABOVE_AGREEMENT','severity':'high','message':f'Invoice is {currency} {invoice_variance:.2f} above the agreed amount.','expected':expected_amount,'observed':invoice,'financial_exposure_estimate':invoice_variance,'currency':currency})
            actions.insert(0,'Do not approve the invoice until the price difference is explained and authorized.')
        elif invoice < expected_amount:
            issues.append({'code':'INVOICE_BELOW_AGREEMENT','severity':'low','message':f'Invoice is {currency} {expected_amount-invoice:.2f} below the agreed amount.','expected':expected_amount,'observed':invoice,'currency':currency})
            review_required = True
        if invoice_unverified_newer:
            actions.append('A newer unverified invoice does not replace the verified invoice result.')
    elif invoice_rows:
        review_required = True
        actions.append('The claimed amount is not verified, so it does not change the assessment.')

    due = date.fromisoformat(str(commitment['due_date'])[:10])
    overdue = today > due
    if overdue:
        issues.append({'code':'COMMITMENT_OVERDUE','severity':'high' if not received else 'medium','message':f'Commitment due date ({due.isoformat()}) has passed.'})
        actions.insert(0,'Contact the supplier, record the latest status and agree a revised delivery date.')

    has_discrepancy = any(i['code'] in {'QUANTITY_SHORTFALL','INVOICE_ABOVE_AGREEMENT'} for i in issues)
    if has_discrepancy:
        status = 'DISCREPANCY'
    elif overdue:
        status = 'OVERDUE'
    elif review_required:
        status = 'REVIEW_REQUIRED'
    elif not received:
        status = 'AWAITING_EVIDENCE'
    else:
        status = 'ON_TRACK'

    if status == 'AWAITING_EVIDENCE':
        actions.append('Keep payment approval pending until the obligation is verified.')
    elif status == 'ON_TRACK':
        actions.append('If all recorded conditions are met, a person can review the next step.')

    summaries = {
        'DISCREPANCY': f'Evidence does not match the commitment with {party}.',
        'OVERDUE': f'The commitment with {party} is overdue.',
        'REVIEW_REQUIRED': f'The commitment with {party} needs a human review.',
        'AWAITING_EVIDENCE': f'Awaiting evidence for the commitment with {party}.',
        'ON_TRACK': f'Available evidence currently matches the recorded expectations for {party}; this is not payment authorization.'
    }
    # These estimates may overlap (e.g. short delivery and invoice overcharge).
    # Report separately; do not add them into a misleading total.
    return {
        'status': status, 'summary': summaries[status], 'issues': issues,
        'exposure_estimates': {'quantity_shortfall': quantity_exposure, 'invoice_overcharge': invoice_variance, 'currency': currency, 'may_overlap': True},
        'financial_exposure': max(quantity_exposure, invoice_variance),
        'financial_exposure_basis': 'Quantity shortfall estimates are proportional to the recorded agreement value; invoice variance is based on the recorded claimed amount. Neither is a verified loss or amount payable.',
        'currency': currency, 'next_actions': list(dict.fromkeys(actions)),
        'evidence_count': len(evidence),
        'assessment_basis': 'latest verified evidence by type when reviews exist; an unverified record does not clear a verified shortfall',
        'human_review_required': status != 'ON_TRACK'
    }


def _governing_record(records: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """Pick the record that may change fulfilment.

    Callers that do not supply review_status keep the latest value. A first
    unreviewed observation still counts, so a shortfall is visible before
    review. Once any record is verified, only the latest verified value can
    change the result. A newer unreviewed record does not clear it.
    """
    if not records:
        return None, False
    if not any('review_status' in item for item in records):
        return records[-1], False
    verified = [item for item in records if item.get('review_status') == 'VERIFIED']
    if not verified:
        return records[-1], False
    newer_unverified = records[-1].get('review_status') != 'VERIFIED'
    return verified[-1], newer_unverified
