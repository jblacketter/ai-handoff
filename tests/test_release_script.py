"""Phase 41: scripts/release.py — transactional three-file version bump."""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "release.py"

PYPROJECT = '[project]\nname = "demo"\nversion = "3.4.0"\ndescription = "x"\n'
CITATION = "cff-version: 1.2.0\ntitle: demo\nversion: 3.4.0\ndate-released: 2026-08-16\n"
UVLOCK = ('version = 1\n\n[[package]]\nname = "other"\nversion = "1.2.3"\n\n'
          '[[package]]\nname = "demo"\nversion = "3.4.0"\nsource = { editable = "." }\n')


def _load():
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CITATION.cff").write_text(CITATION, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(UVLOCK, encoding="utf-8")
    assert not (tmp_path / ".git").exists()          # the script needs no git
    return tmp_path


def _bytes(root):
    return {n: (root / n).read_bytes() for n in ("pyproject.toml", "CITATION.cff", "uv.lock")}


def _fake_uv(tmp_path, monkeypatch, body: str) -> Path:
    """Put a fake `uv` first on PATH; `body` is the shell script after the shebang."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return uv


# a fake `uv lock` that rewrites ONLY the demo package's version line
UV_REWRITE = r'''
[ "$1" = "lock" ] || exit 9
python3 - <<'PY'
import re, pathlib
lock = pathlib.Path("uv.lock"); py = pathlib.Path("pyproject.toml").read_text()
new = re.search(r'version = "([^"]+)"', py).group(1)
t = lock.read_text()
t = re.sub(r'(name = "demo"\nversion = ")[^"]+(")', lambda m: m.group(1) + new + m.group(2), t)
lock.write_text(t)
PY
'''
UV_WRITE_THEN_FAIL = r'''
[ "$1" = "lock" ] || exit 9
printf 'garbage partial write\n' > uv.lock
echo "resolver exploded" >&2
exit 1
'''


class TestRelease:
    def test_no_lock_happy_path(self, root, capsys):
        m = _load()
        rc = m.main(["3.5.0", "--root", str(root), "--no-lock", "--date", "2026-08-17"])
        out = capsys.readouterr().out
        assert rc == 0 and "3.4.0 → 3.5.0" in out and "git tag v3.5.0" in out
        assert 'version = "3.5.0"' in (root / "pyproject.toml").read_text()
        cff = (root / "CITATION.cff").read_text()
        assert "version: 3.5.0\n" in cff and "date-released: 2026-08-17\n" in cff
        assert (root / "uv.lock").read_text() == UVLOCK                    # untouched with --no-lock

    @pytest.mark.parametrize("bad", ["3.4.0", "3.4.0-x", "3.3.9", "v3.5.0", "3.5", "abc"])
    def test_refuses_non_increasing_or_malformed_and_writes_nothing(self, root, bad, capsys):
        m = _load()
        before = _bytes(root)
        assert m.main([bad, "--root", str(root), "--no-lock"]) == 1
        assert "release:" in capsys.readouterr().err
        assert _bytes(root) == before

    def test_dry_run_is_inert(self, root, monkeypatch, capsys):
        m = _load()
        marker = root / "uv-ran"
        _fake_uv(root, monkeypatch, f'touch "{marker}"\nexit 0\n')
        before = _bytes(root)
        assert m.main(["3.5.0", "--root", str(root), "--dry-run"]) == 0
        assert "dry run" in capsys.readouterr().out
        assert _bytes(root) == before and not marker.exists()

    def test_lock_enabled_rewrites_only_the_project_entry(self, root, monkeypatch):
        m = _load()
        _fake_uv(root, monkeypatch, UV_REWRITE)
        assert m.main(["3.5.0", "--root", str(root), "--date", "2026-08-17"]) == 0
        lock = (root / "uv.lock").read_text()
        assert 'name = "demo"\nversion = "3.5.0"' in lock
        assert 'name = "other"\nversion = "1.2.3"' in lock
        assert lock.replace('name = "demo"\nversion = "3.5.0"', 'name = "demo"\nversion = "3.4.0"') == UVLOCK
        assert 'version = "3.5.0"' in (root / "pyproject.toml").read_text()
        assert "version: 3.5.0" in (root / "CITATION.cff").read_text()

    def test_uv_writes_then_fails_rolls_everything_back(self, root, monkeypatch, capsys):
        m = _load()
        _fake_uv(root, monkeypatch, UV_WRITE_THEN_FAIL)
        before = _bytes(root)
        rc = m.main(["3.5.0", "--root", str(root)])
        err = capsys.readouterr().err
        assert rc == 2 and "resolver exploded" in err and "rolled back: pyproject.toml, uv.lock" in err
        assert _bytes(root) == before

    def test_uv_missing_without_no_lock_rolls_back(self, root, monkeypatch, capsys):
        m = _load()
        monkeypatch.setenv("PATH", str(root / "empty-bin"))
        (root / "empty-bin").mkdir()
        before = _bytes(root)
        assert m.main(["3.5.0", "--root", str(root)]) == 2
        assert "not found on PATH" in capsys.readouterr().err and _bytes(root) == before

    def test_citation_write_failure_after_lock_rolls_back_pyproject_and_lock(self, root, monkeypatch, capsys):
        m = _load()
        _fake_uv(root, monkeypatch, UV_REWRITE)
        real = m._write_atomic

        def flaky(path, data):
            if path.name == "CITATION.cff" and data != CITATION.encode():
                raise OSError("disk full")
            return real(path, data)

        monkeypatch.setattr(m, "_write_atomic", flaky)
        before = _bytes(root)
        rc = m.main(["3.5.0", "--root", str(root)])
        err = capsys.readouterr().err
        assert rc == 2 and "disk full" in err and "rolled back: pyproject.toml, uv.lock" in err
        assert _bytes(root) == before

    def test_usage(self, root, capsys):
        m = _load()
        assert m.main([]) == 1
        assert m.main(["--help"]) == 0
        assert m.main(["3.5.0", "--bogus", "--root", str(root)]) == 1
        assert m.main(["3.5.0", "3.6.0", "--root", str(root)]) == 1
        assert m.main(["3.5.0", "--root", str(root), "--no-lock", "--date", "yesterday"]) == 1
        assert _bytes(root)["pyproject.toml"] == PYPROJECT.encode()

    def test_cli_entry(self, root):
        import subprocess
        r = subprocess.run([sys.executable, str(SCRIPT), "3.5.0", "--root", str(root), "--no-lock"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and "done. next:" in r.stdout
