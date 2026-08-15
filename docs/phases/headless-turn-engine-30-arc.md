# Phase 31: Headless Turn Engine (3.0 arc)

## Status
- [x] Planning
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

**What:** An opt-in `tagteam watch --mode headless` in which each turn is a
fresh, bounded process instead of keystrokes typed into a long-lived
terminal. On turn flip the orchestrator (the existing watcher's
`_StateProcessor`) spawns the owed agent through its signed-in CLI
(`claude -p` for Claude, `codex exec` for Codex) with a composed context —
handoff skill contract + current state + round-log tail — streams the
process output to a per-turn log, verifies the agent wrote its round, and
records per-turn token usage in a new additive `usage` table. Ships with
`tagteam cycle rounds --tail N`, `tagteam tail`, and strict failure
handling (timeout / nonzero exit / no round written → pause + notify,
never a silent loop).

**Why:** It is the keystone of the 3.0 arc (`docs/tagteam-3.0-proposal.md`
§3–4). It removes the send-keys/idle-scrape edges that produce the two
known quirk classes (agent sits idle on its turn; long-lived session
drifts and jumps ahead), gives Windows a working orchestration path for
free (pure `subprocess`), bounds per-turn context (finishing what 2.0's
tail reads started), and produces the per-turn usage data every later
phase (32 usage CLI, 34 cockpit panels, 35 hub) reads.

**Depends on:** Phase 28 (SQLite canonical store, `PRAGMA user_version`
migrations already in `tagteam/db.py`), Phase 24 (`_StateProcessor`
refactor — headless is one more branch of `_handle_ready`).

**Size:** large (comparable to Phase 28). Suggested to land as 3–4 PRs
in the order of the Technical Approach steps; the impl cycle reviews the
sum.

**Release:** 0.8.0, then soak per proposal §5.

---

## Scope

### In Scope

1. **`tagteam cycle rounds --tail N`** — closes the last open 2.0
   follow-up. Returns the last N entries of the merged round list
   (AMEND entries count as entries). Plus a Python helper the orchestrator
   uses directly. SKILL.md mentions it as an optional token saver.
2. **Additive schema v3 — `usage` table** in `tagteam/db.py`
   (`SCHEMA_VERSION = 3`, one new `if current < 3:` migration step, new
   table only — no renames/removals). Recording only; surfacing is
   Phase 32/34.
3. **`tagteam/headless.py`** (new module):
   - **Adapter table** — per-provider spec: how to invoke the CLI, how the
     prompt is passed (stdin), how to parse the final structured output
     for usage/session id. Two adapters ship: `claude` and `codex`.
     Selection is by `agents.<role>.headless.provider`, defaulting from
     the agent's `command`/name (`claude*` → claude, `codex*` → codex).
   - **Prompt composer** — builds the bounded turn context: role banner
     (who you are, which agent name to pass as `--updated-by`), the full
     `.claude/skills/handoff/SKILL.md` contract, `handoff-state.json`
     contents, `cycle rounds --tail N` (default N=3, `--tail-rounds`),
     the state's `command`, and a closing instruction ("do the work, then
     make exactly one `tagteam cycle add`/`cycle init` call as the
     contract says, then stop").
   - **Turn runner** — `Popen` with the prompt on stdin, cwd = project
     root, stdout+stderr streamed line-by-line to
     `.tagteam/turns/<phase>_<type>_r<N>_<role>_<ts>.log`; writes
     `.tagteam/turns/inflight.json` (phase/type/round/role/pid/log_path/
     started_at) while running and removes it after; enforces
     `--turn-timeout` (default 60 min) by killing the whole process
     tree (POSIX: `start_new_session=True` + `os.killpg`; Windows:
     `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T`); on Ctrl-C kills the
     child before exiting.
   - **Outcome verification** — after exit, re-read state: the turn is
     "ok" iff exit code 0 **and** `seq` advanced with the turn no longer
     owed to the same role at the same round. Otherwise the outcome is
     `timeout` / `nonzero_exit` / `no_round`.
   - **Usage capture** — parse the adapter's structured output (tokens,
     cache read/write, cost if present, model, session id, num_turns) and
     insert one `usage` row per spawned turn, including failed ones
     (status column). Malformed/missing usage never fails a turn that
     otherwise succeeded — the row is written with null token fields and a
     `diagnostics` entry (`kind = headless_usage_unparsed`).
   - **Failure handling** — on any non-ok outcome: write a `diagnostics`
     row (`kind = headless_turn_failed`), write
     `.tagteam/headless-paused.json` (reason, phase, type, round, role,
     log_path, ts), send a notification, and log the resume recipe. While
     the pause marker exists the orchestrator refuses to dispatch (also
     on restart) and says why every tick. Zero automatic retries
     (`--turn-retries N`, default 0, opt-in). Phase 32's `tagteam resume`
     clears the marker; in 31 the recipe is "delete the marker (or fix
     state) and the watcher resumes on the next tick".
