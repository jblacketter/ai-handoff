# Phase 33: Escalation Briefer (3.0 arc)

## Status
- [x] Planning
- [x] In Review
- [x] Approved (round 6)
- [x] Implementation (in progress)
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
`docs/escalations/<phase>_<type>_r<N>_<eventstamp>-a<attempt>.md` (plus a
`…_latest.md` alias) and an additive `briefs` table;
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
   - and an **atomic claim** for this **escalation event** succeeds (Scope
     5). The event identity (reviewer r2/r3) is a **repair-safe canonical
     key** derived only from file-backed data: `event_key = "<phase>|<type>|
     <round>|<role>|<action>|<ts>"` of the entry that produced the current
     escalated status — by construction the cycle's **latest round entry**
     at claim time (an `ESCALATE`, a `NEED_HUMAN`, or the `REQUEST_CHANGES`
     that auto-escalated), read from the canonical rounds source in the same
     transaction. `ts` is the entry's ISO timestamp stored in the JSONL, so
     the key survives a DB rebuild (unlike `rounds.id`, which reimport does
     not preserve; the numeric `rounds.id` is stored only as informational
     `event_row_id`). A re-armed cycle that escalates again at the same
     round has a new latest entry (new action/ts) and therefore a new event.
     The claim inserts the `briefs` row with `status = running`, `kind =
     auto`, `event_key`, **before** the spawn, in one transaction under the
     project writer lock, guarded by two partial unique indexes:
     `(event_key) WHERE kind = 'auto'` (at most one automatic attempt per
     event) and `(event_key) WHERE status = 'running'` (at most one
     **active** attempt per event across kinds), and by an `INSERT … WHERE
     NOT EXISTS (a row for this event_key with status ok|partial)` — a
     prior *successful* attempt of either kind satisfies the event.
     **Repair & `db_invalid`** (reviewer r3): `repair.rebuild_db_from_files_
     and_verify()` currently deletes the DB and reimports only file-backed
     cycles/state, which would erase `briefs` — and, latent since Phases
     31–32, `usage` and `interjections`. This phase makes repair **preserve
     non-file-backed tables**: before removing the DB it snapshots `usage`,
     `interjections`, `briefs` rows (via `ATTACH`/row copy into a temp
     file), rebuilds, then re-inserts them unchanged (ids preserved; the
     tables have no FKs into `rounds`, and `briefs` links by `event_key`, so
     nothing dangles). While the `db_invalid` sentinel is set, claim
     uniqueness cannot be trusted: `_maybe_brief` and `--generate` **do not
     spawn** and log "briefer skipped: DB invalid — run `tagteam state
     repair-db`". Tests: repair after auto ok / auto failed / running→
     abandoned / manual ok / manual failed, then restart: dedupe unchanged
     and `successful_brief_for_event(current event_key)` returns the same
     row (or none) as before the repair; `db_invalid` set → no spawn. A second watcher, a racing
     `--generate`, a re-tick, or a restart therefore cannot create a second
     automatic attempt or a concurrent one: the insert fails → no spawn.
     Guarantee stated precisely: **at most one automatic briefer attempt
     per escalation event, at most one running attempt per event across
     kinds**; a failed or abandoned automatic attempt is *not* retried
     automatically (the log and notification say "run `tagteam brief
     --generate` to retry").
   It then spawns the briefer **synchronously** (nothing else is
   dispatchable while a cycle is escalated) with a hard timeout
   (`briefer.timeout_minutes`, default 15), updates the claim row to its
   final status, and continues the existing escalation logging/notification,
   now including "brief: `<path>`" (or "brief failed: <reason> — `tagteam
   brief --generate` to retry"). Works in **every watcher mode** — it is its
   own subprocess — and on Windows.
   **Abandoned claims** (crash between claim and completion): the claim row
   persists the **runner identity** — `runner_pid` + `runner_ident`
   (`procs.identity(pid)` of the process that made the claim: the watcher
   for `kind = auto`, the `tagteam brief --generate` CLI process for `kind =
   manual`) — and `stem`, so detection needs no file that a crash could
   lose. A `running` row is abandoned when **(a)** `runner_pid` is not alive
   or `procs.identity(runner_pid) != runner_ident` (same check
   `cancel-turn`'s `bind_inflight` uses), **or (b)** the runner is alive but
   `started_at` is older than `timeout_minutes + 5 min` *and* no
   `inflight.json` binds to the row's `stem` (a hung runner). A live manual
   `--generate` process is therefore never misclassified: its pid/identity
   match. `_maybe_brief` and `tagteam brief` mark such rows `abandoned`
   (never respawn automatically) and the log/notification point at
   `--generate`. The briefer's `inflight.json` lifecycle is defined in
   Scope 4.
2. **Briefer identity/config** (top-level `briefer:` block in `tagteam.yaml`,
   all optional):
   ```yaml
   briefer:
     enabled: true              # REQUIRED to activate; absent block = off
     provider: claude           # claude | codex; default: the lead's provider
     executable: /opt/bin/claude
     args: ["--model", "opus"]  # same structural validation as headless.args
     timeout_minutes: 15
   ```
   Defaults reuse Phase 31's adapters and permission defaults; the brief is
   read-mostly work but the briefer needs Bash/Read to inspect the tree and
   run tests, plus Write for the one output file. **One rule** (reviewer
   r1, corrected r3 to honor the arc's hard constraint — proposal §2 "new
   behavior ships behind opt-in flags; flag-off behavior identical to the
   previous release" — which overrides §4's "opt-out via config" wording):
   the briefer is **opt-in**: an *absent* `briefer:` block, or `enabled`
   absent/false, means **disabled** and the watcher's escalation handling is
   byte-for-byte what 0.9.0 does; `enabled: true` activates it (provider
   defaults to the lead's); an *invalid* block (bad types, unknown keys,
   unknown provider) or an executable that cannot be resolved at startup
   **warns and disables the briefer for that watcher run** — it never blocks
   the loop. The arbiter may later flip the default in a subsequent phase
   once soak data exists. To keep that isolation explicit, briefer checks
   live in a separate `validate_briefer_config(config) -> list[str]` and
   `get_briefer_spec(config)`; the existing fatal `validate_config()` (agents
   block) is unchanged, so `agents.*` errors still block startup in every
   mode exactly as in 0.9.0 and briefer errors never do. Tests cover: absent
   block (disabled — 0.9.0 behavior), invalid block (warn + disabled), unknown
   provider (warn + disabled), missing executable (warn + disabled),
   `enabled: false`, and the pre-0.10 flag-off compatibility case (a
   0.9.0-era `tagteam.yaml` with no block escalates exactly as before —
   existing watcher tests unmodified). Which model tier writes good briefs
   (proposal Q5) is
   *measured during soak* via `briefer.args` — no decision baked in.
3. **Composed context** (bounded by policy, reviewer r2): a role banner
   ("you are the escalation briefer, not a participant; you do not write
   rounds"), the **escalation entry (bounded — see policy below)**, the cycle's round history
   from the grouped view (`tail_rounds(...)`, every round, including the
   additive `entries` list so nothing is hidden — Scope 7), the plan doc
   `docs/phases/<phase>.md` when present, pending interjections for the
   cycle, and the state. **Prompt-size policy** — deterministic
   per-component budgets with a hard total (reviewer r3; there is no cap on
   cycle rounds, `STALE_ROUND_LIMIT` only bounds *stale* repeats): total
   ≤ 60 000 chars, **measured on the complete serialized prompt** (banner,
   headings, notices, separators, markers included). Per-component budgets
   are **inclusive of their own markers/separators**: escalation entry
   ≤ 8 000 (when longer: head 5 800 + `[… N chars elided …]` marker + tail
   2 000, all inside 8 000 — reduced *last*, and its minimum evidence is a
   1 000-char head + 500-char tail + marker); newest 6 entries ≤ 4 000 each
   (same head/marker/tail shape); older entries: header line + first 400
   chars; plan doc ≤ 20 000 (head + marker); interjections ≤ 4 000 total;
   state ≤ 4 000; banner + headings ≤ 3 000 fixed. Reduction order when the
   measured total exceeds 60 000: older entries → header lines (oldest
   first) → plan doc → interjections → newest entries → escalation entry
   (down to its minimum). If the total is *still* over 60 000 (pathological
   framing), a **final deterministic clamp** truncates the serialized prompt
   from the end while preserving the escalation-entry minimum, the output
   path block and the required-headings block (which are placed before the
   history for exactly this reason). Every reduction is announced in the
   prompt ("N older entries abbreviated / plan truncated — read `tagteam
   cycle rounds …` / the file for the full text"). Tests: an oversized
   escalation entry, an oversized plan doc, oversized interjections, a
   40-round ordinary cycle, and a **boundary case whose component maxima
   plus framing sum above 60 000** each produce a prompt ≤ 60 000 chars with
   the expected markers, escalation head+tail present, and the path/headings
   blocks intact. The prompt names the exact output path and the
   required section headings, and forbids any `tagteam cycle add`/`init`/
   `state set`.
4. **Output contract.** The briefer writes markdown to a path that is
   **unique and stable per event and attempt** (reviewer r3):
   `docs/escalations/<phase>_<type>_r<N>_<eventstamp>-a<attempt>.md`, where
   `<eventstamp>` is the triggering entry's `ts` compacted to
   `YYYYMMDDTHHMMSSffffff` and `<attempt>` counts rows for *that event*
   (`attempt = 1 + max(attempt)` over all rows of that event, both kinds, allocated atomically inside the shared claim transaction; the automatic attempt is `a1` only when it is the first claimant — after a failed manual `a1` the automatic attempt is `a2`). Same-round
   re-escalation therefore yields distinct files; nothing is ever
   overwritten. A human-friendly alias `docs/escalations/<phase>_<type>_
   latest.md` is rewritten to the newest ok/partial brief's content after
   each success (alias only; the DB row's `path` is the unique file). The
   file — and every alias copy of it — carries a header line naming its
   event key and attempt, so even a stale alias identifies which event it
   briefs. **Alias writes happen only inside enabled briefer handling**
   (reviewer r5): the runner rewrites the alias on success and replaces it
   with the stub ("no brief yet for the current event <key>; previous
   brief: <path>") after a failed attempt; `_maybe_brief` (enabled) writes
   the stub when it handles a new event that has no successful brief and
   the alias does not already name that event (e.g. restart with a failed
   auto row, or a claim refused by a prior failed manual attempt).
   With the briefer disabled **no file is written or touched** — flag-off
   behavior stays byte-for-byte 0.9.0 and any existing alias is simply a
   historical artifact whose header names its event. Tests: enabled event-B
   failure → stub; enabled event-B no-claim (restart after a failed auto
   row) → stub; disabled re-escalation → alias content and mtime unchanged.
   **Inflight lifecycle** (reviewer r5): `headless.run_process` does not
   manage `inflight.json` (the `HeadlessEngine` wrapper does), so
   `run_briefer` owns it explicitly for **both** kinds: (i) inside the
   claim transaction, before the INSERT, read any existing `inflight.json`;
   if it binds to a live process (`controls.bind_inflight`) the claim is
   **refused** with "another turn is in flight (<stem>) — wait or
   `tagteam cancel-turn`" (no row inserted); if it does not bind (stale) it
   is removed and logged, exactly as `cancel-turn` does; (ii) after the
   claim, **before spawn**, write `inflight.json` with `role: briefer`,
   `agent: briefer`, `provider`, `stem`, `log_path`, `events_path`,
   `started_at`, `pid: null`, `child_ident: null`, `watcher_pid` /
   `watcher_ident` = the **runner's** pid/identity (same keys as engine
   turns so `bind_inflight`, `cancel-turn`, and `tagteam tail` work
   unchanged), plus `brief_id`, `event_key`, `kind`, `attempt`; (iii) in
   `on_spawn`, record the child `pid` + `child_ident`; (iv) remove the
   pointer only when `run_process` returns or raises `SpawnError` (normal
   completion of the runner, before `finish_brief`); a hard runner crash
   leaves both the pointer and the row's `runner_pid`/`runner_ident`, which
   is what abandoned detection (Scope 1) reads. `tagteam cancel-turn` on a
   briefer kills it and the attempt finishes `failed` (reason `cancelled`).
   Tests: `tagteam tail` (and `--events`) resolves the briefer's log while
   it runs and prints the `briefer` role banner; a simulated runner crash
   (row + pointer left, runner pid dead) is detected as abandoned on the
   next tick, while a live manual `--generate` (runner pid alive, identity
   matching) is not; a live unrelated inflight turn makes the claim refuse
   with no row; a stale one is removed and the claim proceeds. Headings,
   in order:
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
   manual`; `event_key` (repair-safe canonical key, Scope 1) + informational `event_row_id`; partial unique indexes
   `(event_key) WHERE kind = 'auto'` and `(event_key) WHERE status =
   'running'`; plus `started_at`, `runner_pid`, `runner_ident`, `stem`,
   `attempt` for recovery (runner = watcher for auto, CLI for manual). The file is the human-facing artifact; the row is
   what the cockpit (Phase 34) reads. `usage` row with `role = "briefer"` per
   spawn (usage status vocabulary unchanged; the *brief* status lives in
   `briefs`; mapping: usage ok→brief ok/partial by verification, usage
   timeout/nonzero_exit/spawn_failed/no_round→brief failed).
