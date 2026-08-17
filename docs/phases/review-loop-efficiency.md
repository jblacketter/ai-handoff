# Phase 41: Review-loop efficiency (3.5)

## Status
- [x] Planning
- [x] In Review (round 2: --no-gate = synchronous call only, watcher/gate run authoritative, lead never runs the suite on a gated type; gate check w/o --skip-tests labelled the one explicit exception; release script snapshot/restore of all three files on any failure + tests for write-then-fail uv and CITATION write failure; round 1: one-run rule table + gate entry contents; watchdog per-seq state machine + tests; release script write order/rollback + lock-enabled test)
- [x] Approved (round 3)
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
3. **Verification-budget contract** (first cut committed on the branch,
   `349d42f`; the one-run rule in §C supersedes its wording and the SKILL
   text is updated to match at impl time) — exactly one full-suite run per
   submission, the one on the record (the on-submit gate's when `on_submit`
   is on, else the lead's, cited as `N passed @ commit`); the reviewer reads
   `--tail 1`, takes it as fact and spot-checks only touched test files.
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
- Escape hatch: `--no-gate` on `cycle add`/`cycle init` skips the
  *synchronous* run for that call only; the submission remains gate-eligible
  for a running watcher / `tagteam gate run` (semantics in §C).
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

**State machine (per `state.seq`).** The processor keeps one
`_watchdog` record: `{seq, sent_at, resends, last_tail, notified}`.

| Event | Transition |
|---|---|
| dispatch of a `ready` turn for a seq ≠ record.seq (new submission, incl. a BOUNCE that hands the turn back) | record ← `{seq, sent_at=now, resends=0, last_tail=None, notified=False}`. The first send keeps today's grace path (`wait_for_idle*` ≤ 10 s, then send even if inconclusive). |
| tick, same seq, `status != ready` | record untouched (a `working` agent is not nudged); it is discarded when the seq changes. |
| tick, same seq, `ready`, interval not elapsed | no capture, no send. |
| tick, same seq, `ready`, interval elapsed, capture fails / empty | no send; `last_tail` unchanged (nothing learned). Log once per interval. |
| capture ok, tail ≠ `last_tail` (incl. `last_tail is None` — first successful capture) | `last_tail ← tail`; **no send** this tick (the agent produced output, or we have no baseline yet). |
| capture ok, tail == `last_tail`, BUSY pattern present or no IDLE pattern | no send; log `busy`. |
| capture ok, tail == `last_tail`, positively idle, `resends < 2` | send; `resends += 1`; `sent_at ← now`. |
| capture ok, tail == `last_tail`, positively idle, `resends == 2`, `notified == False` | no send; `notify_macos` + log once; `notified ← True`. |
| anything with `notified == True` | no send, no further notification, until the seq changes. |
| `resend_minutes == 0` | the record is still kept (for logs) but no tick ever sends or notifies. |

Consequences: eligibility for a re-send needs *two* successful captures with
identical tails at least one interval apart; a stale tail from a previous
seq can never suppress or trigger a send because the baseline is dropped on
seq change; capture failure never sends. Tests (`tests/test_watcher.py`)
drive `_StateProcessor.tick()` with a fake clock and a fake capture for
both `iterm2` and `tmux`: seq rollover resets counters/baseline/notified,
capture failure never sends, changed tail resets the baseline and
suppresses the tick, cap → exactly one notification per seq, `0` disables.

### C. Verification-budget contract — committed (`349d42f`), refined here

**The one-run rule (impl cycles).** Exactly one full-suite run per
submission, and it is the one whose result is on the record:

| Situation | The one full run | Lead does | Reviewer does |
|---|---|---|---|
| `on_submit: true` (gated type) | the on-submit gate's `check_tests` | `gate check --skip-tests` pre-flight (optional), then `cycle add`; the submission text cites the gate result and commit (`gate: PASS @ <sha>` — the gate entry carries the numbers) | starts from the `GATE:` entry; never re-runs |
| `on_submit: true` but `--no-gate` | the **watcher's / `gate run`'s** gate — `--no-gate` suppresses only the synchronous call; the submission stays gate-eligible exactly as before this phase (same event key, so at most one gate ever runs for it) | does **not** run the suite; uses `--no-gate` only when a watcher is running (or will run `tagteam gate run` next); cites nothing — the gate entry is the record | starts from the `GATE:` entry (if none has appeared yet, the gate is still owed: wait / `tagteam gate status`, do not run the suite) |
| gate not enabled / not `on` for this type | the lead's own run | same as the previous row | same |
| gate BOUNCE (any mode) | the bounced attempt's run is spent; the **re-submission** gets one new run (on-submit again, or the lead's own if `--no-gate`) | fixes, re-submits `--round N+1` | — |

