# Phase 33 — Escalation Briefer: Findings

## Plan cycle (6 rounds, all reviewer turns headless — with two incidents worth keeping)

| Round | Outcome | Wall |
|---|---|---|
| 1 | REQUEST_CHANGES (4) | 99.3 s |
| 2 | REQUEST_CHANGES (4) | 193.8 s |
| 3 | REQUEST_CHANGES (4) | 122.5 s |
| 4 | REQUEST_CHANGES (4) | 85.0 s |
| 5 | REQUEST_CHANGES (3) | 117.7 s |
| 6 | APPROVE | (interactive Codex; the headless duplicate was cancelled at 52 s — see below) |

**Incident 1 — a headless *lead* answered a plan round autonomously.** Before round 6 the
interactive lead's edit script failed and its `tagteam resume` ran anyway; the watcher
re-dispatched the owed turn — the lead's — and a headless Claude (`claude -p`, 210 s) read
round 5's feedback, edited the plan, committed, and submitted round 6. Its work was
reviewed by the interactive lead and kept as-is (it independently chose the same fixes,
and reused `bind_inflight` for the busy check). Evidence that the plan→lead path works
headless for a *plan* revision, not just implementation.

**Incident 2 — two watchers, and an agent using `cancel-turn`.** A `tagteam watch --mode
iterm2` started the previous evening (pre-Phase-32 code in memory, so it ignored the pause
marker) was still running alongside the headless watcher. On round 6 both dispatched:
the interactive Codex in the iTerm tab reviewed and approved, and ran
`tagteam cancel-turn --by Codex` on the headless duplicate (killed at 52 s, recorded as
`cancelled by Codex`). Two lessons: (a) restart long-lived watchers after upgrading
(they do not reload code); (b) `cancel-turn`'s identity binding worked when invoked by
an agent, and the resulting `cancelled` row/marker made the situation legible.

## Dogfood (impl cycle)

_filled in below_

## Downgrade proof (0.9.0 opens a v5 project)

_filled in below_
