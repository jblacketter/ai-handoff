"""Phase 48 test helper: a fake Claude Code config dir with the tagteam
plugin recorded the way `installed_plugins.json` / `settings.json` do it.
Injected through CLAUDE_CONFIG_DIR so tests exercise the real reader."""
from __future__ import annotations

import json
from pathlib import Path

from tagteam.plugin import PLUGIN_KEY

REPO = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO / "plugin"


def fake_plugin(tmp_path: Path, monkeypatch, *, scope: str = "user",
                project_path: Path | None = None, enabled: bool | None = True,
                enabled_in: str = "user", broken: bool = False,
                schema_version: int = 2, registry_text: str | None = None,
                settings_text: str | None = None) -> Path:
    """Build `<tmp>/claude/` and point CLAUDE_CONFIG_DIR at it. Returns the
    plugin install path (a copy of this repo's `plugin/` tree)."""
    cfg = tmp_path / "claude"
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    install = cfg / "plugins" / "cache" / "tagteam" / "tagteam" / "9.9.9"
    (install / "skills" / "handoff").mkdir(parents=True, exist_ok=True)
    if not broken:
        (install / "skills" / "handoff" / "SKILL.md").write_bytes(
            (PLUGIN_SRC / "skills" / "handoff" / "SKILL.md").read_bytes())
    (install / ".claude-plugin").mkdir(exist_ok=True)
    (install / ".claude-plugin" / "plugin.json").write_bytes(
        (PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_bytes())
    record = {"scope": scope, "installPath": str(install), "version": "9.9.9"}
    if scope == "project":
        record["projectPath"] = str(project_path)
    reg = {"version": schema_version, "plugins": {PLUGIN_KEY: [record]}}
    (cfg / "plugins" / "installed_plugins.json").write_text(
        registry_text if registry_text is not None else json.dumps(reg), encoding="utf-8")
    if settings_text is not None:
        (cfg / "settings.json").write_text(settings_text, encoding="utf-8")
    elif enabled is not None:
        target = cfg / "settings.json"
        if enabled_in == "project" and project_path is not None:
            target = project_path / ".claude" / "settings.json"
        elif enabled_in == "local" and project_path is not None:
            target = project_path / ".claude" / "settings.local.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"enabledPlugins": {PLUGIN_KEY: enabled}}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return install


def no_plugin(tmp_path: Path, monkeypatch) -> Path:
    """An empty config dir: nothing installed."""
    cfg = tmp_path / "claude-empty"
    cfg.mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return cfg


def framework_files(project: Path, *, skill: bool = True) -> None:
    """templates + checklists (+ optionally the vendored skill) so
    needs_setup's other requirements are met."""
    (project / "templates").mkdir(parents=True, exist_ok=True)
    (project / "templates" / "phase_plan.md").write_text("t")
    (project / "docs" / "checklists").mkdir(parents=True, exist_ok=True)
    (project / "docs" / "checklists" / "code_review.md").write_text("c")
    if skill:
        d = project / ".claude" / "skills" / "handoff"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_bytes(
            (REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md").read_bytes())
