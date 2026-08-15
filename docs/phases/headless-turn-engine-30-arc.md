# Phase 31: Headless Turn Engine (3.0 arc)

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [x] Implementation Review (approved round 3)
- [x] Complete (release: tag v0.8.0 after green Windows CI)

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
   - **Adapter table** — per-provider spec: executable, canonical argv
     (structured *streaming* output, prompt on stdin, cwd), reserved
     flags, and an event parser that yields incremental log lines and the
     final usage record. Two adapters ship: `claude` and `codex`. Argv
     ownership is fully specified under "Adapters" below.
   - **Prompt composer** — builds the bounded turn context: role banner
     (who you are, which agent name to pass as `--updated-by`), the full
     `.claude/skills/handoff/SKILL.md` contract, `handoff-state.json`
     contents, `cycle rounds --tail N` (default N=3, `--tail-rounds`),
     the state's `command`, and a closing instruction ("do the work, then
     make exactly one `tagteam cycle add`/`cycle init` call as the
     contract says, then stop").
   - **Turn runner** — `Popen` with the prompt on stdin, cwd = project
     root, **stdout and stderr as separate pipes** read by two reader
     threads. Per turn, two files under `.tagteam/turns/` with the stem
     `<phase>_<type>_r<N>_<role>_<ts>`:
     - `<stem>.events.jsonl` — raw structured stdout, appended line by
       line as it arrives (this is what usage is parsed from; stderr never
       touches it).
     - `<stem>.log` — the human-followable log: each stdout event
       rendered by the adapter (assistant text, tool calls, results) as
       it arrives, plus every stderr line prefixed `[stderr] `, plus
       runner lines (`[tagteam] spawned pid …`, `[tagteam] outcome …`).
     Both files are flushed per line so `tagteam tail` sees progress
     during the turn, not at exit. Writes `.tagteam/turns/inflight.json`
     (phase/type/round/role/pid/stem/started_at) while running and removes
     it after; enforces `--turn-timeout` (default 60 min) by killing the
     whole process tree (POSIX: `start_new_session=True` + `os.killpg`;
     Windows: `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T /PID`); on
     Ctrl-C kills the child before exiting.
   - **Outcome verification** — see "Outcome verification" below: the
     runner snapshots the owed turn's identity *before* spawning and, after
     exit, requires the **expected cycle transition** by the owed role at
     the expected round for the expected phase/type — not merely "seq
     advanced". Anything else is `timeout` / `nonzero_exit` / `no_round`
     / `spawn_failed`.
   - **Usage capture** — parse the adapter's structured events from
     `<stem>.events.jsonl` only (tokens, cache read/write, cost if
     present, model, session id, num_turns) and insert one `usage` row per
     spawned turn, including failed ones (status column). Malformed/missing usage never fails a turn that
     otherwise succeeded — the row is written with null token fields and a
     `diagnostics` entry (`kind = headless_usage_unparsed`).
   - **Failure handling** — on any non-ok outcome (`timeout`,
     `nonzero_exit`, `no_round`, `spawn_failed`): write a `diagnostics`
     row (`kind = headless_turn_failed`), write
     `.tagteam/headless-paused.json` (reason, phase, type, round, role,
     log_path, ts), send a notification, and log the resume recipe. While
     the pause marker exists the orchestrator refuses to dispatch (also
     on restart) and says why every tick. **No automatic retries in
     Phase 31** — coding turns are not idempotent (a timed-out attempt may
     already have edited the worktree), so any retry is a human decision
     made with the log in hand. A `--turn-retries` flag with explicit
     at-least-once semantics is deferred to Phase 32 alongside `resume`.
     Phase 32's `tagteam resume` clears the marker; in 31 the recipe is
     "inspect the log, fix the tree/state if needed, delete the marker,
     and the watcher resumes on the next tick".
4. **Watcher integration** (`tagteam/watcher.py`):
   - `--mode headless` accepted by `watch_command`; `_auto_detect_mode`
     **never** returns it.
   - New flags: `--turn-timeout MIN` (default 60), `--tail-rounds N` (default 3).
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
   - Startup validation: both roles' headless executables must resolve
     (see "Adapters": `headless.executable` if set, else
     `shutil.which(provider)`), and `headless.args` must contain no
     reserved flags; otherwise exit 1 with a clear message. Existing pause
     marker → log loudly, do not dispatch.
5. **`tagteam tail`** (new CLI command, `tagteam/cli.py` dispatch →
   `headless.tail_command`): follows the in-flight turn's `<stem>.log`
   (from `inflight.json`) like `tail -f`, prints the outcome line when the
   turn ends; with no in-flight turn prints the most recent turn log's
   last `--lines N` (default 40) and exits. `--no-follow` for scripts;
   `--events` follows the raw `<stem>.events.jsonl` instead. Pure Python
   file following (cross-platform).
