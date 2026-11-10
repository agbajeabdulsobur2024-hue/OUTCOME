# NEAR hackathon integration status

Last reviewed: 10 November 2026

This document separates working integrations from planned work and ecosystem technologies that are intentionally not being used. Track starter prompts are guidance, not a requirement to integrate every named NEAR technology.

## Current implementation

| Capability | Status | What is true today |
|---|---|---|
| NEAR AI Cloud inference | Implemented in code; live use unverified | The backend can call the OpenAI-compatible NEAR AI Cloud chat-completions endpoint when `NEARAI_API_KEY` is configured. The user explicitly triggers a brief. No key is configured in the repository environment, so no live request has been confirmed. |
| TEE / response verification | Not implemented | The provider lists the selected model as TEE-hosted, but OUTCOME does not yet verify the hardware attestation, response signature or their binding for an individual brief. The UI now says so explicitly. |
| NEAR account / wallet connection | Not implemented | No owner wallet is connected and the app holds no NEAR signing key. |
| Function-call access-key enforcement | Not implemented | Current payment rules are deterministic application-level checks. They are not enforced by a NEAR account key or smart contract. |
| NEAR Intents Agent API | Intentionally not connected | The Agent API is an experimental, mainnet-only beta with real-asset risk and no testnet. It requires owner signatures and a funded agent. OUTCOME will not connect or fund it for a synthetic hackathon demo. |
| Chain Signatures | Not integrated | The available Chain Signatures documentation describes the technology as alpha and testnet-only. It is not needed for the current supplier-payment protection workflow. |
| On-chain audit / execution ledger | Not implemented | Commitment history, policy snapshots, payment proposals and action events are persisted in the app database, not on NEAR. |
| Adversarial payment defense demo | Implemented at application layer | The UI can submit a simulated prompt-injected payment request that exceeds the owner cap and changes the destination. The policy evaluator blocks it and records the reasons. No signing or transfer occurs. |

## What the security demo proves — and does not prove

The adversarial test demonstrates deterministic rejection of a hostile payment proposal when it violates the owner-defined spending limit and exact destination. The same evaluator also checks currency, commitment value, delivery evidence, invoice matching and partial-payment permission.

It does **not** prove that a NEAR wallet, function-call access key, TEE, or on-chain contract would reject a transaction. No private key is available to the app, and the app cannot submit a transaction.

## Recommended NEAR scope for this submission

1. Keep NEAR AI Cloud as the live ecosystem integration, once an API key is configured and an actual request can be demonstrated.
2. Prioritize cryptographic verification of NEAR AI Cloud model attestation and response signatures before describing a brief as verified confidential inference. Use the official verifier implementation as the reference; do not replace it with a status flag or a self-asserted claim.
3. Keep payment authorization fail-closed and demonstrate the hostile-request block.
4. Do not add NEAR Intents Agent API or real-fund movement to the demo. The official quickstart states that it is mainnet-only beta, with no testnet and real asset risk.
5. Consider a testnet on-chain audit anchor only if it can be implemented, deployed and independently verified before submission. Do not claim an on-chain ledger while it remains local database history.

## Verification references

- NEAR AI Cloud API: https://cloud.near.ai/
- NEAR AI Cloud verification guide: https://docs.near.ai/cloud/verification/
- Official verifier: https://github.com/nearai/nearai-cloud-verifier
- NEAR Intents Agent API overview: https://docs.agentsonintents.com/integration/agent-api/overview
- NEAR Intents Agent API quickstart and risk warning: https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction
- NEAR Chain Signatures overview: https://near-horizon.notion.site/Chain-Signatures-4e919cf57f93472f9a6e89bf29581a02
