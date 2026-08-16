<p align="center">
  <img src="docs/assets/banner.svg" alt="tagteam — two AIs hand off the work, one human breaks the tie" width="100%">
</p>

# Tagteam

**Two AIs hand off the work, one human breaks the tie.** One AI agent leads, a second reviews, and you arbitrate — phase by phase, from a roadmap, with every round on the record.

## The loop

```mermaid
flowchart LR
    R["Roadmap<br/>next phase"] --> P["Lead writes<br/>the plan"]
    P --> PR["Reviewer reviews<br/>the plan"]
    PR -- "request changes" --> P
    PR -- "approve" --> I["Lead implements"]
    I --> IR["Reviewer reviews<br/>the implementation"]
    IR -- "request changes" --> I
    IR -- "approve" --> R
    PR -. "escalate" .-> A["Arbiter (you)<br/>rules"]
    IR -. "escalate" .-> A
    A -. "request changes (plan)" .-> P
    A -. "approve (plan) /<br/>request changes (impl)" .-> I
    A -. "approve (impl)" .-> R
```

- **Lead** (one AI agent) plans each phase, then implements the approved plan.
- **Reviewer** (a second AI agent) reviews both — the plan, then the implementation — and approves, requests changes, or escalates.
- **Arbiter** (you) enters only when the two cannot settle it: an escalation, a question only a human can answer, or a cycle that stopped making progress. Your ruling takes the reviewer's seat: **request changes** hands the turn back to the Lead; **approve** closes the cycle — an approved plan goes to implementation, an approved implementation advances the roadmap.

Each phase in `docs/roadmap.md` goes through two cycles — **plan**, then **impl** — and each cycle is a sequence of **rounds** (one lead submission, one reviewer response). Every round is appended to `docs/handoffs/`, and `handoff-state.json` says whose turn it is; either agent can pick up where the other left off.

## Why

An AI grading its own work rarely catches its own blind spots; a second model with a different training does. Long agent sessions drift and get expensive; short, bounded turns don't. And a human who has to relay every message is the bottleneck — the loop should run by itself and call you only when it needs a decision. Tagteam is the smallest structure that gets all three: a lead, a reviewer, and a record.

## Try it

```bash
pip install tagteam
cd ~/projects/myproject
tagteam quickstart
```

You'll be prompted for your two agent names, then quickstart sets up the workspace and starts a session. It auto-detects the best terminal backend available on your machine:

- **iTerm2** (macOS, default when iTerm2 is installed) — opens three labeled tabs in a single window, auto-launching iTerm2 if it isn't already running.
- **tmux** (Linux, WSL, or macOS without iTerm2) — creates one `tmux` session with three labeled panes.
- **manual** (anywhere else, including Windows without WSL) — prints the three commands for you to run in terminals you open yourself.

When quickstart finishes it prints what to paste into the Lead and Reviewer agents to kick off the first phase. Override the auto-detection with `--backend iterm2|tmux|manual` if you need a specific one.

**Single phase** — start a plan cycle, let the watcher handle the back-and-forth, and stop when the phase completes:

```text
/handoff start my-phase
```

**Full roadmap** — run all incomplete phases end-to-end:

```text
/handoff start --roadmap
/handoff start --roadmap api-gateway
```

| Command                         | Purpose                                         | Who  |
| ------------------------------- | ----------------------------------------------- | ---- |
| `/handoff`                      | Auto-detects role + state, does the right thing | Both |
| `/handoff start [phase]`        | Begin a new phase (plan cycle)                  | Lead |
| `/handoff start [phase] impl`   | Begin the implementation cycle for a phase      | Lead |
| `/handoff status`               | Orientation, status check, drift reset          | Both |

**Human-in-the-loop** — add `--confirm` to pause for approval before each automatic send: `tagteam watch --mode notify --confirm`.

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

