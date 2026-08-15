"""
Headless turn engine (Phase 31, 3.0 arc).

In ``tagteam watch --mode headless`` each turn is a fresh process instead
of keystrokes typed into a long-lived terminal: the orchestrator composes
a bounded context (handoff skill contract + current state + round-log
tail + the state's command), spawns the owed agent through its signed-in
CLI (``claude -p`` / ``codex exec``) with that prompt on stdin, streams
the structured stdout events and stderr to per-turn files, waits (with a
timeout), verifies that the *expected cycle transition* happened, and
records per-turn token usage in the ``usage`` table.

The spawned agent still writes its own round via ``tagteam cycle add`` /
``cycle init`` exactly as an interactive agent would — this module never
parses agent prose into a round. Failure (timeout, nonzero exit, exit 0
without the expected transition) pauses dispatch via a marker file and
notifies; there are no automatic retries.

Design reference: docs/phases/headless-turn-engine-30-arc.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tagteam.config import HEADLESS_PROVIDERS, get_headless_spec

# ---------------------------------------------------------------------------
# Constants / paths
# ---------------------------------------------------------------------------

TURNS_RELDIR = Path(".tagteam") / "turns"
INFLIGHT_NAME = "inflight.json"
PAUSE_RELPATH = Path(".tagteam") / "headless-paused.json"
SKILL_RELPATH = Path(".claude") / "skills" / "handoff" / "SKILL.md"

DEFAULT_TURN_TIMEOUT_MINUTES = 60
DEFAULT_TAIL_ROUNDS = 3
KEEP_TURN_LOGS = 50

# The standard "act on your turn" command written by cycle add/init.
STANDARD_TURN_COMMAND = (
    "Read .claude/skills/handoff/SKILL.md and handoff-state.json, "
    "then act on your turn"
)

OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_NONZERO = "nonzero_exit"
OUTCOME_NO_ROUND = "no_round"

VARIADIC = -1  # option arity: one or more values until the next flag

_LEAD_ACTIONS = {"SUBMIT_FOR_REVIEW"}
_REVIEWER_ACTIONS = {"APPROVE", "REQUEST_CHANGES", "ESCALATE", "NEED_HUMAN"}


class HeadlessConfigError(ValueError):
    """Raised at startup for unresolvable executables or invalid args."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Adapter:
    """Per-provider invocation + event-parsing spec.

    ``canonical_head``/``canonical_tail`` are the flags tagteam owns
    (mode, structured output, cwd, prompt source). ``defaults`` are
    permission/sandbox flags grouped by *family*; a family's default is
    dropped when the user's ``headless.args`` sets an option of that
    family (``overridable``). ``options`` is the allowlist of user options
    with their arity; anything else in ``headless.args`` is rejected.
    ``reserved`` names are rejected in every spelling.
    """
    provider: str
    canonical_head: tuple[str, ...]
    canonical_tail: tuple[str, ...]
    defaults: dict[str, tuple[str, ...]]
    options: dict[str, int]
    reserved: frozenset[str]
    overridable: dict[str, str]
    short_with_value: frozenset[str] = frozenset()

    def family_of(self, name: str, values: list[str]) -> str | None:
        fam = self.overridable.get(name)
        if fam is not None:
            return fam
        return None


class ClaudeAdapter(Adapter):
    def family_of(self, name, values):  # noqa: D401 - see Adapter
        return super().family_of(name, values)


class CodexAdapter(Adapter):
    def family_of(self, name, values):
        fam = super().family_of(name, values)
        if fam is not None:
            return fam
        # `-c approval_policy=...` / `--config approval_policy=...`
        if name in ("-c", "--config") and values and \
                values[0].split("=", 1)[0].strip() == "approval_policy":
            return "approval"
        return None


CLAUDE = ClaudeAdapter(
    provider="claude",
    canonical_head=("-p", "--output-format", "stream-json", "--verbose"),
    canonical_tail=(),
    defaults={
        "permission": ("--permission-mode", "acceptEdits"),
        "tools": ("--allowedTools", "Bash", "Read", "Edit", "Write",
                  "Glob", "Grep"),
    },
    options={
        "--model": 1,
        "--fallback-model": 1,
        "--effort": 1,
        "--permission-mode": 1,
        "--dangerously-skip-permissions": 0,
        "--allowedTools": VARIADIC,
        "--allowed-tools": VARIADIC,
        "--disallowedTools": VARIADIC,
        "--disallowed-tools": VARIADIC,
        "--tools": VARIADIC,
        "--add-dir": VARIADIC,
        "--max-turns": 1,
        "--max-budget-usd": 1,
        "--append-system-prompt": 1,
        "--append-system-prompt-file": 1,
        "--system-prompt": 1,
        "--system-prompt-file": 1,
        "--settings": 1,
        "--setting-sources": 1,
        "--mcp-config": VARIADIC,
        "--strict-mcp-config": 0,
        "--plugin-dir": 1,
        "--betas": VARIADIC,
        "--no-session-persistence": 0,
        "--disable-slash-commands": 0,
        "--forward-subagent-text": 0,
    },
    reserved=frozenset({
        "-p", "--print", "--output-format", "--input-format", "--verbose",
        "-r", "--resume", "-c", "--continue", "--session-id",
        "--include-partial-messages", "--replay-user-messages",
        "--include-hook-events", "--from-pr", "--fork-session",
        "-h", "--help", "-v", "--version", "--ide", "--worktree", "-w",
    }),
    overridable={
        "--permission-mode": "permission",
        "--dangerously-skip-permissions": "permission",
        "--allowedTools": "tools",
        "--allowed-tools": "tools",
        "--tools": "tools",
    },
    short_with_value=frozenset(),
)

