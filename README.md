# OUTCOME

An operations intelligence app for commitments, evidence, and follow-through.

The first workflow protects supplier payments by comparing what was agreed with what the evidence shows.

- Repeated supplier patterns are shown only when at least two latest evidence records are verified. Small samples are labelled early signals.
- OUTCOME does not infer fraud or intent.
- Follow-through (approval, execution report, and evidence-backed outcome) is recorded. OUTCOME does not perform the external action.
- The latest applicable evidence must be verified before a proposal can pass. A passing proposal still needs human approval.

## Demo

A business agrees to receive 100 units for USD 500, payable after delivery. Receiving evidence shows 80 units.

OUTCOME flags the 20-unit shortfall, estimates USD 100 exposure, and recommends holding payment approval until the discrepancy is resolved.

Live demo: [outcome-puce.vercel.app](https://outcome-puce.vercel.app)

Use synthetic data only. The hosted app has no login.

## NEAR

### Testnet audit anchor

The Rust contract and testnet script are under [`near/`](near/).

The contract stores hash-only events in an append-only hash chain with block metadata. The testnet script is written to add a function-call key that can call only `record_event` on that contract.

- The contract and the testnet proof script are implemented. The WASM build has passed GitHub Actions.
- A local sandbox script exercises the same method restriction. That chain is not testnet.
- No testnet contract has been deployed. The testnet script has not been run, so there is no testnet contract address or transaction hash.

The web app does not connect a wallet or hold the key. Setup: [near/README.md](near/README.md). Full status: [NEAR hackathon integration](docs/NEAR_HACKATHON_INTEGRATION.md).

### NEAR AI Cloud

When enabled, a brief explains the deterministic assessment, uncertainty, and questions for the owner. The user triggers it with **Generate AI brief**. The model does not change commitment status, payment eligibility, or approvals, and it does not execute actions.

The adapter runs only when `NEARAI_API_KEY` is set on the server. A live completion and response signature have not been confirmed in this repository. Data boundaries and checks: [AI integration](docs/AI_INTEGRATION.md).

NEAR Intents and Chain Signatures are not connected. See the safety boundary and the [integration status](docs/NEAR_HACKATHON_INTEGRATION.md).

## Run locally

Requires Python 3.11+. Local development uses SQLite.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

1. Open http://127.0.0.1:8000.
2. Open API docs at http://127.0.0.1:8000/docs.
3. Click **Load synthetic demo case**.

The seed creates two fictional Acme commitments with verified fixture evidence, a blocked payment proposal, an action to reconcile delivery, and a repeated-short-delivery early signal. It refuses to run if commitments already exist.

Walkthrough: [3-minute demo script](docs/DEMO_SCRIPT.md).

To enable NEAR AI Cloud briefs, copy `.env.example`, export `NEARAI_API_KEY` in the server environment, and restart. The app does not auto-load `.env` files.

Hosted setup: [Vercel + Turso deployment](docs/VERCEL_TURSO_DEPLOYMENT.md).

## Safety boundary

This is a hackathon demo, not a production financial service.

- No user authentication or tenant isolation. Actor and reviewer names are manually entered labels, not authenticated identities or signatures.
- Do not enter sensitive live business or payment data.
- No custody of funds, and no real transfers. Human approval remains required.
- OUTCOME does not approve invoices or submit blockchain transactions. A simulated payment proposal returns explicit block reasons or `AWAITING_HUMAN_APPROVAL`. [Payment policy simulation](docs/PAYMENT_POLICY_SIMULATION.md).
- The demo reset endpoint is disabled unless `OUTCOME_ALLOW_DEMO_RESET=true` is set in a disposable local environment.
- This demo does not connect a live wallet or move real funds. The NEAR Intents Agent API is an experimental, mainnet-only beta. Its quickstart warns that real assets are at risk and that no testnet is available. [Overview](https://docs.agentsonintents.com/integration/agent-api/overview). [Quickstart](https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction).

## Product principles

- Understand before acting.
- Keep a durable record of commitments.
- Notice material changes, not noise.
- Explain evidence, uncertainty and impact.
- Keep consequential actions within explicit human permission.
- Verify outcomes, not just attempted actions.

## Tests

```bash
pytest
```

## License

MIT License.
