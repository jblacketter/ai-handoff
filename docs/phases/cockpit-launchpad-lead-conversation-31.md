# Phase 37: Cockpit Launchpad & Lead Conversation (3.1)

## Status
- [x] Planning
- [x] In Review (round 2: launch-intent state machine, atomic lead-slot claim, composite idempotent Start, Codex resume argv + SSE replay/cursor, untrusted-content + id boundaries + turn lifecycle, bind-authoritative port rule, schema restore order; round 3: codex resume argv order verified against 0.147.0, persisted launch claim, existing marker keys retained + owner_token/kind, exclusive canary bind + quick-restart test; round 4: port-keyed Tagteam lease instead of the canary, fail-closed slot recovery, launch claim ownership + crash reconciliation + retry semantics, UX flow rewritten to the launch intent)
- [x] Approved (round 5)
- [x] Implementation
- [x] Implementation Review (approved round 4, 2026-08-16)
- [ ] Complete (release **3.1.0** via PR)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** make the cockpit the place a tagteam session *starts and is
driven from*, not only watched. Three things:

1. **Cockpit by default, honestly labelled.** Bare `tagteam serve` opens
   the cockpit (the Saloon moves behind `--theme saloon`); the CLI banner
   names the project and its cycle state; a port already held by another
   tagteam server is refused with the fix, never silently shadowed.
2. **Launchpad.** When nothing is in progress the cockpit's *Needs you*
   zone shows a **Start** card: the next roadmap phase (inferred), and one
   click launches either the interactive session (three terminals) or the
   headless watcher and hands the lead its first turn. The watcher chip
   gains a **Start** action. Every launch runs the same CLI as the terminal
   and is recorded like every other cockpit action.
3. **Lead conversation.** A **Lead** panel in the cockpit where the
   arbiter talks to the lead agent — brainstorm, plan, say `/handoff start
   <phase>`, and after implementation give feedback or close the phase —
   through the lead's own signed-in CLI, resumable across messages,
   streamed live, transcript on disk. It is the same lead, in the same
   project directory, with the same permissions and the same handoff
   skill as the terminal session; the difference is where you type.

**Why:** the arbiter's real workflow (Jack, 2026-08-16): "we start with
brainstorming discussions and planning interactively; then that plan is
the basis for the handoff; after implementation I still need to follow up
with the lead, offer feedback, or accept the cycle so we can close out the
phase." Today that conversation lives only in a terminal tab; the cockpit
answers *does anything need me?* but not *how do I begin?* or *let me tell
the lead something*. Walking through it as a user (2026-08-16): `tagteam
serve` on an idle project shows an empty Needs-you and an old Feed with no
way forward; the CLI said "Tagteam Dashboard", the tab said "Cockpit"; two
`tagteam serve` processes on one port shadowed each other silently.

**Depends on:** Phase 31 headless engine (provider adapters, spawn, usage,
inflight marker), Phase 32 controls, Phase 34 cockpit (`run_action`, SSE,
token model), Phase 35 hub. **Size:** large-medium. Branch
`phase-37-cockpit-launchpad`, PR at the end. **Release:** 3.1.0.

**Compatibility rule for this phase.** One deliberate default flip (bare
`tagteam serve` = cockpit) — called out in README/CHANGELOG-style notes and
covered by an updated test; everything else additive: schema v7 adds
nullable columns only; the watcher's turn semantics are unchanged for
existing modes; `tagteam serve --theme saloon` is byte-identical to today's
bare `serve`.

## UX design (flow first — `ux-design-guide`)

