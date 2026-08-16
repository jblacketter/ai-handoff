"""
Watcher daemon for automated handoff orchestration.

Polls handoff-state.json and triggers agents via desktop notifications
or tmux send-keys when it's their turn.
"""

import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tagteam.config import read_config, get_agent_names
from tagteam.state import read_state, update_state, get_state_path, normalize_phase_key


def notify_macos(title: str, message: str) -> None:
    """Desktop notification. Name kept for backward compatibility (tests
    and callers patch `tagteam.watcher.notify_macos`); since Phase 32 it
    dispatches to `tagteam.notify.notify`, which is cross-platform (macOS
    osascript, Windows toast/msg, Linux notify-send) and best-effort."""
    from tagteam.notify import notify
    notify(title, message)


def pane_exists(pane_target: str) -> bool:
    """Check if a tmux pane exists."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane_target, "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def capture_pane(pane_target: str, last_n_lines: int = 5) -> str:
    """Capture the last N lines of a tmux pane's visible content."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane_target, "-p",
             "-S", str(-last_n_lines)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except Exception:
        return ""


BUSY_PATTERNS = [
    "esc to interrupt",
    "thinking",
    "Running",
    "Do you want to proceed",
    "Do you want to make this edit",
]

IDLE_PATTERNS = [
    # Claude Code
    "? for shortcuts",
    "context left",
    "for help",
    "> ",
    "\u276f",           # ❯ — Claude Code's actual prompt character
    "accept edits",     # status bar: "⏵⏵ accept edits on (shift+tab to cycle)"
    "shift+tab",        # alternate match for the same status bar
    # Codex
    "/skills to list",
    "/model to change",
    "type a message",
    "enter a command",
    # Shell prompt (agent not yet started)
    "$ ",
    "% ",
    "\ue0b0",          # Powerline prompt separator
    "@",               # user@hostname in shell prompts
]


def _check_idle_patterns(content: str) -> bool:
    """Check terminal content for idle/busy patterns.

    Returns True if the agent appears idle (at input prompt),
    False if busy or content is empty.
    """
    if not content.strip():
        return False

    lines = content.strip().splitlines()
    tail = "\n".join(lines[-4:]).lower()

    for pattern in BUSY_PATTERNS:
        if pattern.lower() in tail:
            return False

    for pattern in IDLE_PATTERNS:
        if pattern.lower() in tail:
            return True

    return False


def is_agent_idle(pane_target: str) -> bool:
    """Check if an agent TUI in a tmux pane is idle (at input prompt)."""
    content = capture_pane(pane_target, last_n_lines=5)
    return _check_idle_patterns(content)


def is_agent_idle_iterm(session_id: str, debug: bool = False) -> bool:
    """Check if an agent TUI in an iTerm2 session is idle."""
    from tagteam.iterm import get_session_contents
    content = get_session_contents(session_id, last_n_lines=5)
    idle = _check_idle_patterns(content)
    if debug and not idle:
        tail = content.strip().splitlines()[-2:] if content.strip() else []
        _log(f"   (not idle yet, last lines: {tail!r})")
    return idle


def wait_for_idle(
    pane_target: str,
    timeout: float = 300.0,
    poll_interval: float = 5.0,
) -> bool:
    """Wait until the agent in the given pane is idle, up to timeout seconds."""
    start = time.time()
    while time.time() - start < timeout:
        if is_agent_idle(pane_target):
            return True
        time.sleep(poll_interval)
    return False


