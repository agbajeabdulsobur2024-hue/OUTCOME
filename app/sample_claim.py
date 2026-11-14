"""Idempotent sample-session creation for simultaneous first visits.

Two tabs, a double tap, or a client retry can each reach POST /api/demo/trials
before either has received the session cookie. With nothing to tell them apart,
each created its own sample workspace.

The browser therefore sends a random *sample claim* (header X-Outcome-Sample-Claim),
generated once and kept in its own storage. The server derives the sample workspace
id from the claim, so every simultaneous request from that browser addresses the same
row and the same fixed record ids. Creation uses INSERT OR IGNORE, which both SQLite
and Turso/libSQL support, so the guarantee does not depend on a single process.

The claim only de-duplicates creation. It is not an identity: the session cookie
remains the authority, and the claim grants nothing beyond the synthetic sample.
This module imports no web framework so its behaviour can be tested directly.
"""

import hashlib
import re
import threading
import uuid

CLAIM_HEADER = 'X-Outcome-Sample-Claim'
_CLAIM_RE = re.compile(r'^[A-Za-z0-9_-]{22,64}$')
_NAMESPACE = uuid.UUID('6f1c1f0e-5a43-4d0a-9d0e-0c5a1d7b9e11')
_STRIPES = tuple(threading.Lock() for _ in range(32))


def valid_claim(value) -> str:
    """Return the claim if it is well formed, otherwise an empty string."""
    claim = (value or '').strip() if isinstance(value, str) else ''
    return claim if _CLAIM_RE.match(claim) else ''


def workspace_id_for(claim: str) -> str:
    digest = hashlib.sha256(('outcome-sample-claim:' + claim).encode('utf-8')).hexdigest()
    return 'demo-' + digest[:32]


def lock_for(workspace_id: str) -> threading.Lock:
    """A per-workspace lock inside one process (striped, so none are retained)."""
    return _STRIPES[int(hashlib.sha256(workspace_id.encode('utf-8')).hexdigest(), 16) % len(_STRIPES)]


def is_claimed(workspace_id) -> bool:
    """True for a sample workspace whose id came from a claim (ordinary ones are UUIDs)."""
    return isinstance(workspace_id, str) and workspace_id.startswith('demo-')


def record_id(workspace_id: str, label: str) -> str:
    """A stable row id, so populating the same sample twice writes the same rows."""
    return str(uuid.uuid5(_NAMESPACE, workspace_id + ':' + label))


def ensure_workspace(db, workspace_id: str, created_at: str, expires_at: str):
    """Create the sample workspace row if absent. Safe to call concurrently.

    Returns (row, existed_before). The row is read back, so a caller that lost
    the race receives the winner's row and expiry.
    """
    before = db.execute(
        "SELECT id, expires_at FROM workspaces WHERE id=? AND kind='demo'", (workspace_id,),
    ).fetchone()
    if not before:
        db.execute(
            '''INSERT OR IGNORE INTO workspaces (id, kind, owner_user_id, created_at, expires_at, synthetic)
               VALUES (?, 'demo', NULL, ?, ?, 1)''',
            (workspace_id, created_at, expires_at),
        )
    row = db.execute(
        "SELECT id, expires_at FROM workspaces WHERE id=? AND kind='demo'", (workspace_id,),
    ).fetchone()
    return row, bool(before)
