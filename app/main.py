import os, sqlite3, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.engine import assess_commitment

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('OUTCOME_DATA_DIR', ROOT / 'data'))
DB_PATH = DATA_DIR / 'outcome.db'

def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    return db

def init_db():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS commitments (id TEXT PRIMARY KEY, supplier TEXT NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, due_date TEXT NOT NULL, payment_condition TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id), kind TEXT NOT NULL, value REAL, note TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL)')

@asynccontextmanager
async def lifespan(app):
    init_db(); yield

app = FastAPI(title='OUTCOME', description='Supplier commitment intelligence: compare what was agreed with what the evidence shows.', version='0.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

class CommitmentCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    quantity: float = Field(gt=0)
    unit: str = Field(default='units', min_length=1, max_length=40)
    amount: float = Field(gt=0)
    currency: str = Field(default='USD', min_length=3, max_length=8)
    due_date: str
    payment_condition: str = Field(default='Pay after verified delivery', max_length=300)

class EvidenceCreate(BaseModel):
    kind: Literal['quantity_received','invoice_amount','note']
    value: float | None = None
    note: str = Field(default='', max_length=1000)
    source: str = Field(default='manual', max_length=100)

def get_record(cid):
    with connect() as db:
        c = db.execute('SELECT * FROM commitments WHERE id=?',(cid,)).fetchone()
        if not c: raise HTTPException(404,'Commitment not found')
        commitment = dict(c)
        evidence = [dict(r) for r in db.execute('SELECT * FROM evidence WHERE commitment_id=? ORDER BY created_at,rowid',(cid,)).fetchall()]
    return {'commitment':commitment,'evidence':evidence,'assessment':assess_commitment(commitment,evidence)}

@app.get('/')
def home(): return FileResponse(ROOT / 'static' / 'index.html')
@app.get('/api/health')
def health(): return {'status':'ok','service':'outcome','version':app.version}
@app.get('/api/commitments')
def list_commitments():
    with connect() as db: ids=[r['id'] for r in db.execute('SELECT id FROM commitments ORDER BY created_at DESC').fetchall()]
    return [get_record(cid) for cid in ids]
@app.post('/api/commitments',status_code=201)
def create_commitment(payload: CommitmentCreate):
    try: datetime.fromisoformat(payload.due_date)
    except ValueError: raise HTTPException(422,'due_date must be an ISO date, e.g. 2026-11-14')
    cid=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute('INSERT INTO commitments VALUES (?,?,?,?,?,?,?,?,?,?)',(cid,payload.supplier,payload.description,payload.quantity,payload.unit,payload.amount,payload.currency.upper(),payload.due_date[:10],payload.payment_condition,now))
    return get_record(cid)
@app.get('/api/commitments/{cid}')
def read_commitment(cid: str): return get_record(cid)
@app.get('/api/commitments/{cid}/assessment')
def read_assessment(cid: str): return get_record(cid)['assessment']
@app.post('/api/commitments/{cid}/evidence',status_code=201)
def add_evidence(cid: str,payload: EvidenceCreate):
    get_record(cid)
    if payload.kind in ('quantity_received','invoice_amount') and payload.value is None: raise HTTPException(422,'A numeric value is required for this evidence type')
    if payload.value is not None and payload.value < 0: raise HTTPException(422,'Evidence value cannot be negative')
    with connect() as db:
        db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),cid,payload.kind,payload.value,payload.note,payload.source,datetime.now(timezone.utc).isoformat()))
    return get_record(cid)
@app.post('/api/demo/reset')
def reset_demo():
    with connect() as db: db.execute('DELETE FROM evidence'); db.execute('DELETE FROM commitments')
    return {'status':'reset'}