6. **Config surface** (`tagteam/config.py`, additive, all optional):
   ```yaml
   agents:
     lead:
       name: Claude
       headless:
         provider: claude              # claude | codex (default: inferred, see Adapters)
         executable: /opt/bin/claude   # optional; default shutil.which(provider)
         args: ["--model", "opus"]     # YAML list; validated against the adapter option table
   ```
   `validate_config` checks types, rejects unknown providers, and requires
   `args` to be a list of strings (a string is an error — no shell
   tokenization anywhere); `headless.build_argv()` then validates the
   list structurally against the adapter's option table (positional
   text, `-`, `--`, and reserved options in any spelling are startup
   errors — see "Adapters and argv ownership").
7. **Windows acceptance path**: a new `.github/workflows/tests.yml`
   running pytest on `ubuntu-latest` and `windows-latest` (there is no
   test CI today — only `publish.yml`). The headless tests drive a
   **fake agent CLI** (`tests/fixtures/fake_agent.py`, a Python script
   selected via env var to emulate: writes-round-ok / no-round /
   nonzero-exit / hang-for-timeout / malformed-output /
   unrelated-state-write, in both claude-stream-json and codex-JSONL
   flavors, emitting events *incrementally* with sleeps between them) so
   spawn, streaming, timeout/kill, pause, verification, and usage-parse
   paths run on both OSes. The fixture is installed into a temp PATH dir
   as `claude`/`codex` — a shell script on POSIX and a `.cmd` shim on
   Windows — so tests resolve it through exactly the `shutil.which` +
   PATHEXT path real shims use.
8. **Docs** (proposal §6 standing criterion): README gains a "Headless
   mode (opt-in)" section (what it is, how to enable, `tagteam tail`,
   what happens on failure, Windows note); `docs/how-tagteam-works.md`
   is **not** started here (Phase 36); `HELP_TEXT` and `watch --help`
   updated; SKILL.md (both copies) updated for `--tail N`, a two-line
   "headless turns" note (the contract is identical — the agent still
   writes its own round), and the **`/handoff start [phase] impl`
   clarification** (see "Plan-approved → implement boundary" below);
   `docs/roadmap.md` Phase 31 entry marked complete with outcome notes;
   CHANGELOG-style notes in the release commit.
9. **Dogfood acceptance** (reviewer decision, round 1): (a) the late
   rounds of Phase 31's own impl cycle run with `--mode headless` once
   the engine exists; (b) the complete Phase 32 plan cycle runs headless;
   and (c) the **plan-approved → lead-implements → impl-init boundary is
   exercised headless** at least once (a `ready` lead turn whose command
   is `/handoff start <phase> impl`, produced either by full-roadmap
   auto-advance or by setting that state by hand on a scratch project),
   with the resulting impl submission verified to contain a real
   implementation diff. Findings (tokens per turn, wall time, incidents,
   whether a real Windows CLI smoke happened) recorded in
   `docs/phases/headless-turn-engine-findings.md`.

### Out of Scope (explicitly)

- `tagteam pause` / `resume` / `cancel-turn` / `interject` / `usage` /
  `rollback` — Phase 32. (31 writes the pause marker; 32 adds the verbs.)
- Automatic turn retries (`--turn-retries`) — Phase 32, where at-least-once
  semantics over a possibly-modified worktree can be defined and tested
  next to `resume`.
- Windows *notification* path — Phase 32 (in 31 `notify_macos` keeps its
  existing silent no-op off macOS; failures are still visible in the
  watcher log and `tagteam tail`).
- Escalation briefer — Phase 33.
- Any dashboard/server change (`server.py`, `tagteam/data/web/`) —
  Phase 34. Usage is recorded, not shown.
- Trimmed/"headless-variant" skill contract — v1 sends the full SKILL.md;
  measurement during soak decides (Decisions §5).
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
is the single source of truth for both modes; (b) no second, brittle
output-format contract to design and version; (c) the agent needs Bash
anyway (it edits files, runs tests, calls the CLI), so requiring it to
write the round adds no permission surface; (d) failure detection is
exact — the cycle store either contains the expected transition by the
owed role at the expected round (or the expected new cycle for a `start`
command) or it does not (see "Outcome verification").

The orchestrator's job per turn is therefore: compose → spawn → stream
structured events → wait/timeout → verify the expected transition →
record usage → (dispatch next tick | pause). Agent stdout is a stream of
structured events consumed as it arrives: rendered into the human log for
`tagteam tail` and post-mortems, and mined for the usage record (model,
session id, final token counts) — never for round content.

### Adapters and argv ownership (proposal Open Question 2)

