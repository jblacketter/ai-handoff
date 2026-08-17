# Phase 42: Terminal.app backend (3.6)

## Status
- [x] Planning
- [ ] In Review (round 1: cold-launch window accounting — targeted Lead reuse rule, id-diff identification, exact-three invariant + tests)
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** a fourth session backend, `terminal`, that drives macOS
**Terminal.app** through AppleScript the way `iterm2` drives iTerm2: `tagteam
session start --backend terminal --launch` opens three Terminal.app windows
(Lead, Watcher, Reviewer), launches both agents, primes them once their prompt
is visible, and writes `.handoff-session.json`; `tagteam watch --mode
terminal` (auto-detected from that file) types the next command into the right
window with the same busy-detection, retry and watchdog discipline as the
iTerm2 mode. `session kill`, `session adopt`, the health check in
`tagteam state`, and the dashboard's log tail all work for it. **Opt-in** —
`default_backend()` still picks iTerm2 → tmux; the only default-behaviour
change is that a Mac with *neither* iTerm2 nor tmux now falls through to
`terminal` instead of `manual` (Terminal.app ships with every Mac, so this is
the "no install step" the phase exists for). Every other platform / backend is
byte-identical.

**Why:** today a new macOS user must install iTerm2 (or tmux) before the
watched loop works. Terminal.app is always present. This phase removes the
install step from the first-run path without touching the iTerm2 code paths
that the maintainer's own loop depends on.

**Not in scope:** any cockpit work; a Windows Terminal / GNOME Terminal /
Kitty backend; changing what `iterm2` or `tmux` do; the headless engine;
tab-based layout in Terminal.app (see §A.1); Terminal.app profile/appearance
management.

## Scope

### A. `tagteam/terminal.py` — the driver

Mirrors `tagteam/iterm.py`'s public surface one-for-one so the watcher, server
and health check can treat both as a "tab driver" (§C):

| function | Terminal.app implementation |
|---|---|
| `terminal_is_running()` | System Events process check (`"Terminal"`) |
| `_ensure_terminal_ready()` | `open -b com.apple.Terminal` if not running, then poll `tell application "Terminal" to count windows` until it compiles/answers (same 10 s deadline / 0.2 s poll as iTerm2) |
| `create_session(project_dir, launch)` | §A.1 |
| `write_text_to_session(session_id, text)` | `do script <text> in <tab>` on the tab whose `tty` matches (§A.2) |
| `get_session_contents(session_id, last_n_lines)` | `contents of <tab>` (visible screen), last N lines |
| `session_id_is_valid(session_id)` | a tab with that `tty` exists in some window |
| `_any_session_alive(data)` | Terminal running and ≥1 role's tty still valid |
| `list_sessions()` | `[{"unique_id": <tty>, "tab_title": <custom title or name>, "window_id": <id>}]` — same row shape as `list_iterm_sessions()` |
| `get_session_id(role, project_dir)` / `kill_session(project_dir)` | shared session-file helpers (§C) + `close <tab>` (`saving no`) |

**A.1 Windows, not tabs.** Terminal.app's scripting dictionary has no
`make new tab`; creating tabs requires GUI scripting (System Events ⌘T), which
needs the Accessibility permission and a focused window — a real onboarding
hurdle and fragile under user interaction. So `create_session` opens **three
windows** (Lead / Watcher / Reviewer), sets each tab's `custom title`, and
records each tab's `tty` and its window `id`. Windows are positioned side by
side across the main screen (`bounds`), best-effort — a failure to position is
ignored.

*Window accounting (deterministic, cold and warm launch alike).* Every role
window is identified by **diffing `id of every window`** around its creating
call — never by "front window" or by index:

1. `was_running = terminal_is_running()`; `_ensure_terminal_ready()` (which
   `open -b com.apple.Terminal`s if needed and polls `count windows` until the
   dictionary answers).