def send_tmux_keys(
    pane_target: str,
    command: str,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    pre_send_delay: float = 1.0,
) -> bool:
    """Send keys to a tmux pane with retry logic.

    Steps:
    1. Verify pane exists
    2. Wait for agent to be idle (at input prompt)
    3. Clear any partial input (Escape x3 + C-c)
    4. Send command as literal text + C-m
    5. Retry on failure
    """
    if not pane_exists(pane_target):
        _log(f"   ERROR: Pane '{pane_target}' does not exist")
        return False

    for attempt in range(1, max_retries + 1):
        try:
            # Wait for agent to be idle before sending
            _log(f"   Checking if agent in {pane_target} is idle...")
            if not wait_for_idle(pane_target, timeout=15.0, poll_interval=3.0):
                _log(f"   Idle detection inconclusive for {pane_target}, proceeding after 15s")

            if pre_send_delay > 0:
                time.sleep(pre_send_delay)

            # Clear any partial input — different TUIs need different keys:
            # Escape x3 clears Claude Code, C-c clears Codex
            for _ in range(3):
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_target, "Escape"],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.15)
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "C-c"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.5)

            # Send command as literal text (-l flag prevents key name interpretation)
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "-l", command],
                capture_output=True, text=True, timeout=5, check=True,
            )
            time.sleep(1.0)

            # Send C-m (carriage return) to submit — more reliable
            # than "Enter" with TUI agents like Claude Code and Codex
            result = subprocess.run(
                ["tmux", "send-keys", "-t", pane_target, "C-m"],
                capture_output=True, text=True, timeout=5,
            )

            if result.returncode == 0:
                return True

            _log(f"   Attempt {attempt}/{max_retries} failed"
                 f" (rc={result.returncode})")

        except subprocess.CalledProcessError as e:
            _log(f"   Attempt {attempt}/{max_retries} error:"
                 f" {e.stderr.strip() if e.stderr else e}")
        except Exception as e:
            _log(f"   Attempt {attempt}/{max_retries} error: {e}")

        if attempt < max_retries:
            _log(f"   Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    return False


def wait_for_idle_iterm(
    session_id: str,
    timeout: float = 300.0,
    poll_interval: float = 5.0,
) -> bool:
    """Wait until the agent in the given iTerm2 session is idle."""
    start = time.time()
    while time.time() - start < timeout:
        if is_agent_idle_iterm(session_id, debug=True):
            return True
        time.sleep(poll_interval)
    return False


def send_iterm_command(
    session_id: str,
    command: str,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> bool:
    """Send a command to an iTerm2 session with retry logic.

    Simpler than tmux: no pre-send input clearing is needed. Submission
    is handled inside write_text_to_session() with an explicit CR.
    """
    from tagteam.iterm import write_text_to_session, session_id_is_valid

    if not session_id_is_valid(session_id):
        _log(f"   ERROR: Session '{session_id}' does not exist")
        return False

    for attempt in range(1, max_retries + 1):
        _log(f"   Checking if agent is idle...")
        if not wait_for_idle_iterm(session_id, timeout=10.0, poll_interval=2.0):
            _log("   Idle detection inconclusive, proceeding after 10s")

        if write_text_to_session(session_id, command):
            return True

        _log(f"   Attempt {attempt}/{max_retries} failed")
        if attempt < max_retries:
            _log(f"   Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    return False


def _try_roadmap_advance(state: dict, project_dir: str = ".") -> dict | None:
    """Attempt to auto-advance to the next phase in full-roadmap mode.

    Called when status is "done" and result is "approved".
    Returns the new state if advanced, or None if no advance needed.

    Includes a staleness guard: re-reads state before writing to ensure
    the lead hasn't already advanced past this transition.
    """
    run_mode = state.get("run_mode", "single-phase")
    if run_mode != "full-roadmap":
        return None

    roadmap = state.get("roadmap")
    if not roadmap:
        return None

    result = state.get("result")
    current_type = state.get("type")

    if result != "approved":
        return None

    queue = roadmap.get("queue", [])
    idx = roadmap.get("current_index", 0)
    completed = roadmap.get("completed", [])

    if current_type == "plan":
        # Plan approved → hand to lead to implement.
        # STALENESS GUARD: If the state has already moved to type=impl,
        # the lead already started the impl cycle — skip this transition.
        fresh = read_state(project_dir)
        if fresh and fresh.get("type") == "impl":
            _log("   SKIP: plan→impl advance already happened (type is impl)")
            return None
        if fresh and fresh.get("status") == "ready" and fresh.get("turn") == "reviewer":
            _log("   SKIP: lead already submitted for review")
            return None

        phase = state.get("phase", "?")
        seq = state.get("seq", 0)
        updates = {
            "turn": "lead",
            "status": "ready",
            "result": None,
            "command": f"/handoff start {phase} impl",
        }
        new_state = update_state(updates, project_dir, expected_seq=seq)
        if new_state is None:
            _log("   SKIP: state changed since approval detected (seq mismatch)")
            return None
        _log(f"   AUTO-ADVANCE: plan approved → lead implements"
             f" (phase: {phase})")
        return new_state

    if current_type == "impl":
        # Impl approved → advance to next phase or complete.
        current_phase = state.get("phase")

        # STALENESS GUARD: If state already shows a different phase,
        # the lead already started the next phase — skip.
        fresh = read_state(project_dir)
        if fresh:
            fresh_phase = fresh.get("phase")
            if fresh_phase and current_phase:
                # Normalize both sides to avoid false positives when formats differ
                if normalize_phase_key(fresh_phase) != normalize_phase_key(current_phase):
                    _log(f"   SKIP: impl advance already happened"
                         f" (phase moved to {fresh_phase})")
                    return None
            if (fresh.get("status") == "ready"
                    and fresh.get("turn") == "reviewer"
                    and fresh.get("type") == "plan"):
                _log("   SKIP: lead already submitted next phase for review")
                return None

        # Normalize existing completed list to clean up any corruption from previous runs
        # (state["phase"] might be full phase-N-slug format from cycle commands)
        completed_normalized = [normalize_phase_key(p) for p in completed]

        if current_phase:
            phase_slug = normalize_phase_key(current_phase)
            if phase_slug not in completed_normalized:
                completed = completed_normalized + [phase_slug]
            else:
                # Already completed, but update to normalized list anyway to fix corruption
                completed = completed_normalized

        seq = state.get("seq", 0)

        if idx + 1 < len(queue):
            next_idx = idx + 1
            next_phase = queue[next_idx]
            roadmap_update = {
                "queue": queue,
                "current_index": next_idx,
                "completed": completed,
                "pause_reason": None,
            }
            updates = {
                "phase": next_phase,
                "type": "plan",
                "round": 1,
                "turn": "lead",
                "status": "ready",
                "result": None,
                "roadmap": roadmap_update,
                "command": f"/handoff start {next_phase}",
            }
            new_state = update_state(updates, project_dir, expected_seq=seq)
            if new_state is None:
                _log("   SKIP: state changed since approval detected (seq mismatch)")
                return None
            _log(f"   AUTO-ADVANCE: impl approved → lead starts next phase"
                 f" ({next_phase})")
            return new_state
        else:
            # Last phase — roadmap complete
            roadmap_update = {
                "queue": queue,
                "current_index": idx,
                "completed": completed,
                "pause_reason": None,
            }
            updates = {
                "status": "done",
                "result": "roadmap-complete",
                "roadmap": roadmap_update,
            }
            new_state = update_state(updates, project_dir, expected_seq=seq)
            if new_state is None:
                _log("   SKIP: state changed since approval detected (seq mismatch)")
                return None
            _log("   ROADMAP COMPLETE: all phases finished!")
            return new_state

    return None


def _log(msg: str) -> None:
    """Print with timestamp and flush (required for tmux pane output)."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        if value.endswith("Z"):
            try:
                parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class _StateProcessor:
    """Encapsulates per-tick processing logic for the watcher.

    A single instance is reused across all loop iterations so it carries
    the small bit of mutable state the loop needs (last seq, idle/send
    timing). Both the polling loop and the future event-driven loop call
    the same `tick()` method, ensuring identical behavior regardless of
    what triggered the tick.
    """

    RESEND_TIMEOUT = 300  # seconds — re-send command if still 'ready'

    def __init__(
        self,
        *,
        mode: str,
        lead_name: str,
        reviewer_name: str,
        lead_pane: str,
        reviewer_pane: str,
        lead_session_id: str | None,
        reviewer_session_id: str | None,
        confirm: bool,
        timeout_minutes: int,
        project_dir: str,
        max_retries: int,
        retry_delay: float,
        pre_send_delay: float,
        engine=None,
        briefer=None,
    ):
        self.mode = mode
        # Phase 31: HeadlessEngine when mode == "headless" (else None).
        self.engine = engine
        # Phase 33: BriefSpec (enabled) or None — escalation briefer.
        self.briefer = briefer
        self.lead_name = lead_name
        self.reviewer_name = reviewer_name
        self.lead_pane = lead_pane
        self.reviewer_pane = reviewer_pane
        self.lead_session_id = lead_session_id
        self.reviewer_session_id = reviewer_session_id
        self.confirm = confirm
        self.timeout_minutes = timeout_minutes
        self.project_dir = project_dir
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.pre_send_delay = pre_send_delay

        self.last_processed_seq: int | None = None
        self.last_processed_at: str | None = None
        self.idle_since: float = time.time()
        self.last_ready_send_time: float | None = None
        # Phase 32: pause marker honored in every mode. `_paused_seq` is the
        # seq we declined to dispatch while paused; when the marker clears
        # and that seq is still ready, tick() re-dispatches it exactly once.
        self._paused_seq: int | None = None
        self._last_pause_log: float = 0.0

    # -- pause (Phase 32) --------------------------------------------------

    def _pause_info(self) -> dict | None:
        from tagteam.headless import read_pause
        try:
            return read_pause(self.project_dir)
        except Exception:
            return None

    def _log_paused(self, info: dict, force: bool = False) -> None:
        now = time.time()
        if force or now - self._last_pause_log > 60:
            self._last_pause_log = now
            _log(f"!! PAUSED: {info.get('reason')}")
            _log("   resume with: tagteam resume")

    def try_repair(self) -> None:
        """Opportunistic shadow-DB repair, bounded by repair's own backoff."""
        try:
            from tagteam import repair as _repair
            if _repair.should_attempt_repair(self.project_dir):
                res = _repair.attempt_repair(self.project_dir)
                if res["success"]:
                    _log("[repair] db_invalid cleared after successful "
                         "rebuild + parity check")
                else:
                    if _repair.needs_louder_signal(self.project_dir):
                        _log(f"[repair] WARN: db_invalid set for >24h "
                             f"without recovery (last reason: "
                             f"{res.get('reason')})")
        except Exception as e:
            _log(f"[repair] error during opportunistic repair: {e}")

    def tick(self, state: dict) -> None:
        """Process one state observation. No-op if seq hasn't advanced
        (unless the watchdog re-send window has elapsed)."""
        current_seq = state.get("seq", 0)
        updated_at = state.get("updated_at") or "__missing__"

        # First-poll bootstrap: if the existing state isn't actionable,
        # just record and wait. If it IS actionable (ready), pick it up.
        if self.last_processed_seq is None:
            if state.get("status") != "ready":
                self.last_processed_seq = current_seq
                self.last_processed_at = updated_at
                self.idle_since = time.time()
                _log(f"Current state: {state.get('status', '?')}"
                     f" (turn: {state.get('turn', '?')},"
                     f" phase: {state.get('phase', '?')})")
                # Phase 33: a watcher (re)started on an already-escalated
                # cycle briefs it once (claim dedupe makes this idempotent).
                # Every other non-ready first state is unchanged.
                if state.get("status") == "escalated":
                    self._maybe_brief(state)
                return
            _log("Picking up active turn from existing state")

        # Seq dedup with stuck-agent + watchdog re-send logic
        if current_seq == self.last_processed_seq:
            # Phase 32: resume re-dispatch — we declined this seq while
            # paused; if the marker is now gone and the state is still
            # ready, dispatch it exactly once.
            if (self._paused_seq == current_seq
                    and state.get("status") == "ready"):
                if self._pause_info() is None:
                    self._paused_seq = None
                    _log("Resumed — dispatching the still-owed turn")
                    self._dispatch(state)
                    return
                self._log_paused(self._pause_info() or {})
                return
            elapsed = time.time() - self.idle_since
            if (elapsed > self.timeout_minutes * 60
                    and state.get("status") == "working"):
                _log(f"Warning: no state change for {self.timeout_minutes}m"
                     " - agent may be stuck")
                notify_macos("Tagteam",
                             f"No activity for {self.timeout_minutes}m")
                self.idle_since = time.time()

            if self.mode == "headless":
                # A headless turn is a synchronous spawn; re-sending would
                # start a duplicate agent process. If dispatch is paused
                # (failed turn), keep the operator informed instead.
                if state.get("status") == "ready":
                    info = self._pause_info()
                    if info is not None:
                        self._log_paused(info)
                    # Phase 37: the owed turn was declined because another
                    # turn (a lead conversation, the briefer) held the slot;
                    # dispatch it once the slot is free.
                    elif self.engine is not None and self.engine.slot_busy is not None:
                        from tagteam import headless as _h
                        if not _h.slot_status(self.project_dir)["held"]:
                            _log("Turn slot freed — dispatching the still-owed turn")
                            self.engine.slot_busy = None
                            self._dispatch(state)
                return

            if (state.get("status") == "ready"
                    and self.last_ready_send_time is not None
                    and (time.time() - self.last_ready_send_time
                         > self.RESEND_TIMEOUT)):
                _log("Watchdog: state still 'ready' after 5m"
                     " — re-sending command")
                self.last_ready_send_time = None  # avoid rapid re-sends
                # fall through to re-process
            else:
                return

        # New state (or watchdog re-send) — record and dispatch
        self.last_processed_seq = current_seq
        self.last_processed_at = updated_at
        self.idle_since = time.time()
        self._dispatch(state)

    def _dispatch(self, state: dict) -> None:
        current_status = state.get("status")
        current_turn = state.get("turn")
        command = state.get("command", "")
        phase = state.get("phase", "?")
        round_num = state.get("round", "?")

        agent_name = (self.lead_name if current_turn == "lead"
                      else self.reviewer_name)
        pane = self.lead_pane if current_turn == "lead" else self.reviewer_pane
        session_id = (self.lead_session_id if current_turn == "lead"
                      else self.reviewer_session_id)

        if current_status == "ready" and command:
            self._handle_ready(agent_name, pane, session_id,
                               command, phase, round_num, state=state)
        elif current_status == "working":
            _log(f"   {agent_name} is working...")
        elif current_status == "done":
            self._handle_done(state)
        elif current_status == "escalated":
            self._handle_escalated(state)
        elif current_status == "aborted":
            reason = state.get("reason", "unknown")
            _log(f"-- Cycle aborted: {reason}")
            notify_macos("Tagteam", f"Cycle aborted: {reason}")

    def _handle_ready(self, agent_name, pane, session_id,
                      command, phase, round_num, state=None):
        _log(f">> {agent_name}'s turn"
             f" (phase: {phase}, round: {round_num})")
        # Phase 32: the pause marker holds dispatch in EVERY mode. Do not
        # arm the watchdog re-send while paused; remember the seq so resume
        # can re-dispatch it once.
        info = self._pause_info()
        if info is not None:
            self._paused_seq = (state or {}).get("seq")
            self._log_paused(info, force=True)
            return
        self._paused_seq = None
        send_success = False

        if self.mode == "iterm2":
            if self.confirm:
                try:
                    input(f"[{_ts()}]    Press Enter to send"
                          f" '{command}' to {agent_name}...")
                except EOFError:
                    return
            send_success = send_iterm_command(
                session_id, command,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
            )
            if send_success:
                _log(f"   Sent to {agent_name}: {command}")
            else:
                _log(f"   FAILED: Could not send to"
                     f" {agent_name} after {self.max_retries} attempts")
                notify_macos("Tagteam", f"Failed to send to {agent_name}")

        elif self.mode == "tmux":
            if self.confirm:
                try:
                    input(f"[{_ts()}]    Press Enter to send"
                          f" '{command}' to {pane}...")
                except EOFError:
                    return
            send_success = send_tmux_keys(
                pane, command,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                pre_send_delay=self.pre_send_delay,
            )
            if send_success:
                _log(f"   Sent to {pane}: {command}")
            else:
                _log(f"   FAILED: Could not send to '{pane}'"
                     f" after {self.max_retries} attempts")
                notify_macos("Tagteam",
                             f"Failed to send to {pane} after retries")

        elif self.mode == "notify":
            send_success = True
            _log(f"   Command: {command}")
            notify_macos("Tagteam", f"{agent_name}'s turn: {command}")

        elif self.mode == "headless":
            # Phase 31: spawn a fresh agent process for this turn and block
            # until it exits. The engine verifies the expected cycle
            # transition, records usage, and pauses dispatch on failure.
            # No send-time bookkeeping: the watchdog re-send path is
            # short-circuited for headless in tick().
            if self.engine is None:
                _log("   ERROR: headless mode without an engine")
                return
            _log(f"   Command: {command}")
            self.engine.run_owed_turn(state or {})
            return

        # Track send time for watchdog re-send (success OR failure;
        # failure also gets a retry window via watchdog).
        self.last_ready_send_time = time.time()

    def _handle_done(self, state: dict) -> None:
        result = state.get("result", "completed")

        advanced = _try_roadmap_advance(state, self.project_dir)
        if advanced:
            self.last_processed_at = None
            self.idle_since = time.time()
            return

        done_msg = "/handoff"
        if result == "roadmap-complete":
            _log("** Roadmap complete: all phases finished!")
            notify_macos("Tagteam", "Roadmap complete!")
        else:
            _log(f"** Cycle complete: {result}")
            notify_macos("Tagteam", f"Cycle complete: {result}")

        _log(f"   Sending completion notice to {self.lead_name}...")
        if self.mode == "iterm2":
            send_iterm_command(
                self.lead_session_id, done_msg,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
            )
        elif self.mode == "tmux":
            send_tmux_keys(
                self.lead_pane, done_msg,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                pre_send_delay=self.pre_send_delay,
            )

    def _handle_escalated(self, state: dict) -> None:
        roadmap = state.get("roadmap") or {}
        pause_reason = roadmap.get("pause_reason") or state.get("reason")
        if pause_reason:
            _log(f"!! Paused: {pause_reason}")
            _log("   Resume with: python -m tagteam state set"
                 " --status ready --turn <lead|reviewer>")
            notify_macos("Tagteam", f"Paused: {pause_reason}")
        else:
            _log("!! Escalated to human arbiter")
            notify_macos("Tagteam", "Escalated to human arbiter!")
            self._maybe_brief(state)

    def _maybe_brief(self, state: dict) -> None:
        """Phase 33: run the escalation briefer once per escalation event.
        No-op when the briefer is not enabled (0.9.0 behavior), for
        roadmap-advance pauses, and when the canonical cycle status is not
        escalated/needs-human. Idempotent via the briefs claim."""
        if self.briefer is None or not getattr(self.briefer, "enabled", False):
            return
        roadmap = state.get("roadmap") or {}
        if roadmap.get("pause_reason") or state.get("reason"):
            return
        try:
            from tagteam import briefer as _briefer
            res = _briefer.run_briefer(self.project_dir, kind="auto", spec=self.briefer,
                                       log=_log, notify=notify_macos)
            if res.status in ("ok", "partial"):
                _log(f"   brief: {res.path}")
            elif res.status == "failed":
                _log(f"   brief failed: {res.reason} — `tagteam brief --generate` to retry")
        except Exception as e:  # the loop must never die because of the briefer
            _log(f"   briefer error: {type(e).__name__}: {e}")


def _build_processor(
    *,
    mode: str,
    lead_pane: str,
    reviewer_pane: str,
    confirm: bool,
    timeout_minutes: int,
    project_dir: str,
    max_retries: int,
    retry_delay: float,
    pre_send_delay: float,
    turn_timeout_minutes: int | None = None,
    tail_rounds: int | None = None,
    turn_retries: int = 0,
) -> _StateProcessor | None:
    """Resolve config + iTerm session IDs into a ready processor.

    Returns None if iterm2 mode is requested but session IDs are missing,
    or if headless mode fails startup validation (caller should bail out
    — error already logged here).
    """
    config_path = Path(project_dir) / "tagteam.yaml"
    config = read_config(config_path)
    if config:
        lead_name, reviewer_name = get_agent_names(config)
        lead_name = lead_name or "lead"
        reviewer_name = reviewer_name or "reviewer"
    else:
        lead_name = "lead"
        reviewer_name = "reviewer"

    lead_session_id = None
    reviewer_session_id = None
    if mode == "iterm2":
        from tagteam.iterm import get_session_id
        lead_session_id = get_session_id("lead", project_dir)
        reviewer_session_id = get_session_id("reviewer", project_dir)
        if not lead_session_id or not reviewer_session_id:
            _log("ERROR: Could not find session IDs in .handoff-session.json")
            _log("  Run 'python -m tagteam session start' first.")
            return None

    # Phase 33: escalation briefer — opt-in; problems warn and disable, never block.
    briefer_spec = None
    try:
        from tagteam.briefer import resolve_briefer
        bs = resolve_briefer(config or {}, project_dir)
        if bs.enabled:
            briefer_spec = bs
        elif bs.problems:
            _log("WARNING: escalation briefer disabled for this run:")
            for pr in bs.problems:
                _log(f"  - {pr}")
    except Exception as e:
        _log(f"WARNING: escalation briefer disabled for this run: {e}")

    engine = None
    if mode == "headless":
        from tagteam.headless import (HeadlessEngine,
                                      DEFAULT_TURN_TIMEOUT_MINUTES,
                                      DEFAULT_TAIL_ROUNDS)
        engine = HeadlessEngine(
            project_dir, config,
            lead_name=lead_name, reviewer_name=reviewer_name,
            timeout_minutes=(turn_timeout_minutes
                             if turn_timeout_minutes is not None
                             else DEFAULT_TURN_TIMEOUT_MINUTES),
            tail_rounds=(tail_rounds if tail_rounds is not None
                         else DEFAULT_TAIL_ROUNDS),
            confirm=confirm, log=_log, notify=notify_macos,
            retries=turn_retries,
        )
        errors = engine.validate()
        if errors:
            _log("ERROR: headless mode cannot start:")
            for e in errors:
                _log(f"  - {e}")
            return None

    return _StateProcessor(
        mode=mode,
        lead_name=lead_name,
        reviewer_name=reviewer_name,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        lead_session_id=lead_session_id,
        reviewer_session_id=reviewer_session_id,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        project_dir=project_dir,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
        engine=engine,
        briefer=briefer_spec,
    )


def _log_startup_banner(processor: _StateProcessor, interval: int) -> None:
    _log(f"Watching handoff-state.json"
         f" (interval: {interval}s, mode: {processor.mode})")
    _log(f"Lead: {processor.lead_name} | Reviewer: {processor.reviewer_name}")
    if processor.mode == "tmux":
        _log(f"Panes: lead={processor.lead_pane},"
             f" reviewer={processor.reviewer_pane}")
        for name, pane in [("lead", processor.lead_pane),
                           ("reviewer", processor.reviewer_pane)]:
            if pane_exists(pane):
                _log(f"  {name} pane OK: {pane}")
            else:
                _log(f"  WARNING: {name} pane '{pane}' not found")
    elif processor.mode == "iterm2":
        from tagteam.iterm import session_id_is_valid
        for name, sid in [("lead", processor.lead_session_id),
                          ("reviewer", processor.reviewer_session_id)]:
            if session_id_is_valid(sid):
                _log(f"  {name} session OK: {sid}")
            else:
                _log(f"  WARNING: {name} session '{sid}'"
                     " not found in iTerm2")
    elif processor.mode == "headless" and processor.engine is not None:
        eng = processor.engine
        for role in ("lead", "reviewer"):
            spec = eng.roles.get(role)
            if spec:
                _log(f"  {role}: {spec.provider} via {spec.executable}")
        _log(f"  turn timeout: {eng.timeout_s / 60:.0f} min |"
             f" round tail: {eng.tail_n} | retries: {eng.retries} |"
             f" logs: {eng.project_root}/.tagteam/turns/")
    if processor.briefer is not None:
        _log(f"  escalation briefer: on ({processor.briefer.provider}, "
             f"timeout {processor.briefer.timeout_s / 60:.0f} min)")
    info = processor._pause_info()
    if info is not None:
        processor._log_paused(info, force=True)
    if processor.confirm:
        _log("Confirm mode: will pause before sending commands")
    print(flush=True)


# ---------------------------------------------------------------------------
# Watcher pidfile (Phase 34): a project-bound liveness record. Tagteam's own
# launch shape (`python -m tagteam watch --mode X` from the project cwd) puts
# no project path on argv, so argv-matching cannot bind a watcher to a
# project; the pidfile (pid + creation identity + mode) can. Written at
# start, removed on clean exit; a stale file (dead pid / identity mismatch)
# is reported, never trusted.
#
# OPT-IN (3.0 arc §2: flag-off behavior is identical to the previous
# release): the file is written only when the project has opted into the
# cockpit (`serve: {theme: cockpit}` in tagteam.yaml) or the watcher was
# started with `--pidfile`. Otherwise `tagteam watch` writes nothing new;
# the cockpit then falls back to the cwd-bound process scan / in-flight
# identity (see `cockpit_api.watcher_status`).
# ---------------------------------------------------------------------------

WATCHER_PIDFILE = "watcher.json"


def pidfile_enabled(project_root: str | Path, explicit: bool | None = None) -> bool:
    """True when the watcher should keep a pidfile: `--pidfile` given, or
    the project's tagteam.yaml has `serve.theme: cockpit`. Never raises."""
    if explicit is not None:
        return bool(explicit)
    try:
        cfg = read_config(Path(project_root) / "tagteam.yaml") or {}
        serve = cfg.get("serve") if isinstance(cfg, dict) else None
        theme = serve.get("theme") if isinstance(serve, dict) else None
        return isinstance(theme, str) and theme.strip().lower() == "cockpit"
    except Exception:
        return False


def pidfile_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".tagteam" / WATCHER_PIDFILE


def write_pidfile(project_root: str | Path, mode: str) -> Path | None:
    """Record this process as the project's watcher. Best-effort."""
    import json
    import os
    from tagteam import procs
    p = pidfile_path(project_root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "ident": procs.identity(os.getpid()), "mode": mode,
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "argv": list(sys.argv), "project_dir": str(Path(project_root).resolve())}
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return p
    except OSError:
        return None


def read_pidfile(project_root: str | Path) -> dict | None:
    import json
    p = pidfile_path(project_root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def remove_pidfile(project_root: str | Path, pid: int | None = None) -> bool:
    """Remove the pidfile if it names `pid` (default: this process)."""
    import os
    pid = os.getpid() if pid is None else pid
    cur = read_pidfile(project_root)
    if cur is None or cur.get("pid") != pid:
        return False
    try:
        pidfile_path(project_root).unlink()
        return True
    except OSError:
        return False


def _pidfile_root(project_dir: str) -> str:
    if project_dir == ".":
        try:
            from tagteam.state import _resolve_project_root
            return _resolve_project_root()
        except Exception:
            return "."
    return project_dir


def watch(
    interval: int = 10,
    mode: str = "notify",
    lead_pane: str = "tagteam:0.0",
    reviewer_pane: str = "tagteam:0.2",
    confirm: bool = False,
    timeout_minutes: int = 30,
    project_dir: str = ".",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    pre_send_delay: float = 1.0,
    force_poll: bool = False,
    turn_timeout_minutes: int | None = None,
    tail_rounds: int | None = None,
    turn_retries: int = 0,
    pidfile: bool | None = None,
) -> bool:
    """Main watch loop. Blocks until interrupted with Ctrl-C.

    `pidfile`: True/False forces the Phase 34 liveness record on/off; None
    (default) follows the project's cockpit opt-in (`serve.theme: cockpit`).

    Returns False if the watcher could not start (missing iTerm session
    IDs, headless startup validation failed); True otherwise.

    Delegates per-tick work to _StateProcessor. Trigger source is either
    polling (default fallback when ``watchdog`` isn't installed, or when
    ``force_poll=True``) or watchdog filesystem events (when available).
    """
    processor = _build_processor(
        mode=mode,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        project_dir=project_dir,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
        turn_timeout_minutes=turn_timeout_minutes,
        tail_rounds=tail_rounds,
        turn_retries=turn_retries,
    )
    if processor is None:
        return False

    _log_startup_banner(processor, interval)

    if mode == "headless" and not force_poll:
        # A headless tick blocks for the length of a turn; running that
        # inside a watchdog callback is the wrong shape. Poll instead.
        force_poll = True
        _log("[trigger] headless mode uses poll trigger")

    pidfile_root = _pidfile_root(project_dir)
    keep_pidfile = pidfile_enabled(pidfile_root, pidfile)
    if keep_pidfile:
        write_pidfile(pidfile_root, mode)
    try:
        if not force_poll:
            from tagteam import watcher_events
            if watcher_events.is_available():
                _log("[trigger] event-driven (watchdog) with 30s heartbeat")
                if _run_event_loop(processor, project_dir):
                    return True
                # Event loop failed at startup — fall through to poll mode.
                _log(f"[trigger] falling back to poll mode"
                     f" (interval={interval}s)")
            else:
                _log("[trigger] poll mode (install `tagteam[event]`"
                     " to enable event-driven mode)")
        else:
            _log(f"[trigger] poll mode (forced via --poll, interval={interval}s)")

        _run_poll_loop(processor, project_dir, interval)
        return True
    finally:
        if keep_pidfile:
            remove_pidfile(pidfile_root)


def _run_poll_loop(processor: "_StateProcessor",
                   project_dir: str, interval: int) -> None:
    try:
        while True:
            processor.try_repair()
            state = read_state(project_dir)
            if state is not None:
                processor.tick(state)
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("Watcher stopped.")


def _run_event_loop(processor: "_StateProcessor", project_dir: str) -> bool:
    """Run the event-driven loop. Returns True on clean exit (Ctrl-C),
    False if the watchdog observer failed to start (caller should fall
    back to poll mode).

    Filesystem-event backends can fail at runtime even when watchdog
    imports cleanly — for example macOS FSEvents can raise
    ``SystemError: Cannot start fsevents stream`` on certain volumes,
    and inotify can hit ``OSError(ENOSPC)`` when the user's
    inotify-watch quota is exhausted. In those cases we log the reason
    and let watch() drop back to polling so the watcher keeps running.
    """
    from tagteam import watcher_events

    state_path = get_state_path(project_dir)

    def on_change():
        processor.try_repair()
        state = read_state(project_dir)
        if state is not None:
            processor.tick(state)

    try:
        watcher_events.watch_with_events(state_path, on_change)
    except KeyboardInterrupt:
        _log("Watcher stopped.")
        return True
    except Exception as e:
        _log(f"[trigger] event mode failed:"
             f" {type(e).__name__}: {e}")
        return False
    return True


# --- CLI entry point ---

def _auto_detect_mode(project_dir: str = ".") -> tuple[str, str | None]:
    """Pick the best send-keys mode based on what's actually set up.

    Returns (mode, reason). `reason` is a human-readable one-liner the
    caller can log so the operator knows why this mode was chosen
    (or why we fell back to notify).

    Priority:
      1. iterm2 — if `.handoff-session.json` has session IDs for BOTH
         lead and reviewer roles. Means `tagteam session start
         --backend iterm2 --launch` ran successfully.
      2. tmux — if the default tmux session exists. Means
         `tagteam session start --backend tmux --launch` ran.
      3. notify — fallback. The watcher will pop macOS notifications
         but won't auto-type into either agent's terminal.
    """
    try:
        from tagteam.iterm import get_session_id
        lead_sid = get_session_id("lead", project_dir)
        reviewer_sid = get_session_id("reviewer", project_dir)
        if lead_sid and reviewer_sid:
            return "iterm2", "iterm2 session IDs found"
    except Exception:
        pass

    try:
        from tagteam.session import session_exists
        if session_exists():
            return "tmux", "tmux session 'tagteam' found"
    except Exception:
        pass

    return "notify", (
        "no iterm2 session file or tmux session detected — "
        "watcher will only post notifications. Run "
        "`tagteam session start --launch` to enable auto-send."
    )


def watch_command(args: list[str]) -> int:
    """Parse CLI args and start the watcher."""
    interval = 10
    mode = None  # None = auto-detect; explicit --mode overrides.
    lead_pane = "tagteam:0.0"
    reviewer_pane = "tagteam:0.2"
    confirm = False
    timeout_minutes = 30
    max_retries = 3
    retry_delay = 2.0
    pre_send_delay = 1.0
    force_poll = False
    turn_timeout_minutes = None
    tail_rounds = None
    turn_retries = 0
    pidfile = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--pidfile":
            pidfile = True
            i += 1
        elif arg == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
            i += 2
        elif arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            if mode not in ("notify", "tmux", "iterm2", "headless"):
                print(f"Invalid mode: {mode}. Use 'notify', 'tmux', 'iterm2',"
                      " or 'headless'.")
                return 1
            i += 2
        elif arg == "--lead-pane" and i + 1 < len(args):
            lead_pane = args[i + 1]
            i += 2
        elif arg == "--reviewer-pane" and i + 1 < len(args):
            reviewer_pane = args[i + 1]
            i += 2
        elif arg == "--confirm":
            confirm = True
            i += 1
        elif arg == "--timeout" and i + 1 < len(args):
            timeout_minutes = int(args[i + 1])
            i += 2
        elif arg == "--retries" and i + 1 < len(args):
            max_retries = int(args[i + 1])
            i += 2
        elif arg == "--retry-delay" and i + 1 < len(args):
            retry_delay = float(args[i + 1])
            i += 2
        elif arg == "--send-delay" and i + 1 < len(args):
            pre_send_delay = float(args[i + 1])
            i += 2
        elif arg == "--poll":
            force_poll = True
            i += 1
        elif arg == "--turn-timeout" and i + 1 < len(args):
            turn_timeout_minutes = int(args[i + 1])
            i += 2
        elif arg == "--tail-rounds" and i + 1 < len(args):
            tail_rounds = int(args[i + 1])
            i += 2
        elif arg == "--turn-retries" and i + 1 < len(args):
            turn_retries = int(args[i + 1])
            if turn_retries < 0:
                print("--turn-retries must be >= 0")
                return 1
            i += 2
        elif arg in ("-h", "--help"):
            print("Usage: python -m tagteam watch [options]")
            print()
            print("Options:")
            print("  --interval N       Poll interval in seconds (default: 10)")
            print("  --mode MODE        'notify', 'tmux', 'iterm2', or 'headless'")
            print("                     (default: auto-detect from session state;")
            print("                      'headless' is never auto-detected)")
            print("  --lead-pane TARGET tmux pane target for lead (default: tagteam:0.0)")
            print("  --reviewer-pane T  tmux pane target for reviewer (default: tagteam:0.2)")
            print("  --confirm          Pause for confirmation before sending commands")
            print("  --timeout N        Alert after N minutes of inactivity (default: 30)")
            print("  --retries N        Max send retries on failure (default: 3)")
            print("  --retry-delay N    Seconds between retries (default: 2.0)")
            print("  --send-delay N     Seconds to wait before sending (default: 1.0)")
            print("  --poll             Force polling mode (skip watchdog event detection)")
            print("  --pidfile          Keep .tagteam/watcher.json (pid + identity) for the cockpit's")
            print("                     liveness strip; implied by `serve: {theme: cockpit}` in tagteam.yaml")
            print()
            print("Headless mode (opt-in, --mode headless):")
            print("  --turn-timeout N   Kill a spawned turn after N minutes (default: 60)")
            print("  --tail-rounds N    Rounds of history in each turn's context (default: 3)")
            print("  --turn-retries N   Retry a failed turn up to N times, only when the")
            print("                     repo and handoff fingerprints are unchanged (default: 0)")
            print("  Follow the in-flight turn with: tagteam tail")
            print("  Controls (any mode): tagteam pause / resume / cancel-turn / interject")
            return 0
        else:
            print(f"Unknown argument: {arg}")
            return 1

    if mode is None:
        mode, reason = _auto_detect_mode(".")
        _log(f"[mode] auto-detected: {mode} ({reason})")

    started = watch(
        interval=interval,
        mode=mode,
        lead_pane=lead_pane,
        reviewer_pane=reviewer_pane,
        confirm=confirm,
        timeout_minutes=timeout_minutes,
        max_retries=max_retries,
        retry_delay=retry_delay,
        pre_send_delay=pre_send_delay,
        force_poll=force_poll,
        turn_timeout_minutes=turn_timeout_minutes,
        tail_rounds=tail_rounds,
        turn_retries=turn_retries,
        pidfile=pidfile,
    )
    return 0 if started else 1
