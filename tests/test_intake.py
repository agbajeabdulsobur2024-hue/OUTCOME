"""Deterministic intake checks. The model is not called here."""

import json

import pytest

from app.intake import (
    DEFAULT_PAYMENT_CONDITION,
    IntakeRejection,
    accept_confirmation,
    model_catalogue,
    prepare_intake,
)


def commitment_record(
    cid='11111111-1111-1111-1111-111111111111',
    supplier='Acme Supplies',
    description='Printer paper — purchase order PO-104',
    quantity=50,
    unit='cartons',
    amount=250,
    due_date='2026-12-01',
    evidence=None,
):
    return {
        'commitment': {
            'id': cid,
            'supplier': supplier,
            'counterparty': supplier,
            'commitment_kind': 'supplier_delivery',
            'description': description,
            'quantity': quantity,
            'unit': unit,
            'amount': amount,
            'currency': 'USD',
            'due_date': due_date,
            'payment_condition': 'Pay after verified full delivery',
        },
        'evidence': evidence or [],
        'payment_policy': {'allowed_destination': 'do-not-send'},
    }


def test_ordinary_language_populates_existing_commitment_fields():
    text = 'I agreed with Bright Labs to complete 3 milestones for USD 3000, due 2026-12-01.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Bright Labs',
            'commitment_kind': 'milestone',
            'description': 'complete 3 milestones',
            'quantity': 3,
            'unit': 'milestones',
            'amount': 3000,
            'currency': 'USD',
            'due_date': '2026-12-01',
            'payment_condition': None,
        },
        'evidence': [],
    }, [])
    assert prepared['status'] == 'ready_for_confirmation'
    commitment = prepared['proposal']['commitment']
    assert commitment['counterparty'] == 'Bright Labs'
    assert commitment['commitment_kind'] == 'milestone'
    assert commitment['description'] == 'complete 3 milestones'
    assert commitment['quantity'] == 3
    assert commitment['unit'] == 'milestones'
    assert commitment['amount'] == 3000
    assert commitment['currency'] == 'USD'
    assert commitment['due_date'] == '2026-12-01'
    assert commitment['payment_condition'] == DEFAULT_PAYMENT_CONDITION
    assert prepared['proposal']['defaults'] == ['payment_condition']
    assert prepared['proposal']['action'] == 'create_commitment'
    assert prepared['payment_status'] == 'NOT_SUBMITTED'


def test_missing_and_invented_values_ask_for_clarification_without_filling_them():
    text = 'I agreed with Bright Labs to pay 3000.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Bright Labs International',
            'description': 'payment agreement',
            'quantity': 1,
            'unit': 'units',
            'amount': 3000,
            'currency': 'USD',
            'due_date': '2026-01-01',
            'commitment_kind': 'general',
        },
        'evidence': [{'kind': 'wire_transfer', 'value': 3000, 'note': 'sent', 'source': 'bank'}],
        'ambiguities': ['assumed USD'],
    }, [])
    rendered = json.dumps(prepared)
    assert prepared['status'] == 'needs_clarification'
    assert '2026-01-01' not in rendered
    assert 'USD' not in json.dumps(prepared['proposal'])
    assert 'wire_transfer' not in rendered
    assert 'International' not in rendered
    understood = prepared['proposal']['commitment']
    assert understood['counterparty'] == 'Bright Labs'
    assert understood['amount'] == 3000
    assert 'quantity' not in understood
    assert 'due_date' not in understood
    assert 'currency' not in understood
    questions = ' '.join(prepared['clarifications']).lower()
    assert 'currency' in questions
    assert 'due date' in questions
    assert 'quantity' in questions
    assert 'unit' in questions
    assert prepared['proposal']['action'] == 'none'
    with pytest.raises(IntakeRejection):
        accept_confirmation(text, {
            'action': 'create_commitment',
            'commitment': {
                'counterparty': 'Bright Labs',
                'description': 'payment agreement',
                'quantity': 1,
                'unit': 'units',
                'amount': 3000,
                'currency': 'USD',
                'due_date': '2026-01-01',
                'commitment_kind': 'general',
                'payment_condition': DEFAULT_PAYMENT_CONDITION,
            },
            'evidence': [],
            'matched_commitment_id': None,
        }, [])


