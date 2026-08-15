# Phase 33: Escalation Briefer (3.0 arc)

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

**What:** The first *satellite* agent of the 3.0 arc. When a cycle enters
`escalated` (reviewer `ESCALATE`, or auto-escalation after 10 stale rounds)
or `needs-human` (reviewer `NEED_HUMAN`), the watcher spawns **one** headless
turn — the *briefer* — whose only job is to write the human arbiter a
decision brief: each side's position, what is actually in dispute, what the
briefer checked, a recommendation with confidence, and the exact commands
that enact each possible ruling. The brief lands in
`docs/escalations/<phase>_<type>_r<N>.md` and an additive `briefs` table;
`tagteam brief` shows it; the escalation notification points at it. A small
`tagteam rule` CLI lets the arbiter act on it from the terminal.

**Why:** Arbitration today means re-reading the round log by hand
(proposal §1). The briefer is the biggest arbiter-experience win per line of
code (§4 Phase 33) and it respects the token budget by construction: it fires
only on escalation, once, with a hard timeout, and its usage is recorded.

**Depends on:** Phase 31 (headless adapters, `run_process`, usage table),
Phase 32 (`notify()`, interjections, controls). Source brief:
`docs/tagteam-3.0-proposal.md` §4 Phase 33, §8 Q5.

**Size:** small. Branch `phase-33-escalation-briefer`, PR at the end.
**Release:** 0.10.0.

---

## Scope

### In Scope

1. **Trigger.** In `watcher._StateProcessor` a top-level status of
   `escalated` (which covers both cycle states) leads to `_maybe_brief(state)`
   — from `_handle_escalated` on a new seq **and from the first-poll
   bootstrap** (reviewer r1: today `tick()` records a non-`ready` first
   state and returns; that path now also calls `_maybe_brief` when the state
   is escalated, so a watcher started or restarted on an already-escalated,
   unbriefed cycle briefs it exactly once; the first-poll behavior for every
   other non-ready state is unchanged). `_maybe_brief` proceeds only when:
   - the state is not a roadmap-advance pause (`roadmap.pause_reason` /
     `state.reason` absent — those are not disputes);
   - the briefer is enabled and its spec validated at startup (Scope 2);
   - the **canonical per-cycle status** (`cycle.read_status(phase, type)`)
     says `state ∈ {escalated, needs-human}` — `cycle_state` is always read
     from there, never inferred from the top-level `escalated`, and the
     round is the cycle status's round;
   - and an **atomic claim** for this escalation event succeeds (Scope 5):
     the `briefs` row is inserted with `status = running`, `kind = auto`,
     **before** the spawn, inside a single transaction under the project
     writer lock, protected by a partial unique index on `(phase, type,
     round, cycle_state) WHERE kind = 'auto'`. A second watcher, a racing
     `--generate`, a re-tick, or a restart cannot create a second automatic
     attempt: the insert fails → no spawn. Guarantee stated precisely: **at
     most one automatic briefer attempt per escalation event**; a failed or
     abandoned automatic attempt is *not* retried automatically (the log
     and notification say "run `tagteam brief --generate` to retry").
   It then spawns the briefer **synchronously** (nothing else is
   dispatchable while a cycle is escalated) with a hard timeout
   (`briefer.timeout_minutes`, default 15), updates the claim row to its
   final status, and continues the existing escalation logging/notification,
   now including "brief: `<path>`" (or "brief failed: <reason> — `tagteam
   brief --generate` to retry"). Works in **every watcher mode** — it is its
   own subprocess — and on Windows.
   **Abandoned claims** (crash between claim and completion): a `running`
   row is considered abandoned when its `stem` has no live `inflight.json`
   *and* its `started_at` is older than `timeout_minutes + 5 min`, or its
   recorded `watcher_pid`/`watcher_ident` no longer match a live process
   (same `procs.identity` check as `cancel-turn`); `_maybe_brief` and
   `tagteam brief` mark such rows `abandoned` (never respawn
   automatically) and the log/notification point at `--generate`.
2. **Briefer identity/config** (top-level `briefer:` block in `tagteam.yaml`,
   all optional):
   ```yaml
   briefer:
     enabled: true              # default true
     provider: claude           # claude | codex; default: the lead's provider
     executable: /opt/bin/claude
     args: ["--model", "opus"]  # same structural validation as headless.args
     timeout_minutes: 15
   ```
   Defaults reuse Phase 31's adapters and permission defaults; the brief is
   read-mostly work but the briefer needs Bash/Read to inspect the tree and
   run tests, plus Write for the one output file. **One rule** (reviewer
   r1): an *absent* `briefer:` block means **enabled with the lead's
   provider**; `enabled: false` disables; an *invalid* block (bad types,
   unknown keys, unknown provider) or an executable that cannot be resolved
   at startup **warns and disables the briefer for that watcher run** — it
   never blocks the loop. To keep that isolation explicit, briefer checks
   live in a separate `validate_briefer_config(config) -> list[str]` and
   `get_briefer_spec(config)`; the existing fatal `validate_config()` (agents
   block) is unchanged, so `agents.*` errors still block startup in every
   mode exactly as in 0.9.0 and briefer errors never do. Tests cover: absent
   block (enabled, lead provider), invalid block (warn + disabled), unknown
   provider (warn + disabled), missing executable (warn + disabled),
   `enabled: false`. Which model tier writes good briefs (proposal Q5) is
   *measured during soak* via `briefer.args` — no decision baked in.
3. **Composed context** (bounded, like turns): a role banner ("you are the
   escalation briefer, not a participant; you do not write rounds"), the
   escalation entry itself, the **entire** cycle round history for that
   cycle (`cycle rounds`, all entries — a brief must see the whole dispute,
   not a tail), the plan doc `docs/phases/<phase>.md` when present, pending
   interjections for the cycle, and the state. The prompt names the exact
   output path and the required section headings, and forbids any
   `tagteam cycle add`/`init`/`state set`.
4. **Output contract.** The briefer writes markdown to
   `docs/escalations/<phase>_<type>_r<N>.md` with these headings, in order:
   `## Positions` (lead / reviewer, in their own terms), `## Crux` (what is
   actually in dispute, separated from points already resolved in earlier
   rounds), `## Evidence` (what it checked: files, tests, diffs — and what it
   found), `## Recommendation` (one ruling + confidence high/medium/low +
   why), `## Rulings` (the exact `tagteam rule …` commands for each option:
   approve / request-changes / answer). For `needs-human` the same headings
   apply with `## Crux` = "what is being asked and why the reviewer could not
   decide". After exit, the orchestrator verifies the file exists, is
   non-empty, and contains the five headings; result status is `ok`,
   `partial` (file present, headings missing → still stored, flagged), or
   `failed` (no file / nonzero exit / timeout / spawn failure). A failed
   brief **never pauses** dispatch (the cycle is already stopped) — it writes
   a `briefer_failed` diagnostic and says so in the notification and log.
   The briefer's stdout events go to `.tagteam/turns/<stem>.events.jsonl` /
   `.log` like any turn (stem role `briefer`); `tagteam tail` works during it.
