# OUTCOME — 3-Minute Demo Script

## Setup
Run the FastAPI app and open the local UI. Start with an empty workspace, then click **Load synthetic demo case**. The seed is fictional, explicitly labelled, and refuses to overwrite existing commitments.

## 1. Show the intelligence (30 seconds)
The dashboard shows two Acme Supplies commitments. Both have verified short-delivery evidence:
- 100 boxes agreed; 80 recorded as received.
- 50 boxes agreed; 40 recorded as received.

The pattern panel surfaces a repeated short-delivery **EARLY_SIGNAL**. Explain that this is a review signal, not an accusation or fraud finding.

## 2. Show commitment-level reasoning (45 seconds)
Open the first commitment. Show:
- The stable commitment record and original version.
- The shortfall and estimated exposure.
- The invoice evidence and its review state.
- The next action recommendation.
- The payment proposal history showing BLOCKED because verified delivery is short.

Explain that a recorded invoice alone does not satisfy the delivery condition.

## 3. Show controlled follow-through (60 seconds)
In the action panel:
1. Approve the proposed reconciliation action.
2. Report that the supplier was contacted.
3. Add new receiving evidence for 100 boxes.
4. Mark that new evidence verified.
5. Verify the action outcome using that same verified evidence.

The action lifecycle records approval, execution report and evidence-backed outcome verification. OUTCOME does not send the email or make the phone call.

## 4. Demonstrate the security boundary (30 seconds)
In the payment protection panel, run **Run adversarial payment test**. This simulates a prompt-injected agent trying to send more than the owner-defined cap to `attacker-controlled.near`. OUTCOME records the proposal as BLOCKED with spending-limit and destination violations. No signing key is available to the application and no transaction is attempted.

This is an application-level policy-gate demonstration, not a live NEAR wallet or function-call-key enforcement demo.

## 5. Reassess payment (30 seconds)
Submit the payment proposal again. With the latest delivery evidence verified, invoice evidence verified, exact destination matched and policy cap respected, the proposal becomes AWAITING_HUMAN_APPROVAL.

Emphasize the boundary: this is not payment approval, no wallet is connected, no transaction is submitted and no money moves.

## 6. Close (15 seconds)
OUTCOME connects what was agreed, what happened, what evidence supports it, what action was taken and whether the intended result was verified. The shared commitment record keeps that history together.
