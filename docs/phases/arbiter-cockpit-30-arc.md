# Phase 34: Arbiter Cockpit (3.0 arc)

## Status
- [x] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** Redesign the web dashboard around the human's actual job —
arbitration and monitoring — on top of the data Phases 31–33 now record.
The cockpit is a plain-JS, framework-free page served by the existing
hand-rolled server, organised as: **Now** (state, owed turn, in-flight
process with a live tail), **Inbox** (escalations and needs-human questions
with the Phase 33 brief and in-browser rulings), **Blockers** (pause, pending
interjections, abandoned briefs, watcher/turn liveness), **Feed** (live round
stream via SSE — entries, rulings, interjections, briefs as they happen),
**Diff** (scope-diff of the current submission with a capped `git diff`),
and **Usage** (burn by role / cycle / process from the `usage` table, a
round-over-round churn curve, and the subscription-window signal the CLI
reports). Controls from Phase 32/33 (`pause`/`resume`/`interject`/
`cancel-turn`/`brief --generate`/`rule`) become buttons over new POST
endpoints. The Saloon survives as an optional theme (`?theme=saloon` / a
toggle) — not the information architecture.

**Why:** Proposal §1/§4 Phase 34: the Saloon "optimizes for charm over
information; it cannot drive the system"; arbitration currently means
switching to a terminal. Everything the cockpit shows already exists as
data (state, rounds with `entries`/`rulings`, `interjections`, `briefs`,
`usage`, `inflight.json`, pause marker, scope-diff baseline); this phase
renders it and wires the controls.

**Depends on:** Phases 31–33. Source brief: `docs/tagteam-3.0-proposal.md`
§4 Phase 34, §8 Q6.

**Size:** large (comparable to Phase 31). Branch `phase-34-arbiter-cockpit`,
PR at the end. **Release:** 0.11.0.

---

## Scope

### In Scope

1. **Server hardening for write endpoints** (`tagteam/server.py`):
   - `ThreadingHTTPServer` (SSE needs long-lived connections; today's
     single-threaded `HTTPServer` would block every other request).
   - **Bind `127.0.0.1` by default**; `--host 0.0.0.0` (or any address)
     opts in to remote access. This is a deliberate, called-out deviation
     from "flag-off identical": the cockpit adds state-changing endpoints
     (rule/cancel/pause), so shipping them bound to all interfaces by
     default would be a regression in safety. Documented in README and the
     release notes; the old behavior is one flag away.
   - **CSRF/origin protection for every POST**: the server generates a
     random per-run token, embeds it in the served HTML, and requires it
     in an `X-Tagteam-Token` header on all POSTs (existing `/api/state`,
     `/api/config`, `/api/launch`, `/api/start-phase` included); it also
     rejects POSTs whose `Origin`/`Referer` (when present) is not the
     server's own origin. `Access-Control-Allow-Origin: *` is dropped for
     POST responses and for the new endpoints (kept for the read-only GETs
     the Saloon already exposes, to avoid breaking external readers).
   - Every write endpoint returns `{ok, message}` and never raises through
     the handler (500 with a JSON error, logged).
2. **Read endpoints** (all JSON, all read-only):
   - `GET /api/now` — state, owed role, `inflight.json` (with age), pause
     marker, watcher liveness (via `.tagteam/watcher-*.log` mtime is not
     reliable — use the pause/inflight/runner identity plus the existing
     `/api/watcher/status`), briefer enabled flag.
   - `GET /api/rounds/<cycle>` — **extended** with `entries`, `rulings`,
     `interjections` per round (already produced by `tail_rounds`) —
     additive fields only.
   - `GET /api/interjections?phase&type` (pending/delivered/retired).
   - `GET /api/briefs?phase&type` (history) and `GET /api/brief/<id>`
     (content); `GET /api/brief/current?phase&type` (the current event's
     successful brief or its attempt state — same rule as `tagteam brief`).
   - `GET /api/usage?phase&type&role` — `usage.aggregate()` output plus a
     per-round series for the churn curve (`[{round, role, input, output,
     cache_read, cost, duration}]`) and the latest **rate-limit signal**
     (Scope 6).
   - `GET /api/scope-diff/<cycle>` — the scope-diff path list from a new
     programmatic `cycle.compute_scope_diff(phase, type)` (extracted from
     `_cli_scope_diff`, CLI keeps its output byte-identical) plus a **capped**
     unified `git diff` for those paths (≤ 200 KB, ≤ 400 files; truncation
     flagged); binary files listed, not diffed.
   - `GET /api/tail?lines=N` — last N lines of the in-flight turn log (or the
     most recent), same resolution as `tagteam tail --no-follow`.
   - `GET /api/events` — **SSE stream** (Scope 3).
