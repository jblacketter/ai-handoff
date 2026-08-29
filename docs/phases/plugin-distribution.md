# Phase 48: Plugin distribution — one contract, installed once, instead of 58 forks

## Status
- [ ] Planning — plan cycle in progress. Round 1: direction approved, v1 scope ruled **B** (skill + SessionStart hook; agents deferred), migration set ratified; four required plan fixes, addressed in round 2 (marked *r2* below). Round 2: one blocking caller contract — `needs_setup()` — addressed in round 3 (marked *r3*).
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

> **If only Claude reads it, it is a plugin asset.
> If the tagteam CLI reads it, or a human edits it after seeding, the package keeps it.**

*(r2)* The contract is read by **both**: Claude discovers it as a skill, and the
CLI reads it into every headless prompt (`headless.py` `SKILL_RELPATH`). So the
contract is the one file that lives in both halves — the plugin tree delivers it
to Claude, the package delivers it to tagteam-composed prompts — and a test pins
the two copies byte-identical. The plugin is still the only thing Claude reads
*from*; it is not the only place the bytes exist. The original "if Claude reads
it → plugin" wording overstated the seam and is withdrawn.

### Moves to the plugin

- `.claude/skills/handoff/` — the contract. Versioned once, installed once.
- *(new)* `hooks/hooks.json` — a `SessionStart` hook that surfaces phase / type /
  round / turn when the project has a `handoff-state.json`. Today the cycle state
  is learned by running `/handoff`; the hook makes it ambient. *(r2)* The hook is
  a one-liner that delegates to a new CLI subcommand (see *The hook*); all logic
  and all tests are on the Python side.
- ~~`agents/`~~ — *(r2)* **deferred to a later phase** by round-1 ruling.
  `codex-brief` is the agent forbidden from making the cycle-writing call — a
  contract of its own that deserves its own review.

### Stays with the package

`templates/`, `checklists/`, `workflows.md`, seeded `roadmap.md` and
`decision_log.md` (per-project, substituted, human-edited); `data/panels/*.md`
(read by `panel.py`, never by Claude directly); *(r2)* the **packaged copy of the
contract** at `tagteam/data/.claude/skills/handoff/SKILL.md` (read by
`headless.py`, and still what `setup` vendors when the plugin is absent); and the
entire CLI — state machine, cycle store, watcher, gatekeeper, roadmap DAG,
registry, cockpit, hub — plus *(r2)* one new subcommand, `tagteam hook
session-start`, which is the hook's body and the skew warning's emitter.

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
│   └── handoff/SKILL.md     # what Claude discovers; byte-identical to the packaged copy (tested)
└── hooks/
    └── hooks.json           # SessionStart: `tagteam hook session-start` (see below)
