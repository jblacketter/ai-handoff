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

1. **Trigger.** In `watcher._StateProcessor._handle_escalated` (reached on a
   new seq whose top-level status is `escalated`, which covers both cycle
   states `escalated` and `needs-human`), when:
   - the state is not a roadmap-advance pause (`roadmap.pause_reason` /
     `state.reason` absent — those are not disputes), and
   - the briefer is enabled (default on; `briefer.enabled: false` opts out),
   - and no brief already exists for this **escalation event** — key
     `(phase, type, round, cycle_state)` where `cycle_state ∈ {escalated,
     needs-human}` (dedupe via the `briefs` table; a re-tick, watcher restart,
     or a second identical event does not re-spawn),
   the watcher spawns the briefer **synchronously** (as headless turns are;
   nothing else is dispatchable while a cycle is escalated) with a hard
   timeout (`briefer.timeout_minutes`, default 15) and then continues its
   existing escalation logging/notification, now including "brief:
   `<path>`" (or "brief failed: <reason>"). Works in **every watcher mode**
   — it is its own subprocess — and on Windows.
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
   run tests, plus Write for the one output file. `validate_config` gains the
   block (types, unknown keys, provider); `get_briefer_spec(config)` mirrors
   `get_headless_spec`. Which model tier writes good briefs (proposal Q5) is
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
5. **Storage (schema v5, additive):** table `briefs(id, ts, phase, type,
   round, cycle_state, path, content, status, provider, model, usage_row_id,
   duration_ms, stem)`; the file is the human-facing artifact, the row is the
   record the cockpit (Phase 34) will read. `usage` row with `role =
   "briefer"` per spawn (status ok/timeout/nonzero_exit/no_round→`failed`
   mapping documented; usage vocabulary unchanged — the *brief* status lives
   in `briefs`).
6. **`tagteam brief [--phase P --type T] [--list] [--json]`** — prints the
   latest brief for the current (or given) cycle (path + content), or lists
   all briefs; `--json` for scripts. Exit 1 when none.
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
     continues). Content required.
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
- `tagteam/db.py` — `SCHEMA_VERSION = 5`, `_SCHEMA_V5` briefs table,
  `add_brief`, `get_briefs`, `latest_brief(phase, type)`,
  `brief_exists(phase, type, round, cycle_state)`.
- `tagteam/config.py` — `briefer:` block validation, `get_briefer_spec`.
- `tagteam/watcher.py` — `_handle_escalated` calls the briefer (guarded by
  enabled/dedupe/pause-reason), logs/notifies the path; `_build_processor`
  validates the briefer spec at startup only when enabled and logs a
  warning (not an error) if its executable is missing — the loop must still
  run without a briefer.
- `tagteam/cli.py` — `brief`, `rule` dispatch + help.
- SKILL.md (both), README, roadmap, findings.
- Tests: `tests/test_briefer.py` (new): config/spec, prompt composition
  (whole history, headings, forbidden commands), fake-agent briefer that
  writes the file (ok / missing headings → partial / no file → failed /
  nonzero / timeout), dedupe (second escalated tick, watcher restart),
  roadmap-pause skip, disabled skip, usage row role briefer, `brief`
  command, `rule approve|request-changes|answer` transitions + audit rows +
  invalid-state rejection, `--generate`, watcher integration (escalated
  state → briefer called once; notify text includes path). `tests/test_db.py`
  v5 migration.

### Schema v5
```sql
CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    phase        TEXT NOT NULL, type TEXT NOT NULL, round INTEGER NOT NULL,
    cycle_state  TEXT NOT NULL,           -- escalated | needs-human
    path         TEXT, content TEXT,
    status       TEXT NOT NULL,           -- ok | partial | failed
    provider     TEXT, model TEXT,
    usage_row_id INTEGER, duration_ms INTEGER, stem TEXT
);
CREATE INDEX IF NOT EXISTS idx_briefs_cycle ON briefs(phase, type, round);
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

- [ ] A cycle entering `escalated` or `needs-human` causes exactly one
  briefer spawn per escalation event (dedupe survives re-ticks and a watcher
  restart); a roadmap-advance pause and `briefer.enabled: false` cause none;
  the watcher's existing escalation log/notification still fires and now
  names the brief path or the failure.
- [ ] The composed prompt contains the whole round history of the cycle, the
  escalation entry, the plan doc when present, pending interjections, the
  exact output path, the five required headings, and the no-cycle-writes
  instruction (unit tests on `compose_brief_prompt`).
- [ ] With the fake briefer: `ok` (file + all headings → `briefs` row status
  ok, `usage` row role briefer, notification names the path), `partial`
  (headings missing → stored + flagged), `failed` (no file / nonzero /
  timeout → `briefer_failed` diagnostic, no pause marker written, watcher
  keeps running).
- [ ] `tagteam brief` prints the latest brief for the current cycle (or
  `--phase/--type`), `--list`, `--json`; exit 1 when none; `--generate` runs
  the briefer on demand for the current escalated cycle.
- [ ] `tagteam rule approve|request-changes` writes a reviewer-role entry at
  the current round with `updated_by` = arbiter and the `[ARBITER RULING by
  …]` prefix, transitions the cycle (approved / in-progress→lead) and top-
  level state accordingly, and writes an `arbiter_ruling` diagnostic;
  `rule answer --to R` records an interjection targeted at R and re-arms the
  cycle to in-progress/ready_for R; all three are rejected (exit 1, message)
  when the cycle is not `escalated`/`needs-human`.
- [ ] Briefer startup validation never blocks the watcher: missing/invalid
  briefer config logs a warning and disables the briefer for that run;
  `agents.*` errors still block as before.
- [ ] Schema: `SCHEMA_VERSION == 5`; fresh/v4 → v5; newer `user_version`
  tolerated; 0.9.0 opens a v5 project (release checklist).
- [ ] Flag-off behavior unchanged for the loop itself: with `briefer.enabled:
  false`, `_handle_escalated` behaves exactly as in 0.9.0 (existing watcher
  tests pass unmodified).
- [ ] Docs: README section, help texts, SKILL.md needs-human/escalated
  guidance updated (both copies), roadmap, findings with at least one real
  brief (scratch dogfood) and its tokens/time, plus a lighter-model run.
- [ ] Released as 0.10.0 via PR → merge → tag (post-approval; CI green).

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
