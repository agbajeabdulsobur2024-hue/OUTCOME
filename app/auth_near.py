"""Testnet wallet login. A request-body account id is never trusted on its own."""

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib import error as urlerror
from urllib import request as urlrequest

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from fastapi import HTTPException

RECIPIENT = 'outcome.app'
TAG = 2147484061
NAMED_ACCOUNT = re.compile(
    r'^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?)*$'
)
IMPLICIT_ACCOUNT = re.compile(r'^[0-9a-f]{64}$')
_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58encode(data: bytes) -> str:
    zeros = len(data) - len(data.lstrip(b'\x00'))
    number = int.from_bytes(data, 'big')
    chars = []
    while number > 0:
        number, rem = divmod(number, 58)
        chars.append(_ALPHABET[rem])
    return ('1' * zeros) + ''.join(reversed(chars))


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        index = _ALPHABET.find(char)
        if index < 0:
            raise ValueError('Invalid base58')
        number = number * 58 + index
    raw = number.to_bytes((number.bit_length() + 7) // 8, 'big') if number else b''
    pad = len(text) - len(text.lstrip('1'))
    return (b'\x00' * pad) + raw


def is_testnet_account(account_id: str) -> bool:
    if not account_id or len(account_id) > 64 or account_id.endswith('.near'):
        return False
    if IMPLICIT_ACCOUNT.fullmatch(account_id):
        return True
    return account_id.endswith('.testnet') and bool(NAMED_ACCOUNT.fullmatch(account_id))


def _borsh_string(value: str) -> bytes:
    raw = value.encode('utf-8')
    return len(raw).to_bytes(4, 'little') + raw


def nep413_digest(message: str, nonce: bytes, recipient: str) -> bytes:
    if len(nonce) != 32:
        raise ValueError('NEP-413 nonce must be 32 bytes')
    payload = TAG.to_bytes(4, 'little')
    payload += _borsh_string(message)
    payload += nonce
    payload += _borsh_string(recipient)
    payload += b'\x00'  # callbackUrl = None. The server never accepts a client callback.
    return hashlib.sha256(payload).digest()


def verify_nep413(message: str, nonce: bytes, recipient: str, public_key: str, signature: str) -> None:
    if not public_key.startswith('ed25519:'):
        raise HTTPException(401, 'The wallet signature could not be checked.')
    try:
        key_bytes = b58decode(public_key.removeprefix('ed25519:'))
        signature_bytes = base64.b64decode(signature, validate=True)
        digest = nep413_digest(message, nonce, recipient)
        VerifyKey(key_bytes).verify(digest, signature_bytes)
    except (BadSignatureError, ValueError, TypeError):
        raise HTTPException(401, 'The wallet signature could not be checked.')
    if len(key_bytes) != 32 or len(signature_bytes) != 64:
        raise HTTPException(401, 'The wallet signature could not be checked.')


def rpc_url() -> str:
    network = os.getenv('OUTCOME_NEAR_NETWORK', 'testnet').strip().lower()
    if network != 'testnet':
        raise HTTPException(403, 'OUTCOME only talks to NEAR testnet.')
    url = os.getenv('OUTCOME_NEAR_RPC_URL', 'https://rpc.testnet.near.org').strip()
    if 'mainnet' in url or 'testnet' not in url:
        raise HTTPException(403, 'OUTCOME only talks to NEAR testnet.')
    return url


def contract_id() -> str:
    value = os.getenv('OUTCOME_NEAR_CONTRACT_ID', 'thewordweaver.testnet').strip()
    if not is_testnet_account(value):
        raise HTTPException(403, 'The audit contract must be a testnet account.')
    return value


def lookup_full_access_key(account_id: str, public_key: str) -> bool:
    """Confirm the signing key is a full-access key of the claimed account."""
    body = json.dumps({
        'jsonrpc': '2.0',
        'id': 'outcome-auth',
        'method': 'query',
        'params': {
            'request_type': 'view_access_key',
            'finality': 'final',
            'account_id': account_id,
            'public_key': public_key,
        },
    }).encode('utf-8')
    req = urlrequest.Request(rpc_url(), data=body, headers={'Content-Type': 'application/json'})
    try:
        with urlrequest.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False
    result = payload.get('result') if isinstance(payload, dict) else None
    if not isinstance(result, dict) or payload.get('error'):
        return False
    return result.get('permission') == 'FullAccess'


def login_message(account_id: str, challenge_id: str) -> str:
    return (
        'Sign in to OUTCOME on NEAR testnet.\n\n'
        'This signature proves you control this testnet account. '
        'It does not approve a supplier payment or move funds.\n\n'
        f'account: {account_id}\n'
        f'challenge: {challenge_id}'
    )


def create_challenge(db, account_id: str) -> dict:
    if not is_testnet_account(account_id):
        raise HTTPException(422, 'Use a NEAR testnet account. Mainnet accounts are not accepted.')
    now = datetime.now(timezone.utc)
    challenge_id = str(uuid.uuid4())
    nonce = os.urandom(32)
    message = login_message(account_id, challenge_id)
    expires = now + timedelta(minutes=10)
    db.execute('DELETE FROM auth_challenges WHERE expires_at < ?', (now.isoformat(),))
    db.execute(
        '''INSERT INTO auth_challenges
           (id, account_id, nonce_hex, message, recipient, created_at, expires_at, used)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)''',
        (challenge_id, account_id, nonce.hex(), message, RECIPIENT, now.isoformat(), expires.isoformat()),
    )
    return {
        'challenge_id': challenge_id,
        'account_id': account_id,
        'message': message,
        'recipient': RECIPIENT,
        'nonce': base64.b64encode(nonce).decode('ascii'),
        'expires_at': expires.isoformat(),
        'network': 'testnet',
    }


def consume_challenge(db, challenge_id: str, account_id: str, public_key: str, signature: str) -> dict:
    row = db.execute('SELECT * FROM auth_challenges WHERE id=?', (challenge_id,)).fetchone()
    if not row or row['used']:
        raise HTTPException(401, 'This sign-in challenge is no longer valid.')
    if row['account_id'] != account_id:
        raise HTTPException(401, 'The signed account does not match the challenge.')
    if row['expires_at'] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(401, 'This sign-in challenge has expired.')
    try:
        nonce = bytes.fromhex(row['nonce_hex'])
    except ValueError:
        raise HTTPException(401, 'This sign-in challenge is no longer valid.')
    verify_nep413(row['message'], nonce, row['recipient'], public_key, signature)
    if not lookup_full_access_key(account_id, public_key):
        raise HTTPException(401, 'That key is not a full-access key of the testnet account.')
    db.execute('UPDATE auth_challenges SET used=1 WHERE id=?', (challenge_id,))
    user = db.execute('SELECT * FROM users WHERE account_id=?', (account_id,)).fetchone()
    if user:
        user_id = user['id']
    else:
        user_id = str(uuid.uuid4())
        db.execute(
            'INSERT INTO users (id, account_id, created_at) VALUES (?, ?, ?)',
            (user_id, account_id, datetime.now(timezone.utc).isoformat()),
        )
    return {'user_id': user_id, 'account_id': account_id}


def issue_session(db, user: dict) -> str:
    token = str(uuid.uuid4()) + str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    db.execute(
        '''INSERT INTO sessions (token_hash, user_id, account_id, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)''',
        (hashlib.sha256(token.encode('utf-8')).hexdigest(), user['user_id'], user['account_id'],
         now.isoformat(), expires.isoformat()),
    )
    return token


def read_session_token(db, token: str) -> dict | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    row = db.execute('SELECT * FROM sessions WHERE token_hash=?', (digest,)).fetchone()
    if not row or row['expires_at'] < datetime.now(timezone.utc).isoformat():
        return None
    return {'user_id': row['user_id'], 'account_id': row['account_id']}
