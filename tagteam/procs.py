"""
Small process helpers (Phase 32): liveness, parent pid, stable creation
identity, and process-tree kill — used by `tagteam cancel-turn` to bind a
recorded PID to the turn that spawned it before signalling anything.

`identity(pid)` returns "<pid>:<creation-time-string>" taken verbatim from
the platform tool (POSIX `ps -o lstart=`; Linux additionally
`/proc/<pid>/stat` starttime ticks; Windows `Win32_Process.CreationDate`).
The engine records it at spawn with this same function, so a later
comparison is plain string equality — a reused PID has a different
creation string. Any lookup failure returns None (callers treat that as
"unverifiable" and refuse to kill).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

_TIMEOUT = 5


def _run(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _ps_field(pid: int, field: str) -> str | None:
    out = _run(["ps", "-o", f"{field}=", "-p", str(pid)])
    if out is None:
        return None
    out = out.strip()
    return out or None


def _win_query(pid: int, prop: str) -> str | None:  # pragma: no cover - Windows CI
    ps = None
    for cand in ("powershell", "pwsh"):
        import shutil
        ps = shutil.which(cand)
        if ps:
            break
    if not ps:
        return None
    out = _run([ps, "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").{prop}"])
    if out is None:
        return None
    out = out.strip()
    return out or None


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return bool(out) and str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); ps state 'Z' means it's dead for our purposes.
    state = _ps_field(pid, "stat")
    if state and state.startswith("Z"):
        return False
    return True


def parent_pid(pid: int) -> int | None:
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        out = _win_query(pid, "ParentProcessId")
    else:
        out = _ps_field(pid, "ppid")
    if not out:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def identity(pid: int) -> str | None:
    """Stable creation identity string, or None if unverifiable."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        created = _win_query(pid, "CreationDate")
        return f"{pid}:{created}" if created else None
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # field 22 is starttime; the comm field may contain spaces/parens,
            # so split after the last ')'.
            rest = stat.rsplit(")", 1)[1].split()
            starttime = rest[19]  # fields 3.. → index 0 is field 3 → field 22 is index 19
            return f"{pid}:{starttime}"
        except Exception:
            pass
    lstart = _ps_field(pid, "lstart")
    return f"{pid}:{lstart}" if lstart else None


def cwd(pid: int) -> str | None:
    """Current working directory of `pid`, or None if unavailable
    (Linux: /proc; macOS/BSD: `lsof -a -p PID -d cwd -Fn`; Windows: None)."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        return None
    if sys.platform.startswith("linux"):
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
    out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("n") and len(line) > 1:
            return line[1:]
    return None


def list_processes(pattern: str | None = None) -> list[tuple[int, str]]:
    """[(pid, command line)] for visible processes — /proc on Linux (no
    subprocess), `ps -axo pid=,command=` elsewhere; empty on Windows.
    `pattern` (regex) filters command lines. Never raises."""
    import re
    rx = re.compile(pattern) if pattern else None
    out: list[tuple[int, str]] = []
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        return out
    if sys.platform.startswith("linux"):
        try:
            entries = os.listdir("/proc")
        except OSError:
            entries = []
        for name in entries:
            if not name.isdigit():
                continue
            try:
                raw = Path(f"/proc/{name}/cmdline").read_bytes()
            except OSError:
                continue
            argv = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
            if not argv or (rx and not rx.search(argv)):
                continue
            out.append((int(name), argv))
        return out
    text = _run(["ps", "-axo", "pid=,command="]) or ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, argv = line.partition(" ")
        if not pid_s.isdigit():
            continue
        argv = argv.strip()
        if rx and not rx.search(argv):
            continue
        out.append((int(pid_s), argv))
    return out


def kill_tree(pid: int) -> bool:
    """Kill `pid` and everything it spawned. Best-effort; returns True if a
    signal was sent."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    sent = False
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        try:
            r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            sent = r.returncode == 0
        except Exception:
            sent = False
        return sent
    # The engine spawns children with start_new_session=True, so the child
    # is a process-group leader; kill the whole group, then the pid itself.
    try:
        os.killpg(pid, signal.SIGKILL)
        sent = True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
        sent = True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    return sent
