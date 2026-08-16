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
| `tagteam-loop.svg` | 800×260 | ① *The loop* | hero (the `aegis-flow` slot) | The tagteam loop: a roadmap phase goes to the Lead who writes the plan, the Reviewer reviews it and requests changes or approves, the Lead implements, the Reviewer reviews the implementation and approves (next phase) or requests changes; either review can escalate to the Arbiter, whose ruling goes back to the Lead. |
| `tagteam-cycle.svg` | 800×260 | ② *One cycle* | detail hero | One cycle as a state machine: Submitted → Changes (request changes) → Submitted (round N+1); Submitted → Approved; Submitted → Escalated on ESCALATE / NEED_HUMAN or after 10 consecutive stale rounds; the arbiter's ruling approves or hands changes back; AMEND loops on the same round. |
| `tagteam-modes.svg` | 800×240 | ③ *Choose how much runs by itself* | tiers (the `aegis-tiers` slot) | A ladder rising left to right: Manual (you paste /handoff between two agents), Watched (tagteam watch drives your terminals), Headless (each turn is a fresh claude -p / codex exec), + Cockpit & Hub (watch and steer from the browser). |
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
| `screenshots/cockpit-needs-you.png` | 1280×800 | the cockpit on `demo-api`: Now strip (escalated plan cycle, watcher, connection), the **Needs you** card with the decision brief and **Approve / Request changes**, the Watch tabs |
| `screenshots/cockpit-usage.png` | 1280×800 | the cockpit's **Usage** tab: round-over-round token churn, burn by role, the subscription-window signal |
| `screenshots/hub.png` | 1280×800 | the hub over the seed registry: **Needs you** (demo-api, escalated · brief ready · Open), **Waiting** (demo-web, reviewer owed · stale · CLI hint), **Quiet** (demo-docs), burn + window in the strip |

They are captured from **seeded temporary projects, never from a real
project or the live registry**:

```bash
python scripts/showcase_seed.py ~/tagteam-demo          # any dir outside /tmp, /private/tmp, /var/folders (the hub hides those as scratch)
tagteam serve --theme cockpit --dir ~/tagteam-demo/demo/demo-api --port 8080
tagteam hub --registry ~/tagteam-demo/registry.json --port 8090
# open http://127.0.0.1:8080/ and http://127.0.0.1:8090/ in a 1280×800 viewport, wait for the first
# render (connection chip = Live), capture the viewport as PNG (no metadata chunks — the test checks
# IHDR 1280×800 and rejects tEXt/iTXt/zTXt), save under docs/media/screenshots/, then rm -rf ~/tagteam-demo
```

The seed writes three projects with generic names (`demo-api`,
`demo-web`, `demo-docs`) under a parent named `demo`, with short synthetic
round text authored in the seed script, one escalation with a brief, one
owed turn aged past the stale threshold, one approved cycle, a declining
usage series and a subscription-window signal — and a `registry.json`
listing exactly those three, which `tagteam hub --registry` reads instead
of `~/.tagteam/projects.json`.

**Public-safety checklist** (applied before commit; each item recorded in
the phase findings): the page token is a `<meta>` and is never rendered
(searched the DOM text of both pages); no absolute path in the pixels
(hub rows show `name` + `parent/`, the full path is a tooltip only; the
cockpit shows the project name; the seed records the brief's path
project-relative because the Feed prints it); no entry from the live registry (the hub
was started with `--registry` pointing at the seed); no round text from a
real project (all of it lives in `scripts/showcase_seed.py`); no PNG text
metadata (test).
