# Phase 50: Read-only mode — helper processes cannot write the cycle

## Status
- [x] Planning — approved round 3 (2026-09-04, 4cb5f90). Round 1 submitted (2026-09-03); round 2: reviewer blocker on `db.connect` mutating (mkdir / create / WAL pragma / migrate) — design revised, marked *(r2)*; round 3: reviewer gap on the WAL-only sidecar state — fail-closed rule, all four combinations measured, marked *(r3)*
- [x] Implementation — branch `phase-50-read-only-mode`; impl cycle opened 2026-09-04
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
- **`db.connect` is the DB chokepoint — the writer decorators alone are not.**
  *(r2 — reviewer blocker, correct.)* `connect()` today mkdirs `.tagteam/`,
  creates `tagteam.db`, sets `journal_mode=WAL` and runs every pending
  migration before any query — so under the switch a fresh project would
  gain a directory, a DB and sidecars from a mere `usage` or `brief` show,
  and an old-schema DB would be migrated. Round-1's decorator plan did not
  touch this. Revised design:

  `db.read_only_connect(project_dir)` — and `db.connect` **delegates to it
  whenever the switch is set**, so all ~70 call sites inherit the behaviour
  without edits:
  1. `tagteam.db` absent → raise `DatabaseMissing(ReadOnlyError)`. No mkdir,
     no file, nothing.
  2. Open with a SQLite URI chosen by **which sidecars exist** — *(r3)* all
     four combinations measured on this machine (SQLite 3.51, 2026-09-03),
     including a genuine crash state (DB + WAL copied out from under an
     open writer holding committed frames in the WAL, no SHM):

     | on disk | `mode=ro` | `mode=ro&immutable=1` | **rule** |
     |---|---|---|---|
     | neither | cannot open ("unable to open database file") | reads; creates nothing | `immutable=1` |
     | WAL + SHM | reads, **sees WAL frames**; rewrites the existing `-shm` | opens but **misses WAL frames** | `mode=ro` |
     | WAL only | reads WAL frames but **creates `-shm`** | **misses WAL frames** | **refuse** — `WalWithoutIndex(ReadOnlyError)` |
     | SHM only | cannot open | reads the main file; creates/changes nothing | `immutable=1` |

     Fail-closed on WAL-only: neither URI is acceptable there (one creates a
     file, the other can hide committed data), so `read_only_connect`
     closes nothing, creates nothing and raises; the message says any normal
     tagteam command (a writer) repairs the state by checkpointing. A
     refinement — treat a **0-byte** WAL-only as safe for `immutable=1`
     (no frames to miss) — is deliberately *not* taken: one more branch for
     a rare state whose remedy is one ordinary command.
     `immutable=1` is used only when **no `-wal` exists**, so its
     ignore-the-WAL property can never hide data (a stale `-shm` without a
     WAL is inert: measured). The **only** file that may change under any
     read-only open is a *pre-existing* `-shm` in the WAL+SHM state (it is
     the WAL index, not data); `.db` and `-wal` bytes never change, and no
     file is ever created or removed.
     Both common states occur in the wild: this repo keeps `-shm`/`-wal` on
     disk between commands; `~/projects/QA` has only `tagteam.db`.
  3. `PRAGMA query_only=ON` on the connection (second net under `mode=ro`).
  4. `PRAGMA user_version` **<** `SCHEMA_VERSION` → close and raise
     `SchemaBehind(ReadOnlyError)`: read-only mode never migrates; the
     message says any normal (writing) tagteam command migrates it. A
     *newer* `user_version` is allowed (additive-only migration rule; an
     older release reads a newer DB).

  Callers: the cycle / state / gate-status / panel-status readers already
  wrap `db.connect` in `try/except Exception` with a canonical-file
  fallback, so `DatabaseMissing`, `SchemaBehind` and *(r3)* `WalWithoutIndex`
  make them fall back — no edits. The three DB-only readers (`usage`, `brief` show/list,
  `interject --list`) get explicit handling: `DatabaseMissing` → empty
  results, exit 0 (an absent DB truthfully holds no usage/briefs/notes);
  `SchemaBehind` and *(r3)* `WalWithoutIndex` → the CLI refusal line, exit 2.

