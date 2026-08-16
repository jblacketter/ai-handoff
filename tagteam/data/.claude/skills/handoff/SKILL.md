---
name: handoff
description: Unified command for the AI handoff workflow. Auto-detects role and state, then executes the appropriate action.
---

# Skill: /handoff

Unified command for the AI handoff workflow. Reads your role and current state, then does the right thing.

## Setup
1. Read `tagteam.yaml` → determine your role (lead or reviewer)
2. Read `handoff-state.json` → determine current state
3. Follow the instructions for your situation below

## Commands

| Command | Description |
|---------|-------------|
| `/handoff` | Main command — auto-detects role + state, does the right thing |
| `/handoff start [phase]` | Lead starts a plan review cycle (single-phase mode) |
| `/handoff start [phase] impl` | Lead starts an implementation review cycle |
| `/handoff start --roadmap` | Lead starts full-roadmap mode (all incomplete phases) |
| `/handoff start --roadmap [phase]` | Lead starts full-roadmap mode from a specific phase |
| `/handoff status` | Show current state and orientation for both agents |

---

## `/handoff` — Main Command

**Step 1:** Read `tagteam.yaml` (your role) and `handoff-state.json` (state). To read the active cycle, run `tagteam cycle rounds --phase [phase] --type [type]` (works for both JSONL and legacy markdown cycles). Add `--tail N` to read only the last N entries when you just need the latest feedback — it saves context tokens on long cycles.

**Headless turns.** If you were started by `tagteam watch --mode headless`, your prompt already contains this contract, the current state, and the round tail, and there is no human at the terminal. The contract is identical: do the work, make exactly one cycle-writing call (`tagteam cycle add` / `cycle init`) with `--updated-by [your-agent-name]`, then stop. The orchestrator verifies that call happened and pauses (with a notification) if it did not.

**Arbiter interjections.** The human arbiter can leave notes with `tagteam interject`. In a headless turn they appear in your prompt under `=== ARBITER INTERJECTIONS (unconsumed) ===`; interactively they appear as an `interjections` list on the round in `tagteam cycle rounds` output. Treat them as authoritative instructions for this cycle (they may already have been addressed in earlier rounds — verify before acting), and mention in your submission how you handled them.

**Step 2 — CRITICAL: You MUST begin every `/handoff` response with this status banner:**

```
Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [status]
 [Human-readable description of what's happening]
```

Use values from `handoff-state.json`. For the description line, use context-appropriate text:
- Your turn (lead): "Addressing reviewer feedback." or "Submitting for review."
- Your turn (reviewer): "Reviewing lead's submission."
- Not your turn: "Waiting for [agent]'s response."
- Approved: "Cycle complete — approved!"
- Escalated: "Escalated to human arbiter."
- No state: "No active handoff cycle."

If there is no state file, show: `Phase: — | Type: — | Round: — | Turn: — | Status: none`

**Step 3:** Check state and act:

- **No state file or empty:** "No active cycle. Lead should run `/handoff start [phase]`."
- **Approved / done:** Check `run_mode` and `result` in state:
  - If `result == "roadmap-complete"`: "Roadmap complete — all phases finished!"
  - If plan → "Plan approved! Implement, then `/handoff start [phase] impl`."
  - If impl and `run_mode == "full-roadmap"` → "Implementation approved! Watcher will auto-advance to next phase." (The watcher sets `turn: lead` for the next phase — lead runs `/handoff start [next-phase]`.)
  - If impl (single-phase) → "Implementation approved! Start next phase."
- **Escalated:** "Escalated to human arbiter." The arbiter reads `tagteam brief` (a decision brief, if the escalation briefer is enabled) and rules with `tagteam rule approve|request-changes --content "…"` — or from the cockpit's Needs-you card (`tagteam serve --theme cockpit`), which runs the same command.
- **Needs-human:** "Paused for human input." The arbiter answers with `tagteam rule answer --to lead|reviewer --content "…"` (the answer arrives as an interjection and the cycle is re-armed for that role). Do not hand-edit cycle files.
- **Aborted:** "Cycle was aborted. See cycle file for reason."
- **Not your turn:** "Waiting for [other agent]. Tell them to run `/handoff`."
- **Your turn:** See below.

