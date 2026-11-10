from datetime import date
from typing import Any


def assess_commitment(commitment: dict[str, Any], evidence: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """Compare latest evidence with the original supplier commitment."""
    today = today or date.today()
    expected_qty = float(commitment['quantity'])
    expected_amount = float(commitment['amount'])
    currency = commitment.get('currency', 'USD')
    received = [e for e in evidence if e.get('kind') == 'quantity_received' and e.get('value') is not None]
    invoices = [e for e in evidence if e.get('kind') == 'invoice_amount' and e.get('value') is not None]
    issues, actions, exposure = [], [], 0.0
    status = 'ON_TRACK'
    if received:
        actual = float(received[-1]['value'])
        if actual < expected_qty:
            shortfall = expected_qty - actual
            exposure = round(expected_amount * shortfall / expected_qty, 2) if expected_qty else 0
            issues.append({'code':'QUANTITY_SHORTFALL','severity':'high','message':f'Receiving evidence records {actual:g} of {expected_qty:g} units ({shortfall:g} short).','expected':expected_qty,'observed':actual,'financial_exposure':exposure,'currency':currency})
            status = 'DISCREPANCY'
            actions += ['Hold payment approval until the shortfall is reconciled.','Ask the supplier to deliver the missing quantity or issue a corrected invoice/credit note.','Confirm the received quantity with the person responsible for receiving goods.']
        elif actual > expected_qty:
            issues.append({'code':'QUANTITY_OVER_DELIVERY','severity':'medium','message':f'Receiving evidence records {actual:g} units, above the agreed {expected_qty:g}.','expected':expected_qty,'observed':actual})
            status = 'REVIEW_REQUIRED'
            actions.append('Confirm whether the additional quantity was authorized before updating the commitment.')
        else:
            actions.append('Quantity matches the commitment; verify invoice amount and payment conditions.')
    else:
        actions.append('Record the quantity actually received and attach the receiving evidence.')
    if invoices:
        invoice = float(invoices[-1]['value'])
        if invoice > expected_amount:
            delta = round(invoice - expected_amount, 2)
            issues.append({'code':'INVOICE_ABOVE_AGREEMENT','severity':'high','message':f'Invoice is {currency} {delta:.2f} above the agreed amount.','expected':expected_amount,'observed':invoice,'financial_exposure':delta,'currency':currency})
            status = 'DISCREPANCY'
            actions.insert(0,'Do not approve the invoice until the price difference is explained and authorized.')
        elif invoice < expected_amount:
            issues.append({'code':'INVOICE_BELOW_AGREEMENT','severity':'low','message':f'Invoice is {currency} {expected_amount-invoice:.2f} below the agreed amount.','expected':expected_amount,'observed':invoice,'currency':currency})
            if status == 'ON_TRACK': status = 'REVIEW_REQUIRED'
    due = date.fromisoformat(str(commitment['due_date'])[:10])
    if today > due and status == 'ON_TRACK':
        status = 'OVERDUE'
        issues.append({'code':'COMMITMENT_OVERDUE','severity':'medium','message':f'Commitment due date ({due.isoformat()}) has passed.'})
        actions.insert(0,'Contact the supplier, record the latest status and agree a revised delivery date.')
    if status == 'ON_TRACK' and not received:
        actions.append('Keep payment approval pending until delivery is verified.')
    if status == 'ON_TRACK' and received and (not invoices or float(invoices[-1]['value']) <= expected_amount):
        actions.append('If all contractual conditions are met, a human approver can review the payment.')
    summary = (f'Evidence does not match the supplier commitment for {commitment["supplier"]}.' if status == 'DISCREPANCY' else f'The commitment for {commitment["supplier"]} needs a human review.' if status == 'REVIEW_REQUIRED' else f'The supplier commitment for {commitment["supplier"]} is overdue.' if status == 'OVERDUE' else f'No material discrepancy is currently detected for {commitment["supplier"]}.')
    return {'status':status,'summary':summary,'issues':issues,'financial_exposure':round(exposure,2),'currency':currency,'next_actions':list(dict.fromkeys(actions)),'evidence_count':len(evidence),'assessment_basis':'latest evidence by evidence type compared with the original commitment','human_review_required':status != 'ON_TRACK'}
