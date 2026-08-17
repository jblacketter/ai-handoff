# Phase 41: Review-loop efficiency (3.5)

## Status
- [ ] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** make one review round cost **one** full-suite run and **zero**
spurious agent prompts, in every mode:

1. **On-submit gate** — with `gatekeeper.on_submit: true`, the lead's
   `tagteam cycle add … --action SUBMIT_FOR_REVIEW` (and `cycle init`) on a
   gated cycle type runs the gatekeeper *synchronously* before returning, so
   the `GATE:` entry exists whether or not a watcher is running. `tagteam gate
   check --skip-tests` is the cheap pre-flight (scope + plan-doc only).
2. **Watchdog re-send discipline** — the watcher re-sends a `ready` turn's
   command only when the agent's terminal is *positively* idle and unchanged,
   at a configurable interval (`watcher.resend_minutes`, default 15, `0` =
   never), at most twice per submission; busy markers of current Claude Code
   and Codex UIs are recognised.
3. **Verification-budget contract** (already committed on the branch,
   `349d42f`) — lead runs the full suite once and reports `N passed @ commit`;
   the reviewer reads `--tail 1`, takes the report as fact and spot-checks
   only touched test files.
4. **`scripts/release.py X.Y.Z`** — bumps `pyproject.toml`, `CITATION.cff`
   (`version` + `date-released`) and `uv.lock` in one step; prints the
   commit/tag recipe; no git side effects.

**Why (measured on this repo, 2026-08-16):** the full suite takes ~3m45s.
Per impl round it ran **four** times (lead's own run, lead's `gate check`,
reviewer's focused run, reviewer's full run) — ~15 min of pure test time per
round, ~30 min per phase, and the reviewer's runs land on the reviewer's
token budget. Independently, the watcher's watchdog re-sent
`Read .claude/skills/handoff/SKILL.md …` every 5 minutes for the whole
duration of any turn longer than 5 minutes (`RESEND_TIMEOUT = 300`; the idle
check gives up after 10 s and sends anyway; the current Claude Code / Codex
busy UI is not in `BUSY_PATTERNS` and the always-visible `❯` / `? for
shortcuts` lines *match idle*). Each nudge is a fresh prompt for the agent
(a context re-read, ~10k+ tokens) — for the lead **and** for the reviewer
mid-review. The gate itself only fires under a watcher, so in the CLI loop
`gatekeeper.enabled: true` produced no entry the reviewer could trust, which
is exactly why it re-ran everything.

**Not in scope:** parallel lenses, bounce-cap notifications, plan-cycle gate
checks, MCP server, any cockpit work.

## Scope

### A. On-submit gate

- Config: `gatekeeper.on_submit: true|false` (default **false** — byte-identical
  behaviour when unset). Validated in `validate_gatekeeper_config`
  (bool); surfaced by `get_gatekeeper_spec` / `GateSpec.on_submit`.
- Trigger: `tagteam cycle add --role lead --action SUBMIT_FOR_REVIEW` and
  `tagteam cycle init` when `spec.on_submit` and `spec.applies_to(type)`.
  After the round is written (the submission exists; the state is
  `turn: reviewer, status: ready`), the CLI calls
  `gatekeeper.run_gate(root, kind="manual", spec=spec, phase=…, cycle_type=…, log=progress)`
  — the **same at-most-once claim path** as the watcher / `gate run`
  (`kind` stays within the table's `('auto','manual')` CHECK; the row's
  reason/log line says `on-submit`). A concurrently running watcher gate for
  the same event peeks the decided row and does not run again; if the
  watcher already claimed it, the on-submit call is `deferred` and says so.
- Output (stdout, after the usual `Round added:` line):
  - PASS → the report line (`GATE: PASS | tests ok (…) | scope N paths | plan-doc ok`)
    and `next: the reviewer's turn — tell <Reviewer> to run /handoff`.
  - BOUNCE → the report + failing output excerpt (as the entry carries it)
    and `next: the lead's turn — the turn is already back with you; fix and re-submit --round N+1`.
  - deferred / error / not-applicable → one line saying so and who decides
    (`the watcher, or tagteam gate run`). The submission stands.
  - Exit code: **0** for pass and bounce (the round was added; the verdict
    is data), 1 only when the round itself failed to be added. `--json`
    unaffected (cycle add has none).
- Escape hatch: `--no-gate` on `cycle add`/`cycle init` skips the on-submit
  run for that call (e.g. a watcher will gate it).
- `tagteam gate check --skip-tests`: runs scope + plan-doc only, prints the
  report with `tests: skipped`, exit code from the remaining checks. Because
  the on-submit gate is the single full run, the lead's pre-flight no
  longer needs the suite.
- Progress: check names + durations stream to stderr while the tests run
  (the call blocks for the suite's duration; the log line
  `gate: running tests (timeout 15m) — this is the round's one full run`
  is printed first).
