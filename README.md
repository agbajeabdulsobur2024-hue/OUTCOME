# OUTCOME

An operations intelligence agent for commitments, evidence and follow-through.

The first workflow protects supplier payments by comparing what was agreed with what the evidence shows. It now includes versioned amendments, append-only evidence reviews, owner-defined payment policies and a simulated proposal workflow. The latest applicable evidence must be marked verified before a proposal can pass; a passing proposal still requires human approval.

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

## Payment policy simulation

Configure a disabled-by-default policy per commitment with a spending cap, exact approved destination, full-delivery requirement, invoice-match requirement and verified-evidence requirement. Evidence reviews are append-only; a newer unreviewed or rejected record supersedes an older verified record. Submit a proposed payment for evaluation. OUTCOME returns explicit block reasons or `AWAITING_HUMAN_APPROVAL`. It never approves, signs, submits or executes a transfer. See [payment policy simulation](docs/PAYMENT_POLICY_SIMULATION.md).

## Scope

Supplier commitment state, evidence capture, discrepancy detection, risk explanation, policy-bound payment proposal evaluation and a usable interface. Human approval remains required.

## Tests

```bash
pytest
```

MIT License.