#### As Lead (your turn)
1. Read the reviewer's latest feedback: `tagteam cycle rounds --phase [phase] --type [plan|impl]` (or `--tail 1` for just the last entry)
2. Address the feedback: update the plan or implementation files
3. Add your round and update state in one command: `tagteam cycle add --phase [phase] --type [plan|impl] --role lead --action SUBMIT_FOR_REVIEW --round [N+1] --updated-by [your-agent-name] --content "summary of changes"`

When `TAGTEAM_STEP_B=1`, `docs/handoffs/<phase>_<type>.md` is auto-rendered on every cycle write. Do not hand-edit that file; update the cycle with `tagteam cycle add` instead. If a write produces no markdown update, check `handoff-diagnostics.jsonl` for an auto-export diagnostic.

**Mid-review amendment.** If new info arrives (e.g., the human arbiter answers an open question) while the reviewer is still on your submission and you haven't been handed back the turn, run:

```
tagteam cycle add --phase [phase] --type [plan|impl] --role lead --action AMEND --round [N] --updated-by [your-agent-name] --content "<what changed and why>"
```

This appends an amendment to the active round without bumping the round number or returning the turn. The reviewer sees the amendment in the `tagteam cycle rounds` output on their next `/handoff`. AMEND only works when the cycle is mid-review (`ready_for: reviewer`) and the `--round` matches the active round; mismatches error.

**Gatekeeper pre-checks (when `gatekeeper.enabled: true` in `tagteam.yaml`).** A deterministic gate runs between your `SUBMIT_FOR_REVIEW` and the reviewer's turn: the project's test command, the implementation-work scope check (an impl submission must contain real changes since the plan was approved) and a plan-doc check. Before you submit, run `tagteam gate check` — if it fails, fix first; the gate will bounce you otherwise. A bounce hands the turn straight back to you as a `GATE_BOUNCE` entry on your round (the failing output is in it; `tagteam gate status` shows the full report) — address it and re-submit with `--round [N+1]` exactly like a REQUEST_CHANGES.

**Reviewer panel (when `panel.enabled: true` in `tagteam.yaml`).** The reviewer's turn on the paneled cycle types may be taken by a panel of 2–3 lens reviews (correctness / scope / verification by default) whose verdicts are merged into ONE reviewer entry — its content starts `PANEL: APPROVE — …` or `PANEL: REQUEST_CHANGES — …` with findings grouped by lens (`## correctness`, `## scope`, …), written as `updated_by: <Reviewer> panel`. Treat it exactly like a reviewer response: address every group, then re-submit with `--round [N+1]`. If a lens failed, the entry says so (`<lens>: lens failed (…)`) — that axis was not assessed. When the panel could not decide (a lens failed and none objected) the ordinary reviewer turn happens instead, so a plain reviewer entry is also possible on a paneled cycle.

#### As Reviewer (your turn)
1. Read the lead's submission: `tagteam cycle rounds --phase [phase] --type [plan|impl]`
   - If the reviewer panel is enabled for this cycle type, your turn may already have been taken by the panel (a `PANEL:` reviewer entry with `updated_by: <you> panel`) — then there is nothing for you to do until the lead's next submission. You are asked to review yourself only when the panel fell back (a lens failed and none objected) or is not enabled; `tagteam panel status` shows what happened.
   - If the gatekeeper is enabled, the round tail also carries the gate's entry (`role: gatekeeper`, `GATE: PASS | tests ok (…) | scope N paths | plan-doc ok`): the tests already ran and the scope was already checked before your turn — start from those facts. A `GATE_PASS` whose content begins `GATE: checks failed but bounce cap … reached` means the lead hit the bounce cap; the failures are in the entry and the decision is yours (review anyway, request changes, or escalate).
