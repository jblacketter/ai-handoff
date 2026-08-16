"""Phase 37 — launchpad: the *launch intent* state machine, the Start-card
payload, and the composite, idempotent `launch` operation the cockpit's
primary Start button runs.

Launch intent (one function, one consumer set — cockpit card, terminal
copy command, hub row, `launch`):

  observed                                  → intent
  no state / no cycle                        → next actionable roadmap phase, plan
  current cycle `plan` approved / done       → SAME phase, impl
  current cycle `impl` approved / done       → next actionable phase AFTER it, plan
  ready / in-progress / escalated / needs-human / paused → none (reason)
  roadmap exhausted / no actionable phase    → none (never fabricated)
  setup missing (tagteam.yaml / roadmap)     → none (quickstart hint)

"Actionable" is decided by `roadmap.is_terminal_status` (Complete /
✅ Complete… / Absorbed / Deferred / Superseded are terminal, normalized).

The composite `launch` persists its claim in `launches` (UNIQUE key =
sha256(intent.command + observed)) under a short writer-lock hold, does
every side effect (watcher spawn + readiness wait, the lead's first
conversation turn) OUTSIDE the lock, writes each step's reference to the
row as soon as it exists, and reconciles orphaned `pending` rows from
those references. `retry` is an atomic failed→pending transition.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tagteam import procs

TERMINAL_CYCLE_STATES = ("approved", "done")
BLOCKING_CYCLE_STATES = ("in-progress", "ready", "escalated", "needs-human", "working")
WATCHER_READY_WAIT_S = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------ intent ----

def _cycle_status(root: Path, phase: str | None, ctype: str | None) -> dict | None:
    if not phase or not ctype:
        return None
    try:
        from tagteam.hub_api import read_cycle_status_file
        return read_cycle_status_file(root, phase, ctype)
    except Exception:
        try:
            from tagteam.cycle import read_status
            return read_status(phase, ctype, str(root))
        except Exception:
            return None


def _actionable_phases(root: Path) -> list:
    from tagteam import roadmap
    rp = root / "docs" / "roadmap.md"
    try:
        allp = roadmap.parse_roadmap(rp)
    except (FileNotFoundError, ValueError):
        return []
    return [p for p in allp if not roadmap.is_terminal_status(p.status)]


def _next_after(root: Path, phase: str | None) -> tuple:
    """(RoadmapPhase | None, roadmap_present). Next actionable phase after
    `phase` (by name/slug), or the first actionable one when `phase` is
    None or unknown; the just-approved phase itself is skipped by name."""
    from tagteam import roadmap
    rp = root / "docs" / "roadmap.md"
    if not rp.exists():
        return None, False
    try:
        allp = roadmap.parse_roadmap(rp)
    except ValueError:
        return None, True
    start = 0
    if phase:
        for i, p in enumerate(allp):
            if p.slug == phase or roadmap._slugify(p.name) == phase:
                start = i + 1
                break
    for p in allp[start:]:
        if p.slug != phase and not roadmap.is_terminal_status(p.status):
            return p, True
    return None, True


def launch_intent(project_dir: str | Path, *, state: dict | None = None,
                  cycle_status: dict | None = None, paused: dict | None = None,
                  _prefetched: bool = False) -> dict:
    """See module docstring. Always returns {phase, type, command, reason,
    observed} — `command` is None when nothing may start. Read-only callers
    (the hub) pass what they already read (`_prefetched=True`)."""
    from tagteam.state import read_state
    root = Path(project_dir)
    observed = {"seq": None, "phase": None, "type": None, "round": None, "state": None}
    if not (root / "tagteam.yaml").exists():
        return {"phase": None, "type": None, "command": None, "observed": observed,
                "reason": "not set up: no tagteam.yaml — run `tagteam quickstart`"}
    if not (root / "docs" / "roadmap.md").exists():
        return {"phase": None, "type": None, "command": None, "observed": observed,
                "reason": "not set up: no docs/roadmap.md — run `tagteam quickstart`"}
    if _prefetched:
        st = state or {}
        cs = cycle_status
    else:
        try:
            st = read_state(str(root)) or {}
        except Exception:
            st = {}
        cs = _cycle_status(root, st.get("phase"), st.get("type")) if st.get("phase") else None
        try:
            from tagteam.headless import read_pause
            paused = read_pause(root)
        except Exception:
            paused = None
    observed["seq"] = st.get("seq")
    phase, ctype = st.get("phase"), st.get("type")
    cstate = (cs or {}).get("state") if cs else None
    observed.update({"phase": phase, "type": ctype, "round": (cs or {}).get("round") if cs else st.get("round"),
                     "state": cstate or st.get("status")})
    if paused:
        return {"phase": phase, "type": ctype, "command": None, "observed": observed,
                "reason": f"dispatch is paused ({paused.get('reason', 'see .tagteam/headless-paused.json')}) — resume first"}
    if phase and cstate in BLOCKING_CYCLE_STATES:
        turn = st.get("turn") or "?"
        return {"phase": phase, "type": ctype, "command": None, "observed": observed,
                "reason": f"a cycle is in progress ({phase} · {ctype} · {cstate}; turn: {turn})"}
    if phase and cstate in TERMINAL_CYCLE_STATES and ctype == "plan":
        return {"phase": phase, "type": "impl", "command": f"/handoff start {phase} impl",
                "observed": observed, "reason": "plan approved — implement it"}
    if phase and cstate in TERMINAL_CYCLE_STATES and ctype == "impl":
        nxt, present = _next_after(root, phase)
        if nxt is None:
            return {"phase": None, "type": None, "command": None, "observed": observed,
                    "reason": "no actionable phase left in docs/roadmap.md"}
        return {"phase": nxt.slug, "type": "plan", "command": f"/handoff start {nxt.slug}",
                "observed": observed, "reason": f"{phase} approved — next phase"}
    if phase and cs is None and st.get("status") in ("ready", "working"):
        return {"phase": phase, "type": ctype, "command": None, "observed": observed,
                "reason": f"a cycle is in progress ({phase} · {ctype}; turn: {st.get('turn') or '?'})"}
    # no state / no cycle
    nxt, present = _next_after(root, None)
    if nxt is None:
        return {"phase": None, "type": None, "command": None, "observed": observed,
                "reason": "no actionable phase in docs/roadmap.md"}
    return {"phase": nxt.slug, "type": "plan", "command": f"/handoff start {nxt.slug}",
            "observed": observed, "reason": "no cycle in progress"}


def launch_key(intent: dict) -> str:
    obs = intent.get("observed") or {}
    raw = json.dumps({"command": intent.get("command"), "observed": obs}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------- start payload ----

def _headless_ok(root: Path, config: dict | None) -> tuple[bool, list[str]]:
    from tagteam.headless import HeadlessEngine
    try:
        from tagteam.config import get_agent_names
        lead, rev = get_agent_names(config or {})
        lead, rev = lead or "lead", rev or "reviewer"
    except Exception:
        lead, rev = "lead", "reviewer"
    try:
        eng = HeadlessEngine(root, config, lead_name=lead, reviewer_name=rev)
        errs = eng.validate()
    except Exception as e:  # never surface a traceback
        errs = [f"{type(e).__name__}: {e}"]
    return (not errs), errs


def start_payload(project_dir: str | Path, config: dict | None = None) -> dict:
    from tagteam.cockpit_api import watcher_status
    root = Path(project_dir)
    intent = launch_intent(root)
    setup_ok = (root / "tagteam.yaml").exists() and (root / "docs" / "roadmap.md").exists()
    hok, herrs = _headless_ok(root, config) if setup_ok else (False, ["not set up"])
    watcher = watcher_status(root)
    recommended = "headless" if hok else "interactive"
    cmd = intent.get("command")
    commands = {
        "headless": (["tagteam watch --mode headless --pidfile", f"tagteam lead {json.dumps(cmd)}"] if cmd else []),
        "interactive": (["tagteam session start", f"paste into the Lead: {cmd}"] if cmd else []),
    }
    return {"intent": intent, "setup_ok": setup_ok,
            "headless": {"ok": hok, "errors": herrs}, "watcher": watcher,
            "recommended": recommended, "commands": commands}


# --------------------------------------------------------- watcher ops ----

def _tagteam_argv() -> list[str]:
    return [sys.executable, "-m", "tagteam"]


def start_watcher(project_dir: str | Path, *, mode: str = "headless",
                  wait_s: float = WATCHER_READY_WAIT_S) -> dict:
    """Spawn `tagteam watch --mode <mode> --pidfile` detached; wait ≤ wait_s
    for an identity-bound pidfile OR an early exit. Returns {ok, pid,
    message, log}. Refuses when a watcher already runs."""
    from tagteam.cockpit_api import watcher_status
    from tagteam.watcher import read_pidfile
    root = Path(project_dir)
    ws = watcher_status(root)
    if ws.get("running"):
        return {"ok": False, "already": True, "pid": ws.get("pid"), "mode": ws.get("mode"),
                "message": f"a watcher is already running (pid {ws.get('pid')}, {ws.get('mode') or '?'})"}
    if mode not in ("headless", "notify", "iterm2", "tmux"):
        return {"ok": False, "message": f"invalid watcher mode {mode!r}"}
    log = root / ".tagteam" / f"watcher-{mode}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    kw: dict = {}
    if sys.platform == "win32":  # pragma: no cover
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    with log.open("ab") as lf:
        try:
            proc = subprocess.Popen(_tagteam_argv() + ["watch", "--mode", mode, "--pidfile"],
                                    cwd=str(root), stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                    env=env, **kw)
        except OSError as e:
            return {"ok": False, "message": f"could not start the watcher: {e}", "log": str(log)}
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            tail = ""
            try:
                tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
            except OSError:
                pass
            return {"ok": False, "exited": rc, "message": f"the watcher exited immediately (code {rc}) — "
                    f"it rejected its configuration; see {log}", "log": str(log), "log_tail": tail}
        rec = read_pidfile(root)
        if rec and rec.get("pid") == proc.pid and rec.get("ident") and procs.identity(proc.pid) == rec.get("ident"):
            return {"ok": True, "pid": proc.pid, "mode": mode, "message": f"watcher started (pid {proc.pid}, {mode})",
                    "log": str(log)}
        time.sleep(0.1)
    # still running but no pidfile yet: report truthfully
    return {"ok": False, "pid": proc.pid, "message": f"watcher process {proc.pid} started but did not report "
            f"a pidfile within {wait_s:.0f}s — check {log}", "log": str(log), "started_unverified": True}


def stop_watcher(project_dir: str | Path) -> dict:
    """SIGTERM the pidfile'd watcher only when identity verifies."""
    from tagteam.watcher import read_pidfile
    root = Path(project_dir)
    rec = read_pidfile(root)
    if not rec:
        return {"ok": False, "message": "no watcher pidfile (.tagteam/watcher.json) — only a pidfile'd watcher can be stopped from here"}
    pid = rec.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not procs.pid_alive(pid):
        return {"ok": False, "message": "the pidfile's watcher is not alive (stale pidfile)"}
    if not rec.get("ident") or procs.identity(pid) != rec.get("ident"):
        return {"ok": False, "message": f"pid {pid} identity does not match the pidfile — not stopping"}
    try:
        if sys.platform == "win32":  # pragma: no cover
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, 15)
    except OSError as e:
        return {"ok": False, "message": f"could not signal pid {pid}: {e}"}
    return {"ok": True, "pid": pid, "message": f"stop signal sent to watcher pid {pid}"}


