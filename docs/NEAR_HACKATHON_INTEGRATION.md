# NEAR hackathon integration status

Last reviewed: 11 November 2026

This document separates working integrations from planned work and ecosystem technologies that are intentionally not being used. Track starter prompts are guidance, not a requirement to integrate every named NEAR technology.

## Current implementation

| Capability | Status | What is true today |
|---|---|---|
| NEAR AI Cloud inference | Live call succeeded on 11 November 2026 | A call to https://cloud-api.near.ai/v1 for `Qwen/Qwen3.6-35B-A3B-FP8` succeeded. The user still triggers a brief explicitly. Briefs stay optional and do not override the deterministic assessment. The hosted Vercel demo does not serve those briefs: on current main, verification can pass while the brief fails to display because the model can spend the token budget on reasoning. Display fix: unmerged [PR #6](https://github.com/agbajeabdulsobur2024-hue/OUTCOME/pull/6). |
| Response signature + TDX binding | Live `provider_tee` signature and Intel TDX attestation verified on 11 November 2026 | The 11 November 2026 call verified `signature_kind` `provider_tee` (ECDSA/EIP-191) and the Intel TDX attestation. Fail-closed held when evidence was tampered or missing. Before the completion, OUTCOME verifies UpToDate TDX quotes (debug bit, report-data binding, MRCONFIGID, RTMR3) for the gateway and every model candidate. After the completion it requires `signature_kind`, EIP-191 ECDSA recovery, and exactly one matching preflight signer. Provider-TEE signatures require an NRAS verdict of boolean `true` plus `eat_nonce`. NVIDIA JWT signatures, Sigstore, and TLS binding of the completion connection are still not verified in OUTCOME. |
| NEAR account / wallet connection | Web app holds no key | The testnet proof used a local key on `thewordweaver.testnet` to create a scoped function-call key. The OUTCOME web app does not connect a wallet or hold a full-access key. A full-access key was not put in Vercel. |
| Function-call access-key enforcement | Testnet `MethodNameMismatch` proof completed | A restricted function-call key on `thewordweaver.testnet` was limited to `record_event`. `admin_probe` was rejected with `MethodNameMismatch` ([7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk)). That rejected transaction is not indexed, which is expected on this nearcore version. The local sandbox chain is separate and is not this proof. |
| NEAR Intents Agent API | Intentionally not connected | The Agent API is an experimental, mainnet-only beta with real-asset risk and no testnet. It requires owner signatures and a funded agent. OUTCOME will not connect or fund it for a synthetic hackathon demo. |
| Chain Signatures | Not integrated | The available Chain Signatures documentation describes the technology as alpha and testnet-only. It is not needed for the current supplier-payment protection workflow. |
| On-chain audit anchor | Deployed on `thewordweaver.testnet` | Deploy and init, FINAL: [8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy). On-chain `code_hash` `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`. `record_event` accepted and stored, FINAL: [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj). |
| Adversarial payment defense demo | Implemented at application layer | The UI can submit a simulated prompt-injected payment request that exceeds the owner cap and changes the destination. The policy evaluator blocks it and records the reasons. No signing or transfer occurs. |

## What the security demo proves — and does not prove

The adversarial test demonstrates deterministic rejection of a hostile payment proposal when it violates the owner-defined spending limit and exact destination. The same evaluator also checks currency, commitment value, delivery evidence, invoice matching and partial-payment permission.

The 11 November 2026 testnet run recorded `record_event` [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj) (FINAL) and a `MethodNameMismatch` rejection for `admin_probe` [7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk). That rejection is not indexed, which is expected on this nearcore version. The local sandbox chain is separate and is not that proof.

## Recommended NEAR scope for this submission

1. NEAR AI Cloud live inference succeeded on 11 November 2026. Keep briefs optional, user-triggered, and non-authoritative. Do not describe the hosted Vercel demo as serving those briefs while [PR #6](https://github.com/agbajeabdulsobur2024-hue/OUTCOME/pull/6) is unmerged.
2. The live call confirmed the `provider_tee` ECDSA/EIP-191 signature and Intel TDX attestation for `Qwen/Qwen3.6-35B-A3B-FP8`. NVIDIA JWT signatures, Sigstore, and TLS binding of the completion connection are still not in OUTCOME.
3. Keep payment authorization fail-closed and demonstrate the hostile-request block.
4. Do not add NEAR Intents Agent API or real-fund movement to the demo. The official quickstart states that it is mainnet-only beta, with no testnet and real asset risk.
5. Testnet deploy, `record_event`, and the `MethodNameMismatch` rejection are recorded above. Do not put a full-access key in the web app or in Vercel.

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