5. **Storage (schema v5, additive):** table `briefs` — the row is the
   **claim** as well as the record: inserted `running` before the spawn,
   updated to `ok | partial | failed | abandoned` after; `kind = auto |
   manual`; partial unique index `(phase, type, round, cycle_state) WHERE
   kind = 'auto'`; plus `started_at`, `watcher_pid`, `watcher_ident`, `stem`,
   `attempt` for recovery. The file is the human-facing artifact; the row is
   what the cockpit (Phase 34) reads. `usage` row with `role = "briefer"` per
   spawn (usage status vocabulary unchanged; the *brief* status lives in
   `briefs`; mapping: usage ok→brief ok/partial by verification, usage
   timeout/nonzero_exit/spawn_failed/no_round→brief failed).
6. **`tagteam brief [--phase P --type T] [--list] [--json] [--generate]`** —
   prints the **latest** brief for the current (or given) cycle = the
   highest-id row with status `ok` or `partial` for that cycle (any kind); if
   only failed/abandoned/running rows exist it says so and points at
   `--generate`; `--list` shows every row (kind, status, path, ts); `--json`
   for scripts; exit 1 when none. **`--generate`** = a *forced manual
   attempt*: inserts a `kind = manual` row (not subject to the auto unique
   index, so it never defeats automatic dedupe), runs the briefer now for the
   current escalated cycle, and writes to a suffixed path
   `docs/escalations/<phase>_<type>_r<N>-<attempt>.md` (`attempt` =
   1 + count of prior rows for the event) — automatic and prior manual files
   are never overwritten. Refuses (exit 1) when the cycle is not
   `escalated`/`needs-human`, or when an automatic/manual attempt is
   currently `running` and not abandoned. Failed automatic attempts are
   deduped (never auto-retried); `--generate` is the retry path.