3. **SSE live feed** — `GET /api/events` keeps the connection open and emits
   `event: change` frames whenever any of these change: state `seq`, the
   rounds count of the current cycle, `interjections` max id, `briefs`
   max id/status, `usage` max id, pause marker presence, `inflight.json`
   presence/pid. Implementation: a per-connection loop that samples those
   cheap signals every 1 s (DB `SELECT MAX(id)`, file mtimes) and sends a
   frame only on change, plus a `: heartbeat` comment every 15 s;
   `Last-Event-ID` is honored by resending the current snapshot. Multiple
   clients (two devices, proposal Q6) each get their own thread; the server
   caps concurrent SSE connections (default 8, `--max-sse`) and returns 503
   above it. The existing polling in `app.js` stays as the fallback path
   (`EventSource` unsupported or connection lost → back to polling with the
   current backoff). No push from writers is required (no connection
   manager); this is a monitoring feed, 1 s latency is fine.
4. **Write endpoints** (POST, token-protected; each a thin wrapper over the
   Phase 32/33 command functions so behavior stays identical to the CLI):
   `/api/pause {reason?}`, `/api/resume`, `/api/interject {note, to?}`,
   `/api/interject/retire {id}`, `/api/cancel-turn`, `/api/brief/generate`,
   `/api/rule {ruling: approve|request-changes|answer, content?, to?}`.
   `by` is `web:<user>` where user is the OS user of the server process
   (the browser is local by default). Every call records exactly what the
   CLI records (diagnostics, interjections rows, ruling entries).
5. **Frontend — the cockpit** (`tagteam/data/web/`): new `cockpit.html`,
   `cockpit.js`, `cockpit.css` (plain JS, no build step, no framework,
   `EventSource` + `fetch`), served at `/` by default; the Saloon (`index.html`
   + `app.js` + `sprites.js` + `conversation.js`) is served at
   `/?theme=saloon` and via a header toggle, and remains functional
   unchanged. Panels: Now / Inbox / Blockers / Feed / Diff / Usage as
   described in the Summary; keyboard-free, single page, responsive enough
   for a laptop split screen; every control confirms destructive actions
   (`cancel-turn`, `rule approve/request-changes`) with the exact CLI it
   is about to run. No JS unit-test framework exists in the repo: the
   frontend is covered by (a) an HTML/asset smoke test (page and referenced
   assets served, token embedded), (b) endpoint tests, and (c) a real
   browser dogfood pass recorded in findings.
6. **Subscription-window signal**: Phase 31's Claude event stream contains
   `rate_limit_event {status, resetsAt, rateLimitType}`. The headless
   runner and briefer will store the **latest** such event per provider in
   a small additive table `rate_limits(provider, kind, status, resets_at,
   payload_json, ts)` (upsert per provider+kind); `/api/usage` and the
   Usage panel show "5h window: <status>, resets HH:MM" (Codex emits no
   equivalent → "n/a"). This is the honest version of the proposal's
   "burn-down gauge": what the CLI reports, no invented percentages.
7. **`tagteam serve` flags**: `--host` (default 127.0.0.1), `--port`
   (existing), `--theme cockpit|saloon` (default cockpit), `--max-sse`,
   `--no-open` (existing behavior unchanged otherwise).
8. **Docs**: README ("The Cockpit" replaces "The Saloon" section, Saloon
   noted as theme; security note on host binding + token), roadmap,
   findings `docs/phases/arbiter-cockpit-findings.md` (browser dogfood:
   screenshots or described flows, SSE with two tabs, a ruling made from
   the browser on the scratch project's escalation, usage panel over the
   real rows).