- **`db` writer functions** additionally carry a `@_writes` decorator that
  raises `ReadOnlyError` — kept as the *clear refusal* for the DB writer API
  (the reviewer's wording), not as the read-only guarantee. The set is every
  function in `db.py` whose body executes `INSERT`, `UPDATE` or `DELETE`; a
  source-level test enumerates those and asserts each is decorated.

  *(r2)* The round-1 "alternative considered and not recommended" (`mode=ro`)
  is withdrawn: the reviewer was right that connection semantics are the
  substance, and the measurements above settle *how* to open read-only.

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
- *(r2)* `tagteam/db.py`: `read_only_connect()` per §2 (absent → `DatabaseMissing`;
  sidecar rule per the §2 table incl. *(r3)* `WalWithoutIndex`; `query_only`;
  `SchemaBehind` on an old schema); `connect()`
  delegates to it under the switch; `_writes` decorator on every
  INSERT/UPDATE/DELETE function. `ReadOnlyError` and the three subclasses live in
  `dualwrite.py` (imported by `db.py`; no new module).
- *(r2)* `tagteam/hub_api.py`: `read_only_connect` delegates to the `db` one
  (keeps its `None`-on-absent / `ProjectDataError`-on-corrupt contract) — fixes
  the latent "unable to open" on a sidecar-less DB.
- *(r2)* `tagteam/usage.py`, `tagteam/briefer.py` (show/list), `tagteam/controls.py`
  (`interject --list`): `DatabaseMissing` → empty results, exit 0.
- `tagteam/cli.py`: catch `ReadOnlyError` at the dispatcher; message + exit 2
  (`SchemaBehind` appends one line naming the migration remedy).
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
  `tagteam/panel.py`, `tagteam/briefer.py`, *(r2)* `tagteam/hub_api.py`,
  `tagteam/usage.py`, `tagteam/controls.py`
- `plugin/skills/handoff/SKILL.md`, `tagteam/data/.claude/skills/handoff/SKILL.md`,
  `.claude/skills/handoff/SKILL.md`
- `tests/test_readonly.py` (new), `tests/test_panel.py` (lens env),
  `tests/test_headless.py` (spawn env), `tests/test_db.py` (decorator coverage)
- `docs/roadmap.md`, `docs/phases/read-only-mode.md`

## Done means

- With `TAGTEAM_READ_ONLY=1`, every cycle-writing CLI command is refused with
  the one-line message and exit 2, and `handoff-state.json`, the round logs,
  the status files and the DB are byte-identical before and after.
- *(r2)* Under the switch, read-only commands **create or remove no file**
  anywhere under the project (including `.tagteam/`), never migrate, and leave
  every file byte-identical — with one measured, documented exception, *(r3)* **applying only to the
  WAL+SHM state**: the pre-existing `tagteam.db-shm` may be rewritten by the
  `mode=ro` open (it is the WAL index; `.db` and `-wal` bytes are asserted
  unchanged). In every other state every file is byte-identical, and in no
  state is a file created or removed. WAL-only is refused, not read.
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
  - *(r2)* **Tree-snapshot matrix** (reviewer's three fixtures × the read-only
    command list; snapshot = every path under the project root + its bytes,
    taken before and after each command):
    1. fresh project — `tagteam.yaml` only, no `.tagteam/`, no DB: every
       command exits 0 (file-derived or empty results); snapshot identical;
       `.tagteam/` does not exist afterwards.
    2. *(r3)* current-schema DB in each of the **four** sidecar states — the
       WAL-bearing ones built as crash copies from an open writer with
       `wal_autocheckpoint=0` so the WAL really holds committed frames:
       - neither: every command exits 0; snapshot identical (nothing created).
       - WAL+SHM: cycle readers return the WAL-held round; `.db` and `-wal`
         identical, no file created or removed, `-shm` allowed to differ.
       - WAL-only: cycle/state readers fall back to files and exit 0; DB-only
         readers exit 2 with the `WalWithoutIndex` refusal; **no `-shm`
         appears**; snapshot identical.
       - SHM-only: every command exits 0; snapshot identical (stale `-shm`
         untouched, nothing created).
    3. old-schema DB (built by running `_SCHEMA_V1` + `user_version=1` into a
       temp file): cycle/state readers fall back to files and exit 0; the
       DB-only readers exit 2 with the refusal; `user_version` still 1 and
       the file byte-identical afterwards.
  - *(r2, r3)* `db.read_only_connect` unit tests, one per table row: absent →
    `DatabaseMissing`, nothing created; WAL+SHM → sees a row committed by a
    still-open writer with `wal_autocheckpoint=0` (the WAL-visibility
    property); WAL-only → `WalWithoutIndex`, no `-shm` created; SHM-only and
    neither → opens and reads, nothing created; `query_only` refuses an
    INSERT through a raw connection.
  - *(r2)* `hub_api.read_only_connect` on a sidecar-less current DB returns a
    working connection (today: raises).
  - Env parse: `1`, `true`, `yes`, `anything` set; `0`, `false`, `no`, empty,
    unset → not set.
- `tests/test_panel.py`: `run_lens` passes `TAGTEAM_READ_ONLY=1` to the child
  (spy on `run_process`'s `env`); the post-hoc detection test stays green.
- `tests/test_headless.py`: the headless spawn env does not carry the switch.
- `tests/test_briefer.py`: whichever of §3's two outcomes applies, pinned.
- Contract: the existing three-copies byte-identity test and the vendored-hash
  test pass with the new paragraph.

## Implementation notes (impl r1)

Deviations from the approved plan, each found while implementing:

- **Runtime markers were not under the writer lock.** The plan listed "pause
  markers" among the writes `writer_lock` covers; `headless.write_pause`,
  `clear_pause`, `write_cancel`, `clear_cancel` and the turn-slot
  claim/update/release write their files directly. They now carry their own
  `_refuse_if_read_only()` check (`tagteam pause` from a helper → exit 2;
  tested in `TestRuntimeMarkers` and the CLI write matrix).
- **`brief` on an absent DB** follows the command's existing empty semantics
  ("No briefs …", exit 1; `[]`/`null` with `--json`, exit 0) rather than the
  plan's blanket "exit 0", so an absent DB and an empty one answer alike.
- **`cycle rounds` reads the canonical files**, not the DB, so the
  WAL-visibility-through-the-CLI assertion in the matrix uses the DB-backed
  reader `usage --json` (a WAL-held usage row). The property itself is pinned
  at the `read_only_connect` level.
- **Hub reader** opts out of the schema check (`require_current_schema=False`):
  the hub reads older DBs by design and its existing test pins v3/v4/v5.
- **Tests** for the child environments and the hub fix live in
  `tests/test_readonly.py` (they reuse the panel / headless / briefer
  harnesses) rather than spread across those files. `tests/test_panel.py`'s
  rogue-lens test split in two: `rogue` is now *refused* (exit 2, no round,
  panel falls back) and `rogue-unset` — a lens that clears the switch, i.e. a
  write through a path the switch does not cover — still trips the post-hoc
  detection and supersedes. The fake agent records the attempt
  (`FAKE_ROGUE_OUT`) and the switch it saw (`read_only` in the capture).
