---
title: Saloon Rethink — themed onboarding & monitoring scene
date: 2026-08-16
status: brainstorm (unscheduled; add to roadmap Backlog after the Phase 37 branch merges)
author: Jack Blacketter (with Claude)
---

# Saloon Rethink

Side-brainstorm captured while a tagteam session was in progress. Nothing
here is scheduled. When the current phase branch merges, add the entry
in §8 to `docs/roadmap.md` → Backlog.

## 1. How we got here

The visual layer was never designed as a whole; it accreted:

1. **ASCII art** — a fun visual representation of the handoff (TUI era,
   Phase 4).
2. **Monkey Island energy** — "something fun and silly" led to a
   frontier saloon with characters (Phases 10–11).
3. **Today** — Mayor, Bartender (rendered with the Rabbit sprite),
   Watcher, and a Clock. The Clock was originally meant to *be* the
   watcher; that intent drifted and now it's decorative.

Since Phase 34, the Saloon is one **theme** of the dashboard
(`serve.theme: saloon | cockpit`), and the cockpit is the serious
surface. That's the right split and this rethink keeps it: the theme is
a skin over the same state, and its job is to be **fun and humorous
without getting in the way**.

## 2. Diagnosis: characters map to features, not to the loop

| Today | What it maps to | Problem |
|---|---|---|
| Mayor | "oversees"; setup / start a phase | Abstract — a new user can't tell what a Mayor *does* in a handoff |
| Bartender | "keeps reviews flowing" | Vague; reuses the Rabbit sprite; doesn't correspond to a real actor |
| Watcher | the daemon | Good — this one is honest |
| Clock | (nothing) | Vestigial; was meant to be the watcher |

The concepts a first-time user actually needs are the **roles in the
loop**: there is a Lead, a Reviewer, something that passes the turn
between them, and *you* (the Arbiter). None of the current characters
is the Lead or the Reviewer — the two most important actors in the
product have no on-screen presence. That's why the scene is charming
but doesn't teach.

## 3. Design principles for the rethink

1. **Cast = loop roles.** One character per real actor. If a character
   doesn't correspond to something in `tagteam.yaml` or the state
   machine, cut it.
2. **The player is the Arbiter.** The user isn't a spectator; the scene
   should turn to face them on `ESCALATE` / `NEED_HUMAN`.
3. **Teach by doing, in ≤ 3 beats.** First-run flow must accomplish
   exactly: (a) name/choose the two agents (`init`), (b) start the
   watcher/session, (c) hand the user the first message to give the
   Lead. Everything else is optional flavor.
4. **State drives the picture.** Every visible element should change
   with a real state field (turn, round, action, paused, in-flight
   headless turn). No animation without a data source.
5. **Never block.** All dialogue is skippable; the cockpit is one click
   away; power users can set `serve.theme: cockpit` and never see it.
6. **Setting-agnostic engine.** The engine knows *archetypes*; a theme
   pack supplies names, sprites, palette and lines. Swapping settings
   is data, not code.

## 4. The archetype cast (setting-agnostic)

| Archetype | Backed by | Visual behavior |
|---|---|---|
| **Host / Guide** | onboarding + config (`init`, `setup`, `session start`) | Greets on first run; afterwards recedes to a corner "help" presence |
| **Lead** | the configured lead agent (name from `tagteam.yaml`) | Active/glowing on lead's turn; "working" animation while a headless turn is in flight; holds the artifact (plan/impl) |
| **Reviewer** | the configured reviewer agent | Active on reviewer's turn; `REQUEST_CHANGES` = hands it back; `APPROVE` = stamps/nods |
| **Turn-keeper** | watcher / orchestrator | Passes the token between Lead and Reviewer; visibly asleep when the watcher isn't running (this is a real, useful signal) |
| **Round clock** | `round` / `STALE_ROUND_LIMIT` (10) | Ticks per round; grows tense near the cap; this restores the Clock's original meaning |
| **You (Arbiter)** | the human | Not a sprite by default; on escalation the whole cast turns toward the "camera" and the escalation brief (Phase 33) appears as the dialogue |

Six roles including the player. Any setting must cast all of the first
five; the sixth is the viewer.

### State → picture mapping

| State | Picture |
|---|---|
| No `tagteam.yaml` | Host alone on stage; first-run beats begin |
| Configured, watcher down | Turn-keeper asleep; Host hint: "start the session" |
| Lead's turn | Lead lit; Reviewer idle pose |
| Reviewer's turn | Reviewer lit; Lead idle |
| Headless turn in flight | Lit character has a "thinking/working" loop |
| `REQUEST_CHANGES` | Reviewer hands artifact back (brief animation) |
| `APPROVE` | Stamp / cheer beat; clock resets for next cycle |
| Round ≥ 7 of 10 | Clock visibly agitated |
| `ESCALATE` / `NEED_HUMAN` | Cast faces the player; brief shown; choice buttons |
| Paused / interjected | Freeze frame + "paused by <who>" placard |