9. **Dogfood**: this repo's `tagteam serve` used during the impl cycle
   (Feed live while the headless reviewer runs; Usage over real rows;
   pause/resume from the browser as the hold); a scratch-project escalation
   ruled from the browser (`/api/rule` → brief inbox); two-tab SSE.

### Out of Scope (explicitly)
- Cross-project views (Phase 35 hub) — the cockpit is one project.
- Editing plans/rounds from the browser; starting cycles (existing
  `/api/start-phase` stays as-is).
- Authentication beyond the local token / origin check (a remote-access
  deployment story is not part of this phase; `--host` is the user's
  choice).
- Any change to round vocabulary or existing CLI output.
- Removing the Saloon or its endpoints.

---

## Technical Approach

### Files
- `tagteam/server.py` — `ThreadingHTTPServer`; token generation +
  `_check_write_auth()`; new GET routes; SSE handler; POST routes calling
  `controls.*`/`briefer.*` functions with an injected `out` buffer;
  `--host/--theme/--max-sse`; `_get_dashboard_html(theme)`.
- `tagteam/cockpit_api.py` — **new**: pure functions that build the JSON
  payloads (`now_payload`, `usage_payload`, `scope_diff_payload`,
  `brief_current_payload`, `events_signature`) so they are unit-testable
  without HTTP; server routes are thin.
- `tagteam/cycle.py` — `compute_scope_diff(phase, type, project_dir) ->
  dict` (paths + baseline info); `_cli_scope_diff` calls it.
- `tagteam/db.py` — `SCHEMA_VERSION = 6`, `rate_limits` table,
  `upsert_rate_limit`, `latest_rate_limits`; usage per-round series query.
- `tagteam/headless.py` / `tagteam/briefer.py` — record `rate_limit_event`
  from the events stream (claude) into `rate_limits` after each run.
- `tagteam/data/web/cockpit.html|js|css` — new; existing Saloon files
  untouched except a small "Cockpit" link.
- `tagteam/cli.py` — help for new `serve` flags.
- Tests: `tests/test_cockpit_api.py` (payload builders incl. scope-diff
  caps, usage series, now/pause/inflight, current-brief rule),
  `tests/test_server_cockpit.py` (real `ThreadingHTTPServer` on an
  ephemeral port in a thread: token required on every POST incl. legacy
  ones; origin check; each write endpoint calls the CLI-equivalent function
  and records the same rows; SSE: two concurrent clients each receive a
  frame after a `cycle add`, heartbeat present, `Last-Event-ID` resend, 503
  above `--max-sse`; asset smoke test; default bind is loopback and
  `--host` overrides), `tests/test_db.py` v6, `tests/test_headless.py`
  rate-limit capture, existing server validation tests unchanged.

### SSE frame
```
id: <signature-hash>
event: change
data: {"seq": 42, "rounds": 7, "interjections": 3, "briefs": 2, "usage": 19,
       "paused": false, "inflight": {"stem": "...", "pid": 123, "age_s": 41}}
```
The client re-fetches the panels it cares about on `change` (cheap, local).

### Security model (stated plainly)
- Default bind loopback; the token is 32 random bytes hex, generated at
  server start, embedded as `<meta name="tagteam-token">`, required as
  `X-Tagteam-Token` on all POSTs; `Origin`/`Referer` must match the
  server's host:port when present. This blocks cross-site POSTs from other
  pages in the same browser and any non-browser client that has not read
  the page. It does not attempt to protect a deliberately remote-exposed
  server — that is what `--host` opts into, and the README says so.

### Implementation order
0. `compute_scope_diff` extraction + tests (CLI byte-identical).
1. Server hardening: threading, loopback default, token/origin on all POSTs
   (existing tests + new).
2. `cockpit_api.py` payload builders + read endpoints + tests.
3. SSE endpoint + tests (two clients, cap, heartbeat).
4. Write endpoints (wrappers) + tests.
5. Schema v6 `rate_limits` + capture in headless/briefer + usage payload.
6. Frontend cockpit (panels, controls, EventSource with polling fallback,
   theme toggle) + asset smoke test.