def start_session(project_dir: str | Path, *, backend: str | None = None) -> dict:
    """`tagteam session start` (three terminals, agents launched). On the
    manual backend returns the commands for the card to display."""
    from tagteam import session as sess
    root = Path(project_dir)
    backend = backend or sess.default_backend()
    import io, contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = sess.ensure_session(str(root), backend=backend, launch=True, attach_existing=False)
    except Exception as e:
        return {"ok": False, "backend": backend, "message": f"{type(e).__name__}: {e}"}
    out = buf.getvalue()
    return {"ok": result in ("created", "exists", "manual"), "backend": backend, "result": result,
            "message": out.strip()[-1500:]}


# ------------------------------------------------------ composite launch ----

def _me() -> tuple[int, str | None]:
    pid = os.getpid()
    return pid, procs.identity(pid)


def _owner_gone(pid, ident) -> bool:
    if not isinstance(pid, int) or pid <= 0 or not procs.pid_alive(pid):
        return True
    if ident:
        now = procs.identity(pid)
        if now is not None and now != ident:
            return True
    return False


def reconcile_launches(project_dir: str | Path) -> list[dict]:
    """Orphaned `pending` rows (owner definitively gone) → failed with the
    truthful partial state from their persisted references."""
    from tagteam import db
    root = Path(project_dir)
    changed = []
    conn = db.connect(project_dir=str(root))
    try:
        for row in db.pending_launches(conn):
            if not _owner_gone(row.get("owner_pid"), row.get("owner_ident")):
                continue
            partial = _partial_state(root, row)
            db.update_launch(conn, row["key"], ts=_now_iso(), status="failed",
                             finished_at=_now_iso(), error="orphaned: the launching process died",
                             partial_json=json.dumps(partial))
            changed.append(db.get_launch(conn, row["key"]))
    finally:
        conn.close()
    return changed


