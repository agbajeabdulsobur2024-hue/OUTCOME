"""Intake over HTTP. The model call is stubbed; assessment and storage are real."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.ai import AIProviderError
from tests.test_ai import verified_provider_result
from tests.test_ai_api import displayable_brief
from tests.test_public_hackathon import login, use_db


ROOT = Path(__file__).resolve().parents[1]


def verified_extraction(extraction):
    return {
        'provider': 'NEAR AI Cloud',
        'model': 'Qwen/Qwen3.6-35B-A3B-FP8',
        'attestation_status': 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED',
        'verification': verified_provider_result(),
        'extraction': extraction,
    }


def paper_commitment():
    return {
        'counterparty': 'Acme Supplies',
        'commitment_kind': 'supplier_delivery',
        'description': 'Printer paper — purchase order PO-104',
        'quantity': 50,
        'unit': 'cartons',
        'amount': 250,
        'currency': 'USD',
        'due_date': '2026-12-01',
        'payment_condition': 'Pay after verified full delivery',
    }


def test_review_does_not_write_and_confirmation_runs_assessment_and_brief(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    calls = {'brief': 0}

    def fake_extract(text, catalogue):
        assert 'SECRET_NOTE' not in json.dumps(catalogue)
        return verified_extraction({
            'commitment': {'counterparty': 'Acme Supplies'},
            'evidence': [{
                'kind': 'quantity_received',
                'value': 42,
                'note': 'delivered 42 cartons',
                'source': 'warehouse',
            }],
        })

    def fake_brief(record):
        calls['brief'] += 1
        assert record['assessment']['status'] == 'DISCREPANCY'
        assert record['evidence'][-1]['review_status'] == 'UNREVIEWED'
        return displayable_brief('42 of 50 cartons are recorded. Hold the release.')

    monkeypatch.setattr(main, 'extract_business_event', fake_extract)
    monkeypatch.setattr(main, 'generate_operational_brief', fake_brief)
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json=paper_commitment())
        assert created.status_code == 201, created.text
        cid = created.json()['commitment']['id']
        before = client.get('/api/commitments').json()
        text = 'Acme Supplies delivered 42 cartons of printer paper for PO-104.'
        reviewed = client.post('/api/intake', json={'text': text})
        assert reviewed.status_code == 200, reviewed.text
        body = reviewed.json()
        assert body['writes_performed'] is False
        assert body['confirmation_required'] is True
        assert body['proposal']['action'] == 'add_evidence'
        assert body['proposal']['matched_commitment_id'] == cid
        assert body['extraction_attestation']['attestation_status'] == 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED'
        assert len(client.get('/api/commitments').json()) == len(before)
        assert client.get(f'/api/commitments/{cid}').json()['evidence'] == []

        confirmed = client.post('/api/intake/confirm', json={
            'text': text, 'confirmed': True, 'proposal': body['proposal'],
        })
        assert confirmed.status_code == 201, confirmed.text
        saved = confirmed.json()
        assert saved['wrote'] is True
        assert saved['payment_status'] == 'NOT_SUBMITTED'
        assert 'NOT SUBMITTED' in saved['payment_wording']
        assert saved['assessment']['status'] == 'DISCREPANCY'
        assert saved['assessment']['financial_exposure'] == 40
        assert any(issue['code'] == 'QUANTITY_SHORTFALL' for issue in saved['assessment']['issues'])
        assert saved['record']['evidence'][-1]['review_status'] == 'UNREVIEWED'
        assert saved['record']['evidence'][-1]['value'] == 42
        assert saved['record']['payment_decisions'] == []
        assert saved['insights']['brief']['summary'] == '42 of 50 cartons are recorded. Hold the release.'
        assert saved['insights']['attestation_status'] == 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED'
        assert calls['brief'] == 1
        again = client.post('/api/intake/confirm', json={
            'text': text, 'confirmed': True, 'proposal': body['proposal'],
        })
        assert again.status_code == 200, again.text
        assert again.json()['wrote'] is False
        assert len(client.get(f'/api/commitments/{cid}').json()['evidence']) == 1


def test_confirmation_is_required_and_a_failed_brief_does_not_change_the_assessment(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    text = 'I agreed with Bright Labs to complete 3 milestones for USD 3000, due 2026-12-01.'
    monkeypatch.setattr(main, 'extract_business_event', lambda text, catalogue: verified_extraction({
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
    }))
    with TestClient(main.app) as client:
        reviewed = client.post('/api/intake', json={'text': text})
        proposal = reviewed.json()['proposal']
        refused = client.post('/api/intake/confirm', json={
            'text': text, 'confirmed': False, 'proposal': proposal,
        })
        assert refused.status_code == 422
        assert client.get('/api/commitments').json() == []
        monkeypatch.setattr(main, 'generate_operational_brief', lambda record: (_ for _ in ()).throw(
            AIProviderError('verification failed')
        ))
        confirmed = client.post('/api/intake/confirm', json={
            'text': text, 'confirmed': True, 'proposal': proposal,
        })
        assert confirmed.status_code == 201, confirmed.text
        saved = confirmed.json()
        assert saved['assessment']['status'] == 'AWAITING_EVIDENCE'
        assert saved['insights'] is None
        assert 'not' in saved['insights_withheld_reason'].lower() or 'withheld' in saved['insights_withheld_reason'].lower()
        assert 'SECRET' not in json.dumps(saved)
        record = client.get(f"/api/commitments/{saved['commitment_id']}").json()
        assert record['ai_brief_history'] == []
        assert record['assessment']['status'] == 'AWAITING_EVIDENCE'
        assert record['payment_decisions'] == []


def test_unverified_extraction_and_unverified_brief_are_not_shown(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    monkeypatch.setattr(main, 'extract_business_event', lambda text, catalogue: (_ for _ in ()).throw(
        AIProviderError('NEAR AI response verification failed closed (VerificationError).')
    ))
    with TestClient(main.app) as client:
        failed = client.post('/api/intake', json={'text': 'Northwind delivered 8 boxes for the front desk.'})
        assert failed.status_code == 502
        assert client.get('/api/commitments').json() == []
        created = client.post('/api/commitments', json={
            'counterparty': 'Northwind',
            'description': 'Office boxes for the front desk',
            'quantity': 10, 'unit': 'boxes', 'amount': 80, 'currency': 'USD', 'due_date': '2026-12-20',
        })
        cid = created.json()['commitment']['id']
        monkeypatch.setattr(main, 'extract_business_event', lambda text, catalogue: verified_extraction({
            'commitment': {'counterparty': 'Northwind', 'description': 'front desk'},
            'evidence': [{'kind': 'quantity_received', 'value': 10, 'note': 'delivered 10 boxes', 'source': 'intake'}],
        }))
        reviewed = client.post('/api/intake', json={
            'text': 'Northwind delivered 10 boxes for the front desk.',
        })
        assert reviewed.status_code == 200, reviewed.text
        monkeypatch.setattr(main, 'generate_operational_brief', lambda record: {
            'provider': 'NEAR AI Cloud',
            'model': 'Qwen/Qwen3.6-35B-A3B-FP8',
            'brief': {'summary': 'SECRET_UNVERIFIED_BRIEF'},
            'verification': {'verified': True},
            'attestation_status': 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED',
        })
        confirmed = client.post('/api/intake/confirm', json={
            'text': 'Northwind delivered 10 boxes for the front desk.',
            'confirmed': True,
            'proposal': reviewed.json()['proposal'],
        })
        assert confirmed.status_code == 201, confirmed.text
        rendered = confirmed.text
        assert 'SECRET_UNVERIFIED_BRIEF' not in rendered
        saved = confirmed.json()
        assert saved['assessment']['status'] == 'ON_TRACK'
        assert saved['insights'] is None
        assert saved['payment_status'] == 'NOT_SUBMITTED'
        assert 'paid' not in saved['payment_wording'].lower()
        record = client.get(f'/api/commitments/{cid}').json()
        assert record['ai_brief_history'] == []
        assert record['evidence'][-1]['review_status'] == 'UNREVIEWED'


def test_intake_shares_the_ai_rate_limit_and_still_saves_after_the_limit(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    monkeypatch.setenv('OUTCOME_AI_RATE_LIMIT_PER_HOUR', '1')
    monkeypatch.setattr(main, 'extract_business_event', lambda text, catalogue: verified_extraction({
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
    }))
    monkeypatch.setattr(main, 'generate_operational_brief', lambda record: displayable_brief('Should not be called'))
    with TestClient(main.app) as client:
        text = 'I agreed with Bright Labs to complete 3 milestones for USD 3000, due 2026-12-01.'
        reviewed = client.post('/api/intake', json={'text': text})
        assert reviewed.status_code == 200, reviewed.text
        limited = client.post('/api/intake', json={'text': text})
        assert limited.status_code == 429
        confirmed = client.post('/api/intake/confirm', json={
            'text': text, 'confirmed': True, 'proposal': reviewed.json()['proposal'],
        })
        assert confirmed.status_code == 201, confirmed.text
        saved = confirmed.json()
        assert saved['assessment']['status'] == 'AWAITING_EVIDENCE'
        assert saved['insights'] is None
        assert 'rate limit' in saved['insights_withheld_reason'].lower()
        assert client.get(f"/api/commitments/{saved['commitment_id']}").json()['ai_brief_history'] == []


def test_hosted_and_other_workspaces_cannot_use_intake_to_cross_records(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    monkeypatch.setenv('OUTCOME_DB_BACKEND', 'sqlite')
    monkeypatch.setenv('VERCEL', '1')
    with TestClient(main.app) as client:
        blocked = client.post('/api/intake', json={'text': 'Acme Supplies delivered 42 cartons.'})
        assert blocked.status_code == 409
        assert client.get('/api/commitments').json() == []
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setattr(main, 'extract_business_event', lambda text, catalogue: verified_extraction({
        'commitment': {'counterparty': 'Acme Supplies'},
        'evidence': [{'kind': 'quantity_received', 'value': 42, 'note': 'delivered 42 cartons', 'source': 'intake'}],
    }))
    with TestClient(main.app) as owner, TestClient(main.app) as other:
        login(owner, monkeypatch, 'owner.testnet')
        login(other, monkeypatch, 'other.testnet')
        assert owner.post('/api/workspaces').status_code == 201
        assert other.post('/api/workspaces').status_code == 201
        created = owner.post('/api/commitments', json=paper_commitment())
        cid = created.json()['commitment']['id']
        reviewed = other.post('/api/intake', json={
            'text': 'Acme Supplies delivered 42 cartons of printer paper for PO-104.',
        })
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()['proposal']['matched_commitment_id'] != cid
        assert owner.get(f'/api/commitments/{cid}').json()['evidence'] == []
        assert other.get('/api/commitments').json() == []


def test_manual_form_remains_and_intake_is_absent_without_a_key(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.delenv('NEARAI_API_KEY', raising=False)
    monkeypatch.delenv('NEAR_AI_KEY', raising=False)
    page = (ROOT / 'static' / 'index.html').read_text()
    assert 'id="create"' in page
    assert 'id="intake"' in page
    assert 'Confirm and record' in page
    with TestClient(main.app) as client:
        disabled = client.post('/api/intake', json={'text': 'We agreed with Northwind on 10 boxes.'})
        assert disabled.status_code == 503
        created = client.post('/api/commitments', json={
            'supplier': 'Northwind', 'description': 'Office boxes for the front desk',
            'quantity': 10, 'unit': 'boxes', 'amount': 80, 'currency': 'USD', 'due_date': '2026-12-20',
        })
        assert created.status_code == 201, created.text
        assert created.json()['assessment']['status'] == 'AWAITING_EVIDENCE'
