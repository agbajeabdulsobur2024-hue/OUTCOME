import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app import anchors, auth_near, main


def use_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')


def signing_account(name='alice.testnet'):
    key = SigningKey.generate()
    public = 'ed25519:' + auth_near.b58encode(bytes(key.verify_key))
    return name, key, public


def login(client, monkeypatch, name='alice.testnet', keys=None):
    account, key, public = signing_account(name)
    registry = keys if keys is not None else {}
    registry[account] = public
    monkeypatch.setattr(auth_near, 'lookup_full_access_key', lambda account_id, public_key: registry.get(account_id) == public_key)
    challenge = client.post('/api/auth/challenge', json={'account_id': account})
    assert challenge.status_code == 201, challenge.text
    body = challenge.json()
    digest = auth_near.nep413_digest(body['message'], base64.b64decode(body['nonce']), body['recipient'])
    signature = base64.b64encode(key.sign(digest).signature).decode('ascii')
    session = client.post('/api/auth/session', json={
        'challenge_id': body['challenge_id'],
        'account_id': account,
        'public_key': public,
        'signature': signature,
    })
    assert session.status_code == 201, session.text
    assert session.json()['account_id'] == account
    return account


def commitment_body(description='Own order'):
    return {
        'supplier': 'Northwind', 'description': description,
        'quantity': 10, 'unit': 'boxes', 'amount': 80, 'currency': 'USD',
        'due_date': '2026-12-20',
    }


