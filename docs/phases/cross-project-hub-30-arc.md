# Phase 35: Cross-Project Hub (3.0 arc)

## Status
- [x] Planning
- [x] In Review (round 2: dispatch seam, non-mutating registry reads, exhaustive read-only + SSE contract, per-kind shared window, abandoned ⊂ stale)
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** `tagteam hub` — one surface over every registered project
(`~/.tagteam/projects.json`, which `registry.py` already maintains):
*what needs me across all of them*, *is anything stuck*, *how much am I
burning against the one shared subscription window*. The hub is a triage
list ranked by intent — **Needs you** → **Waiting** → **Quiet** — with the
project's cockpit (Phase 34) **mounted one click away** at `/p/<id>/`, so
acting on an escalation across N projects never requires starting a second
server. Plus a text mode (`tagteam hub --list [--json]`) for terminal users
and scripts.

**Why:** proposal §4 Phase 35 — "for a daily-driver on N projects, this is
the *what needs me* surface." The cockpit answers that question for one
project; the real registry today has 43 dirs, 21 with a handoff state, a
long quiet tail, two turns owed for days/weeks nobody noticed, and scratch
dirs registered as noise. The hub exists to make that state visible in one
glance and to make the two stuck ones impossible to miss.

**Depends on:** Phase 34 (cockpit builders, token/loopback model, SSE
pattern), `registry.py`. Source brief: `docs/tagteam-3.0-proposal.md` §4
Phase 35, §8 Q7.

**Size:** medium. Branch `phase-35-cross-project-hub`, PR at the end.
**Release:** 0.12.0.

---

## UX design (flow first — skill `ux-design-guide`)

**User + goal, in their words:** the arbiter running tagteam on several
repos: "across all my projects — what needs me right now, is anything
stuck, and how much am I burning?"

**Governing principles (4):**
- **IA by intent** — one list ranked by what needs the human, never by
  registry order or alphabet.
- **Visual hierarchy / Von Restorff** — at most one hot group; quiet
  projects recede to a count.
- **Progressive disclosure / breadth ≤ 2 clicks** — the hub is triage;
  detail and *action* live in the project's cockpit, one click away.
- **Tesler / smart defaults** — staleness thresholds, noise filtering
  (`/tmp` and missing dirs), the quiet collapse and the shared subscription
  signal are inferred, not configured.

**Flow:**
1. `tagteam hub` → `http://localhost:8090/`. **Top strip:** `N projects ·
   M live` (a live = watcher running or turn in flight), burn across
   projects (24 h / 7 d / all: tokens, cost where priced), the **shared
   subscription window** (the newest `rate_limits` row per `(provider,
   kind)` across all projects — one pool, one truth per window), and the
   connection mode (Live / Polling).
   *[Visibility of status]*
2. **Needs you** — projects whose cycle is `escalated` / `needs-human`, or
   paused after a failed turn. One row: project (basename + parent),
   `phase · type · rN`, *why* (`escalated r3 · brief ready` / `question from
   reviewer` / `paused: turn timeout`), age, **→ Open** as the single
   primary action. Empty state: "Nothing needs you across N projects."
   *[Von Restorff, IA by intent]*
3. **Waiting** — turns owed to an agent, sorted by age; badges: `in
   flight`, `watcher`, `paused`; **stale** when owed ≥ 30 min with no
   live in-flight and no watcher, **abandoned?** when stale for ≥ 24 h
   (never for a live long-running turn) — each with the CLI hint
   (`tagteam watch --mode headless`, `tagteam resume`).
   *[Smart defaults, Recover-don't-scold]*
4. **Quiet** — done / idle projects collapsed to a count ("31 quiet",
   expandable), sorted by last activity; scratch (`/tmp`, `/private/tmp`,
   `/var/folders`) and no-state dirs are hidden by default with a
   `show all` toggle. *[Progressive disclosure]*
5. **→ Open** — the project's **cockpit mounted by the hub** at `/p/<id>/`
   (same page, JS, per-run token, loopback default; the cockpit JS learns a
   base path from a meta tag). Ruling / pausing / interjecting is therefore
   two clicks from the hub, no per-project server. A "← Hub" link in the
   mounted cockpit's Now strip. *[Breadth vs depth; Feature consolidation:
   one Needs-you card per project, one home]*