def _partial_state(root: Path, row: dict) -> dict:
    from tagteam import db
    wp = row.get("watcher_pid")
    watcher = None
    if isinstance(wp, int) and wp > 0:
        alive = procs.pid_alive(wp) and (not row.get("watcher_ident") or procs.identity(wp) == row.get("watcher_ident"))
        watcher = {"pid": wp, "alive": bool(alive)}
    turn = None
    if row.get("conversation_id") and row.get("turn_n"):
        conn = db.connect(project_dir=str(root))
        try:
            t = db.get_conversation_turn(conn, row["conversation_id"], int(row["turn_n"]))
        finally:
            conn.close()
        if t:
            turn = {"conversation_id": row["conversation_id"], "n": t["n"], "status": t["status"]}
    return {"watcher": watcher, "turn": turn}


def launch(project_dir: str | Path, *, intent: dict, config: dict | None, by: str,
           ensure_watcher: bool = True, retry: bool = False, watcher_mode: str = "headless",
           send=None, watcher_wait_s: float = WATCHER_READY_WAIT_S,
           background: bool = False) -> tuple[int, dict]:
    """The composite Start. Returns (http_status, payload).

    `background=True` (the server): the lead's turn is STARTED synchronously
    (slot claimed, `running` row + reference persisted) and RUN on a worker
    thread that finalizes the launch row; the call returns 202 with the
    persisted reference right away. `send=` (tests / sync callers) runs the
    turn inline."""
    from tagteam import db, lead_chat
    from tagteam.dualwrite import writer_lock
    root = Path(project_dir)
    live = launch_intent(root)
    if not intent or intent.get("command") != live.get("command") or \
            (intent.get("observed") or {}) != (live.get("observed") or {}):
        return 409, {"ok": False, "error": "state changed — refresh and start again",
                     "intent": live}
    if not live.get("command"):
        return 409, {"ok": False, "error": live.get("reason"), "intent": live}
    key = launch_key(live)
    me_pid, me_ident = _me()
    now = _now_iso()

    # ---- short lock: claim (or find) the row; no side effects inside
    with writer_lock(root):
        conn = db.connect(project_dir=str(root))
        try:
            row, created = db.claim_launch(conn, key=key, ts=now, intent_json=json.dumps(live),
                                           owner_pid=me_pid, owner_ident=me_ident)
            if not created:
                if row["status"] == "pending" and _owner_gone(row.get("owner_pid"), row.get("owner_ident")):
                    partial = _partial_state(root, row)
                    db.update_launch(conn, key, ts=now, status="failed", finished_at=now,
                                     error="orphaned: the launching process died",
                                     partial_json=json.dumps(partial))
                    row = db.get_launch(conn, key)
                if row["status"] == "failed" and retry:
                    if db.retry_launch(conn, key, ts=now, owner_pid=me_pid, owner_ident=me_ident):
                        row = db.get_launch(conn, key)
                        created = True   # we own the (re)attempt now
        finally:
            conn.close()

    if not created:
        if row["status"] == "pending":
            return 202, {"ok": True, "launched": False, "status": "pending",
                         "conversation_id": row.get("conversation_id"), "turn_n": row.get("turn_n"),
                         "message": "a launch for this state is already in progress", "launch": row}
        if row["status"] == "succeeded":
            return 200, {"ok": True, "launched": False, "status": "succeeded",
                         "existing": {"conversation_id": row.get("conversation_id"), "turn_n": row.get("turn_n")},
                         "message": "already launched for this state", "launch": row}
        partial = json.loads(row.get("partial_json") or "{}") if row.get("partial_json") else {}
        return 409, {"ok": False, "status": "failed", "error": row.get("error"), "partial": partial,
                     "message": "the previous launch for this state failed — retry with retry:true", "launch": row}

    # ---- side effects OUTSIDE the lock; every reference persisted as it exists
    def _persist(**fields):
        c = db.connect(project_dir=str(root))
        try:
            db.update_launch(c, key, ts=_now_iso(), **fields)
        finally:
            c.close()

    def _fail(error: str, extra: dict | None = None):
        c = db.connect(project_dir=str(root))
        try:
            partial = _partial_state(root, db.get_launch(c, key))
        finally:
            c.close()
        partial.update(extra or {})
        _persist(status="failed", finished_at=_now_iso(), error=error, partial_json=json.dumps(partial))
        return 409, {"ok": False, "status": "failed", "error": error, "partial": partial}

    watcher_info = None
    if ensure_watcher:
        # a recorded alive watcher (retry) is reused — never a second one
        wp = row.get("watcher_pid")
        if isinstance(wp, int) and wp > 0 and not _owner_gone(wp, row.get("watcher_ident")):
            watcher_info = {"ok": True, "pid": wp, "reused": True, "message": f"watcher pid {wp} still running"}
        else:
            watcher_info = start_watcher(root, mode=watcher_mode, wait_s=watcher_wait_s)
            if watcher_info.get("already"):
                watcher_info["ok"] = True
            if watcher_info.get("pid"):
                _persist(watcher_pid=watcher_info["pid"], watcher_ident=procs.identity(watcher_info["pid"]))
            if not watcher_info.get("ok"):
                return _fail(f"watcher: {watcher_info.get('message')}", {"watcher_result": watcher_info})

    # an existing turn (retry) is returned — never a second message; a
    # persisted conversation whose turn never got created is reused
    cid = row.get("conversation_id")
    if cid and row.get("turn_n"):
        c = db.connect(project_dir=str(root))
        try:
            existing = db.get_conversation_turn(c, cid, int(row["turn_n"]))
        finally:
            c.close()
        if existing is not None:
            _persist(status="succeeded", finished_at=_now_iso())
            return 200, {"ok": True, "launched": False, "status": "succeeded",
                         "existing": {"conversation_id": cid, "turn_n": row["turn_n"]},
                         "watcher": watcher_info, "message": "the lead turn already exists"}
    try:
        if not cid or lead_chat.get_conversation(root, cid) is None:
            conv = lead_chat.new_conversation(root, provider=lead_chat.resolve_lead(config, root).provider,
                                              title=live["command"])
            cid = conv["id"]
        # persist the conversation reference before the turn runs, so a crash
        # mid-turn leaves a recoverable trace (turn_n is the expected number)
        _persist(conversation_id=cid, turn_n=1)
        if background and send is None:
            handle = lead_chat.start_turn(root, cid, live["command"], config=config, by=by)
            _persist(turn_n=handle.n)

            def _worker():
                try:
                    lead_chat.run_turn(handle)
                    _persist(status="succeeded", finished_at=_now_iso())
                except Exception as e:      # run_turn already ended the row as failed
                    _fail(f"lead turn: {type(e).__name__}: {e}", {"watcher_result": watcher_info})
            import threading
            threading.Thread(target=_worker, name=f"launch-turn-{cid}", daemon=True).start()
            return 202, {"ok": True, "launched": True, "status": "pending",
                         "conversation_id": cid, "turn_n": handle.n, "watcher": watcher_info,
                         "message": f"launched: {live['command']} — the lead is on it (turn {handle.n})"}
        sender = send or (lambda: lead_chat.send(root, cid, live["command"], config=config, by=by))
        turn = sender()
    except lead_chat.LeadBusy as busy:
        return _fail(f"lead: slot busy — {busy.reason} (stem {busy.marker.get('stem')})",
                     {"watcher_result": watcher_info})
    except lead_chat.LeadChatError as e:
        return _fail(f"lead: {e}", {"watcher_result": watcher_info})
    _persist(status="succeeded", finished_at=_now_iso())
    return 200, {"ok": True, "launched": True, "status": "succeeded",
                 "conversation_id": cid, "turn": turn, "watcher": watcher_info,
                 "message": f"launched: {live['command']}"}
