# Phase 35 — Cross-Project Hub: findings

Plan: `docs/phases/cross-project-hub-30-arc.md` (approved round 2; UX flow
designed first with the `ux-design-guide` skill). Branch
`phase-35-cross-project-hub`, release 0.12.0.

## What shipped

- **`tagteam hub`** (`tagteam/hub.py`): server on `127.0.0.1:8090` by
  default (`--host`, `--port`, `--interval`, `--max-sse`, `--registry
  PATH`, `--all`), `--list [--json]` text mode; `tagteam registry list
  [--json] | unregister PATH`. Nothing runs unless invoked (flag-off
  identical; the registry format is unchanged).
- **`tagteam/hub_api.py`** (pure, read-only): `classify_registry` (ok /
  legacy / missing / no-yaml / scratch), `read_only_connect` (`mode=ro`,
  never creates, never migrates), `project_summary` (state, canonical
  cycle-status file, pause, in-flight + pid liveness, watcher liveness via
  the DB-free `cockpit_api.watcher_status`, brief-ready from a read-only
  query, usage totals), `classify_row` (needs_you / waiting / quiet; stale
  = owed ≥ 30 min ∧ no live in-flight ∧ no watcher; **abandoned = stale ∧
  ≥ 24 h**), `hub_payload`, `aggregate_usage` (24 h / 7 d / all),
  `shared_rate_limits` (newest row **per (provider, kind)** across
  projects, tie-break later `ts` then registry order), `hub_signature`
  (content hash of the state / cycle-status / registry files, size+mtime
  of rounds / DB / WAL, pause marker, in-flight stem+pid+alive, watcher
  pidfile alive, watcher liveness from ONE shared process snapshot),
  `render_text`. An AST test forbids any `db.connect` / `now_payload` /
  `read_status` / `read_rounds` / `event_for_cycle` /
  `get_registered_projects` call in the module (only
  `sqlite3.connect(…mode=ro)` inside `read_only_connect`).
- **`CockpitRouter`** (`tagteam/server.py`): the Phase 34 handler's routing
  moved into a per-project context object (`project_dir`, mode, token,
  `max_sse` + its own SSE counter, `base_path`) with `handle_get(h, parsed,
  path)` / `handle_post(h, parsed, path)` / `handle_options(h)` /
  `check_write_auth(h)` / `sse(h)` over the live handler `h`; a
  `_ResponseMixin` holds the `_send_*` helpers. `make_handler` builds one
  router and its handler class is a thin per-request shell — the standalone
  cockpit and the legacy path are unchanged (existing tests untouched, all
  green). The hub's `HubHandler` caches `{project_id: CockpitRouter}` (context,
  not handlers), strips `/p/<id>` and delegates.
- **Base-aware HTML:** `_get_dashboard_html(theme, token, base_path)`
  rewrites root-relative `href="/…"` / `src="/…"` to `<base>/…` and injects
  `<meta name="tagteam-base">`; `cockpit.js` prefixes every fetch /
  EventSource URL from the meta and renders "← Hub" only when mounted;
  standalone (`base_path=""`) → served page identical to 0.11.0 (test).
- **Hub page** `hub.html|css|js`: top strip (N projects · M live · burn 24 h
  / 7 d · shared window · Live/Polling), Needs you (primary **Open**),
  Waiting (badges: abandoned? / stale / in flight / watcher / paused + the
  CLI hint), Quiet collapsed to a count, "show hidden" toggle, empty
  states; SSE `/api/hub/events` with polling fallback and a 30 s snapshot
  refresh in live mode.
- **`registry.read_registry_raw()` / `registry_path()`** — non-mutating
  reads; `get_registered_projects()` (prunes + rewrites) untouched for
  `upgrade` / `rollback`.

## Deviations / notes for the reviewer

- **`cycle.read_status` is DB-first (it calls `db.connect`, which
  migrates)** — the plan listed it among "non-migrating readers"; it is
  not. The hub reads the canonical status file directly
  (`hub_api.read_cycle_status_file`: `docs/handoffs/` then
  `.tagteam/legacy/`), and derives the escalation event key from the
  rounds JSONL's last entry with the same formula as
  `briefer.event_for_cycle`. The AST test bans `read_status(` /
  `read_rounds(` too.
- **Legacy (pre-`tagteam.yaml`) projects are visible.** The plan hid every
  dir without `tagteam.yaml`; on the real registry that would have hidden
  `sonicgrid/python-tests/ui`, whose `handoff-state.json` has had a
  reviewer turn owed since April. Rule now: no yaml **and** no state →
  hidden (`no-yaml`); no yaml but a state file → visible, kind `legacy`.