2. Review the referenced plan/implementation files
3. Choose ONE action (all commands update both cycle and state in one call):
   - **APPROVE:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action APPROVE --round [N] --updated-by [your-agent-name] --content "Approved."`
   - **REQUEST_CHANGES:** For detailed feedback, use stdin with a heredoc. The system auto-escalates to the human arbiter when it detects 10+ consecutive stale rounds (lead re-submitting identical content with no progress).
     ```
     tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action REQUEST_CHANGES --round [N] --updated-by [your-agent-name] <<'EOF'
     Your detailed feedback here. Backticks, quotes, and special chars are safe.
     EOF
     ```
   - **ESCALATE:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action ESCALATE --round [N] --updated-by [your-agent-name] --content "Reason."`
   - **NEED_HUMAN:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action NEED_HUMAN --round [N] --updated-by [your-agent-name] --content "Question for human."`

**Step 4 — CRITICAL: You MUST end every `/handoff` response with this exact box:**

```
┌──────────────────────────────────────────────────┐
│ NEXT: Tell [agent name] to run:  /handoff        │
└──────────────────────────────────────────────────┘
```

Replace `[agent name]` with the next agent's name. For completed/escalated/needs-human states, replace with the appropriate next action.

---

## `/handoff start [phase]` — Start a New Phase

**Lead only.** Append `impl` to start an implementation review instead of a plan review.

1. Read `tagteam.yaml` to confirm you are the lead
2. Create or verify the phase plan at `docs/phases/[phase].md` (Summary, Scope, Technical Approach, Files, Success Criteria)
3. Create the cycle and update state in one command: `tagteam cycle init --phase [phase] --type [plan|impl] --lead [lead-name] --reviewer [reviewer-name] --updated-by [your-agent-name] --content "summary of initial submission"`
4. Begin your response with the status banner (showing the newly created state).
5. End with the NEXT COMMAND box.

**`/handoff start [phase] impl` means implement first.** This command is what you run (or are handed by the watcher after plan approval) when the *plan* cycle is approved and the implementation review must begin. Before step 3:
- Read the approved plan at `docs/phases/[phase].md` and the plan cycle's history (`tagteam cycle rounds --phase [phase] --type plan`).
- Implement the plan in full and run the project's verification (tests) until it passes.
- Only then run `tagteam cycle init --type impl` — **exactly once**, with a submission that summarizes what was implemented. If an impl cycle for this phase already exists, do not create another; act on it with `/handoff` instead.
An impl cycle opened over an unchanged tree is a contract violation, not a formality.

---

## `/handoff start --roadmap [phase?]` — Start Full-Roadmap Mode

**Lead only.** Runs all remaining roadmap phases end-to-end with review gates.

1. Read `tagteam.yaml` to confirm you are the lead
2. Build the phase queue using the CLI:
   - All incomplete phases: `tagteam roadmap queue`
   - Starting from a specific phase: `tagteam roadmap queue [phase-slug]`
   - This prints a comma-separated list of phase slugs (e.g. `api-gateway,dashboard,ci-integration`)
3. The first slug in the output is the starting phase
4. Create the plan for the first phase at `docs/phases/[phase].md` if it doesn't exist
5. Create the cycle via CLI: `tagteam cycle init --phase [phase] --type plan --lead [lead-name] --reviewer [reviewer-name] --content "summary of initial submission"`
6. Run:
   ```
   tagteam state set --turn reviewer --status ready \
     --phase [first-phase] --type plan --round 1 \
     --run-mode full-roadmap \
     --roadmap-queue [comma-separated-slugs-from-step-2] \
     --roadmap-index 0 \
     --command "Read .claude/skills/handoff/SKILL.md and handoff-state.json, then act on your turn" \
     --updated-by [your-agent-name]
   ```
7. Begin with the status banner. End with the NEXT COMMAND box.

**Lifecycle in full-roadmap mode:**
- Each phase goes through: plan cycle → (lead implements) → impl cycle → (advance)
- After plan approval, watcher sets `turn: lead` — lead implements and runs `/handoff start [phase] impl`
- After impl approval, watcher advances to next phase and sets `turn: lead` — lead runs `/handoff start [next-phase]`
- After the last phase's impl approval, state is set to `result: "roadmap-complete"`

**`/handoff status` in roadmap mode shows:**
```
Phase: phase-name | Type: plan | Round: 2 | Turn: lead | Status: ready
 Mode: full-roadmap | Progress: 3/7 | Next: next-phase-name
```

---

## `/handoff status` — Orientation & Reset

For both agents. Re-reads everything and gives a full orientation.

1. Read `tagteam.yaml` → show role assignment
2. Read `handoff-state.json` → show current state
3. Read active cycle via `tagteam cycle status --phase [phase] --type [type]` and `tagteam cycle rounds --phase [phase] --type [type]` → show round, last action
4. Begin with the status banner: `Phase: [phase] | Type: [plan/impl] | Round: [N] | Turn: [agent] | Status: [state]` and description line
5. Show role assignments and cycle details below the banner
6. End with the NEXT COMMAND box showing the appropriate next action.