CODEX = CodexAdapter(
    provider="codex",
    # `-C <root>` is inserted at build time (see build_argv).
    canonical_head=("exec", "--json"),
    canonical_tail=("--skip-git-repo-check", "-"),
    defaults={
        "sandbox": ("--sandbox", "workspace-write"),
        "approval": ("-c", "approval_policy=never"),
    },
    options={
        "-m": 1, "--model": 1,
        "-s": 1, "--sandbox": 1,
        "-c": 1, "--config": 1,
        "-a": 1, "--ask-for-approval": 1,
        "--approve-for-me": 0,
        "--dangerously-bypass-approvals-and-sandbox": 0,
        "--dangerously-bypass-hook-trust": 0,
        "--add-dir": 1,
        "-p": 1, "--profile": 1,
        "--oss": 0,
        "--local-provider": 1,
        "--ignore-user-config": 0,
        "--ignore-rules": 0,
        "--color": 1,
        "--enable": 1, "--disable": 1,
    },
    reserved=frozenset({
        "exec", "--json", "-C", "--cd", "-o", "--output-last-message",
        "--output-schema", "--ephemeral", "--skip-git-repo-check",
        "-i", "--image", "-h", "--help", "-V", "--version",
    }),
    overridable={
        "-s": "sandbox", "--sandbox": "sandbox",
        "-a": "approval", "--ask-for-approval": "approval",
        "--approve-for-me": "approval",
        "--dangerously-bypass-approvals-and-sandbox": "approval",
    },
    short_with_value=frozenset({"-C", "-m", "-s", "-c", "-o", "-p", "-a", "-i"}),
)

ADAPTERS: dict[str, Adapter] = {"claude": CLAUDE, "codex": CODEX}


def _normalize_token(adapter: Adapter, tok: str) -> tuple[str, str | None]:
    """Split a flag token into (option_name, attached_value | None).

    ``--flag=value`` → ("--flag", "value"); ``-Cdir`` → ("-C", "dir") for
    short options that accept attached values; otherwise (tok, None).
    """
    if tok.startswith("--"):
        if "=" in tok:
            name, val = tok.split("=", 1)
            return name, val
        return tok, None
    # short option
    if len(tok) > 2 and tok[:2] in adapter.short_with_value:
        return tok[:2], tok[2:]
    return tok, None


def _looks_like_flag_or_marker(tok: str) -> bool:
    return tok == "-" or tok == "--" or tok.startswith("-")


def validate_user_args(adapter: Adapter, user_args: list[str]) -> tuple[list[str], set[str]]:
    """Structurally validate ``headless.args`` against the adapter's option
    table. Returns (normalized_tokens, overridden_families) or raises
    ``HeadlessConfigError`` naming the offending token.

    Every token must be consumed as a known option plus its arity of
    values. Positionals (bare text), ``-``, ``--``, unknown flags,
    reserved flags in any spelling, and dangling/flag-shaped values are
    all errors — so no user token can become the CLI's prompt or an
    option terminator, and the composed stdin prompt keeps ownership.
    """
    if not isinstance(user_args, list) or not all(isinstance(a, str) for a in user_args):
        raise HeadlessConfigError(
            "headless.args must be a list of strings (never a shell string)")
    out: list[str] = []
    families: set[str] = set()
    i = 0
    n = len(user_args)
    while i < n:
        tok = user_args[i]
        if tok in ("-", "--"):
            raise HeadlessConfigError(
                f"headless.args token {tok!r} is not allowed (prompt marker / "
                f"option terminator would displace the stdin prompt)")
        if not tok.startswith("-") or tok.strip() == "":
            raise HeadlessConfigError(
                f"headless.args token {tok!r} is positional text; only known "
                f"options are allowed (a positional would become the prompt)")
        name, attached = _normalize_token(adapter, tok)
        if name in adapter.reserved:
            raise HeadlessConfigError(
                f"headless.args token {tok!r} uses reserved option {name!r} "
                f"(owned by tagteam for provider {adapter.provider})")
        if name not in adapter.options:
            raise HeadlessConfigError(
                f"headless.args token {tok!r} is not a known {adapter.provider} "
                f"option; allowed: {', '.join(sorted(adapter.options))}")
        arity = adapter.options[name]
        values: list[str] = []
        i += 1
        if attached is not None:
            if arity == 0:
                raise HeadlessConfigError(
                    f"headless.args option {name!r} takes no value (got {tok!r})")
            values.append(attached)
            if arity == VARIADIC:
                while i < n and not _looks_like_flag_or_marker(user_args[i]):
                    values.append(user_args[i]); i += 1
        elif arity == 1:
            if i >= n or _looks_like_flag_or_marker(user_args[i]):
                got = user_args[i] if i < n else "<end>"
                raise HeadlessConfigError(
                    f"headless.args option {name!r} requires a value; got {got!r}")
            values.append(user_args[i]); i += 1
        elif arity == VARIADIC:
            while i < n and not _looks_like_flag_or_marker(user_args[i]):
                values.append(user_args[i]); i += 1
            if not values:
                raise HeadlessConfigError(
                    f"headless.args option {name!r} requires at least one value")
        # arity 0: nothing to consume
        fam = adapter.family_of(name, values)
        if fam:
            families.add(fam)
        out.append(name)
        out.extend(values)
    return out, families