7. **`tagteam rule <approve|request-changes|answer> [--content TEXT] [--by
   NAME] [--to lead|reviewer]`** — act on an escalation from the terminal
   (Phase 34 adds the browser). Semantics (no round-vocabulary change; the
   `rounds.role` CHECK admits only lead/reviewer, and schema changes are
   additive-only):
   - `approve` → a **reviewer-role** `APPROVE` entry at the current round
     with `updated_by = <arbiter>` and content prefixed
     `[ARBITER RULING by <name>]` — the arbiter takes the reviewer's seat to
     close the cycle. Valid only when the cycle is `escalated` or
     `needs-human`.
   - `request-changes` → likewise a reviewer-role `REQUEST_CHANGES` (lead
     continues). Content required. **Dedicated path** (reviewer r1): rulings
     go through a new `cycle.add_ruling(phase, type, action, content, by)`
     that appends the reviewer-role entry and applies the plain transition
     **without the stale-round auto-escalation check** (which would otherwise
     immediately re-escalate a cycle that reached `escalated` via 10 stale
     submissions), while preserving the canonical rounds file write, the
     shadow-DB mirror, `_auto_export_cycle_md`, and `_derive_top_level_state`
     under the writer lock — i.e. everything `add_round` does except the
     stale gate, plus the ruling prefix and `updated_by`. Tests start from a
     real auto-escalated cycle (10 identical lead submissions) and assert
     `rule request-changes` ends at `in-progress / ready_for lead` with top-
     level `ready / turn lead`; the explicit-`ESCALATE` case is tested too.
   - `answer --to lead|reviewer` (for `needs-human`, default `--to reviewer`
     since `NEED_HUMAN` is a reviewer action): records the answer as an
     interjection targeted at that role **and** re-arms the cycle to
     `in-progress / ready_for <role>` via a new `cycle.rearm(phase, type,
     role, by)` that updates the cycle status + top-level state (no rounds
     entry — the interjection is the audit record). Valid only from
     `needs-human`/`escalated`.
   Every ruling also writes a `diagnostics` row (`arbiter_ruling`) with the
   ruling, by, and the brief id it acted on (if any). SKILL.md's stale
   "edit the cycle file's Human Input Needed section" instruction is
   replaced by `tagteam brief` / `tagteam rule`.
8. **Docs**: README ("Escalations: the briefer and `tagteam rule`"), help
   texts, SKILL.md (both copies), roadmap, findings doc
   `docs/phases/escalation-briefer-findings.md` (real brief(s), tokens/time,
   model-tier note).
