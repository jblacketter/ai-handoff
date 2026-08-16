# Phase 38: Gatekeeper Pre-checks (3.2)

## Status
- [x] Planning
- [ ] In Review
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
  ├─ claim turn slot kind=gate (fail-closed like briefer; SlotBusy → skip
  │    this tick, retry on the next; do NOT spawn the reviewer past a
  │    live slot)
  ├─ run checks (subprocess test command with timeout; scope diff; plan doc)
  ├─ under dualwrite.writer_lock:
  │    re-read status; if the submission is no longer the same
  │    (round/seq moved — lead AMENDed or the arbiter ruled) → record
  │    gate row as 'superseded', release slot, return (no cycle write)
  │    PASS  → append entry role=gatekeeper action=GATE_PASS (no state
  │             change; like AMEND, rides the round)
  │    BOUNCE→ append entry role=gatekeeper action=GATE_BOUNCE with the
  │             REQUEST_CHANGES transition (state=in-progress,
  │             ready_for=lead → turn=lead, updated_by=Gatekeeper)
  ├─ finish gate row (ok/bounce/superseded/error, duration, result_json)
  ├─ release slot
  └─ PASS → fall through to the existing reviewer hand-off *in the same
       tick*; BOUNCE → return (next tick sees turn=lead and hands off to
       the lead as usual)
```

**Event key** = `f"{phase}/{type}/r{round}/{submission_seq}"` where
`submission_seq` is `state.seq` at the moment the submission was recorded
(already stored on `state_history`), so a re-submission of the same round
number after a bounce is a distinct event, while a watcher restart or a
second watcher process cannot gate the same submission twice.

### Checks (all deterministic; each yields `ok | fail | skip` + detail)

| id | applies to | passes when | on `skip` |
|---|---|---|---|
| `tests` | types in `gatekeeper.on` (default `[impl]`) | configured `gatekeeper.tests.command` exits 0 within `timeout_minutes` (default 15) | no command configured → skip with note |
| `scope` | `impl` only | `compute_scope_diff` succeeds and `paths` is non-empty | not a git repo / legacy cycle without baseline (`ScopeDiffError`) → skip with the error text (never fail on missing prerequisites) |
| `plan-doc` | all gated types | `docs/phases/<phase>.md` exists and is non-empty | — |

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
  submission_seq INTEGER NOT NULL,
  kind TEXT NOT NULL,             -- 'auto' (watcher) | 'manual' (gate run)
  status TEXT NOT NULL,           -- running | pass | bounce | superseded | error
  attempt INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  duration_s REAL, result_json TEXT, stem TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_running ON gates(event_key) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_decided ON gates(event_key) WHERE status IN ('pass','bounce');
```

`db.claim_gate(...)` = the `claim_brief` `BEGIN IMMEDIATE … INSERT … WHERE
NOT EXISTS` pattern; `finish_gate(...)`; `sweep_abandoned_gates(...)` for
rows whose owner died mid-run (marker-file check, as the briefer does).
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
- **B. `cycle.py`** — `add_gate_entry()`; `GATE_PASS`/`GATE_BOUNCE`
  transitions; `ROLE_GATEKEEPER`; `_derive_top_level_state` writes
  `updated_by: Gatekeeper` on bounce.
- **C. `db.py`** — schema v8 `gates`, `claim_gate`/`finish_gate`/
  `sweep_abandoned_gates`/`gates_for_cycle`/`last_gate`, `_ACTION_TO_STATUS`
  additions, `NON_FILE_BACKED_TABLES`.
- **D. `config.py`** — `GATEKEEPER_KEYS`, `validate_gatekeeper_config`,
  `get_gatekeeper_spec`.
- **E. `watcher.py`** — `_maybe_gate()` called from `_handle_ready` for
  `turn == reviewer` before the headless spawn / notify; spec resolved in
  `_build_processor`; PASS falls through in the same tick.
- **F. `headless.py`** — `SLOT_KIND_GATE`; no prompt change.
- **G. `cli.py`** — `gate check|run|status|list`.
- **H. Cockpit** — feed kind `gate`, `now_payload.gatekeeper`, strip chip.
- **I. Docs** — SKILL (both copies: lead pre-flight line + "gate entries
  in the round tail" note in the reviewer section); README "Gatekeeper"
  subsection; `docs/how-tagteam-works.md`; roadmap; `CITATION.cff` +
  `pyproject.toml` → 3.2.0 at the end.
- **J. Tests** — `tests/test_gatekeeper.py` (checks, decide, cap, claim
  at-most-once + concurrent claims, superseded, timeout, no-git skip,
  output truncation, log file), `test_db.py` v8, `test_config.py`,
  `test_cycle.py` (gate transitions + parser tolerance), `test_watcher.py`
  (gate before spawn; slot busy → retry; disabled → byte-identical path),
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
   submission (`uq_gates_decided`), and an abandoned `running` row is swept
   and retried once.
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

## Open questions for the reviewer
- Should `plan` cycles be gated by default (`on: [plan, impl]`, plan-doc
  check only)? I propose **impl only** by default: it's where the cost is,
  and a plan bounce for a missing file is rare.
- `max_bounces` default 2 vs 1? Two lets a lead fix a genuinely flaky
  first run; one is stricter on token spend. I lean 2.