7. Docs; browser dogfood on this repo + scratch escalation; findings;
   bump 0.11.0; PR.

---

## Success Criteria

- [ ] `tagteam serve` binds `127.0.0.1` by default and `--host 0.0.0.0`
  binds all interfaces (test); every POST — including the pre-existing
  `/api/state`, `/api/config`, `/api/launch`, `/api/start-phase` — is
  rejected (403, JSON) without the per-run `X-Tagteam-Token` or with a
  mismatching `Origin`, and accepted with both (tests); no
  `Access-Control-Allow-Origin: *` on POST responses.
- [ ] The server is threaded: an open SSE connection does not block other
  requests (test: hold an SSE stream and fetch `/api/state` concurrently).
- [ ] `GET /api/events` emits an initial snapshot, then a `change` frame
  within 2 s of a `cycle add`, an interjection, a brief row, a usage row, a
  pause marker change, or an inflight change; two concurrent clients both
  receive it; a heartbeat comment arrives within 20 s idle; `Last-Event-ID`
  gets a snapshot; the (N+1)th client above `--max-sse` gets 503.
- [ ] Read endpoints return the documented shapes: `/api/now`,
  `/api/rounds/<cycle>` with `entries`/`rulings`/`interjections`,
  `/api/interjections`, `/api/briefs`, `/api/brief/<id>`,
  `/api/brief/current` (same selection rule as `tagteam brief`),
  `/api/usage` (aggregate + per-round series + rate-limit signal),
  `/api/scope-diff/<cycle>` (paths + capped diff, truncation flagged),
  `/api/tail` (unit tests on payload builders + endpoint tests).
- [ ] `compute_scope_diff` extracted; `tagteam cycle scope-diff` output is
  byte-identical to before (existing tests unmodified).
- [ ] Each write endpoint performs exactly what its CLI counterpart does
  (same rows/markers/diagnostics, `by = web:<user>`), returns `{ok,
  message}`, and never surfaces a traceback (tests per endpoint incl. an
  invalid `rule` on a non-escalated cycle → `{ok:false}` 400).
- [ ] Schema v6 `rate_limits` additive; headless turns and briefs record
  the latest Claude `rate_limit_event`; `/api/usage` includes it; 0.10.0
  opens a v6 project (release checklist).
- [ ] Frontend: `/` serves the cockpit with the token embedded and all
  referenced assets resolve (smoke test); `/?theme=saloon` serves the
  unchanged Saloon; the cockpit works with SSE and falls back to polling
  when `EventSource` is unavailable (manual dogfood, recorded).
- [ ] Docs + findings: README cockpit section and security note; findings
  record the browser dogfood — live Feed during a headless reviewer turn,
  a ruling made from the browser on a scratch escalation (row + brief
  link), pause/resume from the browser used as the hold, Usage panel over
  the real rows, two-tab SSE.
- [ ] Released as 0.11.0 via PR → merge → tag (post-approval; CI green).

## Open Questions

1. **Loopback default** — a real behavior change for anyone serving on a
   LAN today. Recommendation: accept it as a safety fix (one flag restores
   it) and say so in the release notes.
2. **Token scope** — per server run (recommended) vs. persisted in
   `.tagteam/`. Per run means a browser tab must reload after the server
   restarts; persisted means a token on disk. Recommendation: per run.
3. **Feed granularity** — signal-only frames (recommended; client refetches)
   vs. shipping full payloads over SSE. Signals keep the server trivial and
   multi-client safe.

## Risks
- **Scope size** — mitigated by the ordered steps: 0–5 are backend with
  tests and each is independently reviewable; the frontend (6) is a
  single self-contained asset set; the Saloon stays as the safety net.
- **Long-lived connections + threading in a hand-rolled server** —
  `ThreadingHTTPServer` is stdlib; per-connection loops are simple sleeps;
  cap on SSE clients; tests cover concurrency basics.
- **No JS test framework** — accepted for this phase (plain JS, small);
  covered by endpoint tests + asset smoke + recorded browser dogfood.
- **Diff endpoint cost on big repos** — capped bytes/files, and the diff is
  computed only on request, never in the SSE loop.