def resolve_executable(provider: str, configured: str | None) -> str:
    """argv[0]: ``headless.executable`` if set (resolved with which when it
    isn't a path), else ``shutil.which(provider)``. Raises when unresolvable."""
    if configured:
        cand = configured
        p = Path(cand)
        if p.is_absolute() or os.sep in cand or (os.altsep and os.altsep in cand):
            if p.exists():
                return str(p)
            raise HeadlessConfigError(
                f"headless.executable {configured!r} does not exist")
        found = shutil.which(cand)
        if not found:
            raise HeadlessConfigError(
                f"headless.executable {configured!r} not found on PATH")
        return found
    found = shutil.which(provider)
    if not found:
        raise HeadlessConfigError(
            f"{provider!r} CLI not found on PATH; install it or set "
            f"agents.<role>.headless.executable")
    return found


def build_argv(adapter: Adapter, executable: str, user_args: list[str],
               project_root: str | Path) -> list[str]:
    """Deterministic argv: ``[exe] + canonical_head (+ -C root for codex)
    + effective defaults + validated user args + canonical_tail``.

    The prompt-source marker (codex ``-``) is always the final token and
    nothing user-supplied can follow it; claude reads the prompt from
    stdin with ``-p`` and no positional.
    """
    validated, families = validate_user_args(adapter, user_args)
    argv: list[str] = [executable]
    argv.extend(adapter.canonical_head)
    if adapter.provider == "codex":
        argv.extend(["-C", str(project_root)])
    for fam, toks in adapter.defaults.items():
        if fam not in families:
            argv.extend(toks)
    argv.extend(validated)
    argv.extend(adapter.canonical_tail)
    return argv


# ---------------------------------------------------------------------------
# Event rendering + usage parsing
# ---------------------------------------------------------------------------

def _short(text: str, n: int = 240) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def render_event(provider: str, line: str) -> str | None:
    """Render one structured stdout line into a human log line, or None
    to skip it. Never raises."""
    line = line.strip()
    if not line:
        return None
    try:
        ev = json.loads(line)
    except ValueError:
        return f"[{provider}] {_short(line)}"
    if not isinstance(ev, dict):
        return None
    try:
        if provider == "claude":
            return _render_claude(ev)
        if provider == "codex":
            return _render_codex(ev)
    except Exception:  # rendering must never break the runner
        return None
    return None


def _render_claude(ev: dict) -> str | None:
    t = ev.get("type")
    if t == "system":
        if ev.get("subtype") == "init":
            return (f"[claude] session {ev.get('session_id')} "
                    f"model {ev.get('model')} "
                    f"permission {ev.get('permissionMode')}")
        return None
    if t == "assistant":
        msg = ev.get("message") or {}
        parts = []
        for block in msg.get("content") or []:
            bt = block.get("type")
            if bt == "text" and block.get("text"):
                parts.append(_short(block["text"], 400))
            elif bt == "tool_use":
                inp = block.get("input") or {}
                desc = inp.get("command") or inp.get("file_path") or \
                    inp.get("pattern") or json.dumps(inp)
                parts.append(f"→ {block.get('name')}: {_short(desc, 200)}")
        return "\n".join(parts) if parts else None
    if t == "user":
        msg = ev.get("message") or {}
        parts = []
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict))
                flag = " (error)" if block.get("is_error") else ""
                parts.append(f"← {_short(content or '', 200)}{flag}")
        return "\n".join(parts) if parts else None
    if t == "result":
        usage = ev.get("usage") or {}
        return (f"[claude] result {ev.get('subtype')} turns={ev.get('num_turns')} "
                f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
                f"cache_read={usage.get('cache_read_input_tokens')} "
                f"cost=${ev.get('total_cost_usd')} "
                f"duration_ms={ev.get('duration_ms')}")
    return None


def _render_codex(ev: dict) -> str | None:
    t = ev.get("type")
    if t == "thread.started":
        return f"[codex] thread {ev.get('thread_id')}"
    if t in ("item.started", "item.completed"):
        item = ev.get("item") or {}
        it = item.get("type")
        if it == "agent_message" and t == "item.completed":
            return _short(item.get("text", ""), 400)
        if it == "command_execution":
            if t == "item.started":
                return f"$ {_short(item.get('command', ''), 300)}"
            return (f"  exit {item.get('exit_code')}: "
                    f"{_short(item.get('aggregated_output', ''), 300)}")
        if it == "reasoning" and t == "item.completed":
            return None
        if t == "item.completed":
            return f"[codex] {it}: {_short(json.dumps(item), 200)}"
        return None
    if t == "turn.completed":
        u = ev.get("usage") or {}
        return (f"[codex] turn completed in={u.get('input_tokens')} "
                f"cached={u.get('cached_input_tokens')} "
                f"out={u.get('output_tokens')}")
    if t == "turn.failed" or t == "error":
        return f"[codex] {t}: {_short(json.dumps(ev), 300)}"
    return None


