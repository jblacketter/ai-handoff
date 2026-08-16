# Phase 36: Visual Story & Portfolio Feed (3.0 arc)

## Status
- [x] Planning
- [ ] In Review
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete (release **3.0.0** via PR — the release that completes Phase 36 ships as 3.0.0, proposal §5)

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** the storytelling phase that closes the 3.0 arc. Three
deliverables, all documentation and media — no package code changes:

1. **README restructured around a visual narrative** — what tagteam is,
   the loop, one review cycle, and the 3.0 architecture as mermaid
   flowcharts (GitHub renders them), organized in the *reader's* order
   (what → why → try it → how a phase runs → run it unattended → watch
   and steer → reference). Depth moves to a README-linked
   `docs/how-tagteam-works.md`; nothing currently documented is lost
   (coverage ledger below).
2. **`docs/media/`** — standalone SVG exports of the key diagrams,
   deliberately shaped like the portfolio site's Aegis assets
   (`aegis-flow.svg` 800×260, `aegis-tiers.svg` 800×240: dark panels,
   monospace labels, `role="img"` + `aria-label`, no external refs), plus
   a real cockpit/hub screenshot set and a manifest so a future
   `tagteam.html` portfolio page can consume them without edits.
3. **`docs/showcase.md`** — a one-pager for an outside reader: the
   problem, the loop, the numbers, generated from this repo's own soak
   data by a checked-in script (`scripts/showcase_numbers.py`) so the
   numbers are reproducible, not typed.

**Why:** proposal §4 Phase 36 — "§6's per-phase docs duty keeps things
*accurate*, this phase makes them *compelling*." The README today is
285 lines in build order (Quick Start → handoff → platforms → headless
→ controls → briefer → cockpit → hub → saloon → config → CLI): accurate,
but a first-time reader meets a bulleted role list, then a wall of
feature prose whose biggest block (headless) is the one they need last.
There is no picture of the loop anywhere a visitor lands.

**Depends on:** nothing new. Consumes what Phases 31–35 recorded
(`docs/handoffs/*.jsonl`, `.tagteam/legacy/*.jsonl`, `tagteam usage`).
Source: `docs/tagteam-3.0-proposal.md` §4 Phase 36, §5 (3.0.0), §6.

**Size:** medium. Branch `phase-36-visual-story`, PR at the end.
**Release:** 3.0.0 (version bump only; behavior identical to 0.12.0).

## UX design (flow first — `ux-design-guide`)

**Who / goal.** Three readers, one story:

- *GitHub visitor:* "what is this, is it for me, how do I try it?" (5-second
  test on the README).
- *Portfolio visitor* (recruiter / peer): gets the story from three or four
  pictures on a page that is not in this repo — needs assets, not prose.
- *Outside reader of `docs/showcase.md`:* wants the problem, the loop and
  evidence that it works, in one screen.

**Diagnosis.** "New users don't know where to start" + "It looks cluttered
even though it works": the README's IA follows the build history (module
order), not the reader's intent; visual hierarchy is flat (every feature
is an H2/H3 with paragraphs; no diagram carries structure); the same
concept appears under several words ("handoff", "cycle", "review cycle",
"handoff session").

**Principles (4).**

1. **Mental models / match the real world** — tell it in the reader's
   order: *problem → the loop → try it → what happens during a phase →
   run it unattended → watch and steer → reference*. Never in module
   order.
2. **Progressive disclosure** — the README carries the narrative and the
   happy path; every deep paragraph (headless `args` validation, retry
   fingerprint, cancel-turn identity, briefer claim semantics, cockpit
   security note, hub read-only guarantees, watcher liveness) moves to
   `docs/how-tagteam-works.md`, one section per README section, linked
   from the section it left. Platform variants stay in `<details>`.
3. **Visual hierarchy (squint test)** — one diagram per major section;
   the four diagrams *are* the outline. Feature prose is demoted to a
   sentence + link where a diagram or table says it better.
