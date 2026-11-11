# Verification Plan

The pytest workflow is configured for pushes and pull requests to `main`.

Verification coverage in the current build:
- Original commitment creation and assessment semantics.
- Append-only commitment amendments and version history.
- Append-only evidence reviews, including rejection after verification.
- Latest-evidence semantics: a newer unreviewed or rejected record supersedes an older verified record.
- Payment proposals blocked by missing, unverified, rejected or discrepant evidence.
- Partial payment proposals blocked unless explicitly enabled by owner policy.
- Payment policy updates are append-only and included in the shared commitment timeline.
- Supplier pattern signals require at least two incidents backed by latest verified evidence; recurring-pattern labels additionally require a sample of at least five and an incident rate of at least 30%.
- Action lifecycle rejects invalid transitions and requires same-commitment verified evidence to verify an outcome.
- Synthetic demo seeding is non-destructive and refuses to run when existing commitments are present.
- Destructive demo reset is disabled unless an explicit local-only environment flag is enabled.
- Optional NEAR AI Cloud brief is disabled without a server-side key, stores advisory history only after cryptographic verification, and cannot override deterministic checks.
- Human approval remains required even when simulation checks pass.
- A tester can sign a hash-only testnet `record_event` with their own wallet. The server checks the final transaction before storing it. OUTCOME does not hold a key, and payment remains a simulation.

Reviewer and actor names are demo inputs, not authenticated identities. A local `PYTHONPATH=. pytest -q` run on 11 Nov 2026 reported 72 passed and 2 skipped. The skipped tests are the live Turso checks, which need database credentials that are not in this tree.
