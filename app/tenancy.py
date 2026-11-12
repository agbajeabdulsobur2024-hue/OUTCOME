"""Workspace scope. Identity comes from a verified session, never from a body field."""

import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from app.auth_near import read_session_token
from app.onboarding import read_progress

_workspace_id: ContextVar[str | None] = ContextVar('outcome_workspace_id', default=None)
_user: ContextVar[dict | None] = ContextVar('outcome_user', default=None)
_kind: ContextVar[str] = ContextVar('outcome_scope_kind', default='global')

DEMO_COOKIE = 'outcome_demo'
SESSION_COOKIE = 'outcome_session'


def activate(scope: dict):
    kind = scope.get('kind') or 'global'
    workspace_id = scope.get('workspace_id')
    # The hosted global scope is a quarantine, not a shared workspace.
    # A non-null sentinel makes existing workspace-scoped SQL return no rows
    # without changing local-development legacy behavior. Hosted writes are
    # independently blocked by safety.blocks_shared_write().
    if os.getenv('VERCEL') == '1' and kind == 'global' and workspace_id is None:
        workspace_id = '__outcome_global_scope_quarantine__'
    return (
        _workspace_id.set(workspace_id),
        _user.set(scope.get('user')),
        _kind.set(kind),
    )


def deactivate(tokens) -> None:
    _workspace_id.reset(tokens[0])
    _user.reset(tokens[1])
    _kind.reset(tokens[2])


def current_workspace_id() -> str | None:
    return _workspace_id.get()


def current_user() -> dict | None:
    return _user.get()


def use_workspace(workspace_id: str | None):
    return _workspace_id.set(workspace_id)


def reset_workspace(token) -> None:
    _workspace_id.reset(token)


def trial_ttl() -> timedelta:
    try:
        hours = int(os.getenv('OUTCOME_TRIAL_TTL_HOURS', '24'))
    except ValueError:
        hours = 24
    return timedelta(hours=min(168, max(1, hours)))