6. **`tagteam brief [--phase P --type T] [--list] [--json] [--generate]
   [--event KEY]`** — scoped to the **current escalation event** (reviewer
   r4): it computes the cycle's current `event_key` (Scope 1) and prints
   the highest-id `ok`/`partial` row **for that event** (any kind); if the
   current event has only running/failed/abandoned rows — or none — it
   reports exactly that state (with a `--generate` hint) and **never falls
   back to an older event's brief**; when the cycle is not escalated it
   says so and exits 1. Older events stay reachable via `--list` (every row
   for the cycle, newest first, with event key, kind, attempt, status, path)
   and `--event KEY` (explicit selector). `--json` for scripts. The
   accessors are **named by scope** so nothing can call the wrong one by
   accident (reviewer r5): `db.successful_brief_for_event(event_key)` is
   the only lookup `brief` (default) and `rule` use; `db.brief_history(
   phase, type)` (every row, newest first) backs `--list` and `--event KEY`
   only; there is **no** cycle-wide "latest successful brief" helper. The
   `_latest.md` alias is rewritten only on a **successful** attempt; when a
   new event is handled by the enabled briefer without a successful brief
   the alias is replaced by a stub saying "no brief yet for the current
   event <key>; previous brief: <path>" so it can never masquerade as
   current (Scope 4 states exactly who writes it; disabled = no writes).
   `rule` records in its `arbiter_ruling` diagnostic only a brief id
   belonging to the current event (or none). Tests: success(A) → re-arm →
   same-round event B with failed / running / success — `brief` shows B's
   state, never A; `successful_brief_for_event(B)` is None while
   `successful_brief_for_event(A)` still returns A's row (accessor tested
   directly); `_latest.md` stub on B open; `rule` diagnostic never links A
   while B is current. **`--generate`** = a *forced manual
   attempt* using the **same claim transaction** as the watcher (symmetric
   rule): it inserts a `kind = manual`, `status = running` row for the
   current event — refused by the `running` unique index while any attempt
   (auto or manual, not abandoned) is active, refused when the cycle is
   not `escalated`/`needs-human`, and refused while an unrelated live
   `inflight.json` binds (Scope 4); it is *not* subject to the auto index and
   never blocks a later automatic claim except through the "prior success
   satisfies the event" rule. Consequences, all tested: manual claimed first
   → the watcher's automatic claim fails while it runs; if the manual attempt
   ends `ok`/`partial`, a later automatic tick or restart does **not** run
   (event satisfied — the manual brief is the brief); if it ends `failed`,
   the automatic attempt may still run once; auto claimed first → `--generate`
   refuses until it finishes; watcher restart after manual success → none,
   after manual failure → one automatic attempt. Path: `docs/escalations/
   <phase>_<type>_r<N>_<eventstamp>-a<attempt>.md` (`attempt` = 1 + max(attempt) for the
   event) — never overwrites (see Scope 4 for the exact per-event path).
   Failed automatic attempts are never auto-retried; `--generate` is the
   retry path. `tagteam brief`, `--list`, notifications and `rule`
   diagnostics reference the row id + path of the intended event.
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
   ruling, by, the **event key it ruled on**, and the brief id it acted on
   (if any). **Capture-before-append order** (reviewer r5): `add_ruling`
   appends a new latest entry at the same round, so recomputing "current
   event" *after* the append would yield the ruling entry's key and link no
   brief. `rule_command` therefore runs the whole sequence under the
   (reentrant) project writer lock: (1) read the canonical cycle status and
   require `escalated`/`needs-human`; (2) resolve and **retain** the
   triggering `event_key` (+ `event_row_id`) from the latest entry; (3)
   select `brief_id = successful_brief_for_event(event_key)` (may be None);
   (4) re-read status + latest entry and abort with exit 1 if either changed
   (defensive — the lock makes this a no-op in practice); (5) append the
   ruling via `add_ruling` (approve / request-changes) or record the
   interjection + `rearm` (answer — same captured-event convention even
   though it appends no round entry); (6) write the `arbiter_ruling`
   diagnostic with the *captured* key and brief id. Tests: for `approve`,
   `request-changes`, and `answer`, after the command the cycle's latest
   entry is the ruling (or the cycle is re-armed) **and** the diagnostic's
   `event_key`/`brief_id` are event A's; a ruling on an event with no
   successful brief records `brief_id = null`, never an older event's.
   SKILL.md's stale
   "edit the cycle file's Human Input Needed section" instruction is
   replaced by `tagteam brief` / `tagteam rule`.
   **Grouped-rounds contract (reviewer r2):** `parse_jsonl_rounds` keeps only
   the last non-AMEND entry per role and round, so a ruling (or a second
   `NEED_HUMAN` after `rule answer`) would hide the triggering entry in
   `tagteam cycle rounds`, in headless tails, and in the briefer's history.
   Additive fix: every grouped round gains `entries` — **all** raw entries
   for that round in order (`role, action, ts, updated_by, content`) — and
   `rulings` — the subset whose content carries the `[ARBITER RULING by …]`
   prefix; `reviewer_text/reviewer_action` keep today's semantics (last
   non-AMEND entry) so existing consumers are unchanged, and the DB-first
   reader in `cycle.read_rounds`/`tail_rounds` attaches the same lists.
   `cycle render` already prints every entry. Tests: `cycle rounds` output
   and the headless/briefer prompt history after `approve`, after
   `request-changes`, and after `answer` → second `NEED_HUMAN` at the same
   round show both the escalation and the ruling / both `NEED_HUMAN`s.
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
- Retrying a failed brief automatically. Manual retry is `tagteam brief
  --generate` (Scope 6), which uses the same claim transaction as the
  watcher.

