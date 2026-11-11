# OUTCOME — Payment Policy & Proposal Simulation

## Purpose
Show how a recorded commitment — a supplier order or any other obligation — is checked before a person decides. This does not custody funds, sign, or execute a transfer.

## Owner-defined policy
Each commitment has one current policy plus an append-only history of every policy update:
- Disabled by default.
- Maximum amount cannot exceed the recorded commitment value.
- Partial payment proposals are blocked unless the owner explicitly enables them.
- Exact destination match is required.
- Full delivery evidence is required by default.
- Latest applicable delivery and invoice evidence must have a `VERIFIED` review status by default. A newer unreviewed or rejected record invalidates reliance on an older verified record.
- Invoice amount must exactly match the recorded agreement by default.

## Proposal lifecycle
1. Owner configures the policy for a commitment.
2. A payment proposal is submitted with amount, currency, and destination.
3. OUTCOME evaluates the proposal against the policy and latest applicable evidence.
4. Any failed check produces `BLOCKED` with explicit reason codes.
5. Every policy change is retained as a timestamped snapshot.
6. The proposal stores an immutable evaluation basis: commitment version and terms, policy snapshot, and latest evidence IDs/review states used.
7. If every check passes, status is `AWAITING_HUMAN_APPROVAL`.
8. No endpoint approves, signs, submits, or executes a transfer.

A passing result means only that the proposal is eligible for human review. It is not a payment authorization and is not evidence that a transfer is safe.

## Action and outcome tracking

Action records are linked to the same commitment ID. The lifecycle is `PROPOSED` → `APPROVED` → `EXECUTION_REPORTED` → `OUTCOME_VERIFIED` or `OUTCOME_FAILED`, with `REJECTED` available from `PROPOSED`. Outcome verification requires evidence belonging to the same commitment and already marked `VERIFIED`. The API records human-reported events only; it does not execute the external action.

## API
- `GET /api/commitments/{id}/payment-policy`
- `PUT /api/commitments/{id}/payment-policy`
- `POST /api/commitments/{id}/payment-proposals`
- `GET /api/commitments/{id}/payment-proposals`

## Safety boundary
- A proposal is a recommendation. `AWAITING_HUMAN_APPROVAL` means a person may decide. `APPROVED_NOT_SUBMITTED` means a person approved and no transfer was submitted. `BLOCKED` cannot be approved. None of these is a completed payment.
- `transfer_executed` stays false and `transaction_hash` stays null. There is no submit or execute call.
- No wallet private key, seed phrase, custody or blockchain signing is used for payment.
- Sessions and workspaces isolate testers. Evidence reviews are append-only. Reviewer names are still labels, not signatures.
- This is not a production payment service.

## Payment options that are not implemented

These are boundaries, not integrations:

1. The in-app proposal and human decision above. This is implemented. It does not move funds.
2. A tester-signed hash-only audit record on NEAR testnet. The storage-backed upgrade is live and `get_anchor_policy` returns `any_account`. The app offers that signature only when a live view confirms it. No tester signature has been written on chain yet. The signature is not a payment.
3. NEAR Intents. The official Agent API is mainnet-only and is not connected. There is no Intents testnet sandbox in this app.

## Known limitations
- Destination is a manually entered exact-match identifier, not a cryptographically verified wallet ownership proof.
- Evidence is manually entered; source labels are provenance metadata, not proof of authenticity. Review records are append-only, and reviewer names are labels rather than signatures.
- When a review status is present, only the latest verified quantity or invoice changes the assessment. A newer unreviewed record does not clear a verified shortfall. Payment policy still refuses to pass while the latest applicable evidence is unverified.
- The proposal engine does not submit transactions and must not be described as a live NEAR integration.
- There is no settlement ledger or cumulative paid-to-date accounting. Multiple proposals are independent simulations, not a record of funds already paid. A real integration must reconcile confirmed on-chain settlement before allowing subsequent payments.