9. **Dogfood**: (a) force a reviewer `ESCALATE` on the scratch project with a
   deliberately arguable plan, let the briefer run with the default (lead's)
   provider, read the brief, act with `tagteam rule`; (b) same with
   `NEED_HUMAN` + `tagteam rule answer`; (c) if a real escalation happens
   during this phase's own cycles, keep its brief; (d) one run with a lighter
   `briefer.args` model to seed the Q5 measurement. This plan and impl
   cycles' reviewer turns run headless as usual.

### Out of Scope (explicitly)

- Any dashboard/inbox UI (Phase 34); cross-project views (Phase 35).
- New round actions or roles; changes to `rounds`/`cycles` schema.
- Automatic rulings — the briefer recommends, the human decides.
- Briefs for non-escalation events (e.g. every round) — token budget.
- Retrying a failed brief automatically (a human can `tagteam brief --rerun`?
  — no: out of scope; re-escalating or a manual `tagteam brief --generate`
  is *in* scope only as the single flag `tagteam brief --generate` that runs
  the briefer on demand for the current escalated cycle, same dedupe key
  ignored explicitly; useful for the model-tier experiment).

---

## Technical Approach

### Files
- `tagteam/briefer.py` — **new**: `get_briefer_spec` glue, `compose_brief_prompt`,
  `run_briefer(project_root, state) -> BriefResult` (adapter/argv via
  `headless.build_argv`, `headless.run_process` with stem role `briefer`,
  usage via `headless.parse_usage` → `db.add_usage(role="briefer")`,
  verification of the output file, `db.add_brief`, notification text),
  `brief_command`.
- `tagteam/controls.py` — `rule_command` (+ `cycle.rearm` in `cycle.py`).
- `tagteam/db.py` — `SCHEMA_VERSION = 5`, `_SCHEMA_V5` briefs table +
  partial unique index, `claim_brief(...) -> id | None` (single INSERT in a
  transaction under the writer lock; None when the auto unique index
  rejects), `finish_brief(id, status, ...)`, `mark_abandoned(id, reason)`,
  `get_briefs`, `latest_brief(phase, type)` (highest id with ok/partial),
  `running_briefs(phase, type)`.
- `tagteam/config.py` — `briefer:` block validation, `get_briefer_spec`.
- `tagteam/watcher.py` — `_maybe_brief(state)` (canonical cycle status,
  pause-reason skip, enabled, claim → spawn → finish; abandoned detection),
  called from `_handle_escalated` and from the first-poll bootstrap when the
  first state is escalated; `_build_processor` resolves the briefer spec at
  startup via `validate_briefer_config` and logs a warning + disables on any
  problem — the loop must still run without a briefer.
- `tagteam/cli.py` — `brief`, `rule` dispatch + help.
- SKILL.md (both), README, roadmap, findings.
- Tests: `tests/test_briefer.py` (new): config/spec (absent / invalid /
  unknown provider / missing executable / disabled), prompt composition
  (whole history, headings, forbidden commands), fake-agent briefer that
  writes the file (ok / missing headings → partial / no file → failed /
  nonzero / timeout), **claim semantics** (second escalated tick, watcher
  restart on an unbriefed escalation → exactly one; restart with a
  completed or failed record → none; concurrent claim attempts from two
  threads/processes → one wins; crash after claim → abandoned on the next
  tick, no auto respawn; `--generate` after failed/abandoned → manual row,
  suffixed path, auto index untouched), roadmap-pause skip, canonical
  cycle_state (needs-human vs escalated read from cycle status), usage row
  role briefer, `brief` command (latest = highest ok/partial; failed-only
  → hint), `rule approve|request-changes|answer` transitions + audit rows +
  invalid-state rejection **from a 10-stale auto-escalated cycle and from an
  explicit ESCALATE**, watcher integration (escalated state → briefer called
  once; first-poll bootstrap; notify text includes path). `tests/test_db.py`
  v5 migration + unique index.

### Schema v5
```sql
CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,           -- claim time
    phase        TEXT NOT NULL, type TEXT NOT NULL, round INTEGER NOT NULL,
    cycle_state  TEXT NOT NULL,           -- escalated | needs-human (from cycle status)
    kind         TEXT NOT NULL,           -- auto | manual
    attempt      INTEGER NOT NULL,        -- 1 for auto; 1+prior for manual
    status       TEXT NOT NULL,           -- running | ok | partial | failed | abandoned
    started_at   TEXT, finished_at TEXT,
    watcher_pid  INTEGER, watcher_ident TEXT, stem TEXT,
    path         TEXT, content TEXT,
    provider     TEXT, model TEXT,
    usage_row_id INTEGER, duration_ms INTEGER, reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_briefs_cycle ON briefs(phase, type, round);
CREATE UNIQUE INDEX IF NOT EXISTS uq_briefs_auto
    ON briefs(phase, type, round, cycle_state) WHERE kind = 'auto';
```
Downgrade: 0.9.0 (v4) opens a v5 DB (tolerates newer `user_version`,
ignores the table); verified in the release checklist.

### Fake briefer for tests
`tests/fixtures/fake_agent.py` gains `FAKE_AGENT_MODE=brief` (writes the
file named in the prompt with the five headings), `brief_partial` (three
headings), `brief_nofile`; it parses the output path from the prompt's
`=== OUTPUT PATH ===` block.

### Implementation order
0. Schema v5 + accessors + tests.
1. Config block + spec.
2. `briefer.py` compose/run/verify/store + fake modes + tests.
3. Watcher trigger + dedupe + notification + tests.
4. `tagteam brief`, `tagteam rule` (+ `cycle.rearm`) + tests.
5. Docs; dogfood (scratch ESCALATE + NEED_HUMAN, model-tier run); findings;
   bump 0.10.0; PR.

---

## Success Criteria

- [ ] A cycle entering `escalated` or `needs-human` causes **at most one
  automatic** briefer spawn per escalation event, guaranteed by the pre-spawn
  claim row + partial unique index: a re-tick, a watcher restart on an
  unbriefed escalation (first-poll bootstrap → exactly one), a restart with a
  completed/failed record (none), two concurrent claimants (one wins), and a
  crash after claim (row marked `abandoned` on the next tick, no auto
  respawn) all behave as stated; a roadmap-advance pause and
  `briefer.enabled: false` cause none; first-poll behavior for other
  non-ready states is unchanged; `cycle_state` comes from the canonical
  per-cycle status; the watcher's existing escalation log/notification still
  fires and now names the brief path or the failure + `--generate` hint.
- [ ] The composed prompt contains the whole round history of the cycle, the
  escalation entry, the plan doc when present, pending interjections, the
  exact output path, the five required headings, and the no-cycle-writes
  instruction (unit tests on `compose_brief_prompt`).
- [ ] With the fake briefer: `ok` (file + all headings → `briefs` row status
  ok, `usage` row role briefer, notification names the path), `partial`
  (headings missing → stored + flagged), `failed` (no file / nonzero /
  timeout → `briefer_failed` diagnostic, no pause marker written, watcher
  keeps running).
- [ ] `tagteam brief` prints the latest brief (highest-id ok/partial) for
  the current cycle (or `--phase/--type`), `--list`, `--json`; exit 1 when
  none, with a `--generate` hint when only failed/abandoned rows exist;
  `--generate` inserts a manual row, writes to the suffixed path without
  overwriting earlier files, refuses outside escalated/needs-human or while an
  attempt is running, and leaves the automatic dedupe intact.
- [ ] `tagteam rule approve|request-changes` writes a reviewer-role entry at
  the current round with `updated_by` = arbiter and the `[ARBITER RULING by
  …]` prefix via `cycle.add_ruling` (no stale-round auto-escalation; canonical
  file + shadow DB + auto-export + top-level state preserved), transitions
  the cycle (approved / in-progress→lead) and top-level state accordingly —
  verified from a real 10-stale auto-escalated cycle and from an explicit
  ESCALATE — and writes an `arbiter_ruling` diagnostic;
  `rule answer --to R` records an interjection targeted at R and re-arms the
  cycle to in-progress/ready_for R; all three are rejected (exit 1, message)
  when the cycle is not `escalated`/`needs-human`.
- [ ] Briefer startup validation never blocks the watcher: an absent block
  enables the briefer with the lead's provider; invalid block / unknown
  provider / missing executable each log a warning and disable the briefer
  for that run (tests for each); `agents.*` errors still block as before in
  every mode; `validate_config()` itself is unchanged.
- [ ] Schema: `SCHEMA_VERSION == 5`; fresh/v4 → v5; newer `user_version`
  tolerated; 0.9.0 opens a v5 project (release checklist).
- [ ] Flag-off behavior unchanged for the loop itself: with `briefer.enabled:
  false`, `_handle_escalated` behaves exactly as in 0.9.0 (existing watcher
  tests pass unmodified).
- [ ] Docs: README section, help texts, SKILL.md needs-human/escalated
  guidance updated (both copies), roadmap, findings with at least one real
  brief (scratch dogfood) and its tokens/time, plus a lighter-model run.
- [ ] Released as 0.10.0 via PR → merge → tag (post-approval; CI green).

## Decisions (round 1)

- Rulings use a dedicated `cycle.add_ruling` path (no stale gate).
- Escalated-on-bootstrap briefs once; at-most-once automatic attempts via a
  pre-spawn claim row + partial unique index; abandoned detection; manual
  `--generate` retries never defeat the automatic dedupe.
- `cycle_state` always from the canonical per-cycle status.
- Absent `briefer:` = enabled with the lead's provider; invalid config or
  missing executable = warn + disabled for the run; briefer validation is
  separate from the fatal `validate_config`.

## Open Questions

1. **`rule` seat semantics.** `approve`/`request-changes` are written as
   reviewer-role entries with `updated_by` = arbiter (the arbiter takes the
   reviewer's seat). Alternative: add a distinct `RULE` action (still role
   reviewer because of the CHECK) — clearer in history but a contract change
   in SKILL.md and every renderer. Recommendation: reviewer-role entries with
   the prefix (no vocabulary change) for 33; revisit if the cockpit wants a
   distinct action.
2. **Synchronous briefer inside the watcher tick.** Recommendation: yes for
   33 (nothing is dispatchable while escalated; ≤15 min); a background
   thread is a Phase 34 concern if the cockpit needs the watcher responsive
   during the brief.
3. **Default provider.** The lead's CLI (recommended: same subscription, no
   extra login) vs. a fixed `claude`. Q5 (model tier) is measured, not
   decided.

## Risks
- **Briefer bias** (it summarizes a dispute using one of the disputants'
  CLIs). Mitigation: the prompt frames it as a non-participant, requires both
  positions "in their own terms" and evidence checked; the arbiter still
  rules; provider is configurable.
- **Token cost.** Fires only on escalation; whole-history prompt is bounded
  by the 10-round cap; usage recorded under role `briefer`; opt-out.
- **Briefer writes elsewhere / touches state.** Prompt forbids it; the
  post-check ignores anything but the named file; a briefer that ran
  `cycle add` would show as a state change and is a bug to report (its
  turn log is retained).