6. `tagteam hub --list` prints the same triage as text (Jakob's Law for
   terminal users); `--json` for scripts. *[Recognition over recall]*

**Feedback contract (inherits Phase 34):** Live / Polling indicator; rows
re-rank on change without a full-page jump; each project row shows its
last activity time; a project whose files cannot be read shows a `?` row
with the reason (never a page error). **Empty / first-run:** no registry →
"No projects registered — `tagteam setup` in a project registers it";
registry but no state anywhere → the quiet list only.

**Glossary (same word everywhere):** *Hub* — the cross-project surface.
*Needs you / Waiting / Quiet* — the three groups. *Live* — watcher running
or a turn in flight. *Stale* — owed with nothing dispatching. *Open* — the
project's cockpit.

**Deferred (progressive disclosure):** cross-project *actions* from the
list (bulk pause etc.), registry editing in the UI (`tagteam registry
unregister` stays CLI), per-project burn charts (open the cockpit).
**Absorbed by defaults / inference (Tesler):** registry sweep, staleness
thresholds, noise filter, cockpit mounting, read-only DB access.

---

## Scope

### In Scope

1. **`tagteam hub` command** (`tagteam/hub.py` new; `cli.py` dispatch):
   `tagteam hub [--port N (8090)] [--host H (127.0.0.1)] [--registry PATH]
   [--interval S] [--max-sse N] [--all]` starts the hub server;
   `tagteam hub --list [--json] [--all]` prints the triage once and exits.
   Nothing changes for any other command; the registry file format is
   unchanged (**flag-off identical**: no new files, no behavior change
   unless `tagteam hub` runs).
2. **`tagteam/hub_api.py` (new, pure)** — `project_summary(project_dir,
   procs_snapshot)` built ONLY from non-migrating readers: `state.read_state`,
   `cycle.read_status`, `headless.read_pause` / `read_inflight`,
   `watcher.read_pidfile`, `procs.*` (via `cockpit_api.watcher_status`, which
   is DB-free), and `hub_api.read_only_connect(dir)` for the DB-derived bits
   (brief-ready for the current escalation event, usage totals, rate
   limits). It **never calls `cockpit_api.now_payload()`, `db.connect()`,
   or any aggregate that connects+migrates** — a lint-style test greps
   `hub_api.py` for `db.connect(` / `now_payload(` and fails if present.
   `hub_payload(projects, *, now, thresholds, procs_snapshot)` →
   `{groups: {needs_you, waiting, quiet, hidden}, totals, rate_limits,
   ts}` with the ranking rules below; `aggregate_usage(projects, windows)`;
   `shared_rate_limits(projects)` (per-`(provider, kind)`, see below).
   **Read-only DB access:** `read_only_connect(dir)` returns
   `sqlite3.connect("file:<db>?mode=ro", uri=True)` or `None` when the
   file is absent — it never creates `.tagteam/` or the DB, and NEVER runs
   `_migrate`; missing tables/columns → nulls. Errors per project are
   captured into the row (`error`), never raised. Tests: an absent DB stays
   absent after a hub read (no `.tagteam/` created); v3, v4, v5 DBs keep
   their `user_version` byte-for-byte; a project whose DB is corrupt renders
   a row with `error`. §8 Q7: WAL readers do not block writers or vice
   versa; connections are opened per request and closed immediately; a test
   holds a writer connection with an open `BEGIN IMMEDIATE` transaction while
   the hub reads and asserts the read completes with pre-transaction data.
