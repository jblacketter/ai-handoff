# Phase 46: Pause visibility — a held pause marker is announced where it matters (3.8.2)

## Status
- [x] Planning — no plan cycle: the arbiter asked for the implementation itself to be reviewed ("don't revert anything, just have it check your implementation", 2026-08-22); the design is this document, written alongside the code
- [x] Implementation (branch `fix/stale-pause-visibility`, PR #26)
- [x] Implementation Review (approved round 2, 2026-08-22; round 1: Codex — `cycle status` on a legacy-markdown cycle returned without the `dispatch:` line; fixed on the fallback path with unpaused + held-pause regression tests)
- [x] Complete — PR #26 merged; released as **3.8.2** (tag `v3.8.2`, PyPI 2026-08-22)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** make a held `tagteam pause` marker (`.tagteam/headless-paused.json`)
visible at the three places it matters and was invisible: (1) the **hand-off
write** — `cycle init`, `cycle add` (any action that leaves a `ready_for`),
`state set` (ready + lead/reviewer) print a `note: watcher dispatch is PAUSED
(<age> ago, by <who>): <reason>` line naming who will *not* be dispatched;
(2) the **watcher log** — `!! PAUSED (4d 15h ago, by claude): …` instead of
an un-aged reason that reads as if just written; (3) **status** —
`tagteam cycle status` and `tagteam state` gain a `dispatch:` line. `tagteam
pause` records the state it was set on (shown as `[set on old/impl r3, status
done]`) so a marker that outlived its run reads as stale, and says the hold
spans watcher restarts and new cycles. The `/handoff` SKILL.md contract
(package copy + this repo's copy) tells the lead what to do when the note
appears.

**Why:** field report from the aegis (QA) project on 3.7.0, CLI + three iTerm
tabs, poll-mode watcher — `docs/tagteam-issue-stale-pause-marker-2026-08-22.md`.
A pause set 2026-08-18 to quiet a *finished* run ("roadmap run complete; no
active cycle") silently held the *next* run's first turn on 2026-08-22:
`cycle init` said `Cycle created … ready_for: reviewer` and nothing else; the
watcher logged the four-day-old reason every 60 s in a tab the lead never
reads. The marker is unbounded by design (Phase 32: every mode honors it
until `tagteam resume`); the gap is that nobody who writes a turn can see it.
`controls.py` / `watcher.py` / `headless.py` are unchanged since 3.7.0, so
the gap is live on 3.8.1.

**Deliberately not done:** auto-expiring or cycle-scoping the marker
(suggestion 2 in the report). An arbiter pause must hold regardless of what
the agents write — visibility, not expiry, is the fix. Suggestion 5 (the
watcher re-sending `/handoff` on a `done` state) does not reproduce from the
code: `_handle_done` sends one completion notice per state write and a
restarted watcher on `done` records-and-waits; the doc says to capture the
watcher log if it recurs.

## Scope

In: `tagteam/headless.py` (three pure helpers), `tagteam/watcher.py` (one log
line), `tagteam/controls.py` (`pause` records state context + one output
line), `tagteam/cycle.py` (notice after init/add, `dispatch:` on status, two
small helpers), `tagteam/state.py` (notice after `state set`, `Dispatch:` on
`state`), both `SKILL.md` copies, `tests/test_controls.py`, the issue doc.

Out: any change to when the marker is written or cleared; the cockpit (it
already shows a `paused` badge); the headless engine's own pause-on-failure
path (it writes the same marker and is covered by the same display).

## Technical approach

- `headless.pause_age(info) -> str` — `"just now"`, `"12m"`, `"3h 05m"`,
  `"4d 15h"`, or `"?"` (missing/unparseable `ts`; naive timestamps treated as
  UTC). `headless.describe_pause(info) -> str` — `PAUSED (<when>, by <by>):
  <reason>` + `[set on <phase>/<type> r<N>, status <s>]` when the marker
  carries a `state` block. `headless.handoff_pause_notice(root, next_agent)
  -> str | None` — the two-line note, or None when not paused.
- `watcher._StateProcessor._log_paused` uses `describe_pause` (rate limiting
  unchanged).
- `controls.pause_command` adds `"state": {phase, type, round, status, turn,
  seq}` from `read_state` (best effort; absent when there is no state) and
  one more output line about the hold spanning restarts and new cycles.
- `cycle._print_pause_notice(next_agent)` after the `Cycle created` /
  `Round added` lines — init always (ready_for is always reviewer); add only
  when `status.ready_for` is truthy (APPROVE / ESCALATE / NEED_HUMAN leave
  nobody owed → silent). `cycle._agent_name_for(role)` maps role → configured
  name for the note; `cycle._dispatch_line()` for the `dispatch:` field
  (`not paused` | `PAUSED (…) — tagteam resume to release` | `unknown`). All
  three swallow exceptions — a display helper must never fail a write.
- `state._state_set` prints the notice when the resulting state is `ready`
  with a lead/reviewer turn; `state_command` (no args) appends `Dispatch:`.
- Nothing in the gate, the engine, or the marker's lifecycle changes. The
  on-submit gate still runs after the notice (order: write → notice → gate).

## Files

- `tagteam/headless.py`, `tagteam/watcher.py`, `tagteam/controls.py`,
  `tagteam/cycle.py`, `tagteam/state.py`
- `tagteam/data/.claude/skills/handoff/SKILL.md`, `.claude/skills/handoff/SKILL.md` (identical)
- `tests/test_controls.py` — `TestPauseVisibility` (8 tests)
- `docs/tagteam-issue-stale-pause-marker-2026-08-22.md` (report + resolution header)

## Success criteria

1. With a marker held, `cycle init`, `cycle add` that hands a turn over, and
   `state set --status ready --turn X` print the note naming the agent who
   will not be dispatched; closing writes and the unpaused case print nothing.
2. The watcher's `!! PAUSED` line carries the marker's age and author.
3. `tagteam pause` records the state it was set on; `describe_pause` shows it.
4. `tagteam cycle status` / `tagteam state` show `dispatch:` / `Dispatch:`.
5. Both SKILL.md copies describe the note and stay byte-identical.
6. Full suite green (gate run on submission); no behavior change to when
   dispatch is held or released (`TestWatcherPauseAllModes` unchanged).
