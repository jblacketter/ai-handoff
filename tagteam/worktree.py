"""
Phase worktrees (Phase 40): run independent roadmap phases in parallel, each
in its own git worktree that is its own tagteam project.

    tagteam roadmap worktree <phase> [--from REF] [--target BRANCH]
    tagteam roadmap worktree <phase> --remove [--force]
    tagteam roadmap worktrees [--json]

A worktree is just another tagteam project root: it has its own
`tagteam.yaml` (copied verbatim), `handoff-state.json`, watcher, turn slot,
gate and panel — the single-turn-slot invariant holds per project. Nothing
here schedules or merges; the human merges each phase branch.

Creation is guarded by the **publication boundary** (a worktree must start
from code that actually contains every dependency that made the phase ready):

- the parent must be clean (no modified/staged tracked files, no untracked
  non-ignored files) — otherwise refuse, listing the paths;
- `base` = resolved `--from REF`, else HEAD; readiness is evaluated on the
  roadmap AS OF `base` (`git show <base>:docs/roadmap.md`);
- the active run's `completed` list may additionally satisfy a dependency
  only when `base` contains HEAD (HEAD or a descendant of it).

Registry: the worktree path is registered like any other project
(`~/.tagteam/projects.json` stays a flat list of paths). Worktree metadata
lives in a sidecar `~/.tagteam/worktrees.json`:
`{path, parent, phase, branch, target, base, created_at}` — `target` is the
integration branch (the parent's checked-out branch at creation or
`--target`), `base` the sha the branch was created from. `merged?` is always
evaluated against the recorded `target` (`refs/heads/<target>`, falling back
to `origin/<target>`), never against whatever the parent has checked out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from tagteam import registry
from tagteam import roadmap as _rm


class WorktreeError(Exception):
    """A refusal or failure with a human-readable reason."""


def sidecar_path() -> Path:
    """`~/.tagteam/worktrees.json` (follows the registry directory so tests
    that redirect `registry.REGISTRY_DIR` redirect this too)."""
    return Path(registry.REGISTRY_DIR) / "worktrees.json"


def _read_sidecar() -> list[dict]:
    p = sidecar_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _write_sidecar(entries: list[dict]) -> None:
    p = sidecar_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def lookup(path: str | Path) -> dict | None:
    """Sidecar entry for a project path (resolved), or None."""
    want = str(Path(path).resolve())
    for e in _read_sidecar():
        if e.get("path") == want:
            return e
    return None


# ── git helpers ─────────────────────────────────────────────────


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def _repo_root(project_dir: Path) -> Path:
    proc = _git(project_dir, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        raise WorktreeError(f"{project_dir} is not inside a git repository")
    return Path(proc.stdout.strip())


def _current_branch(repo: Path) -> str | None:
    proc = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _rev_parse(repo: Path, ref: str) -> str:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if proc.returncode != 0:
        raise WorktreeError(f"unknown ref '{ref}'")
    return proc.stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return proc.returncode == 0


# Tagteam runtime files never carry an approved dependency; they do not make
# the parent "dirty" even when the project does not gitignore them.
_RUNTIME_PATHS = (".tagteam/", "handoff-state.json", ".handoff-state.tmp",
                  ".handoff-session.json", "handoff-diagnostics.jsonl")


def _is_runtime_path(rel: str) -> bool:
    rel = rel.strip().strip('"')
    if " -> " in rel:  # rename: "old -> new"
        rel = rel.split(" -> ", 1)[1]
    return any(rel == r or rel.startswith(r) for r in _RUNTIME_PATHS)


def _dirty_paths(repo: Path) -> list[str]:
    """Modified/staged tracked files + untracked non-ignored files, minus
    tagteam runtime paths."""
    proc = _git(repo, "status", "--porcelain", "--untracked-files=all")
    out = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        rel = line[3:]
        if _is_runtime_path(rel):
            continue
        out.append(rel)
    return out


def _branch_exists(repo: Path, branch: str) -> bool:
    proc = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return proc.returncode == 0


def _roadmap_text_at(repo: Path, ref: str, rel: str = "docs/roadmap.md") -> str | None:
    proc = _git(repo, "show", f"{ref}:{rel}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def _worktree_paths(repo: Path) -> list[Path]:
    proc = _git(repo, "worktree", "list", "--porcelain")
    return [Path(line[len("worktree "):]) for line in proc.stdout.splitlines()
            if line.startswith("worktree ")]


# ── creation ────────────────────────────────────────────────────


@dataclass
class WorktreeInfo:
    path: str
    parent: str
    phase: str
    branch: str
    target: str | None
    base: str
    created_at: str


def default_worktree_path(repo: Path, phase: str) -> Path:
    return repo.parent / f"{repo.name}-{phase}"


def _phases_from_text(text: str) -> list[_rm.RoadmapPhase]:
    """Parse + validate roadmap text (identity + edges) via a temp file."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tagteam-roadmap-") as d:
        tmp = Path(d) / "roadmap.md"
        tmp.write_text(text, encoding="utf-8")
        return _rm.check_graph(tmp)


