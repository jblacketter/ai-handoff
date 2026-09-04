"""
Arbiter controls for a running orchestration (Phase 32):

  tagteam pause [--reason TEXT] [--by NAME]
  tagteam resume [--quiet]
  tagteam cancel-turn [--by NAME]
  tagteam interject "<note>" [--by NAME] [--to lead|reviewer]
  tagteam interject --list | --retire ID [--by NAME]
  tagteam rollback X.Y.Z [--yes]

`pause` writes the same `.tagteam/headless-paused.json` marker the headless
engine writes on a failed turn; every watcher mode honors it. `cancel-turn`
binds the recorded child PID to the recorded turn (creation identities +
parent pid) before signalling anything, then writes a stem-matched cancel
marker the engine turns into outcome `cancelled`. `interject` appends an
arbiter note (schema v4 `interjections`) that the next eligible turn
receives in its prompt (headless) or sees on `cycle rounds` (interactive).
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from tagteam import headless as h
from tagteam import procs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(project_root: str | Path | None) -> Path:
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    return Path(project_root)


def _who(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("TAGTEAM_ARBITER")
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception:
        return "arbiter"


def _parse(args: list[str], flags_with_value: set[str], flags: set[str]) -> tuple[dict, list[str]]:
    """Tiny arg parser: returns ({flag: value|True}, positionals). Raises
    ValueError on unknown flags / missing values."""
    out: dict = {}
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in flags_with_value:
            if i + 1 >= len(args):
                raise ValueError(f"{a} requires a value")
            out[a] = args[i + 1]; i += 2
        elif a in flags:
            out[a] = True; i += 1
        elif a.startswith("-") and a not in ("-",):
            raise ValueError(f"Unknown argument: {a}")
        else:
            pos.append(a); i += 1
    return out, pos


def _fmt_duration(start_iso: str | None) -> str:
    if not start_iso:
        return "?"
    try:
        start = datetime.fromisoformat(start_iso)
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
        m, s = divmod(max(secs, 0), 60)
        hh, m = divmod(m, 60)
        return f"{hh}h{m:02d}m{s:02d}s" if hh else f"{m}m{s:02d}s"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------

def pause_command(args: list[str], project_root: str | Path | None = None,
                  out=None) -> int:
    out = out or sys.stdout
    try:
        opts, pos = _parse(args, {"--reason", "--by"}, {"-h", "--help"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h"):
        print("Usage: tagteam pause [--reason TEXT] [--by NAME]", file=out)
        print("  Hold dispatch in every watcher mode until `tagteam resume`.", file=out)
        return 0
    if pos:
        print(f"Unexpected argument: {pos[0]!r} (use --reason TEXT)", file=out); return 1
    root = _root(project_root)
    reason = opts.get("--reason") or "paused by operator"
    by = _who(opts.get("--by"))
    prior = h.read_pause(root)
    payload = {"reason": reason, "by": by, "source": "cli", "ts": _now_iso(),
               "log_path": None}
    # Remember the state the pause was set on, so a marker that outlives its
    # run reads as stale wherever it is shown (watcher log, hand-off notice).
    try:
        from tagteam.state import read_state
        st = read_state(str(root)) or {}
    except Exception:
        st = {}
    if st:
        payload["state"] = {k: st.get(k) for k in ("phase", "type", "round", "status", "turn", "seq")}
    if prior and prior.get("source") != "cli":
        # keep the failed-turn context visible for `resume`
        payload["previous"] = {k: prior.get(k) for k in
                               ("reason", "outcome", "log_path", "ts") if prior.get(k)}
    p = h.write_pause(root, payload)
    verb = "Pause updated" if prior else "Paused"
    print(f"{verb}: {reason} (by {by})", file=out)
    print(f"  marker: {p}", file=out)
    print("  resume with: tagteam resume", file=out)
    print("  Dispatch stays held across watcher restarts and new cycles until then;"
          " `cycle init` / `cycle add` / `cycle status` will say so.", file=out)
    return 0


def resume_command(args: list[str], project_root: str | Path | None = None,
                   out=None) -> int:
    out = out or sys.stdout
    try:
        opts, pos = _parse(args, set(), {"--quiet", "-h", "--help"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h"):
        print("Usage: tagteam resume [--quiet]", file=out)
        print("  Clear the pause marker; the watcher re-dispatches the owed turn once.", file=out)
        return 0
    root = _root(project_root)
    info = h.read_pause(root)
    if info is None:
        if not opts.get("--quiet"):
            print("Not paused.", file=out)
            return 1
        return 0
    h.clear_pause(root)
    src = info.get("source") or ("failed turn" if info.get("outcome") else "unknown")
    print(f"Resumed. Was paused for {_fmt_duration(info.get('ts'))} "
          f"({src}): {info.get('reason')}", file=out)
    if info.get("outcome"):
        print(f"  failed turn: {info.get('outcome')} — {info.get('phase')} "
              f"{info.get('type')} r{info.get('round')} ({info.get('role')})", file=out)
        if info.get("log_path"):
            print(f"  log: {info.get('log_path')}", file=out)
    prev = info.get("previous")
    if prev:
        print(f"  earlier failure still worth reading: {prev.get('outcome')} — "
              f"{prev.get('reason')} ({prev.get('log_path')})", file=out)
    print("  The watcher will re-dispatch the still-owed turn on its next tick.", file=out)
    return 0


# ---------------------------------------------------------------------------
# cancel-turn
# ---------------------------------------------------------------------------

def bind_inflight(inflight: dict, self_pid: int | None = None) -> tuple[bool, str]:
    """Decide whether the recorded child PID may be signalled. Returns
    (ok, reason). Fail closed on anything unverifiable."""
    self_pid = os.getpid() if self_pid is None else self_pid
    pid = inflight.get("pid")
    watcher_pid = inflight.get("watcher_pid")
    if not isinstance(pid, int) or pid <= 0:
        return False, "no child pid recorded (turn may not have spawned yet, or stale metadata)"
    if pid == self_pid:
        return False, "recorded pid is this process"
    if not isinstance(watcher_pid, int) or watcher_pid <= 0:
        return False, "no watcher pid recorded (metadata predates Phase 32 or is corrupt)"
    if pid == watcher_pid:
        return False, "recorded child pid equals the watcher pid"
    if not procs.pid_alive(pid):
        return False, f"child pid {pid} is not alive (turn already ended)"
    child_ident = inflight.get("child_ident")
    watcher_ident = inflight.get("watcher_ident")
    if not child_ident or not watcher_ident:
        return False, "creation identities missing from metadata (unverifiable)"
    now_child = procs.identity(pid)
    if now_child is None:
        return False, f"could not determine creation identity of pid {pid} (unverifiable)"
    if now_child != child_ident:
        return False, (f"pid {pid} identity mismatch (recorded {child_ident!r}, "
                       f"now {now_child!r}) — PID reuse; not signalling")
    now_watcher = procs.identity(watcher_pid)
    if now_watcher is None:
        return False, (f"could not determine creation identity of watcher pid "
                       f"{watcher_pid} (unverifiable)")
    if now_watcher != watcher_ident:
        return False, (f"watcher pid {watcher_pid} identity mismatch (recorded "
                       f"{watcher_ident!r}, now {now_watcher!r}) — stale metadata")
    ppid = procs.parent_pid(pid)
    if ppid is None:
        return False, f"could not determine parent of pid {pid} (unverifiable)"
    if ppid != watcher_pid:
        return False, (f"pid {pid} parent is {ppid}, not the recorded watcher "
                       f"{watcher_pid} — not the turn we recorded")
    return True, "bound"


def cancel_turn_command(args: list[str], project_root: str | Path | None = None,
                        out=None) -> int:
    out = out or sys.stdout
    try:
        opts, pos = _parse(args, {"--by"}, {"-h", "--help"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h"):
        print("Usage: tagteam cancel-turn [--by NAME]", file=out)
        print("  Kill the in-flight headless turn; the engine records outcome 'cancelled' and pauses.", file=out)
        return 0
    root = _root(project_root)
    inflight = h.read_inflight(root)
    if inflight is None:
        print("Nothing in flight (no .tagteam/turns/inflight.json).", file=out)
        return 1
    ok, why = bind_inflight(inflight)
    stem = inflight.get("stem")
    if not ok:
        print(f"Refusing to signal: {why}", file=out)
        from tagteam.dualwrite import refuse_if_read_only
        refuse_if_read_only("stale inflight.json cleanup refused")   # Phase 50
        try:
            h.inflight_path(root).unlink()
            print("  Removed stale inflight.json (metadata only; nothing was killed).", file=out)
        except OSError:
            pass
        return 1
    existing = h.read_cancel(root)
    if existing is not None and existing.get("stem") != stem:
        print(f"A cancel marker for another turn ({existing.get('stem')!r}) exists; "
              f"the engine will discard it. Proceeding.", file=out)
    by = _who(opts.get("--by"))
    marker = h.write_cancel(root, {"stem": stem, "pid": inflight["pid"], "by": by})
    sent = h.kill_pid_tree(inflight["pid"])
    print(f"Cancel requested for {inflight.get('provider')} turn {stem} "
          f"(pid {inflight['pid']}, {inflight.get('agent')} / {inflight.get('role')})", file=out)
    print(f"  marker: {marker}", file=out)
    print(f"  {'signalled process tree' if sent else 'could not signal (process may have exited); the engine will still record cancelled'}", file=out)
    return 0


# ---------------------------------------------------------------------------
# interject
# ---------------------------------------------------------------------------

def _owed_identity(root: Path) -> tuple[dict | None, dict]:
    """(state or None, identity dict with phase/type/round/turn or all None)."""
    from tagteam.state import read_state
    st = read_state(str(root))
    if st and st.get("status") in ("ready", "working") and st.get("turn") in ("lead", "reviewer"):
        return st, {"phase": st.get("phase"), "type": st.get("type"),
                    "round": st.get("round"), "turn": st.get("turn")}
    return st, {"phase": None, "type": None, "round": None, "turn": None}


def interject_command(args: list[str], project_root: str | Path | None = None,
                      out=None) -> int:
    out = out or sys.stdout
    try:
        opts, pos = _parse(args, {"--by", "--to", "--retire"}, {"--list", "-h", "--help", "--json"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h"):
        print("Usage: tagteam interject \"<note>\" [--by NAME] [--to lead|reviewer]", file=out)
        print("       tagteam interject --list [--json]", file=out)
        print("       tagteam interject --retire ID [--by NAME]", file=out)
        return 0
    root = _root(project_root)
    from tagteam import db
    from tagteam.dualwrite import DatabaseMissing
    try:
        conn = db.connect(project_dir=str(root))
    except DatabaseMissing:
        if not opts.get("--list"):
            raise
        # Phase 50: read-only and no DB — there are no interjections.
        print("[]" if opts.get("--json") else "No interjections for the current cycle.", file=out)
        return 0
    try:
        if opts.get("--list"):
            st, ident = _owed_identity(root)
            phase = (st or {}).get("phase")
            ctype = (st or {}).get("type")
            rows = db.get_interjections(conn, phase=phase, cycle_type=ctype) if phase else []
            rows += [r for r in db.get_interjections(conn) if r["phase"] is None]
            if opts.get("--json"):
                print(json.dumps(rows, indent=2), file=out)
                return 0
            if not rows:
                print("No interjections for the current cycle.", file=out)
                return 0
            for r in rows:
                if r["retired_ts"]:
                    status = f"retired {r['retired_ts']} by {r['retired_by']}"
                elif r["delivered_ts"]:
                    status = (f"delivered → {r['delivered_role']} r{r['delivered_round']} "
                              f"({r['delivered_stem']})")
                else:
                    status = "pending"
                scope = f"{r['phase']}/{r['type']} r{r['round']}" if r["phase"] else "next cycle"
                target = r["target_role"] or "next turn"
                print(f"#{r['id']} [{r['ts']}] {r['by']} → {target} [{scope}] {status}",
                      file=out)
                print(f"    {r['note']}", file=out)
            return 0
        if opts.get("--retire"):
            try:
                rid = int(opts["--retire"])
            except ValueError:
                print(f"--retire needs an integer id, got {opts['--retire']!r}", file=out)
                return 1
            if db.retire_interjection(conn, rid, by=_who(opts.get("--by")), ts=_now_iso()):
                print(f"Retired interjection #{rid}.", file=out)
                return 0
            print(f"Interjection #{rid} not found, or already delivered/retired.", file=out)
            return 1
        # add
        note = " ".join(pos).strip()
        if not note:
            try:
                if not sys.stdin.isatty():
                    note = sys.stdin.read().strip()
            except (OSError, ValueError):
                note = ""
        if not note:
            print("Provide the note as an argument (or on stdin).", file=out)
            return 1
        target = opts.get("--to")
        if target is not None and target not in db.INTERJECTION_ROLES:
            print(f"--to must be lead or reviewer (got {target!r})", file=out)
            return 1
        st, ident = _owed_identity(root)
        observed = None
        if st is not None:
            observed = {k: st.get(k) for k in ("phase", "type", "round", "status", "result", "turn")}
        rid = db.add_interjection(conn, ts=_now_iso(), note=note, by=_who(opts.get("--by")),
                                  target_role=target, phase=ident["phase"],
                                  cycle_type=ident["type"], round_=ident["round"],
                                  turn=ident["turn"], observed_state=observed)
        if ident["phase"] is None:
            print(f"Interjection #{rid} recorded. WARNING: no turn is currently owed; "
                  f"the note will be delivered to the first eligible turn of the next cycle.",
                  file=out)
        else:
            who = f"the next {target} turn" if target else "the next turn"
            print(f"Interjection #{rid} recorded for {ident['phase']}/{ident['type']} "
                  f"(currently r{ident['round']}, {ident['turn']} owed) → {who}.", file=out)
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def rollback_plan(version: str) -> list[list[str]]:
    """Commands the revert recipe would run for this install."""
    exe = sys.executable or "python"
    if "/uv/tools/" in exe.replace("\\", "/") or "\\uv\\tools\\" in exe:
        install = ["uv", "tool", "install", f"tagteam=={version}", "--force"]
    else:
        install = [exe, "-m", "pip", "install", f"tagteam=={version}"]
    return [install, ["tagteam", "upgrade"]]


def rollback_command(args: list[str], out=None, runner=None) -> int:
    import subprocess
    out = out or sys.stdout
    runner = runner or subprocess.run
    try:
        opts, pos = _parse(args, set(), {"--yes", "-h", "--help"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h") or not pos:
        print("Usage: tagteam rollback X.Y.Z [--yes]", file=out)
        print("  Print (and with --yes run) the revert recipe: reinstall that version,", file=out)
        print("  then `tagteam upgrade` to re-copy framework files into every registered project.", file=out)
        return 0 if (opts.get("--help") or opts.get("-h")) else 1
    version = pos[0]
    if not _VERSION_RE.match(version):
        print(f"Version must look like X.Y.Z (got {version!r})", file=out)
        return 1
    from tagteam.registry import get_registered_projects
    n = len(get_registered_projects())
    plan = rollback_plan(version)
    print(f"Rollback to tagteam {version} would run:", file=out)
    for cmd in plan:
        print("  " + " ".join(cmd), file=out)
    print(f"  (`tagteam upgrade` re-copies framework files into {n} registered project(s); "
          f"existing files are skipped)", file=out)
    if not opts.get("--yes"):
        print("Nothing executed. Re-run with --yes to perform the rollback.", file=out)
        return 0
    for cmd in plan:
        print(f"$ {' '.join(cmd)}", file=out)
        r = runner(cmd)
        rc = getattr(r, "returncode", 0)
        if rc != 0:
            print(f"Command failed with exit {rc}; stopping.", file=out)
            return 1
    print(f"Rolled back to {version}.", file=out)
    return 0


# ---------------------------------------------------------------------------
# rule (Phase 33)
# ---------------------------------------------------------------------------

def rule_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    """tagteam rule <approve|request-changes|answer> [--content TEXT] [--by NAME] [--to lead|reviewer]

    Capture-before-append: under the writer lock, resolve and retain the
    current escalation event, select its successful brief, verify the
    cycle has not changed, apply the ruling, then write the diagnostic
    with the RETAINED event key (add_ruling appends a new latest entry).
    """
    from tagteam import db, dualwrite
    from tagteam import briefer as _briefer
    from tagteam import cycle as cycle_mod
    from tagteam.state import read_state
    out = out or sys.stdout
    try:
        opts, pos = _parse(args, {"--content", "--by", "--to"}, {"-h", "--help"})
    except ValueError as e:
        print(f"{e}", file=out); return 1
    if opts.get("--help") or opts.get("-h") or not pos:
        print("Usage: tagteam rule <approve|request-changes|answer> [--content TEXT] "
              "[--by NAME] [--to lead|reviewer]", file=out)
        print("  Act on an escalated / needs-human cycle from the terminal (see `tagteam brief`).",
              file=out)
        return 0 if (opts.get("--help") or opts.get("-h")) else 1
    verb = pos[0]
    if verb not in ("approve", "request-changes", "answer"):
        print(f"Unknown ruling {verb!r}: use approve, request-changes or answer", file=out); return 1
    content = (opts.get("--content") or " ".join(pos[1:])).strip()
    if verb in ("request-changes", "answer") and not content:
        print(f"`{verb}` requires --content", file=out); return 1
    to = opts.get("--to")
    if verb == "answer":
        to = to or "reviewer"
        if to not in ("lead", "reviewer"):
            print("--to must be lead or reviewer", file=out); return 1
    elif to is not None:
        print("--to only applies to `answer`", file=out); return 1
    by = _who(opts.get("--by"))
    root = _root(project_root)
    st = read_state(str(root)) or {}
    phase, ctype = st.get("phase"), st.get("type")
    if not phase or not ctype:
        print("No active cycle in state.", file=out); return 1

    with dualwrite.writer_lock(root):
        ev, why = _briefer.event_for_cycle(root, phase, ctype)
        if ev is None:
            print(f"Nothing to rule on: {why}", file=out); return 1
        conn = db.connect(project_dir=str(root))
        try:
            brief = db.successful_brief_for_event(conn, ev.event_key)
        finally:
            conn.close()
        # verify unchanged (re-read)
        ev2, _ = _briefer.event_for_cycle(root, phase, ctype)
        if ev2 is None or ev2.event_key != ev.event_key:
            print("Cycle changed while ruling — re-run.", file=out); return 1
        try:
            if verb == "approve":
                cycle_mod.add_ruling(phase, ctype, "APPROVE", content or "Approved by arbiter.",
                                     by, str(root))
                outcome = "approved — cycle closed"
            elif verb == "request-changes":
                cycle_mod.add_ruling(phase, ctype, "REQUEST_CHANGES", content, by, str(root))
                outcome = "changes requested — lead's turn"
            else:
                conn = db.connect(project_dir=str(root))
                try:
                    db.add_interjection(conn, ts=_now_iso(), note=f"[ARBITER ANSWER by {by}] {content}",
                                        by=by, target_role=to, phase=phase, cycle_type=ctype,
                                        round_=ev.round, turn=None,
                                        observed_state={k: st.get(k) for k in
                                                        ("phase", "type", "round", "status", "result", "turn")})
                finally:
                    conn.close()
                cycle_mod.rearm(phase, ctype, to, by, str(root))
                outcome = f"answered — {to}'s turn (answer delivered as an interjection)"
        except ValueError as e:
            print(f"Cannot rule: {e}", file=out); return 1
        conn = db.connect(project_dir=str(root))
        try:
            db.add_diagnostic(conn, "arbiter_ruling", {
                "ruling": verb, "by": by, "content": content, "to": to,
                "phase": phase, "type": ctype, "round": ev.round,
                "event_key": ev.event_key,
                "brief_id": brief["id"] if brief else None,
                "brief_path": brief["path"] if brief else None,
            }, _now_iso())
            conn.commit()
        finally:
            conn.close()
    print(f"Ruling recorded ({by}): {outcome}."
          + (f" Acted on brief #{brief['id']} ({brief['path']})." if brief else " (no brief for this event)"),
          file=out)
    return 0
