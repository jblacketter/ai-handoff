"""Phase 48: plugin distribution — detection and contract provenance.

The handoff contract (``.claude/skills/handoff/SKILL.md``) can be served to
Claude by an installed Claude Code plugin instead of a copy vendored into every
project. Two questions follow, both answered here, both **fail-closed**:

* :func:`plugin_status` — is the tagteam plugin installed *and enabled* for
  this project, according to Claude Code itself (``claude plugin list
  --json``, the effective state after managed policy)? Any doubt → not
  installed, with a one-line reason.
* :func:`vendored_skill_provenance` — is a project-local handoff skill
  directory exactly a tagteam-vendored contract (and nothing else)? Only such
  a directory may be removed by ``tagteam setup``; pathname is not provenance,
  content is.

Nothing here writes anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PLUGIN_NAME = "tagteam"
MARKETPLACE_NAME = "tagteam"
PLUGIN_KEY = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
INSTALLED_SCHEMA_VERSION = 2
SKILL_IN_PLUGIN = Path("skills") / "handoff" / "SKILL.md"
HASHES_FILENAME = "vendored_contract_hashes.json"


@dataclass(frozen=True)
class PluginStatus:
    installed: bool
    reason: str
    install_path: Path | None = None
    scope: str | None = None

    def __str__(self) -> str:
        return ("installed" if self.installed else "not installed") + f" ({self.reason})"


CLAUDE_BIN_ENV = "TAGTEAM_CLAUDE_BIN"   # override the `claude` executable ("" = none)
PLUGIN_LIST_TIMEOUT_S = 30
APPLICABLE_SCOPES = ("user", "project", "local")


def _load_json(text: str):
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, f"invalid JSON ({e.__class__.__name__})"


def _same_path(a: str, b: str | Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, TypeError, ValueError):
        return False


def claude_executable() -> str | None:
    """The `claude` CLI to ask, or None. ``TAGTEAM_CLAUDE_BIN`` overrides PATH
    lookup; set it to an empty string to mean "there is no CLI"."""
    if CLAUDE_BIN_ENV in os.environ:
        v = os.environ[CLAUDE_BIN_ENV].strip()
        return v or None
    return shutil.which("claude")


def list_plugins(project_root: str | Path) -> tuple[list | None, str]:
    """``claude plugin list --json`` run with ``project_root`` as cwd (project
    and local scopes are resolved against the cwd). Returns (records, "") or
    (None, reason). The CLI reports *effective* state — managed policy and
    settings precedence are its job, not ours."""
    exe = claude_executable()
    if not exe:
        return None, "claude CLI not found"
    try:
        r = subprocess.run([exe, "plugin", "list", "--json"], cwd=str(project_root),
                           capture_output=True, text=True, timeout=PLUGIN_LIST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"claude plugin list timed out after {PLUGIN_LIST_TIMEOUT_S}s"
    except (OSError, ValueError) as e:
        return None, f"claude plugin list could not run ({e.__class__.__name__})"
    if r.returncode != 0:
        return None, f"claude plugin list exited {r.returncode}"
    data, err = _load_json(r.stdout)
    if err:
        return None, f"claude plugin list: {err}"
    if not isinstance(data, list):
        return None, "claude plugin list: not a JSON array"
    return data, ""


def plugin_status(project_root: str | Path) -> PluginStatus:
    """Is the tagteam plugin installed **and enabled** for ``project_root``?

    Asks Claude Code itself (``claude plugin list --json``), which reports the
    effective state after managed policy and settings precedence. A record
    applies when its ``id`` is ``tagteam@tagteam`` and its scope is ``user``,
    or ``project`` / ``local`` with a ``projectPath`` equal to the project.
    Installed means: exactly one consistent answer, ``enabled`` is ``true``,
    and ``installPath`` holds the handoff skill. Missing CLI, timeout,
    non-zero exit, malformed output, an unsupported scope, an ambiguous
    answer, or anything else → *not installed*, with the reason.
    """
    root = Path(project_root)
    records, err = list_plugins(root)
    if records is None:
        return PluginStatus(False, err)
    applicable = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("id") != PLUGIN_KEY:
            continue
        scope = rec.get("scope")
        if scope == "user":
            applicable.append(rec)
        elif scope in ("project", "local"):
            pp = rec.get("projectPath")
            if isinstance(pp, str) and _same_path(pp, root):
                applicable.append(rec)
        elif scope not in APPLICABLE_SCOPES:
            return PluginStatus(False, f"{PLUGIN_KEY}: unsupported scope {scope!r}")
    if not applicable:
        return PluginStatus(False, f"{PLUGIN_KEY} not installed for {root} "
                                   f"(no user-scope record, no project/local record for this path)")
    enabled_values = {rec.get("enabled") for rec in applicable}
    if len(enabled_values) > 1:
        return PluginStatus(False, f"{PLUGIN_KEY}: ambiguous — {len(applicable)} applicable "
                                   f"records disagree on enabled")
    (enabled,) = enabled_values
    scope = applicable[0].get("scope")
    if enabled is not True:
        return PluginStatus(False, f"installed ({scope} scope) but not enabled "
                                   f"(effective enabled={enabled!r})", scope=scope)
    install_path = applicable[0].get("installPath")
    if not isinstance(install_path, str) or not install_path:
        return PluginStatus(False, "install record has no installPath", scope=scope)
    ip = Path(install_path)
    if not (ip / SKILL_IN_PLUGIN).is_file():
        return PluginStatus(False, f"{ip / SKILL_IN_PLUGIN} missing — broken install", scope=scope)
    return PluginStatus(True, f"{scope} scope, enabled — per claude plugin list", install_path=ip,
                        scope=scope)


# ---------------------------------------------------------------------------
# Contract provenance
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes_path() -> Path:
    return Path(__file__).parent / "data" / HASHES_FILENAME


def known_contract_hashes() -> dict[str, str]:
    """``{sha256: first-version-tag}`` for every contract tagteam ever vendored.
    Generated from git history by ``scripts/contract_hashes.py``; empty on any
    read failure (which makes every removal decision "keep")."""
    try:
        data, err = _load_json(hashes_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}
    if err or not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.get("hashes", {}).items() if isinstance(k, str)}


@dataclass(frozen=True)
class Provenance:
    removable: bool
    reason: str
    version: str | None = None


def vendored_skill_provenance(skill_dir: str | Path,
                              known: dict[str, str] | None = None) -> Provenance:
    """May ``setup`` delete ``skill_dir``? Only if it holds exactly one entry,
    a regular ``SKILL.md`` whose sha256 is a known vendored contract."""
    d = Path(skill_dir)
    if not d.exists():
        return Provenance(False, "absent")
    if not d.is_dir() or d.is_symlink():
        return Provenance(False, "not a plain directory")
    try:
        entries = sorted(p.name for p in d.iterdir())
    except OSError as e:
        return Provenance(False, f"unreadable ({e.__class__.__name__})")
    if not entries:
        return Provenance(False, "empty directory")
    extra = [n for n in entries if n != "SKILL.md"]
    if extra:
        return Provenance(False, f"extra files present: {', '.join(extra)}")
    skill = d / "SKILL.md"
    if skill.is_symlink() or not skill.is_file():
        return Provenance(False, "SKILL.md is not a regular file")
    known = known_contract_hashes() if known is None else known
    try:
        digest = sha256_of(skill)
    except OSError as e:
        return Provenance(False, f"SKILL.md unreadable ({e.__class__.__name__})")
    version = known.get(digest)
    if version is None:
        return Provenance(False, "SKILL.md modified — not a tagteam vendored version")
    return Provenance(True, f"{version} contract", version=version)