3. **Hub server + the dispatch seam** (`tagteam/hub.py`, `tagteam/server.py`):
   `BaseHTTPRequestHandler` instances are per-request and enter handling in
   their constructor, so nothing caches or delegates to handler instances.
   Instead the cockpit's routing is factored out of `make_handler` into a
   **`CockpitRouter`** (in `server.py`): an object holding only immutable
   per-project context — `project_dir`, `token`, `max_sse`, `base_path`,
   its own SSE lock + active count — with `handle_get(h, parsed, path) ->
   bool`, `handle_post(h, path) -> bool`, `check_write_auth(h)`,
   `sse(h)`, all taking the live handler `h` and using `h._send_json` etc.
   `make_handler(...)` builds ONE router and its handler class delegates to
   it (standalone cockpit: behavior unchanged, `base_path=""`). The hub's
   `HubHandler` owns `{project_id: CockpitRouter}` (created lazily, cached
   — routers are context, not handlers) and for `/p/<id>/<rest>` strips the
   prefix and calls `router.handle_get(self, parsed, "/"+rest)` /
   `handle_post(self, "/"+rest)`. Isolation is by construction — each
   router has its own `project_dir` (every read/write resolves against it),
   its own SSE counter/cap, and never sees another mount's path — plus
   tests with **two projects mounted concurrently**: a POST to
   `/p/a/api/pause` pauses A only; a ruling through `/p/a/api/rule` records
   in A's DB/rounds only; SSE on `/p/a` and `/p/b` each receive their own
   change frames and their caps are counted per mount; a wrong/missing
   token on either mount → 403; `/p/<unknown>/…` → 404 JSON. The hub's
   own routes: `GET /` hub page; `GET /api/hub`; `GET /api/hub/usage?window=
   24h|7d|all`; `GET /api/hub/events` (SSE, Scope 3b); `GET /api/hub/info`.
   One per-run token for the hub and all mounts (embedded in every served
   page), loopback default, same Origin/Referer check, no `*` CORS.

   3b. **Hub SSE signature — exhaustive.** Every value `/api/hub` displays
   has a cheap change signal: per project — state file mtime+size,
   current-cycle rounds file mtime+size, pause marker presence, inflight
   stem/pid **and pid-alive**, watcher pidfile pid-alive, **watcher
   liveness from the process scan** (one `procs.list_processes` snapshot
   per tick shared by all projects — not N scans), and the **DB and its
   `-wal` file mtimes+sizes** (covers usage / rate_limits / briefs /
   interjections writes without opening the DB per tick); plus the
   registry file mtime (a project added/removed). Sampled every
   `--interval` (default 3 s), heartbeat, `--max-sse` cap. Belt and
   braces: the page also does a slow periodic snapshot refresh (30 s) in
   live mode. Tests: a DB-only update (a usage row inserted through a
   normal writer connection in project A) → change frame; a watcher process
   started and then exited **without a pidfile** (cwd = project) → change
   frames both ways; a registry edit → change.
4. **Cockpit base path — server-injected, base-aware HTML.** JS cannot
   retroactively prefix `<link href="/cockpit.css">` / `<script
   src="/cockpit.js">`, so `_get_dashboard_html(theme, token, base_path)`
   rewrites the page's root-relative asset URLs to `<base_path>/…` and
   injects `<meta name="tagteam-base" content="<base_path>">`; cockpit.js
   reads the meta (absent → `""`) and prefixes every `fetch`, `EventSource`
   and navigation URL (`/?theme=saloon` → `<base>/?theme=saloon`); a "←
   Hub" link renders only when the base is set. Assets are served **under
   the mount** (`/p/<id>/cockpit.css`) by the router's static branch — the
   exact mounted URLs are tested; root-level assets remain served by the hub
   for its own page. Standalone: `base_path=""` → no rewrite, no meta —
   the served `cockpit.html` bytes and every request URL are **identical to
   0.11.0** (test compares the served page to the packaged file with only
   the token meta injected, as today).
