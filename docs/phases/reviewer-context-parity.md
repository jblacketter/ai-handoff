# Phase 47: Reviewer context parity — the reviewer gets the same footing as the lead

## Status
- [x] Planning — no plan cycle: the arbiter asked for the change directly
      ("can you make the updates to tagteam? I will go with your suggestions",
      2026-08-29). The design is this document, written alongside the code.
- [x] Implementation — branch `phase-47-reviewer-context-parity`
- [x] Implementation Review — approved round 2 (2026-08-29; r1 asked for `UnicodeDecodeError` handling in `read_project_context`, fixed in 5375be4 with a regression test)
- [x] Complete — awaiting merge/release

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** two new optional blocks in the headless turn prompt, rendered by
`compose_prompt` between the arbiter interjections and the handoff contract:

1. **`=== PROJECT CONTEXT (<file>) ===`** — the project's own context file,
   selected by `select_context_file()` as *the file this provider's CLI does
   not already auto-load*. `PROVIDER_AUTOLOADS` records that `claude` reads
   `CLAUDE.md` for itself and `codex` reads `AGENTS.md` for itself; the
   preference order is `AGENTS.md` then `CLAUDE.md`. So Codex reviewing a repo
   that has only `CLAUDE.md` now receives it, while Codex in a repo that has
   `AGENTS.md` receives nothing extra (it already loaded it). Capped at
   `PROJECT_CONTEXT_MAX_CHARS = 12000` with an explicit truncation notice.

2. **`=== CHANGE SURFACE (baseline <sha>) ===`** — for `impl` cycles only, the
   paths attributable to this phase, obtained by calling the existing
   `cycle.compute_scope_diff()`. Lists up to `CHANGE_SURFACE_MAX_PATHS = 60`
   paths, the baseline sha, and the literal `git diff <base> -- <path>` command,
   with the instruction to read the diff rather than trust the lead's summary.

Both are gathered at the call site in `HeadlessTurn` and passed as optional
keyword arguments, so `compose_prompt` stays a pure function the way Phase 46
left it. Both degrade to `None` — a missing context file, an unreadable file, a
cycle with no baseline, or any unexpected error from scope-diff — and a turn
never fails because its context could not be assembled.

**Why:** the headless prompt carried the process contract, the current state and
the round tail, and nothing whatsoever about the project under review. Grepping
the package for `AGENTS.md` or `CLAUDE.md` before this phase returned no hits.
The lead usually got away with it because `claude -p` auto-loads `CLAUDE.md`
from cwd; the reviewer usually did not, because only 6 of the 46 tagteam-managed
projects on this machine have an `AGENTS.md` for `codex exec` to find. The
result was a structural asymmetry in a two-agent loop: the author understood the
codebase and the reviewer did not, which is exactly backwards from what a review
is for.

The change surface is the sharper half. The handoff contract already tells the
reviewer *"the reviewer reads the diff, not a narrative"* — but nothing in the
harness ever told it **which** diff. It has shell access, so it could always run
`git diff`, but it had to guess the baseline. Reusing `compute_scope_diff` means
the reviewer sees precisely the path set an impl-review audit would attribute to
the phase, with pre-existing drift and tagteam's own bookkeeping artifacts
already filtered out — no second, subtly different notion of "what changed."

**Deliberately not done:**

- **No per-repo `AGENTS.md` generation.** Writing 46 of them would recreate the
  copy-drift problem the plugin work is meant to end. One injection point in the
  harness beats 46 files that fall out of sync.
- **No new baseline mechanism.** `compute_scope_diff` already exists, is tested,
  and already excludes `_TAGTEAM_ARTIFACT_FILES`. A second implementation would
  be a second source of truth about what the phase touched.
- **No diff *content* in the prompt.** Only the path list and the command. Diff
  bodies are unbounded; the reviewer has shell access and can read what it needs.
- **No change to the lead's prompt shape.** Both blocks are provider-aware and
  role-agnostic; the lead benefits when a repo carries the file its own CLI does
  not load, and is otherwise unaffected.

## Scope

**In:** `tagteam/headless.py` — four new module constants
(`PROVIDER_AUTOLOADS`, `CONTEXT_FILENAMES`, `PROJECT_CONTEXT_MAX_CHARS`,
`CHANGE_SURFACE_MAX_PATHS`), two block headers, four new functions
(`select_context_file`, `read_project_context`, `render_project_context`,
`collect_change_surface`, `render_change_surface`), two optional kwargs on
`compose_prompt`, and the gather-and-log wiring in the `HeadlessTurn` call site.
`tests/test_headless.py` — 19 tests across three classes.

**Out:** `cycle.py` (consumed, unchanged); the watcher; the gatekeeper; the
panel; the `SKILL.md` contract text; any change to when a baseline is captured;
the cockpit and hub surfaces.

## Verification

Full suite: **1429 passed, 5 skipped** (`uv run python -m pytest`, 4m31s, clean
working tree apart from this phase). The 19 new tests cover all four
provider/file combinations for context selection, empty and oversized context
files, plan-vs-impl gating, scope-diff failure degrading to `None`, path-list
capping, and the ordering guarantee that both blocks precede the contract.

Not yet run: a live headless turn against a real repo. The blocks are covered by
unit tests only; an end-to-end check on the aegis project is the obvious first
review request.

## Follow-ups (not in this phase)

- `tagteam setup` could offer to seed an `AGENTS.md` from an existing
  `CLAUDE.md` for projects that want the reviewer's own CLI to load it directly.
- The panel (`panel.enabled`, Phase 39) remains opt-in and off by default in
  every project on this machine. Turning it on is a separate, config-only
  change — see the arbiter's aegis rollout.