def parse_usage(provider: str, event_lines: list[str]) -> dict | None:
    """Extract the per-turn usage record from the retained structured
    stdout. Returns a dict of usage-table fields, or None if no usage
    could be found (caller writes a null-token row + diagnostic)."""
    events: list[dict] = []
    for line in event_lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    if not events:
        return None
    try:
        if provider == "claude":
            return _usage_claude(events)
        if provider == "codex":
            return _usage_codex(events)
    except Exception:
        return None
    return None


def _usage_claude(events: list[dict]) -> dict | None:
    result = None
    model = None
    session_id = None
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            model = ev.get("model") or model
            session_id = ev.get("session_id") or session_id
        elif ev.get("type") == "result":
            result = ev
    if result is None:
        return None
    usage = result.get("usage") or {}
    mu = result.get("modelUsage") or {}
    if not model and mu:
        model = next(iter(mu.keys()), None)
    return {
        "model": model,
        "session_id": result.get("session_id") or session_id,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
    }


def _usage_codex(events: list[dict]) -> dict | None:
    thread_id = None
    last_usage = None
    turns = 0
    for ev in events:
        t = ev.get("type")
        if t == "thread.started":
            thread_id = ev.get("thread_id") or thread_id
        elif t == "turn.completed":
            turns += 1
            if isinstance(ev.get("usage"), dict):
                last_usage = ev["usage"]
    if last_usage is None:
        return None
    return {
        "model": None,
        "session_id": thread_id,
        "input_tokens": last_usage.get("input_tokens"),
        "output_tokens": last_usage.get("output_tokens"),
        "cache_read_tokens": last_usage.get("cached_input_tokens"),
        "cache_write_tokens": last_usage.get("cache_write_input_tokens"),
        "cost_usd": None,
        "num_turns": turns or None,
    }


# ---------------------------------------------------------------------------
# Start-command parsing + prompt composition
# ---------------------------------------------------------------------------

_START_RE = re.compile(r"^/handoff\s+start\s+([a-z0-9][a-z0-9-]*)(\s+impl)?\s*$")


def parse_start_command(command: str | None) -> tuple[str, str] | None:
    """Narrow validator for cycle-init commands.

    Accepts exactly ``/handoff start <slug>`` and ``/handoff start <slug>
    impl`` (slug alphabet ``[a-z0-9-]``) → ``(slug, "plan"|"impl")``.
    Anything else → None (verified as an ordinary turn).
    """
    if not isinstance(command, str):
        return None
    m = _START_RE.match(command.strip())
    if not m:
        return None
    return m.group(1), ("impl" if m.group(2) else "plan")


IMPL_BOUNDARY_CLAUSE = """
IMPORTANT — plan-approved boundary. The command above starts the
IMPLEMENTATION review cycle for phase "{phase}". Before you run
`tagteam cycle init --type impl`, you must:
  1. Read the approved plan at docs/phases/{phase}.md and the plan cycle
     history (`tagteam cycle rounds --phase {phase} --type plan`).
  2. Implement the plan in full and run the project's verification
     (tests) until it passes.
  3. Only then initialize the impl cycle EXACTLY ONCE with a submission
     summarizing what was implemented. If an impl cycle for this phase
     already exists, do not create another — act on it instead.
""".strip()


def compose_prompt(*, role: str, agent_name: str, project_root: str | Path,
                   state: dict, skill_text: str, tail_entries: list[dict],
                   tail_n: int) -> str:
    """Build the bounded turn context sent on stdin."""
    command = state.get("command") or STANDARD_TURN_COMMAND
    start = parse_start_command(command)
    boundary = ""
    if start and start[1] == "impl":
        boundary = "\n" + IMPL_BOUNDARY_CLAUSE.format(phase=start[0]) + "\n"
    tail_text = "\n".join(json.dumps(e) for e in tail_entries) or "(no rounds yet)"
    return (
        f"You are the {role} ({agent_name}) in a tagteam handoff cycle for the\n"
        f"project at {project_root}. This is a headless turn: no human is\n"
        f"watching this terminal. Read the contract below, then act on your\n"
        f"turn exactly as it says, using --updated-by \"{agent_name}\". Make\n"
        f"exactly one cycle-writing call (tagteam cycle add / tagteam cycle\n"
        f"init). When it succeeds, stop.\n"
        f"{boundary}\n"
        f"=== COMMAND ===\n{command}\n\n"
        f"=== HANDOFF CONTRACT (.claude/skills/handoff/SKILL.md) ===\n"
        f"{skill_text.rstrip()}\n\n"
        f"=== CURRENT STATE (handoff-state.json) ===\n"
        f"{json.dumps(state, indent=2)}\n\n"
        f"=== ROUND TAIL (last {tail_n}) ===\n{tail_text}\n"
    )


# ---------------------------------------------------------------------------
# Pause marker / inflight pointer / log housekeeping
# ---------------------------------------------------------------------------

def pause_path(project_root: str | Path) -> Path:
    return Path(project_root) / PAUSE_RELPATH


def read_pause(project_root: str | Path) -> dict | None:
    p = pause_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"reason": "unreadable pause marker", "path": str(p)}


def write_pause(project_root: str | Path, payload: dict) -> Path:
    p = pause_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("ts", _now_iso())
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def clear_pause(project_root: str | Path) -> bool:
    p = pause_path(project_root)
    if p.exists():
        p.unlink()
        return True
    return False


