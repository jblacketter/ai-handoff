# Phase 40: Roadmap as a DAG (3.4)

## Status
- [x] Planning
- [x] In Review (round 2: one dependency-satisfaction rule (roadmap disposition ∪ active run's `completed`) + cross-worktree publication contract; `queue [start]` pulls in nonterminal ancestors; dynamic advance over a stale queue; graph identity validation; worktree merge target recorded at creation; round 3: advance scans the whole selected queue (blocked entries are never lost behind `current_index`); worktree creation publication boundary (clean parent, base must contain the completion HEAD, readiness evaluated on the roadmap at `base`); empty heading = identity error)
- [x] Approved (round 3)
- [x] Implementation
- [x] Implementation Review (approved round 2)
- [x] Complete — PR #18 merged, released 3.4.0 (2026-08-16)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** `docs/roadmap.md` phases gain an optional dependency line —
`- **Depends on:** <phase>, <phase>` — and tagteam treats the roadmap as a
**directed acyclic graph** instead of a flat list: the queue is a stable
topological order, a phase is *ready* only when every dependency is
terminal, `tagteam roadmap check|graph|ready` make the graph visible and
validated, full-roadmap mode never starts a blocked phase, and
`tagteam roadmap worktree <phase>` gives each independent ready phase its
**own git worktree** (a separate tagteam project root with its own state,
watcher and branch) so independent phases can run **in parallel** — one
loop per worktree, which keeps the single-turn-slot invariant intact per
project. This is the 3.0-proposal §4 candidate "Roadmap as DAG
(`depends_on`, parallel phases in worktrees)".

**Why:** the roadmap is already a graph in practice (this repo's Phase 39
depended on 38; the cockpit-hardening phase depends on nothing and could
run beside an engine phase), but `tagteam roadmap queue` is document order
and full-roadmap mode advances linearly, so a human either serialises
everything or hand-manages branches. Making the dependencies explicit is
cheap (one line per phase), gives the arbiter a "what can start now" answer
(`roadmap ready`), and lets two agents pairs work two phases at once
without inventing a second orchestration model: a worktree is just another
tagteam project (the hub already lists projects; the watcher, gate, panel,
briefer all run per project root).

**Depends on:** Phase 3 (full-roadmap mode / `roadmap queue`), Phase 35
(hub + registry: worktrees register as projects), Phases 38–39 (per-project
satellites keep working unchanged in a worktree). **Size:** medium. Branch
`phase-40-roadmap-dag`, PR at the end. **Release:** 3.4.0.

**Compatibility rule.** A roadmap with no `Depends on:` lines is a DAG with
no edges: `roadmap queue`/`phases`, full-roadmap advance and the state
shape are **byte-identical** to 3.3.0 (tests pin the existing behaviour on
the current fixtures and on this repo's roadmap). Worktree commands are new
and do nothing unless invoked. No cockpit work (parked).

## Design

### Roadmap syntax

Inside a phase block (after `### Phase N: Name`), one optional line:

```
- **Depends on:** phase-38-slug, Phase 39, cross-project-hub
```

Accepted references, resolved against the roadmap's phase list: a slug
(`_slugify(name)`), `Phase N` / `phase-N` (heading number), or an exact
name. Multiple lines or comma/`;` separated lists merge. Self-references,
unknown references and cycles are **errors** reported by `roadmap check`
(and by `queue`/`ready` — a broken graph never yields a partial queue). A
dependency on a *terminal* phase (✅ Complete / Absorbed / Deferred /
Cancelled — `is_terminal_status`) is satisfied; on a non-terminal one it
blocks. Dependencies point at *phases*, never at cycle types (a phase is
"done" when its impl is approved / roadmap status terminal).

`RoadmapPhase` gains `number: int | None`, `depends_on: list[str]`
(resolved slugs) — additive; `parse_roadmap` unchanged for phases without
the line (it stays lenient — identity/graph validation lives in the graph
functions below, so `roadmap phases` keeps listing whatever the file has).

**Graph identity (validated first).** Before any slug-keyed structure is
built, `validate_identities(roadmap_text)` collects *every* problem:
duplicate heading numbers, duplicate normalized slugs (two different names
that `_slugify` to the same slug included), and empty names. Because
`parse_roadmap`'s heading regex (`(.+)`) silently skips a bare
`### Phase N:` heading (and a whitespace-only name strips to `""`), the
identity pass runs its own lenient heading scan
(`^###\s+Phase\s+(\d+):\s*(.*)$`) over the raw text so both forms are
reported as `phase N: empty name` — tested for both spellings. Any problem →
`RoadmapGraphError` (all problems listed); `roadmap check`, `queue`,
`ready`, `graph`, worktree creation and the full-roadmap advance all refuse
a roadmap with identity errors (the advance pauses with
`pause_reason = "roadmap invalid: …"` — see below). This applies to
edge-free roadmaps too: a duplicate slug was already a latent bug (a
duplicated queue entry and an ambiguous `state.phase`), so the
byte-identical guarantee is stated for **well-formed** edge-free roadmaps
(unique numbers and slugs — this repo's roadmap and every existing fixture
qualify; a test asserts that).

### One dependency-satisfaction rule

There is exactly one predicate, used by `roadmap ready`, the full-roadmap
advance/`resume` and `roadmap worktree`:

```
satisfied(dep, phases, completed) :=
      is_terminal_status(phase[dep].status)        # persisted roadmap disposition on disk
   or normalize_phase_key(dep) in completed        # approved in the ACTIVE full-roadmap run
```

`completed` is `state["roadmap"]["completed"]` (normalized) of the project
whose roadmap is being evaluated — the watcher passes the fresh state; the
CLI reads `handoff-state.json` of the project root when `run_mode ==
"full-roadmap"` and prints a note (`(+ N phase(s) completed in the active
run)`); an explicit `--roadmap-only` ignores it. `ready(p) := p incomplete
and not in completed and every dep satisfied`. So an impl APPROVE that only
appends to `completed` (today's behaviour, unchanged) **does** unblock its
dependents in the same run without a manual roadmap edit (tested).

**Cross-worktree publication contract.** Another project (a worktree, or
the parent) has no access to this run's `completed`; it observes a phase's
completion **only through `docs/roadmap.md` in its own checked-out tree**.
So a phase running in a worktree is published by (1) committing a terminal
`- **Status:**` disposition for that phase on the phase branch (the lead's
close-out, exactly as this repo does; the kickoff text says so) and (2)
merging that branch into the recorded integration branch — and a sibling
worktree sees it only after it merges/rebases that branch. Until then the
phase is *incomplete* from every other project's point of view and its
dependents stay blocked there — by design (never assume work that isn't in
the tree). Tested: temp repo, worktree branch commits a terminal status,
merge into the parent → parent's `roadmap ready` lists the dependent;
before the merge it does not.

### Graph operations (`roadmap.py`)

- `dependency_graph(phases) -> {slug: [dep_slugs]}` + `validate_graph`
  (unknown/self/cycle → `RoadmapGraphError` listing every problem).
- `topological_queue(phases, start=None, completed=())`: **stable Kahn**
  — among candidates whose deps are all in the emitted set or satisfied,
  always pick the earliest in roadmap order; incomplete, not-completed
  phases only. With `start`: the queue covers the set
  `{start} ∪ {incomplete phases after start in roadmap order}` **plus every
  nonterminal, not-completed dependency ancestor of those** (transitively),
  in topological order — an ancestor before `start` that is still needed is
  pulled in *ahead of* the phase that needs it and reported on stderr
  (`note: pulled in N dependency ancestor(s): a, b`); an incomplete phase
  before `start` that nothing after it needs is dropped, as today. A blocked
  edge is therefore never silently bypassed: full-roadmap mode starting at
  `start` first runs its unmet ancestors. For an edge-free roadmap the
  ancestor set is empty and the result is today's suffix, byte-identical.
- `ready_phases(phases, completed=())` / `blocked_phases(...)` (with the
  blocking deps) — both use `satisfied` above.
- `roadmap check` (validate; exit 1 on problems), `roadmap graph`
  (text tree; `--mermaid` prints a `flowchart LR` block), `roadmap ready`
  (`slug\tstatus\tname` like `phases`; `--json`), `roadmap queue`
  (topological now), `roadmap phases` (adds a `depends_on` column when
  any phase has one; identical output otherwise).

### Full-roadmap mode

The stored `roadmap.queue` is a *plan*, not a promise: another worktree can
finish and merge a queued phase, the roadmap can be edited mid-run. So the
impl-approved advance (`_try_roadmap_advance`, and `roadmap resume`, which
calls the same function) is **dynamic** on every call:

1. re-parse `docs/roadmap.md`; identity/graph errors → pause with
   `pause_reason = "roadmap invalid: <problems>"` (never start anything);
2. compute `remaining` = every entry of the **whole selected `queue`** (not
   the suffix after `current_index`) that is not terminal in the roadmap,
   not in `completed` and not the phase just approved — `current_index`
   only *describes* the current selection, it never defines the remaining
   set, so an entry that was blocked and jumped over earlier is reconsidered
   on every later advance; skipped entries are logged, never started, and
   not appended to `completed` (`completed` stays "approved in this run");
3. select the first entry of `remaining` in queue order (the queue is
   topological, so queue order is a valid priority) whose deps are all
   `satisfied`; set `current_index` to **that entry's index** (may be lower
   than before), `phase`, `type: plan`, `round 1`, `turn: lead`, `command:
   /handoff start <phase>`, `pause_reason: None`;
4. if `remaining` is empty → `status: done, result: roadmap-complete`
   (roadmap-complete is defined over the selected queue, not the suffix);
5. if `remaining` is non-empty but every entry is blocked → do not start:
   `pause_reason = "blocked: <phase> depends on <unmet deps>[; …]"`, `turn:
   lead`, `command` = the same text (`tagteam roadmap resume` once
   unblocked), `current_index` unchanged. The arbiter clears it by merging
   the dependency's branch / editing the roadmap, then `tagteam roadmap
   resume` re-runs steps 1–5 (it is a no-op unless the state is paused or
   `status: done / result: approved`, and it goes through
   `update_state(expected_seq=…)` like the watcher). It is a *different*
   thing from `tagteam resume` (which clears the dispatch pause marker) and
   stays a separate subcommand.

`--roadmap [phase]` start (`tagteam state set --roadmap-queue …` in the
SKILL, `tagteam roadmap queue [phase]`) uses the topological queue with the
ancestor rule above. Tests pin: same-run approval unblocks a dependent
without a roadmap edit; the diamond `A → {B, C} → D` where `B` completes
externally (terminal on disk, merged) while the watcher is on `A` — the
advance skips `B`, selects `C` with `current_index = 2`, and after `C`
starts `D` (B terminal on disk, C in `completed`); the **mixed case** `A`
approved, `B` blocked (its dep runs elsewhere), `C` ready → run `C`
(`current_index = 2`); `B`'s dep becomes satisfied; after `C`'s approval
the advance selects `B` (`current_index = 1`, lower than before); after
`B` → `roadmap-complete` without re-running `A` or `C`; all-blocked →
pause + `resume` after unblocking; last incomplete entry →
`roadmap-complete` even when the tail of the queue was completed
externally.

### Worktrees (parallel phases)

```
tagteam roadmap worktree <phase>        # create ../<repo>-<phase>/ on branch phase-<slug> from the current HEAD,
                                        # seed it as a tagteam project, register it (hub), print the kickoff
tagteam roadmap worktrees [--json]      # list: path, branch, phase, that project's state (turn/status/round), merged?
tagteam roadmap worktree <phase> --remove   # git worktree remove + unregister (refuses if the branch is unmerged
                                            # unless --force)
```

Creation: refuses if the roadmap has identity/graph errors, if the phase
is not in the roadmap, if the path/branch/phase already has a worktree, or
if the **publication boundary** below is not met; `git worktree add -b phase-<slug> <path> HEAD` (branch
name mirrors this repo's convention; `--from <ref>` overrides HEAD);
copies `tagteam.yaml` verbatim (agents, satellites); does **not** copy
runtime state (`handoff-state.json`, `.tagteam/`, `docs/handoffs/*` are
either gitignored or committed as-is — the worktree starts with whatever
the branch has); runs the equivalent of `tagteam setup` only for missing
framework files; registers the worktree path in `~/.tagteam/projects.json`
exactly like any other project (the registry stays a flat list of paths —
its format does not change, so the hub, `upgrade` and `rollback` see the
worktree as one more row) and records the worktree metadata in a sidecar
`~/.tagteam/worktrees.json` that only `roadmap worktree(s)` read: `path`,
`parent` (project root), `phase`, `branch`, **`target`** (the integration
branch: the parent's checked-out branch at creation, or `--target BRANCH`),
**`base`** (the resolved sha the branch was created from — HEAD or
`--from REF`), `created_at`; prints `cd <path> && tagteam session start` (or `tagteam serve`) and
`/handoff start <phase>`. Two worktrees are two projects: their watchers,
turn slots, gates and panels are independent; port leases keep servers
apart. **Publication boundary for creation** (a worktree must start from code
that actually contains every dependency that made the phase ready):

- the parent must be **clean** — no modified/staged tracked files and no
  untracked, non-ignored files (`git status --porcelain
  --untracked-files=all` empty); otherwise refuse and list the paths
  ("commit or stash first — an approved dependency may live in these
  changes"). No override flag: the fix is a commit.
- `base` = resolved `--from REF`, else HEAD. Readiness is evaluated on the
  **roadmap as of `base`** (`git show <base>:docs/roadmap.md`), so a
  dependency's terminal disposition counts only if that commit carries it.
- the active run's `completed` may additionally satisfy a dependency
  **only if `base` contains the completion HEAD** — i.e. `base` is HEAD or
  a descendant of it (`git merge-base --is-ancestor HEAD <base>`); a clean
  HEAD is where every approved change of this run lives. An older or
  divergent `--from` with a `completed`-satisfied dependency is refused
  with the reason (`--from <ref> does not contain HEAD <sha>, and <dep>
  was approved in the active run but is not terminal in the roadmap at
  <ref>`).
- documented in HTW/README: approved phases must be committed (and, to be
  visible to other worktrees, published per the contract above) before
  spawning dependents. `base` (the sha) is recorded in the sidecar.

Tests: dirty parent (modified tracked file; untracked file) → refuse;
dep satisfied only via `completed` + `--from` at an older commit → refuse,
`--from` a descendant of HEAD → ok, no `--from` → ok; dep terminal at HEAD
but not in the roadmap at an older `--from` → refuse; dep terminal at both
→ ok.

**Merging is the human's** (`git merge`/PR per branch). `merged?` is
evaluated against the **recorded `target`**, never against whatever branch
the parent happens to have checked out later: `git merge-base
--is-ancestor <branch> <target>` run in the parent repo (`refs/heads/<target>`,
falling back to `origin/<target>` when the local branch is gone, e.g. after
a squash-merge the human deleted locally); if neither exists the row shows
`target missing` and counts as *unmerged*. `--remove` refuses an unmerged
branch (or a missing target) without `--force`; with `--force` it removes
the worktree and the branch and unregisters. Tested with the parent
switched to another branch between creation and list/remove. Handoff artefacts under `docs/handoffs/` are per phase and per branch,
so two phases' files never collide; `docs/roadmap.md` status edits can
conflict on merge — documented, human-resolved.

Out of this phase: running two phases inside ONE project root, worktree
auto-merge, and any cross-worktree state (the hub is the cross-project
view).

### CLI summary

```
tagteam roadmap check                     # validate the graph (exit 1 with every problem listed)
tagteam roadmap graph [--mermaid]         # print the DAG
tagteam roadmap ready [--json] [--roadmap-only]   # phases that can start now (roadmap ∪ active run's completed)
tagteam roadmap queue [phase]             # topological order (unchanged output for edge-free roadmaps)
tagteam roadmap phases                    # + depends_on column when present
tagteam roadmap resume                    # re-run the full-roadmap advance now (after unblocking)
tagteam roadmap worktree <phase> [--from REF] [--target BRANCH] [--remove [--force]]
tagteam roadmap worktrees [--json]
```

## Scope

### In
- **A. `roadmap.py`** — `Depends on` parsing (+ `number`), reference
  resolution, `validate_identities`, `dependency_graph`/`validate_graph`/
  `satisfied`/`topological_queue` (ancestor rule)/`ready_phases`/
  `blocked_phases`, `roadmap check|graph|ready [--roadmap-only]|resume`,
  `queue` topological, `phases` column.
- **B. `watcher.py`** — `_try_roadmap_advance` becomes the dynamic
  five-step advance above (whole-queue `remaining`, select first ready,
  `current_index` = selected index, roadmap-complete over the selected
  queue, blocked → pause);
  `roadmap resume` calls it; `--roadmap` start uses the topological queue.
- **C. `worktree.py`** (new) — create (publication boundary: clean parent,
  roadmap-at-base readiness, `completed` only when base ⊇ HEAD)/list/
  remove; registers the path via
  the existing `register_project` (registry format unchanged) + sidecar
  `~/.tagteam/worktrees.json` for worktree metadata; hub `--list` label
  `<name> (worktree: <phase>)` when the sidecar knows the path (read side
  only — no cockpit UI change beyond the existing project row).
- **D. Docs** — SKILL (both copies): `/handoff start --roadmap` uses the
  DAG queue; a one-line "Depends on" mention in the roadmap conventions;
  README "Roadmap as a DAG" subsection + CLI ref; HTW `#roadmap-dag`
  section (syntax, graph commands, full-roadmap behaviour, worktrees);
  `docs/roadmap.md` of this repo gains `Depends on` lines where true
  (39→38, cockpit-hardening → none) as dogfood; sample roadmap in
  `tagteam/data/roadmap.md` shows the line; `pyproject.toml`/`CITATION.cff`
  → 3.4.0.
- **E. Tests** — `tests/test_roadmap.py`: parsing (all reference forms,
  multi-line, no line), identity validation (duplicate numbers, duplicate
  slugs from different names, empty heading name in both spellings, all
  problems listed, refused by check/queue/ready/graph), graph validation (unknown/self/cycle/multiple
  problems), topological queue (well-formed edge-free == today's output on
  the existing fixtures AND on this repo's roadmap; diamond; ties;
  `queue [start]` pulls in nonterminal ancestors ahead of `start`, drops
  unneeded predecessors, edge-free suffix unchanged), `satisfied`/ready/
  blocked with and without `completed`, `check`/`graph`/`ready` CLI (incl.
  `--roadmap-only` and the active-run note); `tests/test_watcher.py`:
  same-run approval unblocks a dependent (no roadmap edit), diamond with an
  externally-completed middle entry (skip + `current_index` = selected),
  the mixed case (A approved, B blocked, C ready → C; B unblocked → B with
  a lower `current_index`; then roadmap-complete without re-running A/C),
  all-blocked → pause_reason + no start, unblock + `roadmap resume`,
  externally-completed tail → roadmap-complete, invalid roadmap → pause,
  `--roadmap [start]` with an unmet ancestor; `tests/test_worktree.py`
  (new): create in a temp git repo (branch, base sha, target, files,
  registry entry + sidecar, kickoff text), refuse invalid graph / not-ready
  / unknown / duplicate, publication boundary (dirty parent; `--from`
  older/divergent with a `completed`-satisfied dep; roadmap-at-base
  readiness; descendant `--from` ok), list with state, merged-vs-target with the parent
  switched to another branch, remove merged / refuse unmerged and missing
  target / `--force`, cross-worktree publication (terminal status committed
  + merged → parent's `ready` unblocks; not before), two worktrees are
  independent projects (state files, `_resolve_project_root` from inside
  each); SKILL copies identical; flag-off byte-identical.

### Out (deliberately)
- Two phases in one project root; auto-merge; conflict resolution.
- Cockpit/hub UI beyond the existing project row (parked).
- Cross-worktree scheduling (a scheduler that starts worktrees itself).

## Technical approach — notes for the reviewer
- **Stable topological order** so a DAG-free roadmap is byte-identical and
  a DAG one is predictable (roadmap order breaks ties).
- **A worktree is a project** — no new orchestration model; the invariants
  Phase 31–39 rely on (one state file, one turn slot, one watcher per
  project root) hold per worktree because `_resolve_project_root` finds the
  worktree's own `tagteam.yaml`.
- **Blocked never starts** in full-roadmap mode; the pause reason is the
  existing field, `roadmap resume` is the explicit re-check.
- **Refuse-by-default worktree removal** (unmerged branch) protects work.

## Files
```
tagteam/roadmap.py            depends_on parsing, graph ops, check|graph|ready|resume
tagteam/worktree.py           new: worktree create/list/remove + registry integration
tagteam/watcher.py            _try_roadmap_advance ready-aware; --roadmap start
tagteam/registry.py           worktree sidecar helpers (read/write ~/.tagteam/worktrees.json); tagteam/hub.py --list label
tagteam/data/roadmap.md       sample gains a Depends on line
tagteam/data/.claude/skills/handoff/SKILL.md, .claude/skills/handoff/SKILL.md
tests/test_roadmap.py, tests/test_watcher.py, tests/test_worktree.py (new)
README.md, docs/how-tagteam-works.md, docs/roadmap.md, docs/phases/roadmap-dag.md
pyproject.toml, CITATION.cff → 3.4.0
```

## Success criteria
1. Edge-free roadmaps: `roadmap queue|phases`, full-roadmap advance and
   state are byte-identical to 3.3.0 (existing tests + this repo's roadmap).
2. `Depends on` parsed in every accepted form; identity errors (duplicate
   numbers/slugs) and graph errors (unknown/self/cycle) reported by
   `roadmap check` with every problem listed; `queue`/`ready`/`graph`/
   worktree creation/advance refuse a broken roadmap.
3. `roadmap queue` is a stable topological order (diamond + tie tests);
   `queue [start]` pulls in nonterminal ancestors and never bypasses an
   edge; `roadmap ready` lists exactly the phases whose deps are satisfied
   under the one rule (roadmap disposition ∪ active run's `completed`).
4. Full-roadmap advance is dynamic over the whole selected queue: skips
   terminal/completed entries, selects the first ready one (index =
   selected, may move backwards), never loses a blocked entry, never starts
   a blocked phase (pause reason), `roadmap resume` continues once unblocked,
   roadmap-complete when nothing incomplete remains; same-run approval
   unblocks dependents without a roadmap edit.
5. `roadmap worktree <phase>` creates a working tagteam project on its own
   branch (kickoff printed, registered, sidecar with target/base, visible to
   `roadmap worktrees` with its state); refuses invalid/not-ready/unknown/
   duplicate and enforces the publication boundary (clean parent, base ⊇
   HEAD when readiness relies on `completed`, roadmap-at-base); `merged?` and `--remove` use the recorded target (refuse
   unmerged or missing target without `--force`); the publication contract
   (terminal status committed + merged) is what unblocks other projects; two
   worktrees run independent loops.
6. Both SKILL copies updated and identical; README + HTW document syntax,
   commands and the parallel model; this repo's roadmap carries the lines.
7. Release 3.4.0 via PR from `phase-40-roadmap-dag`.

## Decisions (round 1 open questions, agreed by the reviewer)
1. Worktree location: sibling directory `../<repo>-<phase>` (not
   `.worktrees/` inside the repo, which would need gitignore and confuse
   `_resolve_project_root`'s walk-up).
2. `roadmap resume` is a separate subcommand from `tagteam resume` (which
   clears the dispatch pause marker — a different thing).
3. `roadmap worktree` requires the phase to be *ready* under the
   satisfaction rule (not merely non-terminal).