```python
@dataclass(frozen=True)
class Adapter:
    provider: str                 # "claude" | "codex"
    canonical: list[str]          # flags tagteam owns (mode/output/cwd/prompt-source)
    defaults: list[str]           # permission/sandbox flags used unless the user sets them
    reserved: frozenset[str]      # user args containing these are rejected at startup
    overridable: frozenset[str]   # if present in user args, the matching default is dropped
    parse_event: Callable[[str], Event | None]   # one stdout line → rendered log line / usage
```

**Who chooses what (deterministic, no tokenizing):**

| Piece | Source | Notes |
|---|---|---|
| provider | `agents.<role>.headless.provider` if set; else inferred from the basename of the first whitespace token of `agents.<role>.command` (`claude*` → claude, `codex*` → codex); else from `agents.<role>.name` lowercased | Inference is **only** for picking the adapter; the interactive `command` string is never executed or tokenized for headless. Unknown/uninferable provider → startup error. |
| argv[0] | `agents.<role>.headless.executable` if set (a single path/name string, resolved with `shutil.which`); else `shutil.which(provider)` | Windows resolves `claude.cmd`/`codex.cmd` via PATHEXT the same way. Not found → startup error. |
| argv[1:] | `adapter.canonical_head + effective_defaults + validated(headless.args) + adapter.canonical_tail` | Built by `build_argv()` (rules below). `headless.args` must be a YAML **list** of strings and is validated **structurally**, not by token membership. `effective_defaults` = `adapter.defaults` minus any flag family the user set via `overridable`. The prompt-source marker (codex `-`) is always the final token; nothing user-supplied can follow it. |
| cwd / stdin | always project root / always the composed prompt | Not configurable. |

Final argv (subject to the step-0 probe):

- **claude**: canonical `-p --output-format stream-json --verbose`
  (`stream-json` requires `--verbose` in print mode); defaults
  `--permission-mode acceptEdits --allowedTools Bash Read Edit Write Glob Grep`;
  reserved `-p --print --output-format --input-format --verbose
  --resume --continue -c`; overridable `--permission-mode --allowedTools
  --dangerously-skip-permissions --model`. Prompt is read from stdin.
  The stream is JSONL: `system` (init, includes `model`, `session_id`),
  `assistant`/`user` message events, and a final `result` event carrying
  `usage`, `total_cost_usd`, `num_turns`, `session_id`.
- **codex**: canonical `exec --json -C <project_root> --skip-git-repo-check -`
  (`-` = prompt on stdin); defaults `--sandbox workspace-write
  -c approval_policy=never` — an **explicit** non-interactive approval
  policy so a user `config.toml` that would wait for approval is never
  inherited (`codex exec --help` in 0.147.0 exposes `--sandbox`,
  `--approve-for-me`, `--dangerously-bypass-approvals-and-sandbox`, and
  `-c key=value`; there is no `--full-auto`); reserved `--json -C --cd -o
  --output-last-message --output-schema --ephemeral`; overridable
  `--sandbox -s -c approval_policy --approve-for-me
  --dangerously-bypass-approvals-and-sandbox --model -m`. Stdout is JSONL
  events; the parser renders agent messages / command events as log lines
  and takes usage from the terminal turn/usage event.

**`build_argv()` validation of `headless.args` (reviewer round 2, point 1).**
The threat is not just a reserved flag but any user token that the CLI
would read as a *prompt* or an option terminator, silently displacing the
stdin prompt. So user args are parsed, not scanned:

- Each adapter carries an **option table**: `{flag_name: arity}` for the
  options it knows (`--model`: 1, `--permission-mode`: 1, `--allowedTools`:
  variadic-until-next-flag, `--dangerously-skip-permissions`: 0, `-s`/
  `--sandbox`: 1, `-c`: 1, `--approve-for-me`: 0, …). Every token in
  `headless.args` must be consumed as an option from that table (with its
  value tokens) — the parser walks the list left to right and any token
  that is not a known option, or is a value where an option was expected,
  is a **startup error naming the token**. Unknown-but-harmless flags are
  therefore rejected too; the table is the allowlist and users extend it
  by editing config *and* the plan for a new adapter entry, not by
  passing raw strings.
- Consequently rejected: bare text (`"fix the tests"`), `-`, `--`, any
  positional; reserved options in **every spelling** — `--output-format
  json`, `--output-format=json`, `-C dir`, `-Cdir`, `--cd=dir`,
  `--print`, `-p`; and value tokens that themselves look like a
  terminator or marker (`--model --`, `--model -`).
- Reserved-family membership is checked on the **normalized option name**
  (split `--flag=value` at the first `=`, expand `-Cdir` to `-C dir` for
  short options with attached values), so `--output-format=stream-json`
  and `--output-format` are the same family.