4. **Consistent language** — a glossary fixed up front and enforced in the
   README, how-tagteam-works, showcase, and the media alt text: **Lead /
   Reviewer / Arbiter**; **phase** (a roadmap entry); **cycle** (plan or
   impl; each phase has one of each); **round** (one lead submission +
   one reviewer response); **turn** (whose move it is); **escalation**
   (cycle handed to the arbiter: `ESCALATE`, `NEED_HUMAN`, or 10 stale
   rounds); **brief**; **watcher** (the daemon; *headless* is one of its
   modes); **cockpit** (one project) / **hub** (all projects); **Saloon**
   (the legacy theme). "Handoff" is used only for the loop as a whole and
   the `/handoff` skill.

**README flow (proposed IA).**

```
1. Banner + one line: "Two AIs hand off the work, one human breaks the tie."
2. The loop  — mermaid: roadmap phase → Lead plans → Reviewer reviews (≤10 rounds)
              → approve → Lead implements → Reviewer reviews → approve → next phase;
              Arbiter enters only on escalation.                        [mental model]
3. Why       — three sentences: one AI grading its own homework; long sessions
              drift; the human is the bottleneck unless the loop can run alone.
4. Try it    — pip install; tagteam quickstart; the three terminals; the
              /handoff table (unchanged commands).                    [happy path]
5. One cycle — mermaid stateDiagram: SUBMIT_FOR_REVIEW ⇄ REQUEST_CHANGES,
              APPROVE, ESCALATE / NEED_HUMAN → arbiter (brief → rule),
              round-10 auto-escalation, AMEND.                       [visual hierarchy]
6. Run it unattended — headless in one paragraph + mermaid architecture
              (watcher spawns fresh claude -p / codex exec turns; state + DB;
              cockpit / hub read via SSE); the arbiter-controls table;
              link → how-tagteam-works.md#headless.              [progressive disclosure]
7. Watch and steer — Cockpit (one paragraph + screenshot), Hub (one paragraph
              + screenshot), Saloon (one line); link → how-tagteam-works.md.
8. Reference — Configuration, CLI reference (unchanged), links: how it works,
              showcase, proposal, roadmap, per-phase findings.
```

Deferred to `docs/how-tagteam-works.md` (progressive disclosure): every
paragraph in the coverage ledger marked *→ HTW*. Absorbed by
convention: nothing new to configure — this phase adds no flags.

**Portfolio asset shape** (Jakob's law applied to the portfolio page: same
palette, same slots as `aegis.html` so the future `tagteam.html` reads as
a sibling): hero flow (800×260) → "you choose how much runs by itself"
ladder (800×240) → detail heroes / cards. The ladder is tagteam's honest
analog of Aegis's tiers: **Manual** (paste `/handoff` yourself) →
**Watched** (the watcher drives your terminals) → **Headless** (fresh
processes, unattended, cockpit + hub).

**Escape hatch noted:** the CLI reference at the tail stays a flat list
(reference material is scanned, not read; chunking it further would fight
`tagteam --help`).

## Scope

### In

**A. README** (`README.md`) — restructured per the flow above. Four
mermaid blocks (`flowchart` ×3, `stateDiagram-v2` ×1). Existing commands,
tables, YAML snippets and the security note's *conclusion* remain; deep
prose moves out (ledger below). Length target ≤ 200 lines excluding
mermaid blocks; every current README fact is either still in README or in
`docs/how-tagteam-works.md`.

**B. `docs/how-tagteam-works.md`** — README-linked depth, sectioned to
mirror README (Loop → Cycle → Headless → Controls → Escalations & briefer
→ Cockpit → Hub → Saloon → Data & files), containing verbatim-moved
paragraphs, plus a "Files tagteam writes" table (`handoff-state.json`,
`docs/handoffs/*_rounds.jsonl|_status.json`, `.tagteam/tagteam.db`,
`.tagteam/turns/`, `.tagteam/headless-paused.json`, `.tagteam/watcher.json`,
`docs/escalations/`, `~/.tagteam/projects.json`) — the mental model of
"where is my stuff" that the README never states in one place.

**C. `docs/media/`** — hand-authored SVGs mirroring the README mermaid
diagrams node-for-node (same labels), portfolio-shaped:

| file | viewBox | shows | portfolio slot |
|---|---|---|---|
| `tagteam-loop.svg` | 800×260 | Lead → Reviewer ↔ Arbiter loop over a roadmap | hero (`aegis-flow` slot) |
| `tagteam-modes.svg` | 800×240 | Manual → Watched → Headless ladder | tiers (`aegis-tiers` slot) |
| `tagteam-cycle.svg` | 800×260 | one cycle's state machine, round-10 line | detail hero |
| `tagteam-headless.svg` | 800×260 | 3.0 architecture (watcher, turns, DB, cockpit, hub) | detail hero |
| `screenshots/cockpit-needs-you.png`, `cockpit-usage.png`, `hub.png` | 1280×800 | real captures from this repo's data | screenshot set |
| `README.md` (manifest) | — | file, viewBox, alt text, slot, palette + conventions, how to regenerate screenshots | — |

Conventions (from the Aegis assets, verified in the portfolio repo):
`fill="#1a2332"` panels on transparent background, strokes `#0fbcbf`
(teal, primary) / `#a78bfa` (violet) / `#34d399` (green) / `#f97066`
(red) / `#e8a838` (amber), muted `#8899aa`, text `#e4ecf0`; monospace
labels; `role="img"` + `aria-label` on the root; no external fonts,
images or CSS; each ≤ 20 KB. Screenshots are captured at a fixed
1280×800 viewport from the running cockpit/hub on this repo (steps in
the manifest); PNG, each ≤ 400 KB.

**D. `docs/showcase.md`** — one page for an outside reader: *The problem*
(3 sentences) · *The loop* (`tagteam-loop.svg` inline) · *What one phase
looks like* (a real cycle's round list, abridged) · *The numbers* (table
generated by the script) · *Where it is going* (one paragraph, links).
Numbers section header states the capture date and the exact command.

**E. `scripts/showcase_numbers.py`** — stdlib only; reads every
`*_status.json` + `*_rounds.jsonl` pair under `docs/handoffs/` and
`.tagteam/legacy/` (dedupe by `(phase, type)`, `docs/handoffs/` wins),
prints a markdown table: cycles (plan / impl), outcomes (approved /
escalated / other), rounds-to-approval per type (median, mean, max,
distribution), rounds by action, share of submissions that drew
`REQUEST_CHANGES`, and the round-10 line (how many cycles reached ≥ 8).
Optional `--usage FILE` (the JSON of `tagteam usage --json`, snapshot
committed as `docs/showcase-data/usage-<date>.json`) adds the headless
table: turns, mean duration by role, and the per-cycle round-over-round
input-token curve (r1 → rN) — the "token curve" the proposal asks for.
`--json` for machines. Exit 2 with a message on unreadable input.

**F. Tests** — `tests/test_docs_story.py`:
- every `docs/media/*.svg` parses as XML, root has `viewBox`,
  `role="img"`, non-empty `aria-label`; no `<image>`, `<foreignObject>`,
  `@import`, `url(`, `http` inside; ≤ 20 KB; screenshots ≤ 400 KB.
- README + `docs/how-tagteam-works.md` + `docs/showcase.md` +
  `docs/media/README.md`: fenced blocks balanced; each mermaid block
  starts with a known diagram type; every relative link / image target
  resolves to a file in the repo.
- **CLI coverage:** every `command == "…"` literal in `cli.py:main` (and
  every subcommand listed in the CLI help) appears as `tagteam <cmd>` in
  README ∪ how-tagteam-works.md — an explicit allow-list with reasons
  for anything intentionally undocumented (expected: none; `migrate`,
  `tui`, `state`, `session` all get a line).
- SVG ↔ mermaid consistency: each SVG's `aria-label` names the diagram it
  mirrors, and every `<text>` label in an SVG that is a node name appears
  in the corresponding README mermaid block (a small allow-list for
  purely decorative labels like axis captions) — the cheap guard against
  the two drifting apart.
- `showcase_numbers.py`: unit test on a fixture dir (known counts, an
  escalated cycle, a legacy-only cycle, a duplicate that `docs/handoffs`
  must win) + a smoke run over this repo's tracked files asserting the
  section headers and non-zero cycle count.
- Glossary guard (light): README does not use "review cycle" / "handoff
  session" / "handoff cycle" as terms (grep-level test with the allowed
  phrases enumerated).

**G. Bookkeeping** — `pyproject.toml` → `3.0.0`; roadmap Phase 36 status;
`docs/phases/visual-story-portfolio-feed-findings.md`; the proposal's
"3.0 is an arc name" note gets a "shipped as 3.0.0" line.

