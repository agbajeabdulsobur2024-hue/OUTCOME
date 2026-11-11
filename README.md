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

Completed on testnet account `thewordweaver.testnet`. The contract stores hash-only events in an append-only hash chain. The web app does not connect a wallet or hold a full-access key, and a full-access key was not put in Vercel.

- Deploy and init, FINAL: [`8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy`](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy). On-chain `code_hash` `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`.
- `record_event` accepted and stored, FINAL: [`3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj`](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj).
- Restricted key limited to `record_event`. `admin_probe` rejected with `MethodNameMismatch`: [`7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk`](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk). That rejection is not indexed, which is expected on this nearcore version.

Explorer: [testnet.nearblocks.io/txns](https://testnet.nearblocks.io/txns/). Setup: [near/README.md](near/README.md).

### NEAR AI Cloud

Optional, user-triggered briefs explain the deterministic assessment. They do not change commitment status, payment eligibility, or approvals, and they do not execute actions.

- Live call on 11 November 2026 to [`cloud-api.near.ai/v1`](https://cloud-api.near.ai/v1) for `Qwen/Qwen3.6-35B-A3B-FP8` succeeded. Provider signature (`signature_kind` `provider_tee`, ECDSA/EIP-191) and Intel TDX attestation verified. Fail-closed held when evidence was tampered or missing.
- The hosted Vercel demo does not serve those briefs. On current main, verification can pass while the brief still fails to display, because the model can spend the token budget on reasoning. Display fix: unmerged [PR #6](https://github.com/agbajeabdulsobur2024-hue/OUTCOME/pull/6).
- NVIDIA JWT signatures, Sigstore, and TLS binding of the completion connection are not yet in OUTCOME. Boundaries: [AI integration](docs/AI_INTEGRATION.md).

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
