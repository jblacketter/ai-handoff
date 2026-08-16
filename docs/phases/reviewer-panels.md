# Phase 39: Reviewer Panels (3.3)

## Status
- [x] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete (release **3.3.0** via PR)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** an opt-in **panel** that takes the reviewer's turn as 2–3
independent **lens** reviews (each a fresh reviewer process with a
lens-specific brief: *correctness*, *scope*, *verification* by default)
whose verdicts are **merged deterministically into exactly one** reviewer
entry — one `APPROVE`, or one `REQUEST_CHANGES` whose findings are grouped
by lens (or an `ESCALATE` / `NEED_HUMAN` if a lens asks for the human). The
lead still receives one message and one turn; the loop's round budget,
transitions, banner and stale-round rule are untouched. This is the
3.0-proposal §4 candidate "Reviewer panels (2–3 lenses merged into one
`REQUEST_CHANGES`; opt-in per phase)".

**Why:** a single reviewer turn tends to lead with whatever it notices first
and under-weights the other axes; this repo's own impl reviews show it —
correctness objections in round 1, scope/plan-conformance in round 2,
"were these claims verified?" in round 3, each costing a full lead round.
Three narrow lenses run against the *same* submission surface all three
kinds of objection in one round. It composes with Phase 38: the gate has
already run the tests and confirmed real implementation work before any
lens spends tokens, and the merged entry rides the round exactly like the
gate's.

**Depends on:** Phase 31 headless engine (`run_process`, adapters,
`RoleSpec`, usage), Phase 33 briefer (spawn-a-satellite-process pattern +
claim row), Phase 37 turn slot, Phase 38 gatekeeper (claim/sweep/pinned
apply pattern; the panel runs after a gate PASS). **Size:** medium. Branch
`phase-39-reviewer-panels`, PR at the end. **Release:** 3.3.0.

**Compatibility rule.** Entirely opt-in behind `panel.enabled: true`;
with the block absent, 3.3.0 behaves identically to 3.2.0 (schema v9 is
additive — one table — and inert). No cycle-state transition is added or
renamed: the merged entry is an ordinary reviewer-role entry written through
`add_round`, so the SKILL banner, cockpit feed, `verify_transition`, stale
counting and the briefer need no new branch. **No cockpit work in this
phase** (the cockpit is parked; see `docs/cockpit-issues.md`) — the feed
already renders reviewer entries generically.

## Design

### What a panel is

```
reviewer's turn owed (turn=reviewer, status=ready, cycle ready_for=reviewer)
  ├─ gate (Phase 38, if enabled) → PASS falls through / BOUNCE returns
  ├─ panel not applicable (disabled, type not in panel.on, phase not in
  │    panel.phases, reviewer does not validate for headless) → existing
  │    reviewer hand-off (unchanged)
  ├─ claim the TURN SLOT (kind=panel, fail-closed) → busy → latch + return
  ├─ locked sweep + claim `panels` row for event_key (at-most-once) —
  │    refused → release slot; decided (merged) → do NOT dispatch (the entry
  │    IS the reviewer's turn); decided (fallback) → existing hand-off;
  │    live-other running → latch + return
  ├─ run the lenses SEQUENTIALLY (one process at a time — the single turn
  │    slot is the project's concurrency invariant; each lens is one
  │    `claude -p` / `codex exec` with the reviewer's headless spec, prompt
  │    on stdin, `.tagteam/panels/<stem>/<lens>.{prompt,log,events.jsonl}`;
  │    the lens writes `verdict.json`; usage recorded role=reviewer,
  │    kind=panel:<lens>)
  ├─ merge (deterministic, below) → decision
  ├─ under dualwrite.writer_lock: re-read top-level state + cycle status;
  │    submission moved (seq != submission_seq, round/state moved, round log
  │    grew) → row `superseded`, no cycle write; a reviewer entry with this
  │    `panel_event` already exists → done (never a second entry); else
  │    `cycle.add_round(role=reviewer, action, round, content,
  │    updated_by="<Reviewer> panel")` — the ordinary reviewer write, one
  │    call, transition + derive + mirror + export inside the same lock;
  │    then finish the panels row in the same hold
  └─ release slot; merged → return WITHOUT dispatching the reviewer;
       fallback → fall through to the existing hand-off in the same tick
```

The panel therefore **is** the reviewer's turn when it applies. In headless
mode it replaces `engine.run_owed_turn` for that submission; in
notify/iTerm2/tmux modes the reviewer's terminal is left idle for that
submission (the entry is written by the panel) — the interactive reviewer
still gets every submission the panel does not decide (fallback,
disabled, not applicable) exactly as today. Manual backend / no watcher:
`tagteam panel run` (same claim path). The lead's turn is untouched.

