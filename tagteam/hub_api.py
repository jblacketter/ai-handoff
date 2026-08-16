"""
Cross-project hub API (Phase 35): pure, READ-ONLY builders over every
registered project.

Hard rules (tested):
- Nothing here mutates another project: no `db.connect()` (it migrates),
  no `cycle.read_status()` / `read_rounds()` (DB-first via connect), no
  `cockpit_api.now_payload()`. State, cycle status and rounds are read from
  their canonical files; the DB is opened `mode=ro` via `read_only_connect`
  and only when it already exists (never created, never migrated).
- Nothing here mutates the registry: `registry.read_registry_raw()` only.
- Per-project failures land in the row (`error`), never raise.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tagteam import headless as h

# Defaults (Tesler: shipped, not configured)
STALE_AFTER_S = 30 * 60
ABANDONED_AFTER_S = 24 * 3600
SCRATCH_PREFIXES = ("/tmp/", "/private/tmp/", "/private/var/folders/", "/var/folders/")
USAGE_WINDOWS = {"24h": 24 * 3600, "7d": 7 * 24 * 3600, "all": None}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts) -> datetime | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _age_s(ts, now: datetime) -> float | None:
    t = _parse_ts(ts)
    return None if t is None else max(0.0, (now - t).total_seconds())


def project_id(path: str) -> str:
    """Short stable slug for a registry path: basename + 6-char hash."""
    p = str(path).rstrip("/\\")
    base = "".join(c if c.isalnum() or c in "-_" else "-" for c in Path(p).name)[:32] or "project"
    digest = hashlib.sha1(p.encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


# ---------------------------------------------------------------------------
# Registry classification
# ---------------------------------------------------------------------------

def is_scratch_path(path: str, scratch_prefixes: tuple[str, ...] = SCRATCH_PREFIXES) -> bool:
    p = str(path)
    if not p.endswith("/"):
        p += "/"
    return any(p.startswith(pre) for pre in scratch_prefixes)


def classify_registry(paths: list[str], *,
                      scratch_prefixes: tuple[str, ...] = SCRATCH_PREFIXES) -> list[dict]:
    """[{path, id, kind: ok|legacy|missing|no-yaml|scratch, hidden: bool}]
    in registry order. Pure: never touches the registry file."""
    out = []
    for raw in paths:
        p = Path(raw)
        entry = {"path": raw, "id": project_id(raw), "kind": "ok", "hidden": False}
        if not p.is_dir():
            entry.update(kind="missing", hidden=True)
        elif is_scratch_path(raw, scratch_prefixes):
            entry.update(kind="scratch", hidden=True)
        elif not (p / "tagteam.yaml").is_file():
            # A pre-tagteam.yaml (legacy) project with a live handoff state
            # is still a project — hide only when there is nothing tagteam
            # about the directory at all.
            if (p / "handoff-state.json").is_file():
                entry.update(kind="legacy", hidden=False)
            else:
                entry.update(kind="no-yaml", hidden=True)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Read-only readers
# ---------------------------------------------------------------------------

def read_only_connect(project_dir: str | Path) -> sqlite3.Connection | None:
    """`mode=ro` connection to an EXISTING project DB, or None. Never creates
    `.tagteam/` or the file, never migrates. Caller closes."""
    db_path = Path(project_dir) / ".tagteam" / "tagteam.db"
    if not db_path.is_file():
        return None
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.DatabaseError as e:
        conn.close()
        raise ProjectDataError(f"db: {e}")
    return conn


class ProjectDataError(Exception):
    """A project file exists but cannot be read/parsed (malformed JSON,
    unreadable, corrupt DB). Distinct from *absence*, which is normal
    (older projects, no cycle yet) and yields nulls."""


def _read_json(path: Path) -> dict | None:
    """Parsed dict, None if the file is ABSENT; `ProjectDataError` if it
    exists but is unreadable / not valid JSON / not an object."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ProjectDataError(f"{path.name}: unreadable ({e.__class__.__name__})")
    except ValueError as e:
        raise ProjectDataError(f"{path.name}: malformed JSON ({e})")
    if not isinstance(data, dict):
        raise ProjectDataError(f"{path.name}: expected a JSON object")
    return data


def read_state_file(project_dir: str | Path) -> dict | None:
    return _read_json(Path(project_dir) / "handoff-state.json")