def create_worktree(project_dir: str | Path, phase: str, *, from_ref: str | None = None,
                    target: str | None = None, path: str | Path | None = None,
                    out=None) -> WorktreeInfo:
    """Create `../<repo>-<phase>` on branch `phase-<slug>` from `base`, seed
    it as a tagteam project, register it, record the sidecar entry. Raises
    `WorktreeError` with the reason on any refusal."""
    out = out or sys.stdout
    project_dir = Path(project_dir).resolve()
    repo = _repo_root(project_dir)
    if repo != project_dir:
        raise WorktreeError(
            f"project root {project_dir} is not the git repository root {repo}; "
            "phase worktrees are created from the repository root")

    # 1. clean parent (publication boundary).
    dirty = _dirty_paths(repo)
    if dirty:
        shown = ", ".join(dirty[:8]) + (" …" if len(dirty) > 8 else "")
        raise WorktreeError(
            f"parent worktree is not clean ({len(dirty)} path(s): {shown}) — commit or "
            "stash first; an approved dependency may live in these changes")

    # 2. base + roadmap-at-base readiness.
    head = _rev_parse(repo, "HEAD")
    base = _rev_parse(repo, from_ref) if from_ref else head
    text = _roadmap_text_at(repo, base)
    if text is None:
        raise WorktreeError(f"docs/roadmap.md does not exist at {base[:12]}")
    phases = _phases_from_text(text)  # raises RoadmapGraphError (ValueError)
    by_slug = {p.slug: p for p in phases}
    ph = by_slug.get(phase)
    if ph is None:
        # accept `Phase N` / exact name too
        resolved = _rm._resolve_ref(phase, phases)
        if resolved is None:
            raise WorktreeError(f"phase '{phase}' not found in docs/roadmap.md at {base[:12]}")
        ph = resolved
    slug = ph.slug
    if _rm.is_terminal_status(ph.status):
        raise WorktreeError(f"phase '{slug}' is already complete ({ph.status})")

    completed = _rm.active_run_completed(str(project_dir))
    base_contains_head = base == head or _is_ancestor(repo, head, base)
    unmet_disk = _rm.unmet_dependencies(ph, by_slug, None)
    if unmet_disk:
        # The active run's completed list may vouch for a dependency only
        # when the base contains HEAD (where every approved change lives).
        if completed and base_contains_head:
            unmet = _rm.unmet_dependencies(ph, by_slug, completed)
        else:
            unmet = unmet_disk
        if unmet:
            reason = (f"phase '{slug}' is not ready at {base[:12]}: depends on "
                      f"{', '.join(unmet)} (not terminal in docs/roadmap.md at that commit")
            if completed and not base_contains_head:
                reason += (f"; --from {from_ref} does not contain HEAD {head[:12]}, so the "
                           f"active run's completed list ({', '.join(completed)}) cannot vouch")
            elif completed:
                reason += " and not in the active run's completed list"
            reason += ")"
            raise WorktreeError(reason)

    # 3. identity: no duplicate worktree/branch/path.
    branch = f"phase-{slug}"
    wt_path = Path(path).resolve() if path else default_worktree_path(repo, slug)
    for e in _read_sidecar():
        if e.get("phase") == slug and e.get("parent") == str(repo):
            raise WorktreeError(f"phase '{slug}' already has a worktree at {e.get('path')}")
    if wt_path.exists():
        raise WorktreeError(f"{wt_path} already exists")
    if wt_path in _worktree_paths(repo):
        raise WorktreeError(f"{wt_path} is already a worktree of {repo}")
    if _branch_exists(repo, branch):
        raise WorktreeError(
            f"branch '{branch}' already exists — remove it or check it out manually")

    # 4. create.
    target_branch = target or _current_branch(repo)
    _git(repo, "worktree", "add", "-b", branch, str(wt_path), base)
    try:
        _seed_project(repo, wt_path, out=out)
        registry.register_project(str(wt_path))
        info = WorktreeInfo(path=str(wt_path), parent=str(repo), phase=slug, branch=branch,
                            target=target_branch, base=base,
                            created_at=datetime.now(timezone.utc).isoformat())
        entries = _read_sidecar()
        entries.append(asdict(info))
        _write_sidecar(entries)
    except Exception:
        _git(repo, "worktree", "remove", "--force", str(wt_path), check=False)
        _git(repo, "branch", "-D", branch, check=False)
        raise
    return info