5. **Frontend** `hub.html|css|js` (plain JS; reuses cockpit.css tokens):
   top strip, the three groups + hidden toggle, rows with the primary
   **Open** link, staleness badges with CLI hints, empty states, Live /
   Polling indicator, SSE with polling fallback (`?nosse=1` as in 34).
6. **Registry: non-mutating reads** (`registry.py`, additive):
   `get_registered_projects()` prunes missing dirs and REWRITES
   `~/.tagteam/projects.json` on read, so the hub cannot use it. Add a
   public `read_registry_raw() -> list[str]` (no pruning, no write) and
   `registry_path()`; `hub`, `hub --list` and `tagteam registry list` use
   the raw read; `registry list` shows every raw entry with a marker
   (`missing` / `no tagteam.yaml` / `scratch` / `ok`) — the hub classifies
   the same way: missing dir → hidden (revealable with `--all`), no
   `tagteam.yaml` → hidden, scratch prefixes → hidden. `tagteam registry
   unregister PATH` is the ONLY mutation in this phase (existing
   `unregister_project`). Tests: `hub --list`, `hub --list --json`,
   `hub --list --all`, `/api/hub` and `registry list` leave the registry
   file **byte-for-byte unchanged** (including with a missing dir present);
   `unregister` changes exactly that entry. `get_registered_projects()`
   itself is untouched (upgrade/rollback keep their pruning behavior).
7. **Docs**: README "The Hub" section, roadmap, findings
   `docs/phases/cross-project-hub-findings.md` (dogfood over the REAL
   registry: the two stale projects surface in Waiting with correct ages;
   the scratch dirs hidden; opening this repo's cockpit from the hub and
   pausing/resuming; SSE across projects; `--list` output).
8. **Dogfood**: this machine's registry (43 dirs) during the impl cycle;
   a ruling on a scratch project's escalation made from the hub-mounted
   cockpit; both cockpits (standalone and mounted) exercised.

### Out of Scope (explicitly)
- Cross-project actions from the hub list (bulk pause/resume/interject).
- Registry editing UI; auto-discovery of projects not registered.
- Any change to per-project cockpit endpoints or the CLI's output.
- Remote / multi-user access (same local token model as Phase 34).
- Phase 36 visuals.

---

## Technical Approach

### Files
- `tagteam/hub.py` — new: `hub_command(args)`, `resolve_hub_options`,
  `make_hub_handler(registry_reader, token, …)` → `HubHandler` (hub routes;
  `{project_id: CockpitRouter}` cache of per-project context; prefix strip
  for `/p/<id>/…` and delegation to the router), `--list` text/JSON.
- `tagteam/hub_api.py` — new, pure: `project_id(path)`,
  `classify_registry(paths)`, `read_only_connect(dir)`,
  `project_summary(dir, procs_snapshot)`, `hub_payload(…)`,
  `aggregate_usage(…)`, `shared_rate_limits(…)`, `hub_signature(…,
  procs_snapshot)`.
- `tagteam/server.py` — refactor: cockpit routing extracted into
  `CockpitRouter(project_dir, token, max_sse, base_path, …)`;
  `make_handler` builds one router and delegates (standalone behavior and
  the legacy path unchanged — existing tests unmodified);
  `_get_dashboard_html(theme, token, base_path="")` rewrites asset URLs and
  injects the base meta only when `base_path` is non-empty.
- `tagteam/registry.py` — additive: `read_registry_raw()`, `registry_path()`.
- `tagteam/data/web/cockpit.js|html` — base-path meta support + "← Hub"
  link (only when base set); `hub.html|css|js` new.
- `tagteam/registry.py` — `list`/`unregister` CLI glue in `cli.py`;
  `registry.py` unchanged in format.
