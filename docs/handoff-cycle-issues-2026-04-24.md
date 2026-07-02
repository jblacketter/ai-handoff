# Handoff-cycle issues surfaced during a multi-phase QA-suite session

**Reporter:** claude (as lead agent on the QA/ suite)
**Date:** 2026-04-24
**Source context:** A single multi-phase session through `~/projects/QA/` that closed seven phases end-to-end (suite-orientation, reference-framework-triage, live-env-housekeeping, automation-framework-example-uplift, extract-mcp-server, extract-browser-recorder, harmonic-runtime-mvp) and was partway into the eighth (bugalizer-phase-4-fix-proposals) when the issues below were observed.

Filing here for future fix work in tagteam; not blocking the QA suite's ongoing phases.

---

## Issue 1 (high severity): `tagteam cycle init` silently writes to the wrong project when cwd is a nested project dir

### What happened
The QA suite is laid out as:

```
~/projects/QA/                          <- suite root (has handoff-state.json + docs/handoffs/)
  qaagent/                              <- nested project (has its own handoff-state.json + docs/handoffs/)
  bugalizer/                            <- nested project (same)
  harmonic/                             <- nested project (same)
  ...
```

While planning Phase 5 (bugalizer-phase-4-fix-proposals), my shell ended up with `cwd = ~/projects/QA/bugalizer/` after an earlier `cd` for a git-status check in that subdirectory. The subsequent `tagteam cycle init --phase bugalizer-phase-4-fix-proposals --type plan ...` call:

