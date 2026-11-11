# Optional NEAR AI Cloud Reasoning

## Purpose
OUTCOME can request a plain-language operational brief for a recorded financial commitment. The model explains the deterministic assessment, highlights uncertainty and suggests questions for the owner. It does not calculate or override commitment status, change payment eligibility, approve payments or execute actions.

## Provider
- Endpoint: https://cloud-api.near.ai/v1/chat/completions
- Model: Qwen/Qwen3.6-35B-A3B-FP8
- Environment variable: NEARAI_API_KEY (the adapter also accepts NEAR_AI_KEY)
- The model is listed by NEAR AI Cloud as confidential TEE inference.
- Published price at implementation time: USD 0.17 per million input tokens and USD 1.10 per million output tokens; pay-as-you-go with no minimums. Check the current catalogue before use.

## Data boundary
An operational brief is sent when a person clicks **Generate AI brief**, and after they explicitly confirm an intake proposal. It is not sent in the background. The brief adapter sends:
- Commitment terms and ID.
- Deterministic assessment status, issues and next actions.
- Up to the latest 10 evidence records, excluding free-text evidence notes and review-history details.
- Payment policy fields and action type/description/status.

Do not enter sensitive live business data into this hackathon demo. Before the completion is sent, OUTCOME fetches a NEAR attestation report with a client nonce (`provider=near`, `signing_algo=ecdsa`, `include_tls_fingerprint=false`, header `x-no-aliasing: true`) and verifies every gateway and model quote. Verification uses `dcap-qvl` and the TD10 body from `to_json()` (`report.TD10`). A quote is accepted only when its TCB status is exactly `UpToDate`. The official sample verifier also accepts `OutOfDate`; OUTCOME does not. The debug bit in `td_attributes` must be clear, report data must bind the 20-byte ECDSA address (right-padded) and the nonce, echoed gateway `report_data` must match the verified quote, `MRCONFIGID` must equal `0x01 || SHA-256(app_compose) || 15 zero bytes`, and RTMR3 must replay the IMR 3 event log. The completion is then sent with `x-no-aliasing: true`, `Accept-Encoding: identity`, and `chat_template_kwargs.enable_thinking` set to false so Qwen3.6 returns the brief instead of spending `max_tokens` on reasoning. That flag does not skip verification.

The signature response must include `signature_kind` and `signing_algo`. OUTCOME does not infer `signature_kind` from the colon count and does not assume ECDSA when `signing_algo` is missing. It builds the expected text from the kind (`model:request_hash:response_hash` or `request_hash:response_hash`), requires an exact match to the bytes that were sent and received, recovers the EIP-191 ECDSA signer from a 65-byte signature, and requires exactly one matching preflight report. Provider-TEE signatures must also present NVIDIA GPU evidence whose NRAS `x-nvidia-overall-att-result` is boolean `true` and whose `eat_nonce` matches the preflight nonce. The string `PASS` is not accepted. Empty or null model content is rejected after verification, with the `finish_reason`, and is not displayed. If any check fails, the brief is rejected and is not stored or displayed.

A provider-TEE result is labelled `PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED`. A gateway signature is labelled `GATEWAY_SIGNATURE_AND_GATEWAY_TDX_VERIFIED` and is not described as model-TEE verification.

Outstanding limitations, including after the 11 Nov 2026 live run:

- OUTCOME does not verify NVIDIA NRAS JWT signatures locally. It uses the TLS response from `nras.attestation.nvidia.com` and still requires a boolean `true` verdict and a matching `eat_nonce`.
- OUTCOME does not verify Sigstore image provenance. A measured quote is not a provenance statement.
- OUTCOME does not bind the completion's TLS connection to the quote. The peer certificate and the attestation report were not taken from the same connection, and HTTPS is not attestation.
- Intel quote collateral comes from `dcap-qvl`'s default Phala PCCS, not from a separately pinned Intel PCS response.
- The official sample verifier also accepts TCB status `OutOfDate`. OUTCOME accepts only `UpToDate`.

This is not proof that the model's reasoning is correct. The protocol references are https://docs.near.ai/cloud/verification/reference/quote-nonce-signer, https://docs.near.ai/cloud/verification/cloud-api/response-signatures, https://docs.near.ai/cloud/quickstart, and https://github.com/nearai/nearai-cloud-verifier.

A separate live run on 11 Nov 2026 (01:09–01:14 Africa/Lagos), against `https://cloud-api.near.ai/v1` and `Qwen/Qwen3.6-35B-A3B-FP8`, confirmed preflight and completion HTTP 200, `signature_kind=provider_tee`, UpToDate TDX, a clear debug bit, MRCONFIGID, RTMR3, and an NRAS verdict of boolean true with a matching `eat_nonce`. With reasoning disabled, the same checks returned a brief labelled `PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED`. A tampered response produced no brief. This repository does not contain an API key; do not paste one into documentation, examples, or Vercel settings from this tree.

## Safety contract
- Deterministic rules remain authoritative.
- The model cannot authorize, approve or execute payments.
- The model is instructed not to infer fraud, dishonesty or intent.
- Output is validated against a fixed JSON shape before display.
- Malformed responses, missing signatures, hash mismatches, signer mismatches, failed TDX verification and attestation-binding failures all fail closed; no AI-generated action is automatically created.
- AI briefs are stored as advisory history linked to the commitment.
- Requests are rate-limited per client IP (default five per hour; configurable with OUTCOME_AI_RATE_LIMIT_PER_HOUR) to limit accidental key spend. Intake review and a brief generated after confirmation share this limit.

## Business intake
A person can describe a commitment or a piece of evidence in ordinary language from the same page as the manual form. OUTCOME sends that text to the same NEAR AI Cloud model and endpoint. The model is instructed to return JSON using only existing commitment and evidence fields, with nulls where the text does not support a value.

Before anything is shown, the response goes through the same preflight quote and exact-byte signature checks as a brief. If those checks fail, the proposal is not shown and nothing is saved. After the checks, OUTCOME drops any value the text does not support, including dates, amounts, currencies, and parties the model added. It asks a question only for a missing or ambiguous field that the existing record requires. It matches a description to an existing commitment only when the text identifies one record, and it does not save a second copy of the same evidence.

Nothing is written until the person confirms the proposal. Confirmation saves commitment and evidence rows through the existing models, then runs the deterministic assessment. Evidence recorded this way stays unreviewed until a person reviews it. The operational brief is requested after that assessment and is stored only when its own checks pass. A failed brief does not change the assessment and is not labelled verified.

Intake does not approve, submit, or complete a payment. The manual forms remain available when the API key is absent.

## Enable
Set NEARAI_API_KEY in the server environment and restart OUTCOME. The UI will enable the AI brief button and ordinary-language intake. No API key is sent to the browser. Do not commit the key or place it in documentation examples.
