# Verification Plan

The pytest workflow is configured for pushes and pull requests to `main`.

Verification coverage in the current build:
- Original commitment creation and assessment semantics.
- Append-only commitment amendments and version history.
- Append-only evidence reviews, including rejection after verification.
- Latest-evidence semantics: a newer unreviewed or rejected record supersedes an older verified record.
- Payment proposals blocked by missing, unverified, rejected or discrepant evidence.
- Partial payment proposals blocked unless explicitly enabled by owner policy.
- Supplier pattern signals require at least two incidents backed by latest verified evidence; recurring-pattern labels additionally require a sample of at least five and an incident rate of at least 30%.
- Action lifecycle rejects invalid transitions and requires same-commitment verified evidence to verify an outcome.
- Synthetic demo seeding is non-destructive and refuses to run when existing commitments are present.
- Destructive demo reset is disabled unless an explicit local-only environment flag is enabled.
- Optional NEAR AI Cloud brief is disabled without a server-side key, stores advisory history, and cannot override deterministic checks.
- Human approval remains required even when simulation checks pass.
- No wallet signing, transaction submission or real asset movement.

Reviewer and actor names are demo inputs, not authenticated identities. An isolated 13-test run of the payment evaluator, pattern-recognition logic and NEAR AI adapter passed in a Python environment. The full repository API/integration suite has not yet run; the GitHub connector returned no CI status for the current branch, so do not treat the full suite as passed.