2. `before = id of every window` (one AppleScript call).
3. **Lead.** *Reuse condition:* `not was_running` **and** `len(before) == 1`
   **and** `busy of selected tab of window id <before[0]>` is `false` (an
   idle login shell — Terminal's own launch window, not a restored session
   running something). If it holds, Lead is created **targeted**:
   `do script "cd <abs_dir>" in window id <before[0]>`; afterwards
   `id of every window` must still equal `before` (no new window). If a new
   window appeared anyway (Terminal ignored the target), that new window
   becomes Lead and the launch window is left alone; a one-line note says so.
   If the reuse condition does not hold (Terminal was already running; zero
   or several windows were restored on launch; the launch tab is busy) Lead is
   created **untargeted** like the other two roles and nothing pre-existing
   is touched.
4. **Watcher, then Reviewer.** Untargeted `do script "cd <abs_dir>"`; after
   each call `after = id of every window`, `new = after − seen`; `new` must be
   **exactly one** id — that window (its `tab 1`) is the role's; `seen` grows
   by it. `new` of 0 or ≥2 (Terminal reused a window, or the user opened one
   in the ~100 ms window) is an error: the windows we created so far are
   closed (`close … saving no`), no session file is written, `create_session`
   prints `Error creating Terminal.app session: <what was seen>` and returns
   False (same shape as the iTerm2 error path).
5. Titles / tty / bounds are set per recorded window id; the session file is
   written; launch commands and priming follow (as iTerm2 — file first).

*Invariant* (asserted by the mocked tests, §D): the session file always holds
exactly three roles with three distinct ttys and three distinct window ids;
in the cold-launch reuse case the launch window's id **is** the Lead's — no
untracked fourth window; in every other case exactly three windows were
created and every pre-existing window id is untouched (never targeted, never
closed). `kill_session` closes only the recorded window ids' tabs (by tty).

**A.2 Identity = tty.** Terminal.app has no persistent session UUID, but every
tab has a `tty` (`/dev/ttys004`) that is unique among open tabs and stable for
the tab's lifetime — the same guarantee iTerm2's `unique ID` gives us in
practice (both die with the tab). It survives the user re-ordering, moving, or
merging windows, which index-based tracking would not. `session_id` in
`.handoff-session.json` holds the tty path; the file additionally records
`window_id` per role for `kill`/`list` (informational, never used for
lookup). Consequence to document: after a *quit and restore* of Terminal.app
the ttys change and the session is stale — exactly the iTerm2 behaviour
(`_any_session_alive` false → "Stale session file, removing").

