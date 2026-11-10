# OUTCOME

An operations intelligence agent for commitments, evidence and follow-through.

The first workflow protects supplier payments by comparing what was agreed with what the evidence shows. OUTCOME also surfaces repeated supplier patterns only when at least two latest evidence records are verified; it labels small samples as early signals and never infers fraud or intent. Follow-through actions have an approval, execution-report and evidence-backed outcome lifecycle; OUTCOME records these steps but does not perform external actions. It now includes versioned amendments, append-only evidence reviews, owner-defined payment policies and a simulated proposal workflow. The latest applicable evidence must be marked verified before a proposal can pass; a passing proposal still requires human approval.

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

Open http://127.0.0.1:8000. API docs: /docs. Click **Load synthetic demo case** to create two fictional Acme commitments with verified fixture evidence, a blocked payment proposal, an action to reconcile delivery and a repeated-short-delivery early signal. The seed endpoint refuses to run if commitments already exist. See [the 3-minute demo script](docs/DEMO_SCRIPT.md) for the end-to-end walkthrough.

## Product principles

- Understand before acting.
- Keep a durable record of commitments.
- Notice material changes, not noise.
- Explain evidence, uncertainty and impact.
- Keep consequential actions within explicit human permission.
- Verify outcomes, not just attempted actions.

## Safety boundary

This MVP is decision support. It does not custody funds, approve invoices or submit blockchain transactions. The NEAR Intents Agent API is currently documented as an experimental, mainnet-only beta; its quickstart warns that real assets are at risk and no testnet is available. We will not connect a live wallet or move real funds in this demo. See the [official overview](https://docs.agentsonintents.com/integration/agent-api/overview) and [quickstart](https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction).

## Action and outcome tracking

Propose an action against a commitment, record owner approval or rejection, report what was done, and verify the result against evidence already marked `VERIFIED`. Invalid state transitions are rejected. This tracks human follow-through; it does not execute email, calls, supplier contact or payments.

## Payment policy simulation

Configure a disabled-by-default policy per commitment with a spending cap, exact approved destination, full-delivery requirement, invoice-match requirement, verified-evidence requirement and an explicit partial-payment permission (off by default). Evidence reviews are append-only; a newer unreviewed or rejected record supersedes an older verified record. Submit a proposed payment for evaluation. OUTCOME returns explicit block reasons or `AWAITING_HUMAN_APPROVAL`. It never approves, signs, submits or executes a transfer. See [payment policy simulation](docs/PAYMENT_POLICY_SIMULATION.md).

## Scope

Supplier commitment state, evidence capture, discrepancy detection, risk explanation, policy-bound payment proposal evaluation and a usable interface. Human approval remains required.

## Tests

```bash
pytest
```

MIT License.
