# Phase 43: Cockpit hardening — a legible cycle (3.7)

## Status
- [x] Planning
- [x] In Review (round 1)
- [x] Approved (round 1 — Codex: scope + Phase 44 split endorsed)
- [x] Implementation (branch `phase-43-cockpit-hardening`)
- [ ] Implementation Review (round 1: Codex — a row that goes running → terminal kept its "running" sort key and stayed on top; fixed: any row is re-inserted when its sort key changes, moved not rebuilt; behavioural node-driven regression test + source guard)
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** make a running handoff *unmistakable* in the cockpit. When a cycle
is in progress the Watch zone opens with a **Cycle** region — Lead and
Reviewer as two lanes with the turn visibly on one side — over a persistent
**Activity** log that lists every agent turn for the project (both roles'
cycle turns, gate runs, panel lenses, briefer, lead-conversation turns),
streams the running one live, and keeps each finished one on screen with a
named outcome (`finished · cancelled · failed · timed out · process gone`)
and a link to its log. Clicking **Start** answers within one refresh — the
Start card is replaced by the cycle region in its *starting* state instead of
lingering as if nothing happened. The Lead panel stops discarding the lines it
streamed: the tool activity of a finished turn stays under the reply as a
collapsed disclosure. The Now strip says what *kind* of turn is in flight
(cycle turn r2 · lead conversation · gate · panel) and what the last turn's
outcome was. The global SSE signal covers conversation turns, launches and
log growth, so the page moves when the engine does.

**Why:** the first real use of the cockpit for a handoff (`docs/cockpit-issues.md`,
2026-08-16): a running handoff was not legible as a handoff — activity showed
only in a window *inside* the Lead panel that collapsed when the turn ended;
after that there was no way to tell running from cancelled; and the reviewer
never appeared at all. Mechanically (mapped 2026-08-17): the only "running"
signal is `#chip-inflight`, driven by the single turn-slot marker, and it
renders `kind: cycle` and `kind: conversation` identically (`cockpit.js`
`renderNow`, ~163-172); the Lead panel's `.live` box exists only while
`t.status === 'running'` and `renderConversation` wipes and rebuilds the
transcript on the `end` event, replacing streamed lines with the reply
(`cockpit.js` ~787-806); the Start card disappears only when the lead's first
state write flips `launch_intent()` (`launch.py` ~135-138) — nothing on the
page reflects the pending `launches` row; the reviewer has **no region** —
its live output goes only to `.tagteam/turns/<stem>.log`, reachable through
the manually-opened, non-streaming tail drawer; `events_signature`
(`cockpit_api.py` ~646-734) omits `conversation_turns`, `launches` and log
growth, so several state changes push no SSE frame at all.

**Scoping call (arbiter to confirm in round 1).** The roadmap backlog said
to fold the saloon rethink (`docs/saloon-rethink.md`) into this phase. This
plan keeps Phase 43 to the *evidenced* problem — cycle legibility and
running/finished/cancelled honesty — and promotes the saloon rethink to
**Phase 44** (`Depends on: Phase 43`): the theme engine's "state → picture"
table needs exactly the per-role turn state and outcome vocabulary this phase
makes explicit and exposes (`/api/activity`, `now.turn_kind`,
`now.last_turn`). Building both at once would make a large phase larger and
mix a UX repair with a visual re-cast. If the arbiter prefers one phase, the
saloon items are appended here as scope §F and the size becomes large.

**Depends on:** Phase 34 cockpit (`/api/now`, SSE, `run_action`), Phase 37
launchpad + lead conversation (`launches`, `conversation_turns`,
per-conversation SSE), Phase 38 gates, Phase 39 panels (their DB rows are
read, not changed). **Size:** medium. Branch `phase-43-cockpit-hardening`,
PR at the end. **Release:** 3.7.0.

**Compatibility rule.** Additive only. No schema change (`SCHEMA_VERSION`
stays 9 — every source the activity view needs already exists: the in-flight
marker, `usage` rows per cycle turn, `conversation_turns`, `gates`,
`panels`, `launches`). No CLI change. Existing endpoints keep their shapes
(new keys only). The Saloon theme and the hub are untouched except that the
hub row gains nothing (out of scope). `tagteam tail` is unchanged; the
cockpit's tail *drawer* is replaced by the activity rows' log view (feature
consolidation — one home for "what is the agent doing").

