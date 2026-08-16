"""
Arbiter cockpit API (Phase 34): pure payload builders for the cockpit's
read endpoints, the SSE change signature, and thin wrappers that run the
Phase 32/33 control commands with a captured `out` buffer.

Everything here is unit-testable without HTTP; `tagteam.server` routes are
thin. Builders never raise on missing data — they return the documented
shape with nulls / empty lists so the page can render an honest empty
state. Only programming errors propagate.
"""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tagteam import headless as h

SCOPE_DIFF_MAX_BYTES = 200_000
SCOPE_DIFF_MAX_FILES = 400
DEFAULT_TAIL_LINES = 40
MAX_TAIL_LINES = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _age_s(ts: str | None) -> float | None:
    """Seconds since an ISO timestamp, or None when unparsable."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - t).total_seconds())


def web_user() -> str:
    """`by` identity recorded for browser-initiated controls: `web:<user>`
    where user is the OS user running the server (the browser is local by
    default)."""
    env = os.environ.get("TAGTEAM_ARBITER")
    if env:
        return f"web:{env}"
    try:
        return f"web:{getpass.getuser()}"
    except Exception:
        return "web:arbiter"


# ---------------------------------------------------------------------------
# Watcher liveness (project-bound)
# ---------------------------------------------------------------------------

def _same_dir(a: str | None, b: str | Path) -> bool:
    if not a:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


# A real watcher invocation, anchored at the START of the command line:
# `python -m tagteam watch …`, `python …/bin/tagteam watch …` (console
# script), `…/bin/tagteam watch …`, or `tagteam watch …`. Deliberately NOT a
# loose "tagteam.*watch" — a shell whose command text merely mentions the
# words (with the project as cwd) must not count as a watcher.
import re as _re
_PY = r"\S*python[\w.]*(?:\.exe)?"
WATCH_ARGV_RE = _re.compile(
    r"^(?:" + _PY + r"\s+-m\s+tagteam"                       # python -m tagteam watch
    r"|" + _PY + r"\s+\S*[/\\]tagteam(?:\.exe)?"           # python /path/bin/tagteam watch
    r"|(?:\S*[/\\])?tagteam(?:\.exe)?"                     # [/path/]tagteam watch
    r")\s+watch(?:\s|$)")


def watcher_status(project_dir: str | Path, inflight: dict | None = None) -> dict:
    """Is a watcher running FOR THIS PROJECT?  Signals, in order:

    1. the watcher pidfile (`.tagteam/watcher.json`: pid + creation identity
       + mode) — Tagteam's own launch shape puts no project path on argv,
       so this is the primary binding; a dead pid / identity mismatch is
       reported as `stale_pidfile` and never trusted;
    2. a process scan: `tagteam … watch` processes whose argv names the
       project OR whose cwd is the project (older watchers, no pidfile);
    3. the in-flight pointer's watcher pid/identity (headless runner).

    Returns {running, pid, mode, source, stale_pidfile}."""
    from tagteam import procs
    from tagteam import watcher as watcher_mod
    root = Path(project_dir)
    out = {"running": False, "pid": None, "mode": None, "source": None, "stale_pidfile": False}
    rec = watcher_mod.read_pidfile(root)
    if rec is not None:
        pid = rec.get("pid")
        alive = isinstance(pid, int) and pid > 0 and procs.pid_alive(pid)
        ident_ok = True
        if alive and rec.get("ident"):
            now_ident = procs.identity(pid)
            ident_ok = (now_ident is None) or (now_ident == rec.get("ident"))
        if alive and ident_ok:
            out.update({"running": True, "pid": pid, "mode": rec.get("mode"),
                        "source": "pidfile", "started_at": rec.get("started_at")})
            return out
        out["stale_pidfile"] = True
    # process scan (Linux: /proc, no subprocess; macOS/BSD: one `ps -axo`)
    me = os.getpid()
    for pid, argv in procs.list_processes(WATCH_ARGV_RE.pattern):
        if pid == me or not procs.pid_alive(pid):
            continue
        names_project = any(_same_dir(tok.rstrip("/\\"), root) for tok in argv.split()
                            if tok.startswith(("/", "~", "\\")) or (len(tok) > 2 and tok[1] == ":"))
        if names_project or _same_dir(procs.cwd(pid), root):
            mode = None
            if argv and "--mode" in argv:
                try:
                    mode = argv.split("--mode", 1)[1].split()[0]
                except IndexError:
                    mode = None
            out.update({"running": True, "pid": pid, "mode": mode, "source": "process-scan"})
            return out
    # in-flight runner
    if inflight and inflight.get("watcher_pid"):
        wpid = inflight.get("watcher_pid")
        try:
            if procs.pid_alive(wpid) and (not inflight.get("watcher_ident")
                                         or procs.identity(wpid) == inflight.get("watcher_ident")):
                out.update({"running": True, "pid": wpid, "mode": "headless", "source": "inflight"})
                return out
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# /api/now
# ---------------------------------------------------------------------------

def now_payload(project_dir: str | Path) -> dict:
    """State, owed role (+ how long owed), in-flight, pause marker, watcher
    liveness, briefer flag, agents, and a pending-notes count."""
    from tagteam.state import read_state
    from tagteam.config import read_config, get_agent_names, get_briefer_spec
    from tagteam.cycle import read_status
    root = Path(project_dir)
    state = read_state(str(root)) or {}
    config = read_config(root / "tagteam.yaml") or {}
    lead, reviewer = (get_agent_names(config) if config else (None, None))
    try:
        briefer_enabled = bool(get_briefer_spec(config).get("enabled")) if config else False
    except Exception:
        briefer_enabled = False

    phase, ctype = state.get("phase"), state.get("type")
    cycle_status = None
    if phase and ctype:
        try:
            cycle_status = read_status(phase, ctype, str(root))
        except Exception:
            cycle_status = None

    owed = None
    turn = state.get("turn")
    if state.get("status") in ("ready", "working") and turn in ("lead", "reviewer"):
        owed = {
            "role": turn,
            "agent": lead if turn == "lead" else reviewer,
            "since": state.get("updated_at"),
            "age_s": _age_s(state.get("updated_at")),
        }

    inflight = h.read_inflight(root)
    if inflight is not None:
        inflight = dict(inflight)
        inflight["age_s"] = _age_s(inflight.get("started_at"))
        pid = inflight.get("pid")
        try:
            from tagteam import procs
            inflight["pid_alive"] = bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))
        except Exception:
            inflight["pid_alive"] = None

    paused = h.read_pause(root)
    if paused is not None:
        paused = dict(paused)
        paused["age_s"] = _age_s(paused.get("ts"))

    try:
        watcher = watcher_status(root, inflight)
    except Exception:
        watcher = {"running": False, "pid": None, "mode": None, "source": None, "stale_pidfile": False}

    pending_notes = 0
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            rows = db.get_interjections(conn, undelivered_only=True, include_retired=False)
            pending_notes = len([r for r in rows
                                 if r.get("phase") is None
                                 or (r.get("phase") == phase and r.get("type") == ctype)])
        finally:
            conn.close()
    except Exception:
        pending_notes = 0

    return {
        "ts": _now_iso(),
        "state": state,
        "cycle": cycle_status,
        "owed": owed,
        "inflight": inflight,
        "paused": paused,
        "watcher": watcher,
        "briefer_enabled": briefer_enabled,
        "agents": {"lead": lead, "reviewer": reviewer},
        "pending_notes": pending_notes,
        "project_dir": str(root),
    }


# ---------------------------------------------------------------------------
# /api/rounds (extended), /api/interjections, /api/briefs
# ---------------------------------------------------------------------------

def rounds_payload(project_dir: str | Path, phase: str, ctype: str) -> dict:
    """Rounds with additive `entries`, `rulings`, `interjections` per round
    (exactly what `tagteam cycle rounds` prints) plus the legacy `html`."""
    from tagteam.cycle import tail_rounds
    from tagteam.parser import format_rounds_html
    try:
        rounds = tail_rounds(phase, ctype, None, str(project_dir))
    except Exception:
        rounds = []
    return {"rounds": rounds, "html": format_rounds_html(rounds) if rounds else ""}


def _interjection_status(r: dict) -> str:
    if r.get("retired_ts"):
        return "retired"
    if r.get("delivered_ts"):
        return "delivered"
    return "pending"


def interjections_payload(project_dir: str | Path, phase: str | None,
                          ctype: str | None) -> dict:
    """Notes for the cycle (plus unscoped notes), newest last, with a
    derived `status` (pending / delivered / retired)."""
    from tagteam import db
    rows: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            if phase and ctype:
                rows = db.get_interjections(conn, phase=phase, cycle_type=ctype)
                rows += [r for r in db.get_interjections(conn) if r["phase"] is None]
            else:
                rows = db.get_interjections(conn)
        finally:
            conn.close()
    except Exception:
        rows = []
    for r in rows:
        r["status"] = _interjection_status(r)
    rows.sort(key=lambda r: r["id"])
    return {"interjections": rows,
            "pending": len([r for r in rows if r["status"] == "pending"])}


def briefs_payload(project_dir: str | Path, phase: str | None, ctype: str | None) -> dict:
    """Brief history (newest first), without content (see /api/brief/<id>)."""
    from tagteam import db
    rows: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            rows = db.brief_history(conn, phase, ctype)
        finally:
            conn.close()
    except Exception:
        rows = []
    for r in rows:
        r["has_content"] = bool(r.get("content"))
        r.pop("content", None)
    return {"briefs": rows}


def brief_payload(project_dir: str | Path, brief_id: int) -> dict | None:
    from tagteam import db
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            return db.get_brief(conn, int(brief_id))
        finally:
            conn.close()
    except Exception:
        return None


def brief_current_payload(project_dir: str | Path, phase: str | None, ctype: str | None) -> dict:
    """The current escalation event's successful brief, or its attempt
    state — the same selection rule as `tagteam brief`."""
    from tagteam import db
    from tagteam import briefer
    if not phase or not ctype:
        return {"event": None, "reason": "no cycle selected", "brief": None, "attempts": []}
    ev, why = briefer.event_for_cycle(project_dir, phase, ctype)
    if ev is None:
        return {"event": None, "reason": why, "brief": None, "attempts": []}
    event = {"phase": ev.phase, "type": ev.type, "round": ev.round,
             "cycle_state": ev.cycle_state, "role": ev.role, "action": ev.action,
             "ts": ev.ts, "content": ev.content, "event_key": ev.event_key}
    brief = None
    attempts: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            brief = db.successful_brief_for_event(conn, ev.event_key)
            if brief is None:
                try:
                    from tagteam.config import read_config
                    cfg = read_config(Path(project_dir) / "tagteam.yaml") or {}
                    briefer.sweep_abandoned(project_dir, briefer.resolve_briefer(cfg, project_dir).timeout_s)
                except Exception:
                    pass
                attempts = db.briefs_for_event(conn, ev.event_key)
                for a in attempts:
                    a.pop("content", None)
        finally:
            conn.close()
    except Exception:
        pass
    return {"event": event, "reason": "ok", "brief": brief, "attempts": attempts}


# ---------------------------------------------------------------------------
# /api/usage
# ---------------------------------------------------------------------------

def usage_series(rows: list[dict]) -> list[dict]:
    """Per-turn series for the churn curve (oldest first)."""
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"), "ts": r.get("ts"), "phase": r.get("phase"),
            "type": r.get("type"), "round": r.get("round"), "role": r.get("role"),
            "agent": r.get("agent"), "provider": r.get("provider"), "status": r.get("status"),
            "input": r.get("input_tokens"), "output": r.get("output_tokens"),
            "cache_read": r.get("cache_read_tokens"), "cache_write": r.get("cache_write_tokens"),
            "cost": r.get("cost_usd"), "duration": r.get("duration_ms"),
        })
    return out


def usage_payload(project_dir: str | Path, phase: str | None = None,
                  ctype: str | None = None, role: str | None = None) -> dict:
    """`usage.aggregate()` roll-ups + by-agent buckets + per-turn series +
    the latest rate-limit signal(s)."""
    from tagteam import db
    from tagteam import usage as usage_mod
    rows: list[dict] = []
    limits: list[dict] = []
    try:
        conn = db.connect(project_dir=str(project_dir))
        try:
            rows = db.get_usage(conn, phase=phase, cycle_type=ctype)
            limits = db.latest_rate_limits(conn)
        finally:
            conn.close()
    except Exception:
        rows, limits = [], []
    if role:
        rows = [r for r in rows if r.get("role") == role]
    agg = usage_mod.aggregate(rows)
    by_agent: dict[str, dict] = {}
    for r in rows:
        key = f"{r.get('agent') or '?'} ({r.get('role') or '?'})"
        usage_mod._add(by_agent.setdefault(key, usage_mod._empty_bucket()), r)
    by_agent = {k: usage_mod._finish(v) for k, v in by_agent.items()}
    for lim in limits:
        lim["resets_in_s"] = None
        if lim.get("resets_at"):
            age = _age_s(lim["resets_at"])
            if age is not None:
                # _age_s clamps at 0 for the future; compute signed seconds.
                try:
                    t = datetime.fromisoformat(lim["resets_at"])
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    lim["resets_in_s"] = (t - _now()).total_seconds()
                except (TypeError, ValueError):
                    lim["resets_in_s"] = None
    return {
        "filter": {"phase": phase, "type": ctype, "role": role},
        "by_role": agg["by_role"],
        "by_cycle": agg["by_cycle"],
        "by_agent": by_agent,
        "totals": agg["totals"],
        "series": usage_series(rows),
        "rate_limits": limits,
    }


# ---------------------------------------------------------------------------
# /api/scope-diff
# ---------------------------------------------------------------------------

# Tagteam bookkeeping the cockpit never shows as phase work: the CLI's own
# artifact set (docs/handoffs/, handoff-state.json, …) plus the .tagteam/
# runtime directory, applied at FILE level after directory expansion.
_COCKPIT_ARTIFACT_PREFIXES = (".tagteam/",)


def is_cockpit_artifact(path: str) -> bool:
    from tagteam.cycle import _is_tagteam_artifact
    return _is_tagteam_artifact(path) or any(path.startswith(pre) for pre in _COCKPIT_ARTIFACT_PREFIXES)


def _git_out(project_dir: str, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", project_dir, *args], capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 1, ""


def _split_unified(diff_text: str) -> dict[str, str]:
    """Split a multi-file unified diff into {path: patch} by `diff --git`
    headers (path = the b/ side, or a/ side for deletions)."""
    out: dict[str, str] = {}
    cur_path = None
    cur: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur_path is not None:
                out[cur_path] = "".join(cur)
            cur = [line]
            rest = line[len("diff --git "):].rstrip("\n")
            # "a/<p> b/<p>" — take the b/ side (last token that starts with b/)
            b_idx = rest.rfind(" b/")
            cur_path = rest[b_idx + 3:] if b_idx >= 0 else rest
        else:
            cur.append(line)
    if cur_path is not None:
        out[cur_path] = "".join(cur)
    return out


def scope_diff_payload(project_dir: str | Path, phase: str, ctype: str, *,
                       max_bytes: int = SCOPE_DIFF_MAX_BYTES,
                       max_files: int = SCOPE_DIFF_MAX_FILES) -> dict:
    """Scope-diff paths + a capped, per-file `git diff` against the cycle
    baseline (committed and uncommitted changes together; untracked files
    as all-additions). Binary files are listed with `patch: null`.
    Truncation is flagged per file (`truncated`) and overall."""
    from tagteam.cycle import compute_scope_diff, ScopeDiffError
    root = str(Path(project_dir))
    try:
        info = compute_scope_diff(phase, ctype, root)
    except ScopeDiffError as e:
        return {"phase": phase, "type": ctype, "error": str(e), "paths": [],
                "files": [], "truncated": False, "omitted_files": 0, "baseline": None}
    paths = info["paths"]
    # Expand collapsed untracked directories (`git status --porcelain` lists
    # `newpkg/` for a whole new tree) into the files git would add
    # (respecting .gitignore), and drop Tagteam bookkeeping at file level.
    file_paths: list[str] = []
    for p in paths:
        if p.endswith("/"):
            rc, out = _git_out(root, "ls-files", "--others", "--exclude-standard", "-z", "--", p, timeout=20)
            members = [m for m in out.split("\0") if m] if rc == 0 else []
            if not members and (Path(root) / p).is_dir():
                # tracked-but-dirty dir won't appear collapsed; be safe anyway
                rc2, out2 = _git_out(root, "ls-files", "-z", "--", p, timeout=20)
                members = [m for m in out2.split("\0") if m] if rc2 == 0 else []
            file_paths.extend(members)
        else:
            file_paths.append(p)
    file_paths = sorted({fp for fp in file_paths if not is_cockpit_artifact(fp)})

    files: list[dict] = []
    total = 0
    truncated_any = False
    listed = file_paths[:max_files]
    omitted = max(0, len(file_paths) - len(listed))
    if omitted:
        truncated_any = True

    tracked: list[str] = []
    untracked: list[str] = []
    for p in listed:
        rc, _ = _git_out(root, "ls-files", "--error-unmatch", "--", p, timeout=10)
        # Only an untracked NEW file needs the --no-index path; anything the
        # index knows (or a staged deletion, which no longer exists on disk)
        # diffs against the baseline directly.
        if rc == 0 or not (Path(root) / p).exists():
            tracked.append(p)
        else:
            untracked.append(p)
    base = info["diff_base"]
    # Status vs the baseline: added if the path is absent from the baseline
    # tree, deleted if absent from the working tree, else modified.
    in_base: set[str] = set()
    if tracked:
        rc, out = _git_out(root, "ls-tree", "-r", "--name-only", "-z", base, "--", *tracked, timeout=20)
        if rc == 0:
            in_base = {m for m in out.split("\0") if m}

    numstat: dict[str, tuple[int | None, int | None]] = {}
    patches: dict[str, str] = {}
    if tracked:
        rc, out = _git_out(root, "diff", "--numstat", base, "--", *tracked)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    a, d, pth = parts[0], parts[1], "\t".join(parts[2:])
                    numstat[pth] = (None if a == "-" else int(a), None if d == "-" else int(d))
        rc, out = _git_out(root, "diff", base, "--", *tracked)
        if rc == 0:
            patches.update(_split_unified(out))
    for p in untracked:
        rc, out = _git_out(root, "diff", "--no-index", "--numstat", "--", os.devnull, p, timeout=10)
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                a, d = parts[0], parts[1]
                numstat[p] = (None if a == "-" else int(a), None if d == "-" else int(d))
        rc, out = _git_out(root, "diff", "--no-index", "--", os.devnull, p, timeout=10)
        if out:
            split = _split_unified(out)
            # --no-index headers name the temp path; take the only patch
            patches[p] = next(iter(split.values()), out) if split else out

    for p in listed:
        adds, dels = numstat.get(p, (None, None))
        binary = (p in numstat and adds is None and dels is None)
        patch = None if binary else patches.get(p)
        exists = (Path(root) / p).exists()
        if p in untracked:
            status = "untracked"
        elif not exists:
            status = "deleted"
        elif p not in in_base:
            status = "added"
        else:
            status = "modified"
        entry = {"path": p, "status": status, "binary": binary,
                 "additions": adds, "deletions": dels, "patch": None, "truncated": False,
                 "bytes": len(patch.encode("utf-8", "replace")) if patch else 0}
        if patch is not None:
            size = entry["bytes"]
            if total + size > max_bytes:
                room = max(0, max_bytes - total)
                entry["patch"] = patch.encode("utf-8", "replace")[:room].decode("utf-8", "replace")
                entry["truncated"] = True
                truncated_any = True
                total = max_bytes
            else:
                entry["patch"] = patch
                total += size
        files.append(entry)

    return {
        "phase": phase, "type": ctype, "error": None,
        "baseline": info["baseline"], "diff_base": base,
        "paths": paths, "file_paths": file_paths,
        "committed": info["committed"], "uncommitted": info["uncommitted"],
        "files": files, "truncated": truncated_any, "omitted_files": omitted,
        "bytes": total, "max_bytes": max_bytes, "max_files": max_files,
    }


# ---------------------------------------------------------------------------
# /api/tail
# ---------------------------------------------------------------------------

def tail_payload(project_dir: str | Path, lines: int = DEFAULT_TAIL_LINES,
                 events: bool = False) -> dict:
    """Last N lines of the in-flight turn log (or the most recent) — the
    same resolution as `tagteam tail --no-follow`."""
    root = Path(project_dir)
    lines = max(1, min(int(lines or DEFAULT_TAIL_LINES), MAX_TAIL_LINES))
    inflight = h.read_inflight(root)
    target = None
    if inflight is not None:
        try:
            target = Path(inflight["events_path" if events else "log_path"])
        except KeyError:
            target = None
    if target is None or not target.exists():
        target = h._latest_log(root, events)
    if target is None:
        return {"path": None, "lines": [], "inflight": inflight is not None,
                "stem": (inflight or {}).get("stem"), "message":
                "No headless turn logs found (.tagteam/turns/ is empty). "
                "Start the watcher with `tagteam watch --mode headless`."}
    text = h._tail_lines(target, lines)
    return {"path": str(target), "lines": text.splitlines() if text else [],
            "inflight": inflight is not None, "stem": (inflight or {}).get("stem"),
            "message": None}


# ---------------------------------------------------------------------------
# SSE signature
# ---------------------------------------------------------------------------

def events_signature(project_dir: str | Path) -> dict:
    """Cheap change signals sampled by the SSE loop: state seq, rounds count
    of the current cycle, max ids of interjections / briefs / usage /
    rate_limits, briefs status digest, pause marker, inflight stem+pid."""
    from tagteam.state import read_state
    root = Path(project_dir)
    state = read_state(str(root)) or {}
    phase, ctype = state.get("phase"), state.get("type")
    sig: dict = {"seq": state.get("seq"), "phase": phase, "type": ctype,
                 "turn": state.get("turn"), "status": state.get("status"),
                 "rounds": None, "interjections": None, "briefs": None,
                 "briefs_status": None, "usage": None, "rate_limits": None,
                 "paused": False, "inflight": None}
    if phase and ctype:
        for d in (root / "docs" / "handoffs", root / ".tagteam" / "legacy"):
            rp = d / f"{phase}_{ctype}_rounds.jsonl"
            if rp.exists():
                try:
                    st = rp.stat()
                    sig["rounds"] = [st.st_size, int(st.st_mtime_ns // 1_000_000)]
                except OSError:
                    pass
                break
    try:
        from tagteam import db
        conn = db.connect(project_dir=str(root))
        try:
            def _max(table):
                try:
                    return conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0]
                except Exception:
                    return None
            sig["interjections"] = _max("interjections")
            sig["briefs"] = _max("briefs")
            sig["usage"] = _max("usage")
            sig["rate_limits"] = _max("rate_limits")
            try:
                sig["briefs_status"] = conn.execute(
                    "SELECT COALESCE(SUM(CASE status WHEN 'running' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'ok' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'partial' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'failed' THEN 1 ELSE 0 END),0),"
                    " COALESCE(SUM(CASE status WHEN 'abandoned' THEN 1 ELSE 0 END),0)"
                    " FROM briefs").fetchone()
                sig["briefs_status"] = list(sig["briefs_status"]) if sig["briefs_status"] else None
            except Exception:
                sig["briefs_status"] = None
            # retirement / delivery change rows without changing max(id)
            try:
                sig["interjections_state"] = conn.execute(
                    "SELECT COALESCE(SUM(delivered_ts IS NOT NULL),0),"
                    " COALESCE(SUM(retired_ts IS NOT NULL),0) FROM interjections").fetchone()
                sig["interjections_state"] = list(sig["interjections_state"])
            except Exception:
                sig["interjections_state"] = None
            try:
                sig["rate_limits_ts"] = conn.execute("SELECT MAX(ts) FROM rate_limits").fetchone()[0]
            except Exception:
                sig["rate_limits_ts"] = None
        finally:
            conn.close()
    except Exception:
        pass
    sig["paused"] = h.pause_path(root).exists()
    inflight = h.read_inflight(root)
    if inflight is not None:
        pid = inflight.get("pid")
        alive = None
        try:
            from tagteam import procs
            alive = bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))
        except Exception:
            alive = None
        sig["inflight"] = {"stem": inflight.get("stem"), "pid": pid, "alive": alive,
                           "age_s": int(_age_s(inflight.get("started_at")) or 0)}
    # watcher liveness (pidfile pid alive?) — a watcher dying is a change too
    try:
        from tagteam import watcher as watcher_mod
        from tagteam import procs
        rec = watcher_mod.read_pidfile(root)
        if rec is not None:
            wpid = rec.get("pid")
            sig["watcher"] = {"pid": wpid, "alive": bool(isinstance(wpid, int) and wpid > 0
                                                          and procs.pid_alive(wpid))}
        else:
            sig["watcher"] = None
    except Exception:
        sig["watcher"] = None
    return sig


def signature_id(sig: dict) -> str:
    """Stable id for an SSE frame — hash of the signature WITHOUT the
    volatile inflight age (so an unchanged turn does not re-fire)."""
    stable = dict(sig)
    if stable.get("inflight"):
        stable["inflight"] = {k: v for k, v in stable["inflight"].items() if k != "age_s"}
    raw = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Write wrappers (thin: identical behavior to the CLI commands)
# ---------------------------------------------------------------------------

def _run(fn, args: list[str], project_dir: str | Path) -> dict:
    """Run a Phase 32/33 command function with a captured `out`; map the
    exit code to {ok, message}. Never raises."""
    buf = io.StringIO()
    try:
        rc = fn(args, project_root=str(project_dir), out=buf)
    except Exception as e:  # contract: never surfaces a traceback
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "rc": 1,
                "cli": _cli_line(fn, args)}
    return {"ok": rc == 0, "message": buf.getvalue().strip(), "rc": rc,
            "cli": _cli_line(fn, args)}


_FN_NAMES = {"pause_command": "pause", "resume_command": "resume",
             "interject_command": "interject", "cancel_turn_command": "cancel-turn",
             "rule_command": "rule", "brief_command": "brief"}


def _cli_line(fn, args: list[str]) -> str:
    import shlex
    name = _FN_NAMES.get(getattr(fn, "__name__", ""), getattr(fn, "__name__", "?"))
    return "tagteam " + " ".join([name] + [shlex.quote(a) for a in args])


def cli_preview(action: str, params: dict) -> str:
    """The exact CLI line an action would run (shown in confirmations)."""
    fn, args = _plan(action, params, by=web_user())
    return _cli_line(fn, args)


def _plan(action: str, params: dict, *, by: str):
    """Map (action, params) → (command function, argv). Raises ValueError
    for unknown actions / bad params (mapped to 400 by the server)."""
    from tagteam import controls, briefer
    params = params or {}

    def _s(key, default=""):
        v = params.get(key, default)
        return ("" if v is None else str(v)).strip()

    if action == "pause":
        args = ["--by", by]
        reason = _s("reason")
        if reason:
            args = ["--reason", reason] + args
        return controls.pause_command, args
    if action == "resume":
        return controls.resume_command, []
    if action == "interject":
        note = _s("note")
        if not note:
            raise ValueError("'note' is required")
        args = [note, "--by", by]
        to = _s("to")
        if to:
            if to not in ("lead", "reviewer"):
                raise ValueError("'to' must be lead or reviewer")
            args += ["--to", to]
        return controls.interject_command, args
    if action == "interject/retire":
        try:
            rid = int(params.get("id"))
        except (TypeError, ValueError):
            raise ValueError("'id' must be an integer")
        return controls.interject_command, ["--retire", str(rid), "--by", by]
    if action == "cancel-turn":
        return controls.cancel_turn_command, ["--by", by]
    if action == "brief/generate":
        return briefer.brief_command, ["--generate"]
    if action == "rule":
        verb = _s("ruling")
        if verb not in ("approve", "request-changes", "answer"):
            raise ValueError("'ruling' must be approve, request-changes or answer")
        args = [verb]
        content = _s("content")
        if content:
            args += ["--content", content]
        elif verb != "approve":
            raise ValueError(f"'{verb}' requires 'content'")
        args += ["--by", by]
        to = _s("to")
        if verb == "answer":
            args += ["--to", to or "reviewer"]
        elif to:
            raise ValueError("'to' only applies to answer")
        return controls.rule_command, args
    raise ValueError(f"Unknown action: {action}")


def run_action(action: str, params: dict, project_dir: str | Path,
               by: str | None = None) -> dict:
    """Perform a cockpit control. Returns {ok, message, rc, cli}. Unknown
    action / invalid params → {ok: False, error: ..., rc: 400}."""
    try:
        fn, args = _plan(action, params, by=by or web_user())
    except ValueError as e:
        return {"ok": False, "message": str(e), "rc": 400, "cli": None}
    return _run(fn, args, project_dir)
