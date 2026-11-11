import json
import hashlib
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.engine import assess_commitment
from app.payments import evaluate_payment_proposal
from app.patterns import detect_supplier_patterns
from app.ai import (
    AIConfigurationError, AIProviderError, brief_is_displayable,
    generate_operational_brief, is_configured, public_ai_brief_entry,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('OUTCOME_DATA_DIR', ROOT / 'data'))
DB_PATH = DATA_DIR / 'outcome.db'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    """Open one database connection and always commit/rollback and close it.

    Local development uses SQLite. Vercel uses Turso's HTTP serverless driver,
    so no writable local database file or long-lived TCP connection is needed.
    """
    default_backend = 'turso' if os.getenv('VERCEL') == '1' else 'sqlite'
    backend = os.getenv('OUTCOME_DB_BACKEND', default_backend).strip().lower()
    if backend == 'sqlite':
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    elif backend == 'turso':
        database_url = os.getenv('TURSO_DATABASE_URL', '').strip()
        auth_token = os.getenv('TURSO_AUTH_TOKEN', '').strip()
        if not database_url or not auth_token:
            raise RuntimeError(
                'OUTCOME_DB_BACKEND=turso requires TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.'
            )
        try:
            import turso_serverless
        except ImportError as exc:
            raise RuntimeError(
                'The Turso backend requires the turso-serverless package.'
            ) from exc
        db = turso_serverless.connect(database_url, auth_token=auth_token)
        db.row_factory = turso_serverless.Row
    else:
        raise RuntimeError(
            f"Unsupported OUTCOME_DB_BACKEND '{backend}'. Use 'sqlite' or 'turso'."
        )

    try:
        # Turso defaults foreign-key enforcement to OFF. Set it on every
        # connection before any transaction starts; SQLite does the same.
        db.execute('PRAGMA foreign_keys = ON')
        yield db
    except BaseException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    else:
        db.commit()
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS commitments (
            id TEXT PRIMARY KEY, supplier TEXT NOT NULL, description TEXT NOT NULL,
            quantity REAL NOT NULL, unit TEXT NOT NULL, amount REAL NOT NULL,
            currency TEXT NOT NULL, due_date TEXT NOT NULL, payment_condition TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            kind TEXT NOT NULL, value REAL, note TEXT NOT NULL, source TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS commitment_versions (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            version INTEGER NOT NULL, terms_json TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(commitment_id, version))''')
        db.execute('''CREATE TABLE IF NOT EXISTS assessment_history (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            status TEXT NOT NULL, assessment_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_policies (
            commitment_id TEXT PRIMARY KEY REFERENCES commitments(id),
            enabled INTEGER NOT NULL DEFAULT 0, max_amount REAL NOT NULL,
            allowed_destination TEXT NOT NULL DEFAULT '',
            require_full_delivery INTEGER NOT NULL DEFAULT 1,
            require_invoice_match INTEGER NOT NULL DEFAULT 1,
            require_verified_evidence INTEGER NOT NULL DEFAULT 1,
            allow_partial_payments INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS evidence_reviews (
            id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id),
            status TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_proposals (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            status TEXT NOT NULL, proposal_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_policy_history (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            policy_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS commitment_actions (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            action_type TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS action_events (
            id TEXT PRIMARY KEY, action_id TEXT NOT NULL REFERENCES commitment_actions(id),
            event_type TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL,
            evidence_id TEXT REFERENCES evidence(id), created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS ai_briefs (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            provider TEXT NOT NULL, model TEXT NOT NULL, brief_json TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS ai_request_limits (
            id TEXT PRIMARY KEY, client_ip TEXT NOT NULL, created_at TEXT NOT NULL)''')
        policy_columns = {row['name'] for row in db.execute('PRAGMA table_info(payment_policies)').fetchall()}
        if 'require_verified_evidence' not in policy_columns:
            db.execute('ALTER TABLE payment_policies ADD COLUMN require_verified_evidence INTEGER NOT NULL DEFAULT 1')
        if 'allow_partial_payments' not in policy_columns:
            db.execute('ALTER TABLE payment_policies ADD COLUMN allow_partial_payments INTEGER NOT NULL DEFAULT 0')


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='OUTCOME', description='Supplier commitment intelligence: compare what was agreed with what the evidence shows.', version='0.3.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


class CommitmentCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    unit: str = Field(default='units', min_length=1, max_length=40)
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(default='USD', min_length=3, max_length=8)
    due_date: str
    payment_condition: str = Field(default='Pay after verified delivery', max_length=300)


class EvidenceCreate(BaseModel):
    kind: Literal['quantity_received', 'invoice_amount', 'note']
    value: float | None = Field(default=None, allow_inf_nan=False)
    note: str = Field(default='', max_length=1000)
    source: str = Field(default='manual', max_length=100)


class PaymentPolicyUpdate(BaseModel):
    enabled: bool = False
    max_amount: float = Field(gt=0, allow_inf_nan=False)
    allowed_destination: str = Field(min_length=1, max_length=200)
    require_full_delivery: bool = True
    require_invoice_match: bool = True
    require_verified_evidence: bool = True
    allow_partial_payments: bool = False


class PaymentProposalCreate(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=8)
    destination: str = Field(min_length=1, max_length=200)



class EvidenceReviewCreate(BaseModel):
    status: Literal['VERIFIED', 'REJECTED']
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=1000)


class CommitmentActionCreate(BaseModel):
    action_type: Literal['CONTACT_SUPPLIER', 'REQUEST_EVIDENCE', 'HOLD_PAYMENT', 'RECONCILE_DELIVERY', 'OTHER']
    description: str = Field(min_length=3, max_length=1000)


class ActionEventCreate(BaseModel):
    event_type: Literal['APPROVED', 'REJECTED', 'EXECUTION_REPORTED', 'OUTCOME_VERIFIED', 'OUTCOME_FAILED']
    actor: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=1000)
    evidence_id: str | None = None


class CommitmentAmendment(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    due_date: str | None = None
    payment_condition: str | None = Field(default=None, max_length=300)


def get_record(cid: str):
    with connect() as db:
        c = db.execute('SELECT * FROM commitments WHERE id=?', (cid,)).fetchone()
        if not c:
            raise HTTPException(404, 'Commitment not found')
        commitment = dict(c)
        evidence = [dict(row) for row in db.execute(
            '''SELECT e.*,
                COALESCE((SELECT r.status FROM evidence_reviews r WHERE r.evidence_id=e.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1), 'UNREVIEWED') AS review_status,
                (SELECT r.reviewer FROM evidence_reviews r WHERE r.evidence_id=e.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1) AS reviewer,
                (SELECT r.note FROM evidence_reviews r WHERE r.evidence_id=e.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1) AS review_note,
                (SELECT r.created_at FROM evidence_reviews r WHERE r.evidence_id=e.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1) AS reviewed_at
                FROM evidence e WHERE e.commitment_id=? ORDER BY e.created_at, e.rowid''', (cid,)
        ).fetchall()]
        for evidence_record in evidence:
            evidence_record['review_history'] = [dict(row) for row in db.execute(
                'SELECT status, reviewer, note, created_at FROM evidence_reviews WHERE evidence_id=? ORDER BY created_at, rowid',
                (evidence_record['id'],)
            ).fetchall()]
        versions = [dict(row) for row in db.execute(
            'SELECT version, terms_json, reason, created_at FROM commitment_versions WHERE commitment_id=? ORDER BY version', (cid,)
        ).fetchall()]
        history = [dict(row) for row in db.execute(
            'SELECT id, status, assessment_json, created_at FROM assessment_history WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
        ).fetchall()]
        policy_row = db.execute('SELECT * FROM payment_policies WHERE commitment_id=?', (cid,)).fetchone()
        policy_history = [dict(row) for row in db.execute(
            'SELECT id, policy_json, created_at FROM payment_policy_history WHERE commitment_id=? ORDER BY created_at, rowid',
            (cid,)
        ).fetchall()]
        proposals = [dict(row) for row in db.execute(
            'SELECT id, status, proposal_json, created_at FROM payment_proposals WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
        ).fetchall()]
        action_rows = [dict(row) for row in db.execute(
            'SELECT id, action_type, description, created_at FROM commitment_actions WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
        ).fetchall()]
        ai_briefs = [dict(row) for row in db.execute(
            'SELECT id, provider, model, brief_json, created_at FROM ai_briefs WHERE commitment_id=? ORDER BY created_at, rowid',
            (cid,)
        ).fetchall()]
        actions = []
        for action in action_rows:
            events = [dict(row) for row in db.execute(
                'SELECT id, event_type, actor, note, evidence_id, created_at FROM action_events WHERE action_id=? ORDER BY created_at, rowid',
                (action['id'],)
            ).fetchall()]
            action['events'] = events
            action['status'] = events[-1]['event_type'] if events else 'PROPOSED'
            actions.append(action)
    timeline = [{
        'event_type': 'COMMITMENT_CREATED',
        'title': 'Original commitment recorded',
        'created_at': commitment['created_at'],
        'details': 'Version 1 · ' + commitment['supplier'],
    }]
    for version in versions:
        if int(version['version']) > 1:
            terms = json.loads(version['terms_json'])
            timeline.append({
                'event_type': 'COMMITMENT_AMENDED',
                'title': 'Commitment amended to version ' + str(version['version']),
                'created_at': version['created_at'],
                'details': version['reason'] + ' · ' + str(terms['quantity']) + ' ' + terms['unit'] + ' · ' + str(terms['amount']) + ' ' + terms['currency'],
            })
    for item in evidence:
        timeline.append({
            'event_type': 'EVIDENCE_RECORDED',
            'title': 'Evidence recorded: ' + item['kind'].replace('_', ' '),
            'created_at': item['created_at'],
            'details': str(item.get('value') if item.get('value') is not None else '') + ' · source ' + item['source'],
        })
        for review in item['review_history']:
            timeline.append({
                'event_type': 'EVIDENCE_REVIEWED',
                'title': 'Evidence ' + review['status'].lower(),
                'created_at': review['created_at'],
                'details': 'Reviewer: ' + review['reviewer'] + ((' · ' + review['note']) if review['note'] else ''),
            })
    for row in history:
        saved = json.loads(row['assessment_json'])
        timeline.append({
            'event_type': 'ASSESSMENT_SAVED',
            'title': 'Assessment: ' + row['status'].replace('_', ' '),
            'created_at': row['created_at'],
            'details': saved.get('summary', ''),
        })
    for row in policy_history:
        timeline.append({
            'event_type': 'PAYMENT_POLICY_UPDATED',
            'title': 'Owner payment policy updated',
            'created_at': row['created_at'],
            'details': 'Policy snapshot recorded for this commitment.',
        })
    for row in proposals:
        proposal = json.loads(row['proposal_json'])
        timeline.append({
            'event_type': 'PAYMENT_PROPOSAL_EVALUATED',
            'title': 'Payment proposal: ' + row['status'].replace('_', ' '),
            'created_at': row['created_at'],
            'details': proposal.get('message', '') + ' · ' + ', '.join(reason['code'] for reason in proposal.get('reasons', [])),
        })
    for action in actions:
        timeline.append({
            'event_type': 'ACTION_PROPOSED',
            'title': 'Action proposed: ' + action['action_type'].replace('_', ' '),
            'created_at': action['created_at'],
            'details': action['description'],
        })
        for event in action['events']:
            timeline.append({
                'event_type': 'ACTION_EVENT',
                'title': 'Action ' + event['event_type'].replace('_', ' ').lower(),
                'created_at': event['created_at'],
                'details': event['actor'] + ((' · ' + event['note']) if event['note'] else ''),
            })
    published_briefs = []
    for row in ai_briefs:
        try:
            stored = json.loads(row['brief_json'])
        except (TypeError, ValueError):
            stored = None
        shown = public_ai_brief_entry(
            row['id'], row['provider'], row['model'], row['created_at'], stored,
        )
        published_briefs.append(shown)
        timeline.append({
            'event_type': 'AI_BRIEF_GENERATED',
            'title': 'AI operational brief generated' if shown['displayable'] else 'AI operational brief withheld',
            'created_at': row['created_at'],
            'details': (
                shown['brief'].get('brief', {}).get('summary', '')
                if shown['displayable'] else
                'Brief withheld because cryptographic verification is incomplete.'
            ),
        })
    timeline.sort(key=lambda item: item['created_at'])
    return {
        'commitment': commitment,
        'evidence': evidence,
        'assessment': assess_commitment(commitment, evidence),
        'commitment_versions': versions,
        'assessment_history': [
            {'id': row['id'], 'status': row['status'], 'assessment': json.loads(row['assessment_json']), 'created_at': row['created_at']}
            for row in history
        ],
        'payment_policy': ({
            'enabled': bool(policy_row['enabled']),
            'max_amount': policy_row['max_amount'],
            'allowed_destination': policy_row['allowed_destination'],
            'require_full_delivery': bool(policy_row['require_full_delivery']),
            'require_invoice_match': bool(policy_row['require_invoice_match']),
            'require_verified_evidence': bool(policy_row['require_verified_evidence']),
            'allow_partial_payments': bool(policy_row['allow_partial_payments']),
            'updated_at': policy_row['updated_at'],
            'configured': True,
        } if policy_row else {
            'enabled': False, 'max_amount': commitment['amount'],
            'allowed_destination': '', 'require_full_delivery': True,
            'require_invoice_match': True, 'require_verified_evidence': True,
            'allow_partial_payments': False, 'configured': False,
        }),
        'payment_policy_history': [
            {'id': row['id'], 'policy': json.loads(row['policy_json']), 'created_at': row['created_at']}
            for row in policy_history
        ],
        'payment_proposals': [
            {'id': row['id'], 'status': row['status'], 'proposal': json.loads(row['proposal_json']), 'created_at': row['created_at']}
            for row in proposals
        ],
        'actions': actions,
        'ai_brief_history': published_briefs,
        'timeline': timeline,
    }


def save_assessment_snapshot(cid: str):
    record = get_record(cid)
    assessment = record['assessment']
    with connect() as db:
        db.execute(
            'INSERT INTO assessment_history (id, commitment_id, status, assessment_json, created_at) VALUES (?, ?, ?, ?, ?)',
            (str(uuid.uuid4()), cid, assessment['status'], json.dumps(assessment, sort_keys=True), utc_now())
        )


def save_initial_version(cid: str, terms: dict):
    with connect() as db:
        db.execute(
            'INSERT INTO commitment_versions (id, commitment_id, version, terms_json, reason, created_at) VALUES (?, ?, 1, ?, ?, ?)',
            (str(uuid.uuid4()), cid, json.dumps(terms, sort_keys=True), 'Original agreement', utc_now())
        )


@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    default_backend = 'turso' if os.getenv('VERCEL') == '1' else 'sqlite'
    backend = os.getenv('OUTCOME_DB_BACKEND', default_backend).strip().lower()
    with connect() as db:
        db.execute('SELECT 1').fetchone()
    return {
        'status': 'ok',
        'service': 'outcome',
        'version': app.version,
        'database_backend': backend,
    }


@app.get('/api/commitments')
def list_commitments():
    with connect() as db:
        ids = [row['id'] for row in db.execute('SELECT id FROM commitments ORDER BY created_at DESC').fetchall()]
    return [get_record(cid) for cid in ids]


@app.post('/api/commitments', status_code=201)
def create_commitment(payload: CommitmentCreate):
    try:
        parsed_due = datetime.fromisoformat(payload.due_date)
    except ValueError:
        raise HTTPException(422, 'due_date must be an ISO date, e.g. 2026-11-14')
    cid = str(uuid.uuid4())
    now = utc_now()
    terms = payload.model_dump()
    terms['currency'] = payload.currency.upper()
    terms['due_date'] = parsed_due.date().isoformat()
    with connect() as db:
        db.execute(
            'INSERT INTO commitments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (cid, payload.supplier.strip(), payload.description.strip(), payload.quantity, payload.unit,
             payload.amount, terms['currency'], terms['due_date'], payload.payment_condition, now)
        )
    save_initial_version(cid, terms)
    save_assessment_snapshot(cid)
    return get_record(cid)


@app.get('/api/commitments/{cid}')
def read_commitment(cid: str):
    return get_record(cid)


@app.get('/api/commitments/{cid}/assessment')
def read_assessment(cid: str):
    return get_record(cid)['assessment']


@app.post('/api/commitments/{cid}/evidence', status_code=201)
def add_evidence(cid: str, payload: EvidenceCreate):
    get_record(cid)
    if payload.kind in ('quantity_received', 'invoice_amount') and payload.value is None:
        raise HTTPException(422, 'A numeric value is required for this evidence type')
    if payload.kind == 'note' and payload.value is not None:
        raise HTTPException(422, 'Note evidence must not include a numeric value')
    if payload.value is not None and payload.value < 0:
        raise HTTPException(422, 'Evidence value cannot be negative')
    with connect() as db:
        db.execute(
            'INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)',
            (str(uuid.uuid4()), cid, payload.kind, payload.value, payload.note.strip(), payload.source.strip() or 'manual', utc_now())
        )
    save_assessment_snapshot(cid)
    return get_record(cid)






@app.post('/api/evidence/{evidence_id}/reviews', status_code=201)
def review_evidence(evidence_id: str, payload: EvidenceReviewCreate):
    with connect() as db:
        exists = db.execute('SELECT id FROM evidence WHERE id=?', (evidence_id,)).fetchone()
        if not exists:
            raise HTTPException(404, 'Evidence record not found')
        review_id = str(uuid.uuid4())
        created_at = utc_now()
        db.execute('''INSERT INTO evidence_reviews
            (id, evidence_id, status, reviewer, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (review_id, evidence_id, payload.status, payload.reviewer.strip(),
             payload.note.strip(), created_at))
        if not payload.reviewer.strip():
            raise HTTPException(422, 'Reviewer identity is required')
    return {'id': review_id, 'evidence_id': evidence_id, 'status': payload.status,
            'reviewer': payload.reviewer.strip(), 'note': payload.note.strip(),
            'created_at': created_at}


@app.post('/api/commitments/{cid}/amendments', status_code=201)
def amend_commitment(cid: str, payload: CommitmentAmendment):
    current = get_record(cid)
    changes = payload.model_dump(exclude_unset=True)
    reason = changes.pop('reason').strip()
    if not reason:
        raise HTTPException(422, 'An amendment reason is required')
    if not changes:
        raise HTTPException(422, 'Provide at least one term to amend')
    if any(value is None for value in changes.values()):
        raise HTTPException(422, 'Amended terms cannot be null')
    if 'due_date' in changes:
        try:
            changes['due_date'] = datetime.fromisoformat(changes['due_date']).date().isoformat()
        except ValueError:
            raise HTTPException(422, 'due_date must be an ISO date, e.g. 2026-11-14')
    if 'currency' in changes:
        changes['currency'] = changes['currency'].strip().upper()
        if not changes['currency']:
            raise HTTPException(422, 'currency cannot be blank')
    for field in ('description', 'unit', 'payment_condition'):
        if field in changes:
            changes[field] = changes[field].strip()
            if not changes[field]:
                raise HTTPException(422, f'{field} cannot be blank')
    old_terms = current['commitment']
    new_terms = {key: old_terms[key] for key in (
        'supplier', 'description', 'quantity', 'unit', 'amount',
        'currency', 'due_date', 'payment_condition'
    )}
    new_terms.update(changes)
    policy = current['payment_policy']
    if policy.get('configured') and float(policy['max_amount']) > float(new_terms['amount']):
        raise HTTPException(409, 'Existing payment-policy cap exceeds the amended commitment value. Lower the policy cap before amending the amount.')
    version = len(current['commitment_versions']) + 1
    now = utc_now()
    with connect() as db:
        db.execute('''UPDATE commitments SET description=?, quantity=?, unit=?,
            amount=?, currency=?, due_date=?, payment_condition=? WHERE id=?''',
            (new_terms['description'], new_terms['quantity'], new_terms['unit'],
             new_terms['amount'], new_terms['currency'], new_terms['due_date'],
             new_terms['payment_condition'], cid))
        db.execute('''INSERT INTO commitment_versions
            (id, commitment_id, version, terms_json, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (str(uuid.uuid4()), cid, version, json.dumps(new_terms, sort_keys=True), reason, now))
    save_assessment_snapshot(cid)
    return get_record(cid)


@app.get('/api/commitments/{cid}/payment-policy')
def read_payment_policy(cid: str):
    return get_record(cid)['payment_policy']


@app.put('/api/commitments/{cid}/payment-policy')
def update_payment_policy(cid: str, payload: PaymentPolicyUpdate):
    record = get_record(cid)
    if not payload.allowed_destination.strip():
        raise HTTPException(422, 'An exact approved destination is required')
    if payload.max_amount > float(record['commitment']['amount']):
        raise HTTPException(422, 'Policy spending limit cannot exceed the recorded commitment amount')
    now = utc_now()
    policy_snapshot = {
        'enabled': payload.enabled,
        'max_amount': payload.max_amount,
        'allowed_destination': payload.allowed_destination.strip(),
        'require_full_delivery': payload.require_full_delivery,
        'require_invoice_match': payload.require_invoice_match,
        'require_verified_evidence': payload.require_verified_evidence,
        'allow_partial_payments': payload.allow_partial_payments,
        'updated_at': now,
        'configured': True,
    }
    with connect() as db:
        db.execute('''INSERT INTO payment_policies
            (commitment_id, enabled, max_amount, allowed_destination,
             require_full_delivery, require_invoice_match, require_verified_evidence, allow_partial_payments, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(commitment_id) DO UPDATE SET
            enabled=excluded.enabled, max_amount=excluded.max_amount,
            allowed_destination=excluded.allowed_destination,
            require_full_delivery=excluded.require_full_delivery,
            require_invoice_match=excluded.require_invoice_match,
            require_verified_evidence=excluded.require_verified_evidence,
            allow_partial_payments=excluded.allow_partial_payments,
            updated_at=excluded.updated_at''',
            (cid, int(payload.enabled), payload.max_amount, payload.allowed_destination.strip(),
             int(payload.require_full_delivery), int(payload.require_invoice_match),
             int(payload.require_verified_evidence), int(payload.allow_partial_payments), now))
        db.execute(
            'INSERT INTO payment_policy_history (id, commitment_id, policy_json, created_at) VALUES (?, ?, ?, ?)',
            (str(uuid.uuid4()), cid, json.dumps(policy_snapshot, sort_keys=True), now)
        )
    return get_record(cid)['payment_policy']


@app.post('/api/commitments/{cid}/payment-proposals', status_code=201)
def create_payment_proposal(cid: str, payload: PaymentProposalCreate):
    record = get_record(cid)
    assessment = evaluate_payment_proposal(
        record['commitment'], record['evidence'], record['payment_policy'],
        payload.amount, payload.currency, payload.destination,
    )
    latest_evidence = {}
    for item in record['evidence']:
        if item['kind'] in ('quantity_received', 'invoice_amount'):
            latest_evidence[item['kind']] = item
    assessment['evaluation_basis'] = {
        'commitment_version': max((v['version'] for v in record['commitment_versions']), default=1),
        'commitment_terms': {
            key: record['commitment'][key] for key in (
                'supplier', 'description', 'quantity', 'unit', 'amount',
                'currency', 'due_date', 'payment_condition'
            )
        },
        'policy_snapshot': record['payment_policy'],
        'evidence_snapshot': [
            {
                'id': item['id'], 'kind': item['kind'], 'value': item['value'],
                'review_status': item['review_status'], 'reviewed_at': item['reviewed_at'],
            }
            for item in latest_evidence.values()
        ],
    }
    proposal_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute(
            'INSERT INTO payment_proposals (id, commitment_id, status, proposal_json, created_at) VALUES (?, ?, ?, ?, ?)',
            (proposal_id, cid, assessment['status'], json.dumps(assessment, sort_keys=True), created_at)
        )
    return {'id': proposal_id, 'commitment_id': cid, 'created_at': created_at, 'proposal': assessment}


@app.get('/api/commitments/{cid}/payment-proposals')
def list_payment_proposals(cid: str):
    return get_record(cid)['payment_proposals']




@app.post('/api/commitments/{cid}/actions', status_code=201)
def create_commitment_action(cid: str, payload: CommitmentActionCreate):
    get_record(cid)
    if not payload.description.strip():
        raise HTTPException(422, 'Action description cannot be blank')
    action_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute('''INSERT INTO commitment_actions
            (id, commitment_id, action_type, description, created_at)
            VALUES (?, ?, ?, ?, ?)''',
            (action_id, cid, payload.action_type, payload.description.strip(), created_at))
    return next(action for action in get_record(cid)['actions'] if action['id'] == action_id)


@app.post('/api/actions/{action_id}/events', status_code=201)
def record_action_event(action_id: str, payload: ActionEventCreate):
    with connect() as db:
        action = db.execute('SELECT * FROM commitment_actions WHERE id=?', (action_id,)).fetchone()
        if not action:
            raise HTTPException(404, 'Action not found')
        previous = db.execute(
            'SELECT event_type FROM action_events WHERE action_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (action_id,)
        ).fetchone()
        current = previous['event_type'] if previous else 'PROPOSED'
        transitions = {
            'PROPOSED': {'APPROVED', 'REJECTED'},
            'APPROVED': {'EXECUTION_REPORTED'},
            'EXECUTION_REPORTED': {'OUTCOME_VERIFIED', 'OUTCOME_FAILED'},
        }
        if payload.event_type not in transitions.get(current, set()):
            raise HTTPException(409, f'Invalid action transition: {current} -> {payload.event_type}')
        if not payload.actor.strip():
            raise HTTPException(422, 'Actor identity is required')
        if payload.event_type == 'OUTCOME_VERIFIED' and not payload.evidence_id:
            raise HTTPException(422, 'Verified outcome must reference verified evidence')
        if payload.evidence_id:
            evidence = db.execute(
                'SELECT id FROM evidence WHERE id=? AND commitment_id=?',
                (payload.evidence_id, action['commitment_id'])
            ).fetchone()
            if not evidence:
                raise HTTPException(422, 'Evidence must belong to the same commitment as the action')
            if payload.event_type == 'OUTCOME_VERIFIED':
                review = db.execute(
                    'SELECT status FROM evidence_reviews WHERE evidence_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
                    (payload.evidence_id,)
                ).fetchone()
                if not review or review['status'] != 'VERIFIED':
                    raise HTTPException(422, 'Outcome evidence must be marked VERIFIED before outcome verification')
        event_id = str(uuid.uuid4())
        created_at = utc_now()
        db.execute('''INSERT INTO action_events
            (id, action_id, event_type, actor, note, evidence_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (event_id, action_id, payload.event_type, payload.actor.strip(),
             payload.note.strip(), payload.evidence_id, created_at))
    return {'id': event_id, 'action_id': action_id, 'event_type': payload.event_type,
            'actor': payload.actor.strip(), 'note': payload.note.strip(),
            'evidence_id': payload.evidence_id, 'created_at': created_at}




@app.get('/api/ai/status')
def read_ai_status():
    return {
        'configured': is_configured(),
        'provider': 'NEAR AI Cloud',
        'model': 'Qwen/Qwen3.6-35B-A3B-FP8',
        'privacy_tier': 'TEE model',
        'attestation_verification_required': True,
        'attestation_verified_by_outcome': None,
        'verification_method': 'Preflight UpToDate Intel TDX quote (debug bit, MRCONFIGID, RTMR3) plus exact-byte EIP-191 ECDSA signature; NVIDIA NRAS boolean true and eat_nonce for provider-TEE signatures. HTTPS is not attestation.',
    }


@app.post('/api/commitments/{cid}/ai-brief', status_code=201)
def create_ai_brief(cid: str, request: Request):
    if not is_configured():
        raise HTTPException(503, 'Optional NEAR AI Cloud is not configured. Set NEARAI_API_KEY to enable it.')
    record = get_record(cid)
    client_ip = request.client.host if request.client else 'unknown'
    try:
        limit = max(1, int(os.getenv('OUTCOME_AI_RATE_LIMIT_PER_HOUR', '5')))
    except ValueError:
        limit = 5
    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(hours=1)).isoformat()
    cleanup_cutoff = (now_utc - timedelta(days=1)).isoformat()
    with connect() as db:
        db.execute('DELETE FROM ai_request_limits WHERE created_at < ?', (cleanup_cutoff,))
        recent = db.execute(
            'SELECT COUNT(*) FROM ai_request_limits WHERE client_ip=? AND created_at>=?',
            (client_ip, cutoff)
        ).fetchone()[0]
        if recent >= limit:
            raise HTTPException(429, 'AI brief rate limit reached for this client. Try again later.')
        db.execute(
            'INSERT INTO ai_request_limits (id, client_ip, created_at) VALUES (?, ?, ?)',
            (str(uuid.uuid4()), client_ip, utc_now())
        )
    try:
        result = generate_operational_brief(record)
        if not brief_is_displayable(result):
            raise AIProviderError('NEAR AI response did not pass cryptographic verification.')
    except AIConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except AIProviderError as exc:
        raise HTTPException(502, str(exc))
    brief_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute(
            'INSERT INTO ai_briefs (id, commitment_id, provider, model, brief_json, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (brief_id, cid, result['provider'], result['model'],
             json.dumps(result, sort_keys=True), created_at)
        )
    return {'id': brief_id, 'commitment_id': cid, 'created_at': created_at, **result}


@app.get('/api/commitments/{cid}/audit-anchor')
def get_commitment_audit_anchor(cid: str):
    """Return a privacy-minimized digest for an optional NEAR testnet anchor.

    Raw supplier/evidence notes, destination addresses and AI text are excluded.
    Only the SHA-256 digest is intended for public-chain storage.
    """
    record = get_record(cid)
    commitment = record['commitment']
    # Public-chain metadata is intentionally opaque and structured. Do not hash
    # free-text fields (supplier names, descriptions, payment conditions, notes,
    # evidence sources, issue messages or AI text) into a publicly guessable digest.
    snapshot = {
        'commitment': {
            'quantity': commitment['quantity'],
            'unit': commitment['unit'],
            'amount': commitment['amount'],
            'currency': commitment['currency'],
            'due_date': commitment['due_date'],
        },
        'evidence': [
            {
                'kind': item.get('kind'),
                'review_status': item.get('review_status'),
            }
            for item in record['evidence']
        ],
        'assessment': {
            'status': record['assessment']['status'],
            'issue_codes': sorted(
                str(issue.get('code', 'UNKNOWN'))
                for issue in record['assessment'].get('issues', [])
            ),
        },
        'commitment_versions': [
            {'version': item['version'], 'created_at': item['created_at']}
            for item in record['commitment_versions']
        ],
        'payment_policy': {
            key: record['payment_policy'][key] for key in (
                'enabled', 'max_amount', 'require_full_delivery',
                'require_invoice_match', 'require_verified_evidence',
                'allow_partial_payments', 'configured',
            )
        },
        'payment_proposals': [
            {'status': item['status'], 'created_at': item['created_at']}
            for item in record['payment_proposals']
        ],
        'actions': [
            {
                'action_type': item['action_type'],
                'status': item['status'],
                'events': [
                    {'event_type': event['event_type'], 'created_at': event['created_at']}
                    for event in item['events']
                ],
            }
            for item in record['actions']
        ],
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    opaque_commitment_ref = 'outcome-' + hashlib.sha256(commitment['id'].encode('utf-8')).hexdigest()
    return {
        'commitment_id': opaque_commitment_ref,
        'event_type': 'COMMITMENT_STATE_ANCHORED',
        'payload_hash': hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
        'canonicalization': 'UTF-8 JSON; sorted keys; compact separators',
        'privacy': 'Opaque commitment reference and digest only. Free text, supplier identity, evidence values/sources, destinations and AI text are excluded.',
    }


@app.get('/api/patterns')
def read_supplier_patterns():
    records = list_commitments()
    commitments = [record['commitment'] for record in records]
    evidence_by_commitment = {
        record['commitment']['id']: record['evidence']
        for record in records
    }
    return detect_supplier_patterns(commitments, evidence_by_commitment)




@app.post('/api/demo/seed', status_code=201)
def seed_demo():
    if list_commitments():
        raise HTTPException(409, 'Demo seed requires an empty workspace. Existing commitments were not changed.')
    first = create_commitment(CommitmentCreate(
        supplier='Acme Supplies', description='Packaging boxes — purchase order PO-104',
        quantity=100, unit='boxes', amount=500, currency='USD',
        due_date='2026-12-01', payment_condition='Pay after verified full delivery',
    ))
    second = create_commitment(CommitmentCreate(
        supplier='Acme Supplies', description='Additional packaging boxes — purchase order PO-109',
        quantity=50, unit='boxes', amount=250, currency='USD',
        due_date='2026-12-05', payment_condition='Pay after verified full delivery',
    ))
    for record, received, invoice in ((first, 80, 500), (second, 40, 250)):
        cid = record['commitment']['id']
        receiving = add_evidence(cid, EvidenceCreate(
            kind='quantity_received', value=received,
            note='Synthetic demo receiving record; not a real supplier document.',
            source='demo_fixture',
        ))['evidence'][-1]
        invoicing = add_evidence(cid, EvidenceCreate(
            kind='invoice_amount', value=invoice,
            note='Synthetic demo invoice record; not a real supplier document.',
            source='demo_fixture',
        ))['evidence'][-1]
        review_evidence(receiving['id'], EvidenceReviewCreate(
            status='VERIFIED', reviewer='DEMO_FIXTURE',
            note='Synthetic fixture pre-reviewed for demonstration only.',
        ))
        review_evidence(invoicing['id'], EvidenceReviewCreate(
            status='VERIFIED', reviewer='DEMO_FIXTURE',
            note='Synthetic fixture pre-reviewed for demonstration only.',
        ))
    first_id = first['commitment']['id']
    update_payment_policy(first_id, PaymentPolicyUpdate(
        enabled=True, max_amount=500, allowed_destination='acme-demo-wallet.near',
        require_full_delivery=True, require_invoice_match=True,
        require_verified_evidence=True, allow_partial_payments=False,
    ))
    create_payment_proposal(first_id, PaymentProposalCreate(
        amount=500, currency='USD', destination='acme-demo-wallet.near',
    ))
    create_commitment_action(first_id, CommitmentActionCreate(
        action_type='RECONCILE_DELIVERY',
        description='Ask the supplier to deliver the missing 20 boxes or issue a credit note.',
    ))
    return {'status': 'seeded', 'synthetic_data': True, 'commitments': list_commitments(),
            'patterns': read_supplier_patterns()}


@app.post('/api/demo/reset')
def reset_demo():
    if os.getenv('OUTCOME_ALLOW_DEMO_RESET', 'false').lower() != 'true':
        raise HTTPException(403, 'Demo reset is disabled. Set OUTCOME_ALLOW_DEMO_RESET=true only in a disposable local environment.')
    with connect() as db:
        db.execute('DELETE FROM ai_briefs')
        db.execute('DELETE FROM ai_request_limits')
        db.execute('DELETE FROM action_events')
        db.execute('DELETE FROM commitment_actions')
        db.execute('DELETE FROM payment_proposals')
        db.execute('DELETE FROM payment_policy_history')
        db.execute('DELETE FROM evidence_reviews')
        db.execute('DELETE FROM payment_policies')
        db.execute('DELETE FROM assessment_history')
        db.execute('DELETE FROM commitment_versions')
        db.execute('DELETE FROM evidence')
        db.execute('DELETE FROM commitments')
    return {'status': 'reset'}
