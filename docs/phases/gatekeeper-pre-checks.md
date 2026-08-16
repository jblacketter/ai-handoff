# Phase 38: Gatekeeper Pre-checks (3.2)

## Status
- [x] Planning
- [x] In Review (round 2: implementation-work boundary, gate-owed latch for every watcher mode, owner-aware gate rows + sweep policy, pinned submission identity + PASS write semantics)
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete (release **3.2.0** via PR)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** a deterministic **gate** that runs between the lead's
`SUBMIT_FOR_REVIEW` and the reviewer's turn. It runs the project's test
command and the existing scope-diff audit, then either:

- **PASS** — attaches a short report (tests summary, scope paths) to the
  current round so the reviewer's turn starts with the facts already in
  hand; the turn stays with the reviewer, or
- **BOUNCE** — hands the turn straight back to the lead with the failing
  output, so no reviewer turn (model tokens, minutes of wall clock, one of
  the ten rounds' worth of attention) is spent on a submission that
  doesn't build.

No model is involved. The gate is a satellite agent in the sense of the
3.0 proposal §1 ("new agents wrap the loop — gatekeeper before it,
briefer after it — they do not join it as peers"): the lead/reviewer/
arbiter triangle and its bounded rounds are unchanged.

**Why:** the impl-review cycles in this repo's own history spend early
rounds on things a script could have caught — a submission whose tests
don't pass, or an impl cycle opened over an unchanged tree (the SKILL
already calls the latter "a contract violation, not a formality", but
nothing enforces it). The 3.0 proposal §4 named this the first candidate
after the arc; Phase 37's own impl review is the most recent example of a
reviewer round that opened with "did the tests run?". The proposal's hard
constraint 5 (token headroom is a budget) says satellites must be
deterministic-first — this one is deterministic *only*.

**Depends on:** Phase 31 headless engine (turn slot, `run_owed_turn`),
Phase 32 controls, Phase 33 briefer (the claim-row + slot pattern this
copies), Phase 34 cockpit feed. **Size:** medium. Branch
`phase-38-gatekeeper-pre-checks`, PR at the end. **Release:** 3.2.0.

**Compatibility rule for this phase.** Entirely opt-in behind
`gatekeeper.enabled: true` in `tagteam.yaml`; with the block absent,
3.2.0 behaves identically to 3.1.1 (the schema migration to v8 is
additive and inert; no new writes happen). Schema v8 adds one table and
zero column changes. No cycle-state transition is renamed or removed; the
gate reuses the existing `REQUEST_CHANGES`-shaped transition for a bounce.

## Design

### Where the gate runs

The gate runs in the **watcher**, at the seam where it would otherwise
hand the reviewer its turn (`_StateProcessor._handle_ready`,
`watcher.py:632`) — in **both** headless and interactive/notify modes,
because in both the watcher is the thing that says "reviewer, go". It
does **not** run inside `tagteam cycle add` in the lead's process: that
would make the lead's single cycle-writing call block for the length of
the test suite (minutes), collide with headless turn timeouts, and put a
subprocess inside the writer lock.

Consequence: with no watcher running (manual backend, or two humans
pasting commands) the gate does not fire on its own. That is acceptable
for an opt-in satellite and mirrors the briefer; `tagteam gate run` (below)
covers the manual case, and the SKILL tells the lead about `tagteam gate
check` for pre-flight.

### Sequence (watcher tick, `turn == reviewer`, gate enabled)

```
state.seq changed → _handle_ready(state)
  ├─ gate not applicable (type not in gatekeeper.on, or already decided
  │    for this submission) → existing behaviour (spawn / notify reviewer)
  ├─ claim gate row for event_key (at-most-once) → None → existing behaviour
  ├─ claim turn slot kind=gate (fail-closed like briefer; SlotBusy → set the
  │    processor's GATE-OWED LATCH `_gate_owed_seq = state.seq`, return
  │    without dispatching; do NOT spawn/notify the reviewer past a live slot —
  │    later ticks with the SAME seq re-enter here (see "Gate-owed latch")
  ├─ run checks (subprocess test command with timeout; scope diff; plan doc)
  ├─ under dualwrite.writer_lock:
  │    re-read top-level state + cycle status; if the submission is no longer
  │    the same — `state.seq != gate.submission_seq`, or the cycle's
  │    round/state moved (lead AMENDed, arbiter ruled) → record the gate row
  │    'superseded', release slot, return (no cycle write)
  │    PASS  → append entry role=gatekeeper action=GATE_PASS to the rounds
  │             JSONL + shadow DB + auto-export ONLY — the AMEND write path:
  │             NO `_derive_top_level_state`, NO handoff-state.json write,
  │             `seq` unchanged (a bumped seq would make the next tick
  │             re-dispatch the reviewer under a new event)
  │    BOUNCE→ append entry role=gatekeeper action=GATE_BOUNCE with the
  │             REQUEST_CHANGES transition (state=in-progress,
  │             ready_for=lead → turn=lead, updated_by=Gatekeeper; this one
  │             DOES derive state and bump seq, like any reviewer response)
  ├─ finish gate row (ok/bounce/superseded/error, duration, result_json)
  ├─ release slot
  └─ PASS → fall through to the existing reviewer hand-off *in the same
       tick*; BOUNCE → return (next tick sees turn=lead and hands off to
       the lead as usual)
```

**Submission identity.** `submission_seq` = the top-level
`handoff-state.json` `seq` read at claim time (it is NOT taken from
`state_history`, which does not store it; no history field is added). It is
stored on the gate row and compared again under the lock at finalization.
**Event key** = `f"{phase}/{type}/r{round}/{submission_seq}"`, so a
re-submission of the same round number after a bounce is a distinct event,
while a watcher restart or a second watcher process cannot gate the same
submission twice. Because `GATE_PASS` never touches `seq`, the reviewer
dispatch that follows a PASS happens exactly once: the same tick continues
into the existing hand-off, and every later tick / restart sees the same
seq (dedupe) plus a decided gate row (`uq_gates_decided`) → no second gate,
no second spawn.

### Gate-owed latch (every watcher mode)

`_StateProcessor.tick()` returns immediately when `state.seq ==
last_processed_seq`; today only the pause-resume path and the headless
engine's `slot_busy` latch re-dispatch. The gate needs its own: when the
gate returns **without a decision** (slot busy, or claim refused because
another runner's attempt is `running`), the processor sets
`_gate_owed_seq = state.seq` and does **not** dispatch the reviewer. In
the same-seq branch of `tick()`, for **headless, notify, tmux and iTerm2
alike**: if `_gate_owed_seq == state.seq` and the state is still
`ready` / `turn == reviewer`, re-enter `_maybe_gate()`; on a decision the
latch clears and the tick continues into the normal hand-off (PASS) or
returns (BOUNCE). A watcher restart needs no latch: its first tick picks
up the ready state ("Picking up active turn"), the gate row's
at-most-once claim decides whether to run or to defer to a live runner.
The reviewer is never dispatched while a gate for that submission is
undecided. Tests: busy on the first tick / free on a later identical tick
for each of the four modes (stub notifier / stub sender / fake engine),
and restart mid-gate.

### Checks (all deterministic; each yields `ok | fail | skip` + detail)

| id | applies to | passes when | on `skip` |
|---|---|---|---|
| `tests` | types in `gatekeeper.on` (default `[impl]`) | configured `gatekeeper.tests.command` exits 0 within `timeout_minutes` (default 15) | no command configured → skip with note |
| `scope` | `impl` only | there is **implementation work since the implementation boundary** (below): the set of paths changed since the boundary — committed after `boundary.sha` ∪ currently dirty/untracked whose content hash differs from the boundary's snapshot — minus tagteam artifacts (`_TAGTEAM_ARTIFACT_FILES` / `_PREFIXES`, `.tagteam/`) and this phase's plan artifacts (`docs/roadmap.md`, `docs/phases/<phase>.md`) is non-empty | not a git repo, no HEAD (no commits yet), or no boundary recorded (plan approved before 3.2.0 / legacy cycle) → skip with the reason (never fail on missing prerequisites); the phase-baseline `compute_scope_diff` remains what the cockpit Diff tab shows |
| `plan-doc` | all gated types | `docs/phases/<phase>.md` exists and is non-empty | — |

**Implementation boundary.** The existing phase baseline (plan-init,
propagated to impl) is deliberately the *whole-phase* diff for the scope
UI, so "paths non-empty" would pass on plan work alone (this very phase:
roadmap + plan doc committed after the baseline). The gate therefore uses
a second, distinct snapshot, `impl_boundary`, captured **when the plan
cycle is approved** — inside `add_round`/`add_ruling` for a plan-cycle
`APPROVE`, under the writer lock, before implementation can begin — with
the same shape as a baseline plus content hashes of the dirty paths:
`{sha, dirty: {path: sha256|null}, captured_at, source: "plan-approve"}`.
It is stored on the plan cycle's status file and copied onto the impl
status at impl init (`impl_boundary`, source `copied-from-plan`), exactly
like `baseline`. Rules: **no HEAD** (unborn branch) → boundary `sha:
null`, the diff is "every tracked+untracked non-artifact path with a
content hash different from the snapshot" — still meaningful; **dirty tree
at capture** → those paths are in the snapshot with hashes, so a path that
is still dirty but unchanged does not count, a path whose content changed
does; **no boundary** → skip. Tests: plan-only changes after the boundary
(roadmap / plan doc / handoff files edited or committed) → `fail`; one
real code change → `pass`; an intentional implementation-doc change
(README / `docs/how-tagteam-works.md`) → `pass`; unborn HEAD; dirty at
capture then unchanged → `fail`, dirty at capture then modified → `pass`;
legacy cycle without boundary → `skip`.

A `fail` on any check → BOUNCE; otherwise PASS. `skip` never bounces but
is always reported so the reviewer sees what was *not* checked. Timeout →
`fail` with "timed out after N min" (the reviewer would have hit the same
wall). Test command output is captured, stderr merged, and truncated to
the **last** `max_output_chars` (default 4000) so a bounce entry stays
small enough for the round tail (`DEFAULT_TAIL_ROUNDS = 3`).

The test command runs with `cwd = project_root`, `shell=True` when given
as a string (so `python -m pytest -q tests/` works as typed; Windows gets
`cmd` semantics — Windows CI is manual, see roadmap), a list is passed
verbatim. Environment inherits the watcher's, plus `TAGTEAM_GATE=1` so a
test suite can detect it if it wants to.

### Bounce cap and stale interplay

`gatekeeper.max_bounces` (default **2**) is the number of *consecutive*
gate bounces allowed on one cycle. On the next failing submission the
gate records **PASS-WITH-FINDINGS**: it appends `GATE_PASS` whose content
begins `GATE: checks failed but bounce cap (2) reached — reviewer, see
report` and lets the reviewer decide (escalate, ask the human, or review
anyway). This bounds the worst case: the gate can never consume more than
`max_bounces` of the ten rounds, and never traps a lead whose environment
differs from the watcher's (wrong venv, missing tool) in a loop the human
never sees. Gate bounces do not reset `_count_stale_rounds`; identical
re-submissions after bounces still auto-escalate at 10 as today.

### Round entries

New role `gatekeeper` (display name `Gatekeeper`) and actions
`GATE_PASS`, `GATE_BOUNCE`. They are written by a dedicated
`cycle.add_gate_entry(...)` — same shape as `add_ruling` (bypasses
`VALID_ROLES`/`VALID_ACTIONS` validation of the *CLI* path, still goes
through `writer_lock`, `_derive_top_level_state`, shadow-DB and
auto-export). Content is a fixed, greppable format:

```
GATE: PASS | tests ok (984 passed, 5 skipped, 3m38s) | scope 12 paths | plan-doc ok
```
```
GATE: BOUNCE | tests FAILED (exit 1, 41s) | scope 12 paths | plan-doc ok
--- tests: last 4000 chars ---
FAILED tests/test_x.py::test_y - AssertionError ...
```

Because the entry rides the round like an AMEND, the reviewer's headless
prompt receives it through the existing round tail (`tail_rounds`) with
**no change to `compose_prompt`**; interactive reviewers see it in
`tagteam cycle rounds`. `parse_jsonl_rounds` needs no change beyond
tolerating the new role string in the `entries` grouping. `db._ACTION_TO_STATUS`
gains `GATE_PASS → (in-progress, reviewer)` and `GATE_BOUNCE →
(in-progress, lead)`.

### CLI

```
tagteam gate check  [--phase P --type T]     # run checks, print report, write nothing (lead pre-flight; exit 0/1)
tagteam gate run    [--phase P --type T]     # run checks against the current submission and record PASS/BOUNCE
                                             # (manual-mode substitute for the watcher; same claim/at-most-once path)
tagteam gate status                          # last gate result for the current cycle (or --json)
tagteam gate list   [--phase P --type T]     # gate rows for a cycle
```

`gate check` is what the SKILL tells the lead to run before
`SUBMIT_FOR_REVIEW` when the gate is enabled ("`tagteam gate check` — if
it fails, fix first; the gate will bounce you otherwise").

### Cockpit

- Feed: a new item kind `gate` (pass = neutral, bounce = warm) built from
  the round entries; one CSS rule; no new endpoint.
- `now_payload` gains `gatekeeper: {enabled, last: {status, round, ts} | null}`
  so the strip can show "gate ✓ r3" / "gate ↩ r3"; the Needs-you zone is
  **unchanged** (a bounce is the lead's problem, not the arbiter's — the
  arbiter is only pulled in when the cap trips and the reviewer escalates).
- Actions: none. `gate run` deliberately is *not* a cockpit button in this
  phase.

### Storage (schema v8, additive)

```sql
CREATE TABLE IF NOT EXISTS gates (
  id INTEGER PRIMARY KEY,
  event_key TEXT NOT NULL,        -- phase/type/rN/seq
  phase TEXT NOT NULL, type TEXT NOT NULL, round INTEGER NOT NULL,
  submission_seq INTEGER NOT NULL, -- top-level state.seq read at claim
  kind TEXT NOT NULL,             -- 'auto' (watcher) | 'manual' (gate run)
  status TEXT NOT NULL,           -- running | pass | bounce | superseded | error | abandoned
  attempt INTEGER NOT NULL,
  runner_pid INTEGER, runner_ident TEXT,   -- the process that owns the attempt (populated at claim)
  started_at TEXT NOT NULL, updated_at TEXT, finished_at TEXT,
  duration_s REAL, result_json TEXT, stem TEXT,
  reason TEXT                     -- error / abandoned / superseded detail
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_running ON gates(event_key) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_decided ON gates(event_key) WHERE status IN ('pass','bounce');
```

`db.claim_gate(...)` = the `claim_brief` `BEGIN IMMEDIATE … INSERT … WHERE
NOT EXISTS (a pass|bounce row for the event) … WHERE NOT EXISTS (a running
row for the event)` pattern, allocating `attempt = 1 + max(attempt)` and
writing `runner_pid` + `runner_ident` (`procs.identity`) in the same
statement; `finish_gate(...)` (status, finished_at, duration, result,
reason). **Sweep / retry policy** (`sweep_abandoned_gates`, run under the
writer lock before every claim, same as the briefer): a `running` row is
marked `abandoned` when its runner is **definitively gone** (dead pid, or
recorded non-null identity ≠ live identity), or when the runner is alive
but the attempt is older than `timeout_minutes + grace` **and** no turn-slot
marker with the row's `stem` exists; a live-and-verifiable runner within
time is left alone; a live-but-unverifiable runner (identity unavailable)
is left alone (fail closed) and reported in `gate status`. After
`abandoned` or `error`, the next claim allocates a new attempt (at most
**one** automatic retry per event: attempt 3+ is refused and the gate
records `PASS`-with-findings "gate could not complete after 2 attempts —
reviewer, see report" so the loop never stalls). Concurrent reclaim is
serialized by the writer lock and `uq_gates_running`. Tests: live pid,
dead pid, identity mismatch, timeout with matching slot marker (kept) vs
missing marker (abandoned), unverifiable (kept + reported), and two
concurrent claimers after an abandon → exactly one new running row.
`gates` joins `NON_FILE_BACKED_TABLES` so `repair` preserves it. Test
output beyond what the entry carries goes to
`.tagteam/gates/<phase>_<type>_r<N>-a<attempt>.log` (`stem`).

### Config

```yaml
gatekeeper:
  enabled: true                       # default false — absent block = 3.1.1 behaviour
  on: [impl]                          # cycle types gated; plan gating = plan-doc check only
  tests:
    command: "python -m pytest -q"    # string (shell) or list; omitted → tests check skipped
    timeout_minutes: 15
  scope: true                         # impl only; false → skipped
  max_bounces: 2
  max_output_chars: 4000
```

`config.validate_gatekeeper_config()` + `get_gatekeeper_spec()` mirror the
briefer's (`GATEKEEPER_KEYS`, unknown keys rejected, types checked).
`tagteam quickstart`/`setup` do **not** turn it on; the README shows the
block.

## Scope

### In
- **A. `gatekeeper.py`** — `GateSpec`, `run_checks()` (tests / scope /
  plan-doc), `decide()` (pass / bounce / cap-pass), `run_gate()`
  (claim → slot → checks → locked write → finish → release), report
  formatting, log file.
- **B. `cycle.py`** — `add_gate_entry()` (PASS = the AMEND write path:
  rounds + shadow DB + export, no state derive; BOUNCE = the
  REQUEST_CHANGES path); `GATE_PASS`/`GATE_BOUNCE` transitions;
  `ROLE_GATEKEEPER`; `_derive_top_level_state` writes `updated_by:
  Gatekeeper` on bounce; **`impl_boundary` capture on plan-cycle
  `APPROVE`** (`add_round` + `add_ruling`), propagation at impl init,
  `compute_impl_work(phase, project_dir)` (the boundary diff with the
  artifact/plan-artifact exclusions and content-hash rule).
- **C. `db.py`** — schema v8 `gates`, `claim_gate`/`finish_gate`/
  `sweep_abandoned_gates`/`gates_for_cycle`/`last_gate`, `_ACTION_TO_STATUS`
  additions, `NON_FILE_BACKED_TABLES`.
- **D. `config.py`** — `GATEKEEPER_KEYS`, `validate_gatekeeper_config`,
  `get_gatekeeper_spec`.
- **E. `watcher.py`** — `_maybe_gate()` called from `_handle_ready` for
  `turn == reviewer` before the headless spawn / notify (all four modes);
  the **gate-owed latch** `_gate_owed_seq` re-entered from the same-seq
  branch of `tick()`; spec resolved in `_build_processor`; PASS falls
  through in the same tick.
- **F. `headless.py`** — `SLOT_KIND_GATE`; no prompt change.
- **G. `cli.py`** — `gate check|run|status|list`.
- **H. Cockpit** — feed kind `gate`, `now_payload.gatekeeper`, strip chip.
- **I. Docs** — SKILL (both copies: lead pre-flight line + "gate entries
  in the round tail" note in the reviewer section); README "Gatekeeper"
  subsection; `docs/how-tagteam-works.md`; roadmap; `CITATION.cff` +
  `pyproject.toml` → 3.2.0 at the end.
- **J. Tests** — `tests/test_gatekeeper.py` (checks, decide, cap, claim
  at-most-once + concurrent claims, superseded, timeout, no-git skip,
  output truncation, log file; **impl-boundary matrix**: plan-only
  changes fail, code change passes, implementation-doc change passes,
  unborn HEAD, dirty-at-capture unchanged vs modified, legacy no-boundary
  skip; **sweep matrix**: live / dead / identity mismatch / timeout with
  and without slot marker / unverifiable / concurrent reclaim; **PASS
  write semantics**: top-level seq unchanged, one decision, one reviewer
  dispatch across later ticks and a restart), `test_db.py` v8,
  `test_config.py`, `test_cycle.py` (gate transitions + parser tolerance;
  boundary capture on plan APPROVE incl. rulings; propagation at impl
  init), `test_watcher.py` (gate before spawn; **gate-owed latch: busy on
  the first tick, free on a later identical tick, for headless / notify /
  tmux / iTerm2, and across a restart**; disabled → byte-identical path),
  `test_cockpit_api.py` / `test_server_cockpit.py` (feed + now), CLI tests,
  and a SKILL-copies-in-sync assertion.

### Out (deliberately)
- Any model call in the gate (a "cheap opt-in model pre-review" is a
  separate, later decision — proposal §4 wording).
- Reviewer panels, roadmap DAG, MCP.
- Gating `APPROVE` or reviewer submissions (the gate is one-directional:
  lead → reviewer).
- Cockpit *action* to run the gate; per-check config beyond the table
  above; running the gate inside `cycle add`.
- Auto-enabling for existing projects.

## Technical approach — notes for the reviewer

- **One write, under the lock, after re-validation.** The checks run
  *outside* the writer lock (they take minutes); the cycle write happens
  *inside* it after re-reading status and comparing `(round, seq)` to the
  claimed event. If they moved, the row is `superseded` and nothing is
  written — the newer submission gets its own event key and its own gate.
- **Fail-closed on the slot** exactly like the briefer: if
  `slot_owner_gone` cannot verify, we do not run and do not spawn the
  reviewer past a live slot; the tick returns and the watcher retries.
- **The bounce transition is `REQUEST_CHANGES` in every respect that
  matters** (`ready_for=lead`, `turn=lead`, `_STATE_COMMAND`, watcher
  notifies/spawns the lead) so no downstream consumer — SKILL banner,
  cockpit, headless `verify_transition` for the *lead's* next turn — needs
  a new branch. `verify_transition` for `GATE_BOUNCE` is added for
  completeness (`gate run` path).
- **`gate check` and the watcher share `run_checks()`**, so what the lead
  sees locally is what the gate will decide (modulo environment).
- **Windows**: `shell=True` string commands are the documented form; the
  Windows job is manual in CI (roadmap) — I will run it once by hand
  before release since this phase adds a subprocess path.

## Files
```
tagteam/gatekeeper.py                     new
tagteam/cycle.py                          add_gate_entry, ROLE_GATEKEEPER, GATE_* transitions
tagteam/db.py                             SCHEMA_VERSION 8, gates table + fns, _ACTION_TO_STATUS
tagteam/config.py                         gatekeeper block validate/spec
tagteam/watcher.py                        _maybe_gate in _handle_ready
tagteam/headless.py                       SLOT_KIND_GATE
tagteam/cli.py                            gate subcommand
tagteam/cockpit_api.py                    now_payload.gatekeeper
tagteam/data/web/cockpit.{js,css}         feed kind gate, strip chip
tagteam/data/.claude/skills/handoff/SKILL.md, .claude/skills/handoff/SKILL.md
README.md, docs/how-tagteam-works.md, docs/roadmap.md, docs/phases/gatekeeper-pre-checks.md
tests/test_gatekeeper.py (new) + touched suites above
pyproject.toml, CITATION.cff             3.2.0 (last commit)
```

## Success criteria
1. With `gatekeeper` absent from `tagteam.yaml`, the full suite and a
   scripted headless plan+impl cycle behave identically to 3.1.1 (no
   `gates` rows, no gate entries, no new prompt text).
2. Enabled, headless: a lead `SUBMIT_FOR_REVIEW` on an impl cycle whose
   test command fails produces exactly one `GATE_BOUNCE` entry, `turn:
   lead`, no reviewer spawn, and one `gates` row `bounce`; the lead's next
   headless prompt shows the failure tail.
3. Enabled, passing: exactly one `GATE_PASS` entry precedes the reviewer
   spawn *in the same watcher tick*; the reviewer's prompt round tail
   contains the `GATE: PASS …` line.
4. Two watchers / a restart mid-check cannot produce two decisions for one
   submission (`uq_gates_decided`); an abandoned `running` row (dead /
   mismatched runner, or timed out with no slot marker) is swept and
   retried once; a live-unverifiable runner is left alone and reported.
4b. A `GATE_PASS` leaves `handoff-state.json` `seq` unchanged and produces
   exactly one reviewer dispatch across later ticks and a watcher restart;
   the gate-owed latch retries an undecided gate on identical ticks in
   every watcher mode without ever dispatching the reviewer first.
4c. The scope check fails on plan-only changes since the implementation
   boundary and passes on a real code or intentional implementation-doc
   change; missing boundary / no HEAD / not-git → skip with reason.
5. After `max_bounces` consecutive bounces, the next failing submission
   passes-with-findings and reaches the reviewer.
6. `tagteam gate check` exits 1 with the same report the gate would write;
   `tagteam gate run` in manual mode records the decision through the same
   claim path.
7. Interactive/notify watcher mode gates too (test at the `_handle_ready`
   seam with a stub notifier).
8. Cockpit feed renders pass/bounce items; `/api/now` carries
   `gatekeeper`.
9. Both SKILL copies updated and identical (except the known line-61
   delta, which this phase also reconciles); README documents the block.
10. Release 3.2.0 via PR from `phase-38-gatekeeper-pre-checks`.

## Resolved questions (round 1 → 2)
- Default `on: [impl]` — agreed by the reviewer.
- `max_bounces` default **2** — agreed by the reviewer.

## Round-2 changes (reviewer r1)
1. Scope check now enforces the unchanged-implementation contract through
   a distinct **implementation boundary** captured at plan approval
   (`impl_boundary`: sha + dirty-path content hashes), with no-HEAD /
   dirty-tree / no-boundary rules and the plan-only-fails / code-passes /
   impl-doc-passes tests; the phase baseline stays for the scope UI.
2. **Gate-owed latch** (`_gate_owed_seq`) re-enters the gate on identical
   ticks in headless, notify, tmux and iTerm2; restart covered by the
   at-most-once claim; the reviewer is never dispatched past an undecided
   gate.
3. `gates` rows carry `runner_pid` / `runner_ident` / `updated_at` /
   `reason`, populated at claim; explicit sweep policy (definitively gone,
   or timed out without a slot marker → `abandoned`; unverifiable → kept
   and reported; one automatic retry, then pass-with-findings) with the
   full test matrix.
4. `submission_seq` = top-level `state.seq` at claim (not `state_history`),
   stored and re-compared under the lock; `GATE_PASS` uses the AMEND write
   path (no state derive, no seq bump); tests prove seq unchanged, one
   decision, one dispatch across ticks and restart.
