"""Phase 36: scripts/upgrade_smoke.py — the isolated upgrade harness.

* the real registry (if any) is never touched, an unrelated sentinel is
  neither visited nor changed, the temporary registry lists exactly one
  entry, and a source-set-up project upgrades as a no-op;
* the helper runs under the interpreter selected with --python, in
  isolated mode from a cwd outside the repo, and reports its identity
  before any registry call — a same-named package in the checkout cannot
  shadow the target interpreter's installed one, and a version mismatch
  refuses to proceed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "upgrade_smoke.py"


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _real_registry_path() -> Path:
    from tagteam import registry
    return Path(registry.registry_path())


def _setup_project_isolated(project: Path, tmp: Path) -> None:
    """Run tagteam.setup.main with the registry globals patched (same rule as
    the harness): the real registry never sees the disposable project."""
    from tagteam import registry, setup as tsetup
    reg_dir = tmp / "setup-registry"
    old = (registry.REGISTRY_DIR, registry.REGISTRY_FILE)
    registry.REGISTRY_DIR, registry.REGISTRY_FILE = reg_dir, reg_dir / "projects.json"
    try:
        assert registry.REGISTRY_FILE.resolve().is_relative_to(tmp.resolve())
        project.mkdir(parents=True, exist_ok=True)
        (project / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n", encoding="utf-8")
        tsetup.main(str(project))
    finally:
        registry.REGISTRY_DIR, registry.REGISTRY_FILE = old
    assert json.loads((reg_dir / "projects.json").read_text(encoding="utf-8")) == [str(project.resolve())]


def _run(args: list[str]) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(HARNESS), "--json", *args], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    try:
        rep = json.loads(r.stdout)
    except ValueError:
        rep = {"raw": r.stdout, "stderr": r.stderr}
    return r.returncode, rep


def test_upgrade_smoke_isolated(tmp_path):
    project = tmp_path / "disposable"
    _setup_project_isolated(project, tmp_path)
    sentinel = tmp_path / "unrelated"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("do not touch\n", encoding="utf-8")
    real = _real_registry_path()
    real_before = _sha(real)

    code, rep = _run(["--project", str(project), "--sentinel", str(sentinel)])
    assert code == 0, rep
    assert rep["problems"] == []
    assert rep["project_diff"] == []                                   # source upgrade over source setup: no-op
    assert rep["temp_registry_after"] == [str(project.resolve())]     # exactly one entry, still
    assert str(sentinel.resolve()) not in rep["helper_stdout"]
    assert sorted(p.name for p in sentinel.iterdir()) == ["keep.txt"]
    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "do not touch\n"
    assert _sha(real) == real_before                                   # independent of the harness's own check
    # identity line: the helper is this interpreter and imported this checkout's package
    h = rep["helper"]
    assert Path(h["executable"]).resolve() == Path(sys.executable).resolve()
    import tagteam
    assert Path(h["file"]) == Path(tagteam.__file__).resolve()
    assert h["version"] == tagteam.__version__
    # the visited path is the disposable project only
    visited = [l.split(": ", 1)[1].strip() for l in rep["helper_stdout"].splitlines() if l.startswith(("Project: ", "Target: "))]
    assert visited and set(visited) == {str(project.resolve())}


def test_upgrade_smoke_detects_project_change_without_breaking_isolation(tmp_path):
    """A project that is NOT a no-op (a stale skill file) is reported as
    exit 1 with the diff, while every isolation check still holds."""
    project = tmp_path / "stale"
    _setup_project_isolated(project, tmp_path)
    skill = project / ".claude" / "skills" / "handoff" / "SKILL.md"
    skill.write_text("old contract\n", encoding="utf-8")
    code, rep = _run(["--project", str(project)])
    assert code == 1, rep
    assert rep["problems"] == []
    assert any(d.startswith("~ .claude/skills/handoff/SKILL.md") for d in rep["project_diff"])


STUB_INIT = textwrap.dedent('''
    __version__ = "9.9.9"
''')
STUB_REGISTRY = textwrap.dedent('''
    from pathlib import Path
    REGISTRY_DIR = Path.home() / ".tagteam"
    REGISTRY_FILE = REGISTRY_DIR / "projects.json"
    def registry_path():
        return REGISTRY_FILE
    def read_registry_raw():
        import json
        return json.loads(REGISTRY_FILE.read_text()) if REGISTRY_FILE.exists() else []
''')
STUB_CLI = textwrap.dedent('''
    import json
    from pathlib import Path
    def upgrade_command():
        from tagteam import registry
        entries = json.loads(Path(registry.REGISTRY_FILE).read_text())
        for e in entries:
            print("Project: " + e)
            Path(e, "STUB-VISITED").write_text("stub upgrade ran\\n")
        return 0
''')


@pytest.fixture(scope="module")
def stub_venv(tmp_path_factory) -> dict:
    """A real venv (no pip, no network) with a same-named stub `tagteam`
    package copied into its purelib."""
    root = tmp_path_factory.mktemp("stubvenv")
    vdir = root / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=(os.name != "nt")).create(str(vdir))
    py = vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = subprocess.run([str(py), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    pkg = Path(purelib) / "tagteam"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(STUB_INIT, encoding="utf-8")
    (pkg / "registry.py").write_text(STUB_REGISTRY, encoding="utf-8")
    (pkg / "cli.py").write_text(STUB_CLI, encoding="utf-8")
    return {"python": str(py), "prefix": str(vdir), "pkg": pkg}


def test_harness_selects_target_interpreter_and_cannot_be_shadowed(stub_venv, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    # launched from the checkout cwd; the checkout has a real `tagteam` package right here
    code, rep = _run(["--project", str(project), "--python", stub_venv["python"], "--expect-version", "9.9.9"])
    assert code == 1, rep            # 1 = isolation held, project changed (the stub writes a marker on purpose)
    assert rep["problems"] == []
    assert rep["project_diff"] == ["+ STUB-VISITED"]
    h = rep["helper"]
    assert Path(h["executable"]).resolve() == Path(stub_venv["python"]).resolve()
    assert Path(h["file"]).resolve() == (stub_venv["pkg"] / "__init__.py").resolve()   # the venv's stub, not the checkout
    assert Path(h["file"]).resolve().is_relative_to(Path(h["prefix"]).resolve())
    assert h["version"] == "9.9.9"
    assert (project / "STUB-VISITED").exists()                     # the stub's upgrade_command ran...
    assert "REGISTRY: " in rep["helper_stdout"]                     # ...against the temporary registry
    reg_line = [l for l in rep["helper_stdout"].splitlines() if l.startswith("REGISTRY: ")][0]
    assert "tagteam-upgrade-smoke-" in reg_line
    assert rep["temp_registry_after"] == [str(project.resolve())]


def test_harness_refuses_wrong_version_before_any_call(stub_venv, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    code, rep = _run(["--project", str(project), "--python", stub_venv["python"], "--expect-version", "3.0.0"])
    assert code == 2, rep
    assert any("9.9.9" in p and "3.0.0" in p for p in rep["problems"])
    assert not (project / "STUB-VISITED").exists()                 # `go` was never sent
    assert "helper_stdout" not in rep


def test_harness_default_interpreter_reports_checkout_package(tmp_path):
    project = tmp_path / "proj"
    _setup_project_isolated(project, tmp_path)
    code, rep = _run(["--project", str(project)])
    assert code == 0, rep
    import tagteam
    assert Path(rep["helper"]["file"]) == Path(tagteam.__file__).resolve()
    # installed-wheel mode would refuse an editable checkout (file outside the prefix) — prove the guard exists
    code2, rep2 = _run(["--project", str(project), "--expect-version", tagteam.__version__])
    under = Path(tagteam.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    if not under:
        assert code2 == 2 and any("not under the interpreter prefix" in p for p in rep2["problems"])
    else:   # a non-editable CI install: identity passes and the run is a no-op
        assert code2 == 0
