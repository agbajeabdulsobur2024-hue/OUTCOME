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
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.engine import assess_commitment
from app.payments import evaluate_payment_proposal
from app.patterns import detect_supplier_patterns
from app.ai import (
    AIConfigurationError, AIProviderError, brief_is_displayable,
    extract_business_event, generate_operational_brief, is_configured, public_ai_brief_entry,
)
from app.intake import IntakeRejection, accept_confirmation, model_catalogue, prepare_intake
from app import anchors, auth_near, finance, onboarding, safety, tenancy

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
        db.execute('''CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_user_id TEXT,
            created_at TEXT NOT NULL, expires_at TEXT, synthetic INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS auth_challenges (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, nonce_hex TEXT NOT NULL,
            message TEXT NOT NULL, recipient TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, account_id TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS payment_decisions (
            id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            decision TEXT NOT NULL, actor TEXT NOT NULL, outcome_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS audit_receipts (
            id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id),
            transaction_hash TEXT NOT NULL UNIQUE, signer_account_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL, execution_status TEXT NOT NULL,
            explorer_url TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(commitment_id, payload_hash))''')
        db.execute('''CREATE TABLE IF NOT EXISTS request_limits (
            id TEXT PRIMARY KEY, client_ip TEXT NOT NULL, bucket TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS account_onboarding (
            user_id TEXT PRIMARY KEY, status TEXT NOT NULL, step INTEGER NOT NULL,
            updated_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_request_limits_lookup ON request_limits(bucket, client_ip, created_at)')
        commitment_columns = {row['name'] for row in db.execute('PRAGMA table_info(commitments)').fetchall()}
        if 'workspace_id' not in commitment_columns:
            db.execute('ALTER TABLE commitments ADD COLUMN workspace_id TEXT')
        if 'commitment_kind' not in commitment_columns:
            db.execute("ALTER TABLE commitments ADD COLUMN commitment_kind TEXT NOT NULL DEFAULT 'general'")
        db.execute('CREATE INDEX IF NOT EXISTS idx_commitments_workspace ON commitments(workspace_id)')
        policy_columns = {row['name'] for row in db.execute('PRAGMA table_info(payment_policies)').fetchall()}
        if 'require_verified_evidence' not in policy_columns:
            db.execute('ALTER TABLE payment_policies ADD COLUMN require_verified_evidence INTEGER NOT NULL DEFAULT 1')
        if 'allow_partial_payments' not in policy_columns:
            db.execute('ALTER TABLE payment_policies ADD COLUMN allow_partial_payments INTEGER NOT NULL DEFAULT 0')


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(
    title='OUTCOME',
    description='General-purpose financial accountability: compare what was agreed with what the evidence shows, for any commitment a person records.',
    version='0.5.0',
    lifespan=lifespan,
)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.middleware('http')
async def guard_public_requests(request: Request, call_next):
    if not request.url.path.startswith('/api/'):
        request.state.tenant = {'workspace_id': None, 'user': None, 'kind': 'global'}
        return await call_next(request)
    writing = request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
    try:
        if writing:
            safety.reject_upload(request)
        with connect() as db:
            if writing:
                safety.enforce_write_limits(db, request)
            scope = tenancy.resolve(db, request)
        if writing and safety.blocks_shared_write(scope, request.url.path):
            return JSONResponse(
                status_code=409,
                content={'detail': 'Create your own workspace before recording anything. The shared database was not changed.'},
            )
        request.state.tenant = scope
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
    tokens = tenancy.activate(request.state.tenant)
    try:
        return await call_next(request)
    finally:
        tenancy.deactivate(tokens)


class CommitmentCreate(BaseModel):
    supplier: str | None = Field(default=None, max_length=120)
    counterparty: str | None = Field(default=None, max_length=120)
    commitment_kind: str = Field(default='general', min_length=1, max_length=48)
    description: str = Field(min_length=1, max_length=500)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    unit: str = Field(default='units', min_length=1, max_length=40)
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(default='USD', min_length=3, max_length=8)
    due_date: str
    payment_condition: str = Field(
        default='Release only after the agreed obligation is verified', max_length=300,
    )

    @model_validator(mode='after')
    def name_the_other_party(self):
        left = (self.supplier or '').strip()
        right = (self.counterparty or '').strip()
        if left and right and left != right:
            raise ValueError('counterparty and supplier must name the same party')
        party = right or left
        if not party:
            raise ValueError('Name the other party on this commitment')
        kind = self.commitment_kind.strip()
        if not kind:
            raise ValueError('commitment_kind cannot be blank')
        self.supplier = party
        self.counterparty = party
        self.commitment_kind = kind
        return self


class EvidenceCreate(BaseModel):
    kind: Literal[
        'quantity_received', 'obligation_fulfilled',
        'invoice_amount', 'amount_claimed', 'note',
    ]
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
    action_type: Literal[
        'CONFIRM_OBLIGATION', 'CONTACT_SUPPLIER', 'REQUEST_EVIDENCE',
        'HOLD_PAYMENT', 'RECONCILE_DELIVERY', 'OTHER',
    ]
    description: str = Field(min_length=3, max_length=1000)


class ActionEventCreate(BaseModel):
    event_type: Literal['APPROVED', 'REJECTED', 'EXECUTION_REPORTED', 'OUTCOME_VERIFIED', 'OUTCOME_FAILED']
    actor: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=1000)
    evidence_id: str | None = None


class IntakeText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class IntakeConfirm(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    confirmed: bool
    proposal: dict


class CommitmentAmendment(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    due_date: str | None = None
    payment_condition: str | None = Field(default=None, max_length=300)
    commitment_kind: str | None = Field(default=None, min_length=1, max_length=48)
    counterparty: str | None = Field(default=None, min_length=1, max_length=120)


def get_record(cid: str, db=None):
    """Read one commitment. Pass db to reuse the caller's connection."""
    if db is None:
        with connect() as owned:
            return get_record(cid, owned)
    c = db.execute('SELECT * FROM commitments WHERE id=?', (cid,)).fetchone()
    if not c or not tenancy.same_workspace(c['workspace_id']):
        raise HTTPException(404, 'Commitment not found')
    commitment = dict(c)
    commitment['counterparty'] = commitment.get('supplier')
    commitment['commitment_kind'] = commitment.get('commitment_kind') or 'general'
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
    payment_decisions = [dict(row) for row in db.execute(
        '''SELECT id, proposal_id, decision, actor, outcome_json, created_at
           FROM payment_decisions WHERE commitment_id=? ORDER BY created_at, rowid''',
        (cid,),
    ).fetchall()]
    action_rows = [dict(row) for row in db.execute(
        'SELECT id, action_type, description, created_at FROM commitment_actions WHERE commitment_id=? ORDER BY created_at, rowid', (cid,)
    ).fetchall()]
    ai_briefs = [dict(row) for row in db.execute(
        'SELECT id, provider, model, brief_json, created_at FROM ai_briefs WHERE commitment_id=? ORDER BY created_at, rowid',
        (cid,)
    ).fetchall()]
    audit_receipts = [dict(row) for row in db.execute(
        '''SELECT id, transaction_hash, signer_account_id, payload_hash, execution_status,
                  explorer_url, created_at
           FROM audit_receipts WHERE commitment_id=? ORDER BY created_at, rowid''',
        (cid,),
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
    for row in payment_decisions:
        outcome = json.loads(row['outcome_json'])
        timeline.append({
            'event_type': 'PAYMENT_DECISION',
            'title': 'Human payment decision: ' + row['decision'].replace('_', ' '),
            'created_at': row['created_at'],
            'details': outcome.get('reason', ''),
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
    record = {
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
        'payment_decisions': [
            {'id': row['id'], 'proposal_id': row['proposal_id'], 'decision': row['decision'],
             'actor': row['actor'], 'outcome': json.loads(row['outcome_json']), 'created_at': row['created_at']}
            for row in payment_decisions
        ],
        'actions': actions,
        'ai_brief_history': published_briefs,
        'audit_receipts': [
            {**row, 'independently_verified': row['execution_status'] in {'FINAL', 'FINAL_SUCCESS'}}
            for row in audit_receipts
        ],
        'timeline': timeline,
    }
    anchor = audit_anchor_for_record(record)
    for receipt in record['audit_receipts']:
        receipt['matches_current_record'] = receipt['payload_hash'] == anchor['payload_hash']
    record['audit_anchor'] = anchor
    return record


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


def patterns_for_records(records):
    return detect_supplier_patterns(
        [record['commitment'] for record in records],
        {record['commitment']['id']: record['evidence'] for record in records},
    )


def records_for_current_workspace(db, limit=None):
    """Load commitments in the current workspace on the connection already open."""
    workspace_id = tenancy.current_workspace_id()
    if workspace_id is None:
        rows = db.execute(
            'SELECT id FROM commitments WHERE workspace_id IS NULL ORDER BY created_at DESC'
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT id FROM commitments WHERE workspace_id=? ORDER BY created_at DESC',
            (workspace_id,),
        ).fetchall()
    ids = [row['id'] for row in rows]
    if limit is not None:
        ids = ids[:limit]
    return [get_record(item, db) for item in ids]


@app.get('/api/commitments')
def list_commitments():
    with connect() as db:
        return records_for_current_workspace(db)


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
            '''INSERT INTO commitments
               (id, supplier, description, quantity, unit, amount, currency, due_date,
                payment_condition, created_at, workspace_id, commitment_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (cid, payload.supplier, payload.description.strip(), payload.quantity, payload.unit,
             payload.amount, terms['currency'], terms['due_date'], payload.payment_condition, now,
             tenancy.current_workspace_id(), payload.commitment_kind)
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
    if payload.kind in (
        'quantity_received', 'obligation_fulfilled', 'invoice_amount', 'amount_claimed',
    ) and payload.value is None:
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
        workspace_id = tenancy.current_workspace_id()
        if workspace_id is None:
            exists = db.execute(
                '''SELECT e.id FROM evidence e JOIN commitments c ON c.id=e.commitment_id
                   WHERE e.id=? AND c.workspace_id IS NULL''',
                (evidence_id,),
            ).fetchone()
        else:
            exists = db.execute(
                '''SELECT e.id FROM evidence e JOIN commitments c ON c.id=e.commitment_id
                   WHERE e.id=? AND c.workspace_id=?''',
                (evidence_id, workspace_id),
            ).fetchone()
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
    if 'counterparty' in changes:
        party = changes.pop('counterparty').strip()
        if not party:
            raise HTTPException(422, 'counterparty cannot be blank')
        changes['supplier'] = party
    if 'commitment_kind' in changes:
        changes['commitment_kind'] = changes['commitment_kind'].strip()
        if not changes['commitment_kind']:
            raise HTTPException(422, 'commitment_kind cannot be blank')
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
    new_terms['commitment_kind'] = old_terms.get('commitment_kind') or 'general'
    new_terms.update(changes)
    new_terms['counterparty'] = new_terms['supplier']
    policy = current['payment_policy']
    if policy.get('configured') and float(policy['max_amount']) > float(new_terms['amount']):
        raise HTTPException(409, 'Existing payment-policy cap exceeds the amended commitment value. Lower the policy cap before amending the amount.')
    version = len(current['commitment_versions']) + 1
    now = utc_now()
    with connect() as db:
        db.execute('''UPDATE commitments SET supplier=?, description=?, quantity=?, unit=?,
            amount=?, currency=?, due_date=?, payment_condition=?, commitment_kind=? WHERE id=?''',
            (new_terms['supplier'], new_terms['description'], new_terms['quantity'], new_terms['unit'],
             new_terms['amount'], new_terms['currency'], new_terms['due_date'],
             new_terms['payment_condition'], new_terms['commitment_kind'], cid))
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


class PaymentDecisionCreate(BaseModel):
    proposal_id: str = Field(min_length=8, max_length=80)
    decision: Literal['APPROVED', 'REJECTED']
    actor: str = Field(min_length=1, max_length=120)


@app.post('/api/commitments/{cid}/payment-decisions', status_code=201)
def decide_payment(cid: str, payload: PaymentDecisionCreate):
    """Record a person's payment decision. Wallet connection does not call this."""
    record = get_record(cid)
    proposal = next((item for item in record['payment_proposals'] if item['id'] == payload.proposal_id), None)
    if not proposal:
        raise HTTPException(404, 'Payment proposal not found')
    if payload.decision == 'APPROVED' and proposal['status'] != 'AWAITING_HUMAN_APPROVAL':
        raise HTTPException(409, 'A blocked or unfinished proposal cannot be authorised. No transfer was submitted.')
    if any(item['proposal_id'] == payload.proposal_id for item in record['payment_decisions']):
        raise HTTPException(409, 'This proposal already has a human decision.')
    try:
        outcome = finance.human_payment_outcome(
            payload.decision, proposal['status'], finance.observe_official_network(),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    decision_id = str(uuid.uuid4())
    created_at = utc_now()
    proposal_status = 'APPROVED_NOT_SUBMITTED' if payload.decision == 'APPROVED' else 'REJECTED_BY_HUMAN'
    stored_proposal = dict(proposal['proposal'])
    stored_proposal['status'] = proposal_status
    stored_proposal['transfer_executed'] = False
    stored_proposal['transaction_hash'] = None
    stored_proposal['financial_action'] = outcome['financial_action']
    with connect() as db:
        db.execute(
            '''INSERT INTO payment_decisions
               (id, proposal_id, commitment_id, decision, actor, outcome_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (decision_id, payload.proposal_id, cid, payload.decision, payload.actor.strip(),
             json.dumps(outcome, sort_keys=True), created_at),
        )
        db.execute(
            'UPDATE payment_proposals SET status=?, proposal_json=? WHERE id=? AND commitment_id=?',
            (proposal_status, json.dumps(stored_proposal, sort_keys=True), payload.proposal_id, cid),
        )
    return {'id': decision_id, 'commitment_id': cid, 'proposal_id': payload.proposal_id,
            'proposal_status': proposal_status, 'created_at': created_at, 'outcome': outcome}


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
        workspace_id = tenancy.current_workspace_id()
        if workspace_id is None:
            action = db.execute(
                '''SELECT a.* FROM commitment_actions a
                   JOIN commitments c ON c.id=a.commitment_id
                   WHERE a.id=? AND c.workspace_id IS NULL''',
                (action_id,),
            ).fetchone()
        else:
            action = db.execute(
                '''SELECT a.* FROM commitment_actions a
                   JOIN commitments c ON c.id=a.commitment_id
                   WHERE a.id=? AND c.workspace_id=?''',
                (action_id, workspace_id),
            ).fetchone()
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
                'SELECT id, kind, value FROM evidence WHERE id=? AND commitment_id=?',
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
                agreed = db.execute(
                    'SELECT quantity FROM commitments WHERE id=?',
                    (action['commitment_id'],),
                ).fetchone()
                covers_agreement = (
                    evidence['kind'] in ('quantity_received', 'obligation_fulfilled')
                    and evidence['value'] is not None
                    and agreed is not None
                    and float(evidence['value']) >= float(agreed['quantity'])
                )
                if not covers_agreement:
                    raise HTTPException(422, 'Verified outcome must cite a received quantity that covers the agreed amount. The original short count does not prove fulfilment.')
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


def reserve_ai_call(request: Request) -> None:
    """Count one NEAR AI Cloud call against the existing per-client limit."""
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


def workspace_records() -> list[dict]:
    with connect() as db:
        return records_for_current_workspace(db, limit=40)


def store_ai_brief(cid: str, result: dict) -> dict:
    brief_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute(
            'INSERT INTO ai_briefs (id, commitment_id, provider, model, brief_json, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (brief_id, cid, result['provider'], result['model'],
             json.dumps(result, sort_keys=True), created_at)
        )
    return {'id': brief_id, 'commitment_id': cid, 'created_at': created_at, **result}


@app.post('/api/commitments/{cid}/ai-brief', status_code=201)
def create_ai_brief(cid: str, request: Request):
    if not is_configured():
        raise HTTPException(503, 'Optional NEAR AI Cloud is not configured. Set NEARAI_API_KEY to enable it.')
    record = get_record(cid)
    reserve_ai_call(request)
    try:
        result = generate_operational_brief(record)
        if not brief_is_displayable(result):
            raise AIProviderError('NEAR AI response did not pass cryptographic verification.')
    except AIConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except AIProviderError as exc:
        raise HTTPException(502, str(exc))
    return store_ai_brief(cid, result)


def _commitment_from_proposal(fields: dict) -> CommitmentCreate:
    return CommitmentCreate(
        counterparty=fields['counterparty'],
        commitment_kind=fields.get('commitment_kind') or 'general',
        description=fields['description'],
        quantity=fields['quantity'],
        unit=fields['unit'],
        amount=fields['amount'],
        currency=fields['currency'],
        due_date=fields['due_date'],
        payment_condition=fields.get('payment_condition') or 'Release only after the agreed obligation is verified',
    )


def _evidence_from_proposal(item: dict) -> EvidenceCreate:
    return EvidenceCreate(
        kind=item['kind'],
        value=item.get('value'),
        note=item.get('note') or '',
        source=item.get('source') or 'intake',
    )


@app.post('/api/intake')
def review_intake(payload: IntakeText, request: Request):
    """Extract a proposal. This does not save a commitment, evidence, or payment."""
    if not is_configured():
        raise HTTPException(
            503,
            'Optional NEAR AI Cloud is not configured. Set NEARAI_API_KEY to enable intake. The manual forms still work.',
        )
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, 'Describe the event in words.')
    reserve_ai_call(request)
    records = workspace_records()
    try:
        extracted = extract_business_event(text, model_catalogue(records))
    except AIConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except AIProviderError as exc:
        raise HTTPException(502, str(exc))
    prepared = prepare_intake(text, extracted['extraction'], records)
    return {
        'text': text,
        'status': prepared['status'],
        'clarifications': prepared['clarifications'],
        'proposal': prepared['proposal'],
        'confirmation_required': prepared['status'] == 'ready_for_confirmation',
        'writes_performed': False,
        'payment_status': prepared['payment_status'],
        'payment_wording': prepared['payment_wording'],
        'extraction_attestation': {
            'provider': extracted['provider'],
            'model': extracted['model'],
            'attestation_status': extracted['attestation_status'],
            'verification': extracted['verification'],
        },
    }


@app.post('/api/intake/confirm')
def confirm_intake(payload: IntakeConfirm, request: Request):
    """Save a confirmed proposal, then assess it. A brief is advisory and fail-closed."""
    if payload.confirmed is not True:
        raise HTTPException(422, 'Explicit confirmation is required before records are saved.')
    text = payload.text.strip()
    records = workspace_records()
    try:
        decision = accept_confirmation(text, payload.proposal, records)
    except IntakeRejection as exc:
        raise HTTPException(422, ' '.join(exc.clarifications))
    prepared = decision['prepared']
    proposal = prepared['proposal']
    wrote = False
    record = None
    if decision['write']:
        if proposal['action'] in {'create_commitment', 'create_commitment_and_evidence'}:
            record = create_commitment(_commitment_from_proposal(proposal['commitment']))
            wrote = True
        cid = record['commitment']['id'] if record else proposal['matched_commitment_id']
        for item in proposal['evidence']:
            record = add_evidence(cid, _evidence_from_proposal(item))
            wrote = True
        if record is None:
            record = get_record(cid)
    else:
        cid = proposal.get('matched_commitment_id')
        record = get_record(cid) if cid else None
    insights = None
    withheld = None
    if wrote and record and is_configured():
        try:
            reserve_ai_call(request)
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            withheld = (
                'AI request rate limit reached for this client. The assessment is authoritative. '
                'No brief was marked verified.'
            )
        else:
            try:
                result = generate_operational_brief(record)
                if not brief_is_displayable(result):
                    raise AIProviderError('NEAR AI response did not pass cryptographic verification.')
                insights = store_ai_brief(record['commitment']['id'], result)
                record = get_record(record['commitment']['id'])
            except AIProviderError:
                withheld = (
                    'The operational brief was withheld because cryptographic verification failed. '
                    'The assessment is authoritative. Nothing was marked verified.'
                )
            except AIConfigurationError:
                withheld = 'NEAR AI Cloud is not configured, so no brief was generated. The assessment is authoritative.'
    elif wrote and record:
        withheld = 'NEAR AI Cloud is not configured, so no brief was generated. The assessment is authoritative.'
    body = {
        'wrote': wrote,
        'status': prepared['status'],
        'clarifications': prepared['clarifications'],
        'commitment_id': None if record is None else record['commitment']['id'],
        'record': record,
        'assessment': None if record is None else record['assessment'],
        'insights': insights,
        'insights_withheld_reason': withheld,
        'payment_status': 'NOT_SUBMITTED',
        'payment_wording': (
            'NOT SUBMITTED. No payment was approved, submitted, or completed. '
            'Connecting a wallet is not payment authorisation.'
        ),
    }
    return JSONResponse(status_code=201 if wrote else 200, content=body)


def audit_anchor_for_record(record: dict) -> dict:
    """Privacy-minimized digest. Free text never enters the hash."""
    commitment = record['commitment']
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


@app.get('/api/commitments/{cid}/audit-anchor')
def get_commitment_audit_anchor(cid: str):
    return audit_anchor_for_record(get_record(cid))


@app.get('/api/patterns')
def read_supplier_patterns():
    return patterns_for_records(list_commitments())




def populate_synthetic_demo(db=None):
    """Write the Acme sample and read it back on the same connection.

    The hosted function limit is 60 seconds. A fresh Turso connection per
    row, or another connection for each commitment read, does not finish
    in that window.
    """
    if db is None:
        with connect() as owned:
            return populate_synthetic_demo(owned)
    samples = (
        {
            'description': 'Printer paper — purchase order PO-104',
            'quantity': 50, 'amount': 250, 'due_date': '2026-12-01',
            'received': 42, 'invoice': 250, 'policy': True,
        },
        {
            'description': 'Printer paper — purchase order PO-109',
            'quantity': 20, 'amount': 100, 'due_date': '2026-12-05',
            'received': 16, 'invoice': 100, 'policy': False,
        },
    )
    workspace_id = tenancy.current_workspace_id()
    for sample in samples:
        payload = CommitmentCreate(
            supplier='Acme Supplies', description=sample['description'],
            quantity=sample['quantity'], unit='cartons', amount=sample['amount'],
            currency='USD', due_date=sample['due_date'],
            payment_condition='Pay after verified full delivery',
            commitment_kind='supplier_delivery',
        )
        cid = str(uuid.uuid4())
        now = utc_now()
        terms = payload.model_dump()
        terms['currency'] = payload.currency.upper()
        terms['due_date'] = datetime.fromisoformat(payload.due_date).date().isoformat()
        db.execute(
            '''INSERT INTO commitments
               (id, supplier, description, quantity, unit, amount, currency, due_date,
                payment_condition, created_at, workspace_id, commitment_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (cid, payload.supplier, payload.description.strip(), payload.quantity, payload.unit,
             payload.amount, terms['currency'], terms['due_date'], payload.payment_condition,
             now, workspace_id, payload.commitment_kind),
        )
        db.execute(
            '''INSERT INTO commitment_versions
               (id, commitment_id, version, terms_json, reason, created_at)
               VALUES (?, ?, 1, ?, ?, ?)''',
            (str(uuid.uuid4()), cid, json.dumps(terms, sort_keys=True), 'Original agreement', now),
        )
        evidence = []
        for kind, value, note in (
            ('quantity_received', sample['received'], 'Synthetic demo receiving record; not a real supplier document.'),
            ('invoice_amount', sample['invoice'], 'Synthetic demo invoice record; not a real supplier document.'),
        ):
            evidence_id = str(uuid.uuid4())
            db.execute(
                'INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)',
                (evidence_id, cid, kind, value, note, 'demo_fixture', now),
            )
            db.execute(
                '''INSERT INTO evidence_reviews
                   (id, evidence_id, status, reviewer, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (str(uuid.uuid4()), evidence_id, 'VERIFIED', 'DEMO_FIXTURE',
                 'Synthetic fixture pre-reviewed for demonstration only.', now),
            )
            evidence.append({
                'id': evidence_id, 'kind': kind, 'value': value,
                'review_status': 'VERIFIED', 'reviewed_at': now,
            })
        commitment = {
            'supplier': payload.supplier, 'description': payload.description,
            'quantity': payload.quantity, 'unit': payload.unit, 'amount': payload.amount,
            'currency': terms['currency'], 'due_date': terms['due_date'],
            'payment_condition': payload.payment_condition,
        }
        assessment = assess_commitment(commitment, evidence)
        db.execute(
            '''INSERT INTO assessment_history
               (id, commitment_id, status, assessment_json, created_at)
               VALUES (?, ?, ?, ?, ?)''',
            (str(uuid.uuid4()), cid, assessment['status'], json.dumps(assessment, sort_keys=True), now),
        )
        if not sample['policy']:
            continue
        destination = 'acme-demo-wallet.near'
        policy = {
            'enabled': True, 'max_amount': sample['amount'],
            'allowed_destination': destination,
            'require_full_delivery': True, 'require_invoice_match': True,
            'require_verified_evidence': True, 'allow_partial_payments': False,
            'updated_at': now, 'configured': True,
        }
        db.execute(
            '''INSERT INTO payment_policies
               (commitment_id, enabled, max_amount, allowed_destination,
                require_full_delivery, require_invoice_match, require_verified_evidence,
                allow_partial_payments, updated_at)
               VALUES (?, 1, ?, ?, 1, 1, 1, 0, ?)''',
            (cid, sample['amount'], destination, now),
        )
        db.execute(
            'INSERT INTO payment_policy_history (id, commitment_id, policy_json, created_at) VALUES (?, ?, ?, ?)',
            (str(uuid.uuid4()), cid, json.dumps(policy, sort_keys=True), now),
        )
        proposal = evaluate_payment_proposal(
            commitment, evidence, policy, sample['amount'], 'USD', destination,
        )
        db.execute(
            'INSERT INTO payment_proposals (id, commitment_id, status, proposal_json, created_at) VALUES (?, ?, ?, ?, ?)',
            (str(uuid.uuid4()), cid, proposal['status'], json.dumps(proposal, sort_keys=True), now),
        )
        db.execute(
            '''INSERT INTO commitment_actions
               (id, commitment_id, action_type, description, created_at)
               VALUES (?, ?, ?, ?, ?)''',
            (str(uuid.uuid4()), cid, 'RECONCILE_DELIVERY',
             'Ask the supplier to deliver the missing 8 cartons or issue a credit note.', now),
        )
    records = records_for_current_workspace(db)
    return {
        'status': 'seeded', 'synthetic_data': True, 'commitments': records,
        'patterns': patterns_for_records(records),
    }


@app.post('/api/demo/seed', status_code=201)
def seed_demo():
    if list_commitments():
        raise HTTPException(409, 'Demo seed requires an empty workspace. Existing commitments were not changed.')
    return populate_synthetic_demo()


def _set_cookie(response, request: Request, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, httponly=True, samesite='lax',
        secure=tenancy.cookie_secure(request), path='/',
    )


@app.post('/api/demo/trials', status_code=201)
def create_demo_trial(request: Request, response: Response):
    expires_at = None
    with connect() as db:
        tenancy.purge_expired_demos(db)
        cookie = request.cookies.get(tenancy.DEMO_COOKIE, '')
        existing = db.execute(
            "SELECT id, expires_at FROM workspaces WHERE id=? AND kind='demo'",
            (cookie,),
        ).fetchone() if cookie else None
        if existing and existing['expires_at'] >= utc_now():
            workspace_id = existing['id']
            expires_at = existing['expires_at']
            created = False
        else:
            created_row = tenancy.create_demo_workspace(db)
            workspace_id = created_row['id']
            expires_at = created_row['expires_at']
            created = True
        token = tenancy.use_workspace(workspace_id)
        try:
            if created:
                body = populate_synthetic_demo(db)
            else:
                records = records_for_current_workspace(db)
                body = {
                    'status': 'resumed', 'synthetic_data': True,
                    'commitments': records,
                    'patterns': patterns_for_records(records),
                }
        finally:
            tenancy.reset_workspace(token)
    _set_cookie(response, request, tenancy.DEMO_COOKIE, workspace_id, int(tenancy.trial_ttl().total_seconds()))
    body['workspace'] = 'demo'
    body['expires_at'] = expires_at
    return body


class ChallengeCreate(BaseModel):
    account_id: str = Field(min_length=2, max_length=64)


class SessionCreate(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=80)
    account_id: str = Field(min_length=2, max_length=64)
    public_key: str = Field(min_length=16, max_length=120)
    signature: str = Field(min_length=16, max_length=200)


class AuditReceiptCreate(BaseModel):
    transaction_hash: str = Field(min_length=32, max_length=88)
    signer_account_id: str | None = Field(default=None, max_length=64)


@app.get('/api/session')
def read_public_session(request: Request):
    with connect() as db:
        return tenancy.session_view(db, request)


@app.get('/api/near/contract')
def read_near_contract():
    return anchors.public_contract()


@app.post('/api/auth/challenge', status_code=201)
def create_auth_challenge(payload: ChallengeCreate):
    with connect() as db:
        return auth_near.create_challenge(db, payload.account_id.strip())


@app.post('/api/auth/session', status_code=201)
def create_auth_session(payload: SessionCreate, request: Request, response: Response):
    with connect() as db:
        user = auth_near.consume_challenge(
            db, payload.challenge_id, payload.account_id.strip(),
            payload.public_key.strip(), payload.signature.strip(),
        )
        token = auth_near.issue_session(db, user)
    _set_cookie(response, request, tenancy.SESSION_COOKIE, token, 7 * 24 * 3600)
    return {'authenticated': True, 'account_id': user['account_id'], 'network': 'testnet'}


@app.post('/api/auth/logout')
def logout(response: Response):
    response.delete_cookie(tenancy.SESSION_COOKIE, path='/')
    return {'authenticated': False}


class OnboardingUpdate(BaseModel):
    status: Literal['in_progress', 'completed', 'dismissed']
    step: int = Field(ge=0, le=onboarding.STEP_COUNT - 1)


@app.put('/api/onboarding')
def save_onboarding(payload: OnboardingUpdate):
    user = tenancy.current_user()
    if not user:
        raise HTTPException(401, 'Sign in before saving the introduction.')
    with connect() as db:
        saved = onboarding.write_progress(db, user['user_id'], payload.status, payload.step)
    return saved


@app.post('/api/workspaces', status_code=201)
def create_personal_workspace():
    user = tenancy.current_user()
    if not user:
        raise HTTPException(401, 'Connect your NEAR testnet wallet before creating a workspace.')
    with connect() as db:
        created = tenancy.create_personal_workspace(db, user)
    return {'kind': 'personal', 'created': created['created'], 'synthetic': False}


@app.post('/api/commitments/{cid}/audit-receipts', status_code=201)
def submit_audit_receipt(cid: str, payload: AuditReceiptCreate):
    user = tenancy.current_user()
    if not user:
        raise HTTPException(401, 'Connect and sign in with your NEAR testnet wallet before anchoring.')
    if payload.signer_account_id and payload.signer_account_id != user['account_id']:
        raise HTTPException(403, 'A wallet address in the request is not authentication.')
    record = get_record(cid)
    if not record['commitment'].get('workspace_id'):
        raise HTTPException(403, 'The shared workspace cannot be anchored.')
    anchor = record['audit_anchor']
    if any(item['payload_hash'] == anchor['payload_hash'] for item in record['audit_receipts']):
        raise HTTPException(409, 'This commitment state is already anchored.')
    try:
        checked = anchors.assess_anchor_transaction(
            anchors.fetch_transaction(payload.transaction_hash, user['account_id']),
            expected_signer=user['account_id'],
            expected_receiver=anchors.contract_id(),
            expected_payload_hash=anchor['payload_hash'],
            expected_commitment_ref=anchor['commitment_id'],
            expected_event_type=anchor['event_type'],
        )
    except anchors.AnchorRejected as exc:
        raise HTTPException(exc.status_code, str(exc))
    receipt_id = str(uuid.uuid4())
    created_at = utc_now()
    url = anchors.explorer_url(payload.transaction_hash)
    try:
        with connect() as db:
            db.execute(
                '''INSERT INTO audit_receipts
                   (id, commitment_id, transaction_hash, signer_account_id, payload_hash,
                    execution_status, explorer_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (receipt_id, cid, payload.transaction_hash, user['account_id'], anchor['payload_hash'],
                 checked['execution_status'], url, created_at),
            )
    except Exception as exc:
        message = str(exc).lower()
        if 'unique' in message or 'constraint' in message:
            raise HTTPException(409, 'This anchor was already recorded.')
        raise
    return {
        'id': receipt_id,
        'commitment_id': anchor['commitment_id'],
        'transaction_hash': payload.transaction_hash,
        'signer_account_id': user['account_id'],
        'payload_hash': anchor['payload_hash'],
        'execution_status': checked['execution_status'],
        'explorer_url': url,
        'independently_verified': True,
        'matches_current_record': True,
        'method': anchors.ALLOWED_METHOD,
    }


@app.get('/api/commitments/{cid}/audit-receipts/{receipt_id}')
def read_audit_receipt(cid: str, receipt_id: str):
    record = get_record(cid)
    receipt = next((item for item in record['audit_receipts'] if item['id'] == receipt_id), None)
    if not receipt:
        raise HTTPException(404, 'Anchor not found')
    user = tenancy.current_user()
    rechecked = False
    if user and user['account_id'] == receipt['signer_account_id']:
        try:
            checked = anchors.assess_anchor_transaction(
                anchors.fetch_transaction(receipt['transaction_hash'], receipt['signer_account_id']),
                expected_signer=receipt['signer_account_id'],
                expected_receiver=anchors.contract_id(),
                expected_payload_hash=receipt['payload_hash'],
                expected_commitment_ref=record['audit_anchor']['commitment_id'],
                expected_event_type=record['audit_anchor']['event_type'],
            )
            rechecked = checked['independently_verified']
        except anchors.AnchorRejected:
            rechecked = False
    return {
        'id': receipt['id'],
        'transaction_hash': receipt['transaction_hash'],
        'signer_account_id': receipt['signer_account_id'],
        'payload_hash': receipt['payload_hash'],
        'current_payload_hash': record['audit_anchor']['payload_hash'],
        'matches_current_record': receipt['payload_hash'] == record['audit_anchor']['payload_hash'],
        'execution_status': receipt['execution_status'],
        'explorer_url': receipt['explorer_url'],
        'independently_verified': receipt['independently_verified'] and rechecked,
        'rechecked': rechecked,
        'canonicalization': record['audit_anchor']['canonicalization'],
    }


@app.post('/api/demo/reset')
def reset_demo():
    if os.getenv('OUTCOME_ALLOW_DEMO_RESET', 'false').lower() != 'true':
        raise HTTPException(403, 'Demo reset is disabled. Set OUTCOME_ALLOW_DEMO_RESET=true only in a disposable local environment.')
    with connect() as db:
        tenancy.delete_commitment_rows(db, tenancy.global_commitment_ids(db))
    return {'status': 'reset', 'scope': 'global'}
