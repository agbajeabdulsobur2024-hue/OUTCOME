import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.engine import assess_commitment
from app.payments import evaluate_payment_proposal
from app.patterns import detect_supplier_patterns

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('OUTCOME_DATA_DIR', ROOT / 'data'))
DB_PATH = DATA_DIR / 'outcome.db'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


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
            updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS evidence_reviews (
            id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id),
            status TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_proposals (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            status TEXT NOT NULL, proposal_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS commitment_actions (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            action_type TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS action_events (
            id TEXT PRIMARY KEY, action_id TEXT NOT NULL REFERENCES commitment_actions(id),
            event_type TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL,
            evidence_id TEXT REFERENCES evidence(id), created_at TEXT NOT NULL)''')
        policy_columns = {row['name'] for row in db.execute('PRAGMA table_info(payment_policies)').fetchall()}
        if 'require_verified_evidence' not in policy_columns:
            db.execute('ALTER TABLE payment_policies ADD COLUMN require_verified_evidence INTEGER NOT NULL DEFAULT 1')


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='OUTCOME', description='Supplier commitment intelligence: compare what was agreed with what the evidence shows.', version='0.2.0', lifespan=lifespan)
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
        proposals = [dict(row) for row in db.execute(
            'SELECT id, status, proposal_json, created_at FROM payment_proposals WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
        ).fetchall()]
        action_rows = [dict(row) for row in db.execute(
            'SELECT id, action_type, description, created_at FROM commitment_actions WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
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
            'updated_at': policy_row['updated_at'],
            'configured': True,
        } if policy_row else {
            'enabled': False, 'max_amount': commitment['amount'],
            'allowed_destination': '', 'require_full_delivery': True,
            'require_invoice_match': True, 'require_verified_evidence': True, 'configured': False,
        }),
        'payment_proposals': [
            {'id': row['id'], 'status': row['status'], 'proposal': json.loads(row['proposal_json']), 'created_at': row['created_at']}
            for row in proposals
        ],
        'actions': actions,
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
    return {'status': 'ok', 'service': 'outcome', 'version': app.version}


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
    with connect() as db:
        db.execute('''INSERT INTO payment_policies
            (commitment_id, enabled, max_amount, allowed_destination,
             require_full_delivery, require_invoice_match, require_verified_evidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(commitment_id) DO UPDATE SET
            enabled=excluded.enabled, max_amount=excluded.max_amount,
            allowed_destination=excluded.allowed_destination,
            require_full_delivery=excluded.require_full_delivery,
            require_invoice_match=excluded.require_invoice_match,
            require_verified_evidence=excluded.require_verified_evidence,
            updated_at=excluded.updated_at''',
            (cid, int(payload.enabled), payload.max_amount, payload.allowed_destination.strip(),
             int(payload.require_full_delivery), int(payload.require_invoice_match),
             int(payload.require_verified_evidence), utc_now()))
    return get_record(cid)['payment_policy']


@app.post('/api/commitments/{cid}/payment-proposals', status_code=201)
def create_payment_proposal(cid: str, payload: PaymentProposalCreate):
    record = get_record(cid)
    assessment = evaluate_payment_proposal(
        record['commitment'], record['evidence'], record['payment_policy'],
        payload.amount, payload.currency, payload.destination,
    )
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
                record = get_record(action['commitment_id'])
                evidence_record = next((item for item in record['evidence'] if item['id'] == payload.evidence_id), None)
                if not evidence_record or evidence_record['review_status'] != 'VERIFIED':
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


@app.get('/api/patterns')
def read_supplier_patterns():
    records = list_commitments()
    commitments = [record['commitment'] for record in records]
    evidence_by_commitment = {
        record['commitment']['id']: record['evidence']
        for record in records
    }
    return detect_supplier_patterns(commitments, evidence_by_commitment)


@app.post('/api/demo/reset')
def reset_demo():
    with connect() as db:
        db.execute('DELETE FROM action_events')
        db.execute('DELETE FROM commitment_actions')
        db.execute('DELETE FROM payment_proposals')
        db.execute('DELETE FROM evidence_reviews')
        db.execute('DELETE FROM payment_policies')
        db.execute('DELETE FROM assessment_history')
        db.execute('DELETE FROM commitment_versions')
        db.execute('DELETE FROM evidence')
        db.execute('DELETE FROM commitments')
    return {'status': 'reset'}
