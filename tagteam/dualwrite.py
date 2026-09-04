"""
Dual-write infrastructure for Phase 28 Step A.

This module provides the shared primitives for the dual-write era:

  - Per-project writer lock (`writer_lock`) held across the file-write
    + DB-write + divergence-check + sentinel-update critical section.
  - The `db_invalid` sentinel (flag file + DB column) that gates all
    DB readers when the shadow DB is in an unknown state.
  - A thread-local skip flag for the `update_state` → `write_state`
    layering case, so the inner `write_state` wrapper does not double-
    write history.

Pure infrastructure: no live writers are wired to it yet. That happens
in the next commit on this branch. See
`docs/phases/phase-28-dual-write-design.md` for the contract this
module implements.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# Per-project threading.RLock cache. RLock allows the same thread to
# re-enter the lock without blocking; this is the in-process layer
# that complements the file-system-level fcntl lock for cross-process
# serialization.
_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_mutex = threading.Lock()

# Per-thread depth counter per project. Tells `writer_lock` whether
# this is the outermost acquisition (which must take the fcntl lock)
# or a re-entry (which already holds it).
_lock_depth = threading.local()


# ---------- Portable exclusive file lock ----------
#
# POSIX: fcntl.flock(LOCK_EX). Windows: msvcrt.locking on the first byte
# (Phase 31 — before this, `import fcntl` at module top made every cycle
# write fail on Windows). Both are per-process advisory locks on a
# local file; the design explicitly does not support multi-host
# concurrency on a network drive.

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    # Lock one byte far past any content the lock file will ever hold, so
    # the holder-info stamp written at offset 0 never touches the locked
    # region (Windows byte-range locks block writes to the region even
    # from the locking process's own handle in some paths).
    _WIN_LOCK_OFFSET = 1 << 30

    def _os_lock(fd: int) -> None:
        # LK_NBLCK is non-blocking; poll until acquired so we behave like
        # a blocking LOCK_EX without msvcrt's 10-second LK_LOCK give-up.
        while True:
            try:
                os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)

    def _os_unlock(fd: int) -> None:
        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _os_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _os_unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _get_thread_lock(project_key: str) -> threading.RLock:
    with _thread_locks_mutex:
        lock = _thread_locks.get(project_key)
        if lock is None:
            lock = threading.RLock()
            _thread_locks[project_key] = lock
        return lock

# File-system layout (all relative to project root).
TAGTEAM_DIR = ".tagteam"
WRITE_LOCK_FILENAME = ".write.lock"
DB_INVALID_FLAG_FILENAME = "DB_INVALID"


# ---------- Path helpers ----------

def _tagteam_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / TAGTEAM_DIR


def _ensure_tagteam_dir(project_dir: str | Path) -> Path:
    d = _tagteam_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_lock_path(project_dir: str | Path) -> Path:
    return _tagteam_dir(project_dir) / WRITE_LOCK_FILENAME


def _db_invalid_flag_path(project_dir: str | Path) -> Path:
    return _tagteam_dir(project_dir) / DB_INVALID_FLAG_FILENAME


# ---------- Read-only mode (Phase 50) ----------
#
# A helper process the lead delegates to (a panel lens, a brief drafter, a
# verifier) must be able to READ the cycle and must never WRITE it — the
# turn's one cycle-writing call belongs to the caller. `TAGTEAM_READ_ONLY`
# is the process-scoped switch: the parent sets it for the children it
# spawns; every state write refuses *before* anything touches disk.

READ_ONLY_ENV = "TAGTEAM_READ_ONLY"
_READ_ONLY_OFF = {"", "0", "false", "no"}

READ_ONLY_MESSAGE = (
    "tagteam: refused — this process is read-only (TAGTEAM_READ_ONLY is set).\n"
    "A helper (panel lens, brief drafter, verifier) returns text; "
    "the caller makes the cycle-writing call."
)


def read_only() -> bool:
    """True when `TAGTEAM_READ_ONLY` is set to anything but an off value
    (`0`, `false`, `no`, empty — case-insensitive)."""
    v = os.environ.get(READ_ONLY_ENV)
    if v is None:
        return False
    return v.strip().lower() not in _READ_ONLY_OFF


def refuse_if_read_only(detail: str) -> None:
    """Raise `ReadOnlyError(detail)` when the switch is set. For mutators that
    do not go through `writer_lock` or a `db` writer (runtime markers, the
    db_invalid sentinel, destructive helpers)."""
    if read_only():
        raise ReadOnlyError(detail)


class ReadOnlyError(RuntimeError):
    """A write was refused because this process runs in read-only mode.

    `detail` is an optional second line naming the specific refusal and
    its remedy; `str(exc)` is what the CLI prints (exit code 2)."""

    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(READ_ONLY_MESSAGE + (f"\n{detail}" if detail else ""))


class DatabaseMissing(ReadOnlyError):
    """`tagteam.db` does not exist and read-only mode will not create it."""


class SchemaBehind(ReadOnlyError):
    """The DB schema is older than this release expects; read-only mode
    never migrates."""


class WalWithoutIndex(ReadOnlyError):
    """`tagteam.db-wal` exists without `tagteam.db-shm`: SQLite could only
    read it by creating the index file, or (immutable) by ignoring the
    WAL's committed frames. Neither is acceptable read-only; fail closed."""


