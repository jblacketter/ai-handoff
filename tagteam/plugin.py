"""Phase 48: plugin distribution — detection and contract provenance.

The handoff contract (``.claude/skills/handoff/SKILL.md``) can be served to
Claude by an installed Claude Code plugin instead of a copy vendored into every
project. Two questions follow, both answered here, both **fail-closed**:

* :func:`plugin_status` — is the tagteam plugin installed *and enabled* for
  this project, according to Claude Code's own records? Any doubt → not
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


def claude_config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def _load_json(path: Path):
    """Parse ``path`` as JSON; return (data, None) or (None, reason)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path} missing"
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, f"{path} unreadable ({type(e).__name__})"


def _same_path(a: str, b: str | Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, TypeError, ValueError):
        return False


def _enabled_state(project_root: Path, config_dir: Path) -> tuple[bool | None, str]:
    """Consult ``enabledPlugins[PLUGIN_KEY]`` in the user, project and local
    settings. An explicit ``false`` anywhere wins (disabled). Returns
    (True, deciding-file) / (False, deciding-file) / (None, "absent")."""
    files = [config_dir / "settings.json",
             project_root / ".claude" / "settings.json",
             project_root / ".claude" / "settings.local.json"]
    seen_true: str | None = None
    for f in files:
        if not f.exists():
            continue
        data, err = _load_json(f)
        if err or not isinstance(data, dict):
            # a malformed settings file is a reason to distrust the whole answer
            return False, f"{f} malformed"
        enabled = data.get("enabledPlugins")
        if enabled is None:
            continue
        if not isinstance(enabled, dict):
            return False, f"{f} enabledPlugins malformed"
        if PLUGIN_KEY in enabled:
            if enabled[PLUGIN_KEY] is False:
                return False, str(f)
            if enabled[PLUGIN_KEY] is True and seen_true is None:
                seen_true = str(f)
            elif enabled[PLUGIN_KEY] is not True:
                return False, f"{f} enabledPlugins[{PLUGIN_KEY}] not a bool"
    if seen_true:
        return True, seen_true
    return None, "absent"


def plugin_status(project_root: str | Path) -> PluginStatus:
    """Is the tagteam plugin installed **and enabled** for ``project_root``?

    Decided from Claude Code's own records under ``$CLAUDE_CONFIG_DIR`` (or
    ``~/.claude``): ``plugins/installed_plugins.json`` (schema version 2) must
    hold a record for ``tagteam@tagteam`` whose scope applies to this project
    (``user``, or ``project`` with a matching ``projectPath``); the plugin must
    be explicitly enabled (``enabledPlugins`` ``true`` in a settings file and
    ``false`` in none); and the record's ``installPath`` must contain the
    handoff skill. Anything else — missing files, malformed JSON, another
    schema version, an unknown scope — is *not installed*, with the reason.
    """
    root = Path(project_root)
    config_dir = claude_config_dir()
    reg_path = config_dir / "plugins" / "installed_plugins.json"
    data, err = _load_json(reg_path)
    if err:
        return PluginStatus(False, err)
    if not isinstance(data, dict) or data.get("version") != INSTALLED_SCHEMA_VERSION:
        return PluginStatus(False, f"{reg_path}: unsupported schema "
                                   f"(want version {INSTALLED_SCHEMA_VERSION})")
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return PluginStatus(False, f"{reg_path}: no plugins map")
    records = plugins.get(PLUGIN_KEY)
    if not records:
        return PluginStatus(False, f"{PLUGIN_KEY} not in {reg_path}")
    if not isinstance(records, list):
        return PluginStatus(False, f"{reg_path}: {PLUGIN_KEY} record malformed")

    applicable = None
    for rec in records:
        if not isinstance(rec, dict):
            continue
        scope = rec.get("scope")
        if scope == "user":
            applicable = rec
            break
        if scope == "project" and isinstance(rec.get("projectPath"), str) \
                and _same_path(rec["projectPath"], root):
            applicable = rec
            break
    if applicable is None:
        return PluginStatus(False, f"{PLUGIN_KEY} installed, but no user-scope or "
                                   f"matching project-scope record for {root}")
    scope = applicable["scope"]

    enabled, where = _enabled_state(root, config_dir)
    if enabled is False:
        return PluginStatus(False, f"disabled by {where}", scope=scope)
    if enabled is None:
        return PluginStatus(False, f"not explicitly enabled (enabledPlugins[{PLUGIN_KEY}] "
                                   f"absent from every settings file)", scope=scope)

    install_path = applicable.get("installPath")
    if not isinstance(install_path, str) or not install_path:
        return PluginStatus(False, "install record has no installPath", scope=scope)
    ip = Path(install_path)
    if not (ip / SKILL_IN_PLUGIN).is_file():
        return PluginStatus(False, f"{ip / SKILL_IN_PLUGIN} missing — broken install",
                            scope=scope)
    return PluginStatus(True, f"{scope} scope, enabled by {where}", install_path=ip,
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
    data, err = _load_json(hashes_path())
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