---

## Technical Approach

### Files
- `tagteam/briefer.py` — **new**: `get_briefer_spec` glue, `compose_brief_prompt`,
  `run_briefer(project_root, state, kind) -> BriefResult` — the briefer's
  own runner, used by watcher-auto and CLI-manual alike: claim (Scope 1,
  including the inflight collision check), **owns `inflight.json`** for
  the attempt (create before spawn / update in `on_spawn` / remove on
  normal completion — Scope 4; `headless.run_process` itself does not
  manage it), adapter/argv via `headless.build_argv`, `headless.run_process`
  with stem role `briefer`, usage via `headless.parse_usage` →
  `db.add_usage(role="briefer")`, verification of the output file,
  `db.finish_brief`, alias/stub write, notification text; `brief_command`.
- `tagteam/controls.py` — `rule_command` (capture-before-append under the
  writer lock, Scope 7; reuses `bind_inflight`), + `cycle.rearm` and
  `cycle.add_ruling` in `cycle.py`.
- `tagteam/db.py` — `SCHEMA_VERSION = 5`, `_SCHEMA_V5` briefs table +
  two partial unique indexes, `current_event(phase, type) -> (event_key,
  row_id, entry)`, `claim_brief(event_key, kind, runner_pid, runner_ident,
  ...) -> (id, attempt) | None` (single INSERT…WHERE NOT EXISTS in a
  transaction under the writer lock; None when either unique index or the
  prior-success rule rejects; allocates `attempt`),
  `finish_brief(id, status, ...)`, `mark_abandoned(id, reason)`,
  **`successful_brief_for_event(event_key)`** (highest-id ok/partial row
  of that event, or None — the only lookup `brief`/`rule` use),
  **`brief_history(phase, type)`** (all rows newest first — `--list` /
  `--event` only), `running_briefs(event_key)`. No cycle-wide "latest
  successful" helper exists.
