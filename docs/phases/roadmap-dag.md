# Phase 40: Roadmap as a DAG (3.4)

## Status
- [x] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete (release **3.4.0** via PR)

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
the line.

### Graph operations (`roadmap.py`)

- `dependency_graph(phases) -> {slug: [dep_slugs]}` + `validate_graph`
  (unknown/self/cycle → `RoadmapGraphError` listing every problem).
- `topological_queue(phases, start=None)`: **stable Kahn** — among ready
  candidates always pick the earliest in roadmap order; incomplete phases
  only; a `start` phase drops the phases before it in roadmap order *and*
  everything they transitively unblock stays if still needed (i.e. `start`
  means "begin here", the same meaning as today; phases before `start`
  that later phases depend on are assumed done by the human — reported as
  a note, not an error). Result for an edge-free roadmap == today's list.
- `ready_phases(phases)`: incomplete phases whose deps are all terminal;
  `blocked_phases` with the blocking deps.
- `roadmap check` (validate; exit 1 on problems), `roadmap graph`
  (text tree; `--mermaid` prints a `flowchart LR` block), `roadmap ready`
  (`slug\tstatus\tname` like `phases`; `--json`), `roadmap queue`
  (topological now), `roadmap phases` (adds a `depends_on` column when
  any phase has one; identical output otherwise).

### Full-roadmap mode

`_try_roadmap_advance` (watcher) picks the **next queue entry whose
dependencies are terminal at that moment** (re-parsing the roadmap, so a
phase completed in another worktree and merged unblocks it); if the next
queued phase is blocked (roadmap edited mid-run, or a dependency runs
elsewhere and is not merged yet) it does not start it — it records the
existing `roadmap.pause_reason` (`"blocked: <phase> depends on <deps>"`),
sets `turn: lead` with a `command` that says so, and the arbiter clears it
by editing the roadmap / merging, then `tagteam roadmap resume` (existing
mechanism from Phase 3? — no: `pause_reason` is cleared by the next
successful advance; this phase adds `tagteam roadmap resume` = re-run the
advance now). `--roadmap [phase]` start uses the topological queue.

### Worktrees (parallel phases)

```
tagteam roadmap worktree <phase>        # create ../<repo>-<phase>/ on branch phase-<slug> from the current HEAD,
                                        # seed it as a tagteam project, register it (hub), print the kickoff
tagteam roadmap worktrees [--json]      # list: path, branch, phase, that project's state (turn/status/round), merged?
tagteam roadmap worktree <phase> --remove   # git worktree remove + unregister (refuses if the branch is unmerged
                                            # unless --force)
```

Creation: refuses if the phase is not **ready** (deps incomplete) or not
in the roadmap; `git worktree add -b phase-<slug> <path> HEAD` (branch
name mirrors this repo's convention; `--from <ref>` overrides HEAD);
copies `tagteam.yaml` verbatim (agents, satellites); does **not** copy
runtime state (`handoff-state.json`, `.tagteam/`, `docs/handoffs/*` are
either gitignored or committed as-is — the worktree starts with whatever
the branch has); runs the equivalent of `tagteam setup` only for missing
framework files; registers the worktree path in `~/.tagteam/projects.json`
exactly like any other project (the registry stays a flat list of paths —
its format does not change, so the hub, `upgrade` and `rollback` see the
worktree as one more row) and records the worktree metadata (path, parent
project, phase, branch, created_at) in a sidecar
`~/.tagteam/worktrees.json` that only `roadmap worktree(s)` read; prints `cd <path> && tagteam session start` (or `tagteam serve`) and
`/handoff start <phase>`. Two worktrees are two projects: their watchers,
turn slots, gates and panels are independent; port leases keep servers
apart. **Merging is the human's** (`git merge`/PR per branch); after a
merge `roadmap worktrees` shows the branch as merged and `--remove` cleans
up. Handoff artefacts under `docs/handoffs/` are per phase and per branch,
so two phases' files never collide; `docs/roadmap.md` status edits can
conflict on merge — documented, human-resolved.

Out of this phase: running two phases inside ONE project root, worktree
auto-merge, and any cross-worktree state (the hub is the cross-project
view).

### CLI summary

```
tagteam roadmap check                     # validate the graph (exit 1 with every problem listed)
tagteam roadmap graph [--mermaid]         # print the DAG
tagteam roadmap ready [--json]            # phases that can start now
tagteam roadmap queue [phase]             # topological order (unchanged output for edge-free roadmaps)
tagteam roadmap phases                    # + depends_on column when present
tagteam roadmap resume                    # re-run the full-roadmap advance now (after unblocking)
tagteam roadmap worktree <phase> [--from REF] [--remove [--force]]
tagteam roadmap worktrees [--json]
```

## Scope

### In
- **A. `roadmap.py`** — `Depends on` parsing (+ `number`), reference
  resolution, `dependency_graph`/`validate_graph`/`topological_queue`/
  `ready_phases`/`blocked_phases`, `roadmap check|graph|ready|resume`,
  `queue` topological, `phases` column.
- **B. `watcher.py`** — `_try_roadmap_advance` chooses the next *ready*
  queue entry (re-parse), blocked → pause_reason + lead command; `--roadmap`
  start uses the topological queue.
- **C. `worktree.py`** (new) — create/list/remove; registers the path via
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
  multi-line, no line), validation (unknown/self/cycle/multiple problems),
  topological queue (edge-free == today's output on the existing fixtures
  AND on this repo's roadmap; diamond; start-phase semantics; stability
  ties), ready/blocked, `check`/`graph`/`ready` CLI; `tests/test_watcher.py`:
  advance skips to the next ready phase, blocked → pause_reason + no start,
  unblock by roadmap edit + `roadmap resume`; `tests/test_worktree.py`
  (new): create in a temp git repo (branch, files, registry entry, kickoff
  text), refuse not-ready / unknown / existing, list with state, remove
  merged / refuse unmerged / `--force`, two worktrees are independent
  projects (state files, `_resolve_project_root` from inside each);
  SKILL copies identical; flag-off byte-identical.

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
2. `Depends on` parsed in every accepted form; unknown/self/cycle reported
   by `roadmap check` with every problem listed; `queue`/`ready` refuse a
   broken graph.
3. `roadmap queue` is a stable topological order (diamond + tie tests);
   `roadmap ready` lists exactly the phases whose deps are terminal.
4. Full-roadmap mode never starts a blocked phase: it pauses with the
   reason and `roadmap resume` continues once unblocked.
5. `roadmap worktree <phase>` creates a working tagteam project on its own
   branch (kickoff printed, registered, visible to `roadmap worktrees` with
   its state); refuses not-ready/unknown/duplicate; `--remove` refuses an
   unmerged branch without `--force`; two worktrees run independent loops.
6. Both SKILL copies updated and identical; README + HTW document syntax,
   commands and the parallel model; this repo's roadmap carries the lines.
7. Release 3.4.0 via PR from `phase-40-roadmap-dag`.

## Open questions for the reviewer
1. Worktree location `../<repo>-<phase>` (sibling directory) vs
   `.worktrees/<phase>` inside the repo (would need gitignore and confuses
   `_resolve_project_root`'s walk-up) — I propose the sibling.
2. `roadmap resume` as a new subcommand vs re-using `tagteam resume` (which
   clears the pause marker for dispatch — a different thing) — I propose
   the separate `roadmap resume`.
3. Should `roadmap worktree` require the phase to be *ready* (my proposal)
   or only *not terminal*, leaving the judgment to the human?