def cookie_secure(request: Request) -> bool:
    proto = (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
    return request.url.scheme == 'https' or proto == 'https'


def _demo_row(db, workspace_id: str):
    if not workspace_id:
        return None
    return db.execute(
        "SELECT * FROM workspaces WHERE id=? AND kind='demo'",
        (workspace_id,),
    ).fetchone()


def _personal_row(db, user_id: str):
    return db.execute(
        "SELECT * FROM workspaces WHERE owner_user_id=? AND kind='personal' ORDER BY created_at LIMIT 1",
        (user_id,),
    ).fetchone()


def resolve(db, request: Request) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    token = request.cookies.get(SESSION_COOKIE, '')
    user = read_session_token(db, token)
    demo_cookie = request.cookies.get(DEMO_COOKIE, '')
    demo = _demo_row(db, demo_cookie)
    demo_expired = bool(demo and demo['expires_at'] and demo['expires_at'] < now)
    demo_live = demo if demo and not demo_expired else None
    header = (request.headers.get('x-outcome-scope') or '').strip().lower()
    if header and header not in {'demo', 'personal', 'global'}:
        raise HTTPException(400, 'Unknown workspace.')
    if header == 'demo':
        if demo_expired:
            raise HTTPException(410, 'This guided demo has expired. Start a new one. Your other workspaces were not changed.')
        if not demo_live:
            raise HTTPException(401, 'Start the guided demo before opening it.')
        return {'workspace_id': demo_live['id'], 'user': user, 'kind': 'demo'}
    if header == 'personal':
        if not user:
            raise HTTPException(401, 'Connect your NEAR testnet wallet before opening a personal workspace.')
        personal = _personal_row(db, user['user_id'])
        if not personal:
            raise HTTPException(404, 'Create a personal workspace first.')
        return {'workspace_id': personal['id'], 'user': user, 'kind': 'personal'}
    if header == 'global':
        return {'workspace_id': None, 'user': user, 'kind': 'global'}
    if user:
        personal = _personal_row(db, user['user_id'])
        if personal:
            return {'workspace_id': personal['id'], 'user': user, 'kind': 'personal'}
    if demo_live:
        return {'workspace_id': demo_live['id'], 'user': user, 'kind': 'demo'}
    return {'workspace_id': None, 'user': user, 'kind': 'global'}


def session_view(db, request: Request) -> dict:
    from app.ai import is_configured
    from app.anchors import public_contract

    now = datetime.now(timezone.utc).isoformat()
    user = read_session_token(db, request.cookies.get(SESSION_COOKIE, ''))
    demo = _demo_row(db, request.cookies.get(DEMO_COOKIE, ''))
    personal = _personal_row(db, user['user_id']) if user else None
    expired = bool(demo and demo['expires_at'] and demo['expires_at'] < now)
    active = bool(demo and not expired)
    configured = is_configured()
    return {
        'authenticated': bool(user),
        'account_id': user['account_id'] if user else None,
        'network': 'testnet',
        'demo': {
            'active': active,
            'expired': expired,
            'expires_at': demo['expires_at'] if demo else None,
            'synthetic': True,
        },
        'personal': {'active': bool(personal)},
        'ai': {
            'configured': configured,
            'availability': 'configured' if configured else 'unavailable',
        },
        'near': public_contract(),
        'onboarding': read_progress(db, user['user_id'] if user else None),
    }


def create_demo_workspace(db) -> dict:
    purge_expired_demos(db)
    now = datetime.now(timezone.utc)
    expires = now + trial_ttl()
    workspace_id = str(uuid.uuid4())
    db.execute(
        '''INSERT INTO workspaces (id, kind, owner_user_id, created_at, expires_at, synthetic)
           VALUES (?, 'demo', NULL, ?, ?, 1)''',
        (workspace_id, now.isoformat(), expires.isoformat()),
    )
    return {'id': workspace_id, 'expires_at': expires.isoformat()}


def create_personal_workspace(db, user: dict) -> dict:
    existing = _personal_row(db, user['user_id'])
    if existing:
        return {'id': existing['id'], 'created': False}
    workspace_id = str(uuid.uuid4())
    db.execute(
        '''INSERT INTO workspaces (id, kind, owner_user_id, created_at, expires_at, synthetic)
           VALUES (?, 'personal', ?, ?, NULL, 0)''',
        (workspace_id, user['user_id'], datetime.now(timezone.utc).isoformat()),
    )
    return {'id': workspace_id, 'created': True}


def purge_expired_demos(db) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT id FROM workspaces WHERE kind='demo' AND expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    ).fetchall()
    for row in rows:
        ids = [item['id'] for item in db.execute(
            'SELECT id FROM commitments WHERE workspace_id=?', (row['id'],)
        ).fetchall()]
        delete_commitment_rows(db, ids)
        db.execute('DELETE FROM workspaces WHERE id=? AND kind=?', (row['id'], 'demo'))


def global_commitment_ids(db) -> list[str]:
    return [row['id'] for row in db.execute(
        'SELECT id FROM commitments WHERE workspace_id IS NULL'
    ).fetchall()]


def delete_commitment_rows(db, ids: list[str]) -> None:
    if not ids:
        return
    marks = ','.join('?' for _ in ids)
    db.execute(f'DELETE FROM audit_receipts WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM ai_briefs WHERE commitment_id IN ({marks})', ids)
    db.execute(
        f'''DELETE FROM action_events WHERE action_id IN (
            SELECT id FROM commitment_actions WHERE commitment_id IN ({marks}))''',
        ids,
    )
    db.execute(f'DELETE FROM commitment_actions WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM payment_decisions WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM payment_proposals WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM payment_policy_history WHERE commitment_id IN ({marks})', ids)
    db.execute(
        f'''DELETE FROM evidence_reviews WHERE evidence_id IN (
            SELECT id FROM evidence WHERE commitment_id IN ({marks}))''',
        ids,
    )
    db.execute(f'DELETE FROM payment_policies WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM assessment_history WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM commitment_versions WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM evidence WHERE commitment_id IN ({marks})', ids)
    db.execute(f'DELETE FROM commitments WHERE id IN ({marks})', ids)


def same_workspace(stored) -> bool:
    current = current_workspace_id()
    actual = stored if stored else None
    return actual == current
