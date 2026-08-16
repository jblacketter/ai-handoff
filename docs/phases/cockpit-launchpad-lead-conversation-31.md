# Phase 37: Cockpit Launchpad & Lead Conversation (3.1)

## Status
- [x] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
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
   next phase, the launch mode (headless if configured, else interactive)
   and the project are inferred; the user names none of them.
4. **Recognition over recall + one primary action (Von Restorff)** — every
   card shows its buttons and the command they run; exactly one primary
   per state (Start · Send · Resume · Rule).

**Flow (idle project → talking → in a session → phase closed):**

```
0. `tagteam serve` (no flags) → cockpit for the cwd's project.
   Banner: "Tagteam cockpit — <name> — <phase · type · rN · state | no active cycle>
            → http://127.0.0.1:8080". Port held by tagteam? "8080 is serving
   <other project> — use --port 8081" and exit 2.                  [defaults, error prevention]
1. Needs you shows the START card (no cycle, or last cycle approved):
   "No cycle in progress. Next: <phase> (docs/roadmap.md)."
   primary [Start headless] — starts the watcher (headless, pidfile) and sends
      the lead "/handoff start <phase>" as the first Lead-panel message;
   secondary [Launch terminals] — `tagteam session start` (three tabs/panes,
      agents launched) + "paste into the Lead: /handoff start <phase>" [Copy];
   not set up (no tagteam.yaml / roadmap)? → the card says so: `tagteam quickstart`.
                                                                  [empty states, recognition]
2. LEAD panel (a Watch tab, promoted next to Feed): the transcript of your
   conversation with the lead; a composer at the bottom; Send is the one
   primary. Each message runs one lead turn (claude -p / codex exec, resumed
   session), streams live, ends with the lead's reply. The lead can run
   `tagteam …` itself (same tools as headless turns), so "/handoff start x",
   "please change y", "close out the phase" all work as in the terminal.
   Composer states: idle · sending (elapsed, [Cancel]) · lead busy on its cycle
   turn (rN) → Send disabled with why + [Interject instead].          [status, Doherty]
3. Watcher chip: "no watcher · [Start]" (headless if agents.*.headless/briefer
   configured, else notify) — same click-runs-CLI pattern.           [recognition]
4. Approved-cycle state: "Phase X approved · Next: Y [Start next phase]" plus the
   Lead panel for follow-up ("ship it", feedback).                    [hint, then out of the way]
5. Hub: idle/done rows get "Start next phase →" linking into that project's
   cockpit Start card (`/p/<id>/#start`); the hub itself stays read-only.
```

Deferred to Advanced: theme, host, port, max-sse, conversation retention.
Absorbed by inference: next phase (roadmap), launch mode, project (cwd),
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
- Port collision: before binding, probe `http://<host>:<port>/api/info`
  with a 300 ms timeout; if it answers with a tagteam identity (the
  cockpit/hub `info` payload has `app: tagteam` — added if missing), print
  `port <p> is already serving <project or 'the hub'> — use --port <p+1>`
  and exit 2; also catch `OSError` on bind with the same message shape. No
  `SO_REUSEADDR`-style sharing; the cockpit binds `127.0.0.1` (as today).

**B. Launchpad** (`cockpit_api.py`, `server.py`, `cockpit.html|css|js`)
- `start_payload(project_dir)`: `{needed: bool, reason, next_phase:
  {slug, title} | null, setup_ok: bool, launch_modes: [...], recommended:
  "headless"|"interactive", commands: {...}}` — `needed` when there is no
  state, no cycle, or the current cycle is approved/done; `next_phase` from
  `roadmap.get_incomplete_phases` (first not-complete); `setup_ok` = has
  `tagteam.yaml` and `docs/roadmap.md`; recommended = headless if
  `agents.*.headless` or `briefer.enabled` present, else interactive.
- Actions (all through `run_action` with dry-run preview, token +
  Origin checks, `by = web:<user>`, recorded in the Feed):
  - `watch.start` → spawns `tagteam watch --mode <headless|notify>
    --pidfile` detached (own process group, stdout/err to
    `.tagteam/watcher-<mode>.log`); refuses if a watcher already runs
    (`watcher_status`); returns pid.
  - `session.start` → `session.ensure_session(project_dir, launch=True,
    attach_existing=False)`; returns the backend + result; on `manual`
    returns the three commands for the card to display.
  - `phase.start` → sends `/handoff start <slug>` as a Lead-panel message
    (below) — the lead writes the plan and inits the cycle; with the watcher
    running, the loop takes it from there.
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
  turns `--resume <sid>`; Codex — `exec resume <thread_id>` when the
  installed CLI supports it (probe `codex exec resume --help` once, cached
  per server), otherwise each turn's stdin carries the transcript tail
  (last N messages, budgeted like `compose_prompt`) — labelled in the
  panel ("continuity: resumed session" / "continuity: transcript replay").
  `--resume`/`--session-id` stay *reserved* for user `args`; only the
  engine sets them.
