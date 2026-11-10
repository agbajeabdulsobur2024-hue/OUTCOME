# Verification Plan

The CI workflow runs the full pytest suite on pushes and pull requests to `main`.

The current verification focus is:
- Original commitment creation and assessment semantics.
- Append-only commitment amendments and version history.
- Append-only evidence reviews, including rejection after verification.
- Latest-evidence semantics: a newer unreviewed record supersedes an older verified record.
- Payment proposals blocked by missing, unverified, rejected or discrepant evidence.
- Human approval remains required even when all simulation checks pass.
- No wallet signing, transaction submission or real asset movement.

Reviewer labels are demo inputs, not authenticated identities. The payment evaluator is simulation-only.
