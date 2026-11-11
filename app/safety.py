"""Public-write limits and upload rejection. No secrets are read here."""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    if os.getenv('VERCEL') == '1':
        forwarded = request.headers.get('x-forwarded-for', '')
        if forwarded:
            return forwarded.split(',')[0].strip()[:80] or 'unknown'
    if request.client and request.client.host:
        return request.client.host[:80]
    return 'unknown'


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def buckets_for(path: str) -> list[tuple[str, int]]:
    found = [('write', _limit('OUTCOME_WRITE_RATE_LIMIT_PER_HOUR', 240))]
    if path == '/api/demo/trials' or path == '/api/demo/seed':
        found.append(('demo', _limit('OUTCOME_DEMO_RATE_LIMIT_PER_HOUR', 20)))
    if path.startswith('/api/auth/'):
        found.append(('auth', _limit('OUTCOME_AUTH_RATE_LIMIT_PER_HOUR', 30)))
    if '/audit-receipts' in path:
        found.append(('anchor', _limit('OUTCOME_ANCHOR_RATE_LIMIT_PER_HOUR', 15)))
    return found


def enforce_write_limits(db, request: Request) -> None:
    ip = client_ip(request)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=1)).isoformat()
    cleanup = (now - timedelta(days=1)).isoformat()
    db.execute('DELETE FROM request_limits WHERE created_at < ?', (cleanup,))
    for bucket, limit in buckets_for(request.url.path):
        recent = db.execute(
            'SELECT COUNT(*) FROM request_limits WHERE client_ip=? AND bucket=? AND created_at>=?',
            (ip, bucket, cutoff),
        ).fetchone()[0]
        if recent >= limit:
            raise HTTPException(429, 'Too many requests from this network. Wait a little, then try again.')
        db.execute(
            'INSERT INTO request_limits (id, client_ip, bucket, created_at) VALUES (?, ?, ?, ?)',
            (str(uuid.uuid4()), ip, bucket, now.isoformat()),
        )


def reject_upload(request: Request) -> None:
    content_type = (request.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
    if content_type.startswith('multipart/') or content_type in {'application/octet-stream', 'application/pdf', 'image/png', 'image/jpeg'}:
        raise HTTPException(415, 'File uploads are not accepted. Record evidence as text in the form.')


def blocks_shared_write(scope: dict, path: str) -> bool:
    """Hosted and signed-in callers must not mutate the legacy shared database."""
    if scope.get('workspace_id') is not None:
        return False
    domain = (
        path.startswith('/api/commitments')
        or path.startswith('/api/evidence')
        or path.startswith('/api/actions')
        or path == '/api/demo/seed'
        or path.startswith('/api/intake')
    )
    if not domain:
        return False
    if scope.get('user'):
        return True
    return os.getenv('VERCEL') == '1'