### Lenses

A lens = name + brief. Built-in briefs (`tagteam/data/panels/<lens>.md`,
shipped as package data; a project may override any by placing
`.tagteam/panels/lenses/<lens>.md` or point `panel.lenses[].brief` at a
file):

| lens | asks | typical findings |
|---|---|---|
| `correctness` | does the change do what the plan says, without bugs? edge cases, error paths, concurrency, data integrity | wrong behaviour, unhandled cases, regressions |
| `scope` | is everything in the plan present and nothing outside it? success criteria met? plan/roadmap/docs updated where the plan says? | missing criteria, scope creep, unexplained deviations, docs drift |
| `verification` | are the submission's claims **verified**? tests exist for the new behaviour, run green, cover the failure modes; evidence in the round matches the tree | untested paths, claims without evidence, tests that don't assert the thing |

The lens prompt = the lens brief + a fixed **panel contract** (below) +
the same context a reviewer turn gets today (`docs/phases/<phase>.md`, the
round tail via `tail_rounds`, the pending interjections for the reviewer,
`handoff-state.json`) + the gate's last entry if any. The lens has the
same tools/permissions as a headless reviewer turn (it runs in the
project cwd through the same adapter) — it *can* read files and run
commands; the brief tells it what to look at.

**Panel contract (in every lens prompt).** You are one lens of a review
panel. Do NOT run `tagteam cycle add` / `cycle init` / any tagteam write.
Write your verdict to `<verdict_path>` as JSON:
`{"verdict": "APPROVE"|"REQUEST_CHANGES"|"ESCALATE"|"NEED_HUMAN",
"summary": "<one line>", "findings": [{"title", "detail", "where"?,
"severity": "blocker"|"major"|"minor"}], "question"?: "<for NEED_HUMAN>"}`.
`REQUEST_CHANGES` needs ≥1 finding of severity blocker/major; `APPROVE`
needs no blocker/major findings (minors may still be listed and are carried
as notes). Then stop.

**Lens outcome** = `ok` (verdict file exists, parses, conforms) | `failed`
(spawn error, timeout — reviewer's headless `timeout_minutes` —, nonzero
exit without a conforming file, non-conforming JSON, or a tagteam write
detected: the round log or `state.seq` changed during the lens → that lens
is `failed: wrote to the cycle` and the panel as a whole is `superseded`
at the merge). Failed lenses are never retried inside the same panel
attempt (a lens that times out once will not get faster); a failed
*panel* attempt (crash) is swept and retried once, like the gate.

### Merge (deterministic)

Precedence over the **ok** lenses: `NEED_HUMAN` > `ESCALATE` >
`REQUEST_CHANGES` > `APPROVE`.

