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

Do not enter sensitive live business data into this hackathon demo. The application does not independently verify the model's hardware attestation or response signature. The API result is labelled NOT_INDEPENDENTLY_VERIFIED_BY_OUTCOME.

## Safety contract
- Deterministic rules remain authoritative.
- The model cannot authorize, approve or execute payments.
- The model is instructed not to infer fraud, dishonesty or intent.
- Output is validated against a fixed JSON shape before display.
- Malformed responses and provider errors fail closed; no AI-generated action is automatically created.
- AI briefs are stored as advisory history linked to the commitment.

## Enable
Set NEARAI_API_KEY in the server environment and restart OUTCOME. The UI will enable the AI brief button. No API key is sent to the browser.