- Ordering: `canonical_head` (mode/output flags, `-C <root>` for codex)
  first, then effective defaults, then validated user args, then
  `canonical_tail` (codex: `--skip-git-repo-check -`; claude: nothing —
  the prompt is stdin with `-p` and no positional). Because
  `canonical_tail` is appended after user args, an accepted user option
  can never leave a dangling value that swallows the marker (the parser
  guarantees arity is satisfied) and can never introduce a positional.
- Tests (both adapters): each of the rejected forms above → startup error
  with the offending token; accepted forms (`--model X`,
  `--permission-mode bypassPermissions`, `--allowedTools A B`, `-c
  approval_policy=untrusted`) land in the expected position with the
  overridden default dropped; the last argv token for codex is always `-`
  and the composed prompt is what the fake agent receives on stdin in
  every accepted case.

**Step 0 of implementation is a probe**: run each CLI once with a trivial
prompt using the argv above, save the actual streamed stdout as
`tests/fixtures/headless/{claude,codex}_stream.jsonl` (plus stderr as
`.stderr.txt`), confirm the permission defaults let a real edit + Bash
test + `tagteam cycle add` turn complete without prompting, and write the
parsers against those fixtures. Field names above are best current
knowledge; the fixtures are the contract, and drift in either CLI is a
one-function fix. Adding a third CLI is one table entry.

### Prompt composition (Open Question 1)

```
You are the {role} ({agent_name}) in a tagteam handoff cycle for the
project at {root}. This is a headless turn: no human is watching this
terminal. Read the contract below, then act on your turn exactly as it
says, using --updated-by "{agent_name}". Make exactly one cycle-writing
call (tagteam cycle add / tagteam cycle init). When it succeeds, stop.
{boundary_clause}

=== COMMAND ===
{state.command}
=== HANDOFF CONTRACT (.claude/skills/handoff/SKILL.md) ===
{skill_text}
=== CURRENT STATE (handoff-state.json) ===
{state_json}
=== ROUND TAIL (last {N}) ===
{tail_jsonl}
```

Full SKILL.md is included in v1 (~4–5k tokens; reviewer decision:
evaluate trimming from measured Phase 32 data). `{boundary_clause}` is
empty except in the case below.

### Plan-approved → implement boundary (reviewer point 2)

In full-roadmap mode, plan approval makes the watcher set
`turn: lead, status: ready, command: /handoff start <phase> impl`
(`watcher._try_roadmap_advance`). Today's SKILL.md describes
`/handoff start [phase] impl` only as "create the impl cycle"; a
context-fresh headless lead following it literally would init an impl
cycle over an unchanged tree. Two changes, both mode-independent:

1. **SKILL.md (both copies)** — the `/handoff start [phase]` section
   gains an explicit `impl` paragraph: *before* `cycle init --type impl`,
   read the approved plan (`docs/phases/<phase>.md`) and the plan cycle's
   history (`cycle rounds --phase <phase> --type plan`), implement the
   plan in full, run the project's verification (tests), and only then
   initialize the impl cycle **exactly once** with a submission that
   summarizes what was implemented. If an impl cycle for the phase already
   exists, do not create another — act on it. This is a clarification of
   existing intent, so it applies to interactive sessions too.
2. **Composed prompt** — when the state's command matches
   `/handoff start <phase> impl`, `{boundary_clause}` restates that
   paragraph and lists the plan path explicitly.

Tests: composer emits the clause for that command and not otherwise; a
transition test with the fake agent shows a `ready` lead turn with that
command results in a *new impl cycle at round 1 by the lead* being
counted as `ok`, and an unchanged state as `no_round`. Dogfood item 9(c)
exercises the real boundary and checks the impl submission carries a
real diff.

### Schema v3 (proposal Open Question 8 — settled: `PRAGMA user_version` already exists)

