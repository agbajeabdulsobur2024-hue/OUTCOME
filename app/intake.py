"""Ordinary-language intake into existing commitment and evidence records.

This module does not call the model, does not write the database, and does not
approve or submit a payment. The model proposes JSON. These checks keep only
values the text supports, ask when something required is missing or ambiguous,
and refuse a second copy of a record the workspace already has.
"""

import math
import re
from datetime import datetime
from typing import Any


class IntakeRejection(Exception):
    def __init__(self, clarifications: list[str]):
        self.clarifications = [item for item in clarifications if item]
        super().__init__(' '.join(self.clarifications))


REQUIRED_COMMITMENT_FIELDS = (
    'counterparty', 'description', 'quantity', 'unit', 'amount', 'currency', 'due_date',
)
COMMITMENT_FIELDS = REQUIRED_COMMITMENT_FIELDS + ('commitment_kind', 'payment_condition')
DEFAULT_COMMITMENT_KIND = 'general'
DEFAULT_PAYMENT_CONDITION = 'Release only after the agreed obligation is verified'
EVIDENCE_KINDS = (
    'quantity_received', 'obligation_fulfilled', 'invoice_amount', 'amount_claimed', 'note',
)
KIND_CUES = {
    'quantity_received': ('received', 'delivered', 'arrived', 'counted', 'shipment'),
    'obligation_fulfilled': ('fulfilled', 'finished', 'completed'),
    'invoice_amount': ('invoice', 'invoiced', 'billed'),
    'amount_claimed': ('claimed',),
    'note': ('said', 'wrote', 'message', 'noted'),
}
PAYMENT_PHRASES = (
    'approve the payment', 'approve payment', 'approve this payment',
    'mark as paid', 'mark it paid', 'mark this paid',
    'send the payment', 'submit the payment', 'release the funds',
    'transfer the money', 'pay the invoice', 'make the payment',
)
CURRENCY_ALIASES = {
    'USD': ('usd', 'us dollar', 'us dollars', 'u.s. dollar', 'u.s. dollars', 'dollar', 'dollars'),
    'EUR': ('eur', 'euro', 'euros'),
    'GBP': ('gbp', 'pound sterling', 'pounds sterling'),
    'NGN': ('ngn', 'naira'),
    'CAD': ('cad', 'canadian dollar', 'canadian dollars'),
    'AUD': ('aud', 'australian dollar', 'australian dollars'),
    'KES': ('kes', 'kenyan shilling', 'kenyan shillings'),
    'ZAR': ('zar', 'rand'),
    'GHS': ('ghs', 'ghanaian cedi', 'cedi', 'cedis'),
}
CURRENCY_SYMBOLS = {'EUR': '€', 'GBP': '£', 'NGN': '₦'}
MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'for', 'with', 'on', 'in', 'by', 'at',
    'from', 'this', 'that', 'these', 'those', 'was', 'were', 'is', 'are', 'be', 'been',
    'we', 'our', 'their', 'they', 'them', 'it', 'its', 'as', 'after', 'before', 'only',
    'into', 'over', 'under', 'than', 'then', 'also', 'not', 'but', 'per',
}
FIELD_QUESTIONS = {
    'counterparty': 'Who is the other party?',
    'description': 'What was agreed? Give a short description in the words you want recorded.',
    'quantity': 'What quantity was agreed?',
    'unit': 'What unit is that quantity in?',
    'amount': 'What amount was agreed?',
    'currency': 'Which currency is that amount in?',
    'due_date': 'What is the due date?',
}
NOT_SUBMITTED = (
    'NOT SUBMITTED. Intake does not approve, submit, or complete a payment. '
    'No transfer was recorded.'
)


