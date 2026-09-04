# Tagteam issue note: reviewer turn is not surfaced automatically

- **Observed:** 2026-08-30, one session (`ui-polish` and `post-scan-guidance` cycles), reported by Codex.
- **Status:** Not reproduced; treated as a possible one-off (arbiter ruling 2026-09-03). No fix scheduled. If it recurs, promote to a phase and reference this note.

## Observed behavior

During the `ui-polish` and `post-scan-guidance` cycles on 2026-08-30 PDT,
Codex did not begin work when `handoff-state.json` transitioned to
`turn: reviewer`. The human had to send a message such as “it’s your turn” or
repeat the handoff command. Once prompted, Codex re-read the state and found a
ready reviewer turn (often with an implementation reviewer panel already
running).

Examples observed in this session:

- After the `ui-polish` plan approval, Codex's response described the plan
  cycle as complete. By the next human message, state had already advanced to
  `ui-polish`, `type: impl`, `round: 2`, `turn: reviewer`.
- During `post-scan-guidance` implementation rounds 2 and 3, state showed
  `turn: reviewer`, `status: ready`, `dispatch: not paused`, and an automatic
  reviewer panel in `running` state, but Codex only checked or waited for that
  panel after a human message arrived.

## Likely cause

Codex reads project state only while it has an active conversation turn. A
filesystem/state transition after Codex has returned its final response does
not itself wake the existing Codex session. Tagteam can update
`handoff-state.json` and start a panel asynchronously, but the UI/session still
needs an external dispatch event that starts a new Codex turn. The state and
panel machinery appear healthy; the missing link is likely watcher-to-Codex
turn delivery (or delivery is targeting a headless process/session that is not
this interactive Codex conversation).

This creates a timing race: Codex can accurately report “done” or “lead's
turn,” Claude can submit the next cycle moments later, and the displayed Codex
response becomes stale without Codex receiving another turn.

## Expected behavior

When state changes to all of the following:

- `turn: reviewer`
- `status: ready`
- `reviewer: codex`
- dispatch not paused

Tagteam should wake the configured Codex target exactly once with the state,
contract, and round tail. If an automatic reviewer panel owns the turn, Codex
should either be woken after the panel merges or receive a durable indication
that it should wait for the panel; duplicate reviewer writes must remain
prevented.

## Suggested diagnostics

1. Log every watcher dispatch with cycle sequence, target agent/session,
   delivery acknowledgement, and retry/failure reason.
2. Confirm that interactive Codex sessions have a registered wake target; do
   not treat “panel started” as equivalent to “reviewer session notified.”
3. After a panel merge, emit a second wake event if the merge leaves a manual
   reviewer turn, or notify the lead when the panel writes the reviewer entry.
4. Add an integration test that transitions lead submission -> reviewer-ready
   after the reviewer has gone idle and asserts that a new reviewer turn is
   actually delivered without human input.

## Workaround

Until automatic delivery is verified, the human can prompt Codex with the
handoff command. Codex should immediately re-read `handoff-state.json` and
`tagteam panel status` rather than trusting its previous response.
