# Phase 48: Plugin distribution — one contract, installed once, instead of 58 forks

## Status
- [ ] Planning — plan cycle not yet started. This document is the plan submission.
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** ship tagteam's Claude-facing contract as an installable Claude Code
plugin, and change `tagteam setup` from *vendoring* that contract into every
project to *removing* the vendored copy once the plugin is present. The Python
package keeps the engine; the plugin carries the contract surface.

**Why:** `tagteam setup` copies `.claude/skills/handoff/SKILL.md` into every
project it touches. On the arbiter's machine that is **58 copies**, and they have
forked. Excluding venv and build artifacts:

| Copies | Contract | State |
|---|---|---|
| 43 | 189 lines | current |
| 6 | 155 lines | **stale** |
| 1 | 110 lines | very stale (a worktree) |

The six stale projects — `bugalizer`, `harmonic`, `northstar/ns-wip-tests`,
`archive/qrcode`, `archive/handoff-test`, `archived/arcprize` — run a contract
containing **zero** occurrences of `gatekeeper` (3 in current), the one-run
verification rule (1), `AMEND` (3), arbiter `interject` (2), `GATE_BOUNCE` (1),
or reviewer `panel` (2).

This is not cosmetic drift. An agent in `bugalizer` cannot honor the one-run rule
or respond to a gate bounce, because nothing ever told it those exist. The engine
in those projects moved on; the contract their agents read did not. **Every
`setup` is a fork**, and the fork count only grows.

## The seam

The split is sharper than expected, and the code already settles it.

**Skills carry no per-project state.** `.claude/skills/handoff/SKILL.md` contains
**zero** `{{...}}` placeholders. It already resolves roles at runtime — "Step 1:
Read `tagteam.yaml` → determine your role." Nothing in it needs to be
per-project; it is a pure, versioned document.

**Templates do carry per-project state.** Eight files under
`tagteam/data/templates/` use `{{lead}}` / `{{reviewer}}`, are seeded once by
`setup`, and then become living documents the team edits.

That yields a rule sharp enough to classify every shipped file:

> **If Claude reads it, it can be a plugin asset.
> If the tagteam CLI reads it, or a human edits it after seeding, it stays.**

### Moves to the plugin

- `.claude/skills/handoff/` — the contract. Versioned once, installed once.
- *(new)* `agents/` — a `codex-brief` agent that drafts a submission payload
  without ever making the cycle-writing call, and a reviewer-side counterpart.
- *(new)* `hooks/hooks.json` — a `SessionStart` hook printing phase / type /
  round / turn when the project has a `handoff-state.json`. Today the cycle state
  is learned by running `/handoff`; the hook makes it ambient.

### Stays with the package

`templates/`, `checklists/`, `workflows.md`, seeded `roadmap.md` and
`decision_log.md` (per-project, substituted, human-edited); `data/panels/*.md`
(read by `panel.py`, never by Claude directly); and the entire CLI — state
machine, cycle store, watcher, gatekeeper, roadmap DAG, registry, cockpit, hub.

The plugin is the **contract surface**. The package stays the **engine**.

## Layout

Verified against plugins installed on the arbiter's machine;
`understand-anything` is the closest analogue, shipping `agents/`, `hooks/` and
`skills/` alongside a real runtime.

```
tagteam-plugin/
├── .claude-plugin/
│   ├── plugin.json          # name, version, description, author, repo, license
│   └── marketplace.json     # single-plugin marketplace manifest
├── skills/
│   └── handoff/SKILL.md     # the single source of truth
├── agents/
│   ├── codex-brief.md
│   └── handoff-reviewer.md
└── hooks/
    └── hooks.json           # SessionStart: surface cycle state
```

## The hard problem: version coupling

Today the contract and the engine ship together and cannot disagree. Splitting
them introduces the **mirror** of the current bug: the plugin instructs an agent
to run `tagteam gate check` against a project pinned to CLI 3.2, which has no
`gate` subcommand. Today: stale contracts against new engines. Tomorrow, if
unmanaged: new contracts against old engines.

Options considered:

1. **Lockstep release from this repo.** `scripts/release.py` bumps
   `plugin.json` alongside `pyproject.toml` and `CITATION.cff`. Precedent
   exists — that script already keeps `CITATION.cff` in sync, and
   `.github/workflows/publish.yml` fails a tag build on a version mismatch.
