# Phase 31 — Headless Turn Engine: Findings

Dogfood and release-check record for `docs/phases/headless-turn-engine-30-arc.md`
(Scope 9 / Success Criteria). Numbers below are from real `claude -p` /
`codex exec` turns; the `usage` table in each project's `.tagteam/tagteam.db`
is the source.

## Step 0 — CLI probes (2026-08-14)

| Provider | Invocation | Result |
|---|---|---|
| claude | `claude -p --output-format stream-json --verbose --permission-mode acceptEdits --allowedTools Bash Read Edit Write Glob Grep` (prompt on stdin) | Ran a Bash command + read a file **without prompting**; stream is JSONL (`system/init`, `assistant`, `user`, `rate_limit_event`, `result`); the final `result` event carries `usage`, `total_cost_usd`, `num_turns`, `session_id`; `system/init` carries `model`. Fixture: `tests/fixtures/headless/claude_stream.jsonl`. |
| codex | `codex exec --json -C <root> --sandbox workspace-write -c approval_policy=never --skip-git-repo-check -` (prompt on stdin) | Ran a shell command + read a file without prompting; stream is JSONL (`thread.started`, `turn.started`, `item.started/completed`, `turn.completed`); `turn.completed.usage` carries `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`; **no model name in the stream** (recorded as null unless `-m` is configured). Fixture: `tests/fixtures/headless/codex_stream.jsonl`. `codex exec --help` (0.147.0) has no `-a`; the explicit non-interactive approval policy is set via `-c approval_policy=never`. |

No companion flag beyond `--verbose` was needed for `stream-json`.

## Discovered dependency: Windows cycle writes were broken

`tagteam/dualwrite.py` imported `fcntl` at module top, so **every cycle/state
write failed on Windows** since Phase 28 — independent of headless mode. Fixed
in this phase with an `msvcrt.locking`-based lock on `win32` (same
per-process advisory semantics). Without this, a headless agent on Windows
could not have written its round.

## Dogfood runs

### (c) Plan-approved → implement → impl-init boundary (scratch project, 2026-08-14)

Scratch project `greet-cli` (tiny Python package + plan in `docs/phases/greet-cli.md`).
Set up: plan cycle approved, then `state set --turn lead --status ready --command "/handoff start greet-cli impl"`
(exactly what full-roadmap auto-advance writes). Ran `tagteam watch --mode headless --turn-timeout 30`.

| Turn | Provider | Outcome | Wall | in / out / cache-read / cache-write | cost | CLI turns |
|---|---|---|---|---|---|---|
| lead (`/handoff start greet-cli impl`) | claude | ok — `cycle init` impl r1 | 39.5 s | 16 / 2 907 / 267 909 / 20 456 | $0.82 | 8 |
| reviewer (impl r1) | codex | ok — APPROVE | 73.5 s | 191 285 / 2 037 / 151 552 / 0 | n/a | 1 |

- The headless lead followed the boundary clause literally: read the plan + plan-cycle
  history, checked `cycle status --type impl` (none), wrote `greeter/__init__.py`,
  `greeter/__main__.py`, `tests/test_greeter.py`, ran pytest (7 passed) and the CLI,
  **then** ran `cycle init --type impl` once. Real diff: 3 files, ~60 lines.
- The reviewer turn spawned automatically on the flip, ran the tests itself and approved.
- `tagteam tail --no-follow` during the lead turn showed the rendered stream (tool calls,
  results) mid-turn; `inflight.json` was present during and gone after each turn.
- Both usage rows landed with status `ok`; codex `model` is null (not in its stream).
- Environment quirk seen in the codex turn log: its shell resolved a different `python`
  than pytest's, so its first `python -m pytest` failed and it recovered by calling
  `pytest` directly. Not a tagteam issue, but worth knowing when reading logs.

### (a) Phase 31 impl-cycle turns headless (this repo)

_filled in below_

### (b) Phase 32 plan cycle headless (this repo)

_to be run when Phase 32 planning starts (after soak green-light)_

## Downgrade proof (0.7.1 opens a v3 project) — done 2026-08-14

Throwaway venv with `pip install tagteam==0.7.1` (SCHEMA_VERSION 2) against a copy of the
scratch project above (DB `user_version = 3`, 2 usage rows):

- `tagteam cycle rounds --phase greet-cli --type impl` → prints the round ✔
- `tagteam state` → shows phase/type/status ✔
- `tagteam cycle status --phase greet-cli --type impl` → `state: approved` ✔
- `tagteam cycle init --phase dg-check` + `cycle add … REQUEST_CHANGES` → both succeed;
  DB still `user_version = 3`, `usage` rows untouched, new cycle rows present ✔
- `tagteam upgrade` (revert recipe step 2) → completes. **Caveat:** `upgrade` is
  registry-wide (it re-ran setup across all 42 registered projects); copies are
  idempotent (existing framework files are skipped), so nothing was overwritten, but
  the recipe should be read as "affects every registered project", not just one.

## Windows

- CI: `.github/workflows/tests.yml` runs the full suite on `windows-latest`
  (fake-agent shims via `.cmd`). Status: _pending first push_.
- Real signed-in CLI smoke on a Windows machine: **not performed** in this
  phase (no Windows host available); recorded here per the plan's Decisions §2.