def prepare_intake(text: str, extraction: Any, records: list[dict]) -> dict:
    """Turn model JSON into a confirmation proposal or a short list of questions."""
    grounded, evidence, currency_gap = _ground(text, extraction)
    match = _match_records(text, grounded, records)
    payment = _payment_request(text)
    clarifications: list[str] = []
    action = 'none'
    matched_id = None
    kept_evidence: list[dict] = []
    skipped = 0
    defaults: list[str] = []
    commitment_out = None
    status = 'needs_clarification'

    if match['status'] == 'ambiguous':
        clarifications.append(_ambiguous_question(match['records']))
    elif match['status'] == 'conflict':
        clarifications.append(_conflict_question(match['records'][0], match['conflicts']))
        matched_id = match['records'][0]['commitment']['id']
    elif match['status'] == 'matched':
        record = match['records'][0]
        matched_id = record['commitment']['id']
        kept_evidence, skipped = _without_duplicates(evidence, record.get('evidence') or [])
        if kept_evidence:
            action = 'add_evidence'
            status = 'ready_for_confirmation'
        else:
            action = 'none'
            status = 'already_recorded'
            clarifications.append(
                'This matches a commitment that is already recorded'
                + (' and the same evidence is already there.' if evidence or skipped else '.')
                + ' Nothing new will be saved.'
            )
    else:
        missing = [field for field in REQUIRED_COMMITMENT_FIELDS if grounded.get(field) is None]
        if not missing:
            action = 'create_commitment_and_evidence' if evidence else 'create_commitment'
            status = 'ready_for_confirmation'
            kept_evidence = list(evidence)
            commitment_out, defaults = _complete_commitment(grounded)
        elif evidence:
            kept_evidence = []
            clarifications.append(_unattached_question(grounded, missing))
            commitment_out = _partial_commitment(grounded)
        elif not _has_business_signal(text, grounded):
            clarifications.append(
                'Describe a commitment or a piece of evidence: the other party, what was agreed, '
                'the quantity, the amount, and the due date.'
            )
        else:
            commitment_out = _partial_commitment(grounded)
            for field in missing:
                clarifications.append(_question_for(field, grounded, currency_gap))

    if payment and status != 'ready_for_confirmation' and status != 'already_recorded':
        useful = [field for field in REQUIRED_COMMITMENT_FIELDS if grounded.get(field) is not None]
        if len(useful) < 2 and not evidence and match['status'] == 'none':
            clarifications = [NOT_SUBMITTED + ' Describe the agreement or the evidence instead.']
            action = 'none'
            commitment_out = None
            kept_evidence = []
            status = 'needs_clarification'

    if status == 'needs_clarification':
        action = 'none'
        kept_evidence = []

    proposal = {
        'action': action,
        'commitment': commitment_out,
        'evidence': kept_evidence,
        'matched_commitment_id': matched_id if status != 'needs_clarification' or match['status'] == 'conflict' else matched_id,
        'defaults': defaults,
        'skipped_duplicate_evidence': skipped if status == 'ready_for_confirmation' else 0,
    }
    if status == 'needs_clarification' and match['status'] != 'conflict':
        proposal['matched_commitment_id'] = None
    return {
        'status': status,
        'clarifications': _unique(clarifications),
        'proposal': proposal,
        'payment_status': 'NOT_SUBMITTED',
        'payment_wording': NOT_SUBMITTED,
    }


def accept_confirmation(text: str, proposal: Any, records: list[dict]) -> dict:
    """Accept a proposal only when the same checks still support it."""
    if not isinstance(proposal, dict):
        raise IntakeRejection(['The confirmation did not include a proposal.'])
    allowed = {
        'action', 'commitment', 'evidence', 'matched_commitment_id',
        'defaults', 'skipped_duplicate_evidence',
    }
    if _unknown_keys(proposal, allowed):
        raise IntakeRejection(['The confirmation included a field this record does not have. Nothing was saved.'])
    commitment = proposal.get('commitment') or {}
    evidence = proposal.get('evidence') or []
    if not isinstance(commitment, dict) or not isinstance(evidence, list):
        raise IntakeRejection(['The confirmation did not include a usable proposal.'])
    if _unknown_keys(commitment, set(COMMITMENT_FIELDS)) or any(
        not isinstance(item, dict) or _unknown_keys(item, {'kind', 'value', 'note', 'source'})
        for item in evidence
    ):
        raise IntakeRejection(['The confirmation included a field this record does not have. Nothing was saved.'])
    prepared = prepare_intake(text, {'commitment': commitment, 'evidence': evidence}, records)
    if prepared['status'] == 'ready_for_confirmation' and _same_proposal(proposal, prepared['proposal']):
        return {'write': True, 'prepared': prepared}
    if prepared['status'] == 'already_recorded':
        return {'write': False, 'prepared': prepared}
    message = prepared['clarifications'] or [
        'A confirmed field is not supported by the text, or it does not match one existing record. Nothing was saved.',
    ]
    raise IntakeRejection(message)


