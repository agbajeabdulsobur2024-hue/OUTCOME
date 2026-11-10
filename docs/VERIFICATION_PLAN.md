# Verification Plan

The pytest workflow is configured for pushes and pull requests to `main`.

Verification coverage in the current build:
- Original commitment creation and assessment semantics.
- Append-only commitment amendments and version history.
- Append-only evidence reviews, including rejection after verification.
- Latest-evidence semantics: a newer unreviewed or rejected record supersedes an older verified record.
- Payment proposals blocked by missing, unverified, rejected or discrepant evidence.
- Partial payment proposals blocked unless explicitly enabled by owner policy.
- Supplier pattern signals require at least two incidents backed by latest verified evidence.
- Action lifecycle rejects invalid transitions and requires same-commitment verified evidence to verify an outcome.
- Human approval remains required even when simulation checks pass.
- No wallet signing, transaction submission or real asset movement.

Reviewer and actor names are demo inputs, not authenticated identities. The connector did not return a successful CI run for the current branch, so the suite must still be executed in a real Python environment before treating these checks as passed.