1. **Headless mode** (recommended, fully automated) — no terminals to drive at all; each turn is a fresh `claude -p` / `codex exec` process. See [Headless](#headless-fresh-process-per-turn) below.
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

> **Manual mode:** you can always run the loop without any automation by pasting `/handoff` output between agents yourself.

</details>

## One cycle

```mermaid
stateDiagram-v2
    [*] --> Submitted: lead SUBMIT_FOR_REVIEW (round 1)
    Submitted --> Submitted: lead AMEND (same round)
    Submitted --> Changes: reviewer REQUEST_CHANGES
    Changes --> Submitted: lead SUBMIT_FOR_REVIEW (round N+1)
    Submitted --> Approved: reviewer APPROVE
    Submitted --> Escalated: reviewer ESCALATE / NEED_HUMAN
    Submitted --> Escalated: REQUEST_CHANGES after 10 consecutive stale rounds (auto)
    Escalated --> Approved: arbiter rules approve
    Escalated --> Changes: arbiter rules request-changes / answers
    Approved --> [*]
```

A cycle ends when the reviewer approves. It comes to you when the reviewer escalates, asks a question only a human can answer, or when the lead has re-submitted unchanged content for **10 consecutive stale rounds** — a cycle that is still making progress can go well beyond ten rounds without escalating. When it does come to you, `tagteam brief` gives you a decision brief (each side's position, the crux, a recommendation, the exact commands) and `tagteam rule approve|request-changes|answer` puts your ruling on the record — or do both from the cockpit's **Needs you** card. Details: [escalations and the briefer](docs/how-tagteam-works.md#escalations).

## Choose how much runs by itself

```mermaid
flowchart LR
    M["Manual<br/>you paste /handoff<br/>between two agents"] --> W["Watched<br/>tagteam watch drives<br/>your terminals"]
    W --> H["Headless<br/>each turn is a fresh<br/>claude -p / codex exec"]
    H --> C["+ Cockpit & Hub<br/>watch and steer<br/>from the browser"]
```

Every rung is the same loop and the same files; only the automation changes. **Manual** costs nothing to set up. **Watched** (`tagteam watch --mode notify|iterm2|tmux`) types the next command into the right terminal for you. **Headless** is for running unattended, on Windows, or whenever long-lived agent sessions become the problem.

### Headless: fresh process per turn

```mermaid
flowchart LR
    W["Watcher<br/>tagteam watch --mode headless"]
    T["Fresh turn process<br/>claude -p / codex exec<br/>skill + state + last rounds"]
    F[("handoff-state.json<br/>docs/handoffs/*.jsonl<br/>.tagteam/tagteam.db")]
    C["Cockpit<br/>tagteam serve --theme cockpit"]
    HUB["Hub<br/>tagteam hub"]
    A["Arbiter (you)"]
    W -- "spawn turn" --> T
    T -- "tagteam cycle add<br/>+ token usage" --> F
    F -- "turn flip" --> W
    F -- "SSE" --> C
    HUB -- "one row per project,<br/>cockpit at /p/id/" --> C
    A -- "pause / interject / rule" --> C
    C -- "same CLI commands" --> F
```

Instead of typing commands into long-lived agent terminals, the watcher can spawn each turn as a **fresh process** through the agent's own signed-in CLI (`claude -p` for Claude, `codex exec` for Codex — subscription auth, no API keys). Every turn gets a bounded context (the handoff skill contract + `handoff-state.json` + the last few rounds), writes its own round with `tagteam cycle add`, and its token usage is recorded. Nothing runs headless unless you ask:

```bash
tagteam watch --mode headless          # never auto-detected; explicit opt-in only
tagteam tail                           # follow the in-flight turn like CI logs
```

**When something goes wrong** (a turn times out, exits nonzero, exits without writing its round, or the CLI cannot start), the watcher pauses dispatch, writes `.tagteam/headless-paused.json` with the reason and log path, and notifies you. It never retries silently. Per-role options (`provider`, `executable`, `args`, `timeout_minutes`) live under `agents.<role>.headless` in `tagteam.yaml`; opt-in retries re-run a turn only when it provably did nothing. Details, defaults and the validation rules: [headless mode](docs/how-tagteam-works.md#headless).

**Arbiter controls (any watcher mode):**

```bash
tagteam pause --reason "reviewing by hand"    # every watcher mode holds dispatch
tagteam resume                                # clears the hold; the owed turn is re-dispatched once
tagteam cancel-turn                           # kill the in-flight headless turn → outcome 'cancelled', then paused
tagteam interject "prefer the smaller diff"   # note for the next turn (--to lead|reviewer to target a role)
tagteam interject --list                      # pending / delivered / retired notes for this cycle
tagteam interject --retire 3                  # close a note without delivering it
tagteam usage [--json]                        # per-turn tokens; roll-ups by role, by cycle, totals
```

How each of these behaves (pause markers, cancel-turn identity checks, interjection scoping, retries, notifications, `tagteam rollback`): [arbiter controls](docs/how-tagteam-works.md#controls).

## Watch and steer

**The Cockpit** — a browser dashboard for one project, built around the arbiter's actual job: *does anything need me?* then *is it healthy and what is it doing?*

```bash
tagteam serve --theme cockpit --dir ~/projects/myproject      # http://localhost:8080
```

<p align="center"><img src="docs/media/screenshots/cockpit-needs-you.png" alt="Cockpit: the Now strip (phase, turn, watcher, connection), a Needs-you card for an escalated plan cycle with its decision brief and Approve / Request changes buttons, and the Watch tabs" width="100%"></p>

The **Now** strip (phase / type / round, whose turn and for how long, the in-flight turn, the pause hold, watcher liveness, connection mode); **Needs you** — one card per thing only the human can do, each with the buttons and the exact CLI they run; **Watch** tabs — Feed, Diff, Usage, Notes. Every button is the CLI command with the same effect, recorded as `by = web:<user>`. Cockpit mode binds **127.0.0.1** by default and guards every POST with a per-run page token; `--host 0.0.0.0` exposes it on the network and is your call. Zones, watcher liveness and the security model in full: [the cockpit](docs/how-tagteam-works.md#cockpit).

**The Hub** — every project you've set up, in one list ranked by what needs you.

```bash
tagteam hub                     # http://localhost:8090 — every registered project, ranked by what needs you
tagteam hub --list [--json]     # the same triage as text
```

<p align="center"><img src="docs/media/screenshots/hub.png" alt="Hub: three registered projects — one under Needs you (escalated, brief ready, Open), one under Waiting (reviewer owed for hours, stale, with the CLI to run), one under Quiet — with burn and the shared subscription window in the top strip" width="100%"></p>

**Needs you** → **Waiting** (turns owed to agents; **stale** when nothing is dispatching) → **Quiet**, with burn across projects and the shared subscription window in the strip. **Open** takes you into that project's cockpit, mounted at `/p/<id>/`. The hub is read-only: it never migrates a project database and never rewrites the registry (`tagteam registry list|unregister PATH`). Details: [the hub](docs/how-tagteam-works.md#hub).

**The Saloon** — the original western-themed dashboard survives as a theme: bare `tagteam serve` is unchanged, and in cockpit mode it lives at `/?theme=saloon`. [More](docs/how-tagteam-works.md#saloon).

## Reference

**Configuration** — agents are defined in `tagteam.yaml`:

```yaml
agents:
  lead:
    name: claude
    command: claude
  reviewer:
    name: codex
    command: codex
```

**CLI reference:**

```bash
tagteam quickstart                     # Setup + init + session start
tagteam session start                  # Auto-detect backend, launch agents
tagteam session start --backend manual # Force manual backend
tagteam session start --no-launch      # Create terminals, skip agent launch
tagteam session kill
tagteam init
tagteam setup
tagteam migrate                        # migrate a legacy project to tagteam.yaml
tagteam state
tagteam state diagnose
tagteam watch --mode notify
tagteam watch --mode headless          # spawn each turn as a fresh agent process
tagteam watch --pidfile                # keep .tagteam/watcher.json for the cockpit's liveness strip
tagteam tail                           # follow the in-flight headless turn
tagteam cycle rounds --phase P --type plan --tail 3
tagteam pause --reason "..." / tagteam resume / tagteam cancel-turn
tagteam interject "note" [--to lead|reviewer] / --list / --retire ID
tagteam usage [--json]
tagteam serve [--theme cockpit] [--host H] [--port N] [--max-sse N]   # dashboard; cockpit is opt-in
tagteam hub [--list [--json]] [--all] [--port 8090]                   # all registered projects; cockpits at /p/<id>/
tagteam registry list [--json] | unregister PATH
tagteam brief [--list | --generate | --event KEY]
tagteam rule approve|request-changes|answer [--content ...] [--to lead|reviewer]
tagteam rollback 0.8.0 [--yes]
tagteam roadmap phases
tagteam serve --dir .
tagteam tui                            # optional Textual TUI (pip install 'tagteam[tui]')
tagteam upgrade                        # re-copy framework files into every registered project
tagteam --help
```

**More:**

- [How tagteam works](docs/how-tagteam-works.md) — the long version of every section above, plus the files tagteam writes and where.
- [Showcase](docs/showcase.md) — the problem, the loop and the numbers from this repository's own use of tagteam.
- [Diagrams and screenshots](docs/media/README.md) — the SVGs in this README as standalone assets.
- [Roadmap](docs/roadmap.md), [3.0 proposal](docs/tagteam-3.0-proposal.md), per-phase plans and findings under `docs/phases/`.

## License

MIT — see [LICENSE](LICENSE). Tagteam is open source and free to use, modify, and
redistribute; the license asks only that the copyright notice travels with the code.
If tagteam is useful to you, a link back to this repository is appreciated
(see [CITATION.cff](CITATION.cff) or GitHub's "Cite this repository" button).