- `tagteam/repair.py` — `rebuild_db_from_files_and_verify` snapshots and
  restores `usage`, `interjections`, `briefs` (non-file-backed tables).
- `tagteam/parser.py` / `tagteam/cycle.py` — additive `entries` + `rulings`
  lists on grouped rounds (JSONL and DB-first paths).
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
  role briefer, `brief` command (current-event scoped:
  `successful_brief_for_event(current key)`; running/failed/abandoned/none
  → state + hint, never an older event; `--list`/`--event` for history;
  accessor tested directly for success(A) → failed(B) / running(B)),
  `rule approve|request-changes|answer` transitions + audit rows +
  invalid-state rejection **from a 10-stale auto-escalated cycle and from an
  explicit ESCALATE**, **capture-before-append** (`arbiter_ruling`
  diagnostic links event A's brief after the ruling entry exists, for
  approve / request-changes / answer; null when A has no successful
  brief), **inflight lifecycle** (`tagteam tail` resolves a running
  briefer; simulated runner crash → abandoned next tick; live manual
  `--generate` not misclassified; live unrelated inflight → claim refused
  with no row; stale inflight → removed, claim proceeds), **alias
  scoping** (enabled failure / no-claim → stub; disabled re-escalation →
  no write), **event identity** (NEED_HUMAN r5 → brief → `rule
  answer` → NEED_HUMAN again r5 → a *second* automatic brief; restart on the
  same event → none), **manual/auto orderings** (manual first then auto tick
  → no concurrent spawn; manual ok → auto never runs; manual failed → auto
  runs once; auto first → `--generate` refuses), **grouped rounds** (`entries`
  / `rulings` after approve, request-changes, answer→re-NEED_HUMAN, in CLI
  output and in the composed prompt), **prompt-size policy** (escalation
  entry head+tail always present; oversized entry/plan/interjections and a framing-boundary case each fit under 60 000 chars with
  abbreviation note), watcher integration (escalated state → briefer called
  once; first-poll bootstrap; notify text includes path). `tests/test_db.py`
  v5 migration + both unique indexes.