```

*(r2)* `agents/` is out of v1. The tree lives at `plugin/` in this repo; the
repo-root `.claude-plugin/marketplace.json` points at it so `/plugin marketplace
add jblacketter/tagteam` resolves.

## The headless contract source *(r2 — round-1 fix 1)*

`tagteam/headless.py` today hard-codes `SKILL_RELPATH = .claude/skills/handoff/
SKILL.md` relative to the project, `HeadlessTurn.validate()` errors when it is
absent, and the file is read into every headless prompt. Removing the vendored
copy would therefore break every headless turn — criterion 4 as written was
unachievable and "CLI behavior is out of scope" was false. Both are corrected.

**Canonical source for tagteam-composed prompts:** the packaged copy,
`tagteam/data/.claude/skills/handoff/SKILL.md`, resolved with
`importlib.resources` — it already ships as package data and is exactly what
`setup` vendors, so the engine and the prompt can never disagree on the contract
version. Claude's plugin cache (`~/.claude/plugins/cache/…`) is **never** read
for prompt composition: it is Claude's discovery mechanism, not tagteam's, and
its layout is not a contract tagteam owns.

**Resolution order** (`headless.resolve_skill_path(project_root, explicit) ->
(Path, source)`):

1. an explicit `skill_path` (existing `HeadlessTurn` kwarg / `--skill-path`) —
   error if missing, exactly as today;
2. the project-local `.claude/skills/handoff/SKILL.md` **if it exists** — a
   project that deliberately keeps or customizes a vendored copy keeps winning,
   so nothing changes for the 43 un-migrated projects;
3. the packaged copy — the new normal for a migrated project.

`validate()` only errors when *none* resolves (a broken install). The prompt
header changes from the fixed `=== HANDOFF CONTRACT (.claude/skills/handoff/
SKILL.md) ===` to `=== HANDOFF CONTRACT (<source>) ===` where `<source>` is
`project` or `packaged`, so a turn log shows which path it took. That is the
only prompt change, and it is outside the contract text.

**Single source of truth in the repo:** `plugin/skills/handoff/SKILL.md` and
`tagteam/data/.claude/skills/handoff/SKILL.md` are the same bytes, enforced by
`tests/test_plugin.py::test_plugin_skill_matches_packaged_copy` and by
`scripts/release.py`, which refuses to bump when they differ. Two files, one
content, one guard — a symlink was rejected because setuptools package data
does not carry symlinks reliably across sdist/wheel.

**Tests in scope:** `tests/test_headless.py` — resolution picks project when
present, packaged when absent, explicit always; `validate()` passes with no
project-local skill; header names the source. Existing tests that create a
project-local skill fixture keep passing unchanged (rule 2).

## Plugin detection *(r2 — round-1 fix 2)*

Presence of a directory under the plugin cache proves nothing — a cached plugin
can be uninstalled, project-scoped to another path, or disabled. Detection reads
Claude Code's own records and **fails closed**: any doubt → "not installed" →
`setup` vendors exactly as today.

`setup.plugin_status(project_root) -> PluginStatus(installed: bool, reason:
str, install_path: Path | None)` decides **installed-and-enabled** for *this*
project as follows:

1. Config dir: `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`.
2. `<config>/plugins/installed_plugins.json` must exist, parse, and have
   `version == 2` (the schema observed on the arbiter's machine; any other
   version → not installed, reason names it).
3. Key `tagteam@tagteam` (`<plugin name>@<marketplace name>`, both declared in
   the manifests this repo ships). Its value is a list of install records; a
   record **applies** to this project iff `scope == "user"`, or `scope ==
   "project"` and `realpath(projectPath) == realpath(project_root)`. Any other
   scope (or a missing/unknown one) does not apply.
4. Enabled state: `enabledPlugins[key]` is consulted in `<config>/settings.json`,
   `<project>/.claude/settings.json` and `<project>/.claude/settings.local.json`.
   An explicit `false` in **any** of them → disabled. The plugin must be
   explicitly `true` in at least one, or absent from all with an applicable
   install record — whichever Claude Code's default is, the test fixture pins
   the rule tagteam applies and the reason string says which file decided.
5. The applying record's `installPath` must exist and contain
   `skills/handoff/SKILL.md`; otherwise the install is broken → not installed.

Malformed JSON, missing files, unexpected types, unreadable paths: all → not
installed with a one-line reason. `tagteam setup` prints the verdict and reason
before acting (`plugin: installed (user scope, enabled by ~/.claude/settings.json)`
/ `plugin: not installed (…)`). `tagteam setup --no-plugin` forces the vendoring
path regardless. There is **no** flag that forces the remove path.

## Ownership-safe removal *(r2 — round-1 fix 3)*

`setup` may only delete what `setup` wrote. Pathname is not provenance;
**content** is. The repo's git history holds every version of the vendored
contract ever shipped — 26 distinct blobs of
`tagteam/data/.claude/skills/handoff/SKILL.md` as of 3.9.0 — and each has a
stable sha256.

- `scripts/contract_hashes.py` walks `git log --follow` over that path and writes
  `tagteam/data/vendored_contract_hashes.json` (`{sha256: first_version_tag}`),
  shipped as package data. `scripts/release.py` regenerates it on every bump;
  a test asserts the current packaged contract's hash is present.
- The remove path fires only when **all** of the following hold for
  `<project>/.claude/skills/handoff/`:
  - the directory contains exactly one entry, `SKILL.md` (no extra files, no
    subdirectories, no symlinks — anything else is evidence of project ownership);
  - `sha256(SKILL.md)` is in the known set.
- Otherwise `setup` **keeps the directory untouched**, prints
  `kept .claude/skills/handoff/: <reason>` (`SKILL.md modified — not a tagteam
  vendored version` / `extra files present: …`), and does not vendor over it
  either. The project stays on rule 2 of the headless resolution order (its
  local copy wins), which is the correct outcome for a project that customized
  its contract.
- A removed directory is reported with the contract version it matched
  (`removed vendored handoff skill (3.8.2 contract) — served by the plugin`), and
  the registry entry records `contract: plugin`.

**Unchanged and worth stating:** when the plugin is *not* detected, `setup` still
does today's `rmtree` + `copytree` over `.claude/skills/handoff/`. That path
already overwrites customizations and always has; the migration depends on it
(the six stale projects must be overwritten). Tightening it is a separate
question and out of scope here — this phase makes the *new* path safe, it does
not audit the old one.

**Tests (`tests/test_setup.py`):** known-hash SKILL.md alone → removed, message
names the version; modified SKILL.md → kept, message says modified; known
SKILL.md + extra file → kept, message lists the file; empty directory → left
alone; plugin not installed → vendored as today; `--no-plugin` with plugin
installed → vendored.

## Setup completeness *(r3 — round-2 fix)*

`setup.needs_setup()` defines "setup is complete" as: local
`.claude/skills/handoff/SKILL.md` exists, `templates/*.md` exists,
`docs/checklists/*.md` exists. A migrated project intentionally lacks the first,
so with no change every caller misbehaves. Audit of callers (all three, 3.9.0):

| Caller | Today | After migration, unfixed |
|---|---|---|
| `setup.run_setup()` line 60 | early-return "already set up" | never early-returns; reruns and re-removes every time (idempotent, but noisy) |
| `session.py:659` (`session start --launch`) | runs setup once when needed | reruns setup on **every** launch |
| `worktree.py:318/326` (`_seed_project`) | runs setup, then re-checks; still-true → `WorktreeError`, worktree rolled back | setup removes/omits the skill (user-scoped plugin applies to any path) → post-check still true → **every worktree creation fails** |

**Semantics.** `needs_setup(project_dir)` returns False iff templates and
checklists are present **and** the skill requirement is met by **either**:

- (a) a project-local `.claude/skills/handoff/SKILL.md`, or
- (b) `plugin_status(project_dir).installed` is True.

Fail-closed is preserved by construction: (b) is the same predicate the remove
path uses, so a plugin that is uncertain, disabled, malformed, or scoped to a
different path does not satisfy the requirement — the local skill is still
required, and `setup` will vendor it. The predicate and the remove path can
therefore never disagree: whatever `setup` decides to leave on disk is what
`needs_setup` accepts. Templates and checklists remain required regardless.

`needs_setup` grows an optional `plugin: PluginStatus | None = None` parameter so
a caller that already computed the status (`run_setup`) passes it in instead of
re-reading Claude Code's registry; callers that pass nothing get it computed.
`plugin_status` reads two small JSON files and stats one path — cheap enough for
every launch.

**Worktrees specifically.** `_seed_project` checks `needs_setup` against the
*worktree* path. A user-scoped plugin applies to any path → (b) satisfied → no
skill is vendored into the worktree and no rollback fires. A **project-scoped**
record's `projectPath` is the main checkout, which does not equal the worktree's
realpath → (b) not satisfied → `setup` vendors the skill into the worktree, as
today → post-check False → no rollback. Both outcomes are correct; neither
stalls. `_seed_project` itself does not change unless the fixture work below
requires it.

**Files in scope (added):** `tagteam/setup.py` (`needs_setup`, `run_setup`
guard); `tagteam/session.py` and `tagteam/worktree.py` only if a signature or
fixture change forces it (the call sites should compile unchanged); their tests.

**Tests.** `tests/test_quickstart.py`: `needs_setup` with no local skill and
plugin installed+enabled (user scope) → False; installed but disabled → True;
malformed registry → True; project scope matching the dir → False; project
scope for another path → True; templates/checklists missing with plugin
installed → True (the skill is not the only requirement). `tests/test_worktree.py`:
regression — user-scoped plugin present, main repo has no local skill, worktree
creation succeeds and the worktree has no `.claude/skills/handoff/`; project-scoped
plugin → worktree creation succeeds and the worktree *does* get a vendored skill.
`tests/test_session.py`: `--launch` on a migrated project does not invoke setup.
Plugin status is injected in tests via the `CLAUDE_CONFIG_DIR` env var pointing at
a tmp fixture (a real `installed_plugins.json` + `settings.json`), never by
monkeypatching internals, so the tests exercise the same reader the CLI uses.

## The hook and the skew warning *(r2 — round-1 fix 4)*

The version-skew warning has to be emitted by *something*, and the round-1 plan
said both the contract text and the CLI were untouchable. Resolution: the
component is the **CLI**. The contract text stays byte-for-byte; the hook is
inert JSON.

`plugin/hooks/hooks.json`:

```json
{ "hooks": { "SessionStart": [ { "hooks": [ { "type": "command",
  "command": "command -v tagteam >/dev/null 2>&1 && tagteam hook session-start --plugin-root \"$CLAUDE_PLUGIN_ROOT\" || true" } ] } ] } }
```

`tagteam hook session-start [--plugin-root DIR]` (new subcommand, `hook.py`):

- If the cwd has no `tagteam.yaml` **or** no `handoff-state.json`: print
  nothing, exit 0. A non-tagteam project with the plugin installed sees nothing.
- If `handoff-state.json` is unreadable or malformed (bad JSON, missing keys,
  wrong types): print nothing, exit 0. Never fail a session start.
- Otherwise print one line, the status-banner shape the contract already
  defines: `tagteam: phase <p> | type <t> | round <n> | turn <who> | status <s>`.
- Skew: read `<plugin-root>/.claude-plugin/plugin.json`; if it declares
  `tagteam.minVersion` (custom key, ignored by Claude Code) greater than
  `tagteam.__version__`, append `warning: plugin <pv> expects tagteam >= <min>,
  installed <iv> — run: uv tool upgrade tagteam`. Missing/malformed plugin.json,
  missing flag, unparsable versions → no warning, still exit 0.
- Every exit is 0; every failure is silent; nothing is written.

The `command -v … || true` guard means a machine without `tagteam` on PATH is
also silent. The falsifier from round 1 — "B carries no more migration risk than
A iff the hook is inert without `handoff-state.json`" — becomes a test rather
than an argument.

**Tests (`tests/test_hook.py`, subprocess against `python -m tagteam`):** no
`tagteam.yaml` → exit 0, empty stdout; `tagteam.yaml` but no state → exit 0,
empty; malformed state (`{`, `[]`, `{"phase": 1}`) → exit 0, empty; valid state →
one banner line; `minVersion` above installed → warning line, exit 0;
`minVersion` at/below → no warning; no `--plugin-root` / bad plugin.json → no
warning, exit 0. Plus a test that `hooks.json` parses and its command string
contains exactly the subcommand invocation (so a hand edit to the JSON cannot
silently drift from the CLI).

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
2. **Declared minimum.** `plugin.json` records a minimum tagteam version
   (`tagteam.minVersion`); *(r2)* the `SessionStart` hook — via `tagteam hook
   session-start`, never the skill text — compares it to the installed
   `tagteam.__version__` and **warns** rather than failing.
3. **Independent versioning.** Rejected — it is the current bug with the arrow
   reversed.

**Chosen: 1 + 2.** Lockstep makes disagreement rare; the runtime check makes it
loud when it happens anyway. *(r3)* Someone who installs the plugin **without**
the package gets no message at all: the hook is deliberately silent when
`tagteam` is not on PATH (`command -v … || true`), because a session start must
never fail or nag on tagteam's account. The skew warning covers the
package-too-old case only; "plugin but no package" surfaces the first time the
agent runs a `tagteam` command the contract tells it to, exactly as today.

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
   *(r2)* The two resolution paths are different code and are verified
   separately, and the impl submission records the exact aegis phase and cycle
   used as the canary:
   - **interactive discovery** — with `.claude/skills/handoff/` removed from
     aegis, a fresh Claude session lists `handoff` under plugin skills
     (`/skills`, or the skill fires on `/handoff`) and the SessionStart banner
     line appears;
   - **headless composition** — a `tagteam watch --mode headless` turn on aegis
     whose prompt header reads `=== HANDOFF CONTRACT (packaged) ===` (the turn
     log carries the header), with the `PROJECT CONTEXT` and `CHANGE SURFACE`
     blocks present.
3. Flip `setup` to remove-instead-of-write when the plugin is detected, printing
   what it removed and why.
4. Sweep the registry. The six stale projects get the current contract the moment
   they install — that is the payoff.

## Scope

**In** *(r2 — scope B)*: a new plugin tree `plugin/` in this repo with
`.claude-plugin/plugin.json`, `skills/handoff/SKILL.md` and `hooks/hooks.json`;
a repo-root `.claude-plugin/marketplace.json`; the packaged contract copy kept
and pinned byte-identical to the plugin copy by test; `headless.py` contract
resolution (explicit → project-local → packaged) and the `(<source>)` prompt
header; `setup.py` plugin detection (`plugin_status`) and the hash-gated remove
path; `scripts/contract_hashes.py` + shipped `vendored_contract_hashes.json`;
new `tagteam hook session-start` subcommand (banner + skew warning); *(r3)*
plugin-aware `needs_setup()` (local skill **or** installed-and-enabled plugin;
templates + checklists still required) with its three callers audited
(`setup.run_setup`, `session start --launch`, `worktree._seed_project`) and
regression tests for each;
`scripts/release.py` bumping `plugin.json` and regenerating the hash file, and
refusing on a plugin/packaged contract mismatch; the publish workflow's version
guard extended to `plugin.json`; tests for all of the above; README install
section.

**Out:** agent definitions (deferred); the state machine, cycle store, watcher,
gatekeeper, panel, roadmap DAG, cockpit and hub; the vendoring path's existing
overwrite behavior when the plugin is absent; `tagteam.yaml` schema. **No change
to the contract's text** — this phase moves it, it does not edit it (a text
change would confound the migration). The CLI *does* change, in exactly the
three places named above (headless resolution, setup, the hook subcommand);
the round-1 claim that it would not was wrong.

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

*(r2)* Ratified in round 1, subject to confirming no byte-exact assertion
consumes the fixture. Checked: `grep -rn handoff-test tests/` in this repo is
empty, and `archive/handoff-test` is not a git repo, so nothing in tagteam's
suite reads it. The impl will grep that directory itself for anything asserting
on `SKILL.md` before adding the note, and record the result in the submission.

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
3. `setup` never removes a skill the project owns: removal requires the
   directory to hold only a `SKILL.md` whose sha256 is a known vendored version;
   anything else is kept, reported, and not vendored over. *(r2)*
4. A full plan+impl cycle completes on aegis with the skill served from the
   plugin — interactive turns discover it as a plugin skill, headless turns
   compose the prompt from the **packaged** copy (header says so) — and the
   reviewer's turn still carries both the contract and Phase 47's `PROJECT
   CONTEXT` / `CHANGE SURFACE` blocks. The submission names the aegis phase and
   cycle used. *(r2)*
5. `scripts/release.py` bumps `plugin.json` in the same commit as
   `pyproject.toml`, refuses when the plugin and packaged contract copies
   differ, and the publish workflow fails a tag where any of the three versions
   disagree.
6. `tagteam hook session-start` prints the banner line when there is a valid
   cycle, warns (naming both versions) when the CLI is below the plugin's
   declared minimum, and is silent with exit 0 everywhere else — no
   `tagteam.yaml`, no state, malformed state, no plugin root. A session start
   never fails because of tagteam. *(r2)*
7. `headless.py` composes a prompt with no project-local skill present, and a
   project-local copy still wins when it is present. *(r2)*
8. `needs_setup()` is False for a migrated project (plugin installed-and-enabled,
   no local skill, templates + checklists present) and True for every uncertain
   plugin state; `session start --launch` does not rerun setup on a migrated
   project; `tagteam worktree` creation on a migrated project with a user-scoped
   plugin succeeds without rollback, and with a project-scoped plugin succeeds
   by vendoring into the worktree. *(r3)*
9. Full suite green.

**Dependencies:** Phase 47 (merged, PR #27) — its `PROJECT CONTEXT` block reads
the *project's* context file while this phase moves the *skill* out of the
project tree. They should not interact; criterion 4 is what proves it.

## Verification plan

- Unit *(r2)*: `plugin_status` matrix — missing/malformed registry, wrong
  schema version, user scope, project scope matching/not matching, disabled in
  any settings file, broken `installPath`; `setup` remove path — known hash
  removed, modified kept, extra file kept, `--no-plugin`; `hook session-start`
  matrix (subprocess) — silent/exit-0 cases and the banner + skew lines;
  headless resolution order and header; plugin/packaged contract byte-equality;
  current contract hash present in the shipped hash file; `hooks.json` command
  string pinned to the subcommand; *(r3)* `needs_setup` plugin matrix,
  `_seed_project` no-rollback regression (user scope) and vendor-into-worktree
  case (project scope), `--launch` no-rerun.
- Integration: a full plan+impl cycle on aegis with the skill served from the
  plugin — both resolution paths checked as described in migration step 2, the
  phase/cycle recorded in the submission.
- Manual: fresh install on a machine with no vendored copy anywhere.
