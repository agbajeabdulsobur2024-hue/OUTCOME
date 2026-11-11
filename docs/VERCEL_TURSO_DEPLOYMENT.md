# Vercel + Turso deployment

OUTCOME uses its existing FastAPI application on Vercel and Turso as its durable SQLite-compatible database. Local development continues to use ordinary SQLite.

## Architecture

- **Vercel:** hosts the FastAPI application as a Python Function.
- **FastAPI:** existing application and API routes.
- **Turso:** remote SQLite-compatible persistence over HTTP using the official `turso-serverless` Python driver.
- **NEAR AI Cloud:** optional, user-triggered operational briefs.
- **GitHub:** source of truth and Vercel Git deployments.

The remote driver is designed for serverless environments: it uses HTTP rather than a persistent TCP connection or a writable local database file. The driver currently has beta status; verify the live database workflow before calling the deployment production-ready.

## 1. Create a Turso database

Create a database in the Turso dashboard or with the current Turso CLI quickstart. Generate a database authentication token. Keep both values private.

You need:
- The database URL, normally in the form `libsql://<database>.<group>.turso.io`.
- A database auth token.

Do not commit either value to GitHub or place them in browser-side JavaScript.

## 2. Configure Vercel

Import `agbajeabdulsobur2024-hue/OUTCOME` into Vercel. The root `index.py` exports `app.main:app`, which Vercel's FastAPI detection recognizes. The project pins Python 3.12 in `.python-version`.

Add these environment variables to the Vercel project for Preview and Production:

| Variable | Value |
|---|---|
| `OUTCOME_DB_BACKEND` | `turso` |
| `TURSO_DATABASE_URL` | Your Turso database URL |
| `TURSO_AUTH_TOKEN` | Your Turso auth token |
| `OUTCOME_ALLOW_DEMO_RESET` | `false` |
| `NEARAI_API_KEY` | Optional server-side key; only if enabling NEAR AI Cloud briefs |
| `OUTCOME_NEAR_NETWORK` | `testnet` |
| `OUTCOME_NEAR_CONTRACT_ID` | `thewordweaver.testnet` |
| `OUTCOME_NEAR_RPC_URL` | `https://rpc.testnet.near.org` |

Do not add a NEAR private key, seed phrase, or full-access key. Optional rate-limit and demo-lifetime names are listed in `.env.example`. `OUTCOME_ALLOW_DEMO_RESET` must stay `false` on the hosted app. A reset, even if turned on locally, does not delete personal or guided-demo workspaces.

Redeploy after adding or changing environment variables.

## 3. Database initialization

On application startup, OUTCOME runs its idempotent schema initialization against the configured database. The first startup creates the commitments, evidence, version history, assessment history, payment policies, policy history, payment proposals, action events, AI brief history and request-limit tables.

Foreign-key enforcement is explicitly enabled on every connection. Each request-scoped database connection is committed on success, rolled back on error, and closed afterwards.

Do not point a public preview at a database containing private business data.

## 4. Local development

The default remains local SQLite:

```bash
OUTCOME_DB_BACKEND=sqlite
uvicorn app.main:app --reload
```

To test the Turso adapter locally, install the pinned requirements and set the same three Turso environment variables used by Vercel. Do not use production credentials for local testing if a separate development database is available.

## 5. Verification before demo-ready

Run the full test suite:

```bash
pytest
```

Then, against a disposable Turso database, run the live driver and end-to-end checks:

```bash
OUTCOME_DB_BACKEND=turso pytest tests/test_turso_live.py -q
```

The live tests initialize the schema, check foreign-key enforcement, exercise the commitment/evidence/payment/action lifecycle, and remove their synthetic test record. They are skipped when Turso credentials are not present.

Then, against a Vercel Preview deployment, verify:

1. `GET /api/health` succeeds, returns `database_backend: turso`, and confirms a database query.
2. Create a commitment and reload it in a separate request.
3. Record quantity and invoice evidence; confirm the assessment persists.
4. Review evidence and confirm review history persists.
5. Configure a payment policy and evaluate a proposal; confirm the decision and evaluation basis persist.
6. Propose an action, approve it, report execution and verify its outcome with verified evidence.
7. Restart/redeploy the preview and confirm all records remain.
8. Confirm the demo reset endpoint remains disabled.

A local unit test of the adapter is not a substitute for this live Turso/Vercel integration test.

## Security and scope

Personal workspaces require a NEAR testnet wallet signature. A wallet address sent in a request body is not identity. Guided demos are separate short-lived copies. Keep `OUTCOME_ALLOW_DEMO_RESET=false` in hosted environments. Use only synthetic data. Do not enter real supplier, invoice, bank, or customer information. The hosted app blocks anonymous writes to the old shared rows.

OUTCOME does not transfer funds or execute external supplier actions. The optional NEAR AI integration remains advisory and does not alter payment policy or approvals.
