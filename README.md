<p align="center">
  <img src="docs/assets/banner.svg" alt="tagteam — two AIs hand off the work, one human breaks the tie" width="100%">
</p>

# Tagteam

A collaboration framework for structured AI-to-AI handoffs with human oversight. One AI leads, another reviews, and you arbitrate — the whole cycle runs phase by phase from a roadmap.

## How it works

- **Lead** (one AI agent) plans each phase and implements the approved plan.
- **Reviewer** (a second AI agent) reviews both the plan and the implementation.
- **Arbiter** (you, the human) breaks ties and approves phases.

Work progresses phase by phase. Each phase is listed in `docs/roadmap.md` and goes through two review cycles: plan, then implementation. If the two agents can't make progress in 10 rounds, control escalates to the human arbiter.

State is tracked in `handoff-state.json` (current turn) and `docs/handoffs/<phase>_<type>_rounds.jsonl` + `_status.json` (per-cycle rounds). Either agent can pick up where the other left off at any time.

## Quick Start

```bash
pip install tagteam
cd ~/projects/myproject
tagteam quickstart
```

You'll be prompted for your two agent names, then quickstart sets up the workspace and starts a handoff session. It auto-detects the best terminal backend available on your machine:

- **iTerm2** (macOS, default when iTerm2 is installed) — opens three labeled tabs in a single window, auto-launching iTerm2 if it isn't already running.
- **tmux** (Linux, WSL, or macOS without iTerm2) — creates one `tmux` session with three labeled panes.
- **manual** (anywhere else, including Windows without WSL) — prints the three commands for you to run in terminals you open yourself.

When quickstart finishes it prints what to paste into the Lead and Reviewer agents to kick off the first handoff. Override the auto-detection with `--backend iterm2|tmux|manual` if you need a specific one.

## Running a handoff

**Single phase** — start a plan review, let the watcher handle the back-and-forth, and stop when the phase completes.

```text
/handoff start my-phase
```

**Full roadmap** — run all incomplete phases end-to-end.

```text
/handoff start --roadmap
/handoff start --roadmap api-gateway
```

