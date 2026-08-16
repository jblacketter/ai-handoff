# Phase 36 — Visual Story & Portfolio Feed: findings

Plan: `docs/phases/visual-story-portfolio-feed-30-arc.md` (approved round
6; UX flow designed first with the `ux-design-guide` skill). Branch
`phase-36-visual-story`, release **3.0.0** — docs, media, scripts and
tests only; the `tagteam/` package tree is byte-identical to 0.12.0.

## What shipped

- **README** restructured in the reader's order — The loop (① mermaid) →
  Why → Try it → One cycle (② stateDiagram) → Choose how much runs by
  itself (③ ladder) → Headless (④ architecture) → Watch and steer (cockpit
  + hub screenshots) → Reference → More → License. Every deep paragraph
  moved verbatim to **`docs/how-tagteam-works.md`** (anchors `#loop`
  `#cycle` `#modes` `#headless` `#controls` `#escalations` `#cockpit`
  `#hub` `#saloon` `#files`), which also gains a "Files tagteam writes"
  table. The coverage ledger in the plan is honored row by row (the three
  spot-check paragraphs — headless `args` validation, retry fingerprint,
  hub read-only — are in HTW verbatim).
- **`docs/media/`** — `tagteam-loop.svg` (800×260), `tagteam-cycle.svg`
  (800×260), `tagteam-modes.svg` (800×240), `tagteam-headless.svg`
  (800×260): hand-authored in the portfolio's Aegis conventions (dark
  `#1a2332` panels, teal / violet / green / amber / red strokes,
  monospace, `role="img"` + `aria-label`, no external refs, 3–6 KB each);
  node labels marked `class="node"` and matched against the README
  mermaid blocks by test. Three real screenshots (1280×800 PNG, no text
  chunks) from **seeded** projects; `docs/media/README.md` manifest with
  slots, alt text, conventions, the seed/capture recipe and the
  public-safety checklist.
- **`docs/showcase.md`** — problem · loop · modes · a real abridged cycle
  (Phase 35 plan, quoted from the tracked JSONL) · the numbers block
  (generated, byte-compared) · where it is going.
- **`scripts/showcase_numbers.py`** (`report --as-of … [--usage …]`,
  `export-usage --as-of …`), **`scripts/showcase_seed.py DIR`**,
  **`scripts/upgrade_smoke.py`** (`--project --sentinel --python
  --expect-version --json`).
- **Tests:** `tests/test_docs_story.py` (21), `tests/test_showcase_numbers.py`
  (7), `tests/test_upgrade_smoke.py` (5). Full suite: **932 passed, 5
  skipped**.
- Bookkeeping: `pyproject.toml` and `CITATION.cff` → 3.0.0; roadmap;
  proposal §5 "shipped as 3.0.0" note.

## The numbers block (as of 2026-08-15 UTC)

26 cycles (13 plan / 13 impl), all approved, none escalated; rounds to
approval plan median 3 (max 7) / impl median 2 (max 4); 65 lead
submissions, 39 request-changes (60% pushback), 26 approvals; longest
stale streak 0 against the 10-consecutive-stale-rounds limit; 22 reviewer
(Codex) headless turns + 1 lead (Claude) turn, 1 cancelled attempt; the
per-cycle input-token curves (one provider each).

**Why 2026-08-15 and not 2026-08-16.** As-of is an inclusive UTC day. This
phase's own impl cycle writes rounds dated 2026-08-16 UTC, so pinning
2026-08-16 would make the committed block go stale on the very `cycle
init` that submits it (the byte-compare test would fail in CI mid-review).
2026-08-15 is the latest day that is stable through this review; it
excludes the four cycles closed on 2026-08-16 UTC (Phase 34 plan/impl,
Phase 35 plan/impl) and this phase's plan cycle. The block
header states the date; regenerating later with a later date is one
command.

## Deviations / notes for the reviewer

- **README length**: 238 lines excluding the mermaid blocks against the
  plan's ≤ 200 target. The overage is the reference tail (CLI reference,
  three `<details>` platform blocks) which the ledger keeps verbatim; the
  narrative part (top through "Watch and steer") is 222 lines including
  the four mermaid blocks (~50 lines). Left as is rather than cutting reference material.
