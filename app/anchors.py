"""Check a user-signed testnet record_event before OUTCOME stores it.

The server never signs or broadcasts. A body field naming a wallet is not identity.
"""

import base64
import json
from urllib import error as urlerror
from urllib import request as urlrequest

from app.auth_near import contract_id, rpc_url

ALLOWED_METHOD = 'record_event'
EVENT_TYPE = 'COMMITMENT_STATE_ANCHORED'
OWNER_ACCOUNT_ID = 'thewordweaver.testnet'
STORAGE_BYTE_COST_YOCTO = 10 ** 19
EVENT_HASH_BYTES = 64


class AnchorRejected(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def explorer_url(tx_hash: str) -> str:
    return 'https://testnet.nearblocks.io/txns/' + tx_hash


def public_contract() -> dict:
    return {
        'network': 'testnet',
        'contract_id': contract_id(),
        'method': ALLOWED_METHOD,
        'event_type': EVENT_TYPE,
        'rpc_url': 'https://rpc.testnet.near.org',
        'wallet': 'HOT Connect',
        'explorer_tx': 'https://testnet.nearblocks.io/txns/',
        'anchor_policy_method': 'get_anchor_policy',
        'policy_after_upgrade': 'any_account',
        'code_hash': '5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr',
        'owner_account_id': OWNER_ACCOUNT_ID,
        'owner_event_id': 'OUTCOME-DEMO-PO-104',
        'upgrade_transaction': 'FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc',
        'tester_signature_recorded': False,
        'known_transactions': [
            {
                'label': 'Deploy and init',
                'hash': '8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy',
                'url': 'https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy',
                'status': 'FINAL',
            },
            {
                'label': 'record_event',
                'hash': '3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj',
                'url': 'https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj',
                'status': 'FINAL',
                'stored_id': 'OUTCOME-DEMO-PO-104',
                'note': 'Owner event. It survived the upgrade and is not a tester signature.',
            },
            {
                'label': 'Upgrade',
                'hash': 'FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc',
                'url': 'https://testnet.nearblocks.io/txns/FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc',
                'status': 'FINAL',
                'code_hash': '5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr',
            },
        ],
    }


def fetch_transaction(tx_hash: str, sender_account_id: str) -> dict:
    body = json.dumps({
        'jsonrpc': '2.0',
        'id': 'outcome-anchor',
        'method': 'tx',
        'params': {
            'tx_hash': tx_hash,
            'sender_account_id': sender_account_id,
            'wait_until': 'FINAL',
        },
    }).encode('utf-8')
    req = urlrequest.Request(rpc_url(), data=body, headers={'Content-Type': 'application/json'})
    try:
        with urlrequest.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise AnchorRejected(502, 'The testnet transaction could not be read.') from exc
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('result'), dict):
        raise AnchorRejected(422, 'The testnet transaction was not found or did not succeed.')
    return payload['result']


def _function_call(transaction: dict) -> dict:
    actions = transaction.get('actions') or []
    if len(actions) != 1 or not isinstance(actions[0], dict):
        raise AnchorRejected(422, 'The transaction is not a single audit record.')
    call = actions[0].get('FunctionCall')
    if not isinstance(call, dict):
        raise AnchorRejected(422, 'The transaction is not a single audit record.')
    return call


def _decode_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        raise AnchorRejected(422, 'The audit arguments could not be read.')
    try:
        decoded = base64.b64decode(raw, validate=True)
        parsed = json.loads(decoded.decode('utf-8'))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise AnchorRejected(422, 'The audit arguments could not be read.')
    if not isinstance(parsed, dict):
        raise AnchorRejected(422, 'The audit arguments could not be read.')
    return parsed