4. **Watcher integration** (`tagteam/watcher.py`):
   - `--mode headless` accepted by `watch_command`; `_auto_detect_mode`
     **never** returns it.
   - New flags: `--turn-timeout MIN`, `--tail-rounds N`, `--turn-retries N`.
   - `_handle_ready` gets a `headless` branch that calls the turn runner
     synchronously (the loop has nothing else to do while a turn runs).
     `--confirm` still works (prompt before spawn).
   - Headless forces the poll trigger (logged) — a blocking tick inside a
     watchdog callback is the wrong shape; revisit if latency matters.
   - The 5-minute watchdog re-send is **disabled** in headless (a re-send
     would spawn a duplicate turn); the "working for N minutes" alert is
     replaced by the turn timeout.
   - `_handle_done` / `_handle_escalated` in headless behave like
     `notify` mode (log + notification; no completion nudge is sent
     because there is no terminal to nudge).
   - Startup validation: both roles' provider CLIs must resolve via
     `shutil.which` (or the configured command); otherwise exit 1 with a
     clear message. Existing pause marker → log loudly, do not dispatch.
5. **`tagteam tail`** (new CLI command, `tagteam/cli.py` dispatch →
   `headless.tail_command`): follows the in-flight turn log
   (`inflight.json`) like `tail -f`, prints the outcome line when the
   turn ends; with no in-flight turn prints the most recent turn log's
   last `--lines N` (default 40) and exits. `--no-follow` for scripts.
   Pure Python file following (cross-platform).
6. **Config surface** (`tagteam/config.py`, additive, all optional):
   ```yaml
   agents:
     lead:
       name: Claude
       headless:
         provider: claude            # claude | codex (default: inferred)
         args: ["--model", "opus"]   # appended to the adapter's base argv
   ```
   `validate_config` checks types; unknown provider is an error.
7. **Windows acceptance path**: a new `.github/workflows/tests.yml`
   running pytest on `ubuntu-latest` and `windows-latest` (there is no
   test CI today — only `publish.yml`). The headless tests drive a
   **fake agent CLI** (`tests/fixtures/fake_agent.py`, a Python script
   selected via env var to emulate: writes-round-ok / no-round /
   nonzero-exit / hang-for-timeout / malformed-output, in both claude-JSON
   and codex-JSONL flavors) so spawn, streaming, timeout/kill, pause, and
   usage-parse paths run on both OSes.
8. **Docs** (proposal §6 standing criterion): README gains a "Headless
   mode (opt-in)" section (what it is, how to enable, `tagteam tail`,
   what happens on failure, Windows note); `docs/how-tagteam-works.md`
   is **not** started here (Phase 36); `HELP_TEXT` and `watch --help`
   updated; SKILL.md updated for `--tail N` and a two-line "headless
   turns" note (the contract is identical — the agent still writes its
   own round); `docs/roadmap.md` Phase 31 entry marked complete with
   outcome notes; CHANGELOG-style notes in the release commit.
