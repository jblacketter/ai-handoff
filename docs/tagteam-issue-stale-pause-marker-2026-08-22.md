> **Resolution (tagteam, 2026-08-22, branch `fix/stale-pause-visibility`):** suggestions 1, 3 and 4 shipped —
> `cycle init` / `cycle add` / `state set` print a `note: watcher dispatch is PAUSED (…)` line whenever the
> write hands a turn over while a marker is held; the watcher log and the note carry the marker's age, author
> and (for CLI pauses) the state it was set on; `tagteam cycle status` and `tagteam state` show a `dispatch:`
> line; the `/handoff` SKILL.md contract tells the lead what to do when the note appears. Suggestion 2
> (auto-expiring the marker) was deliberately **not** done: an arbiter pause must hold regardless of what the
> agents write, so visibility rather than expiry is the fix. Suggestion 5 could not be reproduced from the
> code: `_handle_done` sends one `/handoff` completion notice per state write and a restarted watcher on a
> `done` state only records-and-waits; if the repeated wake-ups recur on a current version, capture the
> watcher log.
>
> Original report below, verbatim from the aegis (QA) project.

# tagteam issue: a stale pause marker silently holds the next cycle

Observed 2026-08-22 on the `aegis` (QA) project, tagteam 3.7.0, watcher in
poll mode. Written by the lead session (claude) for Greg to share with the
tagteam project. Everything below was read from the installed package
(`tagteam/controls.py`, `tagteam/headless.py`, `tagteam/watcher.py`) and the
project's `.tagteam/` directory; nothing is guessed except where marked.

## What happened

1. **2026-08-18**: the previous lead session finished the roadmap run
   (Phase 24 approved, PR #45 open, state `done`, `turn: null`). The watcher
   kept waking `/handoff` on that done state (reported by that session; not
   re-verified here), so the lead ran
   `tagteam pause --reason "roadmap run complete; PR #45 open, no active cycle (paused by claude to stop repeated /handoff wake-ups; tagteam resume to continue)"`.
   That writes `.tagteam/headless-paused.json` (`PAUSE_RELPATH`), a marker
   with `{reason, by, source: "cli", ts}`. Nothing ever removed it.
2. **2026-08-22 21:35**: Greg started `tagteam watch`. The startup banner
   code does call `_log_paused(info, force=True)` when a marker exists, so a
   PAUSED line may have been printed above the excerpt Greg pasted; the
   excerpt begins at `[trigger] poll mode` / `Current state: done`. With
   `turn: null` there was nothing to dispatch, so the watcher sat quietly.
3. **21:45:46**: the lead opened a new cycle with
   `tagteam cycle init --phase input-border-contrast --type plan ...`. The CLI
   printed `Cycle created ... (round 1, ready_for: reviewer) + state updated`
   and said nothing about dispatch being paused.
4. **21:45:46**: the watcher saw `>> codex's turn` and, in `_handle_ready`,
   found the marker, logged `!! PAUSED: roadmap run complete; PR #45 open,
   no active cycle (...)` and declined to dispatch (remembering
   `_paused_seq` so `resume` re-dispatches once). It repeated the line every
   60 s. The reason text is four days old and now false: there IS an active
   cycle, and PR #45 has been merged.
5. The marker is gone now (`.tagteam/` mtime 21:47). The lead's own
   `tagteam resume` at about 21:47 printed `Not paused.`, so the marker was
   cleared a moment earlier, presumably by Greg following the log's hint
   (assumption). Per `watcher.py` the watcher should then log
   `Resumed — dispatching the still-owed turn` and send codex's `/handoff`.

Net effect: a pause set to quiet a *finished* run held the *next* run's
first turn, with a misleading reason, and the lead had no signal at the
moment it mattered (the `cycle init` hand-off).

## Why this is a design gap rather than an operator error

- The pause is **unbounded and unscoped**: one marker, no expiry, no phase or
  cycle binding, survives watcher restarts and days of idleness. A pause
  whose stated reason is "no active cycle" cannot notice that a cycle now
  exists.
- The **lead's write path gives no warning**. `tagteam cycle init` and
  `tagteam cycle add ... SUBMIT_FOR_REVIEW` are the moments the lead hands
  the turn over; both succeed silently while the watcher is held. The
  watcher's own log is the only place the pause is visible, and the lead
  (an agent in another pane, or a headless turn) does not read it.
- The `/handoff` skill (`SKILL.md`) never mentions the pause marker, so
  neither agent is told to check `tagteam resume` / the marker before
  handing off. There is no `tagteam status`-style CLI that reports it
  (`tagteam status` is "Unknown command"; the cockpit UI shows a `paused`
  badge, but the CLI workflow does not).
- The original trigger, if confirmed, is itself a bug: a watcher with no
  owed turn (`status: done`, `turn: null`, roadmap complete) should not be
  re-sending `/handoff`. The 08-18 session reached for `pause` as a
  workaround for that, which is how a long-lived marker came to exist.

## Suggestions (any one of the first three would have prevented this)

1. **Warn on hand-off while paused.** `cycle init` and `cycle add` (any
   action that sets `ready_for`) print a one-line notice when
   `read_pause(root)` is not None:
   `note: watcher dispatch is PAUSED since <ts> by <by> (<reason>); run
   tagteam resume or the reviewer will not be dispatched`. Same for
   `tagteam state set`.
2. **Scope or expire the pause.** Record the `seq`/phase/type at pause time
   and treat the marker as stale once a *new cycle* is initialised (or let
   `tagteam pause --until-next-cycle` opt into that); or give `pause` a
   `--ttl` and have the watcher clear (and log) an expired marker.
3. **Age the reason in the log.** `!! PAUSED (4d 15h ago, by claude): ...`
   makes a stale pause obvious at a glance; today the line reads as if it
   were just written.
4. **Expose it in the agent-facing workflow.** A `tagteam status` (or
   `tagteam cycle status`) field `dispatch: paused since ... / running`, and
   a line in the `/handoff` SKILL.md "Your turn" steps: check it before
   `cycle init`/`SUBMIT_FOR_REVIEW`.
5. **Fix the root cause.** Confirm whether the watcher re-dispatches on a
   `done` / `roadmap-complete` state with `turn: null`, and if so stop it;
   then no one needs to pause a finished run.

## Repro (any project)

```
tagteam pause --reason "finished run"        # marker written
# ... later, possibly days, possibly a watcher restart ...
tagteam cycle init --phase x --type plan --lead a --reviewer b --updated-by a --content "..."
# prints "Cycle created ... ready_for: reviewer" with no pause notice
# watcher: ">> b's turn" then "!! PAUSED: finished run" every 60 s
```

Expected: the lead learns at `cycle init` time that the reviewer will not be
dispatched, and/or the marker does not outlive the run it was set for.