- **Saloon under a mount is not offered.** The Saloon's `app.js` talks to
  root-relative `/api/…` (a per-project server); under `/p/<id>/` only the
  cockpit is served (`?theme=saloon` still returns the cockpit) and the
  mounted cockpit hides its Saloon link. Standalone `/?theme=saloon` is
  unchanged.
- **Scratch filter is a parameter.** pytest's tmp dirs live under `/tmp` /
  `/private/var/folders`, i.e. inside the default scratch prefixes; the
  builders take `scratch_prefixes` so tests can disable the filter and
  cover it explicitly. Production callers use the defaults.
- **Content-hash signals.** The first version of `hub_signature` used
  size+mtime for the state file; a rewrite with equal size inside one
  filesystem timestamp tick was invisible (caught by the signature test).
  Small rewritten-in-place files (state, cycle status, registry) are now
  content-hashed; growing files (rounds, DB, WAL) keep size+mtime(ns).
- **`ui`/`python-behave` ages** are computed from `updated_at`, which is
  what the state records; both are `abandoned?` on the real registry (134
  d, 8 d) with the `tagteam watch --mode headless` hint.

## Verification

- `pytest`: **895 passed, 5 skipped**. New: `tests/test_hub_api.py` (14 —
  AST ban, read-only never-creates/never-migrates for v3/v4/v5, corrupt DB
  → row error, reader vs writer `BEGIN IMMEDIATE`, classification incl.
  legacy/missing/scratch, ranking + stale/abandoned boundary + live long
  turn, brief-ready, hidden/show-all, usage windows summed = `tagteam
  usage`, per-kind shared limits with competing timestamps + tie-break,
  every signature signal incl. DB-only write / registry edit / watcher
  without pidfile start+exit, text rendering), `tests/test_hub_server.py`
  (9 — page/payload/info/assets, registry byte-for-byte untouched by all
  read modes, options/help, two projects mounted concurrently: base meta +
  exact mounted asset URLs, reads against the right project, unknown id
  404, token+origin on mounts, pause A only, ruling through B's mount
  records in B only (rounds + diagnostics), per-mount SSE frames + per-mount
  cap, hub SSE fires on a DB-only write / state change / registry edit +
  heartbeat + cap, standalone page identical to 0.11.0 and legacy verbatim,
  base rewrite unit, registry CLI raw list / unregister only mutation).
- Existing `tests/test_server_cockpit.py` (25) and `test_cockpit_api.py`
  (32) unchanged and green after the router refactor.

## Dogfood (real registry, Chrome, 2026-08-16)

1. `tagteam hub --list` over the machine's registry (44 entries after
   registering this repo — `tagteam setup` here predated the registry):
   40 visible, 4 hidden (two `/private/tmp` scratch dirs, two no-yaml
   dirs), NEEDS YOU (0), WAITING (2) — `python-tests/ui reevaluate-old-
   python-tests impl r1 reviewer owed 134d22h ABANDONED?` and
   `northstar-test-automation/python-behave … impl r3 reviewer owed 8d06h
   ABANDONED?`, both with `→ tagteam watch --mode headless (nothing is
   dispatching)`; QUIET (38); payload in ~1 s.
2. **Hub page** — strip `40 projects · 1 live · 24h: 15,014,961 tok · $3.74
   · 7d: … · window: n/a`; Needs you empty state "Nothing needs you across
   40 projects. 2 turns owed to agents (2 stale)."; Waiting rows with the
   red `abandoned? · reviewer owed` badge, the CLI hint and **Open**;
   Quiet collapsed to `37`, expanded shows done/idle rows by last activity
   with `Open` on each. Registering this repo while the page was open
   fired the hub SSE (registry signal) and the strip re-rendered live
   (`1 live` = this repo's iterm2 watcher found by process scan; burn from
   its 26 usage rows). `window: n/a` because the only project with a
   `rate_limits` row is a hidden scratch dir — correct by design.
3. **Open → mounted cockpit** `/p/ui-b2e3df/`: base meta + assets under
   the mount, "← Hub" link, that project's Feed (an April `SUBMIT_FOR_
   REVIEW`), the Now strip `turn: reviewer · owed 134d…`, and the Needs-you
   cards "A turn is owed but nothing is dispatching" and "No tagteam.yaml
   yet" (legacy project) — the Saloon link is absent under the mount.
4. `--list --json`, `registry list` (kinds `ok` / `legacy` / `scratch`
   shown), and `/api/hub?all=1` all left `~/.tagteam/projects.json`
   byte-for-byte unchanged (verified with a checksum before/after in the
   tests and by hand on the real file).
