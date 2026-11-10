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
            updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_proposals (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            status TEXT NOT NULL, proposal_json TEXT NOT NULL, created_at TEXT NOT NULL)''')


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


class PaymentProposalCreate(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=8)
    destination: str = Field(min_length=1, max_length=200)


def get_record(cid: str):
    with connect() as db:
        c = db.execute('SELECT * FROM commitments WHERE id=?', (cid,)).fetchone()
        if not c:
            raise HTTPException(404, 'Commitment not found')
        commitment = dict(c)
        evidence = [dict(row) for row in db.execute(
            'SELECT * FROM evidence WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
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
            'updated_at': policy_row['updated_at'],
            'configured': True,
        } if policy_row else {
            'enabled': False, 'max_amount': commitment['amount'],
            'allowed_destination': '', 'require_full_delivery': True,
            'require_invoice_match': True, 'configured': False,
        }),
        'payment_proposals': [
            {'id': row['id'], 'status': row['status'], 'proposal': json.loads(row['proposal_json']), 'created_at': row['created_at']}
            for row in proposals
        ],
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


@app.get('/api/commitments/{cid}/payment-policy')
def read_payment_policy(cid: str):
    return get_record(cid)['payment_policy']


@app.put('/api/commitments/{cid}/payment-policy')
def update_payment_policy(cid: str, payload: PaymentPolicyUpdate):
    record = get_record(cid)
    if payload.max_amount > float(record['commitment']['amount']):
        raise HTTPException(422, 'Policy spending limit cannot exceed the recorded commitment amount')
    with connect() as db:
        db.execute('''INSERT INTO payment_policies
            (commitment_id, enabled, max_amount, allowed_destination,
             require_full_delivery, require_invoice_match, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(commitment_id) DO UPDATE SET
            enabled=excluded.enabled, max_amount=excluded.max_amount,
            allowed_destination=excluded.allowed_destination,
            require_full_delivery=excluded.require_full_delivery,
            require_invoice_match=excluded.require_invoice_match,
            updated_at=excluded.updated_at''',
            (cid, int(payload.enabled), payload.max_amount, payload.allowed_destination.strip(),
             int(payload.require_full_delivery), int(payload.require_invoice_match), utc_now()))
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


@app.post('/api/demo/reset')
def reset_demo():
    with connect() as db:
        db.execute('DELETE FROM payment_proposals')
        db.execute('DELETE FROM payment_policies')
        db.execute('DELETE FROM assessment_history')
        db.execute('DELETE FROM commitment_versions')
        db.execute('DELETE FROM evidence')
        db.execute('DELETE FROM commitments')
    return {'status': 'reset'}