`--no-gate` is therefore *never* "I ran it myself": on a gated type the
gate's run is authoritative whether it happens synchronously or from the
watcher; the lead's own run is the record only when no gate applies
(`enabled: false` or the type is not in `on`; there `--no-gate` is a no-op
and is accepted silently). Test: `cycle add --no-gate` on a gated type
writes no gate row, leaves the submission reviewer-ready, and a subsequent
`gate run` runs the checks exactly once.

**Explicit exception (not the default path):** `tagteam gate check`
*without* `--skip-tests` on an `on_submit` project still runs the suite —
that is a deliberate extra run the lead opts into (e.g. before a large fix)
and the command says so first (`note: on_submit is on — the submit will run
the suite again; use --skip-tests for the pre-flight`). The "exactly one"
success criterion is about what the tooling does on the default path; this
opt-in is the only sanctioned way to exceed it.

**Gate entry contents (unchanged format, made a requirement):** the
`GATE: PASS | tests ok (<duration>) | scope N paths | plan-doc ok` line,
followed by the test summary line the runner printed (pytest's
`N passed, M skipped in T s`), the checked commit (`HEAD <sha>`, added by
this phase) and, for BOUNCE, the failing output excerpt (≤ `max_output_chars`).
`tagteam gate status` shows the full report; the reviewer needs nothing
else to treat the tests as run.

SKILL (both copies) — lead's *Verification budget* paragraph gets the table's
rules in prose (`on_submit` → the gate is the run, cite it; `--no-gate` /
no gate → run once and cite `N passed @ sha`); the reviewer bullet already
says "take the report or gate entry as fact".

### D. `scripts/release.py`

- `python scripts/release.py X.Y.Z [--date YYYY-MM-DD] [--root DIR] [--dry-run] [--no-lock]`
- Validates semver and that it is greater than the current `pyproject.toml`
  version; edits `pyproject.toml` `version = "…"`, `CITATION.cff` `version:`
  and `date-released:` (default today); runs `uv lock` unless `--no-lock`
  (warns if `uv` is missing); prints the changed files and the recipe
  (`git commit -am "release: X.Y.Z" && git tag vX.Y.Z && git push && git push --tags`).
  No git side effects. `--dry-run` prints without writing.
- Transactional write (all three files): the script first **snapshots the
  original bytes** of `pyproject.toml`, `uv.lock` (if present) and
  `CITATION.cff`, then proceeds: write `pyproject.toml` (temp file +
  `os.replace`) → run `uv lock` (unless `--no-lock`) → write `CITATION.cff`
  (temp + `os.replace`). On **any** failure at any step — `uv lock`
  non-zero / timeout / binary missing (without `--no-lock`), a write error on
  either file, or an unexpected exception — the script restores **every**
  file whose current bytes differ from its snapshot (a failing `uv lock`
  may have partially rewritten `uv.lock`; a `CITATION.cff` failure after a
  successful lock must roll back both `pyproject.toml` and `uv.lock`), prints
  what failed (uv output included) and exits 2. Success criterion: after a
  failed run the three files are byte-identical to before; nothing looks
  half-released. `--dry-run` writes nothing and runs nothing (prints the
  three planned edits). No git command is ever executed.
- Test `tests/test_release_script.py` imports the script as a module
  (`importlib.util.spec_from_file_location`) and calls `main(argv)` against
  a temp root with copies of the three files: `--no-lock` happy path
  (pyproject + CITATION updated, `date-released` set), refusal on a
  non-increasing version (`3.4.0`, `3.4.0-x`, `3.3.9`) with no writes,
  `--dry-run` leaves all three files byte-identical, and **lock-enabled**
  cases with a deterministic fake `uv` on `PATH` (a shell script that
  rewrites the project's `version = "…"` entry in `uv.lock` and keeps every
  other line): (a) success — the project package entry changes old → new,
  unrelated dependency entries are byte-identical; (b) a fake `uv` that
  **writes garbage into `uv.lock` and then exits 1** → all three files
  byte-identical to before, exit 2; (c) a `CITATION.cff` write failure after
  a successful lock (the test monkeypatches the module's `_write_atomic` to
  raise for that path) → `pyproject.toml` and `uv.lock` restored, exit 2.
  The temp root has no `.git`, which also proves the script needs none.
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
- The gate entry (PASS or BOUNCE) carries the runner's test summary line and
  the checked commit sha; a lead submission on an `on_submit` project cites
  the gate result and commit, not a second run.
- `scripts/release.py`: any failure (lock, either write) leaves all three
  files byte-identical to before and exits 2; `--no-gate` on a gated type
  writes no gate row and leaves the submission gate-eligible (one later
  gate run, never two).
- Full suite passes; docs and both SKILL copies identical.