```sql
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,          -- turn start (UTC ISO)
    phase         TEXT, type TEXT, round INTEGER, role TEXT,
    agent         TEXT,                   -- name from tagteam.yaml
    provider      TEXT,                   -- claude | codex
    model         TEXT,
    status        TEXT NOT NULL,          -- ok | timeout | nonzero_exit | no_round | spawn_failed
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

### Outcome verification (reviewer point 3)

Before spawning, the runner snapshots the **owed turn identity**:
`(phase, type, round, role, state.seq, command)` plus the cycle's
`(state, ready_for, round)` from `cycle status`, and the count of round
entries in that cycle. It then derives the **verification target** —
where the expected transition must appear — because for `start` commands
the target is *not* the pre-spawn cycle (reviewer round 2, point 2):

- **Ordinary turn** (command is the standard "act on your turn" text):
  target = the pre-spawn `(phase, type)`, expected round = the owed round.
- **Start command**: the command is parsed by a narrow validator,
  `parse_start_command(cmd) -> (phase, "plan"|"impl") | None`, accepting
  exactly `/handoff start <slug>` and `/handoff start <slug> impl` where
  `<slug>` matches `^[a-z0-9][a-z0-9-]*$` (the roadmap slug alphabet) —
  nothing else (no `--roadmap`, no extra tokens, no other verbs). Target =
  `(<slug>, plan|impl)`, expected round = 1, expected role = lead. This
  covers both cases the pre-spawn identity gets wrong: `/handoff start
  <next-phase>` runs while state still names the completed previous phase,
  and `/handoff start <phase> impl` runs while `state.type` is `plan`.
- **Anything else** (malformed or arbitrary command text) is **not** a
  cycle-init transition: it is verified as an ordinary turn against the
  pre-spawn identity, so a spawned agent that "helpfully" inits a cycle
  under an unrecognized command lands as `no_round`.

The target is used *only* for expected-transition verification; the
composed prompt still passes `state.command` through verbatim. After the
child exits, the outcome is `ok` **iff all** hold:

1. exit code 0;
2. the *expected transition* happened in the cycle store for the same
   `(phase, type)`:
   - owed role `lead`, ordinary turn → a new round entry with
     `role == lead` at `round == expected` (SUBMIT_FOR_REVIEW), so the
     cycle is now `ready_for: reviewer` — or, for a parsed start command,
     a **new cycle** for the *target* `(phase, type)` exists (absent
     pre-spawn) at round 1 with a lead entry and `ready_for: reviewer`;
   - owed role `reviewer` → a new reviewer entry at `round == expected`
     with one of APPROVE / REQUEST_CHANGES / ESCALATE / NEED_HUMAN, and
     the cycle state changed accordingly;
3. `state.phase/type/round` identify the target cycle and round (for
   start commands, the newly created one), and `state.updated_by` is the
   owed agent's name.

Everything else is `no_round` (exit 0 but the expected transition is
absent), `nonzero_exit`, `timeout`, or `spawn_failed` (the CLI process
could not be started at all — `OSError` from `Popen`, e.g. a configured
executable that is not runnable) — including: seq advanced by an
unrelated writer (human `state set`, watcher repair), a round written for
the wrong phase/type, a round at the wrong round number, an AMEND where a
SUBMIT was owed, or a concurrent human write that flipped the turn
without a matching cycle entry. Concurrent writes are not "handled" in
31: the runner reports what it found (the pause reason names the
mismatch) and pauses; the human decides. All of these are fake-agent test
cases and must land as `no_round`/pause, never `ok`.

### Failure handling detail

| Outcome | Detection | Action |
|---|---|---|
| ok | exit 0 and expected transition present (above) | record usage; next tick dispatches next owed turn |
| timeout | wall clock > `--turn-timeout` | kill process tree; usage row status=timeout; pause |
| nonzero_exit | returncode ≠ 0 (incl. CLI auth/rate-limit failures) | usage row; pause |
| no_round | exit 0 but expected transition absent | usage row status=no_round; pause with the specific mismatch in the reason |
| spawn_failed | `Popen` raised `OSError` (missing/non-executable file, permissions) | usage row status=spawn_failed (exit_code null); pause naming the executable + error |

"Pause" = diagnostics row + `.tagteam/headless-paused.json` + notification
+ log line with the resume recipe. The turn's `<stem>` (both files) is in
all three. There are no automatic retries in Phase 31 (see Scope 3 and
Out of Scope): a failed coding turn may already have modified the
worktree, so re-running it is a human call.

Per-turn logs are retained under `.tagteam/turns/` (already gitignored via
`/.tagteam/*`); the runner prunes to the newest 50 stems on each start.

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
desirable but not gating (Decisions §2).

### Implementation order (each step lands green before the next)

0. Probe `claude -p --output-format stream-json --verbose` /
   `codex exec --json` with the argv above; confirm permission defaults
   complete an edit + Bash + `cycle add` turn unprompted; commit stdout /
   stderr fixtures. (~½ day)
1. `cycle rounds --tail N` + `cycle.tail_rounds()` + SKILL.md changes
   (`--tail`, headless note, `start … impl` clarification) + tests.
2. Schema v3 + `add_usage`/`get_usage` + migration tests.
3. `headless.py`: adapters/argv resolution + composer (incl. boundary
   clause) + runner (two streams, two files, incremental flush) + outcome
   verification + pause; fake-agent fixture and tests (both flavors, every
   outcome incl. unrelated-write/wrong-round/wrong-cycle, log-grows-before-
   exit, timeout kill with grandchild).
4. Watcher integration + flags + startup validation + `_auto_detect_mode`
   guard + resend short-circuit; `test_watcher.py` must pass **unmodified**
   (flag-off "behavior unchanged" criterion).
5. `tagteam tail` + tests.
6. `tests.yml` (ubuntu + windows matrix).
7. Docs (README, help text, roadmap, findings doc skeleton).
8. Dogfood per Scope 9 (incl. the impl boundary); run the 0.7.1 downgrade proof; write findings; bump to 0.8.0; tag.

---

## Files to Create/Modify

- `tagteam/headless.py` — **new**: adapter table + `build_argv()` structural validation, `parse_start_command()`, prompt composer (incl. boundary clause), two-stream turn runner, outcome verification with start-command targets, pause marker, `tail_command`.
- `tagteam/db.py` — `SCHEMA_VERSION = 3`, `_SCHEMA_V3` usage table, `add_usage`, `get_usage`.
- `tagteam/cycle.py` — `--tail N` in `_cli_rounds`; `tail_rounds(phase, type, n)` helper.
- `tagteam/watcher.py` — `headless` mode string, new flags, headless branch in `_handle_ready`, resend short-circuit, startup validation, done/escalated behavior in headless, help text.
- `tagteam/config.py` — optional `agents.<role>.headless.{provider,executable,args}` parsing + validation (list-of-strings, reserved flags); `get_headless_spec(config, role)`.
- `tagteam/cli.py` — `tail` command dispatch + `HELP_TEXT` entries (`watch --mode headless`, `tail`, `cycle rounds --tail`).
- `tagteam/data/.claude/skills/handoff/SKILL.md` and `.claude/skills/handoff/SKILL.md` — `--tail N` mention; headless note; `/handoff start [phase] impl` implement-first clarification. (Both copies; the `data/` one ships.)
- `tests/test_headless.py` — **new**: argv resolution (provider inference, executable, reserved/overridable flags), parsers against fixtures, composer (+ boundary clause), runner via fake agent (ok / no_round variants / nonzero / timeout / malformed / unrelated-write), log-grows-before-exit, stderr kept out of events file, pause marker + no-dispatch-while-paused, usage rows, `tail` command.
- `tests/fixtures/fake_agent.py` — **new**: env-driven fake CLI (claude-stream-json / codex-JSONL flavors, incremental emission, optional grandchild), installed per-test into a temp PATH dir as `claude`/`codex` (POSIX script or Windows `.cmd` shim).
- `tests/fixtures/headless/{claude,codex}_stream.jsonl` + `.stderr.txt` — **new**: real captured outputs from step 0.
- `tests/test_db.py` — v3 migration, `add_usage`/`get_usage`, "user_version greater than code" no-raise regression guard (see criterion below for the separate real-downgrade check).
- `tests/test_cycle.py` — `--tail N` cases (N > len, N = 0 rejected, AMEND counted).
- `tests/test_watcher.py` — **unchanged** (criterion); new headless-mode assertions go in `test_headless.py` or a new `test_watcher_headless.py`.
- `tests/test_watcher_auto_detect.py` — assert headless is never auto-detected.
- `.github/workflows/tests.yml` — **new**: pytest on ubuntu-latest + windows-latest.
- `README.md` — "Headless mode (opt-in)" section; CLI reference rows.
- `docs/roadmap.md` — Phase 31 status/outcome; `docs/phases/headless-turn-engine-findings.md` — **new** (dogfood numbers).
- `pyproject.toml` — version 0.8.0 at release. No new dependencies (stdlib `subprocess`/`threading` only).

---

## Success Criteria

- [x] **Flag-off behavior unchanged** (reviewer point 5 wording): for every existing invocation and config — `tagteam watch` with no `--mode`, or with `notify|tmux|iterm2`, existing `tagteam.yaml` files with no `headless` block — runtime behavior is unchanged from 0.7.1 (dispatch, sends, notifications, state writes, files touched). Concretely: `tests/test_watcher.py`, `test_watcher_events.py`, `test_watcher_auto_detect.py`, `test_config.py` pass **without modification**; `_auto_detect_mode` has a test asserting it never returns `headless`. Help text and `--help` output intentionally change and are excluded from this claim.
- [x] `tagteam watch --mode headless` spawns the owed agent on a `ready` state via the resolved adapter argv (provider/executable/args rules above), with the composed prompt on stdin, cwd = project root, stdout → `<stem>.events.jsonl`, stdout-rendered + `[stderr]` lines → `<stem>.log`, while `.tagteam/turns/inflight.json` exists (fake-agent tests, both flavors).
- [x] **Incremental capture**: with a fake agent that emits events with sleeps between them, `<stem>.log` and `<stem>.events.jsonl` grow *before* the process exits (asserted mid-run), and a stderr line emitted by the fake never appears in `<stem>.events.jsonl` nor breaks usage parsing.
- [x] Argv ownership: provider inference (`headless.provider` > interactive `command` basename > name), `headless.executable` override, `headless.args` list validated structurally against the adapter option table (see `build_argv()`), reserved families rejected in every spelling, overridable defaults dropped when the user supplies the family — each unit-tested for both adapters. `headless.args` given as a string is a config error.
- [x] After a turn where the fake agent performs the expected transition (`cycle add` by the owed role at the owed round, or `cycle init` for a `start` command), the orchestrator records `usage.status = ok` with parsed token fields, and the next tick dispatches the *other* role.
- [x] Verification rejects look-alikes: an unrelated `seq` advance, a round for the wrong phase/type, a round at the wrong number, an AMEND where SUBMIT was owed, and a turn flip with no matching cycle entry each land as `no_round` + pause (fake-agent tests), never `ok`.
- [x] Composer adds the implement-first boundary clause exactly when the command is `/handoff start <phase> impl`; a fake lead turn on that command that creates the impl cycle at round 1 is `ok`, and one that leaves state unchanged is `no_round`.
- [x] Start-command targets: `parse_start_command` accepts exactly `/handoff start <slug>` and `/handoff start <slug> impl` (slug alphabet enforced) and rejects everything else; a cross-phase plan start (state still on the completed previous phase, fake lead inits `<next>_plan` r1) and a plan→impl start (state.type `plan`, fake lead inits `<phase>_impl` r1) both verify `ok`; a fake lead that inits a cycle under a malformed/arbitrary command lands as `no_round`.
- [x] `build_argv()` structural validation: bare text, `-`, `--`, `--output-format=json`, `--output-format json`, `-C dir`, `-Cdir`, `--cd=dir`, `--print`, `-p`, and a dangling value (`--model --`) in `headless.args` each fail at startup naming the token, for both adapters; accepted options land in position with the overridden default dropped; codex argv always ends with `-`; the fake agent receives the composed prompt on stdin in every accepted case.
- [x] Each of timeout / nonzero exit / no_round / spawn_failed produces: a usage row with the matching status, a `diagnostics` row (`headless_turn_failed`), `.tagteam/headless-paused.json` naming the specific mismatch, and no further dispatch on subsequent ticks or on restart until the marker is removed. No retry is attempted. Timeout kills the whole child process tree (verified on POSIX and Windows CI by a fake agent that spawns a grandchild).
- [x] The watchdog re-send path never fires in headless mode (unit test on `_StateProcessor.tick`).
- [x] Ctrl-C during an in-flight turn terminates the child and removes `inflight.json`.
- [x] Malformed/missing usage output on an otherwise-ok turn does not fail the turn; it writes a null-token usage row plus a `headless_usage_unparsed` diagnostic.
- [x] `tagteam cycle rounds --phase X --type Y --tail N` returns the last N merged entries (AMEND counted), errors on N < 1, returns all when N > len; output format per line unchanged.
- [x] `tagteam tail` follows an in-flight `<stem>.log` and prints the outcome line on completion; with nothing in flight prints the last turn log tail; `--no-follow` exits immediately; `--events` follows the raw events file.
- [x] `db.SCHEMA_VERSION == 3`; a fresh DB and a v2 DB both migrate to v3 with the `usage` table; **regression guard**: a DB with `user_version = 4` opens under v3 code without raising (protects the additive-only promise going forward — this is *not* the downgrade proof).
- [x] **Downgrade proof (release/dogfood check, recorded in the findings doc)**: in a throwaway venv, `pip install tagteam==0.7.1` against a copy of a project DB that has run headless turns (user_version 3, `usage` rows present) → `tagteam cycle rounds`, `tagteam state show`, and a `cycle add` all work; then `tagteam upgrade` completes the revert recipe.
- [x] Startup with `--mode headless` fails fast (exit 1, clear message) when a role's executable cannot be resolved, when `headless.provider` is unknown/uninferable, or when `headless.args` contains a reserved flag / is not a list.
- [x] `.github/workflows/tests.yml` runs the full suite on ubuntu-latest and windows-latest and is green (run 31870118036, 2026-08-15), including all headless fake-agent tests on Windows through the `.cmd` shim path. (Reviewer decision: this CI is the gating Windows criterion; a real signed-in CLI smoke on Windows is best-effort and the findings doc + release notes state explicitly whether it happened.)
- [x] README documents headless mode (enable, `tagteam tail`, failure/pause behavior, Windows note); `tagteam --help` and `tagteam watch --help` list the new mode/flags/command; SKILL.md (both copies) carries `--tail N`, the headless note, and the `start … impl` implement-first clarification.
- [x] Dogfood per Scope 9(a) and 9(c): Phase 31 impl-cycle review turns headless (r1 done, r2 headless too), and the plan-approved → implement → impl-init boundary exercised headless with a real implementation diff (scratch `greet-cli`); per-turn tokens, wall time, incidents, and Windows-smoke status recorded in `docs/phases/headless-turn-engine-findings.md`.
- [ ] **(soak-period item — revised impl r2)** Scope 9(b): the full Phase 32 plan cycle run headless. Can only happen after 0.8.0 ships and soaks (proposal §5); tracked in the findings doc, closed when Phase 32 planning starts.
- [ ] **(post-approval release step — revised impl r2)** Released as 0.8.0: `pyproject.toml` is bumped to 0.8.0 in the impl; the `v0.8.0` tag push (Jack) follows impl approval and a green Windows CI run. Downgrade proof already completed (findings doc).

---

## Decisions (round 1 open questions, resolved by reviewer)

1. **Permission defaults** — least-privileged unattended defaults that
   still permit workspace edits and the cycle CLI. Claude:
   `--permission-mode acceptEdits` + explicit `--allowedTools` list,
   accepted only after step 0 proves a real edit + Bash test +
   `cycle add` turn completes without prompting (if it prompts, the
   step-0 findings propose the minimal widening). Codex: `--sandbox
   workspace-write` + explicit `-c approval_policy=never` — never inherit
   a user config that could wait for approval. Wider modes reachable via
   `headless.args` (overridable families).
2. **Windows verification** — ubuntu/windows fake-agent CI is gating; the
   fake goes through the same `.cmd`/PATHEXT resolution as real shims; a
   real signed-in CLI smoke is best-effort and its occurrence is stated in
   the findings doc and release notes.
3. **Dogfood** — late Phase 31 impl turns + the complete Phase 32 plan
   cycle, and the plan-approved → implement → impl-init boundary must be
   exercised.
4. **Turn timeout** — one 60-minute default; per-role timeout config
   deferred.
5. **Skill context** — full SKILL.md in v1; trimming evaluated from
   measured Phase 32 data.
6. **Retries** — none in Phase 31 (reviewer point 6); `--turn-retries`
   with defined at-least-once semantics moves to Phase 32.

## Open Questions

- None blocking. If step 0 shows `claude -p --output-format stream-json`
  needs a companion flag beyond `--verbose` (e.g. for the final `result`
  event's usage), the adapter's canonical list absorbs it and the plan's
  Adapters table is amended in the impl submission.

## Risks

- **CLI event-stream drift (`claude -p --output-format stream-json` /
  `codex exec --json` JSONL).**
  Mitigation: parsers written against captured fixtures; usage parse
  failure never fails a turn (null row + diagnostic); adapter table
  isolates the change to one function per provider.
- **Duplicate or wrong-role round writes by the spawned agent** (agent
  runs `cycle add` twice, or as the wrong role). Mitigation: existing
  cycle state machine already rejects out-of-turn writes; the prompt says
  "exactly one" call; outcome verification requires the *expected*
  transition by the owed role at the owed round; anything else lands as
  no_round → pause, never a silent double round.
- **Headless lead inits an impl cycle over an unchanged tree** at the
  plan-approved boundary. Mitigation: SKILL.md implement-first
  clarification + composed boundary clause + transition test + dogfood
  9(c); the impl reviewer's scope-diff also catches an empty diff.
- **`claude -p` stream-json needing companion flags / event shapes
  differing from expectation.** Mitigation: step-0 probe before any
  parser is written; fixtures pin the shape.
- **Blocking tick + long turns.** A 60-minute synchronous tick means the
  watcher is unresponsive to state edits made by a human mid-turn.
  Mitigation: acceptable for v1 (headless is opt-in; Phase 32 adds
  `cancel-turn`); Ctrl-C is handled cleanly; poll trigger forced so no
  watchdog callback is starved.
- **Subscription rate limits / auth expiry surface as nonzero exits.**
  This is desired: the orchestrator pauses loudly instead of hammering the
  CLI. Mitigation: pause reason includes the log stem; no retries.
- **Process-tree kill on Windows** (`claude.cmd` shim → node child).
  Mitigation: `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T`; CI test with
  a grandchild-spawning fake agent on windows-latest.
- **Token cost of full SKILL.md per turn.** Mitigation: measured, not
  guessed — usage rows from the dogfood cycles feed Open Question 5.
- **Scope pressure to add controls (pause/resume/interject) now.**
  Mitigation: explicit Out of Scope; the pause *marker* is designed so
  Phase 32's verbs are a thin CLI over it.
