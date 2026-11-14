# Navigation, sample entry and reliability: architecture notes

## Routes (hash-based; the URL is the source of truth)
`#overview` · `#commitments` · `#commitments/<id>` · `#commitments/<id>/<tab>` · `#patterns-section` · `#new-commitment` · `#security`
(`<tab>` is one of the record tabs; omitted means the default `decision` tab.) `parseRoute()` reads it, `currentHash()`
builds it from view + selected record + tab, `syncUrl()` is the only writer (pushes only when the URL actually changes).
`popstate` → `syncFromHistory()` restores view, record and tab. Session storage is used only so the Security and Trust page
can return to the record behind it. Never `#signals` (the signals view is `#patterns-section`).

Unavailable record links: 404 → "not available" (URL repaired to `#commitments`); 401/403/410 → "session has ended";
other failures → "could not load" with *Try again*. No record data is shown for any of them.

A deep link (`#commitments…`, `#patterns-section`, `#new-commitment`) opened in a new tab enters the browser's existing session
(last-used scope first). A plain visit to `/` (or `#overview`) always shows the landing page.

## Sample entry
`enterSample()` is the single canonical entry: share the in-flight attempt → resume via cookie or `POST /api/demo/trials`
(sent unscoped) → validate the response → confirm `/api/session` shows the cookie → render → confirm render. It never reads tour state.
- **Get started** = guided onboarding (starts in the labelled sample; final step leads to sign-in for a personal workspace).
- **Revisit the sample** = same sample, no tour.
- **Leave the sample** (banner) returns to the landing page, clears sample data from the DOM, keeps the sample session for reuse.

## Backend reliability
- `app/migrations.py`: `add_column_if_missing` tolerates only the "duplicate column" error from a concurrent start (after re-checking the schema).
- `app/ai.py`: `_complete_json` has a 45 s total budget (`AI_TOTAL_BUDGET_SECONDS`) and fails closed with `AIProviderError`.
  A protective bound; the production cause of the 26 timeouts was not established.
- `POST /api/demo/trials`: resume doesn't spend the demo rate allowance; stale scope headers can't block it; failures are a plain 503; `Server-Timing`, `Cache-Control: no-store`.

## Tests (see tests/run_all.sh)
Browser suites run against `tests/browser/stub-server.cjs` (a stub, **not** the FastAPI backend).

## Get started, direct links and concurrent sample entry (latest stage)

**Two separate entries.** *Get started* opens an onboarding dialog toward the visitor's **own** workspace
(sign in with a NEAR testnet wallet, open a personal workspace, record a first commitment). It never creates or
enters the sample. *Revisit the sample* (landing, footer, and "Revisit the sample instead" inside the dialog) is the
only entry to the interactive sample and starts no tour. The sample guide still lives inside the sample.

**What counts as success.** Nothing is reported as signed in until `/api/session` says `authenticated: true` for the
connected account, and no workspace is entered until `/api/workspaces` returns a personal, non-synthetic workspace
and `/api/session` shows `personal.active`. Wallet output and server responses are validated first
(`static/start-flow.js`). Failures use plain wording, never raw server text for 5xx, and always offer a retry.

**Direct links.** A workspace route (`#commitments/<id>/<tab>`, `#new-commitment`, `#patterns-section`) reached while
signed out is kept in the URL and, as a validated hash only, in `sessionStorage` for 30 minutes
(`static/route-intent.js`). A notice offers Sign in. After a server-confirmed sign-in, an account that has a
workspace is taken to that record and tab with focus on the record. An account with no workspace is told the link
does not belong to it and continues to onboarding. Unavailable or invalid records show the existing calm notice and a
clean URL. Choosing the sample, "Not now", or an expired intent clears it.

**Concurrent first visits.** The browser keeps a random per-browser *sample claim* (created under a Web Lock) and sends
it as `X-Outcome-Sample-Claim`. The server derives one sample workspace and stable record ids from it
(`app/sample_claim.py`), so tabs, double taps and timeout retries share one sample. Without a claim the original
behaviour is unchanged. The claim names no one and grants nothing beyond the synthetic sample.
