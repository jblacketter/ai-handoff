"""Fake agent CLI for headless-engine tests.

Installed into a temp PATH dir as ``claude`` / ``codex`` (POSIX shell
script or Windows ``.cmd`` shim) so tests exercise the same
``shutil.which`` + PATHEXT resolution as the real CLIs.

Behaviour is driven by environment variables:

  FAKE_AGENT_FLAVOR   claude | codex           (event-stream shape)
  FAKE_AGENT_MODE     ok | no_round | nonzero | hang | grandchild_hang |
                      malformed | stderr_noise | wrong_round | amend
  FAKE_AGENT_CAPTURE  path: argv + stdin prompt are dumped here as JSON
  FAKE_AGENT_PIDFILE  path: grandchild pid is written here (grandchild_hang)
  FAKE_AGENT_SLEEP    seconds between emitted events (default 0.25)
  FAKE_AGENT_SIDE_EFFECT  JSON list of actions run before exiting in
                      `nonzero` mode (retry-gate tests):
                        {"write": [relpath, content]}   write a file (append)
                        {"git": ["commit","-am","x"]}   run a git command
                        {"cycle_add": true}             perform the owed transition
  FAKE_AGENT_FAIL_TIMES / FAKE_AGENT_COUNTER  in `flaky` mode: exit 3 for the
                      first N invocations (counter file), then behave like `ok`
  brief modes (Phase 33): `brief` writes the file named in the prompt's
                      === OUTPUT PATH === block with the five headings;
                      `brief_partial` writes three headings; `brief_nofile`
                      exits 0 without writing; `brief_hang` writes nothing and sleeps

The fake reads the composed prompt from stdin, parses the CURRENT STATE
block, and in ``ok`` mode performs the transition a real agent would by
running ``python -m tagteam cycle add|init`` in the cwd it was spawned
in (the project root). Events are emitted incrementally with sleeps so
tests can assert the log grows before exit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _sleep() -> None:
    time.sleep(float(os.environ.get("FAKE_AGENT_SLEEP", "0.25")))


def _parse_state(prompt: str) -> dict:
    marker = "=== CURRENT STATE (handoff-state.json) ==="
    end = "=== ROUND TAIL"
    if marker not in prompt:
        return {}
    body = prompt.split(marker, 1)[1]
    body = body.split(end, 1)[0]
    try:
        return json.loads(body.strip())
    except ValueError:
        return {}


def _tagteam(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "tagteam", *args],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _agent_name(prompt: str) -> str:
    # 'You are the lead (Claude) in a tagteam...'
    try:
        head = prompt.split("\n", 1)[0]
        return head.split("(", 1)[1].split(")", 1)[0]
    except Exception:
        return "fake"


def _do_transition(prompt: str, state: dict, mode: str) -> None:
    role = state.get("turn")
    phase = state.get("phase")
    ctype = state.get("type", "plan")
    rnd = int(state.get("round") or 0)
    name = _agent_name(prompt)
    command = state.get("command") or ""
    # start command → cycle init
    parts = command.strip().split()
    if len(parts) >= 3 and parts[0] == "/handoff" and parts[1] == "start":
        slug = parts[2]
        t = "impl" if len(parts) > 3 and parts[3] == "impl" else "plan"
        _tagteam("cycle", "init", "--phase", slug, "--type", t,
                 "--updated-by", name, "--content", f"fake {t} submission")
        return
    if role == "lead":
        target = rnd + 1
        if mode == "wrong_round":
            target = rnd + 5
        if mode == "amend":
            _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                     "--role", "lead", "--action", "AMEND", "--round", str(rnd),
                     "--updated-by", name, "--content", "fake amend")
            return
        _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                 "--role", "lead", "--action", "SUBMIT_FOR_REVIEW",
                 "--round", str(target), "--updated-by", name,
                 "--content", "fake lead submission")
    else:
        target = rnd if mode != "wrong_round" else rnd + 5
        _tagteam("cycle", "add", "--phase", phase, "--type", ctype,
                 "--role", "reviewer", "--action", "REQUEST_CHANGES",
                 "--round", str(target), "--updated-by", name,
                 "--content", "fake review")


def main() -> int:
    flavor = os.environ.get("FAKE_AGENT_FLAVOR", "claude")
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    # The engine writes the prompt as UTF-8 bytes; decode explicitly so a
    # Windows console code page (cp1252) cannot mangle non-ASCII text.
    prompt = sys.stdin.buffer.read().decode("utf-8", "replace")
    capture = os.environ.get("FAKE_AGENT_CAPTURE")
    if capture:
        with open(capture, "w", encoding="utf-8") as f:
            json.dump({"argv": sys.argv, "prompt": prompt, "cwd": os.getcwd()}, f)
    state = _parse_state(prompt)

    if flavor == "claude":
        _emit({"type": "system", "subtype": "init", "session_id": "fake-sess",
               "model": "fake-model", "permissionMode": "acceptEdits", "tools": []})
        # Phase 34: the real CLI emits a rate-limit frame; the engine records
        # the latest per kind into `rate_limits`.
        _emit({"type": "rate_limit_event",
               "rate_limit_info": {"status": "allowed", "resetsAt": 1786785000,
                                   "rateLimitType": "five_hour",
                                   "overageStatus": "allowed", "isUsingOverage": False}})
    else:
        _emit({"type": "thread.started", "thread_id": "fake-thread"})
    _sleep()

    if mode.startswith("brief"):
        out_path = None
        if "=== OUTPUT PATH ===" in prompt:
            block = prompt.split("=== OUTPUT PATH ===", 1)[1]
            for line in block.splitlines()[1:6]:
                line = line.strip()
                if line and not line.startswith("Write the brief") and not line.startswith("First line") \
                        and not line.startswith("Print DONE") and not line.startswith("==="):
                    out_path = line
                    break
        if mode == "brief_hang":
            time.sleep(600)
            return 0
        if mode != "brief_nofile" and out_path:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            heads = ["## Positions", "## Crux", "## Evidence", "## Recommendation", "## Rulings"]
            if mode == "brief_partial":
                heads = heads[:3]
            body = ["<!-- fake brief -->", "# Decision brief (fake)"]
            for hd in heads:
                body += [hd, f"fake text for {hd[3:].lower()}", ""]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(body))
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "DONE"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}})
        _sleep()
        if flavor == "claude":
            _emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                   "session_id": "fake-brief", "total_cost_usd": 0.02,
                   "usage": {"input_tokens": 100, "output_tokens": 50,
                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                   "result": "DONE"})
        else:
            _emit({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50,
                                                       "cached_input_tokens": 0,
                                                       "cache_write_input_tokens": 0}})
        return 0

    if mode == "stderr_noise":
        sys.stderr.write("warning: something noisy on stderr\n")
        sys.stderr.flush()

    if mode == "hang":
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "hanging"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "hanging"}})
        time.sleep(600)
        return 0

    if mode == "grandchild_hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        pidfile = os.environ.get("FAKE_AGENT_PIDFILE")
        if pidfile:
            with open(pidfile, "w") as f:
                f.write(str(child.pid))
        _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "spawned grandchild"}]}}
              if flavor == "claude" else
              {"type": "item.completed", "item": {"type": "agent_message", "text": "spawned grandchild"}})
        time.sleep(600)
        return 0

    if flavor == "claude":
        _emit({"type": "assistant", "message": {"model": "fake-model", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "tagteam cycle add ..."}}]}})
    else:
        _emit({"type": "item.started", "item": {"type": "command_execution",
                                                "command": "tagteam cycle add ..."}})
    _sleep()

    if mode == "flaky":
        counter = os.environ.get("FAKE_AGENT_COUNTER")
        fails = int(os.environ.get("FAKE_AGENT_FAIL_TIMES", "1"))
        n = 0
        if counter and os.path.exists(counter):
            n = int(open(counter).read().strip() or 0)
        if counter:
            with open(counter, "w") as f:
                f.write(str(n + 1))
        if n < fails:
            sys.stderr.write("fatal: flaky failure\n")
            return 3
        mode = "ok"

    if mode == "nonzero":
        for action in json.loads(os.environ.get("FAKE_AGENT_SIDE_EFFECT", "[]")):
            if "write" in action:
                path, content = action["write"]
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            elif "git" in action:
                subprocess.call(["git", *action["git"]], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            elif action.get("cycle_add"):
                _do_transition(prompt, state, "ok")
        sys.stderr.write("fatal: fake failure\n")
        return 3

    if mode in ("ok", "malformed", "stderr_noise", "wrong_round", "amend"):
        _do_transition(prompt, state, mode)

    _sleep()
    if mode == "malformed":
        sys.stdout.write("this is not json and there is no usage\n")
        sys.stdout.flush()
        return 0

    if flavor == "claude":
        _emit({"type": "result", "subtype": "success", "is_error": False,
               "num_turns": 2, "session_id": "fake-sess", "total_cost_usd": 0.01,
               "usage": {"input_tokens": 11, "output_tokens": 22,
                         "cache_read_input_tokens": 33,
                         "cache_creation_input_tokens": 44},
               "result": "done"})
    else:
        _emit({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
        _emit({"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 22,
                                                   "cached_input_tokens": 33,
                                                   "cache_write_input_tokens": 44}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
