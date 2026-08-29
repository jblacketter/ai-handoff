# Phase 49: Legacy skill drift — detect superseded contracts, retire the dead command family

## Status
- [x] Planning — plan approved round 3 (2026-08-29, bb2f433). Round 1: reviewer ruled **B (detect and report; no deletion, no flag)** and required four plan changes, applied in round 2 (marked *r2*).
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
| **B — detect and report** *(ruled)* | Surfaces the drift where the user will see it: names the candidate paths and the manual review action (review each; remove confirmed pre-plugin copies yourself), and changes nothing outside the project. *(r3: no shell command is emitted.)* | The user has to act; a report can be ignored. |
| **C — remove** | Ends the drift without asking. | A project tool deleting from `~/.claude/` is invasive; the directories are the user's, not tagteam's (tagteam never wrote them — they came from an earlier install method); a false positive would delete a user's own skill. |

**Ruled: B** (reviewer, round 1). The deciding argument is provenance, the
same rule Phase 48 applied to vendored copies: `setup` may only delete what
`setup` wrote, and it never wrote `~/.claude/skills/handoff-*`. *(r2)* And
detection is **not** exact: a name match is a *candidate*, not a proven
superseded artifact — a user may own a custom `handoff` or `handoff-foo`
skill, and tagteam has no provenance (no hash set, since it never shipped
these) to tell the two apart. So the tool reports candidates, states it
changed nothing, and leaves review and removal to the user. No deletion
behavior and no removal flag in this phase.

## Scope

**In:**
- *(r2)* `tagteam/plugin.py`: `claude_config_dir() -> Path` — a dedicated,
  tested resolver for the read-only scan: `$CLAUDE_CONFIG_DIR` when set and
  non-empty (expanded, not required to exist), else `~/.claude`. (Note: the
  Phase 48 `plugin_status` does *not* resolve this itself — it delegates
  discovery to the Claude CLI; the round-1 text claiming otherwise was wrong.)
- *(r2, r3)* `tagteam/plugin.py`: `legacy_handoff_skill_candidates(config_dir=None)
  -> list[Path]` — entries directly under `<config>/skills/` whose name is
  `handoff` or starts with `handoff-`, which are directories (a symlink to a
  directory counts) and which contain a `SKILL.md`. Read-only; sorted by name.
  **Path reporting, precisely:** the config root is expanded and absolutized
  once (`Path(...).expanduser().absolute()` — lexical, no symlink
  dereference); each candidate is reported as `<that root>/skills/<name>` —
  the absolute *lexical* path, never `realpath`'d, so a symlinked entry is
  reported by its link path under the config dir, not by its target. This is
  the path the user would act on. A name match is a candidate only (see
  above).
- *(r2)* `tagteam setup` prints, after the plugin verdict, when candidates
  exist:
  ```
  note: N user-level skill(s) under <config>/skills may conflict with the tagteam plugin's /tagteam:handoff:
    <resolved path 1>
    <resolved path 2>
  tagteam did not modify them. Review each; remove confirmed pre-plugin copies yourself.
  ```
  No shell command is printed — no `rm`, no brace expansion, nothing to paste.
  Silent when there are none.
- *(r2)* `tagteam upgrade` suppresses the per-project note (it calls
  `setup.main(project, report_user_skills=False)`) and prints **one** aggregate
  note at the end of the run, same shape, exactly once regardless of how many
  projects were upgraded. `setup.main(..., report_user_skills=True)` is the
  default so `tagteam setup` and `quickstart` report once per invocation.
- `tagteam hook session-start`: **no change** — a session-start line must stay
  a one-liner; the note belongs to setup/upgrade.
- `tagteam/data/workflows.md`: rewrite to describe the current contract
  (`/tagteam:handoff` / `/handoff` / `tagteam contract`, cycle commands,
  gate, panel, roadmap, headless) — the same content the README's "One cycle"
  and "Reference" sections carry, so it is a trim, not new prose. Keep the
  file (setup/upgrade copy it; `docs/workflows.md` is a documented artifact).
- `tagteam/server.py:1435`: `/handoff-cycle {phase}` → the project-aware
  `handoff_command(root)` form, consistent with launch intents.
- Tests *(r2)*: `claude_config_dir` unset → `~/.claude`; set → that path
  (expanded; nonexistent allowed); set to empty → `~/.claude`. Candidate
  matrix: none / one / several / bare `handoff` / `handoff-foo` without
  `SKILL.md` ignored / non-handoff sibling ignored / a file named `handoff-x`
  ignored / symlinked entry whose **target lies outside the config dir**
  (e.g. `<config>/skills/handoff-old -> <tmp>/elsewhere/skill`) reported
  exactly once as `<config>/skills/handoff-old`, and the outside target path
  never appears in the output; `$CLAUDE_CONFIG_DIR` given as a relative path
  or with `~` → reported paths are absolute. `setup`: note printed
  once with resolved paths and the "did not modify" sentence, no `rm` token
  anywhere in the output, candidates still present afterwards (byte-identical);
  silent on a clean config dir. `upgrade` over **three** registered projects
  with candidates present: the note appears exactly once in the whole output
  and not inside any per-project block; candidates untouched. workflows.md
  contains no `/handoff-` token; server emits the project-aware command; the
  audit test extends `TestRuntimeStringsAudit` recursively over shipped
  `tagteam/data/**/*.md` and runtime `tagteam/**/*.py` for `/handoff-`.

**Out:** deleting anything outside the project — *(r2)* ruled out for this
phase, including any removal flag; the ten directories on the arbiter's machine (already handled by
hand); the Phase 36 upgrade harness's nested-project false positive (noted
2026-08-29 during the sweep; separate).

## Files
`tagteam/plugin.py`, `tagteam/setup.py`, `tagteam/cli.py` (upgrade note),
`tagteam/server.py`, `tagteam/data/workflows.md`, `tests/test_plugin.py`,
`tests/test_setup.py`, the server test covering line 1435, `README.md` if the
reference block needs a line.

## Done means
1. *(r2)* On a machine with candidate user-level skills, `tagteam setup`
   prints the note once with their resolved paths and states tagteam did not
   modify them; `tagteam upgrade` prints it exactly once for the whole run,
   never per project; neither prints a shell command; on a clean config dir
   they print nothing extra. Nothing under the config dir is ever modified by
   tagteam (tested byte-identical).
2. `docs/workflows.md` as installed by `setup` mentions no `/handoff-*`
   command and describes the 3.10 contract surface.
3. No runtime or shipped-doc string emits a `/handoff-*` command (audit test).
4. Full suite green.

## Verification plan
Unit as listed; manual: run `tagteam setup` on a tmp project with
`CLAUDE_CONFIG_DIR` pointing at a fixture holding two fake `handoff-*` dirs and
confirm the note; then against the real `~/.claude` (now clean) and confirm
silence.