| ok lenses say | failed lenses | panel decision |
|---|---|---|
| all APPROVE, none failed | — | **APPROVE** (content lists each lens's one-line summary + carried minors) |
| ≥1 REQUEST_CHANGES | any | **REQUEST_CHANGES** — findings grouped by lens, blockers first; failed lenses named ("verification: lens failed (timeout) — not assessed") |
| ≥1 ESCALATE / NEED_HUMAN | any | that action; content = the asking lens's reason/question first, then the other lenses' findings so the human sees everything |
| all APPROVE but ≥1 failed | some | **fallback** — no decision; the ordinary reviewer turn is dispatched (never approve on a partial panel) |
| all failed | all | **fallback** + one WARN log/notification (`tagteam panel status` shows why) |

`min_lenses` is deliberately *not* configurable in this phase: APPROVE
requires every configured lens; anything less is fallback. Rationale: the
failure mode to avoid is a silent approval on a partial panel.

Merged content is greppable and stable:

```
PANEL: REQUEST_CHANGES — correctness: REQUEST_CHANGES (2 blockers) | scope: APPROVE | verification: REQUEST_CHANGES (1 major)
## correctness
1. [blocker] <title> — <detail> (<where>)
2. [blocker] …
## verification
1. [major] …
## scope — approved
<one-line summary>; minors: …
```
```
PANEL: APPROVE — correctness: APPROVE | scope: APPROVE | verification: APPROVE
correctness: <summary> · scope: <summary> · verification: <summary>
```

The entry carries `panel_event`, `panel_id`, `panel_lenses` (list of
`{lens, outcome, verdict}`) — extra keys, tolerated everywhere (Phase 38
precedent) and read from the canonical JSONL (`read_rounds_file`).

### Stale rounds, escalation, gate interplay

- The merged entry is a reviewer `REQUEST_CHANGES` like any other:
  `_count_stale_rounds` counts identical lead re-submissions across it and
  auto-escalates at 10 exactly as today; the briefer (Phase 33) briefs an
  ESCALATE/NEED_HUMAN written by the panel like one written by a human
  reviewer (the round tail shows the lens findings).
- Order per reviewer-owed submission: **gate, then panel**. A gate BOUNCE
  means no panel (no reviewer turn at all); a gate PASS-with-findings still
  runs the panel — the `verification` lens sees the gate's failure tail in
  the round tail and can decide.
- Arbiter interjections targeted at the reviewer are delivered to the
  lenses (rendered in every lens prompt) and stamped delivered on the
  panel's write, mirroring the headless engine's contract.

### Config

```yaml
panel:
  enabled: true                       # default false — absent block = 3.2 behaviour
  on: [impl]                          # cycle types paneled (plan | impl)
  phases: []                          # optional allowlist of phase slugs; empty/absent = every phase
  lenses: [correctness, scope, verification]   # names (built-in briefs) or {name, brief: path}
  # provider / executable / args / timeout_minutes come from
  # agents.reviewer.headless (the reviewer must validate for headless)
```

`config.validate_panel_config()` / `get_panel_spec()` mirror the
briefer/gatekeeper pattern (unknown keys rejected; 2–3 lenses required —
1 lens is just a reviewer turn, >3 is a token multiplier without a
demonstrated return; each name unique, briefs must exist). The panel is
resolved once at watcher start (`resolve_panel(config, project_root)`
builds the reviewer `RoleSpec` via the same code the headless engine uses;
problems warn and disable, never block).

### Where it runs (watcher) and the latch

`_StateProcessor._maybe_panel(state)` is called from `_handle_ready` for
`turn == reviewer` **after** `_maybe_gate` returned True, in every mode.
Returns True → hand off as today (disabled / not applicable / fallback);
False → do not dispatch (merged — the turn already moved — or deferred /
error → `_panel_owed_seq` latch, re-entered on identical ticks like the
gate's `_gate_owed_seq`; the two latches are independent and checked in
order gate → panel). PASS-through of `_maybe_gate` and `_maybe_panel` in
the same tick keeps the "reviewer dispatched in the same tick" property for
the plain and fallback cases.

Headless mode: `_maybe_panel` runs before `engine.run_owed_turn`; merged
→ `run_owed_turn` is not called for this seq. Because the panel writes the
reviewer transition, the engine's `verify_transition` for a *lead* turn
that follows sees an ordinary REQUEST_CHANGES.

### CLI

```
tagteam panel run     [--phase P --type T]   # run the panel now on the current reviewer-ready submission (manual mode)
tagteam panel status  [--json]               # last panel for the current cycle: lens outcomes, decision, paths
tagteam panel list    [--phase P --type T]   # panel rows for a cycle
tagteam panel lenses                          # the resolved lenses + which brief file each uses (built-in / override)
tagteam panel preview --lens L                # print the exact prompt lens L would get for the current submission (no spawn)
```

`preview` exists so a human can tune a brief without spending a run.

### Storage (schema v9, additive)

```sql
CREATE TABLE IF NOT EXISTS panels (
  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL,        -- phase/type/rN/seq (same identity rule as gates)
  phase TEXT NOT NULL, type TEXT NOT NULL, round INTEGER NOT NULL, submission_seq INTEGER NOT NULL,
  kind TEXT NOT NULL,             -- auto | manual
  status TEXT NOT NULL,           -- running | merged | fallback | superseded | error | abandoned
  attempt INTEGER NOT NULL, runner_pid INTEGER, runner_ident TEXT,
  started_at TEXT NOT NULL, updated_at TEXT, finished_at TEXT, duration_s REAL,
  lenses_json TEXT,               -- [{lens, outcome, verdict, summary, usage_row_id, stem}]
  decision TEXT,                  -- APPROVE | REQUEST_CHANGES | ESCALATE | NEED_HUMAN | null
  stem TEXT, reason TEXT, applied_seq INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_panels_running ON panels(event_key) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_panels_decided ON panels(event_key) WHERE status IN ('merged','fallback');
```

`db.claim_panel` / `finish_panel` / `panels_for_cycle` … are the gate
helpers over a second table (the claim SQL is factored into one internal
`_claim_satellite(conn, table, …)` used by both `claim_gate` and
`claim_panel` so the at-most-once rule has one implementation).
`panels` joins `NON_FILE_BACKED_TABLES`. Files:
`.tagteam/panels/<phase>_<type>_r<N>_panel_<ts>_a<attempt>/<lens>.{prompt,log,events.jsonl,verdict.json}`.

### Recovery (same protocol as the gate, narrower)

- Crash before any lens finished / mid-lens: row `running`, no entry →
  sweep (dead / mismatched runner, or timed out — `lenses × reviewer
  timeout + grace` — with no matching slot marker) → `abandoned`; next
  claim = attempt 2 re-runs the lenses (no entry existed, no duplicate).
- Crash after `add_round` succeeded but before the row finish: entry with
  `panel_event` exists → sweep finishes the row `merged` from the entry
  (`decision`, `applied_seq` = the seq the entry's transition wrote); no
  re-run.
- `add_round` itself is one call under the writer lock (rounds append →
  status → derive → mirror → export, the same path a human reviewer's
  `tagteam cycle add` takes); a crash *inside* it leaves what any reviewer
  CLI crash leaves today, and this phase does not add a new repair for
  that (unchanged risk, stated honestly). What is new — the pinned,
  entry-first write — guarantees the panel never writes twice and never
  writes for a submission that moved.
- A rogue lens that writes to the cycle: detected (round log / seq changed
  during the lens) → lens `failed`, panel `superseded`; the lens's write
  stands as a reviewer entry (it *is* the reviewer's process) — logged
  loudly and shown by `panel status`.

## Scope

### In
- **A. `panel.py`** — `PanelSpec`, `resolve_panel`, built-in lens briefs,
  `compose_lens_prompt`, `run_lens` (spawn via `h.run_process`, verdict
  parse/validate, usage row `kind=panel:<lens>`), `merge`, `run_panel`
  (slot → locked sweep+claim → lenses → merge → locked pinned entry-first
  `add_round` + row finish → release), `sweep_abandoned_panels`,
  `panel_command` (run|status|list|lenses|preview).
- **B. `db.py`** — schema v9 `panels`, `_claim_satellite` refactor (gates
  unchanged in behaviour — existing tests must pass byte-for-byte), panel
  helpers, `NON_FILE_BACKED_TABLES`.
- **C. `config.py`** — `PANEL_KEYS`, `validate_panel_config`,
  `get_panel_spec`.
- **D. `watcher.py`** — `_maybe_panel` after `_maybe_gate` in
  `_handle_ready`; `_panel_owed_seq` latch in the same-seq branch; spec
  resolved in `_build_processor`; startup banner line.
- **E. `headless.py`** — `SLOT_KIND_PANEL`; expose the reviewer
  `RoleSpec` builder for reuse (no behaviour change).
- **F. `cli.py`** — `panel` dispatch + help.
- **G. Package data** — `tagteam/data/panels/{correctness,scope,verification}.md`
  (+ `pyproject.toml` package-data glob).
- **H. Docs** — SKILL (both copies): reviewer section note "when the panel
  is enabled the reviewer's turn may be taken by the panel; a `PANEL:` entry
  is the reviewer's response"; lead section: how to read grouped findings;
  README "Reviewer panels" subsection + CLI ref; `docs/how-tagteam-works.md`
  `#panels` section + files table; roadmap; `pyproject.toml` +
  `CITATION.cff` → 3.3.0.
- **I. Tests** — `tests/test_panel.py`: config matrix; prompt composition
  (brief + contract + context + interjection scoping); verdict parsing
  (valid / missing / malformed / wrong verdict / APPROVE with a blocker →
  failed); merge matrix (every row of the table + precedence + grouping +
  failed-lens naming); `run_panel` with the fake agent (tests/fixtures/
  fake_agent.py gains a `panel` mode that writes a verdict file from an env
  var: approve / request-changes / escalate / need-human / no-file / bad-json
  / rogue-write / timeout) — merged entry is exactly one reviewer entry with
  the right action, `updated_by "<Reviewer> panel"`, turn flips, seq+1
  once; fallback dispatches the reviewer; superseded on a mid-panel AMEND;
  slot busy → deferred, no row; error → row error + slot released; usage
  rows per lens; sweep matrix (live/dead/mismatch/timeout ± marker/
  unverifiable/reconcile-from-entry/concurrent reclaim); watcher: panel
  after gate PASS in all four modes, no reviewer dispatch when merged,
  dispatch on fallback, `_panel_owed_seq` latch, gate BOUNCE → no panel,
  disabled → byte-identical; CLI; SKILL copies identical; flag-off full
  cycle; `test_db.py` v9 + `_claim_satellite` parity for gates.

### Out (deliberately)
- Parallel lens execution (needs multiple turn slots — a later phase).
- Weighted / voting merges, `min_lenses`, per-lens providers or models,
  lens-specific tool restrictions.
- Cockpit changes of any kind (feed renders the entry generically; a chip
  can come with the cockpit UX phase).
- Panels for the lead's turn, or for plan cycles by default (`on: [plan]`
  is allowed but not default).
- Auto-enabling; changing the reviewer's interactive workflow when the
  panel is off.

## Technical approach — notes for the reviewer

- **One write, the ordinary one.** The panel's decision is applied with
  `cycle.add_round(role="reviewer", …)` — no new transition table entries,
  no new derive path — after re-validating `(phase, type, round,
  submission_seq)` and the round-log length under the writer lock, and
  only if no entry with this `panel_event` exists. That is the smallest
  possible surface for a satellite that must produce a real reviewer
  transition.
- **Sequential lenses** respect the single turn slot and the shared
  subscription rate window (Phase 34 rate-limit tracking keeps working
  per process). Wall clock is N× a reviewer turn; the round budget is not
  (still one round). Stated as a known cost.
- **Fallback never approves.** Any lens failure with no lens objecting
  → the human/interactive reviewer's ordinary turn — never a partial
  approval, never a stall.
- **The lens contract is enforced, not trusted**: verdict schema
  validation, plus the round-log/seq change detector for rogue writes.
- **Gate + panel share the machinery** (`_claim_satellite`, sweep policy,
  latch shape) so the two satellites cannot drift in their at-most-once
  semantics; the gate's existing tests are the regression net for the
  refactor.
- **Windows**: nothing new (spawns via the same adapters as headless
  turns); Windows job stays manual in CI.

## Files
```
tagteam/panel.py                          new
tagteam/data/panels/{correctness,scope,verification}.md   new (package data)
tagteam/db.py                             SCHEMA_VERSION 9, panels table, _claim_satellite, helpers
tagteam/config.py                         panel block validate/spec
tagteam/watcher.py                        _maybe_panel + _panel_owed_seq
tagteam/headless.py                       SLOT_KIND_PANEL, reviewer RoleSpec builder exposed
tagteam/cli.py                            panel subcommand
tagteam/data/.claude/skills/handoff/SKILL.md, .claude/skills/handoff/SKILL.md
tests/fixtures/fake_agent.py              panel mode
tests/test_panel.py (new), tests/test_db.py, tests/test_watcher.py touches
README.md, docs/how-tagteam-works.md, docs/roadmap.md, docs/phases/reviewer-panels.md
pyproject.toml (package-data glob + 3.3.0), CITATION.cff
```

## Success criteria
1. With `panel` absent from `tagteam.yaml`, the full suite and a scripted
   headless plan+impl cycle behave identically to 3.2.0 (no `panels` rows,
   no lens processes, no new prompt text, gate tests unchanged after the
   `_claim_satellite` refactor).
2. Enabled, fake agent: a reviewer-owed impl submission produces exactly
   one reviewer entry `PANEL: REQUEST_CHANGES — …` with findings grouped by
   lens when any lens requests changes; `PANEL: APPROVE — …` only when every
   configured lens succeeded and approved; `turn: lead` / cycle `approved`
   accordingly; `seq` +1 exactly once; `updated_by "<Reviewer> panel"`;
   usage rows `kind=panel:<lens>` per lens; the interactive reviewer is not
   dispatched for that submission in any watcher mode.
3. Any lens failure with no objecting lens → `fallback`: the ordinary
   reviewer turn is dispatched (headless: `run_owed_turn`; interactive:
   the send/notify), the row says why; all lenses failed → same + WARN.
4. At-most-once per submission across restart / two watchers; abandoned
   attempts swept and retried once; entry-first reconciliation from a
   crash after `add_round`; a mid-panel AMEND / ruling / reviewer write
   → `superseded`, no cycle write; a rogue lens write → lens failed +
   panel superseded, logged.
5. `ESCALATE` / `NEED_HUMAN` from a lens produce that transition with the
   lens's reason/question first; the briefer briefs it as usual.
6. `tagteam panel run` decides through the same claim path; `status`,
   `list`, `lenses`, `preview` work; `preview` spawns nothing.
7. Both SKILL copies updated and identical; README + HTW document the
   block; release 3.3.0 via PR from `phase-39-reviewer-panels`.

## Open questions for the reviewer
1. Default `on: [impl]` (plan cycles keep the single reviewer) — agree?
2. Sequential lenses (N× wall clock, one slot) vs. deferring the whole
   phase until parallel slots exist — I propose sequential now.
3. `updated_by` for the merged entry: `"<Reviewer> panel"` (e.g. `Codex
   panel`) so `tagteam usage` / the feed attribute it — or keep the plain
   reviewer name?
