# Phase 49: Legacy skill drift — detect superseded contracts, retire the dead command family

## Status
- [ ] Planning — plan cycle opened 2026-08-29. This document is the plan submission.
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**Found after Phase 48 shipped (2026-08-29).** The plugin now serves the one
current contract as `tagteam:handoff`. But three other things still describe
the contract that predates it, and two of them are shipped by tagteam itself:

1. **User-level skills.** `~/.claude/skills/` on the arbiter's machine held ten
   `handoff-*` skill directories (`handoff-cycle`, `-decide`, `-escalate`,
   `-handoff`, `-implement`, `-phase`, `-plan`, `-review`, `-status`, `-sync`;
   ~1,540 lines) from the pre-plugin era. Every one scored **0** for
   `gatekeeper|GATE_BOUNCE|interject|AMEND`; the plugin contract scores 8.
   Being user-scoped they loaded in **every** project and competed with
   `tagteam:handoff` for routing while describing a contract with no
   gatekeeper, no AMEND, no interjections. *Removed by hand on the arbiter's
   machine on 2026-08-29 (backed up first); this phase decides what the tool
   should do about the next machine.*
2. **`tagteam/data/workflows.md`** — copied into every project as
   `docs/workflows.md` by `setup` and refreshed by every `upgrade` — is 200+
   lines documenting the `/handoff-*` command family: `/handoff-plan create`,
   `/handoff-handoff read`, `/handoff-cycle start`, … None of these commands
   exist. The sweep that migrated 42 projects today re-copied this file into
   29 of them.
3. **`tagteam/server.py:1435`** still emits `"command": f"/handoff-cycle {phase}"`.

Items 2 and 3 are plain drift and are fixed here. Item 1 is a **design
question the arbiter referred to this cycle**: should a project tool touch a
user's global config at all?

## The design question — and the recommended answer

`setup.py` operates only on the project (`SKILL_RELDIR`). It already deletes
deprecated flat `handoff-*.md` files *inside a project* — precedent one level
down — but nothing reaches `~/.claude/skills/`.

| Option | For | Against |
|---|---|---|
| **A — do nothing** | A tool never writes outside the project. | The superseded skills keep loading in every project, contradicting the plugin: exactly the drift Phase 48 set out to end. |
| **B — detect and report** *(recommended)* | Surfaces the drift where the user will see it, names the exact directories and the one command to run, changes nothing outside the project. | The user has to act; a report can be ignored. |
| **C — remove** | Ends the drift without asking. | A project tool deleting from `~/.claude/` is invasive; the directories are the user's, not tagteam's (tagteam never wrote them — they came from an earlier install method); a false positive would delete a user's own skill. |

**Recommendation: B.** The deciding argument is provenance, the same rule
Phase 48 applied to vendored copies: `setup` may only delete what `setup`
wrote, and it never wrote `~/.claude/skills/handoff-*`. Detection is cheap and
exact; deletion is the user's. The reviewer is asked to rule on A/B/C.

## Scope

**In:**
- `tagteam/plugin.py`: `superseded_user_skills(config_dir) -> list[Path]` —
  directories under `<config>/skills/` named `handoff` or `handoff-*` whose
  `SKILL.md` exists. Read-only. `$CLAUDE_CONFIG_DIR` honored (as
  `plugin_status` already does for the CLI).
- `tagteam setup` and `tagteam upgrade` print, after the plugin verdict:
  `note: N superseded user-level handoff skill(s) in ~/.claude/skills compete
  with the plugin: <names> — remove with: rm -r ~/.claude/skills/{…}`. Once
  per run (not per project in `upgrade`). Silent when there are none.
- `tagteam hook session-start`: **no change** — a session-start line must stay
  a one-liner; the note belongs to setup/upgrade.
- `tagteam/data/workflows.md`: rewrite to describe the current contract
  (`/tagteam:handoff` / `/handoff` / `tagteam contract`, cycle commands,
  gate, panel, roadmap, headless) — the same content the README's "One cycle"
  and "Reference" sections carry, so it is a trim, not new prose. Keep the
  file (setup/upgrade copy it; `docs/workflows.md` is a documented artifact).
- `tagteam/server.py:1435`: `/handoff-cycle {phase}` → the project-aware
  `handoff_command(root)` form, consistent with launch intents.
- Tests: detection matrix (none / one / several / `handoff` bare / a
  non-handoff sibling ignored / `CLAUDE_CONFIG_DIR`), setup + upgrade print
  the note once and never delete, workflows.md contains no `/handoff-` token,
  server emits the current command; an audit test extends
  `TestRuntimeStringsAudit` to shipped `data/*.md` for `/handoff-`.

**Out:** deleting anything outside the project (unless the reviewer rules C —
then scope grows by a `--remove-superseded` flag that is explicit, never
default); the ten directories on the arbiter's machine (already handled by
hand); the Phase 36 upgrade harness's nested-project false positive (noted
2026-08-29 during the sweep; separate).

## Files
`tagteam/plugin.py`, `tagteam/setup.py`, `tagteam/cli.py` (upgrade note),
`tagteam/server.py`, `tagteam/data/workflows.md`, `tests/test_plugin.py`,
`tests/test_setup.py`, the server test covering line 1435, `README.md` if the
reference block needs a line.

## Done means
1. On a machine with superseded user-level skills, `tagteam setup` and
   `tagteam upgrade` print the note naming them and the removal command; on a
   clean machine they print nothing extra. Nothing under `~/.claude/` is ever
   modified by tagteam.
2. `docs/workflows.md` as installed by `setup` mentions no `/handoff-*`
   command and describes the 3.10 contract surface.
3. No runtime or shipped-doc string emits a `/handoff-*` command (audit test).
4. Full suite green.

## Verification plan
Unit as listed; manual: run `tagteam setup` on a tmp project with
`CLAUDE_CONFIG_DIR` pointing at a fixture holding two fake `handoff-*` dirs and
confirm the note; then against the real `~/.claude` (now clean) and confirm
silence.