### Schema v5
```sql
CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,           -- claim time
    phase        TEXT NOT NULL, type TEXT NOT NULL, round INTEGER NOT NULL,
    cycle_state  TEXT NOT NULL,           -- escalated | needs-human (from cycle status)
    event_key    TEXT NOT NULL,           -- "<phase>|<type>|<round>|<role>|<action>|<ts>" of the triggering entry (repair-safe)
    event_row_id INTEGER,                 -- rounds.id at claim time (informational; not preserved by reimport)
    kind         TEXT NOT NULL,           -- auto | manual
    attempt      INTEGER NOT NULL,        -- 1 + max(attempt) over the event's rows (both kinds), allocated in the claim transaction
    status       TEXT NOT NULL,           -- running | ok | partial | failed | abandoned
    started_at   TEXT, finished_at TEXT,
    runner_pid   INTEGER, runner_ident TEXT, stem TEXT,  -- process that made the claim (watcher for auto, CLI for manual)
    path         TEXT, content TEXT,
    provider     TEXT, model TEXT,
    usage_row_id INTEGER, duration_ms INTEGER, reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_briefs_cycle ON briefs(phase, type, round);
CREATE UNIQUE INDEX IF NOT EXISTS uq_briefs_auto    ON briefs(event_key) WHERE kind = 'auto';
CREATE UNIQUE INDEX IF NOT EXISTS uq_briefs_running ON briefs(event_key) WHERE status = 'running';
-- claim = single INSERT … SELECT … WHERE NOT EXISTS (ok|partial row for event_key), under the writer lock
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
  automatic** briefer spawn per escalation event (`event_key` = canonical key of the triggering
  entry) and at most one *running* attempt per event across kinds,
  guaranteed by the pre-spawn claim row + the two partial unique indexes +
  the prior-success rule: a re-tick, a watcher restart on an unbriefed
  escalation (first-poll bootstrap → exactly one), a restart with a
  completed/failed record (none), two concurrent claimants (one wins), a
  crash after claim (row marked `abandoned` on the next tick, no auto
  respawn), a re-armed cycle escalating again at the same round (a new
  event → one new automatic brief), and every manual/auto ordering listed
  in Scope 6 all behave as stated; a roadmap-advance pause and
  `briefer.enabled: false` cause none; first-poll behavior for other
  non-ready states is unchanged; `cycle_state` comes from the canonical
  per-cycle status; the watcher's existing escalation log/notification still
  fires and now names the brief path or the failure + `--generate` hint.
- [ ] The composed prompt contains the escalation entry (head+tail always
  present within its inclusive budget), the cycle's grouped rounds with
  `entries` (nothing hidden), the plan doc when present, pending
  interjections, the exact output path, the five required headings, and the
  no-cycle-writes instruction; the serializer measures the complete prompt
  and enforces the 60 000-char total via the fixed reduction order, markers,
  and the final clamp — tested with an oversized escalation entry, plan
  doc, interjections, a 40-round cycle, and the framing-boundary case (unit
  tests on `compose_brief_prompt`).
- [ ] Grouped rounds (`tagteam cycle rounds`, `tail_rounds`, headless and
  briefer prompts) carry `entries` and `rulings` so the triggering
  escalation and the ruling — or two `NEED_HUMAN`s at one round — are both
  visible; `reviewer_text` semantics unchanged for existing consumers.
- [ ] With the fake briefer: `ok` (file + all headings → `briefs` row status
  ok, `usage` row role briefer, notification names the path), `partial`
  (headings missing → stored + flagged), `failed` (no file / nonzero /
  timeout → `briefer_failed` diagnostic, no pause marker written, watcher
  keeps running).
- [ ] `tagteam brief` prints `successful_brief_for_event(current event_key)`
  (or `--phase/--type`, `--event KEY`), reports running/failed/
  abandoned/none for the current event without falling back to an older
  event (no cycle-wide "latest" accessor exists), `--list` shows
  `brief_history`, `--json`; exit 1 when none, with a
  `--generate` hint when only failed/abandoned rows exist; `_latest.md`
  becomes a stub when the enabled briefer handles a new event without a
  successful brief;
  `--generate` inserts a manual row, writes to the suffixed path without
  overwriting earlier files, refuses outside escalated/needs-human or while an
  attempt is running, and leaves the automatic dedupe intact.
- [ ] `tagteam rule approve|request-changes` writes a reviewer-role entry at
  the current round with `updated_by` = arbiter and the `[ARBITER RULING by
  …]` prefix via `cycle.add_ruling` (no stale-round auto-escalation; canonical
  file + shadow DB + auto-export + top-level state preserved), transitions
  the cycle (approved / in-progress→lead) and top-level state accordingly —
  verified from a real 10-stale auto-escalated cycle and from an explicit
  ESCALATE — and writes an `arbiter_ruling` diagnostic whose `event_key`
  and `brief_id` are the **triggering** event's, captured under the writer
  lock before the ruling entry is appended (tested for approve,
  request-changes, answer; null brief id when that event has none);
  `rule answer --to R` records an interjection targeted at R and re-arms the
  cycle to in-progress/ready_for R; all three are rejected (exit 1, message)
  when the cycle is not `escalated`/`needs-human`.
- [ ] Briefer startup validation never blocks the watcher: an absent block
  (or `enabled` absent/false) leaves the briefer off and escalation handling
  identical to 0.9.0 (pre-0.10 config compatibility test; existing watcher
  tests unmodified); `enabled: true` activates it with the lead's provider
  by default; invalid block / unknown provider / missing executable each log
  a warning and disable the briefer for that run (tests for each);
  `agents.*` errors still block as before in every mode; `validate_config()`
  itself is unchanged.
- [ ] Repair safety: `rebuild_db_from_files_and_verify` preserves `usage`,
  `interjections`, and `briefs` rows; after repair, the dedupe outcome on
  restart and `successful_brief_for_event(current event_key)` (the current
  event's row, or None) are identical to before the repair for auto ok /
  auto failed / running→abandoned / manual ok / manual failed; with
  `db_invalid` set the briefer does not spawn and says why.
- [ ] Inflight + abandoned detection: `run_briefer` writes `inflight.json`
  (role `briefer`, runner pid/identity, child pid on spawn, brief id /
  event key) before spawn and removes it only on normal completion;
  `tagteam tail` follows a running briefer; a claim is refused (no row)
  while an unrelated live turn is in flight and proceeds after removing a
  stale pointer; a simulated runner crash is marked `abandoned` on the next
  tick while a live manual `--generate` (runner pid alive + identity match)
  is not; `cancel-turn` ends the attempt `failed`.
- [ ] `_latest.md` is written only by enabled briefer handling (success →
  content; enabled failure / no-claim on an unsatisfied event → stub naming
  the current event); with the briefer disabled no file under
  `docs/escalations/` is written or touched.
- [ ] Artifact paths: same-round re-escalation produces two distinct brief
  files (`…_r5_<stampA>-a1.md`, `…_r5_<stampB>-a1.md`); attempts number
  `1 + max(attempt)` across kinds (a failed manual `a1` followed by the
  automatic attempt yields `a2`, both files preserved); nothing overwritten; `_latest.md` alias tracks the newest
  ok/partial; `brief`, `--list`, DB rows, notifications and `rule`
  diagnostics reference the intended event's row + path.
- [ ] Schema: `SCHEMA_VERSION == 5`; fresh/v4 → v5; newer `user_version`
  tolerated; 0.9.0 opens a v5 project (release checklist).
- [ ] Flag-off behavior unchanged for the loop itself: with no `briefer:`
  block (or `enabled` false), `_handle_escalated` and the first-poll
  bootstrap behave exactly as in 0.9.0 (existing watcher tests pass
  unmodified).
- [ ] Docs: README section, help texts, SKILL.md needs-human/escalated
  guidance updated (both copies), roadmap, findings with at least one real
  brief (scratch dogfood) and its tokens/time, plus a lighter-model run.
- [ ] Released as 0.10.0 via PR → merge → tag (post-approval; CI green).

## Decisions (rounds 1–2)

- Escalation event identity = canonical `event_key` (see r3 bullet); auto attempts unique
  per event; one running attempt per event across kinds; prior success
  satisfies the event; manual `--generate` uses the same claim transaction.
- Grouped rounds gain additive `entries` + `rulings`; `reviewer_text`
  unchanged.
- Prompt-size policy (per-component budgets, hard 60 000 total, final clamp) replaces the (false) 10-round bound.
- Rulings use a dedicated `cycle.add_ruling` path (no stale gate).
- Escalated-on-bootstrap briefs once; at-most-once automatic attempts via a
  pre-spawn claim row + partial unique index; abandoned detection; manual
  `--generate` retries never defeat the automatic dedupe.
- `cycle_state` always from the canonical per-cycle status.
- Briefer is **opt-in** (`briefer.enabled: true`); absent = disabled =
  0.9.0 behavior (arc hard constraint wins over §4's "opt-out" wording);
  invalid config or missing executable = warn + disabled for the run;
  briefer validation is separate from the fatal `validate_config`.
- Event identity = repair-safe canonical `event_key` (phase|type|round|
  role|action|ts of the triggering entry); repair preserves `usage`,
  `interjections`, `briefs`; `db_invalid` ⇒ no briefer spawn.
- Artifact paths are unique per event + attempt (`attempt = 1 + max` across
  kinds, allocated in the claim transaction); `_latest.md` is an alias that
  becomes a stub when a new event opens without a successful brief.
- `tagteam brief` and `rule` diagnostics are scoped to the current event;
  no silent fallback to older events (`--list` / `--event KEY` for history).
- Prompt-size policy has per-component budgets and a hard 60 000 total.
- Accessors are named by scope: `successful_brief_for_event(event_key)`
  (the only lookup `brief`/`rule` use) vs `brief_history(phase, type)`
  (`--list`/`--event` only); no cycle-wide "latest successful" helper.
- `rule` captures the triggering `event_key` + brief id under the writer
  lock **before** appending the ruling entry / re-arming; the diagnostic
  records the captured values.
- `run_briefer` owns `inflight.json` for both kinds (create before spawn,
  child pid on spawn, remove on normal completion); the claim row persists
  `runner_pid`/`runner_ident`; abandoned = runner dead/identity mismatch,
  or hung past timeout+5 min with no binding pointer; a live unrelated
  inflight turn refuses the claim.
- `_latest.md` (content or stub) is written only by enabled briefer
  handling; disabled = no writes under `docs/escalations/`.

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
- **Token cost.** Fires only on escalation, at most once automatically per
  event; the prompt is bounded by the size policy (escalation + newest 6
  entries head+tail-bounded, older abbreviated, hard 60 000-char cap with a final clamp) rather than by any
  round cap (cycles can exceed 10 rounds); usage recorded under role
  `briefer`; opt-out.
- **Briefer writes elsewhere / touches state.** Prompt forbids it; the
  post-check ignores anything but the named file; a briefer that ran
  `cycle add` would show as a state change and is a bug to report (its
  turn log is retained).
