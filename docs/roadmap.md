# Project Roadmap

## Overview
Tagteam - A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight.

**Tech Stack:** Python 3.10+, YAML configuration, Markdown templates, Textual (TUI)

**Workflow:** Lead / Reviewer with Human Arbiter

## Phases

### Phase 1: Configurable Agents Init
- **Status:** Complete
- **Description:** Create interactive init command for configuring AI agents and their roles
- **Key Deliverables:**
  - Interactive `python -m tagteam init` command
  - `tagteam.yaml` config file generation
  - Skills updated to read config at runtime
  - Getting started documentation

### Phase 2: Review Cycle Automation
- **Status:** Complete
- **Description:** Automate the back-and-forth review process with a single cycle document
- **Key Deliverables:**
  - `/handoff-cycle` skill for automated review cycles
  - Single cycle document format with status tracking
  - Auto-escalation after 5 rounds
  - Human input pause/resume capability

### Phase 3: Automated Agent Orchestration
- **Status:** Complete
- **Description:** File-based state machine and watcher daemon for automated turn-taking between agents
- **Key Deliverables:**
  - `handoff-state.json` state file with atomic read/write
  - `python -m tagteam watch` watcher daemon (notify + tmux modes)
  - `python -m tagteam state` CLI for viewing/updating state
  - `python -m tagteam session` tmux session management
  - `/handoff-cycle` skill updated with state file integration

### Phase 4: TUI Consolidation
- **Status:** Complete
- **Description:** Consolidate the gamerfy TUI into tagteam as a subpackage. Adds `python -m tagteam tui` command with `--dir` flag, first-time user setup via TUI dialogue, and sound effects.
- **Key Deliverables:**
  - `tagteam/tui/` subpackage with ASCII saloon scene, dialogue system, map widget
  - `python -m tagteam tui [--dir PATH]` CLI subcommand
  - First-time user flow with project scaffolding via TUI
  - `pip install tagteam[tui]` optional dependency
  - Sound effects bundled in package

### Phase 5: Template Variable Substitution
- **Status:** Complete
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - `tagteam/templates.py` module with `render_template()` and `get_template_variables()`
  - Variable substitution in 8 templates (`{{lead}}`, `{{reviewer}}`)
  - `setup.py` reads config and substitutes variables when copying templates
  - Generated docs reflect config when `setup` runs after `init`

### Phase 6: Migration & Advanced Features
- **Status:** Complete
- **Description:** Migration tooling for legacy projects, centralized config parsing with validation
- **Key Deliverables:**
  - `python -m tagteam migrate` command with auto-detection and backups
  - Centralized `tagteam/config.py` module (read, validate, get_agent_names)
  - Forward-compatible `model_patterns` schema field with overlap validation
  - Unit tests for config and migration modules

### Phase 7: Unified Command (Command Drift Fix)
- **Status:** Complete
- **Description:** Replace 10 skill files (~30+ subcommands) with a single `/handoff` command that auto-detects role and state. Fixes agent drift in long context windows.
- **Key Deliverables:**
  - Single `/handoff` skill file (<150 lines)
  - State-driven auto-dispatch (reads role + state, does the right thing)
  - Mandatory NEXT COMMAND output box on every response
  - Deprecation notices on old skill files
  - 3 commands total: `/handoff`, `/handoff start [phase]`, `/handoff status`

