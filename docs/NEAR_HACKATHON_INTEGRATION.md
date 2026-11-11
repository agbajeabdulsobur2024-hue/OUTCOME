# NEAR hackathon integration status

Last reviewed: 11 November 2026

This document separates working integrations from planned work and ecosystem technologies that are intentionally not being used. Track starter prompts are guidance, not a requirement to integrate every named NEAR technology.

## Current implementation

| Capability | Status | What is true today |
|---|---|---|
| NEAR AI Cloud inference | Live completion confirmed outside this tree on 11 Nov 2026 | The backend calls `https://cloud-api.near.ai/v1/chat/completions` for `Qwen/Qwen3.6-35B-A3B-FP8` when `NEARAI_API_KEY` is configured. The user explicitly triggers a brief. A live run that day returned HTTP 200 for preflight and completion, and, with `chat_template_kwargs.enable_thinking` false, a displayable brief. This repository still has no API key. |
| Response signature + TDX binding | Live provider-TEE path confirmed; three gaps remain | The same run verified `signature_kind=provider_tee`, EIP-191 ECDSA, TCB `UpToDate` only, a clear debug bit, MRCONFIGID, RTMR3, and an NRAS verdict of boolean `true` plus `eat_nonce`. Tampered and missing evidence failed closed. OUTCOME still does not verify NVIDIA JWT signatures or Sigstore image provenance, and it does not bind the completion TLS connection to the quote. Collateral comes from Phala's default PCCS. The official sample verifier also accepts `OutOfDate`; OUTCOME does not. |
| NEAR account / wallet connection | Isolated testnet demo scaffold | A dedicated testnet script uses an owner key locally to create a scoped function-call key. The OUTCOME web app itself does not connect a wallet or hold the key. |
| Function-call access-key enforcement | Sandbox proof on protocol 88; live testnet proof pending | The contract is pinned to near-sdk 5.17.2. `near/scripts/sandbox_key_test.cjs` deploys it to a local neard 2.14.0 sandbox, appends `record_event` with a key scoped to that method, and requires `MethodNameMismatch` for `admin_probe` without changing `get_admin_probe_count`. That sandbox chain is not testnet. No testnet transaction has been sent. The demo script treats near-api-js error type `MethodNameMismatch` (message `isn't allowed by the access key`) as the protocol rejection and re-queries `error.context.transactionHash`. |
| NEAR Intents Agent API | Intentionally not connected | The Agent API is an experimental, mainnet-only beta with real-asset risk and no testnet. It requires owner signatures and a funded agent. OUTCOME will not connect or fund it for a synthetic hackathon demo. |
| Chain Signatures | Not integrated | The available Chain Signatures documentation describes the technology as alpha and testnet-only. It is not needed for the current supplier-payment protection workflow. |
| On-chain audit anchor | Deployed on `thewordweaver.testnet` | Deploy and init, FINAL: [8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy). On-chain `code_hash` `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`. `record_event` accepted and stored, FINAL: [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj). |
| Adversarial payment defense demo | Implemented at application layer | The UI can submit a simulated prompt-injected payment request that exceeds the owner cap and changes the destination. The policy evaluator blocks it and records the reasons. No signing or transfer occurs. |

## What the security demo proves — and does not prove

The adversarial test demonstrates deterministic rejection of a hostile payment proposal when it violates the owner-defined spending limit and exact destination. The same evaluator also checks currency, commitment value, delivery evidence, invoice matching and partial-payment permission.

The 11 November 2026 testnet run recorded `record_event` [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj) (FINAL) and a `MethodNameMismatch` rejection for `admin_probe` [7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk). That rejection is not indexed, which is expected on this nearcore version. The local sandbox chain is separate and is not that proof.

## Recommended NEAR scope for this submission

1. Keep NEAR AI Cloud as the live ecosystem integration. A 11 Nov 2026 run against the configured Qwen model returned a brief only after signature and TDX checks passed.
2. Do not label a brief verified unless those cryptographic checks succeed. NVIDIA JWT signatures, Sigstore provenance, and same-connection TLS binding are still outstanding.
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