2. **Declared minimum.** `plugin.json` records a minimum tagteam version; the
   skill checks `tagteam --version` on its first turn and **warns** rather than
   failing.
3. **Independent versioning.** Rejected — it is the current bug with the arrow
   reversed.

**Chosen: 1 + 2.** Lockstep makes disagreement rare; the runtime check makes it
loud when it happens anyway. Someone who installs the plugin without the package
gets a clear message instead of a confusing one.

## Migration

The mechanism already exists one level down. `setup.py` deletes deprecated flat
`handoff-*.md` skill files on every run; this is the same cleanup, one level up.

`tagteam registry list` already enumerates every project `setup` has touched, so
the worklist is generated rather than hand-assembled.

Staged so no project breaks mid-flight:

1. Ship the plugin. Leave `setup` vendoring as it does today — **no behavior
   change**, the plugin is purely additive and can be installed by anyone who
   wants it.
2. Migrate **aegis alone**. Run a full cycle on it. Confirm the reviewer still
   receives the contract, and that Phase 47's `PROJECT CONTEXT` injection still
   resolves with the skill coming from a plugin rather than the project tree.
3. Flip `setup` to remove-instead-of-write when the plugin is detected, printing
   what it removed and why.
4. Sweep the registry. The six stale projects get the current contract the moment
   they install — that is the payoff.

## Scope

**In:** a new plugin tree in this repo; `.claude-plugin/plugin.json` and
`marketplace.json`; the handoff skill relocated (single source of truth, not a
second copy); two agent definitions; a `SessionStart` hook; `setup.py` gaining
plugin detection plus the remove path; `scripts/release.py` version-bumping
`plugin.json`; the publish workflow's version guard extended; tests for
detection, the remove path, and version-skew warning; README install section.

**Out:** the CLI's behavior, the state machine, the cycle store, the watcher, the
gatekeeper, the panel, the roadmap DAG, the cockpit and hub. No change to
`tagteam.yaml` schema. No change to the contract's *text* — this phase moves it,
it does not edit it (a text change would confound the migration).

## Deliberately not done

- **Not moving templates or checklists.** Per-project working documents with live
  `{{}}` substitution; moving them breaks seeding for zero gain.
- **Not moving the panel lenses.** `panel.py` reads them — engine data that
  happens to be markdown.
- **Not bundling the CLI into the plugin.** Plugins are not a package manager;
  `uv tool install tagteam` stays the install path.
- **Not auto-migrating projects.** `setup` removes the vendored copy only when
  run, and only when the plugin is actually present. No action at a distance.
- **Not editing the contract text in this phase.** Move first, edit later, so a
  migration bug and a contract bug can never be confused.

## Decided by the arbiter

**The plugin ships from this repo** (2026-08-29). Version coupling is the
principal risk in this phase, and lockstep release only works if `plugin.json`
sits beside `pyproject.toml` where `scripts/release.py` can bump both and
`publish.yml` can fail a tag on a mismatch. A separate `tagteam-marketplace`
repo was considered and rejected: marginally easier to link to, no other
benefit, and it would put the two halves of a version contract in different
release cycles. The marketplace manifest lives here too, so the plugin is added
with `/plugin marketplace add jblacketter/tagteam`.

## For the reviewer to rule on

**Question: what is in v1 — the skill alone, the skill plus the hook, or skill +
hook + agents?**

The arbiter has explicitly referred this to the review cycle rather than
pre-deciding it. Rule on it in the plan review; the lead will implement whatever
scope is approved.

The three candidates:

| Option | Contents | Case for | Case against |
|---|---|---|---|
| **A — narrow** | skill only | Smallest reviewable unit. The migration is the only risk in flight, so nothing else can confound a failure. | Ships a plugin nobody feels day to day; the visible payoff waits for v2. |
| **B — middle** | skill + `SessionStart` hook | Hook is ~15 lines of JSON, touches no migration path, and is the one change felt immediately in all 46 projects (cycle state on session start instead of typing `/handoff`). | Two things in one review, even if the second is small. |
| **C — broad** | skill + hook + agents | Best portfolio artifact; the whole contract surface lands at once. | `codex-brief` needs its own design conversation — it is the agent forbidden from making the cycle-writing call, which is a subtle contract in itself. A review would argue two unrelated designs at once. |

The lead's recommendation is **B**, on the grounds that the hook carries no
migration risk and the agents carry a design question that deserves its own
cycle. The reviewer should weigh in particular:

- whether a `SessionStart` hook shipped by a plugin can misfire in a project that
  has `tagteam.yaml` but no `handoff-state.json` yet, or in a non-tagteam project
  that happens to have the plugin installed;
- whether bundling agents in v1 would make the remove-path migration harder to
  reason about, or is genuinely independent of it;
- whether "provable in one cycle" is the right bar for a phase that can break the
  contract in 46 repositories at once.

Evidence that would settle it: if the hook can be shown inert without a
`handoff-state.json`, B carries no more migration risk than A.

**Secondary question: confirm the migration set.** The arbiter delegated that
call to the lead and asked for it to be re-raised here. The proposed set is in
*Migration set* below — three migrate, three skip, with `archive/handoff-test`
getting a version note rather than a migration. Ratify or amend it in the plan
review; it changes step 4 of the migration plan, not the design.

## Migration set (delegated to the lead by the arbiter; confirm in review)

The arbiter delegated this call and asked that it be re-raised in the cycle. The
lead's call, by commit recency:

| Project | Last commit | Call |
|---|---|---|
| `bugalizer` | 8 weeks | **migrate** — live sibling of aegis |
| `northstar/ns-wip-tests` | 10 weeks | **migrate** — touches paid contract work |
| `harmonic` | 3 months | **migrate** — dormant, not dead, and the cost is one `setup` run |
| `archived/arcprize` | 4 months | skip — archived |
| `archive/qrcode` | not a git repo | skip — a snapshot, not a project |
| `archive/handoff-test` | not a git repo | skip, but see below |

Reasoning: migration cost is a single `tagteam setup` invocation per project, so
the bar for including one is low — a dormant project that someone later resumes
should wake up on the current contract, not the 155-line one. The bar only
excludes directories that are snapshots rather than working repos, where updating
files nobody will run is pure motion.

**`archive/handoff-test` needs a separate decision, not a migration.** It is a
fixture built to exercise the handoff flow, and it is pinned to the 155-line
contract. Migrating it is wrong (it may exist precisely to test an older shape);
leaving it silently is also wrong (anyone reading it as a reference learns a
contract with no gatekeeper, no `AMEND`, no interjections). Recommend the impl
add a one-line note at its head recording which contract version it captures and
that it is not current. Reviewer: challenge this if the fixture is load-bearing
for a test that would break.

## Deferred — not blocking this phase

**Publish to a public marketplace?** Arbiter's answer as of 2026-08-29: *maybe*.
Deliberately deferred to release time. Nothing structural depends on it — the
marketplace manifest is written either way, and publishing is a separate act, not
a code path. tagteam is already public and Apache-2.0 on PyPI, so nothing would
be newly exposed. If it does happen, sequence it after migration step 2 (a full
cycle on aegis with the skill served from the plugin): a plugin that breaks on
install is worse for the project than no plugin.

## Done means

1. `/plugin marketplace add jblacketter/tagteam` followed by installing the
   plugin makes the `handoff` skill available in a project that has **no**
   `.claude/skills/handoff/` of its own.
2. `tagteam setup` run in a project where the plugin is installed **removes** the
   vendored `.claude/skills/handoff/` and says so; run where it is not installed,
   it vendors exactly as today.
3. `setup` never removes a skill the project owns — only the vendored handoff skill.
4. A full plan+impl cycle completes on aegis with the skill served from the
   plugin, and the reviewer's turn still carries both the contract and Phase 47's
   `PROJECT CONTEXT` / `CHANGE SURFACE` blocks.
5. `scripts/release.py` bumps `plugin.json` in the same commit as
   `pyproject.toml`, and the publish workflow fails a tag where they disagree.
6. A project with a tagteam CLI older than the plugin's declared minimum gets a
   warning naming both versions — and still takes its turn.
7. Full suite green.

**Dependencies:** Phase 47 (merged, PR #27) — its `PROJECT CONTEXT` block reads
the *project's* context file while this phase moves the *skill* out of the
project tree. They should not interact; criterion 4 is what proves it.

## Verification plan

- Unit: plugin detection true/false, `setup` remove path (removes only the
  vendored skill, never a project's own skills), version-skew warning fires below
  the declared minimum and stays silent at or above it.
- Integration: a full plan+impl cycle on aegis with the skill served from the
  plugin, confirming the reviewer's turn still carries the contract and Phase 47's
  context blocks.
- Manual: fresh install on a machine with no vendored copy anywhere.