1. **Succeeded** with the normal "Cycle created: ..." message.
2. **Wrote the cycle files into `~/projects/QA/bugalizer/docs/handoffs/`** instead of the suite root's `~/projects/QA/docs/handoffs/`.
3. **Updated `~/projects/QA/bugalizer/handoff-state.json`** (creating it fresh, since bugalizer's was already deleted) instead of `~/projects/QA/handoff-state.json`.
4. Left the **suite's state.json untouched**, still pointing at the previous phase's completion.

The watcher monitors the suite root's state.json. Since that didn't change, **no `/handoff` was ever dispatched to codex** for Phase 5. The cycle existed — just invisibly from the watcher's perspective.

### Impact
- Silent failure, no error message.
- The lead (me) wrote the plan and assumed it was under review. Nothing was under review.
- Recovery required: delete the misplaced files in `bugalizer/`, clean up bugalizer's new `handoff-state.json`, re-run `cycle init` from the correct cwd.
- Easy to miss in a multi-project suite because the success output ("Cycle created: ...") looks identical to a correct invocation.

### Suggested fix directions (in order of increasing complexity)
1. **Detect and warn.** Have `tagteam cycle init` walk up from cwd looking for the nearest `tagteam.yaml`. If the first match is in a *nested* tagteam project (i.e. the parent directory also has `tagteam.yaml`), print a warning: "Found nested tagteam project at X, but parent at Y is also a tagteam project. Use `--root PATH` to disambiguate."
2. **`--root` / `--project-dir` flag.** An explicit flag that the user can pin in scripts or docs so the behavior is deterministic regardless of cwd.
3. **Lockfile-style disambiguation.** On cycle-init, record the resolved project root in the rounds JSONL header; on `cycle add`, compare and error if the resolution differs.
4. **Best-of-both.** Default to walk-up discovery (like git), but print the resolved root at the top of every `tagteam cycle`-family command's output: `"[tagteam] project root: /Users/jackblacketter/projects/QA"`. Users then visually notice when it's wrong.

### Repro
```bash
cd ~/projects/QA/bugalizer
tagteam cycle init --phase test-phase --type plan --lead claude --reviewer codex --updated-by claude --content "test"
# No warning; writes to bugalizer/docs/handoffs/ and bugalizer/handoff-state.json
```

---

## Issue 2 (medium severity): impl-cycle scope audit conflates phase-attributable changes with pre-existing uncommitted drift

### What happened
In at least two phases (`harmonic-runtime-mvp`, `bugalizer-phase-4-fix-proposals`), the target subproject was a git repo with substantial **pre-existing uncommitted drift** that predated the phase — mostly an incomplete `ai-handoff.yaml` → `tagteam.yaml` migration plus older phase-doc purges. For harmonic this was 21 paths; for bugalizer 16 paths.

When codex reviewed impl submissions, it ran `git diff --name-only` in the subproject and found N additional paths not in my plan's file list — even though those paths were untouched by this phase. Codex appropriately pushed back ("scope verification does not hold"), and I had to spend a round explaining the drift and proving (via `git diff HEAD -- <file> | grep <banner-text>`) which diff chunks were mine vs. which predated the phase.

### Impact
- Extra review rounds spent on bookkeeping rather than substance.
- Reviewer sees noise and reasonably asks whether scope is blown.
- Lead has to hand-audit `git diff` to separate phase-attributable changes from pre-existing drift.
- This is NOT a review-process failure — codex is right to audit via the source of truth (diff). But the cycle protocol has no shared notion of "diff baseline," so the conversation gets framed in terms the cycle doesn't actually capture.

### Suggested fix directions
1. **Capture a git baseline SHA on `cycle init`.** Optional: when the cycle targets a specific subproject, tagteam records `git -C <path> rev-parse HEAD` at init time. Impl submissions carry that SHA. Reviewer's tooling can then show "diff attributable to this phase only = `git diff <baseline-SHA> HEAD`" — excluding anything committed before the cycle started. (Note: this doesn't help with uncommitted drift that existed before init.)
2. **Capture working-tree snapshot on `cycle init`.** Heavier. Record `git status --porcelain` at init so uncommitted drift at cycle start is known. Reviewer can then filter those files out of the audit or flag them separately.
3. **Plan-level convention.** Not a tagteam change: establish a convention that plans include a "pre-existing drift inventory" section when targeting a subproject with a dirty working tree. I started doing this voluntarily in Phase 5 after Phase 4 got caught — it works but depends entirely on lead discipline.
4. **Add a "declare attributable scope" field on impl submissions.** The submitter explicitly lists the N files the phase changed; the reviewer is expected to audit against that list rather than the raw diff. (Approximately what we do in practice, but the friction point is the audit tooling doesn't know about this list.)

Option 2 is the highest-leverage fix for this class of issue.

---

## Issue 3 (low severity): `try_claim_report(id, same, same)` is accepted but not actually atomic

### What happened
Not a tagteam-library bug, but a tagteam-project pattern problem worth documenting.

The bugalizer DB helper `try_claim_report(report_id, expected_status, new_status)` is a compare-and-set. I mistakenly proposed using it with `expected_status == new_status == "analyzing"` as a "mutex lock that doesn't change state." codex correctly flagged that this is a no-op gate: every concurrent worker satisfies the WHERE clause and UPDATE returns `rowcount == 1`, so N concurrent claims can all "succeed."

The clean pattern is to claim via a transient claim state (`TRIAGED → FIX_PROPOSING`, with `FIX_PROPOSING` being distinct from any resting state), then transition out on completion. But this is a trap for future phase planners using the same helper.

### Suggested fix directions
1. **Runtime assertion.** `try_claim_report` raises if `expected == new`. One line, catches the misuse explicitly.
2. **Rename / add variant.** Two functions: `try_claim_transition(id, src, dst)` (requires src != dst) and `try_acquire_lock(id, status)` (row-level lock without state change, implemented with explicit locking semantics).
3. **Docstring warning.** Cheapest fix: add a note to the docstring warning against equal-states usage.

---

## Issue 4 (low severity, UX): `tagteam session --help` is treated as an unknown subcommand

### What happened
```
$ tagteam session --help
Unknown session subcommand: --help
```

Most other CLIs (git, gh, tagteam's top-level) accept `--help` at any depth. Minor UX hiccup; I ended up inspecting the CLI source to find the session subcommands.

### Suggested fix
Standard argparse/click pattern: register `--help` / `-h` on the `session` subparser so `tagteam session --help` prints available subcommands.

---

## Issue 5 (low severity, lead-side): no mid-review amendment path

### What happened
Several times, the human arbiter (Greg) answered open questions while a plan was out for codex review. Those answers would materially improve the plan, but the cycle protocol has no "amend during review" action — I had to either wait for codex to respond (then fold the answers into round 2) or risk a REQUEST_CHANGES on something the user had already resolved.

This isn't a blocker — codex usually round-trips quickly enough — but it creates awkward moments where the lead is holding user-provided answers with no clean way to get them into the record before the reviewer acts.

### Suggested fix directions
1. **Add an `AMEND` action** that's lead-side, doesn't count as a new round, and surfaces to the reviewer as "plan was updated in place; here's what changed." Keeps the review linear instead of forcing a round-trip.
2. **Or: convention to just resubmit as a new round.** This is what I've been doing; it works, just feels heavy for minor amendments.

---

## Summary

| # | Severity | What | Where in stack |
|---|---|---|---|
| 1 | High | cwd-sensitive `cycle init` silently writes to nested projects | `tagteam cli.py`, `tagteam cycle.py` |
| 2 | Medium | Impl scope audit conflates phase changes with pre-existing git drift | Protocol / methodology + optional tooling |
| 3 | Low | `try_claim_report(x, s, s)` is accepted but non-atomic | Project-level pattern (not tagteam-owned) |
| 4 | Low | `tagteam session --help` returns an error | `tagteam cli.py` |
| 5 | Low | No lead-side mid-review amendment path | Protocol |

Issue #1 is the one that actively caused a silent failure mid-session. The rest are friction / UX / documentation.

All observations gathered between 2026-04-23 and 2026-04-24 working as lead on the QA suite.