- `tagteam/cli.py` — `hub` and `registry` dispatch + help.
- Tests: `tests/test_hub_api.py` (classification, ranking, stale /
  abandoned incl. the boundary + live long turn, read-only connect never
  migrates / never creates, absent DB stays absent, per-project error
  isolation, aggregate usage windows, per-kind shared rate limits with
  competing timestamps, signature: DB-only update, watcher without pidfile
  start/exit, registry edit; the no-migrating-call grep),
  `tests/test_hub_server.py` (real server: `/`, `/api/hub`, SSE, two
  projects mounted concurrently — isolation of reads/writes/SSE/auth,
  ruling through `/p/<id>/api/rule` records in that project only, exact
  mounted asset URLs, standalone cockpit page + requests identical to
  0.11.0, `--list` output, writer-holds-transaction read), `tests/
  test_registry.py` additions (raw read; byte-for-byte unchanged across
  all read/list modes; `unregister` only mutation).

### Ranking / staleness rules (pure, tested)
- **needs_you**: cycle state `escalated` | `needs-human`, or pause marker
  with `outcome` (failed turn). Sort: escalations first, then by age desc.
- **waiting**: state `ready|working` with `turn` set. Badges from
  liveness. `stale` = owed ≥ 30 min ∧ no live in-flight ∧ no watcher;
  **`abandoned` = stale ∧ owed ≥ 24 h** (a refinement of stale — a
  demonstrably live long-running turn or a running watcher is never
  labelled abandoned, whatever its age). Sort: abandoned, stale, then age
  desc. Tests: owed 29 m 59 s vs 30 m; owed 25 h with a live in-flight
  process → waiting, not stale; owed 25 h with nothing → abandoned.
- **shared subscription window**: `rate_limits` holds one current row per
  `(provider, kind)` per project (e.g. `five_hour` and `seven_day`), so
  the hub takes the **newest row per `(provider, kind)` across projects**
  (tie-break: later `ts`, then registry order); payload `rate_limits:
  [{provider, kind, status, resets_at, ts, project}]`, all kinds shown in
  the strip. Test: project A newer for `five_hour`, project B newer for
  `seven_day` → both selected from their respective projects.
- **quiet**: everything else with a state (done/approved/aborted/idle).
  Sort by last activity desc.
- **hidden**: no `tagteam.yaml`, no state file, scratch path prefixes,
  unreadable dir (with reason). `--all` / `show all` reveals them.

### Security model
Same as the cockpit: loopback default, one per-run token for the hub and
every mounted cockpit, `X-Tagteam-Token` on every POST, Origin/Referer must
match, no `*` CORS. `--host` opts into exposure with the same README
warning. The hub itself has no write endpoints of its own.

### Implementation order
0. `hub_api.py`: read-only connect + `project_summary` + classification +
   ranking + tests (incl. the writer-lock/WAL test and "never migrates").
1. `tagteam hub --list [--json]` over the real registry (text mode first —
   it validates the ranking on real data before any UI).
2. Cockpit base-path meta + server `base_path` (standalone regression
   test).
3. Hub server: `/`, `/api/hub`, `/api/hub/usage`, SSE, mounted cockpits
   + tests.
4. `hub.html|css|js`.
5. Registry CLI glue; docs; dogfood on the real registry; findings; bump
   0.12.0; PR.

---

## Success Criteria

- [ ] **Flag-off identical:** no command other than `tagteam hub` /
  `tagteam registry` changes behavior; the registry file format is
  unchanged; the standalone cockpit's requests are unchanged (no base
  meta, root URLs) — regression tests.
- [ ] `tagteam hub --list` over a registry of mixed projects prints the
  three groups with correct membership, ages and stale/abandoned flags;
  `--json` returns the documented shape; hidden entries listed with
  `--all`.
- [ ] Hub reads are **read-only and non-mutating**: an absent DB stays
  absent (no `.tagteam/` created); v3/v4/v5 project DBs keep their
  `user_version` byte-for-byte; `hub_api.py` contains no `db.connect(` /
  `now_payload(` call (grep test); a broken DB / unreadable state renders
  as a row with `error`; a writer holding an open transaction does not
  block or corrupt hub reads (test); every hub read/list mode leaves
  `~/.tagteam/projects.json` byte-for-byte unchanged (test).