def model_catalogue(records: list[dict]) -> list[dict]:
    """Terms the extractor may match. Notes, reviews, and payment destinations stay out."""
    catalogue = []
    for record in records[:30]:
        commitment = record['commitment']
        catalogue.append({
            'id': commitment['id'],
            'counterparty': commitment.get('counterparty') or commitment.get('supplier'),
            'commitment_kind': commitment.get('commitment_kind') or DEFAULT_COMMITMENT_KIND,
            'description': commitment['description'],
            'quantity': commitment['quantity'],
            'unit': commitment['unit'],
            'amount': commitment['amount'],
            'currency': commitment['currency'],
            'due_date': str(commitment['due_date'])[:10],
            'evidence': [
                {'kind': item['kind'], 'value': item['value']}
                for item in (record.get('evidence') or [])[-8:]
            ],
        })
    return catalogue


def _ground(text: str, extraction: Any) -> tuple[dict, list[dict], bool]:
    raw = extraction if isinstance(extraction, dict) else {}
    raw_commitment = raw.get('commitment') if isinstance(raw.get('commitment'), dict) else {}
    grounded = {field: None for field in COMMITMENT_FIELDS}
    grounded['counterparty'] = _party(raw_commitment.get('counterparty') or raw_commitment.get('supplier'), text)
    description = _sentence(raw_commitment.get('description'), text, 500)
    grounded['description'] = description if description and _words_supported(description, text) else None
    grounded['quantity'] = _positive_number(raw_commitment.get('quantity'), text)
    grounded['unit'] = _unit(raw_commitment.get('unit'), text)
    grounded['amount'] = _positive_number(raw_commitment.get('amount'), text)
    grounded['currency'] = _currency(raw_commitment.get('currency'), text)
    grounded['due_date'] = _date(raw_commitment.get('due_date'), text)
    kind = _kind_label(raw_commitment.get('commitment_kind'), text)
    grounded['commitment_kind'] = kind
    condition = _sentence(raw_commitment.get('payment_condition'), text, 300)
    grounded['payment_condition'] = condition if condition and _words_supported(condition, text) else None
    evidence = _ground_evidence(raw.get('evidence'), text)
    currency_gap = grounded['currency'] is None and '$' in text
    return grounded, evidence, currency_gap


def _ground_evidence(raw: Any, text: str) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get('kind')
        if not isinstance(kind, str):
            continue
        kind = kind.strip().casefold()
        if kind not in EVIDENCE_KINDS or not _kind_supported(kind, text):
            continue
        note = _sentence(item.get('note'), text, 1000) or ''
        if note and not _words_supported(note, text):
            note = ''
        source = _sentence(item.get('source'), text, 100)
        if not source or source.casefold() not in text.casefold():
            source = 'intake'
        if kind == 'note':
            if not note:
                continue
            kept.append({'kind': 'note', 'value': None, 'note': note, 'source': source})
            continue
        value = _non_negative_number(item.get('value'), text)
        if value is None:
            continue
        kept.append({'kind': kind, 'value': value, 'note': note, 'source': source})
    return kept


def _complete_commitment(grounded: dict) -> tuple[dict, list[str]]:
    commitment = {field: grounded[field] for field in REQUIRED_COMMITMENT_FIELDS}
    defaults = []
    if grounded.get('commitment_kind'):
        commitment['commitment_kind'] = grounded['commitment_kind']
    else:
        commitment['commitment_kind'] = DEFAULT_COMMITMENT_KIND
        defaults.append('commitment_kind')
    if grounded.get('payment_condition'):
        commitment['payment_condition'] = grounded['payment_condition']
    else:
        commitment['payment_condition'] = DEFAULT_PAYMENT_CONDITION
        defaults.append('payment_condition')
    commitment['currency'] = str(commitment['currency']).upper()
    return commitment, defaults


def _partial_commitment(grounded: dict) -> dict | None:
    partial = {field: grounded[field] for field in COMMITMENT_FIELDS if grounded.get(field) is not None}
    return partial or None