def storage_bytes_for_event(
    *,
    commitment_id: str,
    event_type: str,
    payload_hash: str,
    previous_hash: str,
    actor_account_id: str,
    last_hash_len: int = EVENT_HASH_BYTES,
) -> int:
    """Bytes added for one new audit event.

    LookupMap key is a 1-byte storage prefix plus an 8-byte sequence.
    The value is the Borsh encoding of the event. Replacing last_hash grows
    the contract state only when it is shorter than the 64-character event hash.
    """
    value = (
        8
        + 4 + len(commitment_id)
        + 4 + len(event_type)
        + 4 + len(payload_hash)
        + 4 + len(previous_hash)
        + 4 + EVENT_HASH_BYTES
        + 4 + len(actor_account_id)
        + 8
        + 8
    )
    total = 1 + 8 + value
    if last_hash_len < EVENT_HASH_BYTES:
        total += EVENT_HASH_BYTES - last_hash_len
    return total


def required_storage_deposit_yocto(**kwargs) -> int:
    return storage_bytes_for_event(**kwargs) * STORAGE_BYTE_COST_YOCTO


def assess_anchor_transaction(
    tx_result: dict,
    *,
    expected_signer: str,
    expected_receiver: str,
    expected_payload_hash: str,
    expected_commitment_ref: str,
    expected_event_type: str,
) -> dict:
    status = tx_result.get('status') if isinstance(tx_result.get('status'), dict) else {}
    if 'SuccessValue' not in status or 'Failure' in status:
        raise AnchorRejected(422, 'The testnet transaction did not succeed.')
    final_status = tx_result.get('final_execution_status')
    if final_status not in {'FINAL', 'FINAL_SUCCESS'}:
        raise AnchorRejected(422, 'The testnet transaction is not final yet.')
    transaction = tx_result.get('transaction') if isinstance(tx_result.get('transaction'), dict) else {}
    if transaction.get('signer_id') != expected_signer:
        raise AnchorRejected(403, 'The transaction was signed by a different account.')
    if transaction.get('receiver_id') != expected_receiver:
        raise AnchorRejected(422, 'The transaction was not sent to the OUTCOME testnet contract.')
    call = _function_call(transaction)
    if call.get('method_name') != ALLOWED_METHOD:
        raise AnchorRejected(403, 'Only a hash-only record_event can be stored. Other contract calls are refused.')
    args = _decode_args(call.get('args'))
    payload_hash = str(args.get('payload_hash', '')).lower()
    deposit_text = str(call.get('deposit', '0')).split('.', 1)[0] or '0'
    try:
        attached = int(deposit_text)
    except ValueError:
        raise AnchorRejected(422, 'The attached deposit could not be read.')
    if attached == 0:
        if expected_signer != OWNER_ACCOUNT_ID:
            raise AnchorRejected(422, 'A non-owner audit record must attach the exact storage deposit.')
    else:
        required = required_storage_deposit_yocto(
            commitment_id=str(args.get('commitment_id') or ''),
            event_type=str(args.get('event_type') or ''),
            payload_hash=payload_hash,
            previous_hash=str(args.get('previous_hash') or ''),
            actor_account_id=expected_signer,
        )
        if attached != required:
            raise AnchorRejected(422, 'The attached deposit is not the storage amount required for this audit record.')
    if payload_hash != expected_payload_hash:
        raise AnchorRejected(422, 'The anchored digest does not match this commitment.')
    if args.get('commitment_id') != expected_commitment_ref:
        raise AnchorRejected(422, 'The anchored reference does not match this commitment.')
    if args.get('event_type') != expected_event_type:
        raise AnchorRejected(422, 'The anchored event type does not match this commitment.')
    tx_hash = ''
    outcome = tx_result.get('transaction_outcome')
    if isinstance(outcome, dict):
        inner = outcome.get('id') or ''
        tx_hash = inner if isinstance(inner, str) else ''
    return {
        'signer_account_id': expected_signer,
        'payload_hash': payload_hash,
        'execution_status': final_status if final_status != 'FINAL_SUCCESS' else 'FINAL',
        'independently_verified': True,
        'method': ALLOWED_METHOD,
        'transaction_hash_hint': tx_hash,
    }
