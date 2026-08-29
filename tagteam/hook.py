"""Phase 48: ``tagteam hook session-start`` — the body of the plugin's
SessionStart hook.

Prints one status line when the cwd is a tagteam project with a readable
``handoff-state.json``, plus a version-skew warning when the plugin declares a
minimum tagteam version the installed CLI does not meet. In **every** other
case it prints nothing and exits 0: a session start must never fail, and a
non-tagteam project with the plugin installed must see nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MIN_VERSION_KEY = "minVersion"   # under plugin.json["tagteam"]


def _semver(s) -> tuple[int, ...] | None:
    if not isinstance(s, str):
        return None
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", s.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def banner_line(project_dir: Path) -> str | None:
    """The one-line banner, or None when there is nothing safe to say."""
    if not (project_dir / "tagteam.yaml").is_file():
        return None
    state_path = project_dir / "handoff-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    phase, ctype, rnd = state.get("phase"), state.get("type"), state.get("round")
    turn, status = state.get("turn"), state.get("status")
    if not isinstance(phase, str) or not isinstance(ctype, str) \
            or not isinstance(rnd, int) or not isinstance(status, str):
        return None
    if turn is not None and not isinstance(turn, str):
        return None
    return (f"tagteam: phase {phase} | type {ctype} | round {rnd} | "
            f"turn {turn or '—'} | status {status}")


def skew_warning(plugin_root: Path | None, installed: str) -> str | None:
    if plugin_root is None:
        return None
    try:
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    section = manifest.get("tagteam")
    minimum = section.get(MIN_VERSION_KEY) if isinstance(section, dict) else None
    min_t, inst_t = _semver(minimum), _semver(installed)
    if min_t is None or inst_t is None or inst_t >= min_t:
        return None
    return (f"warning: plugin {manifest.get('version', '?')} expects tagteam >= "
            f"{minimum}, installed {installed} — run: uv tool upgrade tagteam")


def session_start(argv: list[str], *, cwd: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    plugin_root: Path | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--plugin-root" and i + 1 < len(argv):
            plugin_root = Path(argv[i + 1]) if argv[i + 1] else None
            i += 2
        else:
            i += 1   # unknown args are ignored, never fatal
    try:
        project = cwd or Path.cwd()
        line = banner_line(project)
        if line is None:
            return 0
        from tagteam import __version__
        print(line, file=out)
        warn = skew_warning(plugin_root, __version__)
        if warn:
            print(warn, file=out)
    except Exception:  # noqa: BLE001 — a hook must never fail a session start
        pass
    return 0


def hook_command(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help"):
        print("usage: tagteam hook session-start [--plugin-root DIR]")
        return 0 if args else 1
    if args[0] == "session-start":
        return session_start(args[1:])
    print(f"unknown hook: {args[0]}", file=sys.stderr)
    return 1
