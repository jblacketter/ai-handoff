"""Phase 48: tagteam.plugin — detection, provenance, and the shipped plugin tree."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tagteam import plugin as pl
from tests._plugin_env import REPO, PLUGIN_SRC, fake_plugin, no_plugin

PACKAGED = REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md"


class TestPluginTree:
    def test_plugin_skill_matches_packaged_copy(self):
        assert (PLUGIN_SRC / "skills" / "handoff" / "SKILL.md").read_bytes() == PACKAGED.read_bytes()

    def test_manifests_parse_and_agree(self):
        manifest = json.loads((PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        assert manifest["name"] == pl.PLUGIN_NAME
        assert market["name"] == pl.MARKETPLACE_NAME
        assert [p["name"] for p in market["plugins"]] == [pl.PLUGIN_NAME]
        assert market["plugins"][0]["source"] == "./plugin"
        assert manifest["tagteam"]["minVersion"]

    def test_plugin_version_matches_pyproject(self):
        import tomllib
        py = tomllib.load(open(REPO / "pyproject.toml", "rb"))["project"]["version"]
        manifest = json.loads((PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["version"] == py
        assert manifest["tagteam"]["minVersion"] == py

    def test_hooks_json_is_pinned_to_the_subcommand(self):
        hooks = json.loads((PLUGIN_SRC / "hooks" / "hooks.json").read_text())
        cmds = [h["command"] for grp in hooks["hooks"]["SessionStart"] for h in grp["hooks"]]
        assert len(cmds) == 1
        cmd = cmds[0]
        assert 'tagteam hook session-start --plugin-root "$CLAUDE_PLUGIN_ROOT"' in cmd
        assert cmd.startswith("command -v tagteam >/dev/null")
        assert cmd.endswith("|| true")
        assert set(hooks["hooks"]) == {"SessionStart"}

    def test_current_contract_hash_is_known(self):
        digest = hashlib.sha256(PACKAGED.read_bytes()).hexdigest()
        assert digest in pl.known_contract_hashes()

    def test_hash_file_is_current(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "contract_hashes.py"),
                            "--check", "--root", str(REPO)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


class TestPluginStatus:
    def test_nothing_installed(self, tmp_path, monkeypatch):
        no_plugin(tmp_path, monkeypatch)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "missing" in st.reason

    def test_user_scope_enabled(self, tmp_path, monkeypatch):
        install = fake_plugin(tmp_path, monkeypatch)
        st = pl.plugin_status(tmp_path / "proj")
        assert st.installed and st.scope == "user" and st.install_path == install
        assert "enabled by" in st.reason

    def test_disabled_in_user_settings(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, enabled=False)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "disabled by" in st.reason

    def test_not_explicitly_enabled_is_not_installed(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, enabled=None)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "not explicitly enabled" in st.reason

    def test_project_local_false_overrides_user_true(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"; proj.mkdir()
        fake_plugin(tmp_path, monkeypatch, enabled=True)
        (proj / ".claude").mkdir()
        (proj / ".claude" / "settings.local.json").write_text(
            json.dumps({"enabledPlugins": {pl.PLUGIN_KEY: False}}))
        st = pl.plugin_status(proj)
        assert not st.installed and "settings.local.json" in st.reason

    def test_project_scope_matching(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"; proj.mkdir()
        fake_plugin(tmp_path, monkeypatch, scope="project", project_path=proj,
                    enabled_in="project")
        assert pl.plugin_status(proj).installed

    def test_project_scope_other_path(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"; proj.mkdir()
        other = tmp_path / "other"; other.mkdir()
        fake_plugin(tmp_path, monkeypatch, scope="project", project_path=other)
        st = pl.plugin_status(proj)
        assert not st.installed and "no user-scope or matching project-scope" in st.reason

    def test_unknown_scope(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, scope="galaxy")
        assert not pl.plugin_status(tmp_path).installed

    def test_malformed_registry(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, registry_text="{not json")
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "unreadable" in st.reason

    def test_wrong_schema_version(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, schema_version=3)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "unsupported schema" in st.reason

    def test_malformed_settings_fails_closed(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, settings_text="[1,2")
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "malformed" in st.reason

    def test_broken_install_missing_skill(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, broken=True)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "broken install" in st.reason

    def test_respects_claude_config_dir(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert not pl.plugin_status(tmp_path).installed


class TestProvenance:
    def _dir(self, tmp_path, content: bytes | None = PACKAGED.read_bytes()):
        d = tmp_path / ".claude" / "skills" / "handoff"
        d.mkdir(parents=True)
        if content is not None:
            (d / "SKILL.md").write_bytes(content)
        return d

    def test_known_contract_is_removable(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path))
        assert p.removable and p.version

    def test_modified_contract_is_kept(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path, PACKAGED.read_bytes() + b"\n# mine\n"))
        assert not p.removable and "modified" in p.reason

    def test_extra_file_is_kept(self, tmp_path):
        d = self._dir(tmp_path)
        (d / "notes.md").write_text("ours")
        p = pl.vendored_skill_provenance(d)
        assert not p.removable and "extra files present: notes.md" in p.reason

    def test_empty_dir_is_kept(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path, None))
        assert not p.removable and p.reason == "empty directory"

    def test_absent(self, tmp_path):
        p = pl.vendored_skill_provenance(tmp_path / "nope")
        assert not p.removable and p.reason == "absent"

    def test_symlinked_skill_is_kept(self, tmp_path):
        d = tmp_path / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
        (d / "SKILL.md").symlink_to(PACKAGED)
        assert not pl.vendored_skill_provenance(d).removable

    def test_empty_known_set_never_removes(self, tmp_path):
        assert not pl.vendored_skill_provenance(self._dir(tmp_path), known={}).removable