def _match_records(text: str, grounded: dict, records: list[dict]) -> dict:
    usable = [record for record in records if record.get('commitment') and record['commitment'].get('id')]
    if not usable:
        return {'status': 'none', 'records': [], 'conflicts': []}
    by_id = {record['commitment']['id']: record for record in usable}
    explicit = [by_id[found] for found in _uuids(text) if found in by_id]
    if len(explicit) > 1:
        return {'status': 'ambiguous', 'records': explicit, 'conflicts': []}
    if len(explicit) == 1:
        return _with_conflicts(explicit, grounded)

    code_hits = []
    text_codes = reference_codes(text)
    if text_codes:
        for record in usable:
            if reference_codes(record['commitment'].get('description') or '') & text_codes:
                code_hits.append(record)
    if len(code_hits) > 1:
        return {'status': 'ambiguous', 'records': code_hits, 'conflicts': []}
    if len(code_hits) == 1:
        return _with_conflicts(code_hits, grounded)

    party = grounded.get('counterparty')
    pool = usable
    if party:
        pool = [record for record in usable if _squash(_party_of(record)) == _squash(party)]
        if party and not pool:
            return {'status': 'none', 'records': [], 'conflicts': []}
    described = [record for record in pool if _description_overlap(record, text)]
    if len(described) > 1:
        return {'status': 'ambiguous', 'records': described, 'conflicts': []}
    if len(described) == 1:
        return _with_conflicts(described, grounded)
    return {'status': 'none', 'records': [], 'conflicts': []}


def _with_conflicts(records: list[dict], grounded: dict) -> dict:
    conflicts = _commercial_conflicts(records[0], grounded)
    if conflicts:
        return {'status': 'conflict', 'records': records, 'conflicts': conflicts}
    return {'status': 'matched', 'records': records, 'conflicts': []}


def _commercial_conflicts(record: dict, grounded: dict) -> list[str]:
    commitment = record['commitment']
    conflicts = []
    party = grounded.get('counterparty')
    if party and _squash(party) != _squash(_party_of(record)):
        conflicts.append('counterparty')
    if grounded.get('quantity') is not None and abs(float(grounded['quantity']) - float(commitment['quantity'])) > 1e-9:
        conflicts.append('quantity')
    if grounded.get('amount') is not None and abs(float(grounded['amount']) - float(commitment['amount'])) > 1e-9:
        conflicts.append('amount')
    if grounded.get('unit') and not _units_compatible(str(grounded['unit']), str(commitment.get('unit') or '')):
        conflicts.append('unit')
    if grounded.get('currency') and str(grounded['currency']).upper() != str(commitment.get('currency') or '').upper():
        conflicts.append('currency')
    if grounded.get('due_date') and str(grounded['due_date'])[:10] != str(commitment.get('due_date') or '')[:10]:
        conflicts.append('due_date')
    return conflicts


def _description_overlap(record: dict, text: str) -> bool:
    tokens = set(content_words(record['commitment'].get('description') or ''))
    overlap = tokens & set(content_words(text))
    if len(overlap) >= 2:
        return True
    if len(tokens) == 1 and tokens <= set(content_words(text)):
        word = next(iter(tokens))
        return len(word) >= 5
    return False


