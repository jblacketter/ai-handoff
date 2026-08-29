"""Phase 48: `tagteam hook session-start` — silent and exit 0 in every failure
case; one banner line (+ skew warning) when there is a valid cycle."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tagteam import hook as hk
from tests._plugin_env import PLUGIN_SRC


def run(cwd: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tagteam", "hook", "session-start", *extra],
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


STATE = {"phase": "alpha", "type": "impl", "round": 2, "turn": "reviewer", "status": "ready"}


def _project(tmp_path: Path, state=STATE) -> Path:
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    if state is not None:
        text = state if isinstance(state, str) else json.dumps(state)
        (tmp_path / "handoff-state.json").write_text(text)
    return tmp_path


class TestSilentCases:
    def test_not_a_tagteam_project(self, tmp_path):
        (tmp_path / "handoff-state.json").write_text(json.dumps(STATE))  # stray state, no yaml
        r = run(tmp_path)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

    def test_no_state_file(self, tmp_path):
        r = run(_project(tmp_path, None))
        assert r.returncode == 0 and r.stdout == ""

    @pytest.mark.parametrize("bad", ["{", "[]", '{"phase": 1}', "", '{"phase":"a","type":"plan","round":"1","status":"ready"}'])
    def test_malformed_state(self, tmp_path, bad):
        r = run(_project(tmp_path, bad))
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

    def test_unknown_args_ignored(self, tmp_path):
        r = run(_project(tmp_path, None), "--bogus", "x")
        assert r.returncode == 0 and r.stdout == ""

    def test_no_plugin_root_no_warning(self, tmp_path):
        r = run(_project(tmp_path))
        assert r.returncode == 0
        assert r.stdout.splitlines() == ["tagteam: phase alpha | type impl | round 2 | turn reviewer | status ready"]

    def test_bad_plugin_root_no_warning(self, tmp_path):
        r = run(_project(tmp_path), "--plugin-root", str(tmp_path / "nope"))
        assert r.returncode == 0 and len(r.stdout.splitlines()) == 1


class TestBanner:
    def test_turn_none_renders_dash(self, tmp_path):
        p = _project(tmp_path, {**STATE, "turn": None, "status": "done"})
        assert hk.banner_line(p) == "tagteam: phase alpha | type impl | round 2 | turn — | status done"


def _plugin_root(tmp_path: Path, min_version: str | None, version="9.9.9") -> Path:
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    m = {"name": "tagteam", "version": version}
    if min_version is not None:
        m["tagteam"] = {"minVersion": min_version}
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(m))
    return root


class TestSkew:
    def test_warns_below_minimum(self, tmp_path):
        p = _project(tmp_path)
        r = run(p, "--plugin-root", str(_plugin_root(tmp_path, "99.0.0")))
        assert r.returncode == 0
        lines = r.stdout.splitlines()
        assert len(lines) == 2
        assert lines[1].startswith("warning: plugin 9.9.9 expects tagteam >= 99.0.0, installed ")
        assert "uv tool upgrade tagteam" in lines[1]

    def test_silent_at_or_above_minimum(self, tmp_path):
        p = _project(tmp_path)
        r = run(p, "--plugin-root", str(_plugin_root(tmp_path, "0.1.0")))
        assert len(r.stdout.splitlines()) == 1

    def test_no_min_declared(self, tmp_path):
        assert hk.skew_warning(_plugin_root(tmp_path, None), "1.0.0") is None

    def test_garbage_min(self, tmp_path):
        assert hk.skew_warning(_plugin_root(tmp_path, "latest"), "1.0.0") is None

    def test_skew_never_blocks_banner(self, tmp_path):
        # even with a plugin root that is a file, exit 0 and the banner prints
        f = tmp_path / "file"; f.write_text("x")
        r = run(_project(tmp_path), "--plugin-root", str(f))
        assert r.returncode == 0 and len(r.stdout.splitlines()) == 1


class TestCli:
    def test_unknown_hook(self, tmp_path):
        r = subprocess.run([sys.executable, "-m", "tagteam", "hook", "nope"], cwd=str(tmp_path),
                           capture_output=True, text=True)
        assert r.returncode == 1 and "unknown hook" in r.stderr


HOOK_CMD = json.loads((PLUGIN_SRC / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]


def _run_hook_cmd(cwd: Path, bin_dir: Path, plugin_root: Path) -> subprocess.CompletedProcess:
    env = {**__import__("os").environ, "PATH": f"{bin_dir}:{__import__('os').environ.get('PATH', '')}",
           "CLAUDE_PLUGIN_ROOT": str(plugin_root)}
    return subprocess.run(["sh", "-c", HOOK_CMD], cwd=str(cwd), env=env, capture_output=True,
                          text=True, timeout=60)


class TestHookCommandShell:
    """The literal hooks.json command, run by sh, against three `tagteam`
    binaries: none, an old one (no `hook` subcommand), and the real one."""

    def _bin(self, tmp_path: Path, script: str | None) -> Path:
        bin_dir = tmp_path / "bin"; bin_dir.mkdir()
        if script is not None:
            exe = bin_dir / "tagteam"
            exe.write_text(script, encoding="utf-8")
            exe.chmod(0o755)
        return bin_dir

    def test_no_tagteam_on_path_is_silent(self, tmp_path):
        p = _project(tmp_path)
        env_path = tmp_path / "emptybin"; env_path.mkdir()
        r = subprocess.run(["/bin/sh", "-c", HOOK_CMD], cwd=str(p), capture_output=True, text=True,
                           env={"PATH": str(env_path), "CLAUDE_PLUGIN_ROOT": str(tmp_path)})
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

    def test_old_cli_without_hook_subcommand_is_silent(self, tmp_path):
        """Seen on the arbiter's machine: the 3.9.0 uv tool prints
        'Unknown command: hook' to stdout and exits 1 — that text must not
        reach the session."""
        p = _project(tmp_path)
        bin_dir = self._bin(tmp_path, "#!/bin/sh\necho 'Unknown command: hook'\necho \"Run 'tagteam --help' for usage.\"\nexit 1\n")
        r = _run_hook_cmd(p, bin_dir, tmp_path)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

    def test_current_cli_prints_banner(self, tmp_path):
        p = _project(tmp_path)
        bin_dir = self._bin(tmp_path, f"#!/bin/sh\nexec {sys.executable} -m tagteam \"$@\"\n")
        r = _run_hook_cmd(p, bin_dir, _plugin_root(tmp_path, "0.0.1"))
        assert r.returncode == 0
        assert r.stdout.splitlines() == ["tagteam: phase alpha | type impl | round 2 | turn reviewer | status ready"]

    def test_current_cli_no_state_prints_nothing(self, tmp_path):
        p = _project(tmp_path, None)
        bin_dir = self._bin(tmp_path, f"#!/bin/sh\nexec {sys.executable} -m tagteam \"$@\"\n")
        r = _run_hook_cmd(p, bin_dir, _plugin_root(tmp_path, "0.0.1"))
        assert r.returncode == 0 and r.stdout == ""
