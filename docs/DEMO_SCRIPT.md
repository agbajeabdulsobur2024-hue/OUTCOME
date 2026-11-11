# OUTCOME — 3-Minute Demo Script

This walkthrough is the synthetic Acme supplier trial. It demonstrates the product. It is not the only commitment OUTCOME can record. A person can open **New commitment** and enter a different obligation, such as a milestone or a repayment, and the same checks run.

## Setup
Run the FastAPI app and open the local UI. A first visit opens the guide on the landing page. **Get started** enters that visitor’s labelled sample. No wallet is required. **Revisit the sample** opens the same sample later without restarting the guide, and **Revisit the guide** plays the introduction again. Each visitor gets a separate synthetic copy, so one person’s changes do not alter the next person’s sample or anyone’s personal workspace. The old shared seed still refuses to overwrite commitments that are already in that shared area.

## 1. Show the intelligence (30 seconds)
The dashboard shows two Acme Supplies commitments. Both have verified short-delivery evidence:
- 50 cartons of printer paper agreed; 42 recorded as received.
- A second Acme order, 20 cartons agreed and 16 recorded as received.

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
3. Add new receiving evidence for 50 cartons.
4. Mark that new evidence verified.
5. Verify the action outcome using that same verified evidence.

The action lifecycle records approval, execution report and evidence-backed outcome verification. OUTCOME does not send the email or make the phone call.

## 4. Demonstrate the security boundary (30 seconds)
In the payment protection panel, run **Run adversarial payment test**. This simulates a prompt-injected agent trying to send more than the owner-defined cap to `attacker-controlled.near`. OUTCOME records the proposal as BLOCKED with spending-limit and destination violations. No payment transaction is attempted.

This is an application-level policy gate. It is not a transfer.

## 5. Reassess payment (30 seconds)
Submit the payment proposal again. With the latest delivery evidence verified, invoice evidence verified, exact destination matched and policy cap respected, the proposal becomes AWAITING_HUMAN_APPROVAL. Record the human approval. OUTCOME stores that decision and does not submit a transfer: the official NEAR Intents Agent API is mainnet-only, and hackathon rule Q-007 says no Intents testnet exists.

Emphasize the boundary: approval is not a payment, and no money moves.

## 5b. Optional testnet audit (if the tester has a wallet)
Connect a NEAR testnet wallet from the landing page and sign the audit record on the order. The wallet submits `record_event` to `thewordweaver.testnet`. OUTCOME shows the explorer link only after it has checked that the final transaction matches this order’s digest. The contract currently deployed still accepts that call only from `thewordweaver.testnet` until its owner upgrades the contract in this repository. A tester without a wallet can skip this step.

## 6. Close (15 seconds)
OUTCOME connects what was agreed, what happened, what evidence supports it, what action was taken and whether the intended result was verified. The shared commitment record keeps that history together.