**Goal (arbiter's words):** "Open the dashboard for my project and either
see the live session or start one from here — and talk to my lead the way
I do in the terminal: brainstorm, plan, hand off, follow up, close out."

**Diagnosis.** *Onboarding / empty states* (idle cockpit dead-ends);
*visibility of status* (which project, which surface, is anything running,
what is next); *feature consolidation / consistent language* (Saloon vs
Cockpit vs "Dashboard"; the terminal has a capability the UI lacks);
*error prevention* (silent port shadow).

**Principles (4).**
1. **Empty states teach** — "no cycle in progress" is an invitation with
   the next phase and one primary action, never a blank.
2. **Visibility of system status** — project, cycle state, watcher, lead
   busy/idle, and "what happens when I click" (the exact CLI) are always
   on screen; the CLI banner says the same thing the page does.
3. **Smart defaults / Tesler** — bare `serve` gives the good surface; the
   *launch intent* (which phase, plan or impl, or nothing) is derived from
   the recorded state and the roadmap, headless is offered only when the
   two-role headless configuration actually validates, and the project is
   the cwd; the user names none of them.
4. **Recognition over recall + one primary action (Von Restorff)** — every
   card shows its buttons and the command they run; exactly one primary
   per state (Start · Send · Resume · Rule).

**Flow (idle project → talking → in a session → phase closed).** Every
Start surface renders the exact `launch_intent` (Scope B), never a guess:

```
0. `tagteam serve` (no flags) → cockpit for the cwd's project.
   Banner: "Tagteam cockpit — <name> — <phase · type · rN · state | no active cycle>
            → http://127.0.0.1:8080". Port held by tagteam? "8080 is held by
   tagteam cockpit for <other project> — use --port 8081", exit 2.  [defaults, error prevention]
1. Needs you shows the START card only when launch_intent has a command:
   - no state / no cycle      → "Start: <next actionable phase> — plan"
   - plan approved            → "Start: <same phase> — implementation"   (/handoff start <p> impl)
   - impl approved            → "Start: <next actionable phase after it> — plan"
   - a cycle in progress / escalated / needs-human / paused → NO Start card
     (Needs-you shows what it shows today; the intent's reason is in the strip)
   - roadmap exhausted / not set up → the card states the reason
     ("no actionable phase in docs/roadmap.md" / "run tagteam quickstart"), no button.
   Buttons: primary [Start headless] ONLY when HeadlessEngine.validate() passes
     (else it is absent and the validation reason is shown); it runs the composite
     launch: ensure watcher (headless, pidfile) + send the intent's command as the
     first Lead-panel message. [Launch terminals] (primary when headless is not
     offered): `tagteam session start` + "paste into the Lead: <intent.command>" [Copy].
                                                              [empty states, recognition]
2. LEAD panel (Watch tab, first): the transcript of your conversation with the
   lead; composer; Send is the one primary. Each message = one lead turn
   (claude -p / codex exec, resumed session), streamed live, replayable from a
   cursor. The lead can run `tagteam …` itself (same tools as headless turns),
   so "/handoff start x", "please change y", "close out the phase" work as in
   the terminal. Composer states: idle · sending (elapsed, [Cancel]) · lead busy
   on its cycle turn (rN) → Send disabled with why + [Interject instead] ·
   error (CLI message + log path).                                  [status, Doherty]
3. Watcher chip: "no watcher · [Start]" — headless only when validate() passes,
   otherwise notify — same click-runs-CLI pattern; [Stop] when identity verifies.
                                                                    [recognition]
4. Approved-cycle state renders the same intent: plan approved → "Plan approved ·
   [Start implementation]"; impl approved → "Phase X approved · Next: <next
   actionable phase> [Start]" or "no actionable phase left"; plus the Lead panel
   for follow-up ("ship it", feedback).                   [hint, then out of the way]
5. Hub: rows whose project has a launch intent get "Start →" linking into that
   project's cockpit Start card (`/p/<id>/#start`); the hub stays read-only.
```

Deferred to Advanced: theme, host, port, max-sse, conversation retention.
Absorbed by inference: the launch intent (state + roadmap), whether
headless is offered (`HeadlessEngine.validate()`), project (cwd),
provider/executable/args (from `agents.lead.headless`).

## Scope

### In

**A. Default + banner + port safety** (`server.py`, `cli.py`)
- `resolve_serve_options`: theme `None` → `cockpit`; `--theme saloon` is
  the legacy path (identical to today's bare serve; the existing
  "byte-identical to 0.10.0" test moves to `--theme saloon`).
- Banner: `Tagteam cockpit — <basename> — <phase · type · rN · state>` or
  `— no active cycle` (from `cockpit_api.now_payload`), then the URL. Saloon
  keeps its current banner.
- Port exclusion — **real bind + Tagteam port lease.** The listening
  socket keeps `allow_reuse_address` (immediate restarts as today) and the
  actual bind stays authoritative for *unrelated* occupants (`EADDRINUSE`
  → refuse, exit 2). Tagteam-vs-Tagteam exclusion — including the
  loopback-vs-wildcard shadow that a bind alone cannot see — comes from a
  **project-independent, port-keyed lease** `~/.tagteam/ports/<port>.json`
  (`{pid, ident, host, port, project, kind: cockpit|hub|saloon,
  started_at}`) claimed atomically (`O_CREAT|O_EXCL`, then fsync) before
  binding and removed on normal shutdown; a lease whose `pid` is dead, or
  whose recorded non-null `ident` definitively mismatches the live process,
  is stale and is replaced (logged); a live-but-unverifiable holder is
  treated as **held** (fail closed, message says so). *Guarantee:* two live
  Tagteam servers can never hold the same port number on this machine,
  regardless of bind host or start order — the loser prints "port <p> is
  held by tagteam <kind> for <project> (pid N) — use --port <p+1>" and
  exits 2 without binding. *Unrelated listeners:* the bind result decides
  for identical addr:port; for the wildcard/loopback shadow a pre-bind
  connect probe of the connectable address (wildcard → `127.0.0.1`,
  300 ms) refuses when something answers ("port <p> is in use on <addr>
  — use --port <p+1>"); the residual race between probe and bind is
  accepted for non-Tagteam processes and stated. The identity probe
  (`/api/info` → `{"app": "tagteam", "project": …}`, added to cockpit +
  hub info) is used only to enrich the message when no lease exists (e.g.
  a pre-3.1 Tagteam server). Tests (macOS/Linux/Windows): immediate
  same-port restart after normal shutdown (lease removed) and after a
  crash (stale lease, dead pid → recovered); two Tagteam servers,
  loopback-then-wildcard and wildcard-then-loopback → second refused by
  the lease naming the first; live-but-unverifiable lease → refused,
  fail closed; unrelated listener → generic message; lease + bind race
  (listener appears between probe and bind on the identical address →
  `EADDRINUSE` path, same message shape).

**B. Launchpad** (`cockpit_api.py`, `server.py`, `cockpit.html|css|js`)
- **Launch intent (state machine, one function, one consumer set).**
  `launch_intent(project_dir) -> {phase, type, command, reason} | {phase:
  None, type: None, command: None, reason}` computed from the state file +
  the canonical cycle status + the roadmap:
  | observed | intent |
  |---|---|
  | no state / no cycle | next actionable roadmap phase → `plan` → `/handoff start <p>` |
  | current cycle `plan` approved/done | **same phase**, `impl` → `/handoff start <p> impl` |
  | current cycle `impl` approved/done | next actionable phase **after** it → `plan` (the roadmap may still say "In progress" for the just-approved phase; it is skipped by name, not by status) |
  | ready / in-progress / escalated / needs-human / paused-after-failure | none — reason "a cycle is in progress (turn: X)" |
  | roadmap exhausted / no actionable phase | none — reason "no actionable phase in docs/roadmap.md" (never fabricate) |
  | setup missing (`tagteam.yaml` / `docs/roadmap.md`) | none — reason + `tagteam quickstart` |
  "Actionable" = roadmap disposition not terminal, where **terminal =
  Complete / ✅ Complete… / Absorbed / Deferred / Superseded** (normalized:
  emoji stripped, case-folded, prefix match on the status field), added
  to `roadmap.py` as `is_terminal_status()` and used by
  `get_incomplete_phases` too (fixes `tagteam roadmap queue` starting at
  Phase 20 on this repo — a regression test runs against a copy of this
  repo's real `docs/roadmap.md` status forms and asserts the queue starts
  at the first genuinely open phase). The Start card, the terminal-copy
  command and `phase.start` all consume the same intent object; the
  intent's `observed` sequence (`state.seq`, cycle stem + round + state)
  is echoed back with it and required by the launch operation (below).
- `start_payload(project_dir)`: `{intent, setup_ok, headless: {ok: bool,
  errors: [...]}, watcher: {...}, recommended: "headless"|"interactive",
  commands: {headless: [...], interactive: [...]}}`; `headless.ok` uses
  **`HeadlessEngine.validate()`** (both roles' config + executables) — a
  headless block or `briefer.enabled` is not proof; Start headless is
  offered only when it validates, otherwise Launch terminals is primary and
  the validation reason is shown.
- Actions (all through `run_action` with dry-run preview, token +
  Origin checks, `by = web:<user>`, recorded in the Feed):
  - `watch.start` → spawns `tagteam watch --mode <headless|notify>
    --pidfile` detached (own process group, stdout/err to
    `.tagteam/watcher-<mode>.log`); refuses if a watcher already runs
    (`watcher_status`); returns pid.
  - `session.start` → `session.ensure_session(project_dir, launch=True,
    attach_existing=False)`; returns the backend + result; on `manual`
    returns the three commands for the card to display.
  - `launch` (the primary Start, **one server-side composite operation**,
    idempotent): request carries the intent + `observed` sequence; the
    server revalidates intent and observed state immediately before
    acting; steps: (1) ensure watcher (start detached if none; after spawn
    wait ≤ 5 s for an identity-bound pidfile *or* early exit — an exit
    means "watcher rejected its config" and is reported, not "started");
    (2) claim the lead slot and send the intent's command as a Lead-panel
    message (a conversation turn). **Idempotency is persisted, and the
    lock stays short:** under `dualwrite.writer_lock` (a few ms) the
    server revalidates intent + `observed`, then inserts a `launches` row
    (schema v7: `id`, `key = sha256(intent.command + observed)` UNIQUE,
    `status ∈ pending|succeeded|failed`, `attempt` (starts 1),
    `owner_pid`, `owner_ident` (the server or `tagteam lead` process that
    holds the claim), `watcher_pid`, `watcher_ident`, `conversation_id`,
    `turn_n`, `created_at`, `updated_at`, `finished_at`, `error`,
    `partial` JSON) — the claim — and releases the lock **before** any
    side effect. Watcher spawn/readiness wait (≤ 5 s) and the conversation
    turn happen outside the lock; each step's reference is written to the
    row as soon as it exists (`watcher_pid`+ident after spawn,
    `conversation_id`/`turn_n` right after the turn row is created); the
    row is finalized `succeeded` or `failed` (reason + truthful `partial`,
    e.g. `watcher: started (pid N alive), lead: slot busy (r3)`).
    **Crash recovery:** on server start and whenever a request hits a
    `pending` row, if the row's `owner_pid` is dead (or its non-null
    `owner_ident` definitively mismatches) the row is reconciled: inspect
    the persisted references — is `watcher_pid` alive (identity-checked)?
    does the turn row exist and what is its status? — and mark the launch
    `failed` with that partial state; a live-but-unverifiable owner is left
    pending (fail closed, reported as such). Repeated / double-clicked /
    retried-after-response-loss POST with the same key: `pending` (owner
    alive) → 202 "in progress"; `succeeded` → 200 `{launched: false,
    existing: turn_ref}`; `failed` → the stored partial state; **`retry:
    true`** = an atomic `UPDATE … SET status='pending', attempt=attempt+1,
    owner=<me>` where `status='failed'` (no second insert under the UNIQUE
    key), after which only the *missing* steps run: an alive recorded
    watcher is reused (never a second one), an existing turn reference is
    returned (never a second `/handoff start`). Concurrent identical POSTs:
    exactly one inserts, the rest see the row. Tests: double-click,
    concurrent POSTs (barrier), retry after response loss, failed/partial
    then retry (no duplicate watcher/message), observed-state drift → 409,
    and crashes (owner killed) after claim/before watcher, after
    watcher/before turn, after turn creation/before finalization — each
    reconciled truthfully and retryable without duplication. `phase.start` alone
    (no watcher) is the same operation with `ensure_watcher=false` for the
    interactive path.
  - `watch.stop` (chip): SIGTERM the pidfile'd watcher only when identity
    verifies (same rule as `cancel-turn`).
- Start card UI in Needs-you (primary Start headless / secondary Launch
  terminals / Copy command), watcher chip Start/Stop, approved-state
  "Next phase" card. Empty Needs-you copy when a cycle *is* running stays
  "Nothing needs you".

**C. Lead conversation** (`tagteam/lead_chat.py` new, `headless.py`
adapters, `db.py` v7, `cockpit_api.py`, `server.py`, `cockpit.*`)
- Model: a **conversation** = ordered messages; each user message spawns
  one **lead turn** through the Phase 31 adapters (`build_argv` with the
  lead's `agents.lead.headless` provider/executable/args and the same
  least-privilege defaults) with the message on stdin, in the project
  cwd, streamed (`stream-json` / `--json`) to
  `.tagteam/conversations/<cid>/<n>.events.jsonl` + `<cid>/transcript.md`
  (append-only, human-readable). **Resume:** Claude — first turn passes
  `--session-id <uuid4>` (or captures `session_id` from the stream), later
  turns `--resume <sid>`; Codex — the installed CLI (0.147.0) has `codex
  exec resume [SESSION_ID] [PROMPT]` but no stable public contract, so:
  probe once per server (`codex exec resume --help` exit 0 + usage line),
  decide **before spawning**; when supported, a dedicated
  `build_resume_argv()` (adapter method) produces — parent options
  **before** the subcommand, because `--sandbox`, `-C`, `-c` are `exec`
  options and `codex exec resume <id> --sandbox …` fails with "unexpected
  argument" on 0.147.0 (verified) —
  `[exe, "exec", "--json", "-C", root, <sandbox/approval defaults +
  validated user args>, "--skip-git-repo-check", "resume", thread_id, "-"]`
  (prompt on stdin); `build_argv()` is not appended to. An exact parser
  smoke test runs `codex exec … resume --help` with that prefix when the CLI
  is installed (skipped otherwise) and the unit test asserts the same
  sandbox/approval policy tokens as the first-turn argv. If a resume invocation
  then fails (nonzero / no thread event), the turn is **failed and
  surfaced** with its log — never auto-replayed (it may already have used
  tools). Without resume support the turn's stdin carries the budgeted
  transcript tail (`compose_prompt`'s budget rules).
  `thread.started.thread_id` is persisted per turn; the panel labels
  continuity ("resumed session" / "transcript replay").
  `--resume`/`--session-id`/`resume` stay *reserved* for user `args`; only
  the engine sets them.
- The first turn of a conversation is prefixed with a short header
  (project name, "you are the Lead; the handoff skill is
  `.claude/skills/handoff/SKILL.md`; current state one line") so `claude
  -p` behaves like the terminal lead; slash commands are enabled (we never
  pass `--disable-slash-commands`).
- **Lead slot — an atomic claim, not a shared marker.** Today
  `HeadlessEngine._run_attempt()` writes `.tagteam/inflight.json` without
  checking it, so two spawners could overwrite each other's marker and
  later unlink the other's. New primitive in `headless.py`, used by
  headless cycle turns, conversation turns and the briefer alike:
  `claim_turn_slot(root, *, owner: str, kind, role, ...) -> Claim | Busy`
  and `release_turn_slot(root, claim)`. Under the project's cross-process
  **writer lock** (`dualwrite.writer_lock`, held only for the claim): read
  the marker; if present → `Busy(marker)` **unless the owner is
  definitively gone**: recover only when the owner `pid` is dead, or when
  the marker's recorded **non-null** identity definitively mismatches the
  identity of the live pid; a live pid whose identity cannot be looked up
  right now, or a legacy marker with no recorded identity, stays **Busy**
  (fail closed; the reason — "owner alive but unverifiable" / "legacy
  marker without identity" — is surfaced to the caller and the UI, and
  `cancel-turn` remains the human's tool). Recovery is logged. Otherwise
  write the marker **keeping the existing field
  contract exactly** — `stem`, `role`, `phase`, `type`, `round`,
  `started_at`, `log_path`, `events_path`, `pid` (child, set once
  spawned), `child_ident`, `watcher_pid` (the runner: watcher process, or
  the cockpit server / `tagteam lead` process for a conversation),
  `watcher_ident`, and the direct-parent relationship — plus the new
  fields `owner_token` (random per claim), `kind` (`cycle` | `conversation`
  | `briefer`), and for conversations `conversation_id`, `turn_n`. No
  reader is renamed: `cancel-turn` keeps binding `pid` + `child_ident` +
  `watcher_pid` + `watcher_ident` + parent; `tail`, `now_payload`, the hub
  payload and `watcher_status` read the same keys and additionally show
  `kind`. Tests exercise `cancel-turn`, `tail`, `now_payload`, the hub
  payload and `watcher_status` against both `kind=cycle` and
  `kind=conversation` markers. `release` re-reads under the lock and unlinks **only if
  the marker's owner token is ours**. `cancel-turn` binding is unchanged
  and works for conversation turns because the same fields are present.
  `watcher_status` learns `kind`: a conversation runner (or the cockpit
  server) is never reported as a watcher merely because it has a runner
  pid. Tests: a barrier race (thread/process pair: watcher dispatch vs
  Send) → exactly one child spawns, the loser gets `Busy` / HTTP 409, and
  the loser cannot erase the winner's marker; stale-owner recovery (dead
  pid; definitive identity mismatch); **fail-closed cases**: identity
  lookup unavailable for a live pid, and a legacy marker without identity
  fields → still Busy, neither the marker nor the owner token is replaced;
  cancel → marker removed only by the owner; briefer path unchanged.
- Policy: if the lead's *cycle* turn holds the slot, Send is refused with
  the reason ("lead is on round N — wait, or Interject") — no queueing;
  reviewer turns are unaffected; pause does not block conversation turns
  (stated in the UI).
- Persistence + accounting: `conversations` (id, created_at, provider,
  session_id, title, last_ts) and `conversation_turns` (conversation_id,
  n, ts, user_text, status, session_id, usage_row_id, log/events paths)
  tables — schema **v7**, additive; usage rows get `kind` (`turn` |
  `conversation` | `briefer`, nullable, default null = turn) so `tagteam
  usage` can split them (`--json` includes it; text roll-up adds a
  "conversation" line). Transcript files are the canonical human record;
  the DB indexes them. `conversations`, `conversation_turns` and
  `launches` join `NON_FILE_BACKED_TABLES` **in parent-before-child order**
  (`…, "conversations", "conversation_turns", "launches"`) so `snapshot_non_file_backed` /
  `restore_non_file_backed` (state repair) preserve them; tests: v6 → v7
  opens and migrates, repair round-trip preserves **all three tables** —
  including a populated `launches` row with its ownership (`owner_pid`,
  `owner_ident`), `attempt`, watcher/turn references and `partial` state —
  and the usage `kind` column, `mode=ro` hub reads still work.
- Endpoints (cockpit-mode only, token-guarded like all POSTs):
  `GET /api/lead` (list + active), `GET /api/lead/<cid>` (messages),
  `POST /api/lead/new`, `POST /api/lead/<cid>/send {text}` → `{turn_n}`,
  `POST /api/lead/<cid>/cancel`, SSE `GET /api/lead/<cid>/events`
  (assistant text blocks / tool-use summaries / status transitions /
  done|error, heartbeat, same cap as `/api/events`). **Replay contract:**
  every conversation event is appended to the retained events file with a
  monotonically increasing `id: <turn_n>:<seq>`; a subscriber sends
  `Last-Event-ID` (or `?after=<id>`) and the server first replays every
  retained event after that cursor from disk, then follows live output —
  so a fast turn that finishes between POST Send and the EventSource
  connecting is not lost, and a reconnect has no gaps and no duplicates.
  Tests: fast-finish (fake completes before subscribe) and reconnect
  mid-turn (cursor resume; exact event ids, no dupes). CLI twin: `tagteam lead "message"
  [--new] [--conversation ID]` (prints the reply; same engine) and
  `tagteam lead --list` — the terminal keeps parity and tests can drive it.
- Panel UI: **Lead** tab first in Watch (Feed second); transcript
  (user/lead bubbles, tool-use lines collapsed, timestamps), composer
  with Send (Cmd/Ctrl-Enter), states idle / sending (elapsed, Cancel) /
  busy (why + Interject link) / error (the CLI's message + log path);
  "New conversation" small; continuity label; deep link `#lead`.
- **Turn lifecycle.** `conversation_turns.status`: `running → ok |
  failed | cancelled` (terminal); a failed/cancelled turn releases the
  slot, keeps its log/events, is shown in the panel with the CLI's message
  and log path, and **never** writes the watcher's global pause marker
  (that marker is for the loop; a conversation failure is the arbiter's own
  turn). **Restart reconciliation:** on cockpit-server start (and on
  `tagteam lead --list`), any `running` row whose owner identity is not
  live is marked `failed (orphaned at <ts>)`, so Send is never permanently
  busy after a crash; the inflight claim's stale-owner rule covers the
  marker side.
- **Untrusted content + identifiers.** User messages, agent text, tool
  summaries, titles, errors and paths reach the DOM **only via text nodes
  / `textContent`** (never `innerHTML`; the panel renders monospace blocks
  built with `createElement`), because the page holds the privileged POST
  token; an agent reply containing `<img onerror>` / `<script>` must render
  as text — browser-level regression (Playwright) plus a JS-source test
  that the Lead panel module never assigns `innerHTML` from payload data.
  Conversation ids are server-generated (`c-<12 hex>`), validated by regex
  and looked up in the DB before any filesystem join; `../`, absolute
  paths and unknown ids → 404, never a path; transcript/event paths are
  resolved and asserted to lie beneath `.tagteam/conversations/`. Request
  body ≤ 64 KB, message ≤ 32 KB, `--list` paging; oversize → 413.
- Security model: loopback + page token as today; conversation turns run
  with the lead's headless permissions (`acceptEdits`, allowed tools) —
  the same trust the watcher already exercises; the `--host 0.0.0.0`
  warning is extended to say the page can now run agent turns and launch
  processes.

**D. Hub** — the hub payload calls the same `launch_intent()` per visible
project (read-only: state file, cycle status, roadmap) and carries
`intent` on the row; the row shows **`Start →`** (label
"plan" / "implementation" from `intent.type`) **only when `intent.command`
exists** — a done plan cycle links to that phase's implementation, a done
impl cycle to the next actionable phase; an exhausted, in-progress,
escalated, needs-human or paused project gets **no** launch link. The link
opens that project's cockpit Start card (`/p/<id>/#start`); the hub adds
no POSTs and stays read-only. Tests: hub payload + rendered row for
plan-approved (label implementation), impl-approved (next phase), roadmap
exhausted (no link), active cycle (no link).

**E. Docs** — README: "Watch and steer" → "Talk to the lead, launch, watch
and steer"; ladder ③ label "+ Cockpit & Hub / talk to the lead, launch /
watch and steer" (mermaid + SVG together — the drift test); HTW: new
`#lead` section (conversation model, lock, continuity, files), `#cockpit`
launch actions, `#serve` default flip + port rule; `docs/media/README.md`
+ a new seeded screenshot `cockpit-lead.png` (Start card + Lead panel; the
seed gains an idle project and a canned transcript — no live agent needed
for the capture); showcase untouched. Files table: `.tagteam/conversations/`.

**F. Tests** (`tests/test_lead_chat.py`, additions to
`test_server_cockpit.py`, `test_cockpit_api.py`, `test_docs_story.py`)
- serve default: bare → cockpit; `--theme saloon` byte-identical to the
  pre-flip bare page (the existing identity test retargeted); banner text
  both states; port exclusion per Scope A (lease: both orders,
  live-but-unverifiable → refused, stale → recovered, immediate restart
  after shutdown and after crash; unrelated listener → generic message;
  probe/bind race → `EADDRINUSE` path).
- `launch_intent` matrix (table above) incl. plan-approved → same-phase
  impl, impl-approved → next phase by name skipping the "In progress"
  just-approved entry, roadmap exhausted, terminal-status normalization
  against a copy of this repo's real roadmap (`✅ Complete`, Absorbed,
  Deferred forms; queue starts at the first open phase); `start_payload`
  with `HeadlessEngine.validate()` failing → Start headless not offered,
  reason shown, terminals primary.