- SKILL (both copies): the gatekeeper paragraph gains the on-submit
  behaviour (your `cycle add` prints the verdict; BOUNCE means the turn is
  already yours; do **not** run the suite separately when `on_submit` is on
  — `gate check --skip-tests` is the pre-flight); reviewer bullet unchanged
  (start from the gate's facts).
- Setup template `tagteam.yaml` comment documents `on_submit`.
- This repo: `gatekeeper.on_submit: true` (dogfood in the impl cycle).

### B. Watchdog re-send discipline

- Config: top-level `watcher:` block, key `resend_minutes` (int ≥ 0, default
  15; `0` disables re-sends). New `validate_watcher_config` /
  `get_watcher_spec` following the briefer/gatekeeper pattern
  (unknown keys rejected). `_StateProcessor.RESEND_TIMEOUT` becomes an
  instance attribute from the spec.
- Re-send rule (iterm2 / tmux modes; headless and manual unchanged —
  headless already short-circuits, manual has no pane):
  1. interval elapsed since the last send for this seq, **and**
  2. the pane is *positively idle*: an IDLE pattern matches, no BUSY pattern
     matches, **and** the captured tail is byte-identical to the previous
     tick's capture (an agent producing output is working, whatever the
     patterns say), **and**
  3. fewer than `WATCHDOG_MAX_RESENDS = 2` re-sends have been made for this
     seq. After the second, the watcher notifies the human once
     (`notify_macos` + log: "still ready after N min — not re-sending; check
     <agent>'s tab") and stops.
  Inconclusive capture (no content / capture failed) → **no re-send** (only
  the *first* send keeps today's "inconclusive → proceed after 10 s").
- Pattern refresh: capture the last 8 lines (was 5); BUSY patterns are
  checked over all of them, IDLE over the last 4. New BUSY markers:
  `to run in background`, `tokens)`, `· ↓`, `working (` (Codex),
  `esc to interrupt` kept. Unit-tested against captured tails of the current
  Claude Code and Codex busy/idle screens.
- Logs: `watchdog: <agent> busy — not re-sending (n/2 used)` at most once per
  interval.

### C. Verification-budget contract — committed (`349d42f`)

Reviewed as part of this plan; adjust wording if the reviewer objects.

### D. `scripts/release.py`

- `python scripts/release.py X.Y.Z [--date YYYY-MM-DD] [--root DIR] [--dry-run] [--no-lock]`
- Validates semver and that it is greater than the current `pyproject.toml`
  version; edits `pyproject.toml` `version = "…"`, `CITATION.cff` `version:`
  and `date-released:` (default today); runs `uv lock` unless `--no-lock`
  (warns if `uv` is missing); prints the changed files and the recipe
  (`git commit -am "release: X.Y.Z" && git tag vX.Y.Z && git push && git push --tags`).
  No git side effects. `--dry-run` prints without writing.
- Test `tests/test_release_script.py` runs it via `runpy` against a temp
  root with copies of the three files (`--no-lock`), including refusal on a
  non-increasing version.
- `docs/how-tagteam-works.md` release notes / memory recipe point at it.

## Files

- `tagteam/config.py` — `GATEKEEPER_KEYS += on_submit`; `WATCHER_KEYS`,
  `validate_watcher_config`, `get_watcher_spec`, `validate_config` hooks.
- `tagteam/gatekeeper.py` — `GateSpec.on_submit`; `run_checks(skip_tests=)`;
  `gate check --skip-tests`; `on_submit_gate(root, phase, type, out)` helper
  used by cycle.py (prints per §A).
- `tagteam/cycle.py` — `_cli_add` / `_cli_init`: `--no-gate`; call the helper
  after a successful lead SUBMIT_FOR_REVIEW / init on a gated type.
- `tagteam/watcher.py` — resend spec, positive-idle rule, tail-change check,
  max resends + notification, pattern refresh, capture depth.
- `scripts/release.py` (new), `tests/test_release_script.py` (new).
- Tests: `tests/test_gatekeeper.py` (on-submit paths: pass, bounce,
  deferred, `--no-gate`, `--skip-tests`, default off = no gate call),
  `tests/test_watcher.py` (resend rules, patterns, config), `tests/test_config.py`.
- Docs: SKILL both copies; `docs/how-tagteam-works.md` (gate section +
  watcher section + files/config table); `README.md` config/CLI lines;
  `tagteam/data/templates/tagteam.yaml` (or wherever setup writes the
  sample) comment; `docs/roadmap.md`; this doc.
- Version: `pyproject.toml` / `CITATION.cff` → 3.5.0 (via the new script,
  as its first real use).

## Success criteria

- With `on_submit: true`, a lead `cycle add SUBMIT_FOR_REVIEW` on an impl
  cycle produces exactly one gate row and one `GATE:` entry with no watcher
  running; a second `cycle add`/`gate run` for the same submission does not
  re-run the checks. With `on_submit` unset every existing test passes
  unchanged and `cycle add` output is byte-identical.
- BOUNCE from on-submit leaves the state `turn: lead, status: ready` with
  the `GATE_BOUNCE` entry, and the CLI says so.
- Watcher: a `ready` turn whose pane shows a busy marker or whose tail
  changed between ticks is never re-sent; positive idle re-sends at the
  configured interval, at most twice, then notifies once.
- `scripts/release.py 3.5.0` produces the three-file bump used for this
  phase's release; refuses `3.4.0` / `3.4.0-x`.
- Full suite passes; docs and both SKILL copies identical.