**A.3 Sending text.** `do script <text> in <tab>` writes the text into the
tab's tty followed by a newline. That is the whole send path — no pre-send
input clearing (as iTerm2). AppleScript escaping identical to
`iterm.write_text_to_session`. **Empirical item (first task of the impl):**
confirm with a live Claude Code and Codex that the trailing newline submits
the input. If either TUI does not submit on the bare newline, the driver
sends `text & return` (an explicit CR before Terminal.app's newline) — a
module constant `_SUBMIT_SUFFIX`, chosen once, with the outcome recorded in
the impl submission and in `docs/how-tagteam-works.md`. There is no third
option that avoids GUI scripting; if neither works the phase stops and
escalates rather than shipping a backend that cannot type.

**A.4 Priming.** `create_session(launch=True)` reuses
`session.wait_for_agent_ready` with `get_session_contents` (the 3.5.1
readiness poll) — no new logic.

### B. `session.py` / CLI surface

- `SUPPORTED_BACKENDS = ("iterm2", "tmux", "terminal", "manual")`;
  `_backend_choices_text()`, `_print_invalid_backend`, `session --help`,
  `quickstart --backend` message and README/how-tagteam-works lists updated.
- `_terminal_supported()`: `sys.platform == "darwin"` and `osascript` on
  PATH and `/System/Applications/Utilities/Terminal.app` exists.
- `default_backend()`: iterm2 → tmux → **terminal** → manual (the one
  default change; only observable on a Mac with neither iTerm2 nor tmux).
- `_validate_backend`, `_print_backend_unavailable("terminal")` (non-macOS:
  "Terminal.app session management is only available on macOS"; suggests
  tmux/manual), `ensure_session` dispatch (same stale-file handling as
  iTerm2, via the driver), `_BACKEND_SURFACE["terminal"] = "window"` for the
  priming box.
- `session adopt --backend terminal --lead /dev/ttys004 …` — the same command
  validates ids through the selected backend's driver and writes
  `"backend": "terminal"`; error text no longer says "iterm2 only" but names
  the two tab backends. `session list-terminal` — sibling of `list-iterm`
  (`session list-iterm` unchanged).
- `session kill` / `attach` dispatch by effective backend (`attach` prints the
  iTerm2-style "not needed" line for `terminal`).

### C. Tab-driver dispatch (watcher, server, health check)

A small module `tagteam/tabs.py`:
- `TAB_BACKENDS = ("iterm2", "terminal")`
- `driver_for(backend) -> module` (`tagteam.iterm` / `tagteam.terminal`)
- the `.handoff-session.json` helpers move here (`SESSION_FILE`,
  `_session_file_path`, `_find_session_file`, `_read_session_file`,
  `_write_session_file`) and `iterm.py` re-exports them (no caller changes,
  no test changes);
- `session_backend(project_dir) -> str | None`: the file's `"backend"`
  (missing → `"iterm2"` for pre-3.6 files).

Watcher:
- new mode `terminal` (`--mode terminal`; help text; `launch.start_watcher`
  accepts it). Internally the iTerm2-specific helpers become tab-driver
  helpers taking the driver module (`send_tab_command`, `is_agent_idle_tab`,
  `wait_for_idle_tab`); `send_iterm_command` / `is_agent_idle_iterm` /
  `wait_for_idle_iterm` remain as thin iTerm2-bound wrappers so existing
  callers and tests are untouched. `_StateProcessor` gains `self.tabs`
  (driver) and every `self.mode == "iterm2"` branch becomes `self.mode in
  TAB_BACKENDS` using it — dispatch, completion notice, watchdog
  `_capture_tail`, startup validation, `_resolve_processor` session-id
  lookup.
- `_auto_detect_mode`: reads `session_backend()`; a session file with both
  ids yields `"iterm2"` or `"terminal"` accordingly (reason string names the
  backend). `--mode iterm2` against a `terminal` session file (or vice
  versa) is an error at startup naming the mismatch.
- Busy/idle patterns, watchdog rules, retries: unchanged, shared.

Server (`_agent_log_tail`) and `state._check_agent_health`: choose the driver
from `session_backend()` instead of importing `tagteam.iterm` directly;
`backend` in the JSON payload reports the actual one.

### D. Docs & tests

- README (backend list, `--backend iterm2|tmux|terminal|manual`),
  `docs/how-tagteam-works.md` (watched-mode paragraph: `--mode …|terminal`,
  windows-not-tabs, tty identity, quit/restore caveat), CLI help strings,
  `tagteam/data/README`-style templates if they name backends.
- Roadmap: Phase 42 entry (this plan) — the backlog "Terminal.app backend"
  item becomes a pointer to it.
- Tests (all with `_osascript` mocked — no AppleScript runs in CI):
  - `tests/test_terminal.py`: create_session window accounting per §A.1 —
    (a) cold launch, one idle launch window → Lead script is
    `do script … in window id <launch>`, two untargeted creations, session
    file = exactly three roles / three ttys / three window ids, launch id ==
    Lead id, no fourth window; (b) warm launch (`was_running`) → three
    untargeted creations, pre-existing ids never targeted or closed;
    (c) cold launch with several restored windows / busy launch tab → same
    as (b); (d) target ignored (new window appears on the Lead call) → the
    new window is Lead, launch window untouched, note printed;
    (e) `new` ≠ 1 on an untargeted call → created windows closed, no
    session file, returns False. Plus: titles per role, tty parse,
    session-file payload with `backend: terminal`, write_text escaping +
    `_SUBMIT_SUFFIX`,
    get_session_contents tail, session_id_is_valid, `_any_session_alive`,
    kill_session, list_sessions parsing, cold-launch reuse vs. running
    branch, `_terminal_supported` platform gating.
  - `tests/test_session.py` (or the existing session tests):
    `SUPPORTED_BACKENDS`, `default_backend()` order incl. the new fall-through,
    `_validate_backend("terminal")` off-macOS message, `ensure_session`
    dispatch, `session adopt --backend terminal`, `list-terminal`.
  - `tests/test_watcher_auto_detect.py`: session file `backend: terminal` →
    mode `terminal`; missing backend key → `iterm2`; mismatch error.
  - watcher tests: `send_tab_command` with a fake driver; `_capture_tail`
    for mode `terminal`; existing iTerm2 tests unchanged.
  - server/state: log tail and health check pick the terminal driver.
- Version: 3.6.0 via `scripts/release.py`; PR from `phase-42-terminal-backend`.

## Technical Approach

1. Extract session-file helpers into `tagteam/tabs.py`; re-export from
   `iterm.py`; suite green (pure move).
2. Write `terminal.py` against the mocked-osascript tests; then the live
   probe (§A.3) on this Mac, recording the submit-suffix outcome.
3. `session.py` backend plumbing + `adopt` / `list-terminal` / kill.
4. Watcher generalisation (`self.tabs`), auto-detect, `launch.start_watcher`.
5. Server + state health dispatch.
6. Docs, roadmap, version bump. Dogfood: one real
   `tagteam session start --backend terminal --launch` on a scratch project
   with the watcher in `terminal` mode driving at least one turn flip; the
   log excerpt goes into the impl submission.

## Files

- new: `tagteam/terminal.py`, `tagteam/tabs.py`, `tests/test_terminal.py`
- modified: `tagteam/iterm.py` (re-exports only), `tagteam/session.py`,
  `tagteam/watcher.py`, `tagteam/launch.py`, `tagteam/server.py`,
  `tagteam/state.py`, `tagteam/cli.py`, `README.md`,
  `docs/how-tagteam-works.md`, `docs/roadmap.md`, existing session/watcher
  tests (additions only), `pyproject.toml`/`CITATION.cff`/`uv.lock` (release)

## Success Criteria

- On a Mac with only Terminal.app: `tagteam session start --backend terminal
  --launch` opens Lead/Watcher/Reviewer windows, both agents start and are
  primed, `.handoff-session.json` has `backend: terminal` and three tty ids;
  `tagteam watch` (no `--mode`) logs `mode: terminal` and, on a turn flip,
  types the command into the correct window only after the idle check;
  `tagteam session kill` closes exactly those windows.
- `tagteam session start --backend terminal` on Linux/Windows prints the
  unavailable message and exits 1; `default_backend()` on those platforms is
  unchanged.
- iTerm2 and tmux paths: every existing test passes unmodified; no AppleScript
  text for iTerm2 changes.
- `tagteam state` health and the dashboard log tail show the terminal
  windows' contents.
- Full suite green; new tests cover the table in §A and the dispatch in §C.

## Known tradeoffs

- Windows instead of tabs (§A.1) — three windows are less tidy than one
  three-tab window; the alternative needs Accessibility permission.
- tty identity dies with the tab, like iTerm2 IDs; a Terminal.app
  quit-and-restore invalidates the session (documented; `session start`
  detects and recreates).
- Text entry relies on `do script`'s newline (or `text & return`); there is
  no way to send bytes without a newline, so multi-line sends are not
  supported (none are used).