def _seed_project(repo: Path, wt_path: Path, *, out=None) -> None:
    """Copy `tagteam.yaml` verbatim when the branch does not carry it and add
    any missing framework files (the equivalent of `tagteam setup`, only for
    what is absent). Runtime state is never copied."""
    src_yaml = repo / "tagteam.yaml"
    dst_yaml = wt_path / "tagteam.yaml"
    if src_yaml.exists() and not dst_yaml.exists():
        shutil.copyfile(src_yaml, dst_yaml)
    try:
        from tagteam.setup import needs_setup, main as setup_main
        if needs_setup(str(wt_path)):
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                setup_main(str(wt_path))
    except Exception as e:  # setup is best-effort; the worktree still works
        print(f"  (framework files not seeded: {e})", file=out or sys.stdout)


def kickoff_text(info: WorktreeInfo) -> str:
    return (
        f"Worktree ready: {info.path}\n"
        f"  branch {info.branch} from {info.base[:12]} (target: {info.target or '?'})\n"
        f"  registered as its own tagteam project (hub row + sidecar)\n"
        f"\n"
        f"Next:\n"
        f"  cd {info.path}\n"
        f"  tagteam session start        # or: tagteam serve\n"
        f"  then tell the lead:  /handoff start {info.phase}\n"
        f"\n"
        f"When the phase is done: commit a terminal `- **Status:**` for it in docs/roadmap.md\n"
        f"on {info.branch}, merge into {info.target or 'the integration branch'}, then\n"
        f"  tagteam roadmap worktree {info.phase} --remove\n"
    )


# ── listing / removal ───────────────────────────────────────────


def merged_state(entry: dict) -> str:
    """'merged' | 'unmerged' | 'target missing' | 'branch missing' —
    evaluated against the recorded target in the parent repo."""
    parent = Path(entry.get("parent") or "")
    branch = entry.get("branch") or ""
    target = entry.get("target")
    if not parent.exists() or not branch:
        return "branch missing"
    if not _branch_exists(parent, branch):
        return "branch missing"
    if not target:
        return "target missing"
    for ref in (f"refs/heads/{target}", f"refs/remotes/origin/{target}"):
        proc = _git(parent, "rev-parse", "--verify", "--quiet", ref, check=False)
        if proc.returncode == 0:
            return "merged" if _is_ancestor(parent, branch, ref) else "unmerged"
    return "target missing"


def list_worktrees(parent: str | Path | None = None) -> list[dict]:
    """Sidecar entries (optionally for one parent) enriched with the
    worktree's own state (`phase/type/round/turn/status`) and `merged`."""
    want = str(Path(parent).resolve()) if parent else None
    rows = []
    for e in _read_sidecar():
        if want and e.get("parent") != want:
            continue
        row = dict(e)
        row["exists"] = Path(e.get("path", "")).is_dir()
        row["merged"] = merged_state(e)
        st = None
        try:
            from tagteam.state import read_state
            st = read_state(e["path"]) if row["exists"] else None
        except Exception:
            st = None
        row["state"] = ({k: st.get(k) for k in ("phase", "type", "round", "turn", "status", "result")}
                        if st else None)
        rows.append(row)
    return rows


