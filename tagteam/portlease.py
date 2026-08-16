"""Phase 37 — Tagteam port lease + occupancy probe for `serve` / `hub`.

Two Tagteam servers must never share a port number on this machine, even
across bind hosts (a `127.0.0.1` listener and a `0.0.0.0` listener can
coexist at the socket level, and the wildcard one is then silently
shadowed on loopback). The real bind stays authoritative for *unrelated*
occupants (`EADDRINUSE` → refuse); Tagteam-vs-Tagteam exclusion is a
project-independent, port-keyed lease file:

    ~/.tagteam/ports/<port>.json  {pid, ident, host, port, project, kind, started_at, token}

published atomically (complete record via a temp file + hard link, so a
contender never sees a half-written lease) BEFORE binding, removed on normal
shutdown, replaced only when the holder is definitively gone (dead pid,
or a recorded non-null identity that mismatches the live process); a
live-but-unverifiable holder keeps the lease (fail closed).

`TAGTEAM_PORT_LEASE_DIR` overrides the directory (tests never touch the
real one).
"""
from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from tagteam import procs


class PortHeld(Exception):
    def __init__(self, record: dict | None, reason: str, *, tagteam: bool):
        super().__init__(reason)
        self.record = record
        self.reason = reason
        self.tagteam = tagteam      # True when a Tagteam holder was identified


def lease_dir() -> Path:
    override = os.environ.get("TAGTEAM_PORT_LEASE_DIR")
    return Path(override) if override else Path.home() / ".tagteam" / "ports"


def lease_path(port: int) -> Path:
    return lease_dir() / f"{int(port)}.json"


def read_lease(port: int) -> dict | None:
    p = lease_path(port)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def holder_gone(rec: dict) -> tuple[bool, str]:
    """Definitive only: dead pid, or recorded non-null identity mismatch."""
    pid = rec.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False, "lease without a pid (fail closed)"
    if not procs.pid_alive(pid):
        return True, f"holder pid {pid} is dead"
    ident = rec.get("ident")
    if not ident:
        return False, f"holder pid {pid} alive; lease without identity (fail closed)"
    now = procs.identity(pid)
    if now is None:
        return False, f"holder pid {pid} alive but identity unavailable (fail closed)"
    if now != ident:
        return True, f"holder pid {pid} identity mismatch (pid reuse)"
    return False, f"holder pid {pid} alive"


class Lease:
    def __init__(self, port: int, path: Path, record: dict):
        self.port, self.path, self.record = port, path, record

    def release(self) -> bool:
        cur = read_lease(self.port)
        if cur is None or cur.get("token") != self.record.get("token"):
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True


def _publish_atomically(path: Path, rec: dict) -> bool:
    """Write the COMPLETE record to a private temp file, then link it into
    place: `os.link` fails with FileExistsError if `path` exists, so a
    contender never sees a half-written lease. Falls back to O_EXCL +
    write only where hard links are unavailable (the record is still
    written in one `write` call). Returns False if `path` already exists."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    data = json.dumps(rec, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    try:
        try:
            os.link(str(tmp), str(path))
            return True
        except FileExistsError:
            return False
        except (OSError, NotImplementedError, AttributeError):
            # no hard links here: exclusive create + one write of the full record
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            return True
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def acquire(port: int, *, host: str, project: str | None, kind: str) -> Lease:
    """Claim the lease for `port` or raise PortHeld naming the holder.

    An EXISTING lease is replaced only when its holder is definitively
    gone. An unreadable / malformed lease is re-read a few times (a writer
    may be mid-flight only on the no-hard-link fallback path) and then
    FAILS CLOSED with an actionable message — never unlinked on the guess
    that it is stale."""
    d = lease_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = lease_path(port)
    me = os.getpid()
    rec = {"pid": me, "ident": procs.identity(me), "host": host, "port": int(port),
           "project": project, "kind": kind, "started_at": datetime.now(timezone.utc).isoformat(),
           "token": secrets.token_hex(8)}
    for _attempt in range(3):
        if _publish_atomically(path, rec):
            return Lease(port, path, rec)
        cur = None
        for _reread in range(5):            # bounded: an in-progress writer on the fallback path
            cur = read_lease(port)
            if cur is not None:
                break
            time.sleep(0.05)
        if cur is None:
            raise PortHeld(None, f"port {port} has an unreadable lease at {path} — if no tagteam server "
                                 f"is running, remove that file; otherwise use --port {port + 1}", tagteam=True)
        gone, why = holder_gone(cur)
        if not gone:
            who = cur.get("kind") or "server"
            proj = cur.get("project") or "?"
            raise PortHeld(cur, f"port {port} is held by tagteam {who} for {proj} "
                                f"(pid {cur.get('pid')}) — use --port {port + 1}", tagteam=True)
        # definitively stale: replace it (a race here is caught by the next publish attempt)
        try:
            path.unlink()
        except OSError:
            pass
    raise PortHeld(read_lease(port), f"port {port} lease could not be claimed — use --port {port + 1}",
                   tagteam=True)


def connectable_host(host: str | None) -> str:
    if host in (None, "", "0.0.0.0", "::", "*"):
        return "127.0.0.1"
    return host


def probe_occupied(host: str | None, port: int, timeout: float = 0.3) -> bool:
    """Does something answer at the connectable form of host:port?"""
    try:
        with socket.create_connection((connectable_host(host), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def identity_probe(host: str | None, port: int, timeout: float = 0.5) -> dict | None:
    """GET /api/info; a Tagteam server answers {"app": "tagteam", ...}."""
    try:
        c = http.client.HTTPConnection(connectable_host(host), int(port), timeout=timeout)
        c.request("GET", "/api/info")
        r = c.getresponse()
        body = r.read(4096)
        c.close()
        d = json.loads(body.decode("utf-8", "replace"))
        return d if isinstance(d, dict) and d.get("app") == "tagteam" else None
    except Exception:
        return None


def occupied_message(host: str | None, port: int) -> str:
    """The refusal text for a port that answered the probe / failed to bind:
    names the Tagteam holder only when verified."""
    ident = identity_probe(host, port)
    if ident:
        who = ident.get("kind") or "server"
        proj = ident.get("project") or "?"
        return f"port {port} is already serving tagteam {who} for {proj} — use --port {port + 1}"
    return f"port {port} is in use on {connectable_host(host)} — use --port {port + 1}"
