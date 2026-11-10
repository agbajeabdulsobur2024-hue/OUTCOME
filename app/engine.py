from datetime import date
from typing import Any


def assess_commitment(commitment: dict[str, Any], evidence: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """Assess a supplier commitment against append-only evidence.

    Status is a summary; independent issue flags are retained so an overdue
    commitment cannot hide a quantity or invoice discrepancy.
    """
    today = today or date.today()
    expected_qty = float(commitment['quantity'])
    expected_amount = float(commitment['amount'])
    currency = commitment.get('currency', 'USD')
    received = [e for e in evidence if e.get('kind') == 'quantity_received' and e.get('value') is not None]
    invoices = [e for e in evidence if e.get('kind') == 'invoice_amount' and e.get('value') is not None]
    issues: list[dict[str, Any]] = []
    actions: list[str] = []
    quantity_exposure = 0.0
    invoice_variance = 0.0
    review_required = False

    if received:
        actual = float(received[-1]['value'])
        if actual < expected_qty:
            shortfall = expected_qty - actual
            quantity_exposure = round(expected_amount * shortfall / expected_qty, 2) if expected_qty else 0.0
            issues.append({'code':'QUANTITY_SHORTFALL','severity':'high','message':f'Receiving evidence records {actual:g} of {expected_qty:g} units ({shortfall:g} short).','expected':expected_qty,'observed':actual,'financial_exposure_estimate':quantity_exposure,'currency':currency})
            actions.extend(['Hold payment approval until the shortfall is reconciled.','Ask the supplier to deliver the missing quantity or issue a corrected invoice/credit note.','Confirm the received quantity with the person responsible for receiving goods.'])
        elif actual > expected_qty:
            issues.append({'code':'QUANTITY_OVER_DELIVERY','severity':'medium','message':f'Receiving evidence records {actual:g} units, above the agreed {expected_qty:g}.','expected':expected_qty,'observed':actual})
            review_required = True
            actions.append('Confirm whether the additional quantity was authorized before updating the commitment.')
        else:
            actions.append('Recorded quantity matches the commitment; verify invoice amount and all payment conditions.')
    else:
        actions.append('Record the quantity actually received and attach receiving evidence.')

    if invoices:
        invoice = float(invoices[-1]['value'])
        if invoice > expected_amount:
            invoice_variance = round(invoice - expected_amount, 2)
            issues.append({'code':'INVOICE_ABOVE_AGREEMENT','severity':'high','message':f'Invoice is {currency} {invoice_variance:.2f} above the agreed amount.','expected':expected_amount,'observed':invoice,'financial_exposure_estimate':invoice_variance,'currency':currency})
            actions.insert(0,'Do not approve the invoice until the price difference is explained and authorized.')
        elif invoice < expected_amount:
            issues.append({'code':'INVOICE_BELOW_AGREEMENT','severity':'low','message':f'Invoice is {currency} {expected_amount-invoice:.2f} below the agreed amount.','expected':expected_amount,'observed':invoice,'currency':currency})
            review_required = True

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
        actions.append('Keep payment approval pending until delivery is verified.')
    elif status == 'ON_TRACK':
        actions.append('If all contractual conditions are met, a human approver can review the payment.')

    supplier = commitment['supplier']
    summaries = {
        'DISCREPANCY': f'Evidence does not match the supplier commitment for {supplier}.',
        'OVERDUE': f'The supplier commitment for {supplier} is overdue.',
        'REVIEW_REQUIRED': f'The commitment for {supplier} needs a human review.',
        'AWAITING_EVIDENCE': f'Awaiting delivery evidence for the commitment with {supplier}.',
        'ON_TRACK': f'Available evidence currently matches the recorded expectations for {supplier}; this is not payment authorization.'
    }
    # These estimates may overlap (e.g. short delivery and invoice overcharge).
    # Report separately; do not add them into a misleading total.
    return {
        'status': status, 'summary': summaries[status], 'issues': issues,
        'exposure_estimates': {'quantity_shortfall': quantity_exposure, 'invoice_overcharge': invoice_variance, 'currency': currency, 'may_overlap': True},
        'financial_exposure': max(quantity_exposure, invoice_variance),
        'currency': currency, 'next_actions': list(dict.fromkeys(actions)),
        'evidence_count': len(evidence),
        'assessment_basis': 'latest evidence by evidence type compared with the recorded commitment; historical evidence retained',
        'human_review_required': status != 'ON_TRACK'
    }
