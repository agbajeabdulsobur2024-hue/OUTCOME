# NEAR hackathon integration status

Last reviewed: 10 November 2026

This document separates working integrations from planned work and ecosystem technologies that are intentionally not being used. Track starter prompts are guidance, not a requirement to integrate every named NEAR technology.

## Current implementation

| Capability | Status | What is true today |
|---|---|---|
| NEAR AI Cloud inference | Implemented in code; live use unverified | The backend can call the OpenAI-compatible NEAR AI Cloud chat-completions endpoint when `NEARAI_API_KEY` is configured. The user explicitly triggers a brief. No key is configured in the repository environment, so no live request has been confirmed. |
| Response signature + TDX binding | Partial cryptographic checks implemented; live verification unverified | Before parsing or persisting a brief, OUTCOME checks exact request/response hashes, the ECDSA signature and signer, a fresh nonce, and Intel TDX quote/report-data binding using `dcap-qvl`. It fails closed. It does not yet verify model/container measurements, compose-manifest binding or Sigstore provenance. The NVIDIA NRAS verdict is supplemental; its JWT signature is not independently verified. No live API key/request has been tested yet. |
| NEAR account / wallet connection | Isolated testnet demo scaffold | A dedicated testnet script uses an owner key locally to create a scoped function-call key. The OUTCOME web app itself does not connect a wallet or hold the key. |
| Function-call access-key enforcement | Contract source and build workflow added; build status unverified; testnet proof pending | The Rust contract has `record_event` and a state-changing `admin_probe`. A GitHub Actions workflow is configured to compile the WASM and check the runner API surface, but no successful run has been confirmed. The script creates a NEAR key scoped to the contract and `record_event` only, submits a real audit event, then expects the protocol to reject `admin_probe`. This is not yet executed on testnet. |
| NEAR Intents Agent API | Intentionally not connected | The Agent API is an experimental, mainnet-only beta with real-asset risk and no testnet. It requires owner signatures and a funded agent. OUTCOME will not connect or fund it for a synthetic hackathon demo. |
| Chain Signatures | Not integrated | The available Chain Signatures documentation describes the technology as alpha and testnet-only. It is not needed for the current supplier-payment protection workflow. |
| On-chain audit anchor | Contract and testnet proof script implemented; deployment pending | A Rust contract stores hash-only events in an append-only hash chain with block metadata; the WASM build is configured for CI; no successful run has been confirmed. The testnet script is intended to append a synthetic or OUTCOME-exported digest using the restricted key. It has not been executed; no testnet contract address or transaction hash exists yet. |
| Adversarial payment defense demo | Implemented at application layer | The UI can submit a simulated prompt-injected payment request that exceeds the owner cap and changes the destination. The policy evaluator blocks it and records the reasons. No signing or transfer occurs. |

## What the security demo proves — and does not prove

The adversarial test demonstrates deterministic rejection of a hostile payment proposal when it violates the owner-defined spending limit and exact destination. The same evaluator also checks currency, commitment value, delivery evidence, invoice matching and partial-payment permission.

The function-call-key proof is prepared but not yet run on testnet. Until the testnet script demonstrates a successful `record_event` and a protocol-level method-scope rejection for `admin_probe`, we must not claim the on-chain restriction has been verified.

## Recommended NEAR scope for this submission

1. Keep NEAR AI Cloud as the live ecosystem integration, once an API key is configured and an actual request can be demonstrated.
2. Run a live NEAR AI request with an API key and confirm the ECDSA + TDX verification path against the selected Qwen model. Do not label any brief verified until this succeeds.
3. Keep payment authorization fail-closed and demonstrate the hostile-request block.
4. Do not add NEAR Intents Agent API or real-fund movement to the demo. The official quickstart states that it is mainnet-only beta, with no testnet and real asset risk.
5. Build and deploy `near/contract`, then run `near/scripts/testnet_demo.cjs`. Record the testnet contract address, accepted transaction hash and explicit protocol rejection evidence in the submission README. Do not call the testnet integration complete before these are captured.

## Verification references

- NEAR AI Cloud API: https://cloud.near.ai/
- NEAR AI Cloud verification guide: https://docs.near.ai/cloud/verification/
- Official verifier: https://github.com/nearai/nearai-cloud-verifier
- NEAR Intents Agent API overview: https://docs.agentsonintents.com/integration/agent-api/overview
- NEAR Intents Agent API quickstart and risk warning: https://docs.agentsonintents.com/integration/agent-api/quickstart/introduction
- NEAR Chain Signatures overview: https://near-horizon.notion.site/Chain-Signatures-4e919cf57f93472f9a6e89bf29581a02


## Implementation locations

- `app/near_verification.py`: fail-closed signed response and Intel TDX verifier.
- `near/contract/`: hash-chain audit contract.
- `near/scripts/testnet_demo.cjs`: adds a restricted function-call key, appends a synthetic audit event, and attempts a disallowed state-changing method.
- `near/README.md`: deployment and local testnet execution guide.
- `.github/workflows/near-contract.yml`: WASM build check.
