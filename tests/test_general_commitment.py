"""A non-supplier commitment uses the same lifecycle as the carton demo."""

from fastapi.testclient import TestClient

from app import main
from tests.test_public_hackathon import login, use_db


def test_a_milestone_agreement_runs_the_same_lifecycle_without_a_special_workflow(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setattr('app.finance.observe_official_network', lambda: 'mainnet')
    with TestClient(main.app) as owner, TestClient(main.app) as stranger:
        login(owner, monkeypatch, 'jordan.testnet')
        login(stranger, monkeypatch, 'sam.testnet')
        assert owner.post('/api/workspaces').status_code == 201
        assert stranger.post('/api/workspaces').status_code == 201

        created = owner.post('/api/commitments', json={
            'counterparty': 'Jordan Lee',
            'commitment_kind': 'milestone',
            'description': 'Design the onboarding screens. Three milestones, fee released after each accepted screen is recorded.',
            'quantity': 3,
            'unit': 'milestones',
            'amount': 3000,
            'currency': 'USD',
            'due_date': '2026-12-01',
            'payment_condition': 'Release the fee only after the agreed milestones are accepted.',
        })
        assert created.status_code == 201, created.text
        record = created.json()
        cid = record['commitment']['id']
        assert record['commitment']['supplier'] == 'Jordan Lee'
        assert record['commitment']['counterparty'] == 'Jordan Lee'
        assert record['commitment']['commitment_kind'] == 'milestone'
        assert record['commitment']['unit'] == 'milestones'
        assert record['assessment']['status'] == 'AWAITING_EVIDENCE'

        short = owner.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'obligation_fulfilled', 'value': 2,
            'note': 'Two screens accepted. The third is still outstanding.',
            'source': 'acceptance_note',
        }).json()['evidence'][-1]
        claimed = owner.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'amount_claimed', 'value': 3000,
            'note': 'Invoice for the full design fee.',
            'source': 'invoice',
        }).json()['evidence'][-1]
        for evidence_id in (short['id'], claimed['id']):
            reviewed = owner.post(f'/api/evidence/{evidence_id}/reviews', json={
                'status': 'VERIFIED', 'reviewer': 'Owner', 'note': 'Checked against the acceptance note.',
            })
            assert reviewed.status_code == 201

        assessed = owner.get(f'/api/commitments/{cid}').json()
        assert assessed['assessment']['status'] == 'DISCREPANCY'
        assert assessed['assessment']['financial_exposure'] == 1000
        assert any(issue['code'] == 'QUANTITY_SHORTFALL' for issue in assessed['assessment']['issues'])
        assert any('Hold payment approval' in step for step in assessed['assessment']['next_actions'])

        action = owner.post(f'/api/commitments/{cid}/actions', json={
            'action_type': 'CONFIRM_OBLIGATION',
            'description': 'Ask Jordan Lee to finish the remaining milestone before any release.',
        })
        assert action.status_code == 201, action.text
        action_id = action.json()['id']
        approved = owner.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'APPROVED', 'actor': 'Owner',
            'note': 'A person authorised the follow-up. This does not release the fee.',
        })
        assert approved.status_code == 201

        policy = owner.put(f'/api/commitments/{cid}/payment-policy', json={
            'enabled': True, 'max_amount': 3000,
            'allowed_destination': 'jordan-lee.testnet',
            'require_full_delivery': True,
            'require_invoice_match': True,
        })
        assert policy.status_code == 200
        blocked = owner.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 3000, 'currency': 'USD', 'destination': 'jordan-lee.testnet',
        })
        assert blocked.status_code == 201
        assert blocked.json()['proposal']['status'] == 'BLOCKED'
        assert blocked.json()['proposal']['transfer_executed'] is False
        assert 'DELIVERY_SHORTFALL' in {reason['code'] for reason in blocked.json()['proposal']['reasons']}
        refused = owner.post(f'/api/commitments/{cid}/payment-decisions', json={
            'proposal_id': blocked.json()['id'], 'decision': 'APPROVED', 'actor': 'Owner',
        })
        assert refused.status_code == 409

        finished = owner.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'obligation_fulfilled', 'value': 3,
            'note': 'The third screen was accepted.',
            'source': 'acceptance_note',
        }).json()['evidence'][-1]
        owner.post(f"/api/evidence/{finished['id']}/reviews", json={
            'status': 'VERIFIED', 'reviewer': 'Owner', 'note': 'All three milestones are accepted.',
        })
        covered = owner.get(f'/api/commitments/{cid}').json()
        assert covered['assessment']['status'] == 'ON_TRACK'
        waiting = owner.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 3000, 'currency': 'USD', 'destination': 'jordan-lee.testnet',
        })
        assert waiting.json()['proposal']['status'] == 'AWAITING_HUMAN_APPROVAL'
        decision = owner.post(f'/api/commitments/{cid}/payment-decisions', json={
            'proposal_id': waiting.json()['id'], 'decision': 'APPROVED', 'actor': 'Owner',
        })
        assert decision.status_code == 201, decision.text
        assert decision.json()['outcome']['financial_action'] == 'NOT_SUBMITTED'
        assert decision.json()['outcome']['transfer_executed'] is False
        saved = owner.get(f'/api/commitments/{cid}').json()
        latest = saved['payment_proposals'][-1]
        assert latest['status'] == 'APPROVED_NOT_SUBMITTED'
        assert latest['proposal']['transfer_executed'] is False
        assert latest['proposal']['transaction_hash'] is None

        reported = owner.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'EXECUTION_REPORTED', 'actor': 'Owner',
            'note': 'Jordan Lee was asked to close the last milestone. No transfer was sent.',
        })
        assert reported.status_code == 201
        still_short = owner.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner',
            'evidence_id': short['id'],
        })
        assert still_short.status_code == 422
        resolved = owner.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner',
            'evidence_id': finished['id'],
            'note': 'The accepted milestones cover the agreement.',
        })
        assert resolved.status_code == 201, resolved.text
        final = owner.get(f'/api/commitments/{cid}').json()
        stored = next(item for item in final['actions'] if item['id'] == action_id)
        assert [event['event_type'] for event in stored['events']] == [
            'APPROVED', 'EXECUTION_REPORTED', 'OUTCOME_VERIFIED',
        ]
        assert stranger.get(f'/api/commitments/{cid}').status_code == 404

        demo = TestClient(main.app)
        assert demo.post('/api/demo/trials').status_code == 201
        trial = demo.get('/api/commitments').json()
        paper = next(item for item in trial if item['commitment']['unit'] == 'cartons' and item['commitment']['quantity'] == 50)
        assert paper['commitment']['commitment_kind'] == 'supplier_delivery'
        assert paper['assessment']['financial_exposure'] == 40