9. **Dogfood acceptance**: one full plan cycle **and** one full impl
   cycle completed with `--mode headless` on a real project before
   0.8.0 is tagged. Proposed: this repo's Phase 32 plan cycle runs
   headless (the impl cycle of Phase 31 itself is the other candidate
   once the code exists — reviewer's call which is less circular).
   Findings (tokens per turn, wall time, anything that broke) recorded in
   `docs/phases/headless-turn-engine-findings.md`.

### Out of Scope (explicitly)

- `tagteam pause` / `resume` / `cancel-turn` / `interject` / `usage` /
  `rollback` — Phase 32. (31 writes the pause marker; 32 adds the verbs.)
- Windows *notification* path — Phase 32 (in 31 `notify_macos` keeps its
  existing silent no-op off macOS; failures are still visible in the
  watcher log and `tagteam tail`).
- Escalation briefer — Phase 33.
- Any dashboard/server change (`server.py`, `tagteam/data/web/`) —
  Phase 34. Usage is recorded, not shown.
- Trimmed/"headless-variant" skill contract — v1 sends the full SKILL.md;
  measurement during soak decides (see Open Questions).
- Resuming prior CLI sessions (`claude --resume`, `codex resume`) —
  fresh spawn every turn; `session_id` is recorded so this can be
  trialed later without a schema change.
- Making headless the default or auto-detected. Never during soak.
- Any change to the interactive backends (`iterm.py`, `session.py`,
  tmux path) beyond accepting the new mode string.
- Removing or renaming any existing flag, file, table, or column.

---

## Technical Approach

### Guiding decision: the agent still writes its own round

The composed prompt tells the spawned agent to follow the SKILL.md
contract exactly as an interactive agent would — i.e. it makes the
`tagteam cycle add` / `cycle init` call itself. The orchestrator does
**not** parse agent prose into a round. Reasons: (a) the SKILL.md contract
stays the single source of truth for both modes; (b) no second, brittle
output-format contract to design and version; (c) the agent needs Bash
anyway (it edits files, runs tests, calls the CLI), so requiring it to
write the round adds no permission surface; (d) failure detection is
trivial and exact — did `seq` advance and the turn flip, or not?

The orchestrator's job per turn is therefore: compose → spawn → stream →
wait/timeout → verify state advanced → record usage → (dispatch next tick
| pause). Agent stdout is captured for `tagteam tail` and post-mortems,
and parsed only for the trailing usage record.

### Adapters (Open Question 2 in the proposal)

```python
@dataclass(frozen=True)
class Adapter:
    provider: str                     # "claude" | "codex"
    argv: list[str]                   # base invocation, prompt via stdin
    parse_final: Callable[[str], TurnResult]   # stdout → usage/session/model
```

- **claude**: `claude -p --output-format json` + permission flags (see
  Open Questions). Final stdout is one JSON object with `usage`
  (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`), `total_cost_usd`, `num_turns`,
  `session_id`, `result`.
- **codex**: `codex exec --json -C <project_root>` + sandbox flag
  (`--sandbox workspace-write`), prompt on stdin (`-`). Stdout is JSONL
  events; the adapter picks the last `turn.completed`/usage-bearing event
  (`input_tokens`, `cached_input_tokens`, `output_tokens`).
- **Step 0 of implementation is a probe**: run each CLI once with a
  trivial prompt, save the *actual* current output as
  `tests/fixtures/headless/{claude,codex}_sample.txt`, and write the
  parsers against those fixtures. Field names above are best current
  knowledge, not a promise; the fixtures are the contract and a drift in
  either CLI is a one-file fix. Provider argv/parse live in one table so
  a third CLI is one entry.

### Prompt composition (Open Question 1)

```
You are the {role} ({agent_name}) in a tagteam handoff cycle for the
project at {root}. This is a headless turn: no human is watching this
terminal. Read the contract below, then act on your turn exactly as it
says, using --updated-by "{agent_name}". Make exactly one cycle-writing
call (tagteam cycle add / tagteam cycle init). When it succeeds, stop.

=== COMMAND ===
{state.command}
=== HANDOFF CONTRACT (.claude/skills/handoff/SKILL.md) ===
{skill_text}
=== CURRENT STATE (handoff-state.json) ===
{state_json}
=== ROUND TAIL (last {N}) ===
{tail_jsonl}
```

Full SKILL.md is included in v1 (~4–5k tokens); usage rows make the
trimmed-variant question measurable rather than argued.

### Schema v3 (proposal Open Question 8 — settled: `PRAGMA user_version` already exists)

```sql
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,          -- turn start (UTC ISO)
    phase         TEXT, type TEXT, round INTEGER, role TEXT,
    agent         TEXT,                   -- name from tagteam.yaml
    provider      TEXT,                   -- claude | codex
    model         TEXT,
    status        TEXT NOT NULL,          -- ok | timeout | nonzero_exit | no_round
    exit_code     INTEGER,
    duration_ms   INTEGER,
    input_tokens  INTEGER, output_tokens INTEGER,
    cache_read_tokens INTEGER, cache_write_tokens INTEGER,
    cost_usd      REAL,
    num_turns     INTEGER,
    session_id    TEXT,
    log_path      TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_phase ON usage(phase, type, round);
```

Downgrade safety: 0.7.x's `_migrate` only raises when
`user_version < SCHEMA_VERSION`; a v3 DB opened by v2 code passes every
guard and ignores the extra table. A test pins this: `_migrate` must not
raise when `user_version` is *greater* than the code's `SCHEMA_VERSION`
(guards the additive-only promise for the rest of the arc).
`db.add_usage()` / `db.get_usage(phase=…, type=…)` are the only new
accessors; `export_to_files`/`import_from_files` are untouched (usage is
runtime data, not cycle history).

### Failure handling detail

| Outcome | Detection | Action |
|---|---|---|
| ok | exit 0, seq advanced, role no longer owed at that round | record usage; next tick dispatches next owed turn |
| timeout | wall clock > `--turn-timeout` | kill process tree; usage row status=timeout; pause |
| nonzero_exit | returncode ≠ 0 (incl. CLI auth/rate-limit failures) | usage row; pause |
| no_round | exit 0 but state unchanged / same role still owed | usage row status=no_round; pause |

"Pause" = diagnostics row + `.tagteam/headless-paused.json` + notification
+ log line with the resume recipe. The turn log path is in all three.
`--turn-retries N` (default 0) re-spawns the same turn up to N times
before pausing; each attempt gets its own usage row and log.

Per-turn logs are retained under `.tagteam/turns/` (already gitignored via
`/.tagteam/*`); the runner prunes to the newest 50 on each start.

### Interaction with the existing tick loop

Headless dispatch is *exactly* what interactive dispatch is — "deliver
`state.command` to the owed agent" — with a fresh process instead of
send-keys. So roadmap mode works unchanged: when the watcher sets
`turn: lead, command: /handoff start X`, the spawned lead turn writes the
plan and inits the cycle; when a plan is approved in roadmap mode and the
lead is set ready, the spawned lead turn implements and inits the impl
cycle (hence the 60-minute default timeout). In single-phase mode the
between-cycles work (implement, `/handoff start X impl`) stays
human-initiated, consistent with "interaction moves to the boundaries".

Because a headless tick blocks for the length of the turn, `tick()`'s
`last_ready_send_time`/`RESEND_TIMEOUT` path is short-circuited when
`mode == "headless"` (guarded by a test), and `try_repair()` still runs
between turns.

### Windows

The headless path touches nothing platform-specific except process-tree
kill and notification (already a no-op off macOS). `shutil.which` handles
`claude.cmd`/`codex.cmd` via PATHEXT. The `windows-latest` CI job is the
verification mechanism; a manual smoke on a real Windows box is
desirable but not gating (see Open Questions).

### Implementation order (each step lands green before the next)

0. Probe `claude -p` / `codex exec` output; commit fixtures. (~½ day)
1. `cycle rounds --tail N` + `cycle.tail_rounds()` + SKILL.md line + tests.
2. Schema v3 + `add_usage`/`get_usage` + migration/downgrade tests.
3. `headless.py`: adapters + composer + runner + verify + pause; fake-agent
   fixture and tests (both flavors, all five outcomes, timeout kill).
4. Watcher integration + flags + startup validation + `_auto_detect_mode`
   guard + resend short-circuit; `test_watcher.py` must pass **unmodified**
   (flag-off byte-identical criterion).
5. `tagteam tail` + tests.
6. `tests.yml` (ubuntu + windows matrix).
7. Docs (README, help text, roadmap, findings doc skeleton).
8. Dogfood cycle headless; write findings; bump to 0.8.0; tag.

---

## Files to Create/Modify

- `tagteam/headless.py` — **new**: adapters, prompt composer, turn runner, outcome verification, pause marker, `tail_command`.
- `tagteam/db.py` — `SCHEMA_VERSION = 3`, `_SCHEMA_V3` usage table, `add_usage`, `get_usage`.
- `tagteam/cycle.py` — `--tail N` in `_cli_rounds`; `tail_rounds(phase, type, n)` helper.
- `tagteam/watcher.py` — `headless` mode string, new flags, headless branch in `_handle_ready`, resend short-circuit, startup validation, done/escalated behavior in headless, help text.
- `tagteam/config.py` — optional `agents.<role>.headless.{provider,args}` parsing + validation; `get_headless_spec(config, role)`.
- `tagteam/cli.py` — `tail` command dispatch + `HELP_TEXT` entries (`watch --mode headless`, `tail`, `cycle rounds --tail`).
- `tagteam/data/.claude/skills/handoff/SKILL.md` and `.claude/skills/handoff/SKILL.md` — `--tail N` mention; headless note. (Both copies; the `data/` one ships.)
- `tests/test_headless.py` — **new**: adapters/parsers against fixtures, composer, runner via fake agent (ok/no_round/nonzero/timeout/malformed), pause marker + no-dispatch-while-paused, usage rows, `tail` command.
- `tests/fixtures/fake_agent.py` — **new**: env-driven fake CLI (claude-JSON / codex-JSONL flavors).
- `tests/fixtures/headless/claude_sample.txt`, `codex_sample.txt` — **new**: real captured outputs from step 0.
- `tests/test_db.py` — v3 migration, `add_usage`/`get_usage`, "user_version greater than code" no-raise guard.
- `tests/test_cycle.py` — `--tail N` cases (N > len, N = 0 rejected, AMEND counted).
- `tests/test_watcher.py` — **unchanged** (criterion); new headless-mode assertions go in `test_headless.py` or a new `test_watcher_headless.py`.
- `tests/test_watcher_auto_detect.py` — assert headless is never auto-detected.
- `.github/workflows/tests.yml` — **new**: pytest on ubuntu-latest + windows-latest.
- `README.md` — "Headless mode (opt-in)" section; CLI reference rows.
- `docs/roadmap.md` — Phase 31 status/outcome; `docs/phases/headless-turn-engine-findings.md` — **new** (dogfood numbers).
- `pyproject.toml` — version 0.8.0 at release. No new dependencies (stdlib `subprocess`/`threading` only).

---

## Success Criteria

- [ ] `tagteam watch` with no `--mode`, or with `notify|tmux|iterm2`, behaves byte-identically to 0.7.1: `tests/test_watcher.py`, `test_watcher_events.py`, `test_watcher_auto_detect.py` pass **without modification**; `_auto_detect_mode` has a test asserting it never returns `headless`.
- [ ] `tagteam watch --mode headless` spawns the owed agent on a `ready` state via the configured/inferred adapter, with the composed prompt on stdin, cwd = project root, and streams output to `.tagteam/turns/<…>.log` while `.tagteam/turns/inflight.json` exists (fake-agent tests, both flavors).
- [ ] After a turn where the fake agent runs `tagteam cycle add`, the orchestrator records `usage.status = ok` with parsed token fields, and the next tick dispatches the *other* role.
- [ ] Each of timeout / nonzero exit / exit-0-with-no-round produces: a usage row with the matching status, a `diagnostics` row (`headless_turn_failed`), `.tagteam/headless-paused.json`, and no further dispatch on subsequent ticks or on restart until the marker is removed. Timeout kills the whole child process tree (verified on POSIX and Windows CI by a fake agent that spawns a grandchild).
- [ ] The watchdog re-send path never fires in headless mode (unit test on `_StateProcessor.tick`).
- [ ] Ctrl-C during an in-flight turn terminates the child and removes `inflight.json`.
- [ ] Malformed/missing usage output on an otherwise-ok turn does not fail the turn; it writes a null-token usage row plus a `headless_usage_unparsed` diagnostic.
- [ ] `tagteam cycle rounds --phase X --type Y --tail N` returns the last N merged entries (AMEND counted), errors on N < 1, returns all when N > len; output format per line unchanged.
- [ ] `tagteam tail` follows an in-flight log and prints the outcome line on completion; with nothing in flight prints the last turn log tail; `--no-follow` exits immediately.
- [ ] `db.SCHEMA_VERSION == 3`; a fresh DB and a v2 DB both migrate to v3 with the `usage` table; a DB with `user_version = 4` opens under v3 code without raising (additive-only guard).
- [ ] Startup with `--mode headless` fails fast (exit 1, clear message) when a role's provider CLI is not on PATH, or when `agents.<role>.headless.provider` is unknown.
- [ ] `.github/workflows/tests.yml` runs the full suite on ubuntu-latest and windows-latest and is green, including all headless fake-agent tests on Windows.
- [ ] README documents headless mode (enable, `tagteam tail`, failure/pause behavior, Windows note); `tagteam --help` and `tagteam watch --help` list the new mode/flags/command; SKILL.md (both copies) mentions `--tail N` and the headless note.
- [ ] One plan cycle and one impl cycle completed end-to-end with `--mode headless` on a real project (see Scope 9); per-turn tokens, wall time, and any incidents recorded in `docs/phases/headless-turn-engine-findings.md`.
- [ ] Released as 0.8.0 (tag matches `pyproject.toml`); revert recipe (`pip install tagteam==0.7.1` + `tagteam upgrade`) verified against a project that ran a headless turn (0.7.1 opens the v3 DB fine).

---

## Open Questions

1. **Permission flags for the spawned CLIs.** The lead turn must edit
   files, run tests, and call `tagteam cycle add`. Options for the
   `claude` adapter default: (a) `--permission-mode acceptEdits` +
   `--allowedTools "Bash,Read,Edit,Write,Glob,Grep"`, (b)
   `--dangerously-skip-permissions`. Recommendation: (a) as the shipped
   default (it is what a trusted interactive session effectively grants
   after a few approvals), with (b) reachable via
   `agents.<role>.headless.args` for users who want it. Codex:
   `--sandbox workspace-write` default (writes inside the repo, which
   covers `.tagteam/`), `--full-auto` via `args`. Reviewer: agree, or
   prefer the stricter default and accept more no_round pauses?
2. **Windows verification level.** Is a green `windows-latest` CI job
   running the fake-agent suite sufficient for the roadmap's "headless
   mode runs on Windows" criterion, or must a real `claude`/`codex` turn
   be run on a Windows machine before 0.8.0? Recommendation: CI is
   gating; a real-CLI smoke is best-effort and recorded in the findings
   doc if done.
3. **Which real cycles satisfy the dogfood criterion.** Proposal: run
   this repo's Phase 32 *plan* cycle headless (both turns), and — once
   the code exists mid-phase — run the last round or two of Phase 31's
   own *impl* cycle headless. Alternatively pick a scratch project.
   Reviewer preference?
4. **Turn-timeout default.** 60 min proposed (roadmap-mode lead turns
   include implementing a phase). Too generous for reviewer turns?
   Per-role timeouts (`--turn-timeout` + `agents.<role>.headless.timeout_minutes`)
   are cheap to add now if the reviewer wants them in 31 rather than 32.
5. **Full SKILL.md every turn vs. trimmed variant.** v1 = full contract;
   the usage table makes the comparison measurable in soak. Confirm we
   defer the trimmed variant to a Phase 32 decision rather than shipping
   both now.

---

## Risks

- **CLI output format drift (`claude -p` JSON / `codex exec` JSONL).**
  Mitigation: parsers written against captured fixtures; usage parse
  failure never fails a turn (null row + diagnostic); adapter table
  isolates the change to one function per provider.
- **Duplicate or wrong-role round writes by the spawned agent** (agent
  runs `cycle add` twice, or as the wrong role). Mitigation: existing
  cycle state machine already rejects out-of-turn writes; the prompt says
  "exactly one" call; outcome verification checks the flip; anything odd
  lands as no_round → pause, never a silent double round.
- **Blocking tick + long turns.** A 60-minute synchronous tick means the
  watcher is unresponsive to state edits made by a human mid-turn.
  Mitigation: acceptable for v1 (headless is opt-in; Phase 32 adds
  `cancel-turn`); Ctrl-C is handled cleanly; poll trigger forced so no
  watchdog callback is starved.
- **Subscription rate limits / auth expiry surface as nonzero exits.**
  This is desired: the orchestrator pauses loudly instead of hammering the
  CLI. Mitigation: pause reason includes the log path; zero retries by
  default.
- **Process-tree kill on Windows** (`claude.cmd` shim → node child).
  Mitigation: `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T`; CI test with
  a grandchild-spawning fake agent on windows-latest.
- **Token cost of full SKILL.md per turn.** Mitigation: measured, not
  guessed — usage rows from the dogfood cycles feed Open Question 5.
- **Scope pressure to add controls (pause/resume/interject) now.**
  Mitigation: explicit Out of Scope; the pause *marker* is designed so
  Phase 32's verbs are a thin CLI over it.
