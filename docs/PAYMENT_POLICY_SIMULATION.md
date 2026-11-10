# OUTCOME — Payment Policy & Proposal Simulation

## Purpose
Demonstrate how OUTCOME's commitment intelligence governs a proposed supplier payment without custody, signing, or transfer execution.

## Owner-defined policy
Each commitment can have one policy:
- Disabled by default.
- Maximum amount cannot exceed the recorded commitment value.
- Exact destination match is required.
- Full delivery evidence is required by default.
- Latest applicable delivery and invoice evidence must have a `VERIFIED` review status by default. A newer unreviewed or rejected record invalidates reliance on an older verified record.
- Invoice amount must exactly match the recorded agreement by default.

## Proposal lifecycle
1. Owner configures the policy for a commitment.
2. A payment proposal is submitted with amount, currency, and destination.
3. OUTCOME evaluates the proposal against the policy and latest applicable evidence.
4. Any failed check produces `BLOCKED` with explicit reason codes.
5. If every check passes, status is `AWAITING_HUMAN_APPROVAL`.
6. No endpoint approves, signs, submits, or executes a transfer.

A passing result means only that the proposal is eligible for human review. It is not a payment authorization and is not evidence that a transfer is safe.

## Action and outcome tracking

Action records are linked to the same commitment ID. The lifecycle is `PROPOSED` → `APPROVED` → `EXECUTION_REPORTED` → `OUTCOME_VERIFIED` or `OUTCOME_FAILED`, with `REJECTED` available from `PROPOSED`. Outcome verification requires evidence belonging to the same commitment and already marked `VERIFIED`. The API records human-reported events only; it does not execute the external action.

## API
- `GET /api/commitments/{id}/payment-policy`
- `PUT /api/commitments/{id}/payment-policy`
- `POST /api/commitments/{id}/payment-proposals`
- `GET /api/commitments/{id}/payment-proposals`

## Safety boundary
- Execution mode is always `SIMULATION_ONLY`.
- `transfer_executed` is always false.
- `transaction_hash` is always null.
- No wallet private key, seed phrase, custody or blockchain signing is used.
- Policy checks are not authentication. The current MVP has no user authentication or tenant isolation and must not be exposed as a production payment service.
- A production version must authenticate the owner, isolate business tenants, protect policy changes, require fresh evidence, record approval identity, use idempotency keys, and independently verify chain settlement before claiming an outcome.

## Known limitations
- Destination is a manually entered exact-match identifier, not a cryptographically verified wallet ownership proof.
- Evidence is manually entered; source labels are provenance metadata, not proof of authenticity. Review records are append-only, but reviewer names are not authenticated in this MVP.
- Latest evidence by type is used; evidence review/verification workflow is not yet implemented.
- The proposal engine does not submit transactions and must not be described as a live NEAR integration.
