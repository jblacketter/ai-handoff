# Phase 50: Read-only mode — helper processes cannot write the cycle

## Status
- [ ] Planning — round 1 submitted (2026-09-03)
- [ ] Implementation — branch `phase-50-read-only-mode`
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

Tagteam already spawns, or expects the lead to spawn, processes that must
**read** the cycle and must **never write** it:

- **Panel lenses** (Phase 39). `panel.run_lens` sets `TAGTEAM_PANEL_LENS` in the
  child's environment — but nothing in the package reads that variable. The
  only protection is *post-hoc detection*: the panel compares the round-log
  length and state `seq` before and after the lens ran and marks the lens
  `failed — wrote to the cycle` if either moved. By then the write has
  happened and the cycle is corrupt.
- **The `codex-brief` drafter** and any verifier subagent the lead delegates
  to. Their definitions carry two "hard rules" (never run a cycle-writing
  command, never run the full suite) — but they have Bash, so the rules are
  honor-system. Phase 48 deferred shipping these agents in the plugin
  precisely because "the agent forbidden from making the cycle-writing call"
  is a contract of its own with no enforcement behind it.

Both cases share one root cause: **the one-cycle-writing-call rule is enforced
by prose, not by the CLI.** This phase gives the CLI a read-only mode that a
parent process switches on for any helper it spawns, so a helper that tries to
write is refused *before* anything touches disk — with a message that says
why. Detection stays as belt and braces; prevention is the new layer.

This is the prerequisite the arbiter accepted on 2026-09-03 for revisiting
"reviewer agents in the plugin": *enforced read-only mode first, then one
agent.* Shipping agents is **not** in this phase.

## The design — recommended answers, with the alternatives considered

### 1. The switch: one environment variable, process-scoped

`TAGTEAM_READ_ONLY=1` (any non-empty value other than `0`, `false`, `no`,
case-insensitive, counts as set). An environment variable is the right shape
because the property *is* process-scoped: a parent sets it for the children it
spawns, the children inherit it, and nothing else on the machine is affected.
A marker file would be project-scoped and leak into the lead's own session.

Consequence, documented rather than special-cased: if a human exports it in
their shell, `tagteam watch`, `cycle add`, `rule`, … refuse in that shell too.
That is correct — the variable means "this process may not write."

### 2. Where it is enforced: the two chokepoints, not a command table

Every file-plus-DB state write in the package (`write_state`, `update_state`,
`init_cycle`, `add_round`, gate entries, panel merges, headless writes, pause
markers, rulings — 29 call sites across 10 modules) goes through
`dualwrite.writer_lock`. Every DB-only write (interjections, briefs,
conversations, usage, rate limits, diagnostics) goes through a `db.*` writer
function on a connection from `db.connect`. So:

- **`dualwrite.writer_lock`** raises `ReadOnlyError` on entry when the switch
  is set — *before* `_ensure_tagteam_dir`, before the lock file is opened,
  before the thread lock is taken. Nothing is created or modified. Reads never
  take this lock (by design, see its docstring), so read paths are untouched.
- **`db` writer functions** are guarded by a `@_writes` decorator that raises
  `ReadOnlyError` when the switch is set. The set is every function in
  `db.py` whose body executes `INSERT`, `UPDATE` or `DELETE`; a source-level
  test enumerates those functions and asserts each is decorated, so a new
  writer cannot be added without the guard.

  *Alternative considered and not recommended:* open SQLite with
  `?mode=ro` under the switch. It catches everything, but it changes read
  behaviour when the DB file is absent (`connect` today creates and migrates
  it; read paths rely on that), and the failure surfaces as SQLite's
  "attempt to write a readonly database" instead of ours. The decorator is
  explicit, testable, and a no-op when the switch is unset. The reviewer may
  rule the other way; the rest of the plan does not change.

- **No per-subcommand table in `cli.py`.** `cycle`, `state`, `interject`
  each mix read and write subcommands; a table would drift. Instead the CLI's
  top-level dispatcher catches `ReadOnlyError` and prints one line, exit code
  `2`:

  ```
  tagteam: refused — this process is read-only (TAGTEAM_READ_ONLY is set).
  A helper (panel lens, brief drafter, verifier) returns text; the caller makes the cycle-writing call.
  ```

  Read-only commands (`cycle rounds`, `cycle status`, `gate status`,
  `panel status`, `state` (show), `interject --list`, `contract`,
  `roadmap queue|ready|check`, `usage`, `brief` (show)) run exactly as
  before under the switch — a test asserts each one succeeds with the
  variable set.

### 3. Who sets it

- **`panel.run_lens`** sets `TAGTEAM_READ_ONLY=1` in the lens child's
  environment next to the existing `TAGTEAM_PANEL_LENS`. The post-hoc
  length/`seq` comparison stays (a lens could still write through some path
  we have not imagined; detection is the second net).
- **`briefer`** (escalation briefer child, Phase 33): set it if, and only if,
  the child's only output is its brief file and the parent does the
  `claim_brief`/`finish_brief` bookkeeping — to be confirmed by reading
  `briefer.py` during implementation and stated in the impl submission. If
  the child itself calls a `db` writer today, that call moves to the parent
  in this phase (it is the same bug in a different coat).
