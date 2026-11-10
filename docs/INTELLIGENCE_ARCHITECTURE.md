# OUTCOME — Intelligence Architecture & Capability Contracts

## Product boundary
OUTCOME is a commitment-centred operations intelligence system. The first vertical slice protects supplier payments by comparing agreed terms with observed evidence. It is decision support: it does not approve invoices, custody funds or submit payments.

## Core operating loop
1. **UNDERSTAND** — establish the business context, objectives, constraints and relevant parties.
2. **ESTABLISH** — create a durable commitment with expected deliverables, value, dates and conditions.
3. **MONITOR** — maintain the commitment state as evidence and time evolve.
4. **DETECT** — identify material changes, missing evidence, contradictions and recurring patterns.
5. **VALIDATE** — check source, recency, completeness and consistency of evidence.
6. **ASSESS** — determine status, urgency, potential exposure, uncertainty and business impact.
7. **DECIDE** — recommend a proportionate next step and explain why.
8. **ACT** — execute only actions explicitly permitted by the owner; require approval for consequential actions.
9. **VERIFY** — confirm the intended outcome actually occurred.
10. **LEARN** — attribute outcomes to evidence and decisions; improve future assessments without silently rewriting history.

## Shared Commitment State
Every commitment receives a stable, immutable ID. All evidence, assessments, decisions, approvals, actions and outcomes must reference it. Never join records merely because supplier names, amounts or descriptions look similar.

Minimum state: identity; parties; agreed deliverables; quantities/units; agreed value/currency; due date; payment conditions; current lifecycle status; evidence references; assessment history; decision/approval history; action history; outcome.

## Six intelligence systems and contracts

### 1. Business Intelligence
**Purpose:** understand the operating context in which a commitment matters.
**Inputs:** owner goals, business constraints, supplier/customer relationships, cash-flow sensitivity and operating policies.
**Outputs:** relevant context and explicit constraints attached to the decision.
**Contract:** do not invent business preferences; distinguish supplied facts from inferred context; surface missing context when it changes the recommendation.

### 2. Commitment Intelligence
**Purpose:** convert an agreement into explicit, monitorable expectations.
**Inputs:** order, contract, message or owner-entered terms.
**Outputs:** canonical commitment, conditions, milestones, dates, quantities, value and responsible parties.
**Contract:** preserve original terms and provenance; ambiguous or conflicting terms remain unresolved until confirmed; amendments are versioned, never silently overwritten.

### 3. Evidence Intelligence
**Purpose:** connect observations to the commitment and assess their reliability.
**Inputs:** receiving records, invoices, delivery notes, receipts, messages and manual observations.
**Outputs:** append-only evidence records with type, value, source, timestamp and review state.
**Contract:** preserve source and timestamp; distinguish observed, reported and verified evidence; missing evidence is not proof of failure or success; corrections create a new record or explicit retraction.

### 4. Pattern Recognition Intelligence
**Purpose:** detect meaningful anomalies and recurring operational patterns.
**Inputs:** validated evidence and historical commitment outcomes.
**Outputs:** candidate patterns with supporting records, confidence and time window.
**Contract:** patterns are hypotheses until validated; explain the evidence; avoid treating one incident as a trend; do not infer intent or misconduct from anomalies alone.

### 5. Decision Intelligence
**Purpose:** assess impact and recommend the next proportionate action.
**Inputs:** commitment state, business context, evidence quality, deadlines and policy.
**Outputs:** status, issues, uncertainty, exposure estimate, rationale and ordered next actions.
**Contract:** separate facts from inference; expose assumptions; do not turn missing evidence into an all-clear; keep human review for material financial decisions; never claim a payment is safe solely because no discrepancy was detected.

### 6. Action & Outcome Intelligence
**Purpose:** coordinate permitted follow-through and verify results.
**Inputs:** approved recommendation, owner permissions, action policy and current commitment state.
**Outputs:** proposed/approved/executed action record, execution result and verified outcome.
**Contract:** deny actions outside explicit permission; make retries idempotent; record who/what authorized an action; distinguish attempted, executed and verified; provide an emergency stop for future integrations.

## Assessment semantics
Statuses describe the current evidence state, not a guarantee:
- `AWAITING_EVIDENCE`: essential evidence has not yet been recorded.
- `ON_TRACK`: available evidence currently matches the recorded expectations; this is not payment authorization.
- `REVIEW_REQUIRED`: evidence or terms require human interpretation.
- `DISCREPANCY`: evidence conflicts materially with the commitment.
- `OVERDUE`: a due date has passed without verified completion; additional discrepancy flags may coexist.

Issues are independent flags. Do not suppress an overdue issue because another discrepancy exists. Exposure estimates must state their basis and must not be presented as a payable amount. If multiple estimates may overlap, do not add them as if independent.

## First vertical-slice capability contract
For a supplier commitment, OUTCOME must:
1. Persist a stable commitment ID and the original agreed terms.
2. Append evidence without overwriting prior observations.
3. Compare the latest applicable evidence with the commitment.
4. Flag quantity shortfall, over-delivery, invoice variance, missing evidence and overdue state independently.
5. Explain each issue with expected value, observed value and source where available.
6. Estimate potential exposure only where the calculation is defensible and label it as an estimate.
7. Recommend a safe next action; never approve or execute payment.
8. Reassess after each new evidence record and retain the assessment basis.

## Verification scenarios
- 100 units agreed; 80 received: shortfall, estimated exposure and hold/reconcile recommendation.
- 100 agreed; 100 received: no quantity discrepancy, but invoice/payment conditions still need checking.
- 100 agreed; no receiving evidence: `AWAITING_EVIDENCE`, never an all-clear.
- Invoice exceeds agreement: independent high-severity issue.
- Due date passes with no delivery: overdue issue and missing-evidence state.
- Due date passes with a short delivery: both overdue and quantity-shortfall issues remain visible.
- New receiving evidence supersedes the current assessment but does not erase earlier evidence.
- No action may approve or transfer funds in this MVP.

## Implementation sequence
1. Make assessment semantics safe and explicit.
2. Persist assessment snapshots and evidence provenance.
3. Add commitment amendments/versioning and evidence review states.
4. Add policy-bound action proposals (simulation only).
5. Add outcome verification and audit trail.
6. Add pattern recognition only after sufficient reliable history exists.

## Non-goals for this hackathon slice
No autonomous payment execution, custody, credit scoring, supplier fraud accusations, broad ERP integrations or generic agent marketplace. NEAR integration must remain simulated unless a safe, explicitly approved test environment and policy boundary are verified.
