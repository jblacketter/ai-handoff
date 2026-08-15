"""
Deterministic fingerprints used by the headless retry gate (Phase 32).

`repo_fingerprint(root)`:
  * None                — not a git repository.
  * UNSUPPORTED         — any git failure / unmerged index / parse or
                          recursion problem: the caller must fail closed
                          (never retry).
  * hex digest          — content-sensitive over every tracked and
                          non-ignored untracked path, recursively through
                          EVERY gitlink (registered submodule, uninitialised
                          submodule, embedded repo, `.gitmodules`-declared
                          path alike). At each level the digest gets
                          HEAD ‖ write-tree(real index) ‖ write-tree(temp
                          index after `git add -A`), and each 160000 entry
                          from `git ls-files --stage -z` (NUL-framed) is
                          recursed into with cwd = that directory. Records
                          are framed as length-prefixed (level, kind, value)
                          triples inside the hash, so the (level, path)
                          binding is part of the digest — never inferred
                          from newline splitting.

`handoff_fingerprint(root, phase, type)`: state seq + target cycle entry
count + cycle (state, ready_for, round). Any handoff transition changes it.

Only `.gitignore`d paths are outside a successful repo fingerprint.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

UNSUPPORTED = "UNSUPPORTED"
_GIT_TIMEOUT = 120
_MAX_DEPTH = 32


class FingerprintError(Exception):
    pass


def _git(cwd: Path, *args: str, env: dict | None = None,
         ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           timeout=_GIT_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FingerprintError(f"git {' '.join(args)} failed to run: {e}") from e
    if r.returncode not in ok_codes:
        raise FingerprintError(
            f"git {' '.join(args)} exited {r.returncode}: "
            f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r


def probe_repo(root: str | Path) -> str:
    """Classify `root`: "repo" (inside a git work tree), "not-repo" (git
    confirmed there is no repository here), or "unknown" (git missing,
    timed out, dubious ownership, corrupt repo, any other failure). Only a
    *confirmed* non-repository is allowed to take the non-git path; anything
    else must fail closed (UNSUPPORTED)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=str(root), capture_output=True, timeout=_GIT_TIMEOUT)
    except Exception:
        return "unknown"
    if r.returncode == 0 and r.stdout.strip() == b"true":
        return "repo"
    err = r.stderr.decode("utf-8", "replace").lower()
    if r.returncode == 128 and "not a git repository" in err:
        return "not-repo"
    return "unknown"


def is_git_repo(root: str | Path) -> bool:
    return probe_repo(root) == "repo"


def _head(cwd: Path) -> str:
    r = _git(cwd, "rev-parse", "--verify", "--quiet", "HEAD", ok_codes=(0, 1))
    if r.returncode == 1:
        return "unborn"
    return r.stdout.decode().strip()


def _level(cwd: Path, level: str, records: list[tuple[str, str, str]],
           depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise FingerprintError("gitlink recursion too deep")
    head = _head(cwd)
    index_tree = _git(cwd, "write-tree").stdout.decode().strip()
    fd, tmp = tempfile.mkstemp(prefix="tagteam-fp-", suffix=".index")
    os.close(fd)
    os.unlink(tmp)  # git wants to create it
    env = dict(os.environ, GIT_INDEX_FILE=tmp)
    try:
        if head == "unborn":
            _git(cwd, "read-tree", "--empty", env=env)
        else:
            _git(cwd, "read-tree", "HEAD", env=env)
        _git(cwd, "add", "-A", env=env)
        tmp_tree = _git(cwd, "write-tree", env=env).stdout.decode().strip()
        listing = _git(cwd, "ls-files", "--stage", "-z", env=env).stdout
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    records.append((level, "HEAD", head))
    records.append((level, "index-tree", index_tree))
    records.append((level, "tmp-tree", tmp_tree))
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        try:
            meta, path_b = raw.split(b"\t", 1)
            mode, sha, _stage = meta.decode().split()
        except ValueError as e:
            raise FingerprintError(f"unparseable ls-files entry: {raw[:60]!r}") from e
        if mode != "160000":
            continue
        path = path_b.decode("utf-8", "surrogateescape")
        sub_level = f"{level}/{path}"
        records.append((level, "gitlink", path))
        sub = cwd / path
        if (sub / ".git").exists():
            _level(sub, sub_level, records, depth + 1)
        else:
            # No working tree (uninitialised submodule): nothing on disk to
            # edit; the recorded SHA is the whole content.
            records.append((sub_level, "gitlink-sha", sha))


def repo_fingerprint(root: str | Path) -> str | None:
    root = Path(root)
    kind = probe_repo(root)
    if kind == "not-repo":
        return None
    if kind != "repo":
        return UNSUPPORTED   # probe failure is not "non-git" — fail closed
    records: list[tuple[str, str, str]] = []
    try:
        _level(root, "", records, 0)
    except FingerprintError:
        return UNSUPPORTED
    except Exception:
        return UNSUPPORTED
    h = hashlib.sha1()
    for level, kind, value in records:
        for part in (level, kind, value):
            b = part.encode("utf-8", "surrogateescape")
            h.update(len(b).to_bytes(4, "big"))
            h.update(b)
        h.update(b"\0")
    return h.hexdigest()


def handoff_fingerprint(root: str | Path, phase: str | None,
                        cycle_type: str | None) -> str:
    """seq + target cycle entry count + cycle (state, ready_for, round)."""
    from tagteam.state import read_state
    from tagteam.cycle import read_rounds, read_status
    root = str(root)
    st = read_state(root) or {}
    entries = 0
    status: dict = {}
    if phase and cycle_type:
        try:
            entries = len(read_rounds(phase, cycle_type, root) or [])
        except Exception:
            entries = -1
        try:
            status = read_status(phase, cycle_type, root) or {}
        except Exception:
            status = {"error": True}
    return (f"seq={st.get('seq')}|entries={entries}|state={status.get('state')}"
            f"|ready_for={status.get('ready_for')}|round={status.get('round')}")
