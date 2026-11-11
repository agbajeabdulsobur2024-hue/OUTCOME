"""Account-level introduction progress.

Completion lives with the signed-in account. A browser can cache a copy, but it
is not the record that decides whether the introduction opens again.
"""

from datetime import datetime, timezone

STATUSES = ('in_progress', 'completed', 'dismissed')
STEP_COUNT = 6


def anonymous() -> dict:
    return {'status': 'anonymous', 'step': 0}


def fresh() -> dict:
    return {'status': 'new', 'step': 0}


def read_progress(db, user_id: str | None) -> dict:
    if not user_id:
        return anonymous()
    row = db.execute(
        'SELECT status, step FROM account_onboarding WHERE user_id=?',
        (user_id,),
    ).fetchone()
    if not row:
        return fresh()
    status = row['status']
    try:
        step = int(row['step'])
    except (TypeError, ValueError):
        return fresh()
    if status not in STATUSES or step < 0 or step >= STEP_COUNT:
        return fresh()
    return {'status': status, 'step': step}


def write_progress(db, user_id: str, status: str, step: int) -> dict:
    if status not in STATUSES:
        raise ValueError('status')
    if not isinstance(step, int) or isinstance(step, bool) or step < 0 or step >= STEP_COUNT:
        raise ValueError('step')
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute(
        'SELECT user_id FROM account_onboarding WHERE user_id=?',
        (user_id,),
    ).fetchone()
    if existing:
        db.execute(
            'UPDATE account_onboarding SET status=?, step=?, updated_at=? WHERE user_id=?',
            (status, step, now, user_id),
        )
    else:
        db.execute(
            'INSERT INTO account_onboarding (user_id, status, step, updated_at) VALUES (?, ?, ?, ?)',
            (user_id, status, step, now),
        )
    return {'status': status, 'step': step}
