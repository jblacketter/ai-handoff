#!/usr/bin/env python3
"""Bump the release version in one transactional step (Phase 41).

    python scripts/release.py X.Y.Z [--date YYYY-MM-DD] [--root DIR] [--dry-run] [--no-lock]

Edits `pyproject.toml` (`version = "…"`), `CITATION.cff` (`version:` and
`date-released:`), `plugin/.claude-plugin/plugin.json` (`version` and
`tagteam.minVersion`, Phase 48), regenerates
`tagteam/data/vendored_contract_hashes.json` from git history, and refreshes
`uv.lock` (`uv lock`), then prints the commit/tag recipe. Refuses to bump when
`plugin/skills/handoff/SKILL.md` differs from the packaged contract. Only
`contract_hashes.py` runs git (read-only).

Transactional: the original bytes of all three files are snapshotted first;
on ANY failure (a `uv lock` that exits non-zero, times out or is missing; a
write error; an unexpected exception) every file whose bytes differ from its
snapshot is restored, the failure is printed and the exit code is 2 — the
tree is byte-identical to before. `--dry-run` writes nothing and runs
nothing. Exit 1 = usage/validation error (nothing written).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# horizontal whitespace only — `\s*$` would swallow the newline
_PYPROJECT_VERSION = re.compile(r'^(version[ \t]*=[ \t]*")([^"]+)(")', re.M)
_CFF_VERSION = re.compile(r"^(version:[ \t]*)(\S+)", re.M)
_CFF_DATE = re.compile(r"^(date-released:[ \t]*)(\S+)", re.M)
PLUGIN_JSON = "plugin/.claude-plugin/plugin.json"
PLUGIN_SKILL = "plugin/skills/handoff/SKILL.md"
PACKAGED_SKILL = "tagteam/data/.claude/skills/handoff/SKILL.md"
HASHES_JSON = "tagteam/data/vendored_contract_hashes.json"
FILES = ("pyproject.toml", "CITATION.cff", "uv.lock", PLUGIN_JSON, HASHES_JSON)
UV_TIMEOUT_S = 600


class ReleaseError(Exception):
    pass


def _parse_semver(s: str) -> tuple[int, int, int]:
    m = _SEMVER.match(s or "")
    if not m:
        raise ReleaseError(f"not a plain semver X.Y.Z: {s!r}")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _write_atomic(path: Path, data: bytes) -> None:
    """Temp file in the same directory + os.replace (never a partial file)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _run_uv_lock(root: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise ReleaseError("`uv` not found on PATH (use --no-lock to skip refreshing uv.lock)")
    try:
        r = subprocess.run([uv, "lock"], cwd=str(root), capture_output=True, text=True, timeout=UV_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ReleaseError(f"`uv lock` timed out after {UV_TIMEOUT_S}s")
    if r.returncode != 0:
        raise ReleaseError(f"`uv lock` failed (exit {r.returncode}):\n{(r.stdout + r.stderr).strip()}")


def _regen_hashes(root: Path) -> None:
    script = root / "scripts" / "contract_hashes.py"
    if not script.is_file():
        return   # a root without the generator (test fixtures) has nothing to regenerate
    r = subprocess.run([sys.executable, str(script), "--root", str(root)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ReleaseError(f"contract_hashes.py failed (exit {r.returncode}):\n"
                           f"{(r.stdout + r.stderr).strip()}")


def plan(root: Path, new_version: str, date: str) -> dict:
    """Validate and compute the new contents. Raises ReleaseError; writes nothing."""
    new_t = _parse_semver(new_version)
    py = root / "pyproject.toml"
    cff = root / "CITATION.cff"
    if not py.is_file():
        raise ReleaseError(f"{py} not found")
    if not cff.is_file():
        raise ReleaseError(f"{cff} not found")
    py_text = py.read_text(encoding="utf-8")
    m = _PYPROJECT_VERSION.search(py_text)
    if not m:
        raise ReleaseError('pyproject.toml: no `version = "…"` line found')
    cur = m.group(2)
    if _parse_semver(cur) >= new_t:
        raise ReleaseError(f"new version {new_version} is not greater than the current {cur}")
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        raise ReleaseError(f"--date must be YYYY-MM-DD, got {date!r}")
    cff_text = cff.read_text(encoding="utf-8")
    if not _CFF_VERSION.search(cff_text) or not _CFF_DATE.search(cff_text):
        raise ReleaseError("CITATION.cff: `version:` / `date-released:` lines not found")
    new_py = _PYPROJECT_VERSION.sub(lambda mm: f"{mm.group(1)}{new_version}{mm.group(3)}", py_text, count=1)
    new_cff = _CFF_VERSION.sub(lambda mm: f"{mm.group(1)}{new_version}", cff_text, count=1)
    new_cff = _CFF_DATE.sub(lambda mm: f"{mm.group(1)}{date}", new_cff, count=1)
    # Phase 48: plugin manifest in lockstep; plugin and packaged contract identical
    pj = root / PLUGIN_JSON
    if not pj.is_file():
        raise ReleaseError(f"{pj} not found")
    try:
        manifest = json.loads(pj.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ReleaseError(f"{PLUGIN_JSON}: invalid JSON ({e})")
    if not isinstance(manifest, dict) or "version" not in manifest:
        raise ReleaseError(f"{PLUGIN_JSON}: no top-level version")
    ps, ks = root / PLUGIN_SKILL, root / PACKAGED_SKILL
    if not ps.is_file() or not ks.is_file():
        raise ReleaseError(f"{PLUGIN_SKILL} and {PACKAGED_SKILL} must both exist")
    if ps.read_bytes() != ks.read_bytes():
        raise ReleaseError(f"{PLUGIN_SKILL} differs from {PACKAGED_SKILL} — the plugin and "
                           f"packaged contract must be byte-identical (copy one over the other)")
    manifest["version"] = new_version
    manifest.setdefault("tagteam", {})
    if not isinstance(manifest["tagteam"], dict):
        raise ReleaseError(f"{PLUGIN_JSON}: 'tagteam' must be an object")
    manifest["tagteam"]["minVersion"] = new_version
    new_plugin = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return {"current": cur, "new": new_version, "date": date,
            "pyproject": new_py.encode("utf-8"), "citation": new_cff.encode("utf-8"),
            "plugin": new_plugin}


def apply(root: Path, p: dict, *, lock: bool, out) -> None:
    """Write pyproject → uv lock → CITATION; restore everything on failure."""
    paths = {name: root / name for name in FILES}
    snap = {name: (path.read_bytes() if path.exists() else None) for name, path in paths.items()}

    def _restore() -> list[str]:
        restored = []
        for name, path in paths.items():
            before = snap[name]
            now = path.read_bytes() if path.exists() else None
            if now == before:
                continue
            if before is None:
                path.unlink(missing_ok=True)
            else:
                _write_atomic(path, before)
            restored.append(name)
        return restored

    try:
        _write_atomic(paths["pyproject.toml"], p["pyproject"])
        if lock:
            _run_uv_lock(root)
        _write_atomic(paths["CITATION.cff"], p["citation"])
        _write_atomic(paths[PLUGIN_JSON], p["plugin"])
        _regen_hashes(root)
    except BaseException as e:
        restored = _restore()
        msg = str(e) if isinstance(e, ReleaseError) else f"{type(e).__name__}: {e}"
        raise ReleaseError(f"{msg}\nrolled back: {', '.join(restored) or 'nothing had changed'}") from e


def main(argv: list[str] | None = None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=out)
        return 0 if argv else 1
    version = None
    date = _dt.date.today().isoformat()
    root = Path(__file__).resolve().parents[1]
    dry = False
    lock = True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--date" and i + 1 < len(argv):
            date = argv[i + 1]; i += 2
        elif a == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1]).resolve(); i += 2
        elif a == "--dry-run":
            dry = True; i += 1
        elif a == "--no-lock":
            lock = False; i += 1
        elif a.startswith("-"):
            print(f"unknown option: {a}", file=err); return 1
        elif version is None:
            version = a; i += 1
        else:
            print(f"unexpected argument: {a}", file=err); return 1
    if version is None:
        print("usage: release.py X.Y.Z [--date YYYY-MM-DD] [--root DIR] [--dry-run] [--no-lock]", file=err)
        return 1
    try:
        p = plan(root, version, date)
    except ReleaseError as e:
        print(f"release: {e}", file=err)
        return 1
    print(f"release: {p['current']} → {p['new']} (date-released {p['date']}) in {root}", file=out)
    print(f"  pyproject.toml   version = \"{p['new']}\"", file=out)
    print(f"  CITATION.cff     version: {p['new']} / date-released: {p['date']}", file=out)
    print(f"  uv.lock          {'uv lock' if lock else 'skipped (--no-lock)'}", file=out)
    print(f"  {PLUGIN_JSON}  version + tagteam.minVersion = {p['new']}", file=out)
    print(f"  {HASHES_JSON}  regenerated from git history", file=out)
    if dry:
        print("dry run — nothing written.", file=out)
        return 0
    try:
        apply(root, p, lock=lock, out=out)
    except ReleaseError as e:
        print(f"release FAILED: {e}", file=err)
        return 2
    print("done. next:", file=out)
    print(f"  git add {' '.join(FILES)} && git commit -m \"release: {p['new']}\"", file=out)
    print(f"  git tag v{p['new']} && git push && git push --tags", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