### Phase 8: Orchestration Fix
- **Status:** Complete
- **Description:** Fix the watcher daemon and tmux send-keys integration so agents automatically pick up tasks when it's their turn
- **Key Deliverables:**
  - `_log()` helper with `flush=True` for visible watcher output in tmux panes
  - Escape x3 + C-c input clearing (C-u doesn't work in TUI agents)
  - C-m submit instead of Enter (reliable across Claude Code and Codex)
  - Agent idle detection via `capture-pane` (waits for prompt before sending)
  - Universal text command instead of `/handoff` for cross-agent compatibility
  - Directory-based skill format (`handoff/SKILL.md` with YAML frontmatter)
  - `setup.py` copies directory-based skills alongside flat `.md` files
  - `session.py` creates 3-column layout with mouse mode and pane labels
  - `--dir` flag for `session start` to set working directory

### Phase 9: Dashboard & TUI Polish
- **Status:** Complete
- **Description:** Fix TUI bugs (GAMERFY naming, silent poll failures, status bar overflow), extract shared parser for TUI and web dashboard, improve web dashboard with escalation choices, phase map, and structured round display, split 46KB HTML into 3 files, add unit test coverage
- **Key Deliverables:**
  - Shared `tagteam/parser.py` used by both TUI and web dashboard
  - `GAMERFY_SOUND` → `HANDOFF_SOUND` rename with backward-compat fallback
  - TUI state poller: failure logging + `[STALE]` indicator
  - Status bar: action truncation (25 chars), `Round N` display (no `/5`)
  - Web dashboard split: `index.html` + `styles.css` + `app.js`
  - Web dashboard: escalation choice buttons, phase map, structured rounds
  - 39 new unit tests across 3 test files (parser, state_watcher, review_dialogue)

### Phase 10: Web Dashboard Redesign
- **Status:** Complete
- **Description:** Redesign from ASCII art to pixel art sprites with modern responsive layout, RPG dialogue system with typewriter effects, and JavaScript conversation engine
- **Key Deliverables:**
  - SVG pixel art sprites (Mayor, Rabbit, Clock, Saloon backdrop) via `sprites.js`
  - JavaScript dialogue engine with typewriter effect, portraits, conversation trees
  - Full-width banner + responsive card grid layout
  - CSS animations: pendulum swing, cuckoo pop-out, mayor glow pulse
  - Character reactions to state changes (color shifts)

### Phase 11: The Saloon — Interactive Character-Driven Setup & Monitoring
- **Status:** Complete
- **Description:** Transform the dashboard into a full saloon experience with three independently clickable characters (Mayor, Bartender, Watcher). First-time setup becomes a guided multi-character conversation. The new Watcher character provides agent monitoring and daemon control.
- **Key Deliverables:**
  - New Watcher character (pixel art sprite + portrait + dialogue)
  - All three characters independently clickable with domain-specific menus
  - Guided multi-character setup flow (Mayor → Bartender → Watcher)
  - Character glow system to guide users between characters
  - Watcher monitoring API (daemon status, tmux session control, log tails)
  - Setup state persistence (resume mid-flow)
- **Phase Plan:** `docs/phases/saloon-interactive-setup.md`

### Phase 12: Cycle Storage & CLI (Performance)
- **Status:** Complete
- **Description:** Replace markdown-based cycle documents with append-only JSONL rounds + JSON status files, updated via CLI commands. Eliminates repeated read/modify/write of markdown from the active handoff loop.
- **Key Deliverables:**
  - `tagteam/cycle.py` module (JSONL/JSON storage, CLI commands, centralized discovery)
  - CLI commands: `cycle init`, `cycle add`, `cycle status`, `cycle rounds`, `cycle render`
  - Format dispatcher in `parser.py` (JSONL first, legacy markdown fallback)
  - All consumers updated: `server.py`, `app.js`, `handoff_reader.py`, `review_replay.py`
  - SKILL.md updated to use CLI commands instead of manual file edits
  - Synthesized markdown view via `cycle render` for human readability
  - 56 unit tests across `test_cycle.py` and `test_parser.py`
- **Phase Plan:** `docs/phases/cycle-storage-cli.md`

### Phase 13: Unified State Command (Performance)
- **Status:** Complete
- **Description:** Merge `cycle add` and `state set` into a single command via `--updated-by` flag, cutting agent tool calls per handoff turn from 2 to 1.
- **Key Deliverables:**
  - `--updated-by` flag on `cycle init` and `cycle add` — auto-updates `handoff-state.json`
  - `_STATE_TRANSITIONS` table and `_update_handoff_state()` helper in `cycle.py`
  - Round-5 auto-escalation preserved in unified command path
  - SKILL.md updated to single-command flow for all agent actions
  - Regression test for round-5 escalation behavior

### Phase 14: Sharing Readiness
- **Status:** Complete
- **Description:** Make tagteam ready for wider sharing — simplify README (remove manual setup, promote automated), mark Saloon as WIP, improve watcher robustness (seq-based change detection, re-send watchdog, retry loop), add `session start --launch` for auto-starting agents
- **Key Deliverables:**
  - README restructured: single automated workflow, one-line manual mention, Saloon marked WIP
  - `config.py`: `get_launch_commands()` helper, `command` field validation, no-PyYAML fallback
  - `session.py` / `iterm.py`: `--launch` flag auto-starts agents and watcher
  - `watcher.py`: seq-based change detection, 5-min re-send watchdog for stuck `ready` states
  - Tests for new config helpers and launch behavior
- **Phase Plan:** `docs/phases/sharing-readiness.md`

### Phase 15: Onboarding Polish
- **Status:** Complete
- **Description:** Simplify the first-run experience by unifying `init` + `setup` + `session start --launch` into a single streamlined flow. Reduce the number of commands a new user needs to go from install to running their first handoff.
- **Key Deliverables:**
  - `quickstart` command: setup + init + session in one command
  - `ensure_session()` with idempotent behavior (tmux auto-attach, iTerm skip)
  - `needs_setup()` 3-point check, `run_init()` with TTY guard
  - README, HELP_TEXT, GETTING_STARTED all updated
  - 21 new tests
- **Phase Plan:** `docs/phases/onboarding-polish.md`

### Phase 16: Stale Handoff Diagnostics
- **Status:** Complete
- **Description:** Build structured logging and diagnostics for debugging stale handoffs.
- **Key Deliverables:**
  - `state diagnose` command with 7 diagnostic checks
  - Enriched history entries (phase, round, updated_by)
  - Seq mismatch side-channel logging (`handoff-diagnostics.jsonl`)
  - `--check-agents` for agent responsiveness via session discovery
  - History anomaly detection (oscillation, repeated escalations)
  - 19 new tests
- **Phase Plan:** `docs/phases/stale-handoff-diagnostics.md`

### Phase 17: Test Coverage & Isolation
- **Status:** Complete
- **Description:** Fix pre-existing test failures, add TUI test isolation.
- **Key Deliverables:**
  - Fixed `detect_agent_names()` — sorted glob, found-flags prevent overwrite
  - TUI tests skip gracefully via `pytest.importorskip("textual")`
  - Added `[tool.pytest.ini_options]` to pyproject.toml
  - Full suite: 228 passed, 3 skipped, 0 failed
- **Phase Plan:** `docs/phases/test-coverage-isolation.md`

### Phase 18: Saloon Production Ready
- **Status:** Complete
- **Description:** Polish the web dashboard to production quality.
- **Key Deliverables:**
  - User-visible error banner for failed API calls (app.js)
  - Exponential backoff polling (2s → 30s max, auto-recovery)
  - State POST validation — rejects unknown fields (server.py)
  - WIP banner removed from README
- **Phase Plan:** `docs/phases/saloon-production-ready.md`

### Phase 19: Public Onboarding
- **Status:** Complete
- **Description:** Make tagteam ready to share with the world. Simpler init prompts, shared handoff explainer in CLI + README, iTerm2 cold-launch fix, README trimmed to a single backend-neutral Quick Start using the `tagteam` console script, prominent post-quickstart priming box.
- **Key Deliverables:**
  - `init` prompts simplified to 2 questions (lead name, reviewer name) — no role prompt
  - `HANDOFF_EXPLAINER` printed once per quickstart path via `show_explainer` plumbing
  - `_ensure_iterm_running()` launches iTerm2 from a fully-quit state; window-count guard prevents duplicate windows
  - README rewritten with "How it works" section and backend-neutral Quick Start
  - `python -m tagteam` replaced with `tagteam` throughout docs
  - Backend-aware priming box (tab/pane/terminal) at end of quickstart
- **Phase Plan:** `docs/phases/public-onboarding.md`

### Phase 20: Tail-only reads (token efficiency) — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Track `last_round_seen` per agent so `tagteam cycle rounds` returns only new rounds since last read. With SQLite this is `WHERE round > ?` — trivially supported by the Phase 28 schema, no separate phase needed.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase A

### Phase 21: Round summary field (token efficiency) — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Writer emits a short `summary` on every round. Already present as a nullable column in the Phase 28 schema (`rounds.summary`).
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase B

### Phase 22: Structured round schema — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Native columns in the Phase 28 `rounds` table replace JSON-in-JSON. Decision/blockers/unresolved_threads/resolved should be added as columns when the production port lands.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase C

### Phase 23: Per-round files (optional, defer if 20–22 suffice)
- **Status:** Deferred (2026-05-03) — superseded by `--tail N` follow-up. See `docs/phases/per-round-files-experiment-findings.md` for the experiment writeup.
- **Description:** Original scope was to split each round into its own file. Token experiment on the rankr corpus (cl100k_base) showed savings come entirely from query shape, not storage shape — Phase 28's SQLite store already covers the storage side. Recommended follow-up: a `tagteam cycle rounds --tail N` flag (small CLI change). Per-round file splitting itself is not worth the complexity.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase D

### Phase 24: Event-driven watcher (optional polish)
- **Status:** Complete (2026-05-03) — shipped via `polish-pack-watcher-tokens-adopt`.
- **What shipped:**
  - `_StateProcessor` extracted from `watch()` so polling and event triggers share one processing path.
  - `tagteam/watcher_events.py` wrapping `watchdog.observers.Observer`, subscribed to `on_modified` + `on_created` + `on_moved` (atomic-rename of `.handoff-state.tmp` → `handoff-state.json` surfaces as a move on most backends).
  - 30s heartbeat as safety net for missed events on broken filesystems / NFS.
  - Runtime-failure fallback: if the observer raises (e.g. macOS FSEvents `SystemError: Cannot start fsevents stream`, or inotify `OSError(ENOSPC)`), the watcher logs the reason and drops back to poll mode instead of exiting.
  - `--poll` flag forces legacy polling for debugging.
  - `[event]` extras group in `pyproject.toml` (`pip install tagteam[event]` to enable; base install stays poll-only).
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase E; `docs/phases/polish-pack-watcher-tokens-adopt.md`

### Phase 25: Drift / out-of-sync audit — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Drift between `handoff-state.json` / `_status.json` / `_rounds.jsonl` is impossible by construction with a single SQLite store: `state` is a singleton row, cycle status is derived from the round log. The audit phase becomes a one-time migration check rather than ongoing work.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase F

### Phase 26: Workspace cleanup — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** `.tagteam/tagteam.db` *is* the workspace cleanup. Runtime state collapses to one gitignored file. The auto-rendered markdown export (`docs/handoffs/<phase>_<type>.md` written on every DB write, byte-identical to today's `tagteam cycle render` output per the spike) preserves git-visible audit history.

### Phase 27: Cycle health & stale-state detection — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** The user-facing health surface (`tagteam state health [--stale-days N]`) reduces to a few SELECT queries over the Phase 28 schema. Should ship as part of the production port, not as a separate phase — the queries are trivial once the DB exists.

### Phase 28: SQLite as canonical runtime store
- **Status:** Complete (2026-05-03) — shipped in 0.6.0. Step A (dual-write), Step B (auto-export + `migrate --to-step-b`), and Stage 2 (DB-backed runtime readers) all merged; Step B activated on this repo post-Stage-2. See `docs/phases/sqlite-spike-findings.md` for the original go/no-go writeup.
- **Description:** Move runtime state (handoff state, cycle status, rounds, diagnostics) from a constellation of JSON/JSONL files to a single SQLite database at `.tagteam/tagteam.db`. Auto-render a synthesized markdown view to `docs/handoffs/<phase>_<type>.md` on every write so PR-reviewable conversation history is preserved. Eliminates by construction the multi-file drift class of bugs that motivated Phases 25 and 27, absorbs Phases 20/21/22/26 as well.
- **Why this is its own phase, not just an implementation detail of 26:** It changes the *canonical* data store, not just its location. The 2.0 proposal stayed file-based by default; this phase is the explicit revisit, scoped to runtime state only (not the round log's role as audit artifact — the markdown render covers that).
- **Schema sketch:**
  - `cycles(id, phase, type, lead, reviewer, state, ready_for, created_at, closed_at)`
  - `rounds(cycle_id, round, role, action, content, summary, decision, blockers_json, unresolved_json, resolved, updated_by, ts)` — collapses Phases 21 and 22 into native columns
  - `state(singleton, turn, status, phase, type, round, run_mode, roadmap_queue, roadmap_index, command, updated_by, ts)`
  - `diagnostics(ts, kind, payload_json)`
- **Migration plan:**
  - Stage 1: `tagteam migrate --to-sqlite` builds `.tagteam/tagteam.db` from existing files; old files remain
  - Stage 2: Dual-write release — write to both files and DB, but read from DB. One release cycle of soak.
  - Stage 3: DB-only — files become opt-in export via `tagteam cycle render`
- **What this absorbs if it lands:**
  - Phase 20 (tail reads) — `WHERE round > last_seen`
  - Phase 21 (summary field) — a column
  - Phase 22 (structured round schema) — native columns
  - Phase 25 (drift audit) — mostly obsolete; drift impossible by construction
  - Phase 26 (workspace cleanup) — `.tagteam/tagteam.db` is the cleanup
  - Phase 27 (cycle health) — collapses to a few SELECT queries
- **What it does NOT absorb:** Phase 23 (per-round files — different concern), Phase 24 (event-driven watcher — orthogonal).
- **Pre-commit experiment:** Before scheduling Stages 2–3, run a small spike that builds the schema, ports `cycle add`/`cycle rounds` against it, and measures (a) write latency on a realistic round burst, (b) read latency for `cycle rounds` against a 100-round cycle, (c) the size of the diff between auto-rendered markdown and current `cycle render` output. Decision criterion: if the spike doesn't surface a blocking issue and the markdown render is byte-identical or trivially aligned, proceed. If the spike reveals real friction, fall back to executing 20–27 incrementally.
- **Open questions:**
  - Does the auto-rendered markdown cover the full set of git-visible properties Jack actually relies on (PR review, `git blame`, archaeology against historical commits)? Worth asking explicitly during the experiment.
  - How are schema migrations versioned and rolled forward? (Probably: `PRAGMA user_version` + migration scripts in `tagteam/migrations/`.)
  - Concurrent access between watcher daemon and CLI commands — WAL mode should handle it, but worth load-testing.

### Phase 29: Watcher mode auto-detection
- **Status:** Complete (2026-05-03).
- **Motivation:** During the first live two-agent loop on this repo, the watcher detected the turn flip but only posted a notification — Codex's tab sat idle until manually relayed. Original assumption was that iTerm2 send-keys infrastructure didn't exist. Investigation revealed it ALL existed: `iterm.write_text_to_session`, `watcher.send_iterm_command`, `is_agent_idle_iterm`, `wait_for_idle_iterm`, and `tagteam watch --mode iterm2` were all wired up. The actual gap was UX: `tagteam watch` defaulted to `mode=notify`, and `--mode iterm2` only worked when `.handoff-session.json` existed (populated by `session start --launch`, NOT by manually opening tabs).
- **What shipped:**
  - `_auto_detect_mode(project_dir)` in `tagteam/watcher.py`: returns `iterm2` if both lead+reviewer iterm session IDs exist in `.handoff-session.json`, `tmux` if the default tmux session exists, else `notify` with a helpful pointer to `tagteam session start --launch`.
  - `tagteam watch` CLI: when `--mode` is not explicitly given, calls `_auto_detect_mode` and logs the picked mode + reason on startup.
  - 6 new tests in `tests/test_watcher_auto_detect.py` covering each mode-detection branch + graceful fallback when `session_exists()` raises.
- **Final scope:** ~50 lines + tests, one session. Smaller than the original 50–80 estimate because the underlying send-keys plumbing was already done.
- **Followup discovered, not done:** if you manually open iTerm tabs (instead of letting `session start --launch` create them), there's no way to register them as the watcher's lead/reviewer panes after-the-fact. → Shipped as Phase 30 below.

### Phase 30: Session adopt for manually-opened iTerm tabs
- **Status:** Complete (2026-05-03) — shipped via `polish-pack-watcher-tokens-adopt`.
- **What shipped:**
  - `tagteam session adopt --lead <unique-id> [--reviewer <id>] [--watcher <id>] [--force]` writes `.handoff-session.json` in the same `{"backend": "iterm2", "tabs": {role: {"session_id": id}}}` schema that `session start --launch` produces, so all existing consumers (`get_session_id`, `_any_session_alive`, watcher auto-detect, `state diagnose`, server log-tail) work unchanged.
  - `tagteam session list-iterm` lists currently-open iTerm2 sessions so users can discover the unique IDs to pass to `adopt`.
  - Reuses the existing `iterm.session_id_is_valid()` helper for liveness checking — no parallel validator.
  - 11 new tests including a `test_watcher_auto_detect_picks_iterm2_after_adopt` regression guard that closes the Phase 29 → adopt loop.
- **Origin:** Phase 29 followup discovered while wiring up the live two-agent loop on this repo.
- **Phase Plan:** `docs/phases/polish-pack-watcher-tokens-adopt.md` (Sub-phase C)

### Phase 31: Headless Turn Engine (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-14 (round 3; all review turns ran headless). Release 0.8.0: `pyproject.toml` bumped; tag push follows a green Windows CI run.
- **Description:** Opt-in `tagteam watch --mode headless`: on turn flip, the orchestrator spawns the owed agent via its signed-in CLI (`claude -p --output-format stream-json` / `codex exec --json`) with a bounded context (skill contract + state + round tail + command) on stdin, streams structured events to `.tagteam/turns/<stem>.events.jsonl` and a human log `<stem>.log`, verifies the agent wrote the *expected* round (or new cycle for `/handoff start …`), and records per-turn token usage in the additive `usage` table (schema v3). Includes `cycle rounds --tail N`, `tagteam tail`, `agents.<role>.headless.{provider,executable,args}` config with structural argv validation, and failure handling (timeout / nonzero exit / no round → diagnostics + `.tagteam/headless-paused.json` + notification; no retries). Never auto-detected; flag-off runtime behavior unchanged from 0.7.1.
- **Also fixed:** `dualwrite.py` imported `fcntl` at module top — every cycle write failed on Windows since Phase 28; now uses `msvcrt.locking` on Windows. New `.github/workflows/tests.yml` runs the suite on ubuntu + windows.
- **Plan:** `docs/phases/headless-turn-engine-30-arc.md` (approved round 3). **Findings:** `docs/phases/headless-turn-engine-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 31

### Phase 32: Orchestration Controls & Usage Surfacing (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 3; every plan and impl review turn ran headless). Release 0.9.0 via PR from `phase-32-orchestration-controls`; tag after merge + green CI.
- **Description:** `tagteam pause`/`resume` (marker honored by every watcher mode; resume re-dispatches the owed turn once), `tagteam cancel-turn` (binds the recorded child PID to the recorded turn via same-source creation identities + parent pid before signalling; engine records outcome `cancelled`), `tagteam interject` (additive `interjections` table with provenance, `--to lead|reviewer`, cycle-scoped eligibility, exact-id delivery stamping on `ok`, `--list/--retire`; surfaced in headless prompts and on `cycle rounds`/`render`), `tagteam usage` (per-turn lines + by-role/by-cycle/totals, `--json`), cross-platform `notify()` (osascript / PowerShell toast → `msg` / notify-send; `TAGTEAM_NO_NOTIFY`), `--turn-retries N` gated on a content-sensitive recursive repo fingerprint AND a handoff fingerprint (fail closed on UNSUPPORTED), per-role `headless.timeout_minutes`, `tagteam rollback X.Y.Z [--yes]`. Schema v4 additive.
- **Plan:** `docs/phases/orchestration-controls-usage-surfacing-30-arc.md` (approved round 7; every review turn headless). **Findings:** `docs/phases/orchestration-controls-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 32

### Phase 33: Escalation Briefer (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 4; plan 6 rounds; all review turns headless). Release 0.10.0 via PR from `phase-33-escalation-briefer`; tag after merge + green CI.
- **Description:** On `escalated`/`needs-human` (canonical cycle status), the watcher runs ONE headless briefer turn per escalation event — event identity is a repair-safe key from the triggering entry; a pre-spawn claim row (schema v5 `briefs`, partial unique indexes) gives at-most-once automatic attempts and one running attempt per event across kinds — that writes `docs/escalations/<phase>_<type>_r<N>_<event>-a<attempt>.md` (Positions / Crux / Evidence / Recommendation / Rulings) under a hard 60k-char prompt budget. `tagteam brief` (current-event scoped, `--list`, `--generate`), `tagteam rule approve|request-changes|answer` (dedicated `add_ruling` path bypassing the stale gate; `answer` = interjection + `rearm`), grouped rounds gain `entries`/`rulings`, repair now preserves `usage`/`interjections`/`briefs`. **Opt-in** (`briefer.enabled: true`; absent = 0.9.0 behavior; §2 hard constraint overrides §4's "opt-out" wording).
- **Plan:** `docs/phases/escalation-briefer-30-arc.md` (approved round 6). **Findings:** `docs/phases/escalation-briefer-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 33

### Phase 34: Arbiter Cockpit (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 3; plan 3 rounds incl. a UX-design round). Release 0.11.0 via PR from `phase-34-arbiter-cockpit`; tag after merge + green CI.
- **Description:** `tagteam serve --theme cockpit` (or `serve.theme: cockpit`) serves a plain-JS cockpit organised by the arbiter's questions: a **Now** strip (state, owed turn + age, in-flight + tail drawer, hold, watcher, connection mode), **Needs you** (typed cards with one action each: escalation + brief + Approve/Request changes, question + Answer, hold + Resume, missing brief + Generate, stale in-flight/no watcher), and **Watch** tabs (Feed via SSE, per-file scope Diff, Usage with churn curve + rate-limit signal, Notes). Backend: `ThreadingHTTPServer`, per-run POST token + Origin check (cockpit mode only), read endpoints (`/api/now`, extended `/api/rounds`, `/api/interjections`, `/api/briefs`, `/api/brief/<id>|current`, `/api/usage`, `/api/scope-diff/<cycle>`, `/api/tail`), SSE `/api/events` (1 s sampler, heartbeat, `--max-sse` cap), action endpoints wrapping the Phase 32/33 command functions (`by = web:<user>`), `cycle.compute_scope_diff` extracted (CLI byte-identical), schema v6 `rate_limits` (latest Claude `rate_limit_event`, repair-preserved). **Bare `tagteam serve` is 0.10.0-identical** (Saloon, bind all, no token, new endpoints 404); the Saloon is served at `/?theme=saloon` in cockpit mode with a token-aware `tagteamFetch()`.
- **Plan:** `docs/phases/arbiter-cockpit-30-arc.md` (approved round 3). **Findings:** `docs/phases/arbiter-cockpit-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 34

### Phase 35: Cross-Project Hub (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-16 (round 2; plan 2 rounds, UX flow designed with the ux-design-guide skill). Release 0.12.0 via PR from `phase-35-cross-project-hub`; tag after merge + green CI.
- **Description:** `tagteam hub` — one read-only surface over every registered project: Needs you → Waiting (stale / abandoned? with the CLI hint) → Quiet, burn across projects and the shared subscription window (newest row per provider/kind), each project's cockpit mounted at `/p/<id>/` through a shared `CockpitRouter` seam (server-injected base-aware HTML), hub SSE over file signals + one shared process scan per tick, `tagteam hub --list [--json]`, `tagteam registry list|unregister`. Never migrates a project DB (`mode=ro`), never rewrites the registry (`read_registry_raw`).
- **Plan:** `docs/phases/cross-project-hub-30-arc.md` (approved round 2). **Findings:** `docs/phases/cross-project-hub-findings.md`.
- **Description:** `tagteam hub` — one surface over every registered project: waiting turns, pending escalations, stale cycles, aggregate token burn against the shared subscription pool.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 35

### Phase 36: Visual Story & Portfolio Feed (3.0 arc)
- **Status:** Implementation in review — plan approved 2026-08-16 (round 6; flow designed first with the ux-design-guide skill), impl cycle opened 2026-08-16 on branch `phase-36-visual-story`; ships as **3.0.0** via PR (docs + media only, behavior identical to 0.12.0).
- **Plan:** `docs/phases/visual-story-portfolio-feed-30-arc.md`. **Findings:** `docs/phases/visual-story-portfolio-feed-findings.md`.
- **Description:** README restructured around a visual narrative (mermaid flowcharts of the planning/review processes and 3.0 architecture), standalone SVG diagram exports in `docs/media/` shaped for the portfolio site's featured-app pages, and an outside-reader `docs/showcase.md` with real soak numbers. Portfolio repo itself is out of scope.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 36

## Backlog

### 3.0 candidate later phases (unscheduled)
- **Status:** Not started — deliberately kept out of the 3.0 arc to protect focus; see `docs/tagteam-3.0-proposal.md` §4 "Candidate later phases"
- Gatekeeper pre-checks (deterministic-first: tests + scope-diff before reviewer sees a submission)
- Reviewer panels (2–3 lenses merged into one `REQUEST_CHANGES`; opt-in per phase)
- Roadmap as DAG (`depends_on`, parallel phases in worktrees)
- Thin MCP server (revisit if the headless path proves insufficient for non-Claude agents)

### Licensing & attribution decision
- **Status:** Decided 2026-08-15 — **stay open, keep MIT.** `CITATION.cff` added and README License section now asks for a link back.
- **Context:** Repo was already public and MIT-licensed (`LICENSE`, `pyproject.toml`, PyPI classifier), and every release is on PyPI as an sdist, so going private would have hidden only *future* work, not retracted anything. Public provenance (commit history, release dates, handoff logs) plus MIT's attribution requirement is the real protection against uncredited copying, and public proof is the whole point of the portfolio plan.
- **Options considered and rejected:** Apache-2.0 (adds patent grant + "state your changes" clause — revisit only if stronger attribution language becomes necessary); AGPL/GPL (deters commercial forks but also the internal adoption that builds reputation); private or private-shared (kills portfolio value without retracting published versions).
- **Open follow-up:** bump `version` / `date-released` in `CITATION.cff` as part of each release (consider folding into the release checklist).

### Terminal.app backend (macOS, optional)
- **Status:** Not started
- **Motivation:** Terminal.app ships with every Mac, so a `terminal` backend would remove the iTerm2 install step for new macOS users. Default stays `iterm2` (richer scripting); Terminal.app is opt-in via `--backend terminal`.
- **Sketch:**
  - New `tagteam/terminal.py` mirroring `iterm.py` against Terminal.app's AppleScript (`do script`, `tell tab N of window M`)
  - Add `"terminal"` to `SUPPORTED_BACKENDS` and `_validate_backend()` in `session.py`
  - Extend `_parse_backend` / `ensure_session` dispatch
- **Known tradeoff:** Terminal.app has no stable session IDs — stale-session recovery must fall back to window+tab index tracking, which is more fragile than iTerm2's session-ID model. Expect the module to be less robust under user tab rearrangement.

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/handoff-phase` to check current phase
2. Use `/handoff-plan create [phase]` to start planning
3. Use `/handoff-status` for project overview
