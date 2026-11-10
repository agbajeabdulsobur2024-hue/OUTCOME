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

Do not enter sensitive live business data into this hackathon demo. Before parsing or persisting model output, OUTCOME verifies the exact request and raw response SHA-256 hashes against the provider's signed chat payload, recovers and checks the ECDSA signing address, fetches a fresh nonce-bound model attestation, verifies the Intel TDX quote using dcap-qvl, and checks that the quote report data binds both the signing address and fresh nonce. If any check fails, the brief is rejected and is not stored or displayed. The returned record is labelled VERIFIED_BY_OUTCOME only after those checks pass.

The NVIDIA NRAS verdict is checked as supplemental evidence and its nonce is matched, but OUTCOME does not independently validate the NRAS JWT signature. TDX verification accepts Intel UpToDate and OutOfDate statuses as cryptographically valid, matching the upstream verifier's stated behavior; an OutOfDate platform status should still be surfaced as a security freshness warning. Verification proves response integrity and signer-to-TEE binding, not correctness of the model's reasoning. The upstream verifier is the protocol reference: https://github.com/nearai/nearai-cloud-verifier.

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
