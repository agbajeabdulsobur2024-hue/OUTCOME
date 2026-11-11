# OUTCOME

Know what was agreed. Verify what happened. Control what happens next.

OUTCOME is general-purpose financial accountability. A person records a commitment, the evidence of what happened, and the decision that follows. The same lifecycle covers supplier and procurement orders, contractor and service agreements, repayments, reimbursements, milestones, expenses, escrow-style release conditions, and other obligations. One scenario does not get its own workflow.

Actions still respect the workspace, the evidence rules, and a person’s authorisation. OUTCOME does not move money. An approval is recorded as approved and not submitted unless a real transfer exists, and this build does not submit one.

- Repeated signals appear only when at least two verified records for the same other party show the same kind of gap. Small samples are labelled early signals.
- OUTCOME does not infer fraud or intent.
- Follow-through (approval, execution report, and evidence-backed outcome) is recorded. OUTCOME does not perform the external action.
- The latest applicable evidence must be verified before a proposal can pass. A newer unreviewed record does not clear a verified shortfall. A passing proposal still needs a person.

## Demo

The Acme printer-paper orders are a synthetic demonstration, not the product boundary.

A business agrees to buy 50 cartons of printer paper for USD 250. Verified evidence shows 42 received, so the estimated exposure is USD 40. OUTCOME explains the shortfall, recommends holding the release, and will not treat that evidence as proof the order was fulfilled. The USD 250 proposal cannot be approved. A second order from the same supplier shows the same kind of gap, so the repeated signal appears. No money moves.

The hosted app at [outcome-puce.vercel.app](https://outcome-puce.vercel.app) serves version 0.4.0 until a later app deploy. The storage-bounded contract upgrade is already live on testnet. Use synthetic data only.

## NEAR

### Testnet audit anchor

Completed on testnet account `thewordweaver.testnet`. The contract stores hash-only events in an append-only hash chain. The app does not hold a full-access key, and a full-access key was not put in Vercel.

- Deploy and init, FINAL: [`8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy`](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy).
- `record_event` accepted and stored, FINAL: [`3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj`](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj). Stored id `OUTCOME-DEMO-PO-104`. That owner event survived the upgrade. No tester signature has been written on chain.
- Upgrade, FINAL: [`FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc`](https://testnet.nearblocks.io/txns/FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc). Code hash `5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr`. `get_anchor_policy` returns `any_account`. The owner is still `thewordweaver.testnet`.
- Restricted key limited to `record_event`. `admin_probe` rejected with `MethodNameMismatch`: [`7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk`](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk). That rejection is not indexed, which is expected on this nearcore version.

The app offers a signature only when a live `get_anchor_policy` view returns `any_account`. If that view cannot be read, the signature is not offered. Connecting a wallet to sign writes an audit record only and does not authorise a payment. A visitor can connect a testnet wallet for a personal workspace. That connection does not authorise a payment either. Setup: [near/README.md](near/README.md). The upgrade review is [CONTRACT_UPGRADE_REVIEW.md](CONTRACT_UPGRADE_REVIEW.md). Full status: [NEAR hackathon integration](docs/NEAR_HACKATHON_INTEGRATION.md).

### NEAR AI Cloud

When a server-side `NEARAI_API_KEY` is set, a person can ask for a brief. The model does not change commitment status, payment eligibility, or approvals. A brief is stored only after the cryptographic checks pass. If those checks fail, nothing is shown as verified.

A live call on 11 November 2026 to [`cloud-api.near.ai/v1`](https://cloud-api.near.ai/v1) for `Qwen/Qwen3.6-35B-A3B-FP8` succeeded. Provider signature (`signature_kind` `provider_tee`, ECDSA/EIP-191) and Intel TDX attestation verified. Fail-closed held when evidence was tampered or missing. Pull request 6, merged as `fa6eb02`, returns that brief when verification passes. On 11 November 2026, `GET https://outcome-puce.vercel.app/api/ai/status` reported `configured: true`. This repository does not contain that key. A brief is still withheld unless the cryptographic checks pass. NVIDIA JWT signatures, Sigstore, and TLS binding of the completion connection are not in OUTCOME. The checks do not prove which machine served the model, that the connection was bound to the quote, or that a brief cannot be replayed. Details: [AI integration](docs/AI_INTEGRATION.md).

The same key and the same checks support ordinary-language intake in this branch. A person reviews the extracted fields and confirms before a commitment or evidence row is saved. The deterministic assessment then runs. Intake is not deployed with this branch. The manual forms work without the key.

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
3. A first visit is the landing. **Get started** is the primary action and walks through the labelled sample. No wallet is required. **Revisit the sample** opens that sample again without the introduction. **Revisit the guide** plays it again. A completed or dismissed introduction does not open on its own.

The guided demo gives that visitor a private synthetic copy: two Acme orders, verified short deliveries, a configured payment policy, a blocked proposal, a follow-up action, and a repeated short-delivery signal. It does not overwrite another visitor’s data. The older shared seed still refuses to run when that shared area already has commitments.

Walkthrough: [3-minute demo script](docs/DEMO_SCRIPT.md).

To enable NEAR AI Cloud briefs, copy `.env.example`, export `NEARAI_API_KEY` in the server environment, and restart. The app does not auto-load `.env` files.

Hosted setup: [Vercel + Turso deployment](docs/VERCEL_TURSO_DEPLOYMENT.md).

## Safety boundary

This is a hackathon demo, not a production financial service.

- A personal workspace requires a signed NEAR testnet login. A wallet address in a request body is not identity. Actor and reviewer names inside a record are still labels.
- Guided demos are separate short-lived copies. One visitor cannot read or change another visitor’s records by guessing an id.
- Do not enter sensitive live business or payment data.
- No custody of funds, and no real transfers. Human approval remains required.
- A simulated payment proposal returns explicit block reasons or `AWAITING_HUMAN_APPROVAL`. [Payment policy simulation](docs/PAYMENT_POLICY_SIMULATION.md).
- `OUTCOME_ALLOW_DEMO_RESET` stays false on the hosted app. Even if it is turned on locally, reset deletes only the old shared rows, not personal or guided-demo workspaces.
- Wallet connect is testnet only. The app does not hold a private key. NEAR Intents is not connected. It is an experimental, mainnet-only beta with no testnet and real-asset risk. [Overview](https://docs.agentsonintents.com/integration/agent-api/overview). [Quickstart](https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction).

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