### Out

- The portfolio repo (`jblacketter.github.io`) — not touched; the
  manifest tells it what to consume.
- Any package code, CLI, flags, schema, or `tagteam/data/` (SKILL.md,
  templates) changes. Version bump only.
- Animated / interactive diagrams, mermaid rendering pipelines, or
  auto-generating the SVGs from mermaid (hand-authored on purpose: the
  portfolio look is not mermaid's; the consistency test guards drift).
- Rewriting per-phase docs or the proposal beyond the 3.0.0 note.

## Coverage ledger (README today → destination)

| README today | destination |
|---|---|
| banner, one-liner | README §1 (kept) |
| "How it works" bullets + state-file paragraph | README §2 loop diagram + 3 lines; file paragraph → HTW "Files tagteam writes" |
| Quick Start (pip, quickstart, backend auto-detect list) | README §4 (kept verbatim) |
| Running a handoff (`/handoff` table, --roadmap, --confirm) | README §4 table + §5 |
| Other platforms `<details>` ×3 | README §4 (kept, collapsed) |
| Headless: intro, what a turn does, failure/pause, options, per-role YAML, defaults/validation, Windows note | README §6: intro + diagram + one "when something goes wrong" line; **rest → HTW#headless** |
| Arbiter controls block + 7 bullets | README §6: command table; **bullets → HTW#controls** |
| Escalations: briefer YAML + commands + brief semantics | README §5: two lines + `tagteam brief`/`rule` in the table; **YAML + semantics → HTW#escalations** |
| The Cockpit: zones, watcher liveness, buttons/CLI parity, security note | README §7: one paragraph + screenshot + the security *conclusion* (loopback default; `--host 0.0.0.0` is your call); **zones/liveness/security detail → HTW#cockpit** |
| The Hub | README §7: one paragraph + screenshot; **read-only guarantees, hidden rules → HTW#hub** |
| The Saloon | README §7: one line; **→ HTW#saloon** |
| Configuration | README §8 (kept) |
| CLI Reference | README §8 (kept, plus `migrate`, `state diagnose`, `tui` if missing) |
| License | README tail |

## Success criteria

1. README opens with the loop diagram; a reader can name the three roles
   and the two cycles from the first screen without scrolling past
   Quick Start (5-second test — the reviewer judges the rendered
   markdown, not the source).
2. Four mermaid diagrams render on GitHub (checked on the PR); their
   labels match the SVGs (test).
3. `docs/media/` contains the four SVGs + three screenshots + manifest,
   each SVG meeting the Aegis conventions (test) and displaying correctly
   when dropped into `aegis.html`'s `<img>` slots (dogfood: opened
   locally in the portfolio page's markup, screenshot in findings).
4. `docs/showcase.md` numbers are exactly the output of
   `python scripts/showcase_numbers.py --usage docs/showcase-data/usage-<date>.json`
   on the commit that ships (recorded in findings; the test covers the
   script's arithmetic).
5. Coverage: the CLI-coverage and link tests pass; every row of the
   ledger is honored (reviewer spot-checks three deep paragraphs —
   headless `args` validation, retry fingerprint, hub read-only — are in
   HTW verbatim).
6. `pytest` green on macOS + CI (Ubuntu, Windows); the package tree
   `tagteam/` has no diff versus 0.12.0 except nothing (`git diff
   v0.12.0 -- tagteam/` empty; `pyproject.toml` version only).
7. Release 3.0.0 via PR; PyPI shows 3.0.0; `tagteam upgrade` on a
   0.12.0 project changes nothing (no `tagteam/data/` diff).

## Open questions for the reviewer

- **Q1.** Screenshots as PNG (real captures; ~200 KB each, three files)
  vs SVG "vis card" mockups like `aegis-vis-*.svg`. Plan says PNG: the
  proposal asks for a *screenshot set*, and mockups of a real UI would
  age worse than captures with a documented regeneration recipe.
- **Q2.** The glossary guard is grep-level and deliberately narrow (three
  phrases). If you'd rather have no prose-linting test at all, say so —
  it is the one test here that could nag future doc edits.
- **Q3.** `docs/how-tagteam-works.md` as one file (mirrors README
  order, anchors per section) vs splitting per topic. One file: it is the
  README's second half, not a docs site.