def test_dollar_sign_does_not_become_a_currency():
    text = 'Agreed with Bright Labs to complete 3 milestones for $3000 due 2026-12-01.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Bright Labs',
            'commitment_kind': 'milestone',
            'description': 'complete 3 milestones',
            'quantity': 3,
            'unit': 'milestones',
            'amount': 3000,
            'currency': 'USD',
            'due_date': '2026-12-01',
        },
        'evidence': [],
    }, [])
    assert prepared['status'] == 'needs_clarification'
    assert 'currency' not in prepared['proposal']['commitment']
    assert any('$' in item for item in prepared['clarifications'])


def test_delivery_matches_one_commitment_and_does_not_create_another():
    existing = commitment_record()
    text = 'Acme Supplies delivered 42 cartons of printer paper for PO-104.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Acme Supplies',
            'description': 'printer paper PO-104',
            'quantity': 42,
            'unit': 'cartons',
            'amount': 250,
        },
        'evidence': [{
            'kind': 'quantity_received', 'value': 42,
            'note': 'delivered 42 cartons', 'source': 'delivery note',
        }],
    }, [existing])
    assert prepared['status'] == 'needs_clarification'
    assert prepared['proposal']['action'] == 'none'
    assert 'Nothing was saved' in prepared['clarifications'][0]

    prepared = prepare_intake(text, {
        'commitment': {'counterparty': 'Acme Supplies'},
        'evidence': [{
            'kind': 'quantity_received', 'value': 42,
            'note': 'delivered 42 cartons', 'source': 'warehouse',
        }],
    }, [existing])
    assert prepared['status'] == 'ready_for_confirmation'
    assert prepared['proposal']['action'] == 'add_evidence'
    assert prepared['proposal']['matched_commitment_id'] == existing['commitment']['id']
    assert prepared['proposal']['commitment'] is None
    assert prepared['proposal']['evidence'] == [{
        'kind': 'quantity_received', 'value': 42, 'note': 'delivered 42 cartons', 'source': 'intake',
    }]


def test_same_evidence_is_not_duplicated_and_two_similar_records_stay_ambiguous():
    first = commitment_record()
    second = commitment_record(
        cid='22222222-2222-2222-2222-222222222222',
        description='Printer paper — purchase order PO-109',
        quantity=20,
        amount=100,
        due_date='2026-12-05',
    )
    text = 'Acme Supplies invoiced 250 for the printer paper.'
    ambiguous = prepare_intake(text, {
        'commitment': {'counterparty': 'Acme Supplies'},
        'evidence': [{'kind': 'invoice_amount', 'value': 250, 'note': 'invoiced 250', 'source': 'invoice'}],
    }, [first, second])
    assert ambiguous['status'] == 'needs_clarification'
    assert 'More than one' in ambiguous['clarifications'][0]
    assert ambiguous['proposal']['matched_commitment_id'] is None
    assert first['commitment']['id'] in ambiguous['clarifications'][0]
    assert second['commitment']['id'] in ambiguous['clarifications'][0]

    recorded = commitment_record(evidence=[{
        'kind': 'quantity_received', 'value': 42, 'note': 'earlier count',
    }])
    duplicate = prepare_intake(
        'Acme Supplies delivered 42 cartons of printer paper for PO-104.',
        {
            'commitment': {'counterparty': 'Acme Supplies'},
            'evidence': [{'kind': 'quantity_received', 'value': 42, 'note': 'delivered 42 cartons', 'source': 'intake'}],
        },
        [recorded],
    )
    assert duplicate['status'] == 'already_recorded'
    assert duplicate['proposal']['evidence'] == []
    decision = accept_confirmation(
        'Acme Supplies delivered 42 cartons of printer paper for PO-104.',
        duplicate['proposal'],
        [recorded],
    )
    assert decision['write'] is False