def test_visitor_explores_guided_demo_without_a_wallet(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        opened = client.get('/api/session')
        assert opened.json()['authenticated'] is False
        demo = client.post('/api/demo/trials')
        assert demo.status_code == 201, demo.text
        body = demo.json()
        assert body['synthetic_data'] is True
        descriptions = [item['commitment']['description'] for item in body['commitments']]
        first = next(item for item in body['commitments'] if item['commitment']['description'].startswith('Packaging boxes — purchase order PO-104'))
        second = next(item for item in body['commitments'] if 'PO-109' in item['commitment']['description'])
        assert first['commitment']['quantity'] == 100
        assert first['commitment']['amount'] == 500
        assert all(item['review_status'] == 'VERIFIED' for item in first['evidence'])
        assert {item['kind']: item['value'] for item in first['evidence']}['quantity_received'] == 80
        assert first['payment_policy']['configured'] is True
        assert first['payment_policy']['enabled'] is True
        assert first['payment_proposals'][0]['status'] == 'BLOCKED'
        assert 'DELIVERY_SHORTFALL' in {reason['code'] for reason in first['payment_proposals'][0]['proposal']['reasons']}
        assert first['actions'][0]['status'] == 'PROPOSED'
        assert second['commitment']['quantity'] == 50
        assert {item['kind']: item['value'] for item in second['evidence']}['quantity_received'] == 40
        assert body['patterns'][0]['pattern_type'] == 'REPEATED_SHORT_DELIVERY'
        assert 'PO-104' in descriptions[0] or any('PO-104' in item for item in descriptions)
        listed = client.get('/api/commitments')
        assert listed.status_code == 200
        assert len(listed.json()) == 2


def test_two_demo_visitors_and_two_signed_in_testers_are_isolated(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    keys = {}
    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        assert alice.post('/api/demo/trials').status_code == 201
        assert bob.post('/api/demo/trials').status_code == 201
        alice_demo = alice.get('/api/commitments').json()[0]['commitment']['id']
        assert bob.get(f'/api/commitments/{alice_demo}').status_code == 404
        assert bob.post(f'/api/commitments/{alice_demo}/evidence', json={
            'kind': 'note', 'value': None, 'note': 'tamper',
        }).status_code == 404
        login(alice, monkeypatch, 'alice.testnet', keys)
        login(bob, monkeypatch, 'bob.testnet', keys)
        assert alice.post('/api/workspaces').status_code == 201
        assert bob.post('/api/workspaces').status_code == 201
        alice_own = alice.post('/api/commitments', json=commitment_body('Alice order')).json()['commitment']['id']
        bob_own = bob.post('/api/commitments', json=commitment_body('Bob order')).json()['commitment']['id']
        assert alice.get(f'/api/commitments/{bob_own}').status_code == 404
        assert alice.post(f'/api/commitments/{bob_own}/evidence', json={
            'kind': 'quantity_received', 'value': 1, 'note': 'nope',
        }).status_code == 404
        visible = [item['commitment']['id'] for item in alice.get('/api/commitments').json()]
        assert alice_own in visible
        assert bob_own not in visible
        assert alice_demo not in visible


def test_wallet_login_requires_a_signed_challenge(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        rejected = client.post('/api/auth/challenge', json={'account_id': 'alice.near'})
        assert rejected.status_code == 422
        account, key, public = signing_account('carol.testnet')
        monkeypatch.setattr(auth_near, 'lookup_full_access_key', lambda account_id, public_key: False)
        challenge = client.post('/api/auth/challenge', json={'account_id': account})
        body = challenge.json()
        digest = auth_near.nep413_digest(body['message'], base64.b64decode(body['nonce']), body['recipient'])
        signature = base64.b64encode(key.sign(digest).signature).decode('ascii')
        unsigned = client.post('/api/auth/session', json={
            'challenge_id': body['challenge_id'], 'account_id': account,
            'public_key': public, 'signature': signature,
        })
        assert unsigned.status_code == 401
        monkeypatch.setattr(auth_near, 'lookup_full_access_key', lambda account_id, public_key: public_key == public and account_id == account)
        signed = client.post('/api/auth/session', json={
            'challenge_id': body['challenge_id'], 'account_id': account,
            'public_key': public, 'signature': signature,
        })
        assert signed.status_code == 201
        replay = client.post('/api/auth/session', json={
            'challenge_id': body['challenge_id'], 'account_id': account,
            'public_key': public, 'signature': signature,
        })
        assert replay.status_code == 401
        claimed = client.post('/api/auth/challenge', json={'account_id': 'mallory.testnet'})
        claimed_body = claimed.json()
        wrong = client.post('/api/auth/session', json={
            'challenge_id': claimed_body['challenge_id'], 'account_id': account,
            'public_key': public, 'signature': signature,
        })
        assert wrong.status_code == 401


def test_demo_expires_and_global_seed_still_refuses_to_overwrite(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json=commitment_body('Global leftover'))
        assert created.status_code == 201
        again = client.post('/api/demo/seed')
        assert again.status_code == 409
        assert len(client.get('/api/commitments', headers={'X-Outcome-Scope': 'global'}).json()) == 1
        demo = client.post('/api/demo/trials')
        assert demo.status_code == 201
        assert len(demo.json()['commitments']) == 2
        with main.connect() as db:
            db.execute(
                "UPDATE workspaces SET expires_at=? WHERE kind='demo'",
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
            )
        expired = client.get('/api/commitments', headers={'X-Outcome-Scope': 'demo'})
        assert expired.status_code == 410
        assert len(client.get('/api/commitments', headers={'X-Outcome-Scope': 'global'}).json()) == 1


def test_payment_block_human_approval_and_resolution_on_the_demo(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        demo = client.post('/api/demo/trials').json()
        first = next(item for item in demo['commitments'] if 'PO-104' in item['commitment']['description'])
        cid = first['commitment']['id']
        action_id = first['actions'][0]['id']
        skipped = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner', 'evidence_id': first['evidence'][0]['id'],
        })
        assert skipped.status_code == 409
        approved = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'APPROVED', 'actor': 'Owner', 'note': 'Hold the payment and ask for the rest.',
        })
        assert approved.status_code == 201
        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 100, 'note': 'Remaining boxes arrived', 'source': 'warehouse',
        }).json()['evidence'][-1]
        reviewed = client.post(f"/api/evidence/{evidence['id']}/reviews", json={
            'status': 'VERIFIED', 'reviewer': 'Owner', 'note': 'Counted on the dock',
        })
        assert reviewed.status_code == 201
        proposal = client.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 500, 'currency': 'USD', 'destination': 'acme-demo-wallet.near',
        })
        assert proposal.status_code == 201
        assert proposal.json()['proposal']['status'] == 'AWAITING_HUMAN_APPROVAL'
        assert proposal.json()['proposal']['transfer_executed'] is False


