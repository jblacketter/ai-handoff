# docs/media — diagrams and screenshots

Standalone assets for the [README](../../README.md)'s visual story, shaped
for the portfolio site's featured-app pages (same palette, sizes and
markup conventions as its `aegis-flow.svg` / `aegis-tiers.svg`), so a
future `tagteam.html` can drop them into its `<img>` slots without edits. The four
SVGs mirror the four README mermaid blocks node-for-node; a test
(`tests/test_docs_story.py`) keeps them from drifting apart.

## Diagrams

| file | viewBox | mirrors README block | portfolio slot | alt text |
|---|---|---|---|---|
| `tagteam-loop.svg` | 800×260 | ① *The loop* | hero (the `aegis-flow` slot) | The tagteam loop: a roadmap phase goes to the Lead who writes the plan, the Reviewer reviews it and requests changes or approves, the Lead implements, the Reviewer reviews the implementation and approves (next phase) or requests changes; either review can escalate to the Arbiter, whose ruling takes the reviewer's seat: request changes hands the turn back to the Lead, approving the plan sends it to implementation, approving the implementation advances the roadmap. |
| `tagteam-cycle.svg` | 800×260 | ② *One cycle* | detail hero | One cycle as a state machine: Submitted → Changes (request changes) → Submitted (round N+1); Submitted → Approved; Submitted → Escalated on ESCALATE / NEED_HUMAN or after 10 consecutive stale rounds; the arbiter's ruling approves or hands changes back; AMEND loops on the same round. |
| `tagteam-modes.svg` | 800×240 | ③ *Choose how much runs by itself* | tiers (the `aegis-tiers` slot) | A ladder rising left to right: Manual (you paste /handoff between two agents), Watched (tagteam watch drives your terminals), Headless (each turn is a fresh claude -p / codex exec), + Cockpit & Hub (talk to the lead, launch, watch and steer). |
| `tagteam-headless.svg` | 800×260 | ④ *Headless: fresh process per turn* | detail hero | The 3.0 architecture: watcher → fresh turn process → files/DB → turn flip back; the cockpit reads over SSE, the hub mounts one cockpit per project, the arbiter pauses / interjects / rules through the cockpit, which runs the same CLI commands. |

**Conventions** (checked by the test): root `<svg xmlns=…>` with `viewBox`,
`role="img"` and a non-empty `aria-label`; no `<image>`, `<foreignObject>`,
`<script>`, `@import`, `xlink:href`, external `url()` or any other external reference (fonts included; `url(#marker)` is fine);
≤ 20 KB each. Palette: panels `#1a2332` on a transparent background;
strokes teal `#0fbcbf` (primary), violet `#a78bfa` (reviewer / hub),
green `#34d399` (approve / turn process), amber `#e8a838` (changes), red
`#f97066` (arbiter / escalation); muted `#8899aa`; text `#e4ecf0`;
monospace labels. Every node label is a `<text class="node">` whose text
(with `<tspan>` lines joined by spaces) is a substring of the matching
README mermaid block — that is the drift contract; decorative captions and
edge labels are plain `<text>`.

## Screenshots

| file | size | shows |
|---|---|---|
| `screenshots/cockpit-needs-you.png` | 1280×800 | the cockpit on `demo-api` (3.8 lanes): the project-first strip (`demo-api` · `rate-limit-middleware — plan, round 3 — escalated to you` · `waiting on you` · `watcher: off · Start`), the red **Needs you** banner with the escalation, its decision brief and **Request changes / Approve**, the two lanes below both "waiting on you" |
| `screenshots/cockpit-usage.png` | 1280×800 | the cockpit's **Usage** tab: round-over-round token churn, burn by role, the subscription-window signal |
| `screenshots/hub.png` | 1280×800 | the hub over the seed registry: **Needs you** (demo-api, escalated · brief ready · Open), **Waiting** (demo-web, reviewer owed · stale · CLI hint), **Quiet** (demo-docs), burn + window in the strip |
| `screenshots/cockpit-lead.png` | 1280×800 | (3.8 lanes) the cockpit on the idle `demo-idle`: the **Start** card (`Next: csv-export — plan`, what Start will do, `/handoff start csv-export`, Copy command, one **Start**), the strip's `idle · last: Claude's chat #2 — done` and `watcher: off · Start`, then the lanes — **Claude — lead** with the two-message chat (kept activity under disclosures) and the composer, **Codex — reviewer** empty ("No reviews yet…") |
| `screenshots/cockpit-cycle.png` | 1280×800 | (3.8 lanes) the cockpit on `demo-web` during a running handoff: the strip (`demo-web` · `checkout-validation — implementation, round 2` · `Codex is working · review, round 2` · `watcher: on`), the quiet Needs-you line, the two lanes — **Claude — lead** waiting with its two finished implementation turns and "Codex is working on its review — streaming in the reviewer lane · watch it", **Codex — reviewer** (pulsing) with round 1's review `changes requested`, the `passed` pre-check and round 2's review `working`, streaming — and the Rounds tab |

They are captured from **seeded temporary projects, never from a real
project or the live registry**:

```bash
python scripts/showcase_seed.py ~/tagteam-demo          # any dir outside /tmp, /private/tmp, /var/folders (the hub hides those as scratch)
tagteam serve --dir ~/tagteam-demo/demo/demo-api --port 8080     # cockpit-needs-you.png / cockpit-usage.png (bare serve = cockpit since 3.1)
tagteam serve --dir ~/tagteam-demo/demo/demo-idle --port 8081    # cockpit-lead.png (Lead tab; the seed installs the skill contract so Start headless is offered)
tagteam serve --dir ~/tagteam-demo/demo/demo-web --port 8082     # cockpit-cycle.png (3.7: the seed leaves a RUNNING reviewer turn here — a detached `sleep` as the live pid, its log growing for a minute; kill the printed pid when done)
tagteam hub --registry ~/tagteam-demo/registry.json --port 8090
# open http://127.0.0.1:8080/ and http://127.0.0.1:8090/ in a 1280×800 viewport, wait for the first
# render (connection chip = Live), capture the viewport as PNG (no metadata chunks — the test checks
# IHDR 1280×800 and rejects tEXt/iTXt/zTXt), save under docs/media/screenshots/, then rm -rf ~/tagteam-demo
```

The seed writes four projects with generic names (`demo-api`,
`demo-web`, `demo-docs`, `demo-idle`) under a parent named `demo`, with short synthetic
round text authored in the seed script, one escalation with a brief, one
owed turn aged past the stale threshold, one approved cycle, a declining
usage series and a subscription-window signal — plus (3.1) `demo-idle`
with an open roadmap phase and a canned two-turn Lead conversation — and a
`registry.json` listing exactly those four, which `tagteam hub --registry`
reads instead of `~/.tagteam/projects.json`.

**Public-safety checklist** (applied before commit; each item recorded in
the phase findings): the page token is a `<meta>` and is never rendered
(searched the DOM text of both pages); no absolute path in the pixels
(hub rows show `name` + `parent/`, the full path is a tooltip only; the
cockpit shows the project name; the seed records the brief's path
project-relative because the Feed prints it); no entry from the live registry (the hub
was started with `--registry` pointing at the seed); no round text from a
real project (all of it lives in `scripts/showcase_seed.py`); no PNG text
metadata (test).
