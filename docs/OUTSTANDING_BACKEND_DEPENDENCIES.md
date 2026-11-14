# Outstanding backend and access dependencies

The frontend for sign-in and the personal workspace is complete as far as the supplied code allows. None of the
following has been verified against a live system.

1. **Live FastAPI backend.** `/api/auth/challenge`, `/api/auth/session`, `/api/auth/logout`, `/api/session`,
   `/api/workspaces`, `/api/onboarding` and `POST /api/demo/trials` exist in this codebase but were only exercised
   through browser stubs (frontend) and a framework-shimmed harness (sample-claim logic). pytest and FastAPI were
   not installable where this was built, so `tests/test_demo_concurrency.py` and the pre-existing pytest suites were
   not run there.
2. **NEAR wallet connector.** `static/near-wallet.js` loads `@hot-labs/near-connect` from esm.sh at runtime and needs
   a real testnet wallet. Browser tests use a wallet test double. A real signature has not been produced or verified.
3. **Session and signing configuration.** Server-side secrets, cookie settings behind the real domain, and the
   challenge/recipient values must be configured and checked live.
4. **Turso/libSQL.** The sample claim relies on `INSERT OR IGNORE` and deterministic ids. SQLite is covered;
   Turso behaviour under true cross-instance concurrency needs a live check (no database was accessed).
5. **Not provided by the codebase (no frontend can supply these):** email or password login, social login, account
   recovery, workspace naming or team invitations, and any mainnet support. The UI does not offer them.