| Command                         | Purpose                                         | Who  |
| ------------------------------- | ----------------------------------------------- | ---- |
| `/handoff`                      | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]`        | Begin a new phase (plan + review cycle)         | Lead |
| `/handoff start [phase] impl`   | Begin implementation review for a phase         | Lead |
| `/handoff status`               | Orientation, status check, drift reset          | Both |

**Human-in-the-loop** — add `--confirm` to pause for approval before each automatic send.

```bash
tagteam watch --mode notify --confirm
```

## Other platforms

<details>
<summary>tmux (explicit invocation)</summary>

```bash
tagteam quickstart --backend tmux
```

Creates one `tmux` session named `tagteam` with three labeled panes (Lead, Watcher, Reviewer). Attach later with `tmux attach -t tagteam`.

</details>

<details>
<summary>Windows / manual fallback</summary>

On Windows without WSL, terminal automation (iTerm2/tmux) isn't available. You have two options:

1. **Headless mode** (recommended, fully automated) — no terminals to drive at all; each turn is a fresh `claude -p` / `codex exec` process. See [Headless mode](#headless-mode-opt-in) below.
2. **Manual fallback** — quickstart prints the commands for you to run yourself in three terminals:

```bash
tagteam quickstart --backend manual
```

You can also run each step individually:

```bash
tagteam setup
tagteam init
tagteam session start --backend manual
tagteam watch --mode notify
```

</details>

<details>
<summary>Advanced setup (run each step yourself)</summary>

```bash
tagteam setup               # copy skills, templates, docs
tagteam init                # interactive agent config → tagteam.yaml
tagteam session start       # create terminals and auto-launch agents
```

Options:

- `tagteam session start --no-launch` — create terminals but don't start agents
- `tagteam session start --backend <name>` — force a specific backend
- `tagteam session kill` — close the current session

> **Manual mode:** you can always run handoffs without any automation by pasting `/handoff` output between agents yourself.

</details>

## Headless mode (opt-in)

Instead of typing commands into long-lived agent terminals, the watcher can spawn each turn as a **fresh process** through the agent's own signed-in CLI (`claude -p` for Claude, `codex exec` for Codex — subscription auth, no API keys):

```bash
tagteam watch --mode headless          # never auto-detected; explicit opt-in only
tagteam tail                           # follow the in-flight turn like CI logs
```

On every turn flip the orchestrator composes a bounded context (the handoff skill contract + `handoff-state.json` + the last 3 rounds), pipes it to the agent on stdin, streams the agent's structured output to `.tagteam/turns/<phase>_<type>_r<N>_<role>_<ts>.log` (human-readable) and `.events.jsonl` (raw), and — because the agent still writes its own round with `tagteam cycle add` — verifies that the expected round landed before dispatching the other agent. Per-turn token usage is recorded in the project DB (`usage` table) for later phases to surface.

**When something goes wrong** (turn timeout — 60 min by default; nonzero exit; the agent exited without writing its round; or the CLI could not be started at all), the watcher pauses dispatch, writes `.tagteam/headless-paused.json` with the reason and log path, and sends a notification. It never retries silently. To resume: read the log, fix anything needed, delete the marker; the watcher picks up on its next tick.

```bash
tagteam watch --mode headless --turn-timeout 90 --tail-rounds 5 --confirm
tagteam cycle rounds --phase my-phase --type plan --tail 2   # last 2 entries only
```

Per-role options live under `agents.<role>.headless` in `tagteam.yaml` (all optional):

```yaml
agents:
  lead:
    name: Claude
    headless:
      provider: claude                # claude | codex (inferred from command/name if omitted)
      executable: /opt/bin/claude     # default: `claude` on PATH
      args: ["--model", "opus"]       # a YAML list; validated — no positionals, no reserved flags
  reviewer:
    name: Codex
    headless:
      args: ["-c", "approval_policy=untrusted"]
```

Defaults are the least-privileged unattended settings that still let the agent edit the repo and run the cycle CLI: Claude runs with `--permission-mode acceptEdits --allowedTools Bash Read Edit Write Glob Grep`; Codex with `--sandbox workspace-write -c approval_policy=never`. Anything you put in `args` is checked against a per-provider option table so a stray token can never become the prompt or override tagteam's own flags.

Interactive modes are unchanged — headless is a peer mode. It is also the path for **Windows**: it needs only `subprocess`, and the test suite runs on `windows-latest` in CI (a real signed-in CLI smoke on Windows is best-effort; see the Phase 31 findings doc).

### Arbiter controls (any watcher mode)

```bash
tagteam pause --reason "reviewing by hand"    # every watcher mode holds dispatch
tagteam resume                                # clears the hold; the owed turn is re-dispatched once
tagteam cancel-turn                           # kill the in-flight headless turn → outcome 'cancelled', then paused
tagteam interject "prefer the smaller diff"   # note for the next turn (--to lead|reviewer to target a role)
tagteam interject --list                      # pending / delivered / retired notes for this cycle
tagteam interject --retire 3                  # close a note without delivering it
tagteam usage [--json]                        # per-turn tokens; roll-ups by role, by cycle, totals
```

- **pause/resume** use the same marker file the engine writes on a failed turn (`.tagteam/headless-paused.json`), so `resume` also tells you what failed and where the log is.
- **cancel-turn** never signals a PID it cannot bind to the recorded turn: it checks the child's and the watcher's creation identities (recorded at spawn) and the parent pid, and if anything is stale or unverifiable it just removes the stale metadata and says so.
- **interject** notes are stored with provenance (who, when, which cycle/round/turn was owed) in the project DB and go into the *next eligible* turn's prompt under an `ARBITER INTERJECTIONS` heading (headless) or show up as `interjections` on `tagteam cycle rounds` (interactive). A note is scoped to the cycle it was written for; delivery is stamped only when the receiving turn succeeds. `--to reviewer` waits for the reviewer's turn.
- **Retries** (`tagteam watch --mode headless --turn-retries N`, default 0) re-run a failed turn only when it provably did nothing: the outcome is `spawn_failed`/`nonzero_exit`/`timeout` **and** a content-sensitive repo fingerprint (HEAD + index + worktree, recursively through every gitlink) **and** the handoff state are unchanged. `no_round`/`cancelled` are never retried; any git failure or unmerged index fails closed. Only `.gitignore`d paths are outside the fingerprint.
- Per-role turn timeouts: `agents.<role>.headless.timeout_minutes`.
- **Notifications** work on macOS (osascript), Windows (toast, `msg` fallback) and Linux (`notify-send`); `TAGTEAM_NO_NOTIFY=1` silences them.
- `tagteam rollback X.Y.Z` prints the revert recipe for your install (uv tool or pip, then `tagteam upgrade`) and runs it only with `--yes`.

### Escalations: the briefer and `tagteam rule`

When a cycle escalates (`ESCALATE`, `NEED_HUMAN`, or auto-escalation after 10 stale rounds) you are the arbiter. Opt in to the **escalation briefer** and the watcher will spawn one headless turn that writes you a decision brief — each side's position, the actual crux, what it checked, a recommendation with confidence, and the exact ruling commands:

```yaml
# tagteam.yaml
briefer:
  enabled: true              # opt-in; absent = off (0.9.0 behavior)
  # provider: claude          # default: the lead's provider
  # args: ["--model", "..."]  # try a lighter model; usage is recorded under role "briefer"
  # timeout_minutes: 15