def remove_worktree(project_dir: str | Path, phase: str, *, force: bool = False,
                    out=None) -> dict:
    """Remove the phase worktree + branch and unregister it. Refuses an
    unmerged branch (or a missing target) unless `force`."""
    project_dir = Path(project_dir).resolve()
    repo = _repo_root(project_dir)
    entries = _read_sidecar()
    entry = next((e for e in entries
                  if e.get("parent") == str(repo) and e.get("phase") == phase), None)
    if entry is None:
        raise WorktreeError(f"no worktree recorded for phase '{phase}' under {repo}")
    state = merged_state(entry)
    if state != "merged" and not force:
        raise WorktreeError(
            f"branch {entry.get('branch')} is {state} relative to target "
            f"'{entry.get('target')}' — merge it first, or pass --force to discard")
    wt_path = Path(entry["path"])
    if wt_path.exists():
        _git(repo, "worktree", "remove", "--force", str(wt_path))
    else:
        _git(repo, "worktree", "prune", check=False)
    if _branch_exists(repo, entry["branch"]):
        _git(repo, "branch", "-D" if force or state != "merged" else "-d", entry["branch"],
             check=False)
    registry.unregister_project(str(wt_path))
    _write_sidecar([e for e in entries if e is not entry])
    return {"path": str(wt_path), "branch": entry["branch"], "merged": state}


# ── CLI ─────────────────────────────────────────────────────────


def _project_root() -> str:
    from tagteam.state import _resolve_project_root
    return _resolve_project_root()


def worktree_command(args: list[str], out=None) -> int:
    out = out or sys.stdout
    if not args or args[0].startswith("-"):
        print("Usage: tagteam roadmap worktree <phase> [--from REF] [--target BRANCH] "
              "[--path DIR] | --remove [--force]", file=out)
        return 1
    phase = args[0]
    rest = args[1:]
    from_ref = target = path = None
    remove = force = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--from" and i + 1 < len(rest):
            from_ref = rest[i + 1]; i += 2; continue
        if a == "--target" and i + 1 < len(rest):
            target = rest[i + 1]; i += 2; continue
        if a == "--path" and i + 1 < len(rest):
            path = rest[i + 1]; i += 2; continue
        if a == "--remove":
            remove = True; i += 1; continue
        if a == "--force":
            force = True; i += 1; continue
        print(f"Unknown option: {a}", file=out)
        return 1
    root = _project_root()
    try:
        if remove:
            res = remove_worktree(root, phase, force=force, out=out)
            print(f"Removed worktree {res['path']} (branch {res['branch']}, was {res['merged']})",
                  file=out)
            return 0
        info = create_worktree(root, phase, from_ref=from_ref, target=target, path=path, out=out)
    except (WorktreeError, ValueError) as e:
        print(f"Error: {e}", file=out)
        return 1
    print(kickoff_text(info), file=out, end="")
    return 0


def worktrees_command(args: list[str], out=None) -> int:
    out = out or sys.stdout
    as_json = "--json" in args
    all_parents = "--all" in args
    root = None if all_parents else _project_root()
    try:
        rows = list_worktrees(root)
    except WorktreeError as e:
        print(f"Error: {e}", file=out)
        return 1
    if as_json:
        print(json.dumps(rows, indent=2), file=out)
        return 0
    if not rows:
        print("No phase worktrees" + ("" if all_parents else f" under {root}") + ".", file=out)
        return 0
    for r in rows:
        st = r.get("state") or {}
        state_txt = (f"{st.get('phase') or '-'} {st.get('type') or ''} r{st.get('round') or '-'} "
                     f"turn={st.get('turn') or '-'} {st.get('status') or '-'}"
                     if st else "no state")
        print(f"{r['phase']:24} {r['branch']:32} {r['merged']:14} {state_txt}  {r['path']}"
              + ("" if r.get("exists") else "  (missing)"), file=out)
    return 0
