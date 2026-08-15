# Phase 32: Orchestration Controls & Usage Surfacing (3.0 arc)

## Status
- [x] Planning
- [x] In Review
- [x] Approved (round 7; all review turns headless)
- [x] Implementation (in progress)
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** Make headless mode livable day-to-day by giving the human arbiter
first-class controls over a running orchestration and a first view of the
token data Phase 31 started recording:

- `tagteam pause` / `tagteam resume` / `tagteam cancel-turn` — hold, release,
  and abort dispatch (all watcher modes honor pause; cancel kills the in-flight
  headless turn).
- `tagteam interject "<note>"` — an arbiter note injected into the *next*
  turn's context and recorded, with provenance, in the round history.
- `tagteam usage` — per-turn token usage aggregated by role / phase / cycle
  (this project; `--json` for scripts).
- Cross-platform notifications (`notify()` — macOS, Windows, Linux) so
  headless failures and pauses are visible off-Mac.
- Phase 31 deferrals now that we have measured data: opt-in `--turn-retries N`
  with a *deterministic* at-least-once rule, per-role
  `headless.timeout_minutes`, and the trimmed-contract question closed from
  data.
- Optional: `tagteam rollback X.Y.Z` (prints the revert recipe tailored to
  the install; executes only with `--yes`).