```

```bash
tagteam brief                       # the brief for the CURRENT escalation event (never an older one)
tagteam brief --list                # every attempt (auto/manual, status, path)
tagteam brief --generate            # run the briefer now (manual attempt; also the retry path)
tagteam rule approve --content "…"  # arbiter takes the reviewer's seat: closes the cycle
tagteam rule request-changes --content "…"   # hands the turn back to the lead (no auto re-escalation)
tagteam rule answer --to reviewer --content "…"  # for NEED_HUMAN: answer delivered as an interjection, cycle re-armed
```

Briefs land in `docs/escalations/<phase>_<type>_r<N>_<event>-a<attempt>.md` (unique per escalation event and attempt; `…_latest.md` is an alias) and in the project DB. It fires **at most once automatically per escalation event** (a pre-spawn claim guarantees this even with two watchers), never retries on its own, never pauses the loop, and its tokens show up in `tagteam usage`. Everything it does is read-only except writing the brief file.

## The Saloon

A graphical dashboard for monitoring and controlling handoff cycles:

```bash
tagteam serve --dir ~/projects/myproject
```

## Configuration

Agents are defined in `tagteam.yaml`:

```yaml
agents:
  lead:
    name: claude
    command: claude
  reviewer:
    name: codex
    command: codex
```

## CLI Reference

```bash
tagteam quickstart                     # Setup + init + session start
tagteam session start                  # Auto-detect backend, launch agents
tagteam session start --backend manual # Force manual backend
tagteam session start --no-launch      # Create terminals, skip agent launch
tagteam session kill
tagteam init
tagteam setup
tagteam state
tagteam state diagnose
tagteam watch --mode notify
tagteam watch --mode headless          # spawn each turn as a fresh agent process
tagteam tail                           # follow the in-flight headless turn
tagteam cycle rounds --phase P --type plan --tail 3
tagteam pause --reason "..." / tagteam resume / tagteam cancel-turn
tagteam interject "note" [--to lead|reviewer] / --list / --retire ID
tagteam usage [--json]
tagteam brief [--list | --generate | --event KEY]
tagteam rule approve|request-changes|answer [--content ...] [--to lead|reviewer]
tagteam rollback 0.8.0 [--yes]
tagteam roadmap phases
tagteam serve --dir .
tagteam upgrade
tagteam --help
```

## License

MIT