- `launch` composite: idempotent on repeat (`existing`), concurrent
  identical POSTs → one watcher + one lead message (barrier), watcher
  early-exit reported as not started, partial state (watcher up, slot
  busy) → 409 with guidance, observed-state mismatch → 409 "state
  changed", retry after response loss, failed/partial then `retry: true`
  (atomic failed→pending, attempt+1, no duplicate watcher or message), and
  the three pending-launch crash windows — owner killed after claim/before
  watcher, after watcher/before turn, after turn creation/before
  finalization — each reconciled truthfully from the persisted references
  and retryable without duplication.
- actions: `watch.start` spawns detached + refuses when running (fake
  watcher via pidfile), `session.start` with backend `manual` returns
  commands (no terminals in CI), `phase.start` = a conversation send;
  dry-run previews for all; token/Origin refusal.
- lead slot: barrier race watcher-vs-Send (exactly one spawn; loser 409;
  loser cannot erase winner's marker), stale-owner recovery, owner-only
  release, cancel cleanup, `watcher_status` not fooled by a conversation
  runner.
- lead chat with the existing fake agent (`tests/fixtures/fake_agent.py`
  gains a "chat" mode that echoes and, on the 2nd message, proves resume
  by referring to the 1st): new conversation → send → events stream →
  transcript.md + DB rows + usage row `kind=conversation`; resume argv
  (`--session-id` first, `--resume` after) asserted via the fake's argv
  log; Codex resume argv (`exec --json -C root <policy>
  --skip-git-repo-check resume <id> -`) with the same sandbox/approval
  tokens as the first turn + parser smoke test when the CLI is present;
  resume-unsupported → transcript replay on stdin; resume-supported-but-
  failed → turn `failed`, surfaced, not replayed; `thread.started.thread_id`
  persisted; SSE fast-finish and reconnect (`Last-Event-ID`) with exact
  ids; conversation while paused allowed.
- lifecycle + boundaries: orphaned `running` row reconciled on server
  start; failed conversation turn does not write the pause marker; hostile
  agent reply (`<img onerror>`, `<script>`) rendered as text (Playwright)
  + JS-source assertion of no `innerHTML` from payload; conversation id
  validation (`../`, absolute, unknown → 404); path containment; 413 on
  oversize.
- `tagteam lead` CLI: send/list, exit codes, `--json`.
- schema v7 additive: v6 DB opens, columns nullable, `snapshot/restore`
  covers all three new tables (populated `launches` row round-trips with
  ownership/attempt/references/partial); `tagteam usage` splits kinds.
- docs: coverage test picks up `lead`; drift test for ③ label; new
  screenshot in the manifest + PNG safety.
- Windows: everything subprocess-based uses the same paths as headless
  (no pty); `session.start` on Windows returns the manual commands.

### Out
- Multi-arbiter / auth beyond the page token; remote access story unchanged.
- Talking to the *reviewer* from the cockpit (interject already covers it).
- Rich markdown rendering of the lead's replies beyond monospace blocks
  + collapsed tool lines (keep the Phase 34 visual language).
- Hub-owned launches (hub links into the cockpit; stays read-only).
- Changing the interactive (iTerm2/tmux) watcher's typing behaviour.

## Success criteria — in-cycle gates (all local)

1. Walk-through as a user (recorded in findings with screenshots from the
   seed): idle project → `tagteam serve` → Start card names the next phase
   → **Start headless** → watcher running (chip), Lead panel shows the
   `/handoff start` turn streaming → cycle appears in the Feed. Second
   path: **Launch terminals** on macOS opens the three tabs; on the seed's
   `manual` backend the card shows the three commands.
2. Lead conversation end-to-end with a real `claude -p` on this repo
   (dogfood, findings): brainstorm message → reply; second message resumes
   the session (the reply references the first); `tagteam lead --list`
   shows it; transcript file readable; usage row kind=conversation; the
   Feed unaffected. Send while a headless lead turn is in flight is refused
   with the reason; a conversation turn blocks the watcher's lead dispatch
   until it ends (test + one real observation).
3. Two live Tagteam servers can never share a port (lease; the loser
   names the holder's project); unrelated occupants refused by the bind /
   probe with the generic message; immediate restart works; `--theme
   saloon` identical to today's bare serve (test).
4. `pytest` green (macOS + CI Ubuntu/Windows); the flag-off statement
   holds: `--theme saloon` byte-identical; v6 DBs open under v7 with no
   behaviour change for existing turns; hub read-only tests unchanged.
5. Docs: README/HTW/manifest updated; ③ drift test green; CLI coverage
   picks up `tagteam lead`; findings record every gate.

## Post-approval checklist (not review gates)
Push (permission granted for this session) → PR → CI green → GitHub README
render → arbiter merges + tags `v3.1.0` → publish → PyPI → installed-wheel
check through `scripts/upgrade_smoke.py --python <3.1.0 venv>
--expect-version 3.1.0` (never bare `tagteam upgrade`).

## Resolved questions (round 1 → 2)
- **Q1** Codex continuity: probe before spawn; dedicated resume argv; a
  failed resume is surfaced, never auto-replayed; replay fallback stays.
- **Q2** Refuse Send while the lead's cycle turn holds the slot; Interject
  is the alternative. No queueing.
- **Q3** `phase.start` stays a visible conversation message — now inside
  the composite, idempotent `launch` operation.

## Round-5 changes (reviewer r4)
1. Scope D: the hub consumes `launch_intent` and shows `Start →` only when
   `intent.command` exists (plan-approved → implementation, impl-approved
   → next actionable phase; exhausted/active/escalated/needs-human/paused
   → no link); hub payload/render tests for those four states.
2. Persistence: repair round-trip preserves all three new tables including
   a populated `launches` row with ownership/attempt/references/partial;
   Scope F's launch-composite test list now includes the three crash
   windows and the retry transition.

## Round-4 changes (reviewer r3)
1. Port: the two-socket canary was unrealizable (a second non-reuse bind on
   the same addr:port fails in-process too — reproduced); replaced by the
   real bind (authoritative for unrelated occupants) + a project-
   independent, port-keyed Tagteam **lease** (`~/.tagteam/ports/<port>.json`,
   pid + identity, atomic create, stale recovery, fail-closed when
   unverifiable) held for the server lifetime; guarantees stated for
   Tagteam-vs-Tagteam and unrelated listeners; tests aligned.
2. Lead slot recovery fails closed: recover only on a dead pid or a
   definitive identity mismatch of a recorded non-null identity;
   live-but-unverifiable and legacy-without-identity stay Busy with the
   reason; tests prove neither marker nor owner token is replaced.
3. Launch claim gains owner pid/identity, attempt counter and per-step
   references; orphaned `pending` reconciled on start/request from the
   persisted references with truthful partial state; `retry: true` is an
   atomic failed→pending transition (attempt+1) that reruns only missing
   steps (reuse alive watcher, return existing turn); crash tests at the
   three points.
4. UX flow and principle 3 rewritten to render the exact `launch_intent`
   (plan approved → same-phase impl; impl approved → next actionable phase;
   in-progress/terminal → no Start; headless only when
   `HeadlessEngine.validate()` passes); Scope F duplicate/stale lines
   removed.

## Round-3 changes (reviewer r2)
The round-2 decisions are now in the normative sections (the earlier
submission had described them without the file being written). Plus:
1. Codex resume argv: parent options before the subcommand
   (`exec --json -C root <policy> --skip-git-repo-check resume <id> -`),
   verified against codex-cli 0.147.0; parser smoke test.
2. Launch idempotency persisted as a `launches` row claimed under a short
   writer-lock hold, side effects outside the lock, pending/succeeded/
   failed semantics for retries; tests for double-click, concurrent,
   retry-after-loss, failed/partial retry.
3. Marker keeps the existing keys (`pid`, `child_ident`, `watcher_pid`,
   `watcher_ident`, parent) + `owner_token`, `kind`, conversation fields;
   readers/binders enumerated and tested for both kinds.
4. Port exclusion via an exclusive canary bind — *superseded in round 4*
   (unrealizable); the listener keeps `allow_reuse_address` and the probe
   is only for the message, both retained.

## Round-2 changes (reviewer r1)
1. Launch-intent state machine (`launch_intent`) with the tested matrix
   and terminal-status normalization in `roadmap.py`; one intent consumed
   by card, copy command and launch.
2. Atomic lead-slot claim/release primitive under the writer lock with an
   owner token, stale-owner recovery, owner-only unlink, cancel-turn
   binding fields, `watcher_status` kind-awareness; race tests.
3. Composite server-side idempotent `launch` keyed on intent + observed
   state, `HeadlessEngine.validate()` gating, pidfile-or-early-exit wait,
   explicit partial state.
4. Codex `build_resume_argv`, probe-before-spawn, surface-not-replay on
   failure, thread_id persisted; SSE replay-from-cursor contract with
   fast-finish and reconnect tests.
5. Text-node-only rendering + hostile-markup tests, server-generated
   validated ids, path containment, size limits, turn lifecycle with
   restart reconciliation, no global pause from conversation failures.
6. Bind-authoritative port rule with normalized probe host, verified
   identity naming, generic message otherwise, three collision tests.
7. Schema: `NON_FILE_BACKED_TABLES` order parent-before-child; v6→v7 and
   repair-preservation tests.