## UX design (flow first — `ux-design-guide`)

**Goal (arbiter's words):** "When a cycle starts, make it unmistakable that a
*cycle* is running — not just the lead — and give me a persistent window into
the activity (both agents' turns, in-flight state, what happened last) that
does not collapse away."

**Diagnosis.** *Visibility of system status* (running / finished / cancelled
are not distinguishable; the reviewer is invisible; Start gives no
acknowledgment); *mental model* (the user's story is "Lead and Reviewer take
turns, the round counts up" — the page's story is "one slot is busy");
*feature consolidation* (agent activity lives in three places — the `.live`
box, the tail drawer, the Feed — none persistent); *Gestalt common region*
(nothing on the page *is* the cycle).

**Principles (4).**
1. **Visibility of system status** — at every moment the page shows: which
   role is on turn, whether an agent process is running and which kind, how
   long, and what the last turn's outcome was; every terminal state is
   *named*, never inferred from absence.
2. **Match the mental model** — render the cycle as two lanes with a turn
   token, the round number, and a timeline of turns; a lead *conversation* is
   drawn differently from a lead *cycle turn* because they are different
   things.
3. **One home for activity (feature consolidation)** — the Activity log is
   the single place agent output appears; the Lead panel keeps its transcript
   role and *links* to it for cycle turns; the tail drawer goes away.
4. **Doherty / acknowledgment** — Start answers within one refresh; a running
   row streams; a finished row stays.

**Flow.**

```
0. Idle project → Start card (Phase 37, unchanged).                    [empty state teaches]
1. Click Start → the POST returns 202; on the very next /api/now the Needs-you
   Start card is gone and the Watch zone's CYCLE region renders in state
   "starting: <phase> · plan — lead is running /handoff start <phase>" with a
   spinner and elapsed time. Source: the pending `launches` row (now.launch).
   If the launch fails (turn failed/cancelled/orphaned) the region says so with
   the error and log path and the Start card returns.                  [Doherty, status]
2. CYCLE region (top of Watch, always present while a cycle exists, a launch is
   pending, or a turn is in flight; absent otherwise):
     ┌ Lead: Claude ────────────┐  ●→  ┌ Reviewer: Codex ────────────┐
     │ on turn · running 1m12s  │      │ waiting                     │
     └──────────────────────────┘      └─────────────────────────────┘
     <phase> · plan · round 2 · owed to lead for 3m · last: reviewer turn r1 finished 4m ago
   The token (●) sits on the owed side; the lane whose agent process is
   running pulses; the kind of the running process is named (cycle turn r2 ·
   lead conversation · gate · panel lens · briefer).             [mental model, status]
3. ACTIVITY log (under the lanes): newest first, one row per turn:
     ▸ 23:14  reviewer · Codex · cycle turn r1 · running 0m41s  [log]   ← streams lines
     ▸ 23:11  gate     · GATE r1 · passed 3m02s                 [log]
     ▸ 23:07  lead     · Claude · cycle turn r1 · finished 2m10s [log]
     ▸ 22:58  lead     · Claude · conversation #3 · finished     [transcript]
   The running row is expanded and streams the tail of its log (SSE, cursor,
   replay on reconnect). When it ends the row stays, its status changes to the
   named outcome, and its streamed lines remain until the page is reloaded
   (after reload the row is rebuilt from the record with an [open log] view).
   Nothing in this list is ever rebuilt from scratch on a refresh — rows are
   keyed and patched.                            [one home, status, never collapses]
4. LEAD panel: a finished turn shows the reply and, under it, "activity
   (N lines)" collapsed — the streamed lines are kept, not discarded. While the
   lead is on a *cycle* turn the panel's gate line reads "Claude is on its cycle
   turn (round N) — see Cycle activity" with a link that focuses the running
   row.                                            [progressive disclosure, one home]
5. Terminal states use one vocabulary everywhere (strip, lanes, activity,
   Needs-you): running · finished · cancelled · failed · timed out · process gone
   · orphaned. Cancelled says who/when ("cancelled 23:12 (web:jack)") and the
   Hold card's Resume is the recovery.                       [recover, don't scold]
6. Global SSE: `events_signature` also covers conversation_turns (max id + status
   digest), launches (max id + status digest), the in-flight log's size, so a
   turn starting/ending or a log growing pushes a change frame.        [Doherty]
```

Deferred to Phase 44 (saloon rethink): sprites, theme packs, first-run beats.
Deferred to Advanced / out: hub "running" badges, mobile layout, activity
retention limits beyond a cap, per-row token cost.
Absorbed by inference: which lane pulses (`inflight.kind` + `role`), what
"last" was (newest terminal row), whether the region shows at all (state ∨
launch ∨ inflight).

## Scope

### In

**A. Activity read model** (`tagteam/cockpit_api.py`)

- `activity_payload(project_dir, limit=50) -> {"items": [...], "truncated": bool}`
  — a read-only merge, newest first, of:
  - the in-flight marker (`h.read_inflight`) → one `running` item (`kind`
    from the marker: `cycle | conversation | briefer | gate | panel`, `role`,
    `agent`, `stem`, `log_path`, `started_at`, `pid_alive`);
  - `usage` rows (one per finished cycle turn — `status` is the outcome
    string, `role/agent/phase/type/round/duration_ms/log_path`);
  - `conversation_turns` (`status ok|running|failed|cancelled`, `error`,
    `log_path`, `conversation_id`, `turn_n`);
  - `gates` and `panels` (status, round, stem, duration, result/decision);
  - `launches` (`pending|succeeded|failed`, only the pending/failed ones from
    the last 24 h — a pending launch is the "starting" state).
  Item shape (stable, documented in the module):
  `{id, source, kind, role, agent, phase, type, round, status, started_at,
  ended_at, duration_ms, log_path, detail, ref}` where `status` is normalised
  to the shared vocabulary `running · finished · cancelled · failed ·
  timed_out · process_gone · orphaned` (mapping table in the module:
  `ok→finished`, `timeout→timed_out`, `nonzero|no_round|spawn_failed→failed`,
  running marker with `pid_alive is False → process_gone`, conversation
  `error` starting `orphaned`→`orphaned`), and `ref` is what the UI links to
  (`{"log": stem}` or `{"conversation": cid, "turn": n}`). `id` is
  `<source>:<rowid|stem>`, so the client can key rows.
- `now_payload` gains `turn_kind` (the marker's `kind`, or null),
  `launch` (the pending/failed launch row for the current intent, or null:
  `{status, command, conversation_id, turn_n, error, ts}`), and `last_turn`
  (the newest *terminal* activity item, or null). No existing key changes.
- `events_signature` adds: max id + status digest of `conversation_turns`,
  max id + status digest of `launches`, and the size of the in-flight
  `log_path` (mtime+size, like the rounds file). One more `stat`, two more
  cheap `SELECT max(id), group_concat(status)` — measured in the test.
- `tail_payload` unchanged (CLI parity for `tagteam tail`).

**B. Log streaming** (`tagteam/server.py`, `tagteam/lead_chat.py` reuse)

- `GET /api/activity` → `activity_payload`.
- `GET /api/activity/log/<stem>/events` — SSE over the lines of
  `.tagteam/turns/<stem>.log`: `id` = byte offset, `Last-Event-ID` / `?after=`
  resume, `event: line` per line, `event: end` once the marker for that stem
  is gone **and** the file has been fully drained, heartbeat as the existing
  streams. Same 0.4 s server poll and the same `--max-sse` cap accounting as
  `/api/lead/<cid>/events`; the stem is validated against
  `^[A-Za-z0-9._-]+$` and resolved strictly under `turns_dir` (no traversal).
  Conversation turns keep their existing per-conversation stream — the
  activity row for a conversation turn subscribes to that.
- The lead-conversation SSE and the new log SSE share one line-poller helper
  (extract from `server.py:1016-1061`) so behaviour (ids, replay, end,
  heartbeat, cap) is identical.

**C. Cockpit UI** (`cockpit.html`, `cockpit.js`, `cockpit.css`)

- **Cycle region** `#cycle` at the top of the Watch zone: two lanes
  (`.lane.lead`, `.lane.reviewer`) with agent names from `now.agents`,
  per-lane state text (`on turn · running <age>` / `on turn · waiting for a
  process` / `waiting` / `starting …`), the token on the owed side, pulse on
  the lane whose process is running (`inflight.role` + `turn_kind`), a status
  line (`phase · type · round N · owed to <role> for <age> · last: <role>
  <kind> r<N> <outcome> <age> ago`). Region visible iff a phase exists in
  state, or `now.launch` is pending, or `now.inflight` is set. Built with
  `createElement`/`textContent` only.
- **Activity log** `#activity` under the lanes: rows keyed by item `id`,
  patched in place (`Map<id, row>`; a refresh adds/updates rows, never wipes
  the container). Running row: expanded, streaming from its SSE into a
  `.lines` box (`max-height` + auto-scroll, pause-on-hover). On `end` the
  row's status is patched from the next `/api/activity` and its `.lines`
  stay. Finished rows: collapsed header with `[log]` (fetches
  `/api/tail?lines=200` for that stem — new optional `stem=` query on
  `/api/tail`, else the existing behaviour) or `[transcript]` (switches to
  the Lead tab and scrolls to the turn). Cap 50 rows + "older turns:
  `tagteam tail`" hint.
- **Start → starting**: `renderNeeds` suppresses the Start card when
  `now.launch.status == "pending"`; the cycle region shows the starting
  state; on `failed` the Start card returns with the failure text under it.
- **Now strip**: `#chip-inflight` text includes the kind (`Claude · lead ·
  cycle turn r2 · 1m12s` / `Claude · lead conversation · 0m40s` / `gate ·
  0m12s`); a new `#chip-last` shows `now.last_turn` (`last: reviewer r1
  finished 4m ago`, red when cancelled/failed/process gone). The tail drawer
  and `#btn-tail` are removed; `#btn-cancel-turn` moves into the running
  activity row (same `/api/cancel-turn`, same confirm).
- **Lead panel**: `renderConversation` keeps a per-turn `lines` array in
  `LEAD` state (fed by the SSE `line` events) and, for a finished turn,
  renders "activity (N lines)" as a `<details>` under the reply/fail body
  instead of dropping them; the gate line for `slot.kind !== 'conversation'`
  becomes "<lead> is on its cycle turn (round N) — see Cycle activity"
  linking to the running row. The existing XSS guard block boundaries
  (`Phase 37: Lead panel` … `Live connection`) are preserved so the source
  guard in `tests/test_launchpad.py` still applies; the new cycle/activity
  block gets its own boundary comments and the same guard.
- **Outcome vocabulary** in one JS table (`OUTCOME_LABEL`), used by the
  strip, lanes, activity rows and the Hold card.

**D. Docs**

- `docs/how-tagteam-works.md` `#cockpit`: replace the "Now strip … tail
  drawer" bullet, add the Cycle region + Activity log bullets, the outcome
  vocabulary, and the `/api/activity` note; `#lead`: the retained-lines and
  "on its cycle turn" behaviour. `README.md` cockpit section: one paragraph
  + the zone list. `docs/cockpit-issues.md`: mark the 2026-08-16 entry
  resolved by Phase 43 (with what changed), keep the file as the running
  list. Screenshot `docs/media/screenshots/cockpit-cycle.png` captured from
  the seed (`scripts/showcase_seed.py` gains a running-turn fixture) — text
  and figure only; no new prose beyond that.

**E. Tests** (`tests/test_cockpit_api.py`, `tests/test_server_cockpit.py`,
`tests/test_launchpad.py`, `tests/test_docs_story.py`)

- `activity_payload`: merge order (newest first, running first among equal
  ts), every source represented, the normalisation table (each raw status →
  vocabulary), `process_gone` when the marker's pid is dead, `orphaned`
  from a reconciled conversation turn, cap + `truncated`, empty project.
- `now_payload`: `turn_kind`, `launch` (pending / failed / absent once
  succeeded), `last_turn` (null with no history; ignores the running item).
- `events_signature`: changes when a conversation turn starts/ends, when a
  launch row appears/finalises, when the in-flight log grows; unchanged
  otherwise; the added cost stays under the existing test's budget.
- Server: `/api/activity` shape + auth gating (GET is token-free like the
  other reads; 404 in legacy mode); log SSE — first lines, `Last-Event-ID`
  replay from a byte offset, `end` after the marker is released, heartbeat,
  `--max-sse` cap, stem validation (traversal → 400), unknown stem → 404;
  `/api/tail?stem=` resolves that stem only.
- Launch: after `POST /api/start/launch` returns 202, `now_payload().launch`
  is pending and `launch_intent` still has a command (the *client* hides the
  card) — and after the turn fails, `launch.status == failed` with the error.
- JS source guards (no JS harness in the repo — grep guards as today): the
  cycle/activity block contains no `innerHTML`; `#cycle`, `#activity`,
  `#chip-last` exist in `cockpit.html`; `btn-tail` no longer exists;
  `OUTCOME_LABEL` lists exactly the seven outcomes; the banned-phrase check
  in `test_docs_story.py` covers the new strings.
- Manual (recorded in the findings doc, not a gate): a Playwright/Chrome
  walk of Start → starting → lead cycle turn → gate → reviewer turn on the
  seed, with the reload-persistence check (rows survive reload with
  outcomes; lines do not).

### Out

- Saloon rethink / theme packs / first-run beats → Phase 44.
- Hub changes (a per-project "running" badge is Phase 44 or later).
- Any engine change: turn slot, outcomes, watcher, gates, panels are read as
  they are recorded today. No schema bump.
- Retaining streamed lines across reloads (the log file is the record;
  `[log]` re-reads it).
- Mobile / narrow layout; theming of the new region beyond the cockpit CSS
  variables already in use.
- Changing `tagteam tail`.

## Success criteria — in-cycle gates (all local)

1. **Start acknowledges within one refresh.** On the seed, after `POST
   /api/start/launch` returns 202, the next `/api/now` carries
   `launch.status == "pending"`, and the page (checked by the JS guard +
   the manual walk) shows the cycle region in the starting state with no
   Start card.
2. **The reviewer is visible.** While a reviewer cycle turn runs, the cycle
   region's Reviewer lane reads `on turn · running <age>` and the running
   activity row streams the reviewer's log without opening anything.
3. **Nothing collapses.** After any turn ends, its row remains with a
   named outcome and its streamed lines until reload; the Lead panel keeps a
   finished turn's lines under a disclosure. Verified by the source guards
   (rows patched by id, no container wipe in the activity block) and the
   manual walk.
4. **Outcomes are named and consistent.** Every terminal state renders from
   `OUTCOME_LABEL`; `now.last_turn`, the strip, the lanes and the activity
   rows agree; cancelled ≠ failed ≠ finished in every surface.
5. **The page moves when the engine does.** `events_signature` changes on
   conversation-turn start/end, launch appear/finalise, and log growth
   (tests), and the added signature cost is bounded (test).
6. Full suite green via the gate on submit; the XSS source guards still
   pass; `tagteam serve --theme saloon` and the hub are byte-identical in
   behaviour (existing tests).

## Post-approval checklist (not review gates)

- Branch `phase-43-cockpit-hardening`; PR; `scripts/release.py 3.7.0` after
  merge; roadmap status line; `docs/cockpit-issues.md` resolution note.

## Implementation notes (impl round 1)

What shipped is the plan, with these deviations / precisions — each
either forced by a fact found while building or a manual-walk finding:

- **Activity ids are stem-based.** A turn with a log stem is `turn:<stem>`
  whatever recorded it (in-flight marker, `usage` row, `gates` / `panels`
  row) so the running row and its later record are ONE row the client
  patches — the plan's `<source>:<rowid>` would have left a stale
  "running" row next to a new "finished" one. Non-stem items keep
  `<source>:<rowid>` (`conversation:<id>`, `launch:<id>`, `inflight:slot`).
  Duplicates are resolved server-side (a terminal record wins over a
  running marker). A launch that reached its lead turn is shown as that
  conversation row only (the turn carries the outcome and log); launch rows
  stay only for launches that never got a turn.
- **Log growth in the SSE signature is coarse:** `LOG_SIGNAL_STEP` = 8 KiB
  steps (`inflight.log_step`), not per byte — the running row streams its
  own lines; the global signal must move while the engine does without a
  full-page refresh per log line. Tested both ways (< one step → same id;
  ≥ one step → change).
- **A running row keeps its log stream open after its record lands** and
  closes on the stream's own `end` (marker gone + file drained): the record
  can arrive before the poller drains the last lines (seen in the walk —
  three lines lost when the stream was closed on the refresh). The server's
  `end` also flushes a final line that lacks its newline.
- **A running row whose record vanishes** (marker gone, nothing recorded —
  not something the engine does, but possible) is marked `orphaned · no
  outcome recorded` on the next refresh, never left "running".
- **Shared streams buffer and replay to late joiners** — the Lead panel and
  the Activity row for the same conversation read one EventSource; the
  registry keeps what a stream delivered (cap 5000) so whichever consumer
  registers second still gets the replay (seen in the walk: an empty row box
  next to a full Lead-panel box).
- **The Cycle region is also shown when there is any recorded activity**
  (not only phase ∨ pending launch ∨ in-flight) so "what happened last"
  never disappears — e.g. after a launch's lead turn finished but before a
  cycle exists.
- **`now.launch` derives the effective status** (the persisted row is
  finalised lazily by the launcher): pending → its lead turn (running →
  pending, ok → gone, failed/cancelled → failed with the turn's error, no
  turn + owner gone → failed "orphaned"); failed → only within 24 h and for
  the CURRENT intent; succeeded → null.
- Tests live in a new sibling module `tests/test_cockpit_activity.py`
  (46 tests: vocabulary table, every source + normalisation, marker merge /
  dedupe / same-id record, cap, `last_turn`, `now` keys, `launch_view`
  paths, signature sources + bounded cost, `tail?stem=` + `turn_log_path`
  traversal, `/api/activity`, log SSE stream / replay / partial-line drain /
  `end` / 400 / 404 / cap, the lead SSE on the shared poller, and the source
  guards) rather than spread over four existing files; the existing Lead-
  panel `innerHTML` guard in `tests/test_launchpad.py` still applies
  unchanged (the Phase 43 block sits *before* the Lead block and has its own
  guard). `tests/test_docs_story.py` gains the new screenshot.
- Seed: `scripts/showcase_seed.py` leaves a RUNNING reviewer turn on
  `demo-web` (a detached `sleep 3600` as the live pid — printed, kill when
  done — a log that keeps growing for ~1 min, a passed gate and three
  finished turns); `docs/media/screenshots/cockpit-cycle.png` captured from
  it (1280×800, no text chunks).
- Manual walk (Playwright, 1280×800, recorded here, not a gate): running
  reviewer turn → lanes/token/strip/streaming row; turn ended as `cancelled`
  and as `finished` → same row patched, lines kept incl. the final partial
  line; Start → *starting* (pending launch + running conversation: Start card
  gone, lead lane running, row + Lead panel share the stream) → turn `ok` →
  Start card back, Lead panel keeps `activity (4 lines)`, region stays with
  history; reload → outcomes persist, conversation lines replay from the
  retained events.

### Impl round 2 (reviewer r1)

- **Ordering after running → terminal.** `upsertActivity` re-inserts an
  existing row whenever its sort key (`running?|started_at|id`) changes —
  running → terminal, terminal → running, a corrected timestamp — by
  moving the same node (`insertBefore`), so its lines and stream stay with
  it and the container is never wiped.
- **Regression test** (`TestActivityLogBehaviour`): the real Phase 43 block
  is evaluated in `node` under a ~40-line DOM stub and driven through
  running → newer finished → the running one ends → a newer running one →
  a terminal → running flip; asserts the DOM order at each step, same node,
  lines intact, container size. Skipped when `node` is not installed (no JS
  harness in the repo; this is the one node-executed test). Verified to fail
  on the pre-fix JS. Plus a source guard on the re-insert.