def _without_duplicates(evidence: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    kept = []
    skipped = 0
    for item in evidence:
        if _is_duplicate(item, existing) or _is_duplicate(item, kept):
            skipped += 1
        else:
            kept.append(item)
    return kept, skipped


def _is_duplicate(item: dict, existing: list[dict]) -> bool:
    for prior in existing:
        if prior.get('kind') != item['kind']:
            continue
        if item['kind'] == 'note':
            if item.get('note') and _squash(item['note']) == _squash(prior.get('note') or ''):
                return True
            continue
        if item.get('value') is None or prior.get('value') is None:
            continue
        if abs(float(item['value']) - float(prior['value'])) <= 1e-9:
            return True
    return False


def _ambiguous_question(records: list[dict]) -> str:
    parts = []
    for record in records[:3]:
        commitment = record['commitment']
        parts.append(
            f"{_party_of(record)} — {commitment.get('description')} ({commitment.get('id')})"
        )
    return (
        'More than one commitment could match. Name the agreement or include its id. '
        'Candidates: ' + '; '.join(parts) + '.'
    )


def _conflict_question(record: dict, conflicts: list[str]) -> str:
    commitment = record['commitment']
    fields = ', '.join(conflicts)
    return (
        f"This refers to the existing commitment with {_party_of(record)} "
        f"({commitment.get('description')}), but {fields} differs from the recorded terms. "
        'Nothing was saved. Intake will not change those terms. Amend that commitment, or describe a different agreement.'
    )


def _unattached_question(grounded: dict, missing: list[str]) -> str:
    party = grounded.get('counterparty')
    missing_text = ', '.join(missing)
    if party:
        return (
            f"No single commitment with {party} matches this evidence. "
            f"Name the existing agreement, or add the missing terms: {missing_text}."
        )
    return (
        'This evidence is not attached to one existing commitment. '
        f'Name the agreement or include its id, or add the missing terms: {missing_text}.'
    )


def _question_for(field: str, grounded: dict, currency_gap: bool) -> str:
    if field == 'currency' and currency_gap:
        amount = grounded.get('amount')
        shown = f'{amount:g}' if isinstance(amount, (int, float)) else 'that amount'
        return f'Which currency is {shown} in? The symbol $ does not name a currency.'
    return FIELD_QUESTIONS[field]


def _has_business_signal(text: str, grounded: dict) -> bool:
    if any(grounded.get(field) is not None for field in REQUIRED_COMMITMENT_FIELDS):
        return True
    folded = text.casefold()
    hints = (
        'agree', 'agreed', 'owe', 'invoice', 'deliver', 'received', 'milestone',
        'repay', 'expense', 'due', 'shipment', 'billed',
    )
    return any(hint in folded for hint in hints)


def _payment_request(text: str) -> bool:
    folded = text.casefold()
    return any(phrase in folded for phrase in PAYMENT_PHRASES)


def _same_proposal(submitted: dict, canonical: dict) -> bool:
    if submitted.get('action') != canonical.get('action'):
        return False
    if (submitted.get('matched_commitment_id') or None) != (canonical.get('matched_commitment_id') or None):
        return False
    if not _same_commitment(submitted.get('commitment'), canonical.get('commitment')):
        return False
    return _same_evidence(submitted.get('evidence') or [], canonical.get('evidence') or [])


def _same_commitment(left: Any, right: Any) -> bool:
    if left is None or left == {}:
        return right is None
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for field in COMMITMENT_FIELDS:
        if _normal_field(field, left.get(field)) != _normal_field(field, right.get(field)):
            return False
    return True


def _same_evidence(left: list, right: list) -> bool:
    if len(left) != len(right):
        return False
    for item, other in zip(left, right):
        if not isinstance(item, dict):
            return False
        if item.get('kind') != other.get('kind'):
            return False
        if _normal_field('quantity', item.get('value')) != _normal_field('quantity', other.get('value')):
            return False
        if _squash(str(item.get('note') or '')) != _squash(str(other.get('note') or '')):
            return False
        if (item.get('source') or 'intake') != other.get('source'):
            return False
    return True


def _normal_field(field: str, value: Any):
    if value is None or value == '':
        return None
    if field in {'quantity', 'amount'}:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        if number.is_integer():
            return int(number)
        return round(number, 6)
    if field == 'currency' and isinstance(value, str):
        return value.upper()
    if isinstance(value, str):
        return ' '.join(value.split())
    return value


def _unknown_keys(payload: dict, allowed: set[str]) -> bool:
    return any(key not in allowed for key in payload)


def _party(value: Any, text: str) -> str | None:
    party = _sentence(value, text, 120)
    if not party:
        return None
    tokens = [token for token in re.findall(r"[a-z0-9]+", party.casefold()) if token not in STOPWORDS and len(token) > 1]
    if not tokens:
        return None
    raw = re.findall(r"[a-z0-9]+", text.casefold())
    present = set(raw)
    supported = [token for token in tokens if token in present]
    if not supported or ' '.join(supported) not in ' '.join(raw):
        return None
    if supported == tokens:
        return party
    kept = [word for word in party.split() if re.sub(r'[^a-z0-9]', '', word.casefold()) in supported]
    rebuilt = ' '.join(kept)
    return rebuilt or None


def _sentence(value: Any, text: str, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = ' '.join(value.split())
    if not cleaned or len(cleaned) > limit:
        return None
    return cleaned


def _words_supported(value: str, text: str) -> bool:
    wanted = content_words(value)
    if not wanted:
        return False
    raw = set(re.findall(r"[a-z0-9]+", text.casefold()))
    for word in wanted:
        if word in raw:
            continue
        if any(token.startswith(word) or word.startswith(token) for token in raw if len(token) >= 4 and len(word) >= 4):
            continue
        return False
    return True


def content_words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    kept = []
    for word in words:
        if word in STOPWORDS or word.isdigit() or len(word) <= 2:
            continue
        kept.append(word)
    return kept


def reference_codes(value: str) -> set[str]:
    folded = value.casefold()
    found = set(re.findall(r"\b[a-z]{1,12}-\d{2,}\b", folded))
    found.update(re.findall(r"\b[a-z]{2,}\d{2,}\b", folded))
    return found


def _positive_number(value: Any, text: str):
    number = _number_value(value)
    if number is None or number <= 0 or not _number_in_text(number, text):
        return None
    return _canonical_number(number)


def _non_negative_number(value: Any, text: str):
    number = _number_value(value)
    if number is None or number < 0 or not _number_in_text(number, text):
        return None
    return _canonical_number(number)


def _number_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(',', ''))
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _canonical_number(number: float):
    if float(number).is_integer():
        return int(number)
    return round(float(number), 6)


def _number_in_text(number: float, text: str) -> bool:
    visible = re.sub(
        r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b',
        ' ',
        text,
    )
    if float(number).is_integer():
        whole = int(number)
        forms = [str(whole)]
        if abs(whole) >= 1000:
            forms.append(f'{whole:,}')
    else:
        forms = [f'{float(number):.6f}'.rstrip('0').rstrip('.')]
    boundary = r'(?<![\dA-Za-z-]){}(?![\dA-Za-z-])'
    return any(re.search(boundary.format(re.escape(form)), visible) is not None for form in forms)


def _unit(value: Any, text: str) -> str | None:
    unit = _sentence(value, text, 40)
    if not unit or not _unit_supported(unit, text):
        return None
    return unit


def _unit_supported(unit: str, text: str) -> bool:
    folded_unit = unit.casefold()
    folded = text.casefold()
    if re.search(r'(?<![a-z0-9])' + re.escape(folded_unit) + r'(?![a-z0-9])', folded):
        return True
    singular = folded_unit[:-1] if folded_unit.endswith('s') else folded_unit
    if len(singular) < 3:
        return False
    return re.search(r'(?<![a-z0-9])' + re.escape(singular) + r's?(?![a-z0-9])', folded) is not None


def _units_compatible(left: str, right: str) -> bool:
    a = left.casefold().strip()
    b = right.casefold().strip()
    if a == b:
        return True
    stem_a = a[:-1] if a.endswith('s') else a
    stem_b = b[:-1] if b.endswith('s') else b
    return stem_a == stem_b and len(stem_a) >= 3


def _currency(value: Any, text: str) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    if not re.fullmatch(r'[A-Z]{3,8}', code):
        return None
    if not _currency_supported(code, text):
        return None
    return code


def _currency_supported(code: str, text: str) -> bool:
    folded = text.casefold()
    if re.search(r'\b' + re.escape(code.casefold()) + r'\b', folded):
        return True
    symbol = CURRENCY_SYMBOLS.get(code)
    if symbol and symbol in text:
        return True
    return any(alias in folded for alias in CURRENCY_ALIASES.get(code, ()))


def _date(value: Any, text: str) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    iso = parsed.date().isoformat()
    if not _date_supported(iso, text):
        return None
    return iso


def _date_supported(iso: str, text: str) -> bool:
    year_text, month_text, day_text = iso.split('-')
    year, month, day = int(year_text), int(month_text), int(day_text)
    if iso in text or f'{year}-{month}-{day}' in text:
        return True
    folded = text.casefold()
    if str(year) not in folded:
        return False
    names = [name for name, number in MONTHS.items() if number == month]
    if not any(re.search(r'\b' + re.escape(name) + r'\b', folded) for name in names):
        return False
    return re.search(r'(?<!\d)' + str(day) + r'(?!\d)', folded) is not None


def _kind_label(value: Any, text: str) -> str | None:
    label = _sentence(value, text, 48)
    if not label or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,47}', label):
        return None
    if label.casefold() == DEFAULT_COMMITMENT_KIND and DEFAULT_COMMITMENT_KIND not in text.casefold():
        return None
    if not _words_supported(label, text) and label.casefold() not in text.casefold():
        return None
    return label


def _kind_supported(kind: str, text: str) -> bool:
    folded = text.casefold()
    return any(re.search(r'\b' + re.escape(cue) + r'\b', folded) for cue in KIND_CUES.get(kind, ()))


def _party_of(record: dict) -> str:
    commitment = record['commitment']
    return str(commitment.get('counterparty') or commitment.get('supplier') or 'the other party')


def _squash(value: str) -> str:
    return re.sub(r'\s+', ' ', value.casefold()).strip()


def _uuids(text: str) -> list[str]:
    return re.findall(
        r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b',
        text,
    )


def _unique(items: list[str]) -> list[str]:
    seen = set()
    kept = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        kept.append(item)
    return kept