def read_cycle_status_file(project_dir: str | Path, phase: str, ctype: str) -> dict | None:
    """Canonical per-cycle status from docs/handoffs/ (or .tagteam/legacy/)."""
    root = Path(project_dir)
    for d in (root / "docs" / "handoffs", root / ".tagteam" / "legacy"):
        p = d / f"{phase}_{ctype}_status.json"
        if p.is_file():
            return _read_json(p)
    return None


def _rounds_file(project_dir: str | Path, phase: str, ctype: str) -> Path | None:
    root = Path(project_dir)
    for d in (root / "docs" / "handoffs", root / ".tagteam" / "legacy"):
        p = d / f"{phase}_{ctype}_rounds.jsonl"
        if p.is_file():
            return p
    return None


def _last_round_entry(project_dir, phase, ctype) -> dict | None:
    p = _rounds_file(project_dir, phase, ctype)
    if p is None:
        return None
    try:
        last = None
        with open(p, "rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        return json.loads(last) if last else None
    except (OSError, ValueError):
        return None


def _event_key(phase, ctype, status, last) -> str | None:
    """Same formula as `briefer.event_for_cycle` (repair-safe, file-derived)."""
    if not last:
        return None
    rnd = int(status.get("round") or last.get("round") or 0)
    return f"{phase}|{ctype}|{rnd}|{last.get('role')}|{last.get('action')}|{last.get('ts')}"


def _query(conn, sql, params=()) -> list[sqlite3.Row]:
    """Run a read. A missing table/column (an older schema) is EXPECTED →
    `[]`; anything else (corrupt file, locked beyond timeout, I/O) is a
    `ProjectDataError` so the row shows it."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no such table" in msg or "no such column" in msg:
            return []
        raise ProjectDataError(f"db: {e}")
    except sqlite3.DatabaseError as e:
        raise ProjectDataError(f"db: {e}")


# ---------------------------------------------------------------------------
# Per-project summary
# ---------------------------------------------------------------------------

def project_summary(project_dir: str | Path, *, now: datetime | None = None,
                    procs_snapshot: list[tuple[int, str]] | None = None) -> dict:
    """Everything the hub row shows for one project. Never raises."""
    now = now or _now()
    root = Path(project_dir)
    row: dict = {"path": str(root), "id": project_id(str(root)), "name": root.name,
                 "parent": root.parent.name, "error": None,
                 "state": None, "cycle_state": None, "phase": None, "type": None,
                 "round": None, "turn": None, "status": None, "result": None,
                 "owed_age_s": None, "last_activity": None, "last_activity_age_s": None,
                 "paused": None, "inflight": None, "watcher": None,
                 "brief_ready": None, "brief_attempts": None, "usage": None}
    try:
        st = read_state_file(root)
        row["state"] = bool(st)
        if st:
            row.update(phase=st.get("phase"), type=st.get("type"), round=st.get("round"),
                       turn=st.get("turn"), status=st.get("status"), result=st.get("result"),
                       last_activity=st.get("updated_at"))
            row["last_activity_age_s"] = _age_s(st.get("updated_at"), now)
            if st.get("status") in ("ready", "working") and st.get("turn") in ("lead", "reviewer"):
                row["owed_age_s"] = _age_s(st.get("updated_at"), now)
        cyc = None
        if row["phase"] and row["type"]:
            cyc = read_cycle_status_file(root, row["phase"], row["type"])
            row["cycle_state"] = (cyc or {}).get("state")
        paused = _read_json(h.pause_path(root))
        if paused is not None:
            row["paused"] = {"reason": paused.get("reason"), "by": paused.get("by"),
                             "outcome": paused.get("outcome"), "ts": paused.get("ts"),
                             "age_s": _age_s(paused.get("ts"), now)}
        inflight = _read_json(h.inflight_path(root))
        if inflight is not None:
            pid = inflight.get("pid")
            alive = None
            try:
                from tagteam import procs
                alive = bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))
            except Exception:
                alive = None
            row["inflight"] = {"stem": inflight.get("stem"), "pid": pid, "alive": alive,
                               "role": inflight.get("role"), "agent": inflight.get("agent"),
                               "age_s": _age_s(inflight.get("started_at"), now)}
        try:
            from tagteam import cockpit_api as capi
            row["watcher"] = capi.watcher_status(root, inflight, procs_snapshot=procs_snapshot)
        except Exception as e:  # never fail the row on process inspection
            row["watcher"] = {"running": False, "pid": None, "mode": None, "source": None,
                              "stale_pidfile": False, "error": str(e)}
        # Phase 37: the same launch intent the cockpit's Start card renders,
        # from what this row already read (files only — read-only stays read-only)
        try:
            from tagteam import launch as _launch
            row["intent"] = _launch.launch_intent(root, state=st or {}, cycle_status=cyc,
                                                  paused=paused, _prefetched=True)
        except Exception as e:
            row["intent"] = {"phase": None, "type": None, "command": None, "reason": f"intent unavailable: {e}"}
        # DB-derived (read-only, only if the DB exists)
        conn = read_only_connect(root)
        if conn is not None:
            try:
                if cyc and cyc.get("state") in ("escalated", "needs-human"):
                    last = _last_round_entry(root, row["phase"], row["type"])
                    key = _event_key(row["phase"], row["type"], cyc, last)
                    if key:
                        ok = _query(conn, "SELECT id FROM briefs WHERE event_key=? AND status IN ('ok','partial') "
                                          "ORDER BY id DESC LIMIT 1", (key,))
                        row["brief_ready"] = bool(ok)
                        row["brief_attempts"] = [r["status"] for r in _query(
                            conn, "SELECT status FROM briefs WHERE event_key=? ORDER BY id", (key,))]
                tot = _query(conn, "SELECT COUNT(*) AS n, COALESCE(SUM(input_tokens),0) AS i, "
                                   "COALESCE(SUM(output_tokens),0) AS o, COALESCE(SUM(cost_usd),0.0) AS c, "
                                   "MAX(ts) AS last FROM usage")
                if tot:
                    r = tot[0]
                    row["usage"] = {"turns": r["n"], "input_tokens": r["i"], "output_tokens": r["o"],
                                    "cost_usd": r["c"], "last_ts": r["last"]}
                    if r["last"] and (row["last_activity"] is None or str(r["last"]) > str(row["last_activity"])):
                        pass  # state updated_at remains the "activity" clock; usage is informational
            finally:
                conn.close()
    except ProjectDataError as e:  # broken data → visible on the row
        row["error"] = str(e)
    except Exception as e:  # contract: never raise
        row["error"] = f"{type(e).__name__}: {e}"
    return row


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _live(row: dict) -> bool:
    inf = row.get("inflight") or {}
    w = row.get("watcher") or {}
    return bool(inf.get("alive")) or bool(w.get("running"))


def classify_row(row: dict, *, stale_after_s: int = STALE_AFTER_S,
                 abandoned_after_s: int = ABANDONED_AFTER_S) -> dict:
    """Adds `group` (needs_you | waiting | quiet), `why`, `stale`,
    `abandoned`, `live`, `hint`. Pure."""
    row = dict(row)
    live = _live(row)
    row["live"] = live
    row["stale"] = False
    row["abandoned"] = False
    row["hint"] = None
    cs = row.get("cycle_state")
    paused = row.get("paused") or {}
    if cs in ("escalated", "needs-human"):
        row["group"] = "needs_you"
        why = "escalated" if cs == "escalated" else "question"
        if row.get("round") is not None:
            why += f" r{row['round']}"
        if cs == "escalated":
            why += " · brief ready" if row.get("brief_ready") else (
                " · brief " + ", ".join(row["brief_attempts"]) if row.get("brief_attempts") else " · no brief")
        row["why"] = why
        row["hint"] = "tagteam brief / tagteam rule … (or Open → Needs you)"
        return row
    if paused and paused.get("outcome"):
        row["group"] = "needs_you"
        row["why"] = f"paused: {paused.get('outcome')} — {paused.get('reason') or ''}".strip(" —")
        row["hint"] = "tagteam resume (after reading the turn log)"
        return row
    if row.get("owed_age_s") is not None:
        row["group"] = "waiting"
        age = row["owed_age_s"]
        row["why"] = f"{row.get('turn')} owed"
        if paused:
            row["why"] += " · paused"
            row["hint"] = "tagteam resume"
        if not live and age >= stale_after_s:
            row["stale"] = True
            row["abandoned"] = age >= abandoned_after_s
            row["hint"] = row["hint"] or "tagteam watch --mode headless (nothing is dispatching)"
        return row
    row["group"] = "quiet"
    st, res = row.get("status"), row.get("result")
    row["why"] = (f"{st}" + (f" — {res}" if res else "")) if st else ("no cycle" if row.get("state") else "no state")
    return row


def _sort_key_needs(r):
    return (0 if r.get("cycle_state") == "escalated" else 1 if r.get("cycle_state") == "needs-human" else 2,
            -(r.get("last_activity_age_s") or 0))


def _sort_key_waiting(r):
    return (0 if r.get("abandoned") else 1 if r.get("stale") else 2, -(r.get("owed_age_s") or 0))


def _sort_key_quiet(r):
    return (r.get("last_activity_age_s") if r.get("last_activity_age_s") is not None else float("inf"))


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

def aggregate_usage(project_dirs: list[str], *, now: datetime | None = None,
                    windows: dict | None = None) -> dict:
    """{window: {turns, input_tokens, output_tokens, cache_read_tokens,
    cost_usd, priced_turns, projects}} summed over projects (read-only)."""
    now = now or _now()
    windows = windows or USAGE_WINDOWS
    out = {w: {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
               "cost_usd": 0.0, "priced_turns": 0, "projects": 0} for w in windows}
    for d in project_dirs:
        try:
            conn = read_only_connect(d)
        except ProjectDataError:
            continue  # the row itself carries the error
        if conn is None:
            continue
        try:
            for w, secs in windows.items():
                if secs is None:
                    rows = _query(conn, "SELECT COUNT(*) n, COALESCE(SUM(input_tokens),0) i, "
                                        "COALESCE(SUM(output_tokens),0) o, COALESCE(SUM(cache_read_tokens),0) cr, "
                                        "COALESCE(SUM(cost_usd),0.0) c, "
                                        "COALESCE(SUM(cost_usd IS NOT NULL),0) pc FROM usage")
                else:
                    since = (now - timedelta(seconds=secs)).isoformat()
                    rows = _query(conn, "SELECT COUNT(*) n, COALESCE(SUM(input_tokens),0) i, "
                                        "COALESCE(SUM(output_tokens),0) o, COALESCE(SUM(cache_read_tokens),0) cr, "
                                        "COALESCE(SUM(cost_usd),0.0) c, "
                                        "COALESCE(SUM(cost_usd IS NOT NULL),0) pc FROM usage WHERE ts >= ?",
                                  (since,))
                if rows:
                    r = rows[0]
                    b = out[w]
                    b["turns"] += r["n"]; b["input_tokens"] += r["i"]; b["output_tokens"] += r["o"]
                    b["cache_read_tokens"] += r["cr"]; b["cost_usd"] += float(r["c"] or 0.0)
                    b["priced_turns"] += r["pc"]
                    if r["n"]:
                        b["projects"] += 1
        except ProjectDataError:
            pass
        finally:
            conn.close()
    for b in out.values():
        b["cost_usd"] = round(b["cost_usd"], 6)
    return out


def shared_rate_limits(project_dirs: list[str]) -> list[dict]:
    """Newest row per (provider, kind) across projects — the subscription
    window is one pool per provider, but `five_hour` and `seven_day` are
    distinct windows. Tie-break: later `ts`, then registry order (earlier
    wins). [{provider, kind, status, resets_at, ts, project}]"""
    best: dict[tuple[str, str], dict] = {}
    for d in project_dirs:
        try:
            conn = read_only_connect(d)
        except ProjectDataError:
            continue
        if conn is None:
            continue
        try:
            rows = _query(conn, "SELECT provider, kind, status, resets_at, ts FROM rate_limits")
        except ProjectDataError:
            rows = []
        try:
            for r in rows:
                key = (r["provider"], r["kind"])
                cand = {"provider": r["provider"], "kind": r["kind"], "status": r["status"],
                        "resets_at": r["resets_at"], "ts": r["ts"], "project": str(d)}
                cur = best.get(key)
                if cur is None or str(cand["ts"] or "") > str(cur["ts"] or ""):
                    best[key] = cand
        finally:
            conn.close()
    return [best[k] for k in sorted(best)]


# ---------------------------------------------------------------------------
# Hub payload
# ---------------------------------------------------------------------------

def hub_payload(paths: list[str], *, now: datetime | None = None, show_all: bool = False,
                stale_after_s: int = STALE_AFTER_S, abandoned_after_s: int = ABANDONED_AFTER_S,
                procs_snapshot: list[tuple[int, str]] | None = None,
                scratch_prefixes: tuple[str, ...] = SCRATCH_PREFIXES) -> dict:
    now = now or _now()
    if procs_snapshot is None:
        try:
            from tagteam import procs, cockpit_api as capi
            procs_snapshot = procs.list_processes(capi.WATCH_ARGV_RE.pattern)
        except Exception:
            procs_snapshot = []
    entries = classify_registry(paths, scratch_prefixes=scratch_prefixes)
    groups = {"needs_you": [], "waiting": [], "quiet": [], "hidden": []}
    visible_dirs: list[str] = []
    for e in entries:
        if e["hidden"] and not show_all:
            groups["hidden"].append({"path": e["path"], "id": e["id"], "kind": e["kind"]})
            continue
        if e["kind"] == "missing":
            groups["hidden"].append({"path": e["path"], "id": e["id"], "kind": e["kind"]})
            continue
        row = classify_row(project_summary(e["path"], now=now, procs_snapshot=procs_snapshot),
                           stale_after_s=stale_after_s, abandoned_after_s=abandoned_after_s)
        row["kind"] = e["kind"]
        groups[row["group"]].append(row)
        visible_dirs.append(e["path"])
    groups["needs_you"].sort(key=_sort_key_needs)
    groups["waiting"].sort(key=_sort_key_waiting)
    groups["quiet"].sort(key=_sort_key_quiet)
    live = sum(1 for g in ("needs_you", "waiting", "quiet") for r in groups[g] if r.get("live"))
    # The subscription window is ONE pool per provider: read the signal from
    # every readable registered project DB, hidden or not (display
    # visibility scopes the list and the burn totals, not the account).
    all_dirs = [e["path"] for e in entries if e["kind"] != "missing"]
    return {
        "ts": now.isoformat(),
        "registry": {"total": len(entries), "visible": len(visible_dirs),
                     "hidden": len(groups["hidden"]), "show_all": show_all},
        "groups": groups,
        "totals": {"projects": len(visible_dirs), "live": live,
                   "needs_you": len(groups["needs_you"]), "waiting": len(groups["waiting"]),
                   "quiet": len(groups["quiet"]),
                   "stale": sum(1 for r in groups["waiting"] if r.get("stale"))},
        "usage": aggregate_usage(visible_dirs, now=now),          # burn: visible projects
        "rate_limits": shared_rate_limits(all_dirs),               # window: every registered project
        "thresholds": {"stale_after_s": stale_after_s, "abandoned_after_s": abandoned_after_s},
    }


# ---------------------------------------------------------------------------
# SSE signature (exhaustive, cheap: files + one shared process snapshot)
# ---------------------------------------------------------------------------

def _stat_sig(p: Path):
    """Size + mtime(ns) — for files that only grow (rounds, DB, WAL)."""
    try:
        st = p.stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def _hash_sig(p: Path, limit: int = 256 * 1024):
    """Content hash — for small files that are rewritten in place (state,
    cycle status, registry): two rewrites within one filesystem timestamp
    tick with equal size would otherwise look identical."""
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) > limit:
        return _stat_sig(p)
    return hashlib.sha1(data).hexdigest()[:12]


def hub_signature(paths: list[str], registry_file: Path | None = None,
                  procs_snapshot: list[tuple[int, str]] | None = None) -> dict:
    """Change signals for every value `/api/hub` shows, without opening a
    DB: registry file; per project — state file, current-cycle status +
    rounds files, pause marker, inflight stem/pid/alive, watcher pidfile
    alive, watcher liveness from the SHARED process snapshot, DB + -wal
    file stats."""
    from tagteam import procs, watcher as watcher_mod
    from tagteam import cockpit_api as capi
    if procs_snapshot is None:
        try:
            procs_snapshot = procs.list_processes(capi.WATCH_ARGV_RE.pattern)
        except Exception:
            procs_snapshot = []
    sig: dict = {"registry": _hash_sig(registry_file) if registry_file else None, "projects": {}}
    for raw in paths:
        root = Path(raw)
        if not root.is_dir():
            sig["projects"][raw] = None
            continue
        ps: dict = {"state": _hash_sig(root / "handoff-state.json"),
                    # marker files are rewritten in place (reason/outcome, pid,
                    # stem, started_at, role, agent…): hash their content
                    "paused": _hash_sig(root / ".tagteam" / "headless-paused.json"),
                    "inflight_file": _hash_sig(h.inflight_path(root)),
                    "pidfile": _hash_sig(watcher_mod.pidfile_path(root)),
                    "db": _stat_sig(root / ".tagteam" / "tagteam.db"),
                    "wal": _stat_sig(root / ".tagteam" / "tagteam.db-wal")}
        try:
            st = read_state_file(root)
        except ProjectDataError as e:
            st = None
            ps["state_error"] = str(e)
        if st and st.get("phase") and st.get("type"):
            for d in (root / "docs" / "handoffs", root / ".tagteam" / "legacy"):
                sp = d / f"{st['phase']}_{st['type']}_status.json"
                if sp.exists():
                    ps["cycle"] = _hash_sig(sp)
                    ps["rounds"] = _stat_sig(d / f"{st['phase']}_{st['type']}_rounds.jsonl")
                    break
        inflight = h.read_inflight(root)
        if inflight:
            pid = inflight.get("pid")
            ps["inflight"] = {"stem": inflight.get("stem"), "pid": pid,
                              "alive": bool(isinstance(pid, int) and pid > 0 and procs.pid_alive(pid))}
        else:
            ps["inflight"] = None
        rec = watcher_mod.read_pidfile(root)
        wpid = rec.get("pid") if rec else None
        ps["pidfile_alive"] = bool(isinstance(wpid, int) and wpid > 0 and procs.pid_alive(wpid)) if rec else None
        try:
            ws = capi.watcher_status(root, inflight, procs_snapshot=procs_snapshot)
            # every watcher field the row displays
            ps["watcher"] = [ws.get("running"), ws.get("pid"), ws.get("mode"), ws.get("source"),
                             ws.get("stale_pidfile")]
        except Exception:
            ps["watcher"] = None
        sig["projects"][raw] = ps
    return sig


def signature_id(sig: dict) -> str:
    raw = json.dumps(sig, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Text rendering (`tagteam hub --list`)
# ---------------------------------------------------------------------------

def _fmt_age(s) -> str:
    if s is None:
        return "?"
    s = int(s)
    d, rem = divmod(s, 86400)
    hh, rem = divmod(rem, 3600)
    mm = rem // 60
    if d:
        return f"{d}d{hh:02d}h"
    if hh:
        return f"{hh}h{mm:02d}m"
    return f"{mm}m"


def _short(path: str) -> str:
    p = Path(path)
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name


def render_text(payload: dict) -> str:
    g = payload["groups"]; t = payload["totals"]
    lines = [f"Tagteam hub — {t['projects']} project(s) visible, {t['live']} live, "
             f"{payload['registry']['hidden']} hidden ({'shown' if payload['registry']['show_all'] else 'use --all'})"]
    rl = payload.get("rate_limits") or []
    if rl:
        lines.append("Subscription window: " + "; ".join(
            f"{r['provider']} {r['kind']}: {r['status']} (resets {r['resets_at'] or '?'})" for r in rl))
    else:
        lines.append("Subscription window: n/a (no rate-limit signal recorded)")
    u = payload.get("usage") or {}
    if u:
        lines.append("Burn: " + " · ".join(
            f"{w}: {b['turns']} turns, in {b['input_tokens']:,} / out {b['output_tokens']:,}"
            + (f", ${b['cost_usd']:.2f}" if b['priced_turns'] else "") for w, b in u.items()))

    def block(title, rows, fmt):
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        if not rows:
            lines.append("  —")
        for r in rows:
            lines.append("  " + fmt(r))

    block("NEEDS YOU", g["needs_you"], lambda r: (
        f"{_short(r['path']):40} {r.get('phase') or '-'} {r.get('type') or ''} r{r.get('round') or '-'}  "
        f"{r['why']}  [{_fmt_age(r.get('last_activity_age_s'))} ago]  → {r['hint'] or ''}"))
    block("WAITING", g["waiting"], lambda r: (
        f"{_short(r['path']):40} {r.get('phase') or '-'} {r.get('type') or ''} r{r.get('round') or '-'}  "
        f"{r['why']} {_fmt_age(r.get('owed_age_s'))}"
        + (" ABANDONED?" if r.get('abandoned') else " STALE" if r.get('stale') else "")
        + (" · in flight" if (r.get('inflight') or {}).get('alive') else "")
        + (" · watcher" if (r.get('watcher') or {}).get('running') else "")
        + (f"  → {r['hint']}" if r.get('hint') else "")))
    block("QUIET", g["quiet"], lambda r: (
        f"{_short(r['path']):40} {r.get('phase') or '-'} {r.get('type') or ''} r{r.get('round') or '-'}  "
        f"{r['why']}  [{_fmt_age(r.get('last_activity_age_s'))} ago]"
        + (f"  ! {r['error']}" if r.get('error') else "")))
    if payload["registry"]["show_all"] or g["hidden"]:
        lines.append("")
        lines.append(f"HIDDEN ({len(g['hidden'])})" + ("" if payload["registry"]["show_all"] else " — use --all to include"))
        for r in g["hidden"][: 50 if payload["registry"]["show_all"] else 0]:
            lines.append(f"  {r['path']}  ({r['kind']})")
    return "\n".join(lines)