- **Cockpit UI wording (round 2, reviewer)**: the shipped Phase 34 Usage
  chart labelled its marker "r10 auto-escalate" and its caption said "the
  round-10 line is auto-escalation" — the false rule this phase removes
  from the docs. Round 1 left it untouched to honor the zero-diff
  `tagteam/` contract; the reviewer ruled a knowingly false portfolio
  visual cannot ship, so it is corrected as a **disclosed, text-only
  package change**: `tagteam/data/web/cockpit.html` (caption → "dashed
  line: the stale-round limit (auto-escalation after 10 consecutive stale
  rounds; a progressing cycle can go past it)") and `cockpit.js` (marker
  label → "10-stale-round limit", comment). `git diff origin/main --
  tagteam/` is exactly those two files (6+/4−); the wheel comparison
  against 0.12.0 differs in exactly those two entries; no behavior change;
  `tagteam/data/web` is not among the files `setup`/`upgrade` copy, so the
  upgrade no-op is unaffected. `cockpit-usage.png` recaptured from the
  seed with the new wording (visible in the caption and marker); a test
  forbids the old wording in both files.
- **Test module split**: the harness tests live in
  `tests/test_upgrade_smoke.py` (the plan named
  `tests/test_docs_story.py::test_upgrade_smoke_isolated`); the assertions
  are the plan's, the venv/subprocess machinery just did not belong in the
  docs module. The stub package for the shadowing test is copied into the
  temporary venv's `purelib` (venv created `--without-pip`, no network,
  same on Windows via `Scripts\python.exe`) instead of `pip install`ing a
  built sdist — equivalent for the shadowing proof, and CI-safe.