def anchor_tx(sender, anchor, method='record_event', receiver='thewordweaver.testnet', deposit='0', payload_hash=None):
    args = {
        'commitment_id': anchor['commitment_id'],
        'event_type': anchor['event_type'],
        'payload_hash': payload_hash or anchor['payload_hash'],
        'previous_hash': '',
    }
    return {
        'final_execution_status': 'FINAL',
        'status': {'SuccessValue': ''},
        'transaction': {
            'signer_id': sender,
            'receiver_id': receiver,
            'actions': [{
                'FunctionCall': {
                    'method_name': method,
                    'args': base64.b64encode(json.dumps(args).encode()).decode(),
                    'deposit': deposit,
                    'gas': 100000000000000,
                },
            }],
        },
    }


def test_user_signed_anchor_is_verified_and_unauthorised_anchors_are_refused(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    keys = {}
    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        login(alice, monkeypatch, 'alice.testnet', keys)
        login(bob, monkeypatch, 'bob.testnet', keys)
        alice.post('/api/workspaces')
        bob.post('/api/workspaces')
        created = alice.post('/api/commitments', json=commitment_body('Anchor me'))
        cid = created.json()['commitment']['id']
        anchor = created.json()['audit_anchor']
        assert anchor['commitment_id'].startswith('outcome-')
        assert anchor['commitment_id'] != cid
        stored = {}

        def fetch(tx_hash, sender):
            if tx_hash not in stored:
                raise anchors.AnchorRejected(422, 'The testnet transaction was not found or did not succeed.')
            return stored[tx_hash]

        monkeypatch.setattr(anchors, 'fetch_transaction', fetch)
        missing = alice.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'A' * 44})
        assert missing.status_code == 401 or missing.status_code == 422
        anonymous = TestClient(main.app)
        anonymous.post('/api/demo/trials')
        demo_id = anonymous.get('/api/commitments').json()[0]['commitment']['id']
        stored['B' * 44] = anchor_tx('bob.testnet', anchor)
        stolen = bob.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'B' * 44})
        assert stolen.status_code == 404
        stored['C' * 44] = anchor_tx('alice.testnet', anchor, method='admin_probe')
        privileged = alice.post(f'/api/commitments/{cid}/audit-receipts', json={
            'transaction_hash': 'C' * 44, 'signer_account_id': 'bob.testnet',
        })
        assert privileged.status_code == 403
        stored['D' * 44] = anchor_tx('mallory.testnet', anchor)
        other_signer = alice.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'D' * 44})
        assert other_signer.status_code == 403
        stored['E' * 44] = anchor_tx('alice.testnet', anchor, payload_hash='ab' * 32)
        mismatch = alice.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'E' * 44})
        assert mismatch.status_code == 422
        stored['F' * 44] = anchor_tx('alice.testnet', anchor)
        saved = alice.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'F' * 44})
        assert saved.status_code == 201, saved.text
        receipt = saved.json()
        assert receipt['independently_verified'] is True
        assert receipt['matches_current_record'] is True
        assert receipt['explorer_url'].endswith('F' * 44)
        assert 'Northwind' not in json.dumps(receipt)
        replay = alice.post(f'/api/commitments/{cid}/audit-receipts', json={'transaction_hash': 'F' * 44})
        assert replay.status_code == 409
        checked = alice.get(f"/api/commitments/{cid}/audit-receipts/{receipt['id']}")
        assert checked.status_code == 200
        assert checked.json()['independently_verified'] is True
        assert checked.json()['rechecked'] is True
        foreign = bob.get(f"/api/commitments/{cid}/audit-receipts/{receipt['id']}")
        assert foreign.status_code == 404
        assert anonymous.get(f'/api/commitments/{demo_id}').status_code == 200


