# Optional NEAR AI Cloud Reasoning

## Purpose
OUTCOME can request a plain-language operational brief for a supplier commitment. The model explains the deterministic assessment, highlights uncertainty and suggests questions for the owner. It does not calculate or override commitment status, change payment eligibility, approve payments or execute actions.

## Provider
- Endpoint: https://cloud-api.near.ai/v1/chat/completions
- Model: Qwen/Qwen3.6-35B-A3B-FP8
- Environment variable: NEARAI_API_KEY (the adapter also accepts NEAR_AI_KEY)
- The model is listed by NEAR AI Cloud as confidential TEE inference.
- Published price at implementation time: USD 0.17 per million input tokens and USD 1.10 per million output tokens; pay-as-you-go with no minimums. Check the current catalogue before use.

## Data boundary
The request is sent only when a user explicitly clicks **Generate AI brief** and the server has an API key. The adapter sends:
- Commitment terms and ID.
- Deterministic assessment status, issues and next actions.
- Up to the latest 10 evidence records, excluding free-text evidence notes and review-history details.
- Payment policy fields and action type/description/status.

Do not enter sensitive live business data into this hackathon demo. Before the completion is sent, OUTCOME fetches a NEAR attestation report with a client nonce (`provider=near`, `signing_algo=ecdsa`) and verifies every gateway and model quote. Verification uses `dcap-qvl` and the TD10 body from `to_json()` (`report.TD10`), which is the shape current `dcap-qvl` returns. A quote is accepted only when its TCB status is `UpToDate`, the debug bit in `td_attributes` is clear, report data binds the 20-byte ECDSA address (right-padded) and the nonce, `MRCONFIGID` equals `0x01 || SHA-256(app_compose) || 15 zero bytes`, and RTMR3 replays the IMR 3 event log. The completion is then sent with `x-no-aliasing: true` and `Accept-Encoding: identity`. The signature request must include `signature_kind`. OUTCOME does not infer that field from the colon count. It recovers the EIP-191 ECDSA signer and requires exactly one matching preflight report. Provider-TEE signatures must also present NVIDIA GPU evidence whose NRAS `x-nvidia-overall-att-result` is boolean `true` and whose `eat_nonce` matches the preflight nonce. The string `PASS` is not accepted. If any check fails, the brief is rejected and is not stored or displayed.

A provider-TEE result is labelled `PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED`. A gateway signature is labelled `GATEWAY_SIGNATURE_AND_GATEWAY_TDX_VERIFIED` and is not described as model-TEE verification. The NRAS JWT signature is not checked locally; the client trusts the TLS response from `nras.attestation.nvidia.com` and still checks the verdict and `eat_nonce`. Sigstore provenance and the live gateway TLS peer certificate are not verified. Collateral comes from `dcap-qvl`'s default Phala PCCS. This is not proof that the model's reasoning is correct. The protocol references are https://docs.near.ai/cloud/verification/reference/quote-nonce-signer, https://docs.near.ai/cloud/verification/cloud-api/response-signatures, and https://github.com/nearai/nearai-cloud-verifier.

## Safety contract
- Deterministic rules remain authoritative.
- The model cannot authorize, approve or execute payments.
- The model is instructed not to infer fraud, dishonesty or intent.
- Output is validated against a fixed JSON shape before display.
- Malformed responses, missing signatures, hash mismatches, signer mismatches, failed TDX verification and attestation-binding failures all fail closed; no AI-generated action is automatically created.
- AI briefs are stored as advisory history linked to the commitment.
- Requests are rate-limited per client IP (default five per hour; configurable with OUTCOME_AI_RATE_LIMIT_PER_HOUR) to limit accidental key spend.

## Enable
Set NEARAI_API_KEY in the server environment and restart OUTCOME. The UI will enable the AI brief button. No API key is sent to the browser.