def turns_dir(project_root: str | Path) -> Path:
    return Path(project_root) / TURNS_RELDIR


def inflight_path(project_root: str | Path) -> Path:
    return turns_dir(project_root) / INFLIGHT_NAME


def read_inflight(project_root: str | Path) -> dict | None:
    p = inflight_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def prune_turn_logs(project_root: str | Path, keep: int = KEEP_TURN_LOGS) -> int:
    """Keep the newest ``keep`` turn stems; delete older .log/.events.jsonl."""
    d = turns_dir(project_root)
    if not d.exists():
        return 0
    stems: dict[str, float] = {}
    for f in d.iterdir():
        if f.name == INFLIGHT_NAME or not f.is_file():
            continue
        stem = f.name
        for suffix in (".events.jsonl", ".log"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        try:
            stems[stem] = max(stems.get(stem, 0.0), f.stat().st_mtime)
        except OSError:
            continue
    ordered = sorted(stems.items(), key=lambda kv: kv[1], reverse=True)
    removed = 0
    for stem, _ in ordered[keep:]:
        for suffix in (".events.jsonl", ".log"):
            f = d / f"{stem}{suffix}"
            if f.exists():
                try:
                    f.unlink(); removed += 1
                except OSError:
                    pass
    return removed


# ---------------------------------------------------------------------------
# Process runner
# ---------------------------------------------------------------------------

def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and everything it spawned."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


@dataclass
class RunOutput:
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    interrupted: bool = False


def run_process(argv: list[str], prompt: str, cwd: str | Path, *,
                events_path: Path, log_path: Path, provider: str,
                timeout_s: float, on_line: Callable[[str], None] | None = None,
                env: dict | None = None) -> RunOutput:
    """Spawn ``argv`` with ``prompt`` on stdin; stream stdout (structured)
    to ``events_path`` and rendered stdout + ``[stderr]`` lines to
    ``log_path``, both flushed per line. Kills the process tree on
    timeout or KeyboardInterrupt (the latter is re-raised)."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    popen_kwargs: dict = dict(
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(cwd), env=env,
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    started = time.monotonic()
    log_lock = threading.Lock()
    with open(events_path, "ab") as ev_f, open(log_path, "ab") as log_f:
        def write_log(text: str) -> None:
            data = (text.rstrip("\n") + "\n").encode("utf-8", "replace")
            with log_lock:
                log_f.write(data); log_f.flush()
            if on_line:
                try:
                    on_line(text)
                except Exception:
                    pass

        write_log(f"[tagteam] {_now_iso()} spawning: {' '.join(argv)}")
        proc = subprocess.Popen(argv, **popen_kwargs)
        write_log(f"[tagteam] spawned pid {proc.pid}")

        def feed_stdin():
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        def read_stdout():
            for raw in iter(proc.stdout.readline, b""):
                ev_f.write(raw if raw.endswith(b"\n") else raw + b"\n")
                ev_f.flush()
                text = raw.decode("utf-8", "replace")
                rendered = render_event(provider, text)
                if rendered:
                    write_log(rendered)

        def read_stderr():
            for raw in iter(proc.stderr.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\n")
                if text.strip():
                    write_log(f"[stderr] {text}")

        threads = [threading.Thread(target=fn, daemon=True)
                   for fn in (feed_stdin, read_stdout, read_stderr)]
        for t in threads:
            t.start()

        timed_out = False
        interrupted = False
        try:
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                write_log(f"[tagteam] turn timeout after {timeout_s:.0f}s — killing process tree")
                _kill_tree(proc)
        except KeyboardInterrupt:
            interrupted = True
            write_log("[tagteam] interrupted (Ctrl-C) — killing process tree")
            _kill_tree(proc)
            raise
        finally:
            for t in threads:
                t.join(timeout=5)
            duration_ms = int((time.monotonic() - started) * 1000)
            if not interrupted:
                write_log(f"[tagteam] exit code {proc.returncode} "
                          f"after {duration_ms} ms"
                          + (" (timeout)" if timed_out else ""))
        return RunOutput(exit_code=proc.returncode, timed_out=timed_out,
                         duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class TurnIdentity:
    phase: str
    type: str
    round: int
    role: str
    seq: int
    command: str
    target_phase: str
    target_type: str
    target_round: int
    is_start: bool
    target_existed: bool
    pre_entries: int


def _read_entries(project_root: str, phase: str, cycle_type: str) -> list[dict]:
    from tagteam.cycle import read_rounds
    try:
        return read_rounds(phase, cycle_type, str(project_root)) or []
    except Exception:
        return []


def _read_status(project_root: str, phase: str, cycle_type: str) -> dict | None:
    from tagteam.cycle import read_status
    try:
        return read_status(phase, cycle_type, str(project_root))
    except Exception:
        return None


def snapshot_identity(project_root: str | Path, state: dict) -> TurnIdentity:
    """Capture the owed turn's identity and derive the verification target."""
    root = str(project_root)
    phase = str(state.get("phase") or "")
    ctype = str(state.get("type") or "plan")
    rnd = int(state.get("round") or 0)
    role = str(state.get("turn") or "")
    command = state.get("command") or STANDARD_TURN_COMMAND
    start = parse_start_command(command)
    if start:
        tphase, ttype = start
        status = _read_status(root, tphase, ttype)
        existed = status is not None
        pre = len(_read_entries(root, tphase, ttype)) if existed else 0
        # New cycles start at round 1; if the cycle already exists the
        # lead is expected to add the next round to it.
        tround = 1 if not existed else int((status or {}).get("round") or 0) + 1
        return TurnIdentity(phase, ctype, rnd, role, int(state.get("seq") or 0),
                            command, tphase, ttype, tround, True, existed, pre)
    pre = len(_read_entries(root, phase, ctype))
    # Round semantics (see cycle.add_round / SKILL.md): a reviewer acts on
    # the round the lead just submitted (state.round == N); a lead owed a
    # turn after REQUEST_CHANGES submits the *next* round (N+1).
    tround = rnd + 1 if role == "lead" else rnd
    return TurnIdentity(phase, ctype, rnd, role, int(state.get("seq") or 0),
                        command, phase, ctype, tround, False, True, pre)


def verify_transition(project_root: str | Path, ident: TurnIdentity,
                      agent_name: str) -> tuple[bool, str]:
    """Did the expected cycle transition happen? Returns (ok, reason)."""
    from tagteam.state import read_state
    root = str(project_root)
    entries = _read_entries(root, ident.target_phase, ident.target_type)
    new = entries[ident.pre_entries:]
    expected_role = "lead" if ident.is_start else ident.role
    allowed = _LEAD_ACTIONS if expected_role == "lead" else _REVIEWER_ACTIONS
    if ident.is_start and not ident.target_existed and _read_status(
            root, ident.target_phase, ident.target_type) is None:
        return False, (f"expected new cycle {ident.target_phase}_{ident.target_type} "
                       f"was not created")
    match = None
    for e in new:
        if (e.get("role") == expected_role
                and int(e.get("round") or -1) == ident.target_round
                and e.get("action") in allowed):
            match = e
            break
    if match is None:
        seen = [f"{e.get('role')}:{e.get('action')}@r{e.get('round')}" for e in new]
        return False, (
            f"no {expected_role} entry with action in {sorted(allowed)} at round "
            f"{ident.target_round} for {ident.target_phase}_{ident.target_type} "
            f"(new entries: {seen or 'none'})")
    state = read_state(root) or {}
    problems = []
    if str(state.get("phase") or "") != ident.target_phase:
        problems.append(f"state.phase={state.get('phase')!r} != {ident.target_phase!r}")
    if str(state.get("type") or "") != ident.target_type:
        problems.append(f"state.type={state.get('type')!r} != {ident.target_type!r}")
    if int(state.get("round") or -1) != ident.target_round:
        problems.append(f"state.round={state.get('round')!r} != {ident.target_round}")
    if int(state.get("seq") or 0) <= ident.seq:
        problems.append("state.seq did not advance")
    if agent_name and (state.get("updated_by") or "") != agent_name:
        problems.append(f"state.updated_by={state.get('updated_by')!r} != {agent_name!r}")
    if problems:
        return False, "cycle entry present but state mismatch: " + "; ".join(problems)
    return True, f"{expected_role} {match.get('action')} at round {ident.target_round}"


# ---------------------------------------------------------------------------
# Engine (used by the watcher)
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    outcome: str
    reason: str
    exit_code: int | None
    duration_ms: int
    stem: str
    log_path: str
    events_path: str
    usage: dict | None = None
    usage_row_id: int | None = None


@dataclass
class RoleSpec:
    role: str
    agent_name: str
    provider: str
    executable: str
    argv: list[str]


class HeadlessEngine:
    """Owns per-role adapters/argv and runs owed turns for the watcher."""

    def __init__(self, project_root: str | Path, config: dict | None, *,
                 lead_name: str, reviewer_name: str,
                 timeout_minutes: int = DEFAULT_TURN_TIMEOUT_MINUTES,
                 tail_rounds: int = DEFAULT_TAIL_ROUNDS,
                 confirm: bool = False,
                 log: Callable[[str], None] | None = None,
                 notify: Callable[[str, str], None] | None = None,
                 skill_path: Path | None = None):
        self.project_root = str(Path(project_root).resolve())
        self.config = config or {}
        self.names = {"lead": lead_name, "reviewer": reviewer_name}
        self.timeout_s = float(timeout_minutes) * 60.0
        self.tail_n = int(tail_rounds)
        self.confirm = confirm
        self._log = log or (lambda m: print(m, flush=True))
        self._notify = notify or (lambda t, m: None)
        self.skill_path = skill_path or (Path(self.project_root) / SKILL_RELPATH)
        self.roles: dict[str, RoleSpec] = {}
        self._last_pause_log = 0.0

    # -- startup -----------------------------------------------------------

    def validate(self) -> list[str]:
        """Resolve executables + build argv for both roles. Returns errors."""
        errors: list[str] = []
        for role in ("lead", "reviewer"):
            spec = get_headless_spec(self.config, role)
            provider = spec["provider"]
            if provider not in ADAPTERS:
                errors.append(
                    f"agents.{role}: cannot determine headless provider "
                    f"(set agents.{role}.headless.provider to one of "
                    f"{', '.join(HEADLESS_PROVIDERS)})")
                continue
            adapter = ADAPTERS[provider]
            try:
                exe = resolve_executable(provider, spec["executable"])
                argv = build_argv(adapter, exe, spec["args"], self.project_root)
            except HeadlessConfigError as e:
                errors.append(f"agents.{role}: {e}")
                continue
            self.roles[role] = RoleSpec(role, self.names[role], provider, exe, argv)
        if not self.skill_path.exists():
            errors.append(f"handoff skill contract not found at {self.skill_path}")
        return errors

    # -- pause -------------------------------------------------------------

    def paused(self) -> dict | None:
        return read_pause(self.project_root)

    def log_paused(self, force: bool = False) -> None:
        info = self.paused()
        if not info:
            return
        now = time.monotonic()
        if force or now - self._last_pause_log > 60:
            self._last_pause_log = now
            self._log(f"!! headless PAUSED: {info.get('reason')}")
            self._log(f"   turn log: {info.get('log_path')}")
            self._log(f"   resume: inspect the log, fix the tree/state if needed, "
                      f"then delete {pause_path(self.project_root)}")

    # -- turn --------------------------------------------------------------

    def run_owed_turn(self, state: dict) -> TurnResult | None:
        """Spawn the owed agent for a ``ready`` state. Returns None if the
        engine is paused or the role is unknown (already logged)."""
        if self.paused():
            self.log_paused(force=True)
            return None
        role = state.get("turn")
        spec = self.roles.get(role)
        if spec is None:
            self._log(f"   headless: no adapter for role {role!r}; skipping")
            return None

        prune_turn_logs(self.project_root)
        ident = snapshot_identity(self.project_root, state)
        stem = (f"{ident.phase or 'nophase'}_{ident.type}_r{ident.round}"
                f"_{role}_{_stamp()}")
        d = turns_dir(self.project_root)
        d.mkdir(parents=True, exist_ok=True)
        log_path = d / f"{stem}.log"
        events_path = d / f"{stem}.events.jsonl"

        from tagteam.cycle import tail_rounds as _tail
        try:
            tail = _tail(ident.phase, ident.type, self.tail_n, self.project_root) \
                if ident.phase else []
        except Exception:
            tail = []
        skill_text = self.skill_path.read_text(encoding="utf-8")
        prompt = compose_prompt(role=role, agent_name=spec.agent_name,
                                project_root=self.project_root, state=state,
                                skill_text=skill_text, tail_entries=tail,
                                tail_n=self.tail_n)

        if self.confirm:
            try:
                input(f"   Press Enter to spawn {spec.provider} for "
                      f"{spec.agent_name} ({role})...")
            except EOFError:
                return None

        started_at = _now_iso()
        inflight = {
            "phase": ident.phase, "type": ident.type, "round": ident.round,
            "role": role, "agent": spec.agent_name, "provider": spec.provider,
            "stem": stem, "log_path": str(log_path),
            "events_path": str(events_path), "started_at": started_at,
            "pid": None,
        }
        inflight_path(self.project_root).write_text(
            json.dumps(inflight, indent=2), encoding="utf-8")
        self._log(f"   headless: spawning {spec.provider} for {spec.agent_name} "
                  f"({role}) — log: {log_path}")

        # Child env: make sure nested-session guards don't refuse to run.
        env = dict(os.environ)
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(k, None)

        try:
            out = run_process(spec.argv, prompt, self.project_root,
                              events_path=events_path, log_path=log_path,
                              provider=spec.provider, timeout_s=self.timeout_s,
                              env=env)
        finally:
            try:
                inflight_path(self.project_root).unlink()
            except OSError:
                pass

        # Outcome
        if out.timed_out:
            outcome, reason = OUTCOME_TIMEOUT, (
                f"turn exceeded {self.timeout_s / 60:.0f} min timeout")
        elif out.exit_code != 0:
            outcome, reason = OUTCOME_NONZERO, f"{spec.provider} exited {out.exit_code}"
        else:
            ok, why = verify_transition(self.project_root, ident, spec.agent_name)
            outcome, reason = (OUTCOME_OK, why) if ok else (OUTCOME_NO_ROUND, why)

        # Usage (never fails the turn)
        try:
            lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        usage = parse_usage(spec.provider, lines)
        row_id = self._record_usage(ident, spec, outcome, out, usage, log_path)

        with open(log_path, "ab") as f:
            f.write(f"[tagteam] outcome {outcome}: {reason}\n".encode("utf-8", "replace"))

        result = TurnResult(outcome=outcome, reason=reason, exit_code=out.exit_code,
                            duration_ms=out.duration_ms, stem=stem,
                            log_path=str(log_path), events_path=str(events_path),
                            usage=usage, usage_row_id=row_id)
        if outcome == OUTCOME_OK:
            self._log(f"   headless: turn ok — {reason} ({out.duration_ms} ms)")
        else:
            self._fail(ident, spec, result)
        return result

    def _record_usage(self, ident: TurnIdentity, spec: RoleSpec, outcome: str,
                      out: RunOutput, usage: dict | None, log_path: Path) -> int | None:
        from tagteam import db
        try:
            conn = db.connect(project_dir=self.project_root)
        except Exception as e:
            self._log(f"   headless: could not open DB for usage row: {e}")
            return None
        try:
            fields = dict(
                ts=_now_iso(), phase=ident.phase or None, type=ident.type,
                round=ident.round, role=ident.role, agent=spec.agent_name,
                provider=spec.provider, status=outcome, exit_code=out.exit_code,
                duration_ms=out.duration_ms, log_path=str(log_path),
            )
            if usage:
                fields.update({k: usage.get(k) for k in (
                    "model", "input_tokens", "output_tokens", "cache_read_tokens",
                    "cache_write_tokens", "cost_usd", "num_turns", "session_id")})
            row_id = db.add_usage(conn, **fields)
            if usage is None:
                db.add_diagnostic(conn, "headless_usage_unparsed", {
                    "phase": ident.phase, "type": ident.type, "round": ident.round,
                    "role": ident.role, "provider": spec.provider,
                    "events_path": str(log_path.with_name(
                        log_path.name[:-4] + ".events.jsonl")),
                }, _now_iso())
                conn.commit()
            return row_id
        except Exception as e:
            self._log(f"   headless: usage row failed: {e}")
            return None
        finally:
            conn.close()

    def _fail(self, ident: TurnIdentity, spec: RoleSpec, result: TurnResult) -> None:
        from tagteam import db
        payload = {
            "reason": f"headless turn {result.outcome}: {result.reason}",
            "outcome": result.outcome, "phase": ident.phase, "type": ident.type,
            "round": ident.round, "role": ident.role, "agent": spec.agent_name,
            "provider": spec.provider, "exit_code": result.exit_code,
            "duration_ms": result.duration_ms, "log_path": result.log_path,
            "events_path": result.events_path, "ts": _now_iso(),
        }
        try:
            conn = db.connect(project_dir=self.project_root)
            try:
                db.add_diagnostic(conn, "headless_turn_failed", payload, payload["ts"])
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self._log(f"   headless: diagnostics write failed: {e}")
        write_pause(self.project_root, payload)
        self._log(f"!! headless turn {result.outcome}: {result.reason}")
        self._log(f"   log: {result.log_path}")
        self._log(f"   dispatch PAUSED — resume by inspecting the log, fixing the "
                  f"tree/state if needed, then deleting {pause_path(self.project_root)}")
        self._notify("Tagteam", f"Headless turn {result.outcome} "
                                f"({spec.agent_name}, {ident.phase} r{ident.round}) — paused")


# ---------------------------------------------------------------------------
# `tagteam tail`
# ---------------------------------------------------------------------------

def _latest_log(project_root: str | Path, events: bool) -> Path | None:
    d = turns_dir(project_root)
    if not d.exists():
        return None
    suffix = ".events.jsonl" if events else ".log"
    cands = [f for f in d.iterdir()
             if f.is_file() and f.name.endswith(suffix)]
    if not cands:
        return None
    return max(cands, key=lambda f: f.stat().st_mtime)


def _tail_lines(path: Path, n: int) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = data.splitlines()
    return "\n".join(lines[-n:])


def tail_command(args: list[str], project_root: str | Path | None = None,
                 out=None, poll_interval: float = 0.5,
                 max_follow_s: float | None = None) -> int:
    """Follow the in-flight headless turn log (or show the last one)."""
    out = out or sys.stdout
    lines = 40
    follow = True
    events = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--lines" and i + 1 < len(args):
            try:
                lines = int(args[i + 1])
            except ValueError:
                print(f"--lines must be an integer, got {args[i+1]!r}", file=out)
                return 1
            i += 2
        elif a == "--no-follow":
            follow = False; i += 1
        elif a == "--events":
            events = True; i += 1
        elif a in ("-h", "--help"):
            print("Usage: tagteam tail [--lines N] [--no-follow] [--events]", file=out)
            print("  Follows the in-flight headless turn log; with nothing in", file=out)
            print("  flight prints the most recent turn log's last N lines.", file=out)
            return 0
        else:
            print(f"Unknown argument: {a}", file=out)
            return 1

    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    root = Path(project_root)

    inflight = read_inflight(root)
    if inflight is None or not follow:
        target = None
        if inflight is not None:
            target = Path(inflight["events_path" if events else "log_path"])
        if target is None or not target.exists():
            target = _latest_log(root, events)
        if target is None:
            print("No headless turn logs found (.tagteam/turns/ is empty). "
                  "Start the watcher with `tagteam watch --mode headless`.", file=out)
            return 1
        print(f"== {target} ==", file=out)
        print(_tail_lines(target, lines), file=out)
        return 0

    target = Path(inflight["events_path" if events else "log_path"])
    print(f"== following {inflight.get('provider')} turn for {inflight.get('agent')} "
          f"({inflight.get('role')}) — {inflight.get('phase')} "
          f"{inflight.get('type')} r{inflight.get('round')} ==", file=out)
    print(f"== {target} ==", file=out)
    pos = 0
    started = time.monotonic()
    printed_tail = False
    try:
        while True:
            if target.exists():
                with open(target, "rb") as f:
                    if not printed_tail:
                        data = f.read()
                        text = data.decode("utf-8", "replace")
                        tail = "\n".join(text.splitlines()[-lines:])
                        if tail:
                            print(tail, file=out)
                        pos = len(data)
                        printed_tail = True
                    else:
                        f.seek(pos)
                        chunk = f.read()
                        if chunk:
                            out.write(chunk.decode("utf-8", "replace"))
                            out.flush()
                            pos += len(chunk)
            if read_inflight(root) is None:
                # drain once more, then stop
                if target.exists():
                    with open(target, "rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                        if chunk:
                            out.write(chunk.decode("utf-8", "replace")); out.flush()
                break
            if max_follow_s is not None and time.monotonic() - started > max_follow_s:
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    return 0