**Why:** Phase 31 shipped the engine; soak on real projects immediately hits
the boundary interactions the proposal §3 promised ("interaction moves to
the boundaries: pause, cancel, interject") and the arbiter today has to
hand-write `.tagteam/headless-paused.json` and `kill` PIDs. Usage rows exist
but nothing reads them. Windows users get log lines but no notification.

**Depends on:** Phase 31 (`tagteam/headless.py`, pause marker, `usage`
table, `inflight.json` with PID). Source brief: `docs/tagteam-3.0-proposal.md`
§4 Phase 32, §8 Q4.

**Size:** medium (Phase 29–30 scale ×2). Single branch
`phase-32-orchestration-controls`, one PR at the end (Jack's rule from
2026-08-15). **Release:** 0.9.0.

---

## Scope

### In Scope

1. **`tagteam pause [--reason TEXT]`** — writes `.tagteam/headless-paused.json`
   (`{reason, by, ts, source: "cli"}`), same file the engine writes on
   failure. **All watcher modes honor it**: `_StateProcessor._handle_ready`
   checks the marker before *any* dispatch (send-keys / notify / headless) and
   logs `PAUSED: <reason>` once per minute instead. Idempotent (re-pause
   updates reason). Exit 0.
2. **`tagteam resume`** — deletes the marker; prints what was paused and for
   how long; if the marker was written by a failed turn, prints the failure
   reason + log path so the operator sees what they are resuming past. Exit 1
   if not paused (`--quiet` → 0).
3. **`tagteam cancel-turn`** — reads `.tagteam/turns/inflight.json`, which
   from this phase records `pid` (child), **`watcher_pid`** (the orchestrator
   process, `os.getpid()` in the engine), `started_at`, and two **stable
   creation identities** captured *at spawn* with the same helper the
   canceller uses: `child_ident = procs.identity(pid)` and `watcher_ident =
   procs.identity(watcher_pid)`. `procs.identity(pid)` returns
   `"<pid>:<creation-time-string>"` straight from the platform tool (POSIX:
   `ps -o lstart= -p PID` — Linux additionally uses `/proc/<pid>/stat`
   starttime ticks when available; Windows: `Get-CimInstance Win32_Process
   … CreationDate`), or `None` if the lookup fails. Because the recorded and
   the recomputed value come from the *same source*, comparison is **string
   equality** — no `>=`, no tolerance, no sub-second/second rounding issue
   (reviewer r2). Before signalling anything it **binds the PID to the
   recorded turn**:
   - reject if `pid` is missing, equals the caller's own pid, or equals
     `watcher_pid`;
   - reject if the process is not alive;
   - reject unless `procs.identity(pid) == child_ident` **and**
     `procs.identity(watcher_pid) == watcher_ident` (a reused pid has a
     different creation time; a crashed-and-restarted watcher likewise; a
     `None` on either side → *unverifiable → do not kill*);
   - reject unless the process's **parent pid equals `watcher_pid`**
     (POSIX `ps -o ppid=`, Windows `ParentProcessId`; lookup failure →
     reject) — belt and braces on top of the identity match.
   On rejection it prints why, **removes the stale `inflight.json`**
   (metadata only — nothing is signalled) and exits 1. On success it writes
   `.tagteam/turns/cancel-requested.json` (`{stem, pid, by, ts}`) and kills
   the tree via `headless.kill_pid_tree(pid)` (POSIX `killpg`, Windows
   `taskkill /F /T /PID`), printing the pid signalled and the marker path.
   The engine, on child exit, checks the cancel marker for its own stem →
   outcome **`cancelled`** (new additive status; usage row + diagnostic
   `headless_turn_cancelled` + pause marker `reason: "cancelled by <by>"` +
   notification); a marker for a *different* stem is logged as stale and
   removed, never applied. Exit 1 if nothing is in flight.
4. **`tagteam interject "<note>" [--by NAME] [--to lead|reviewer]`** — new
   additive table `interjections` (schema v4). Provenance record (proposal
   Q4): `id, ts, by (default: --by, else $TAGTEAM_ARBITER, else OS user),
   note, target_role (NULL = next turn, else lead|reviewer — validated),
   phase, type, round, turn (who was owed when written), delivered_role,
   delivered_round, delivered_stem, delivered_ts` (delivery columns null
   until consumed). **No active owed turn** (no state file, or status not
   `ready`/`working`, e.g. `done`/`escalated`): the note is still stored
   with **`phase`, `type`, `round`, `turn` all NULL** (one rule, used
   everywhere); what the CLI *observed* at write time is preserved in a
   separate provenance column `observed_state` (JSON: the state's
   phase/type/round/status/result, or null when no state file) so the audit
   trail keeps the context without scoping the note to a finished cycle. A
   warning is printed: "no turn is currently owed; the note will be
   delivered to the first eligible turn of the next cycle". Delivery:
   - **Eligibility** for a headless turn of role R whose verification
     target is cycle `(P, T)` (reviewer r2 — no replay of historical notes):
     rows with `delivered_ts IS NULL AND retired_ts IS NULL AND (target_role
     IS NULL OR target_role = R) AND ((phase = P AND type = T) OR phase IS
     NULL)`, ordered by id. Notes are **scoped to the cycle they were
     written for**; a NULL-phase note (written when nothing was owed) is
     eligible for the **first *eligible* turn** of whatever cycle starts
     next — "eligible" includes the role filter, so a NULL-phase `--to
     reviewer` note waits across cycle B's lead turn and lands on B's first
     reviewer turn — and, once delivered, is stamped like any other. Notes
     for another cycle, or for a cycle that has reached a terminal state,
     are never injected.
   - **Retirement**: notes are append-only, but a note can be closed without
     delivery: `tagteam interject --list` shows pending/delivered/retired
     rows for the current cycle; `tagteam interject --retire ID` sets
     `retired_ts` (additive column) with `by`. Notes belonging to a cycle
     that becomes terminal are not auto-mutated (audit trail) — they simply
     stop being eligible.
   - **Same-cycle mode switch** (intended, explicit): a note surfaced to an
     interactive agent via `cycle rounds` at round k and *not* retired is
     still eligible when the same cycle later runs a headless turn — it is
     injected once, and the prompt block says "may already have been
     addressed in earlier rounds; verify before acting". Cross-cycle it is
     not eligible (scoping above).
   - **Headless:** `compose_prompt` gains an
     `=== ARBITER INTERJECTIONS (unconsumed) ===` block listing the selected
     notes (`[ts] by (→ target or "next turn"): note`); the engine keeps the
     **exact list of ids it rendered** and, only if the turn's outcome is
     `ok`, stamps `delivered_*` for *those ids* (never "all pending" — a note
     written mid-turn, or targeted at the other role, stays pending). A note
     targeted at role X waits across the other role's `ok` turns untouched
     and is delivered + stamped on X's next `ok` turn.
   - **Interactive:** `tagteam cycle rounds` (and `--tail`) attaches an
     additive `interjections: [...]` list to the round each note targets, so
     an agent reading its feedback sees them; SKILL.md gets a two-line note
     ("if the round shows `interjections`, treat them as arbiter
     instructions"). Delivery stamping in interactive mode is *not* attempted
     (there is no turn boundary to observe) — the columns stay null and the
     note is still visible.
   - `cycle render` markdown gains an "Arbiter interjections" line per round.
   - The rounds JSONL / DB `rounds` table are untouched (the `role` CHECK
     constraint forbids a third role; interjections are a sibling table, not
     a round entry).
5. **`tagteam usage [--phase P] [--type T] [--role R] [--json] [--limit N]`**
   — reads the `usage` table (this project). Default output: one line per
   turn (newest last: ts, phase/type/round, role, provider, status, duration,
   in/out/cache-read/cache-write, cost) followed by roll-ups **by role**,
   **by phase+type (cycle)**, and **totals** (turn count, ok/failed split,
   token sums, cost sum over non-null, mean duration). `--json` emits
   `{"turns": [...], "by_role": {...}, "by_cycle": {...}, "totals": {...}}`.
   No cross-project mode (Phase 35 hub).
6. **`tagteam/notify.py`** — `notify(title, message)`: macOS `osascript`
   (existing behavior, byte-identical), Windows PowerShell WinRT toast
   (`Windows.UI.Notifications`, no dependency; falls back to `msg` when
   PowerShell is unavailable), Linux `notify-send` if on PATH; all
   best-effort with a 5 s timeout, never raise. `watcher.notify_macos`
   becomes a thin alias to keep existing tests/patches (`patch("tagteam.
   watcher.notify_macos")`) working unchanged; `headless.py` uses the same
   alias. `TAGTEAM_NO_NOTIFY=1` disables all notification (useful in CI and
   tests).
7. **Headless follow-ups from Phase 31 (measured):**
   - `--turn-retries N` (default 0) with a **deterministic at-least-once
     rule** gated on *both* repository and handoff state (reviewer r1):
     before spawning, snapshot (a) the **repo fingerprint** — *content-
     sensitive* (reviewer r2): sha1 of `git rev-parse HEAD` ‖ `git
     write-tree` of the real index (staged content) ‖ `git write-tree` of a
     **temporary index** built by `GIT_INDEX_FILE=<tmp> git read-tree HEAD
     && git add -A && git write-tree` (worktree content of every tracked and
     non-ignored untracked path, hashed by git itself — so editing an
     already-modified tracked file, or changing the bytes of an already-
     present untracked file, changes the fingerprint; ordering is git's own,
     deterministic; the temp index file is deleted afterwards) ‖ **for every
     gitlink, recursively** (reviewer r4–r6, final form — no `foreach`, no
     whitelist): at each repository level, `GIT_INDEX_FILE=<tmp> git
     ls-files --stage -z` (NUL-framed, lossless for any path bytes) is
     parsed into `(mode, sha, stage, path)` records; every mode-`160000`
     entry is recursed into *by construction* — the same triple (HEAD ‖
     index tree ‖ temp-index tree) is computed with cwd = that gitlink
     directory, whether it is a registered submodule, an uninitialised one,
     an unregistered embedded repository, or a `.gitmodules`-declared path
     with a nested repo — because the temp-index tree only records the
     gitlink's HEAD, the recursion is what makes edits *inside* it visible.
     A gitlink directory with no `.git` (uninitialised submodule, no working
     tree) contributes its recorded SHA only — there is nothing on disk to
     edit. The digest is a length-prefixed, NUL-framed record stream —
     `level-path\0kind\0value\0` for each of HEAD / index-tree / temp-tree
     / gitlink-path — so the (level, path) association is *inside* the
     hash, not inferred from line splitting; nested gitlinks are matched at
     their own level by recursion, never by string comparison against a
     root-relative list. **Fail closed**     generally (reviewer r3): if *any* git command in the fingerprint fails
     or times out (unmerged/conflicted index, missing submodule checkout,
     corrupt repo, git not on PATH), or the recomputed fingerprint cannot be
     produced, `repo_fingerprint()` returns the sentinel `UNSUPPORTED` and
     the attempt is **non-retryable** — no unchanged-tree rule is ever
     applied silently. Only `.gitignore`d paths remain outside a successful
     fingerprint (documented blind spot). And
     (b) the **handoff fingerprint** = state `seq` + the target cycle's entry
     count + `(state, ready_for, round)` from `cycle status`. A failed
     attempt is retried only if outcome ∈ {`spawn_failed`, `nonzero_exit`,
     `timeout`} **and both fingerprints are unchanged** after the attempt.
     Any handoff transition (e.g. the agent's `cycle add` succeeded and it
     then exited nonzero) → **never retry** — pause. `no_round`, `cancelled`,
     any tree change → pause. Non-git projects (`git rev-parse` says "not a
     repository"): repo fingerprint is None and only `spawn_failed` is
     retryable (nothing ran; handoff fingerprint still checked). Git repo
     but fingerprint `UNSUPPORTED` (before or after the attempt): nothing is
     retryable.
     Every attempt has its own usage row / log stem; the log says
     `[tagteam] retry k/N (repo + handoff fingerprints unchanged)`.
   - `agents.<role>.headless.timeout_minutes` (config; overrides
     `--turn-timeout` for that role; validated positive int).
   - **Trimmed skill contract — closed, keep full.** From Phase 31's usage
     rows: three reviewer turns consumed 1.69 M / 716 K / 282 K input tokens
     (91–96 % cache-read); the SKILL.md contract is ≈4–5 K tokens per turn,
     <1 % of the smallest turn. Nothing to win. Recorded here and in the
     Phase 31 findings; no `--trimmed-contract` flag.
8. **`tagteam rollback X.Y.Z` (optional, small):** detects how tagteam is
   installed (`sys.executable` under a `uv/tools/tagteam` path → `uv tool
   install tagteam==X.Y.Z --force`; else `python -m pip install
   tagteam==X.Y.Z`), prints that command plus `tagteam upgrade` and the
   registry size ("re-copies framework files into N registered projects"),
   and executes only with `--yes`. Version must match `^\d+\.\d+\.\d+$`.
9. **Docs**: README (Controls section: pause/resume/cancel/interject/usage;
   Windows notifications; retries rule; rollback), `tagteam --help`,
   `watch --help`, SKILL.md (both copies: interjections line), roadmap Phase
   32 entry, findings doc `docs/phases/orchestration-controls-findings.md`
   (dogfood numbers), Phase 31 findings 9(b) closed by this phase's plan
   cycle running headless.
10. **Dogfood**: this plan cycle's reviewer turns run headless (closes Phase
    31 finding 9(b)); the impl cycle's reviewer turns run headless too;
    during them exercise `tagteam pause` (instead of the hand-written
    marker), `tagteam interject` (delivered to the next reviewer turn — the
    delivered stem visible in `tagteam usage`/DB), and `tagteam usage`
    output over the real rows. `cancel-turn` exercised against a scratch
    project's live turn.

### Out of Scope (explicitly)

- Escalation briefer — Phase 33. Cockpit/SSE/usage *panels* — Phase 34.
  Cross-project hub / aggregate burn — Phase 35 (so no `usage --all`).
- Any dashboard/server change.
- Windows *interactive* backends (iTerm2/tmux equivalents). Windows gets
  notifications + headless.
- Editing existing round entries, new round actions, or any change to the
  `rounds`/`cycles` schema (additive-only: new table + new status value).
- Automatic retries beyond the deterministic tree-unchanged rule (no "smart"
  retry, no backoff policy).
- Interjection *editing/deletion* CLI (append-only audit trail; the DB is
  the record).

---

## Technical Approach

### Files

- `tagteam/controls.py` — **new**: `pause_command`, `resume_command`,
  `cancel_turn_command`, `interject_command` (incl. `--list`, `--retire`),
  `rollback_command`; helpers
  `write_pause_cli`, `read_cancel`, `write_cancel`, `clear_cancel`;
  `add_interjection`/`pending_interjections`/`mark_delivered` thin wrappers
  over db.
- `tagteam/usage.py` — **new**: `usage_command`, `aggregate(rows)` (pure),
  text + JSON renderers.
- `tagteam/notify.py` — **new**: `notify()`, per-platform backends,
  `TAGTEAM_NO_NOTIFY`.
- `tagteam/db.py` — `SCHEMA_VERSION = 4`; `_SCHEMA_V4` (`interjections`
  table + index); `USAGE_STATUSES += {"cancelled"}`; `add_interjection(...,
  target_role=None)` (validates role), `get_interjections(phase=None,
  type=None, undelivered_only=False)`, `pending_interjections_for(role,
  phase, type)` (undelivered, unretired, untargeted-or-matching, same cycle
  or NULL-phase, by id), `mark_interjections_delivered(ids, role, round,
  stem, ts)` (exact ids), `retire_interjection(id, by, ts)`.
- `tagteam/headless.py` — `_kill_tree(pid)` refactor + `kill_pid_tree(pid)`
  public; `inflight.json` gains `watcher_pid`; cancel-marker detection →
  `OUTCOME_CANCELLED`; interjection block in `compose_prompt` (new kwarg,
  rendered ids retained) + delivery stamping of exactly those ids on `ok`;
  retries loop with repo + handoff fingerprints; per-role timeout;
  `notify()` via alias. `tagteam/procs.py` (**new**, small): `pid_alive`,
  `parent_pid`, `process_start_time` (POSIX `ps`, Windows PowerShell), used
  by `cancel-turn`'s identity binding and by tests; `identity(pid)` returns
  the same-source creation string used for equality checks.
- `tagteam/watcher.py` — pause check in `_handle_ready` for all modes (rate-
  limited log); `notify_macos = notify` alias; `--turn-retries`; help.
- `tagteam/cycle.py` — `tail_rounds`/`_cli_rounds` attach `interjections`
  per round; `render_cycle` line.
- `tagteam/config.py` — `headless.timeout_minutes` (positive int) in
  validation + `get_headless_spec`.
- `tagteam/cli.py` — dispatch `pause`, `resume`, `cancel-turn`, `interject`,
  `usage`, `rollback`; `HELP_TEXT`.
- `tagteam/data/.claude/skills/handoff/SKILL.md` + local copy — interjections
  note.
- Tests: `tests/test_controls.py` (new), `tests/test_usage.py` (new),
  `tests/test_notify.py` (new, backends mocked via `subprocess.run` patch),
  additions to `tests/test_headless.py` (cancel outcome, interjection
  delivery, retries rule with a fake agent that dirties / doesn't dirty the
  tree, per-role timeout), `tests/test_watcher.py` untouched + new
  `tests/test_watcher_pause.py` (pause honored in notify/tmux/iterm2 via
  patched send functions), `tests/test_db.py` (v4 migration, interjections
  CRUD, `cancelled` status), `tests/test_cycle.py` (rounds show
  interjections).
- README, roadmap, findings, `pyproject.toml` → 0.9.0 at release.

### Schema v4 (additive)

```sql
CREATE TABLE IF NOT EXISTS interjections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    by              TEXT,
    note            TEXT NOT NULL,
    target_role     TEXT CHECK (target_role IS NULL OR target_role IN ('lead','reviewer')),
    phase           TEXT, type TEXT, round INTEGER, turn TEXT,   -- all NULL when nothing was owed
    observed_state  TEXT,                                        -- JSON snapshot of state at write time (provenance only)
    delivered_role  TEXT, delivered_round INTEGER,
    delivered_stem  TEXT, delivered_ts TEXT,
    retired_ts      TEXT, retired_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_interjections_phase ON interjections(phase, type, round);
```
`usage.status` vocabulary: `ok | timeout | nonzero_exit | no_round |
spawn_failed | cancelled` (the Phase 31 vocabulary test is extended; the
Phase 31 plan's comment line is updated in the same commit so the test that
compares it to `db.USAGE_STATUSES` keeps passing — the plan doc is
historical but that guard reads it).

Downgrade: 0.8.0 (`SCHEMA_VERSION 3`) opens a v4 DB (tolerates newer
`user_version`, ignores the extra table; a `cancelled` row is just a string
to 0.8.0 readers). Verified in the release checklist as in Phase 31.

### Cancel flow

```
tagteam cancel-turn:
   inflight = read inflight.json          (else: exit 1 "nothing in flight")
   bind(inflight):                        pid ∉ {missing, self, watcher_pid}
                                          ∧ alive(pid)
                                          ∧ identity(pid) == inflight.child_ident        (None ⇒ reject)
                                          ∧ identity(watcher_pid) == inflight.watcher_ident (None ⇒ reject)
                                          ∧ parent_pid(pid) == watcher_pid              (unverifiable ⇒ reject)
   if not bound: print reason; remove stale inflight.json; exit 1  (nothing signalled)
   write .tagteam/turns/cancel-requested.json {stem,pid,by,ts}
   kill_pid_tree(pid)
engine (in run_owed_turn, after run_process returns):
   if cancel marker exists and marker.stem == this stem:
       outcome = cancelled; remove marker
   elif marker exists for another stem: log + remove (stale)
```
Race: if the child exits normally between marker write and kill, the engine
still finds the marker → `cancelled` (the arbiter asked for it). If the
marker is written but the kill fails, the turn continues; the engine sees
the marker at exit and labels the outcome `cancelled` regardless of exit
code — the operator's intent wins and dispatch pauses. `cancel-turn` prints
the pid it signalled and the marker path.

### Pause in all modes

`_StateProcessor._handle_ready` (all modes): `if headless.read_pause(project_dir): log_paused (rate-limited) ; return` before mode dispatch. The
`last_ready_send_time` watchdog is **not** armed while paused (otherwise
resume would look like a re-send). Startup banner shows pause state in every
mode. `resume` does not itself re-dispatch: the next tick sees the same seq
→ ready state, so `_StateProcessor.tick` gets a small change: when a pause
was observed on the previous tick and is now cleared, treat the current
ready state as new (re-dispatch once). Tested for notify + headless.

### Retries rule (deterministic at-least-once)

```
repo_pre    = repo_fingerprint()      # sha1(HEAD ‖ write-tree(index) ‖ write-tree(tmp index after add -A) ‖ same triple per submodule, recursive)
                                      # None = not a git repo; UNSUPPORTED = any git failure / unmerged index /
                                      #   any parse/recursion failure — fail closed. Every 160000 gitlink at every
                                      #   level (from `ls-files --stage -z`) is recursed into; NUL-framed (level,path)
                                      #   records are part of the digest.
handoff_pre = handoff_fingerprint()   # (state.seq, target cycle entry count, cycle state/ready_for/round)
for attempt in 0..N:
    run turn → outcome
    if outcome == ok: break
    retryable   = outcome in {spawn_failed, nonzero_exit, timeout}
    handoff_ok  = handoff_fingerprint() == handoff_pre        # any transition ⇒ never retry
    repo_post   = repo_fingerprint()
    repo_ok     = (repo_pre not in (None, UNSUPPORTED) and repo_post == repo_pre) \
                  or (repo_pre is None and repo_post is None and outcome == spawn_failed)
                  # UNSUPPORTED on either side ⇒ False (fail closed)
    if attempt < N and retryable and handoff_ok and repo_ok: log retry; continue
    fail(outcome) ; break
```
Each attempt: own stem, usage row (`attempt` is *not* a new column — the
stem timestamp orders them; the log line names the attempt number). Tests
(fake-agent modes): nonzero **after** a successful `cycle add` → no retry;
`git commit` then nonzero → no retry; write inside an already-untracked
directory then nonzero → no retry; **edit an already-modified tracked file**
then nonzero → no retry; **change the contents of an already-present
untracked file** then nonzero → no retry; `git add` (stage only) then
nonzero → no retry; **edit inside a pre-existing dirty submodule** then
nonzero → no retry; **an already-present untracked embedded repository
(not a submodule) dirtied again** → fingerprint changes (it is recursed
into) → no retry; **a `.gitmodules`-declared path with no checkout,
holding an untracked nested repo dirtied again** → changes → no retry;
**a submodule whose path contains a newline plus a separate embedded repo
whose path equals the newline-delimited suffix** → each fingerprinted at
its own (level, path) record → the embedded repo's edit changes the
digest; **nested submodule** (sub-submodule) edited → changes at its own
level; **unmerged
index** (or git failure) → `UNSUPPORTED` → no retry even for a clean
nonzero; clean nonzero → retried; `no_round` / `cancelled` → never
retried.

### Notifications

`notify.py`:
- darwin: `osascript -e 'display notification ...'` (unchanged text).
- win32: `powershell -NoProfile -NonInteractive -Command <toast script>`
  using `Windows.UI.Notifications.ToastNotificationManager` with AppId
  `Tagteam`; on failure fall back to `msg %USERNAME% "<title>: <message>"`.
- linux: `notify-send "<title>" "<message>"` if `shutil.which("notify-send")`.
- All wrapped: `TAGTEAM_NO_NOTIFY` short-circuits; 5 s timeout; exceptions
  swallowed. Tests patch `subprocess.run` and assert per-platform argv
  (parametrized over `sys.platform` values via monkeypatch), plus the
  env-var short-circuit; the Windows toast script itself is verified only
  by CI on `windows-latest` running the "does not raise, returns within
  timeout" test (real toast may not render on a headless runner — that is
  acceptable; the *fallback path* is what's asserted there).

### Interject provenance & delivery

`interject` writes the row with the *current* owed identity (phase, type,
round, turn — or all four NULL, with `observed_state` provenance and a
warning, when nothing is owed) plus
`target_role` (`--to`, default NULL = next turn) so an auditor knows the
state of the loop when the arbiter spoke and who it was for. The engine
selects `pending_interjections_for(role, target_phase, target_type)` when
composing (cycle-scoped — see Eligibility), renders them, and remembers
their ids; stamping happens only when the outcome is `ok`
and only for those ids (a note whose turn failed remains pending and is
re-delivered to the retry/next eligible turn — it was never *acted on*;
a note targeted at the other role is not even rendered). `usage`/DB show
`delivered_stem`, which maps 1:1 to the turn log the note went into.

### Implementation order

0. Schema v4 + statuses + db CRUD + tests.
1. `notify.py` + watcher/headless alias + tests (unblocks visible failures
   on Windows early).
2. `controls.py`: pause/resume (+ all-mode pause in watcher, re-dispatch on
   resume) → cancel-turn (+ engine `cancelled`) → interject (+ prompt block,
   delivery, rounds attach, render) — each with tests.
3. `usage.py` + tests.
4. Headless follow-ups: retries rule, per-role timeout, findings note on the
   trimmed-contract decision.
5. `rollback` (optional; last, small).
6. Docs; dogfood (pause / interject / usage during this repo's impl cycle;
   cancel-turn on scratch); findings; bump 0.9.0; PR.

---

## Success Criteria

- [ ] `tagteam pause --reason x` writes the marker; every watcher mode
  (notify, tmux, iterm2, headless) skips dispatch while it exists and logs
  `PAUSED` at most once per minute; the watchdog re-send does not fire while
  paused; `tagteam resume` removes it, prints the pause reason/duration (and
  the failure reason + log path when the marker came from a failed turn),
  and the watcher re-dispatches the still-ready state exactly once on its
  next tick (tests for notify + headless with the fake agent).
- [ ] `tagteam cancel-turn` with a live headless turn kills the process tree
  (grandchild dead), the engine records outcome `cancelled` (usage row,
  `headless_turn_cancelled` diagnostic, pause marker with "cancelled by",
  notification), and the cancel marker is gone afterwards; with nothing in
  flight it exits 1; a stale cancel marker for another stem is reported and
  removed by the engine, never applied.
- [ ] Stale-inflight safety: a hand-written `inflight.json` whose `pid` is an
  unrelated sleeper (parent ≠ recorded `watcher_pid`) is rejected — the
  sleeper stays alive, the stale file is removed, exit 1 with the reason;
  same for `pid` = self, `pid` = `watcher_pid`, missing pid, dead pid, an
  unverifiable parent/identity lookup, and — mocked — numeric child and
  parent pids that match the metadata while `child_ident` **or**
  `watcher_ident` differs (proves no kill on identity mismatch); the engine
  records both identities at spawn; the live child/grandchild kill test still
  passes.
- [ ] `tagteam interject "note" [--to R]` stores a row with
  `by/ts/target_role/phase/type/round/turn` (`--to` validated; no owed turn →
  `phase/type/round/turn` all NULL, `observed_state` filled, warning
  printed); the next eligible headless turn's prompt
  contains the note under the ARBITER INTERJECTIONS heading (fake-agent
  capture); after that turn is `ok` exactly the rendered ids have
  `delivered_role/round/stem/ts` set and a later prompt does not repeat them;
  a note written while a turn is in flight is delivered to the following
  turn; a failed turn leaves the note undelivered; a note `--to reviewer`
  written before a lead turn is absent from the lead's prompt and
  undelivered after the lead's `ok`, then present in and stamped by the
  reviewer's `ok` turn.
- [ ] Interjection scoping: a note written and surfaced (via `cycle rounds`)
  during an interactive cycle for phase A is **not** injected into a later
  headless turn of phase B (nor into any turn after cycle A is terminal);
  the same note **is** injected exactly once into a later headless turn of
  the *same* cycle A (mode switch) with the "may already have been
  addressed" caveat, then stamped; **terminal state for cycle A →
  `interject` (untargeted, and a second one `--to reviewer`) → start cycle
  B**: the untargeted note is injected + stamped on B's first `ok` turn
  (the lead's), the targeted one is absent from that lead turn and injected
  + stamped on B's first reviewer `ok` turn — neither is stranded on A;
  `interject --retire ID` removes a note from eligibility and `--list`
  shows pending/delivered/retired.
- [ ] `tagteam cycle rounds` / `--tail N` output attaches
  `interjections: [...]` to the targeted round (empty list otherwise);
  `cycle render` shows them; the rounds JSONL and `rounds` table are
  byte/row-identical to before (no new entries).
- [ ] `tagteam usage` prints per-turn lines and by-role / by-cycle / totals
  roll-ups over the project's `usage` rows; `--json` shape as specified;
  filters work; `aggregate()` unit-tested on crafted rows including null
  tokens/cost and failed statuses.
- [ ] `notify()` dispatches to the platform backend (argv asserted per
  platform with `subprocess.run` patched), never raises when the backend is
  missing or fails, honors `TAGTEAM_NO_NOTIFY=1`; existing
  `patch("tagteam.watcher.notify_macos")` tests pass unmodified.
- [ ] `--turn-retries N`: a `nonzero_exit` fake turn that leaves repo and
  handoff fingerprints unchanged is retried up to N times (own stems/usage
  rows, retry log line) and succeeds when the fake starts behaving; the same
  failure after (a) a successful `cycle add`, (b) a `git commit`, (c) a write
  inside an already-untracked directory, (d) any tracked-file edit, (e) a
  further edit to a tracked file that was *already modified* before spawn,
  (f) a content change to an *already-present untracked* file, (g) a
  stage-only `git add`, or (h) an edit inside a *pre-existing dirty
  submodule*, pauses immediately with no retry; a repo whose fingerprint is
  `UNSUPPORTED` (unmerged index / mocked git failure / a mocked parse or
  recursion failure) never retries anything and the log names the reason;
  gitlink cases are *covered by recursion*, each asserted to change the
  fingerprint and therefore suppress retry: (i) an already-present untracked
  embedded repository (real nested `git init`, not a submodule) dirtied
  again, (j) a declared-only `.gitmodules` path (committed entry, no gitlink
  in HEAD/index) holding a nested repo dirtied again, (k) a submodule whose
  path contains a newline **plus** a separate embedded repository whose
  path equals the newline-delimited suffix (the embedded repo's edit is
  detected at its own record; the newline path is framed losslessly), and
  (l) a nested sub-submodule edited (matched at its own level); a repo with
  no gitlinks at all still retries clean turns normally;
  `no_round` and `cancelled` are never retried; a non-git project retries
  only `spawn_failed`.
- [ ] `agents.<role>.headless.timeout_minutes` overrides `--turn-timeout`
  for that role (validated; non-positive → config error).
- [ ] `tagteam rollback 0.8.0` prints the install-appropriate command +
  `tagteam upgrade` note and does nothing without `--yes`; with `--yes` it
  runs them (tested with `subprocess.run` patched); rejects malformed
  versions.
- [ ] Schema: `SCHEMA_VERSION == 4`; fresh/v3 → v4 migrate; `cancelled` in
  `db.USAGE_STATUSES` and the documented vocabulary (guard test updated);
  0.8.0 opens a v4 project (release checklist, findings doc).
- [ ] Flag-off behavior unchanged: existing watcher/headless/config tests
  pass unmodified except the vocabulary guard; interactive send paths are
  untouched apart from the pause check.
- [ ] Docs: README Controls section + Windows notifications + retries +
  rollback; help texts; SKILL.md interjections note (both copies); roadmap;
  findings doc with dogfood numbers; Phase 31 findings 9(b) closed.
- [ ] Dogfood: this phase's plan and impl reviewer turns headless; `pause`,
  `interject` (with a delivered stem), and `usage` used on this repo during
  the impl cycle; `cancel-turn` on a scratch turn — recorded in findings.
- [ ] Released as 0.9.0 via PR merge → tag (post-approval; CI green on
  ubuntu + windows first).

---

## Decisions (round 1, folded into the sections above)

1. **`resume` re-dispatch** — exactly one automatic re-dispatch of the
   still-ready state on the tick after the marker clears (Scope 2,
   "Pause in all modes").
2. **Interjection targeting** — `--to lead|reviewer` ships; `target_role`
   column, pending selection = untargeted-or-matching the receiving role,
   stamping of exactly the rendered ids (Scope 4, Schema, accessors,
   criteria).
3. **Windows notification** — WinRT toast first, `msg` fallback, both
   best-effort; "no visible popup" is non-blocking (Scope 6, Notifications).
4. **Rollback** — stays in scope, print-only unless `--yes` (Scope 8).
5. **Retry gate** — repo fingerprint **and** handoff fingerprint (state
   seq + target cycle entry count + cycle status) must both be unchanged;
   never retry after any handoff transition. *(The round-1 porcelain-based
   repo fingerprint is superseded by Decision 7.)* (Scope 7, Retries rule.)
6. **`cancel-turn` PID binding** — `inflight.json` records `watcher_pid`,
   `started_at`, and same-source creation identities `child_ident` /
   `watcher_ident`; kill only when both identities match by string equality
   and the parent pid is the recorded watcher; otherwise report + clean
   stale metadata (Scope 3, Cancel flow). *(r2)*
7. **Repo fingerprint is content-sensitive and recursive** — final form:
   at every level, HEAD ‖ write-tree(index) ‖ write-tree(temp index after
   `add -A`), and **every 160000 gitlink from `ls-files --stage -z`
   (NUL-framed) is recursed into with the same triple** — registered,
   uninitialised (SHA only, no worktree), embedded, or `.gitmodules`-declared
   alike — with `(level-path, kind, value)` records framed inside the digest
   so association is never inferred from line splitting; no `foreach`, no
   whitelist. Any git failure, unmerged index, or parse/recursion failure ⇒
   `UNSUPPORTED` ⇒ non-retryable (fail closed); only ignored paths are
   outside a successful fingerprint (Scope 7, Retries rule). *(r2–r6)*
8. **Interjections are cycle-scoped** — eligibility requires the receiving
   turn's cycle, or NULL phase (written with nothing owed: `phase/type/
   round/turn` all NULL + `observed_state` provenance) which lands on the
   first *eligible* turn of the next cycle; `retired_ts` + `--list/--retire`;
   same-cycle mode switch injects once with a caveat (Scope 4). *(r2, r3)*

## Open Questions

- None blocking.

## Risks

- **Pause check in interactive modes changes timing** for existing users
  who never pause: mitigated — the check is one `Path.exists()` per
  dispatch, marker absent → identical path; covered by unmodified watcher
  tests.
- **Cancel race / zombie processes** on Windows shims: mitigated by
  `taskkill /T` on the shim pid (proven in Phase 31 CI) and by labeling the
  outcome from the marker, not from the kill's success.
- **Retry rule blind spot: `.gitignore`d paths only.** The fingerprint is
  content-sensitive over every tracked and non-ignored untracked path,
  recursively through submodules (git hashes the bytes via the temp-index
  `write-tree`), so pre-existing modifications/untracked files/dirty
  submodules do not mask agent edits; only ignored paths (build outputs,
  `.tagteam/`, venvs) can change without changing it, and the handoff
  fingerprint independently guards `.tagteam/` state. Every gitlink at
  every level is recursed into (registered or not; NUL-framed paths; the
  (level, path) binding is inside the digest), so no embedded repository
  can hide edits; any git failure, unmerged index, or parse/recursion
  failure fails closed (`UNSUPPORTED` → non-retryable, reason named in the
  log). Documented in README.
- **Interjection delivered but ignored by the agent.** The prompt marks
  them as arbiter instructions and SKILL.md says so; the audit trail shows
  delivery; enforcement is a Phase 34 cockpit concern (show
  delivered-but-unaddressed).
