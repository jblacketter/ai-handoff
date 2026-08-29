"""Phase 48: `tagteam setup` with the plugin — the remove path is gated on
content provenance; every other outcome keeps the project's files."""
from __future__ import annotations

from pathlib import Path

import pytest

from tagteam import setup as su
from tagteam import registry as registry_mod
from tests._plugin_env import REPO, fake_plugin, no_plugin, framework_files

PACKAGED = REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md"


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    p = tmp_path / "proj"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    return p


def skill_dir(p: Path) -> Path:
    return p / ".claude" / "skills" / "handoff"


class TestRemovePath:
    def test_known_vendored_skill_is_removed(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        su.main(str(project))
        out = capsys.readouterr().out
        assert not skill_dir(project).exists()
        assert "plugin: installed (user scope" in out
        assert "removed vendored handoff skill (" in out and "contract) — served by the plugin" in out
        assert (project / "templates" / "phase_plan.md").exists()   # everything else vendored as usual
        assert not su.needs_setup(str(project))

    def test_modified_skill_is_kept_and_not_overwritten(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        mine = PACKAGED.read_bytes() + b"\n# local rule\n"
        (skill_dir(project) / "SKILL.md").write_bytes(mine)
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").read_bytes() == mine
        assert "kept .claude/skills/handoff/: SKILL.md modified" in out

    def test_extra_file_is_kept(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        (skill_dir(project) / "extra.md").write_text("ours")
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "extra.md").exists()
        assert (skill_dir(project) / "SKILL.md").exists()
        assert "extra files present: extra.md" in out

    def test_empty_dir_left_alone(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        skill_dir(project).mkdir(parents=True)
        su.main(str(project))
        assert skill_dir(project).is_dir()
        assert "kept .claude/skills/handoff/: empty directory" in capsys.readouterr().out

    def test_absent_skill_is_not_vendored(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        su.main(str(project))
        assert not skill_dir(project).exists()
        assert "served by the plugin — nothing to vendor" in capsys.readouterr().out

    def test_plugin_not_installed_vendors_as_today(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").read_bytes() == PACKAGED.read_bytes()
        assert "plugin: not installed (" in out

    def test_disabled_plugin_vendors(self, project, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, enabled=False)
        su.main(str(project))
        assert (skill_dir(project) / "SKILL.md").exists()

    def test_no_plugin_flag_forces_vendoring(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        su.main(str(project), no_plugin=True)
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").exists()
        assert "plugin: not installed (--no-plugin)" in out

    def test_cli_no_plugin_flag(self, project, tmp_path, monkeypatch):
        import tagteam.cli as cli
        fake_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(project), "--no-plugin"])
        assert cli.main() == 0
        assert (skill_dir(project) / "SKILL.md").exists()

    def test_run_setup_no_plugin(self, project, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=False)
        # migrated project: complete without the flag …
        assert not su.needs_setup(str(project))
        # … but --no-plugin means the local skill is required, so setup runs and vendors it
        su.run_setup(str(project), no_plugin=True)
        assert (skill_dir(project) / "SKILL.md").exists()


class TestUpgradeSweep:
    def test_upgrade_removes_known_vendored_copies(self, project, tmp_path, monkeypatch, capsys):
        from tagteam.cli import upgrade_command
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        registry_mod.register_project(str(project))
        assert upgrade_command() == 0
        assert not skill_dir(project).exists()