# ---------- Writer lock ----------

@contextmanager
def writer_lock(project_dir: str | Path) -> Iterator[None]:
    """Acquire the project's exclusive writer lock for the duration of
    the `with` block. Blocks until available.

    Held across the dual-write critical section: file write + DB write
    + divergence check + sentinel update. Repair runs under the same
    lock — that is the whole point of using one lock, not two.

    Reentrant within a thread. A nested call from the same thread
    re-acquires (no-op) and decrements on exit. Cross-thread
    serialization comes from a per-project `threading.RLock`;
    cross-process serialization from `fcntl.flock(LOCK_EX)`. The
    fcntl lock is taken only on the outermost acquisition; nested
    calls don't try to re-acquire the OS lock (which on most platforms
    would either block the process against itself or silently
    convert).

    Reads do NOT acquire this lock. They are allowed to race against
    in-flight writes, matching today's file-system semantics.

    fcntl.flock is per-process on POSIX; the design explicitly does not
    support multi-host concurrency on a network drive.
    """
    if read_only():
        # Before the directory, the lock file, the thread lock: a read-only
        # process leaves no trace of having tried.
        raise ReadOnlyError("writer lock refused: every state write goes through it")

    project_key = str(Path(project_dir).resolve())
    thread_lock = _get_thread_lock(project_key)

    with thread_lock:
        if not hasattr(_lock_depth, "depths"):
            _lock_depth.depths = {}
        depths = _lock_depth.depths

        is_outermost = depths.get(project_key, 0) == 0
        depths[project_key] = depths.get(project_key, 0) + 1

        try:
            if is_outermost:
                _ensure_tagteam_dir(project_dir)
                lock_path = _write_lock_path(project_dir)
                fd = os.open(
                    lock_path, os.O_RDWR | os.O_CREAT, 0o644
                )
                try:
                    _os_lock(fd)
                    # Best-effort: stamp PID + timestamp into the lock
                    # file so a diagnostics tool can see who's holding
                    # it. Failure is benign.
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.ftruncate(fd, 0)
                        os.write(
                            fd,
                            f"{os.getpid()} "
                            f"{datetime.now(timezone.utc).isoformat()}\n"
                            .encode(),
                        )
                    except OSError:
                        pass
                    yield
                finally:
                    try:
                        _os_unlock(fd)
                    finally:
                        os.close(fd)
            else:
                # Reentrant case — outer call already holds fcntl.
                yield
        finally:
            depths[project_key] -= 1
            if depths[project_key] == 0:
                del depths[project_key]


def lock_holder(project_dir: str | Path) -> tuple[int, str] | None:
    """Return (pid, iso_timestamp) of the current writer-lock holder,
    or None if the lock file is missing/empty/unparseable. Does NOT
    acquire the lock — purely diagnostic. The returned PID may be a
    stale entry from a process that has since released the lock; the
    caller cannot infer hold status from this function alone."""
    lock_path = _write_lock_path(project_dir)
    if not lock_path.exists():
        return None
    try:
        contents = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not contents:
        return None
    parts = contents.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), parts[1])
    except ValueError:
        return None


# ---------- db_invalid sentinel ----------
#
# Authority rule: the flag file (`.tagteam/DB_INVALID`) is the source
# of truth. The DB column `state.db_invalid_since` is informational
# only and may not be readable when SQLite is unavailable. Every
# reader that wants to gate on `db_invalid` must consult `is_db_invalid`
# (which reads the flag file), not the DB column.