- **Headless turns (`headless.py`) do NOT get it** — the lead's and
  reviewer's turns are exactly the processes that must write. A test pins
  that the headless spawn env does not carry the switch even when the
  parent's does not either (and a second test: a parent watcher started
  under the switch refuses at `writer_lock`, so a misconfigured shell fails
  loudly, not silently).
- **Claude Code subagents** (`codex-brief`, verifiers): the agent definition
  prefixes its tagteam calls with `TAGTEAM_READ_ONLY=1`. That prefix is still
  the agent's choice — but once set, the CLI enforces it, which is the
  guarantee the agents never had. Wiring this into a shipped agent is the
  next phase; this phase only updates the arbiter's user-level
  `codex-brief.md` *by hand, outside the repo* (not a tagteam write to
  `~/.claude` — the Phase 48/49 provenance rule holds).

### 4. The contract

One short paragraph in the handoff contract under the lead's turn
("Read-only helpers"): a process the lead delegates to must run with
`TAGTEAM_READ_ONLY=1`; the CLI then refuses every cycle-writing command from
it, so the turn's one write stays with the lead. Applied identically to all
three copies (`plugin/skills/handoff/SKILL.md`,
`tagteam/data/.claude/skills/handoff/SKILL.md`, this repo's
`.claude/skills/handoff/SKILL.md`); the existing byte-identity test and the
vendored-hash check from Phase 48 are the guard.

## Scope

**In:**
- `tagteam/dualwrite.py`: `ReadOnlyError(RuntimeError)`, `read_only() -> bool`
  (the env parse, one place), and the `writer_lock` entry check.
- `tagteam/db.py`: `_writes` decorator on every INSERT/UPDATE/DELETE function.
- `tagteam/cli.py`: catch `ReadOnlyError` at the dispatcher; message + exit 2.
- `tagteam/panel.py`: set the switch for lens children.
- `tagteam/briefer.py`: set the switch for the briefer child if its writes are
  parent-side (see §3); otherwise move them.
- Contract paragraph, three copies.
- `docs/roadmap.md` Phase 50 entry; backlog entry for "reviewer agents in the
  plugin" naming this phase as its dependency.
- Tests (see Verification plan).

**Out:**
- Shipping `codex-brief` / `doc-drift` in the plugin (own phase, depends on
  this one).
- A Claude Code `PreToolUse` hook that blocks writes at the tool layer.
- Any change to what headless turns, the watcher, or the gatekeeper may write.
- Preventing non-tagteam writes (git, files) from a helper — out of tagteam's
  remit.

## Files

- `tagteam/dualwrite.py`, `tagteam/db.py`, `tagteam/cli.py`,
  `tagteam/panel.py`, `tagteam/briefer.py`
- `plugin/skills/handoff/SKILL.md`, `tagteam/data/.claude/skills/handoff/SKILL.md`,
  `.claude/skills/handoff/SKILL.md`
- `tests/test_readonly.py` (new), `tests/test_panel.py` (lens env),
  `tests/test_headless.py` (spawn env), `tests/test_db.py` (decorator coverage)
- `docs/roadmap.md`, `docs/phases/read-only-mode.md`

## Done means

- With `TAGTEAM_READ_ONLY=1`, every cycle-writing CLI command is refused with
  the one-line message and exit 2, and `handoff-state.json`, the round logs,
  the status files and the DB are byte-identical before and after.
- With the switch unset, behaviour is unchanged (the existing suite is the
  proof; no existing test is modified except to add the env cases).
- A panel lens child receives the switch; a headless turn child does not.
- The contract says how a delegated helper must be run, in all three copies.
- Released as **3.11.0**.

## Verification plan

Focused tests while working; the on-submit gate makes the one full-suite run.

- `tests/test_readonly.py`:
  - `writer_lock` under the switch raises `ReadOnlyError` and creates nothing
    (no `.tagteam/`, no lock file) in a fresh temp project.
  - Each `db` writer under the switch raises; each `db` reader succeeds.
  - Source-level: every `db.py` function containing INSERT/UPDATE/DELETE is
    decorated (`inspect.getsource`), so a new writer cannot slip through.
  - CLI: `cycle init`, `cycle add`, `state set`, `interject <note>`, `rule`,
    `pause`, `resume`, `gate run` under the switch → exit 2, the message, and
    unchanged project files (hash before/after).
  - CLI: the read-only commands listed in §2 under the switch → exit 0.
  - Env parse: `1`, `true`, `yes`, `anything` set; `0`, `false`, `no`, empty,
    unset → not set.
- `tests/test_panel.py`: `run_lens` passes `TAGTEAM_READ_ONLY=1` to the child
  (spy on `run_process`'s `env`); the post-hoc detection test stays green.
- `tests/test_headless.py`: the headless spawn env does not carry the switch.
- `tests/test_briefer.py`: whichever of §3's two outcomes applies, pinned.
- Contract: the existing three-copies byte-identity test and the vendored-hash
  test pass with the new paragraph.