- The first turn of a conversation is prefixed with a short header
  (project name, "you are the Lead; the handoff skill is
  `.claude/skills/handoff/SKILL.md`; current state one line") so `claude
  -p` behaves like the terminal lead; slash commands are enabled (we never
  pass `--disable-slash-commands`).
- **Lead lock.** A conversation turn writes the same `.tagteam/inflight.json`
  marker as a headless cycle turn, with `kind: "conversation"`, role
  `lead`, pid + creation identity; the headless engine already treats a
  live inflight as "a turn is running" (verified in impl; test) so the
  watcher does not dispatch the lead's cycle turn on top of it, and
  `cancel-turn` / `tagteam tail` / the cockpit's in-flight chip work
  unchanged. Conversely, if the lead's *cycle* turn is in flight, Send is
  disabled ("lead is on round N — wait, or Interject") — no queueing.
  Reviewer turns are unaffected. Pause does not block conversation turns
  (talking to the lead while dispatch is held is exactly what the arbiter
  wants) — stated in the UI.
- Persistence + accounting: `conversations` (id, created_at, provider,
  session_id, title, last_ts) and `conversation_turns` (conversation_id,
  n, ts, user_text, status, session_id, usage_row_id, log/events paths)
  tables — schema **v7**, additive; usage rows get `kind` (`turn` |
  `conversation` | `briefer`, nullable, default null = turn) so `tagteam
  usage` can split them (`--json` includes it; text roll-up adds a
  "conversation" line). Transcript files are the canonical human record;
  the DB indexes them.
- Endpoints (cockpit-mode only, token-guarded like all POSTs):
  `GET /api/lead` (list + active), `GET /api/lead/<cid>` (messages),
  `POST /api/lead/new`, `POST /api/lead/<cid>/send {text}` → `{turn_n}`,
  `POST /api/lead/<cid>/cancel`, SSE `GET /api/lead/<cid>/events`
  (assistant text blocks / tool-use summaries / done|error, heartbeat,
  same cap as `/api/events`). CLI twin: `tagteam lead "message"
  [--new] [--conversation ID]` (prints the reply; same engine) and
  `tagteam lead --list` — the terminal keeps parity and tests can drive it.
- Panel UI: **Lead** tab first in Watch (Feed second); transcript
  (user/lead bubbles, tool-use lines collapsed, timestamps), composer
  with Send (Cmd/Ctrl-Enter), states idle / sending (elapsed, Cancel) /
  busy (why + Interject link) / error (the CLI's message + log path);
  "New conversation" small; continuity label; deep link `#lead`.
- Security: loopback + page token as today; conversation turns run with
  the lead's headless permissions (`acceptEdits`, allowed tools) — the same
  trust the watcher already exercises; `--host 0.0.0.0` warning text
  extended to say the page can now run agent turns and launch processes.

**D. Hub** — idle/done rows: "Start next phase →" (`/p/<id>/#start`);
Needs-you rows unchanged; hub stays read-only (no new hub POSTs).

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
  both states; port collision: two servers → second exits 2 naming the
  first's project; bind `OSError` path.
- `start_payload`: no state / no cycle / approved / in-progress / setup
  missing / no incomplete phase; recommended-mode inference.
- actions: `watch.start` spawns detached + refuses when running (fake
  watcher via pidfile), `session.start` with backend `manual` returns
  commands (no terminals in CI), `phase.start` = a conversation send;
  dry-run previews for all; token/Origin refusal.
- lead chat with the existing fake agent (`tests/fixtures/fake_agent.py`
  gains a "chat" mode that echoes and, on the 2nd message, proves resume
  by referring to the 1st): new conversation → send → events stream →
  transcript.md + DB rows + usage row `kind=conversation`; resume argv
  (`--session-id` first, `--resume` after) asserted via the fake's argv
  log; Codex path with resume-unsupported → transcript replay on stdin;
  lead lock: send while a lead cycle turn is inflight → 409 with reason;
  a running conversation turn writes inflight `kind=conversation` and the
  watcher's headless dispatch skips (unit-level: the engine's inflight
  check) — plus cancel; conversation while paused allowed.
- `tagteam lead` CLI: send/list, exit codes, `--json`.
- schema v7 additive: v6 DB opens, columns nullable, `snapshot/restore`
  covers the new tables; `tagteam usage` splits kinds.
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
3. Port collision refused with the other project's name; `--theme saloon`
   identical to today's bare serve (test).
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

## Open questions for the reviewer
- **Q1** Codex-lead continuity: is `codex exec resume <thread_id>`
  reliable enough to depend on, or should the plan make transcript replay
  the only Codex path (simpler, more tokens)?
- **Q2** Lead lock policy while the lead's cycle turn is in flight: refuse
  Send (plan) vs queue the message for after the turn (more magic, more
  surprise). Plan refuses and points at Interject.
- **Q3** `phase.start` = a conversation message `/handoff start <slug>` (the
  lead writes the plan in that turn) vs a dedicated engine dispatch. Plan
  uses the conversation: it is what the arbiter would type, it is visible
  in the transcript, and it needs no new engine mode.