def mark_db_invalid(
    project_dir: str | Path,
    reason: str,
) -> None:
    """Mark the shadow DB as invalid.

    Writes the flag file authoritatively. The flag file is sufficient
    to gate readers and works when SQLite is unavailable.

    Idempotent: calling on an already-invalid DB updates the flag
    file's `since` to the earlier timestamp (preserves "first failure"
    semantics) and replaces `reason`.

    Note: the design originally proposed a parallel DB-side
    observability write to `state.extra_json`. The schema doesn't
    have a dedicated column today, and writing a no-op
    `UPDATE state SET extra_json = COALESCE(extra_json, '{}')` would
    make the code look like it's recording observability info when
    it isn't. Add a real schema column (e.g. `state.db_invalid_since`)
    and store `since`/`reason` there if/when DB-side observability
    becomes load-bearing.
    """
    refuse_if_read_only("db_invalid sentinel write refused (mark_db_invalid)")
    flag_path = _db_invalid_flag_path(project_dir)
    _ensure_tagteam_dir(project_dir)

    now_iso = datetime.now(timezone.utc).isoformat()
    existing_since = None
    if flag_path.exists():
        try:
            existing = json.loads(flag_path.read_text(encoding="utf-8"))
            existing_since = existing.get("since")
        except (json.JSONDecodeError, OSError):
            existing_since = None

    payload = {
        "since": existing_since or now_iso,
        "reason": reason,
        "updated_at": now_iso,
    }
    flag_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def clear_db_invalid(project_dir: str | Path) -> None:
    """Clear the db_invalid sentinel.

    Callers must only invoke this after both DB import and parity
    check have succeeded. The `--force-clear` admin path is documented
    as last-resort; it should call this directly with appropriate
    logging at the call site, not via this helper.
    """
    refuse_if_read_only("db_invalid sentinel write refused (clear_db_invalid)")
    flag_path = _db_invalid_flag_path(project_dir)
    try:
        flag_path.unlink()
    except FileNotFoundError:
        pass


def is_db_invalid(project_dir: str | Path) -> bool:
    """True if the project's shadow DB is marked invalid.

    Reads the flag file. Does NOT consult the DB column — by design,
    the flag file is authoritative and works when SQLite is unavailable.
    """
    return _db_invalid_flag_path(project_dir).exists()


def get_db_invalid_info(project_dir: str | Path) -> dict | None:
    """Return the flag file's payload (`since`, `reason`, `updated_at`),
    or None if the flag is not set. Returns an empty dict if the flag
    file exists but is unparseable (treat as invalid-but-no-detail)."""
    flag_path = _db_invalid_flag_path(project_dir)
    if not flag_path.exists():
        return None
    try:
        return json.loads(flag_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ---------- update_state → write_state layering helper ----------
#
# `state.update_state` calls `state.write_state` internally. Naively
# wrapping both with dual-write would issue two DB state updates and
# double-append history. The rule is: `update_state` owns the DB
# write; `write_state`'s own wrapper short-circuits when called from
# inside `update_state`.
#
# Implemented via a thread-local flag, set by `update_state` for the
# duration of the inner `write_state` call. Using a context manager
# (rather than a bare attribute set/clear) ensures the flag is reset
# even if `write_state` raises, so an exception during a server
# request cannot leak the flag into a later request on the same
# thread.

_state_thread_local = threading.local()


@contextmanager
def skip_inner_dualwrite() -> Iterator[None]:
    """Mark the calling thread so any nested dual-write hook in
    `write_state` short-circuits. Used by `update_state`.

    Reentrant: nested `with skip_inner_dualwrite()` blocks compose
    correctly via a counter, so an outer `update_state` and an inner
    one (rare but possible) both restore the previous state on exit.
    """
    depth = getattr(_state_thread_local, "skip_depth", 0)
    _state_thread_local.skip_depth = depth + 1
    try:
        yield
    finally:
        _state_thread_local.skip_depth = depth


def should_skip_inner_dualwrite() -> bool:
    """True if the current thread is inside a `skip_inner_dualwrite`
    block. Called by `write_state`'s dual-write wrapper."""
    return getattr(_state_thread_local, "skip_depth", 0) > 0


# ---------- Step B activation ----------

def step_b_active() -> bool:
    """True when Phase 28 Step B behavior is explicitly enabled.

    Read per-call so long-running watcher/server processes can pick up
    soak toggles without a restart. Only the exact value "1" enables it.
    """
    return os.environ.get("TAGTEAM_STEP_B") == "1"