def test_ai_verification_failure_is_not_shown_as_verified(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    monkeypatch.setattr(main, 'generate_operational_brief', lambda record: {
        'provider': 'NEAR AI Cloud',
        'model': 'Qwen/Qwen3.6-35B-A3B-FP8',
        'attestation_status': 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED',
        'brief': {'summary': 'SECRET_UNVERIFIED_BRIEF'},
        'verification': {'verified': False},
    })
    with TestClient(main.app) as client:
        cid = client.post('/api/commitments', json=commitment_body()).json()['commitment']['id']
        failed = client.post(f'/api/commitments/{cid}/ai-brief')
        assert failed.status_code == 502
        record = client.get(f'/api/commitments/{cid}').json()
        assert record['ai_brief_history'] == []
        assert 'SECRET_UNVERIFIED_BRIEF' not in json.dumps(record)
        assert 'SECRET_UNVERIFIED_BRIEF' not in failed.text


def test_write_rate_limit_and_upload_rejection(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    monkeypatch.setenv('OUTCOME_WRITE_RATE_LIMIT_PER_HOUR', '2')
    with TestClient(main.app) as client:
        assert client.post('/api/commitments', json=commitment_body('One')).status_code == 201
        assert client.post('/api/commitments', json=commitment_body('Two')).status_code == 201
        blocked = client.post('/api/commitments', json=commitment_body('Three'))
        assert blocked.status_code == 429
        upload = client.post(
            '/api/commitments',
            content=b'not-a-file',
            headers={'Content-Type': 'multipart/form-data; boundary=x'},
        )
        assert upload.status_code == 415


def test_demo_reset_cannot_delete_personal_or_demo_workspaces(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        login(client, monkeypatch, 'owner.testnet')
        assert client.post('/api/workspaces').status_code == 201
        own = client.post('/api/commitments', json=commitment_body('Personal')).json()['commitment']['id']
        assert client.post('/api/demo/trials').status_code == 201
        demo_id = client.get('/api/commitments', headers={'X-Outcome-Scope': 'demo'}).json()[0]['commitment']['id']
        assert client.post('/api/commitments', json=commitment_body('Global'), headers={'X-Outcome-Scope': 'global'}).status_code == 409
    monkeypatch.setenv('OUTCOME_ALLOW_DEMO_RESET', 'true')
    with TestClient(main.app) as client:
        assert client.post('/api/commitments', json=commitment_body('Legacy global')).status_code == 201
        reset = client.post('/api/demo/reset')
        assert reset.status_code == 200
        assert client.get('/api/commitments', headers={'X-Outcome-Scope': 'global'}).json() == []
    with main.connect() as db:
        assert db.execute('SELECT id FROM commitments WHERE id=?', (own,)).fetchone()
        assert db.execute('SELECT id FROM commitments WHERE id=?', (demo_id,)).fetchone()
        assert db.execute('SELECT id FROM workspaces WHERE kind=?', ('personal',)).fetchone()


def test_contract_interface_allows_user_anchors_and_keeps_admin_probe_private():
    source = Path('near/contract/src/lib.rs').read_text()
    record = source.split('pub fn record_event', 1)[1].split('pub fn admin_probe', 1)[0]
    assert 'Only the configured owner account may append' not in record
    assert 'get_anchor_policy' in source
    assert 'any_account' in source
    admin = source.split('pub fn admin_probe', 1)[1]
    assert 'Only the configured owner account may run admin_probe' in admin
    try:
        anchors.assess_anchor_transaction(
            anchor_tx('alice.testnet', {
                'commitment_id': 'outcome-abc',
                'event_type': 'COMMITMENT_STATE_ANCHORED',
                'payload_hash': 'ab' * 32,
            }, method='admin_probe'),
            expected_signer='alice.testnet',
            expected_receiver='thewordweaver.testnet',
            expected_payload_hash='ab' * 32,
            expected_commitment_ref='outcome-abc',
            expected_event_type='COMMITMENT_STATE_ANCHORED',
        )
    except anchors.AnchorRejected as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError('admin_probe was accepted')
