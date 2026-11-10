# OUTCOME

An operations intelligence agent for commitments, evidence and follow-through.

The first workflow protects supplier payments by comparing what was agreed with what the evidence shows.

## First demo

A business agrees to receive 100 units for USD 500, payable after delivery. Receiving evidence records 80 units. OUTCOME flags the 20-unit shortfall, estimates USD 100 exposure and recommends holding payment approval until the discrepancy is resolved.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API docs: /docs.

## Product principles

- Understand before acting.
- Keep a durable record of commitments.
- Notice material changes, not noise.
- Explain evidence, uncertainty and impact.
- Keep consequential actions within explicit human permission.
- Verify outcomes, not just attempted actions.

## Safety boundary

This MVP is decision support. It does not custody funds, approve invoices or submit blockchain transactions. The NEAR Intents Agent API is currently documented as an experimental, mainnet-only beta; its quickstart warns that real assets are at risk and no testnet is available. We will not connect a live wallet or move real funds in this demo. See the [official overview](https://docs.agentsonintents.com/integration/agent-api/overview) and [quickstart](https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction).

## Scope

Supplier commitment state, evidence capture, discrepancy detection, risk explanation and a usable interface. Human review remains required.

## Tests

```bash
pytest
```

MIT License.