- [ ] Hub server: `/` serves the hub with the token; `/api/hub` returns the
  payload; SSE emits a snapshot then a `change` within 2× interval of a
  change in ANY registered project — including a DB-only write and a
  watcher starting/exiting without a pidfile (tests); cap → 503; polling
  fallback; the page refreshes its snapshot every 30 s in live mode.
- [ ] Mounted cockpits at `/p/<id>/` via `CockpitRouter`: page served with
  base-aware asset URLs + base meta, the exact mounted asset URLs resolve;
  every cockpit read works; POSTs require the hub token + Origin; two
  projects mounted concurrently are isolated (reads, writes, SSE slots,
  auth) — a ruling through `/p/a/api/rule` records exactly what the CLI
  records in A only (test); `/p/<unknown>/` → 404; "← Hub" only when
  mounted; the standalone cockpit page and requests are identical to
  0.11.0 (test).
- [ ] Shared subscription window: newest row per `(provider, kind)` across
  projects with the documented tie-break (test with competing timestamps
  for two kinds); aggregate burn for 24 h / 7 d / all matches `tagteam
  usage` totals summed (test).
- [ ] Staleness: `abandoned` ⊂ `stale`; boundary and live-long-turn cases
  tested.
- [ ] UX contract (dogfood-recorded): Needs you empty state, a stale
  Waiting row with its CLI hint, Quiet collapsed with count, hidden
  toggle, Live/Polling indicator, Open → mounted cockpit → action → back.
- [ ] Docs + findings over the real registry; released as 0.12.0 via PR →
  merge → tag (CI green).

## Decisions (round 1)
- Mount cockpits under the hub via a shared `CockpitRouter` seam (not
  handler instances); server-injected base-aware HTML; assets served under
  the mount. Non-mutating registry reads (`read_registry_raw`). Read-only,
  never-migrating DB access with an enforced no-`db.connect` rule in
  `hub_api.py`. Exhaustive SSE signature incl. DB/WAL mtimes and process
  liveness from one shared scan per tick. Newest row per `(provider,
  kind)` across projects. `abandoned` ⊂ `stale`. Defaults kept: port 8090,
  30 min / 24 h, hidden scratch / no-yaml / missing entries with `--all`.

## Open Questions (recommendations)

1. **Mount cockpits under the hub (`/p/<id>/`) vs. link out to
   per-project `tagteam serve --theme cockpit` servers.** Recommend
   **mount**: acting is 2 clicks from the hub with one server, one token,
   one loopback story; the alternative needs the user to start N servers
   or the hub to spawn them. Cost: a base-path meta in the cockpit JS
   (small, regression-tested).
2. **Default port 8090** (cockpit 8080) so both run side by side.
3. **Staleness thresholds** 30 min (stale) / 24 h (abandoned?) —
   defaults only, `--stale-after`/`--abandoned-after` flags if wanted;
   the plan recommends shipping the defaults without flags first
   (Tesler) and adding flags on demand.
4. **Registry noise**: hide `/tmp`, `/private/tmp`, `/var/folders` and
   no-`tagteam.yaml` dirs by default. Recommend yes, with `--all` /
   `show all`.

## Risks
- **Reading N DBs each tick** — SSE signature uses file signals only (no
  DB); DB reads happen on `/api/hub` requests and are short-lived,
  read-only; 43 projects measured during dogfood.
- **Cockpit base-path regression** — the standalone cockpit is the more
  common path; a test asserts identical requests when the meta is absent.
- **Path rewriting for mounted cockpits** — one prefix strip in one place
  (`HubHandler`), covered by GET/POST/SSE tests through the mount.
- **Registry entries that are not tagteam projects** — hidden by
  classification, never crash the page.