- **Seed detail**: the real briefer records a brief's absolute path and
  the cockpit Feed prints it; the seed records it project-relative so the
  screenshot carries no absolute path (checked in the DOM: no `/Users/`,
  `/home/` or `C:\` in the visible text of either page).
- **Playwright for capture**: the Chrome MCP returns scaled JPEGs, so the
  1280×800 PNGs were captured with the Playwright MCP (`setViewportSize`
  1280×800, `page.screenshot` PNG). The manifest recipe stays
  tool-neutral (any 1280×800 viewport capture).
- **Windows note for `upgrade_smoke.py`**: `Path.is_relative_to` and
  `os.path.samefile` behave the same; the harness compares resolved paths
  and the tests run in CI on `windows-latest`.

## Round 2 (reviewer impl r1)

1. **Canonical precedence before the as-of filter** —
   `load_cycles` now reserves a `(phase, type)` key for the first directory
   (`docs/handoffs/`) that has a status file, *before* filtering, so a
   canonical cycle whose entries are all after the cutoff is excluded and
   the older `.tagteam/legacy/` duplicate can never resurface. Regression
   case added to the fixture (canonical `dup/plan` dated 2026-05-09,
   legacy duplicate approved 2026-05-01, as-of 2026-05-08 → the cycle is
   absent). This repo has no such duplicates, so the committed block is
   unchanged (byte-compare still green).
2. **Cockpit wording** — see the deviation entry above; screenshot
   recaptured; guard test added.
3. **Loop diagram ruling routing** — the mermaid and `tagteam-loop.svg` no
   longer send a generic "ruling" back to the Lead: `A → P` "request
   changes (plan)", `A → I` "approve (plan) / request changes (impl)",
   `A → R` "approve (impl)"; README role bullet, SVG `aria-label`, manifest
   and showcase alt texts say the ruling takes the reviewer's seat.
   `test_loop_diagram_routes_the_arbiters_ruling_correctly` parses the
   README block's edges (both escalations, the three ruling outcomes with
   their labels, no generic "ruling" edge) and the SVG's `class="edge"`
   labels (also checked ⊆ the mermaid text). Re-rendered:
   `docs/phases/media/phase-36-loop-render-r2.jpg`.

## In-cycle gates (all local, none needs a PR / tag / PyPI)

1. **5-second test** — the README's first screen (before Quick Start) is
   the loop diagram plus three role bullets naming Lead / Reviewer /
   Arbiter and the plan/impl cycles (`test_readme_opens_with_the_loop_and_names_the_roles_first`).
2. **Mermaid renders** — all four blocks rendered with mermaid@11 in a
   scratch page in Chrome, zero syntax errors (`data-processed=true` ×4):
   `docs/phases/media/phase-36-mermaid-render-1.jpg` (① ②),
   `phase-36-mermaid-render-2.jpg` (③ ④, plus the loop SVG on the dark
   background). Fence/type/node lint in the test; SVG node labels ⊆
   matching mermaid block (test).
3. **SVGs in the portfolio slots** — a local copy of the portfolio's
   `aegis.html` + css with the four SVGs swapped into the flow / tiers /
   two vis slots, all four loaded (`naturalWidth > 0`, 900 px hero width):
   `docs/phases/media/phase-36-portfolio-dropin.jpg`. Screenshot
   public-safety checklist, item by item: token is a `<meta>` and its
   64-char value does not occur in either page's visible text (DOM
   search); no absolute path in visible text (DOM regex, both pages, after
   the seed change above); hub started with `--registry` on the seed file
   ("3 registered": demo-api / demo-web / demo-docs only); all round text
   is authored in `scripts/showcase_seed.py`; PNGs are 1280×800 with no
   `tEXt`/`iTXt`/`zTXt` chunks (test).
4. **Numbers block == script output** — `test_showcase_numbers_block_matches_script_output`
   byte-compares; `docs/showcase-data/usage-2026-08-15.json` passes the
   schema / no-path / banned-key test.
5. **Coverage** — every `command == "…"` in `cli.py:main` appears as
   `tagteam <cmd>` in README ∪ HTW (`migrate`, `tui`, `upgrade`, `state`,
   `session` included); link targets and HTW anchors resolve; glossary
   guard passes (README contains "10 consecutive stale rounds"; no
   "10 rounds" / "round-10" / "review cycle" / "handoff session" /
   "handoff cycle" in the story docs or SVG labels).
6. **Release readiness** —
   - `pytest`: 932 passed, 5 skipped (macOS, 2 m 37 s; round 2).
   - `git diff origin/main -- tagteam/`: round 1 **0 lines**; after the
     round-2 wording fix exactly `tagteam/data/web/cockpit.html` (1 line)
     and `cockpit.js` (marker label + comment) — nothing else (origin/main
     = 688daa8 = v0.12.0).
   - `pip wheel . --no-deps` → `tagteam-3.0.0-py3-none-any.whl`,
     `METADATA` `Version: 3.0.0`; the wheel's 86 `tagteam/` entries have
     the **same file list** as the `tagteam-0.12.0-py3-none-any.whl`
     downloaded from PyPI and the same sha256 hashes for 84 of them — the
     two differing entries are exactly `tagteam/data/web/cockpit.html` and
     `cockpit.js` (the round-2 wording fix).
   - Upgrade smoke, only through the harness: a scratch venv with the
     0.12.0 wheel; 0.12.0's `setup.main` run in a fresh `-I` process with
     `registry.REGISTRY_DIR/REGISTRY_FILE` patched to a scratch registry
     (fail-closed assertion before the call; the scratch registry then
     listed exactly that project); then
     `scripts/upgrade_smoke.py --project <it> --sentinel <unrelated>` from
     the source checkout → helper identity = `.venv/bin/python`, project
     diff **none**, sentinel unchanged and unnamed, temp registry still one
     entry, real `~/.tagteam/projects.json` byte-identical (also checked
     by hand with `cmp` before/after), exit 0. Installed-wheel mode dry
     run of the post-approval recipe with the 0.12.0 venv as stand-in:
     `--python <venv>/bin/python --expect-version 0.12.0` → identity line
     names the venv's executable/prefix and its `site-packages/tagteam/`,
     no-op, exit 0; `--expect-version 3.0.0` against the same venv → exit
     2 "imported tagteam version '0.12.0' != expected '3.0.0'", no `go`.
   - Evidence that the `-I` + external-cwd rule matters: the same 0.12.0
     venv, launched *without* `-I` from the checkout cwd, imported the
     checkout's `tagteam` (reported 3.0.0). The harness never does that.

## Post-approval checklist (not review gates)

Ask before push → PR from `phase-36-visual-story` → CI green (Ubuntu +
Windows) → check the four mermaid blocks render in the PR's README view →
arbiter merges + tags `v3.0.0` → publish workflow → PyPI 3.0.0 →
`pip install tagteam==3.0.0` in a scratch venv and
`python scripts/upgrade_smoke.py --python <venv>/bin/python --expect-version 3.0.0 --project <0.12.0-set-up dir> --sentinel <unrelated>`
(never bare `tagteam upgrade`). Results go in the PR / release notes and
the roadmap status line.
