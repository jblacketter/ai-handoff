# Phase 35: Cross-Project Hub (3.0 arc)

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
   subscription window** (the newest `rate_limits` row across all projects
   — one pool, one truth), and the connection mode (Live / Polling).
   *[Visibility of status]*
2. **Needs you** — projects whose cycle is `escalated` / `needs-human`, or
   paused after a failed turn. One row: project (basename + parent),
   `phase · type · rN`, *why* (`escalated r3 · brief ready` / `question from
   reviewer` / `paused: turn timeout`), age, **→ Open** as the single
   primary action. Empty state: "Nothing needs you across N projects."
   *[Von Restorff, IA by intent]*
3. **Waiting** — turns owed to an agent, sorted by age; badges: `in
   flight`, `watcher`, `paused`; **stale** when owed > 30 min with no
   in-flight and no watcher, **abandoned?** past 24 h — each with the CLI
   hint (`tagteam watch --mode headless`, `tagteam resume`).
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
2. **`tagteam/hub_api.py` (new, pure)** — `project_summary(project_dir)`
   (reuses `cockpit_api.now_payload` pieces but **read-only and cheap**:
   state, cycle status, pause marker, inflight + pid liveness, watcher
   liveness, last activity, brief-ready flag), `hub_payload(projects, *,
   now, thresholds)` → `{groups: {needs_you, waiting, quiet, hidden},
   totals, rate_limit, ts}` with the ranking rules above,
   `aggregate_usage(projects, windows)` and `latest_rate_limit(projects)`.
   **Read-only DB access:** every hub read opens the project DB with
   `sqlite3.connect("file:…?mode=ro", uri=True)` and NEVER runs
   `_migrate` — the hub must not touch another project's schema (a v3
   project stays v3); missing tables/columns → nulls. Errors per project
   are captured into the row (`error`), never raised. §8 Q7: WAL readers do
   not block writers or vice versa; connections are opened per request and
   closed immediately; a test holds a writer connection with an open
   transaction while the hub reads.
3. **Hub server** (`tagteam/hub.py`, reusing `server.TagteamHTTPServer`
   and the cockpit handler): `GET /` hub page; `GET /api/hub` (payload);
   `GET /api/hub/usage?window=24h|7d|all`; `GET /api/hub/events` (SSE,
   signature = per-project cheap file signals — state seq, rounds file
   mtime, pause marker, inflight stem/pid alive, watcher pidfile alive —
   sampled every `--interval` (default 3 s), heartbeat, cap); **mounted
   cockpits** `GET|POST /p/<id>/…` → the Phase 34 handler for that project
   with all cockpit routes, the hub's per-run token, loopback default, same
   Origin check; `<id>` = a short stable slug from the registry path
   (basename + hash). Static assets shared (`/cockpit.css` etc. resolve
   both at root and under `/p/<id>/`).
4. **Cockpit base path** (`tagteam/data/web/cockpit.js|html`): one
   mode-agnostic change — a `<meta name="tagteam-base">` (absent → `""`,
   i.e. identical requests to today) prefixes every `/api/…` and asset
   URL; a "← Hub" link rendered only when the base is set. Regression: the
   standalone cockpit's requests are byte-identical (test asserts no
   base meta and root-relative URLs still work).
5. **Frontend** `hub.html|css|js` (plain JS; reuses cockpit.css tokens):
   top strip, the three groups + hidden toggle, rows with the primary
   **Open** link, staleness badges with CLI hints, empty states, Live /
   Polling indicator, SSE with polling fallback (`?nosse=1` as in 34).
6. **Registry hygiene** (`registry.py`, additive): `tagteam registry list |
   unregister PATH` CLI (thin, existing functions), and `hub` classifies
   entries: missing dir → hidden (registry already prunes), no
   `tagteam.yaml` → hidden, scratch paths → hidden by default. No format
   change.
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
  `HubHandler` (hub routes + per-project cockpit mounting; delegates to
  `server.make_handler(project_dir, mode="cockpit", token=…)` handler
  instances cached per project id; path rewriting `/p/<id>/x` → `/x`),
  `--list` text/JSON rendering.
- `tagteam/hub_api.py` — new, pure: `project_id(path)`,
  `classify_registry(paths)`, `project_summary(dir)`, `hub_payload(…)`,
  `aggregate_usage(…)`, `latest_rate_limit(…)`, `hub_signature(…)`,
  `read_only_connect(dir)`.
- `tagteam/server.py` — small: `make_handler` gains an optional
  `base_path` (injected as `<meta name="tagteam-base">` and used to strip
  the mount prefix), a `hub_link` flag; nothing else changes (legacy path
  untouched; cockpit standalone unchanged).
- `tagteam/data/web/cockpit.js|html` — base-path meta support + "← Hub"
  link (only when base set); `hub.html|css|js` new.
- `tagteam/registry.py` — `list`/`unregister` CLI glue in `cli.py`;
  `registry.py` unchanged in format.
- `tagteam/cli.py` — `hub` and `registry` dispatch + help.
- Tests: `tests/test_hub_api.py` (classification, ranking, staleness
  thresholds, read-only connect never migrates, per-project error
  isolation, aggregate usage windows, shared rate limit, signature),
  `tests/test_hub_server.py` (real server: `/`, `/api/hub`, SSE, mounted
  cockpit GET/POST with the hub token incl. a ruling through
  `/p/<id>/api/rule`, standalone cockpit unchanged, `--list` output,
  a writer holding a transaction while the hub reads), `tests/test_registry.py`
  additions.

### Ranking / staleness rules (pure, tested)
- **needs_you**: cycle state `escalated` | `needs-human`, or pause marker
  with `outcome` (failed turn). Sort: escalations first, then by age desc.
- **waiting**: state `ready|working` with `turn` set. Badges from
  liveness. `stale` = owed ≥ 30 min ∧ no in-flight ∧ no watcher;
  `abandoned` = owed ≥ 24 h. Sort: stale/abandoned first, then age desc.
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
- [ ] Hub reads are **read-only**: a v3/v4/v5 project DB keeps its
  `user_version` after the hub reads it (test); a project with a broken DB
  or unreadable state renders as a row with `error`, not a page error; a
  writer holding an open transaction does not block or corrupt hub reads
  (test).
- [ ] Hub server: `/` serves the hub with the token; `/api/hub` returns the
  payload; SSE emits a snapshot then a `change` within 2× interval of a
  state change in ANY registered project; cap → 503; polling fallback.
- [ ] Mounted cockpit at `/p/<id>/`: page + assets resolve with the base
  meta; every cockpit read works; POSTs require the hub token + Origin;
  a ruling made through `/p/<id>/api/rule` records exactly what the CLI
  records in THAT project (test); "← Hub" link present only when mounted.
- [ ] Shared subscription window: the strip shows the newest `rate_limits`
  row across projects; aggregate burn for 24 h / 7 d / all matches
  `tagteam usage` totals summed (test).
- [ ] UX contract (dogfood-recorded): Needs you empty state, a stale
  Waiting row with its CLI hint, Quiet collapsed with count, hidden
  toggle, Live/Polling indicator, Open → mounted cockpit → action → back.
- [ ] Docs + findings over the real registry; released as 0.12.0 via PR →
  merge → tag (CI green).

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