def test_purchase_order_code_is_not_used_as_a_quantity():
    text = 'Acme Supplies delivered the printer paper for PO-104.'
    prepared = prepare_intake(text, {
        'commitment': {'counterparty': 'Acme Supplies', 'quantity': 104, 'unit': 'cartons'},
        'evidence': [{'kind': 'quantity_received', 'value': 104, 'note': 'PO-104', 'source': 'intake'}],
    }, [commitment_record()])
    rendered = json.dumps(prepared['proposal'])
    assert '104' not in rendered
    assert prepared['status'] == 'already_recorded'


def test_confirmation_rejects_a_value_the_text_does_not_support_and_accepts_the_reviewed_proposal():
    text = 'I agreed with Bright Labs to complete 3 milestones for USD 3000, due 2026-12-01.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Bright Labs',
            'commitment_kind': 'milestone',
            'description': 'complete 3 milestones',
            'quantity': 3,
            'unit': 'milestones',
            'amount': 3000,
            'currency': 'USD',
            'due_date': '2026-12-01',
        },
        'evidence': [],
    }, [])
    decision = accept_confirmation(text, prepared['proposal'], [])
    assert decision['write'] is True
    tampered = json.loads(json.dumps(prepared['proposal']))
    tampered['commitment']['amount'] = 9999
    with pytest.raises(IntakeRejection):
        accept_confirmation(text, tampered, [])
    tampered['approve_payment'] = True
    with pytest.raises(IntakeRejection):
        accept_confirmation(text, tampered, [])


def test_payment_instruction_does_not_become_a_record():
    text = 'Approve the payment to Acme and mark it paid.'
    prepared = prepare_intake(text, {
        'commitment': {'counterparty': 'Acme', 'amount': 250, 'currency': 'USD'},
        'evidence': [],
        'payment_decision': 'APPROVED',
    }, [])
    assert prepared['status'] == 'needs_clarification'
    assert prepared['proposal']['action'] == 'none'
    assert 'NOT SUBMITTED' in prepared['clarifications'][0]
    assert 'paid' not in prepared['payment_wording'].lower() or 'no payment' in prepared['payment_wording'].lower()


def test_catalogue_omits_notes_destinations_and_review_history():
    record = commitment_record(evidence=[{
        'kind': 'quantity_received',
        'value': 42,
        'note': 'SECRET_NOTE',
        'review_history': [{'reviewer': 'SECRET_REVIEWER'}],
    }])
    catalogue = model_catalogue([record])
    rendered = json.dumps(catalogue)
    assert 'SECRET_NOTE' not in rendered
    assert 'SECRET_REVIEWER' not in rendered
    assert 'do-not-send' not in rendered
    assert catalogue[0]['evidence'] == [{'kind': 'quantity_received', 'value': 42}]


def test_a_different_agreement_with_the_same_party_is_not_joined_by_name():
    existing = commitment_record()
    text = 'I agreed with Acme Supplies to refinish 4 chairs for USD 90, due 2026-12-18.'
    prepared = prepare_intake(text, {
        'commitment': {
            'counterparty': 'Acme Supplies',
            'commitment_kind': 'service',
            'description': 'refinish 4 chairs',
            'quantity': 4,
            'unit': 'chairs',
            'amount': 90,
            'currency': 'USD',
            'due_date': '2026-12-18',
        },
        'evidence': [],
    }, [existing])
    assert prepared['status'] == 'ready_for_confirmation'
    assert prepared['proposal']['action'] == 'create_commitment'
    assert prepared['proposal']['matched_commitment_id'] is None