## 5. First-run script (setting-agnostic outline)

1. **Host:** welcome; one sentence on what tagteam is ("two agents,
   one reviews the other, you break ties").
2. **Host → Lead & Reviewer intro:** "Who's on the team?" → runs `init`
   (lead name, reviewer name). The two sprites appear labeled with the
   chosen names.
3. **Turn-keeper intro:** "I pass the turn back and forth. Want me
   awake?" → starts session/watcher (or explains headless mode).
4. **Host hands you the kickoff:** shows the exact first message to
   give the Lead (`/handoff start <phase>` or the priming text), with a
   copy button. Ends: "Go talk to <lead name>. I'll be right here."

Three beats plus a hand-off — matches principle 3.

## 6. Five candidate settings (try each once the engine is theme-driven)

Same five archetypes every time; only the casting changes. The Arbiter
line says how the setting explains *you*.

| # | Setting | Host / Guide | Lead | Reviewer | Turn-keeper | Round clock | You (Arbiter) |
|---|---|---|---|---|---|---|---|
| 1 | **Saloon (revised)** | Mayor | Prospector (digs up the work) | Assayer (tests the ore, sends it back if it's fool's gold) | Station master with the signal flag | Saloon clock (restored to meaning) | The Sheriff — you settle disputes |
| 2 | **Alien spaceship** (Jack's pitch) | Ship's AI voice | Alien engineer | Robot inspector (pedantic, literal — a natural reviewer) | Airlock cycle light passing the token | Countdown to next jump | The abducted human, made judge of an alien argument you barely understand — humor writes itself |
| 3 | **Pirate ship** (Monkey Island lineage) | Captain | First mate (does the deed) | Quartermaster (checks the charts, "not on my ledger") | Lookout in the crow's nest ("your turn!") | Ship's bell, eight bells = round cap | Governor / admiralty — you decide who walks the plank |
| 4 | **Mission control** (1960s NASA) | Flight director | Astronaut | CAPCOM / flight surgeon ("no-go") | Comms loop light | Launch countdown clock | You're in the big chair; "Flight, we need a decision" |
| 5 | **Restaurant kitchen** | Maître d' | Chef | Expediter / critic ("send it back") | Ticket rail / bell | Order timer | The owner; a dish sent back three times lands on your table |

Notes:
- #2 and #5 have the strongest built-in review humor (`REQUEST_CHANGES`
  = "send it back", the robot's literalism).
- #4 is the most legible for professional viewers (portfolio audience)
  and maps escalation cleanly.
- #1 preserves existing sprites/palette; cheapest to ship first as the
  proof of the theme engine.

## 7. Engine sketch (for a future plan cycle, not a spec)

- `tagteam/data/web/themes/<name>/theme.json` — cast (archetype → name,
  sprite id, portrait, idle/active/working poses), palette, and
  dialogue strings keyed by beat (`welcome`, `intro_lead`,
  `intro_reviewer`, `intro_keeper`, `kickoff`, `escalate`, `approve`,
  `request_changes`, `paused`).
- Sprites per theme in `sprites/<theme>.js` (or SVG files); engine
  loads by theme name.
- `serve.theme` extended: `cockpit | saloon | ship | pirate |
  mission | kitchen`, plus `theme.pack: <path>` for user packs later.
- Existing conversation engine (`conversation.js`) already does
  typewriter/portraits/branches; it becomes archetype-keyed instead of
  character-keyed. Existing `Sprites.render*` becomes
  `Sprites.render(archetype, state, theme)`.
- Acceptance for a first cut: saloon re-cast to the archetype model
  with zero new art (reuse sprites, rename roles), first-run flow in
  three beats, clock bound to `round`.

## 8. Roadmap Backlog entry (paste after the Phase 37 branch merges)

```
### Saloon rethink — archetype cast & theme packs
- **Status:** Not started — brainstorm in `docs/saloon-rethink.md` (2026-08-16)
- **Description:** Recast the fun theme around the loop's real roles (Host, Lead, Reviewer, Turn-keeper, Round clock, You-as-Arbiter) instead of feature mascots; three-beat first-run flow (init agents → start watcher → hand the user the kickoff message); every element bound to real state. Make the engine theme-driven so up to five settings can be trialed (saloon revised, alien spaceship, pirate ship, mission control, restaurant kitchen). Cockpit remains the serious surface; theme is a skin.
```

## 9. Open questions

- Should Lead/Reviewer sprites reflect the *vendor* (Claude vs Codex
  vs other) or stay purely in-setting? Suggest in-setting, with the
  configured name as the label.
- Does the theme layer live in the same server as the cockpit (yes,
  today) or move to a `/theme/` route beside `/p/<id>/` in the hub?
- Sound: keep the TUI sound effects as per-theme cues, or drop?
- Should the first-run flow also exist in the CLI (`tagteam quickstart`
  already prints a priming box) so the two never disagree? Probably:
  same beats, two renderers.
