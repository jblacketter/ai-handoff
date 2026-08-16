# Phase 36: Visual Story & Portfolio Feed (3.0 arc)

## Status
- [x] Planning
- [x] In Review (round 2: public-safe evidence + screenshot pipeline, 1:1 diagram contract, stale-round wording, in-cycle release gate vs post-approval checklist, numbers methodology + byte-compare guard; round 3: registry-isolated upgrade smoke, `--as-of` + `attempt` in the snapshot contract, stale streak = equal transitions; round 4: sentinel outside every registry, no `HOME` override, fail-closed registry-path assertion, existence+hash snapshots; round 5: post-approval installed-wheel check goes through the harness with `--python`, helper reports imported tagteam version/path)
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
   (cycle handed to the arbiter: `ESCALATE`, `NEED_HUMAN`, or
   auto-escalation after **10 consecutive stale rounds** — the lead
   re-submitting without progress; a cycle that *is* progressing may run
   past round 10); **brief**; **watcher** (the daemon; *headless* is one of its
   modes); **cockpit** (one project) / **hub** (all projects); **Saloon**
   (the legacy theme). "Handoff" is used only for the loop as a whole and
   the `/handoff` skill.

**README flow (proposed IA).**

```
1. Banner + one line: "Two AIs hand off the work, one human breaks the tie."
2. The loop  — mermaid ①: roadmap phase → Lead plans → Reviewer reviews (rounds)
              → approve → Lead implements → Reviewer reviews → approve → next phase;
              Arbiter enters only on escalation.                        [mental model]
3. Why       — three sentences: one AI grading its own homework; long sessions
              drift; the human is the bottleneck unless the loop can run alone.
4. Try it    — pip install; tagteam quickstart; the three terminals; the
              /handoff table (unchanged commands).                    [happy path]
5. One cycle — mermaid ② stateDiagram: SUBMIT_FOR_REVIEW ⇄ REQUEST_CHANGES,
              APPROVE, ESCALATE / NEED_HUMAN → arbiter (brief → rule),
              "10 consecutive stale rounds → auto-escalate", AMEND.  [visual hierarchy]
6. Choose how much runs by itself — mermaid ③ ladder: Manual (paste /handoff
              yourself) → Watched (the watcher drives your terminals) → Headless
              (fresh processes, unattended); then the headless subsection: one
              paragraph + mermaid ④ architecture (watcher spawns fresh claude -p /
              codex exec turns; state + DB; cockpit / hub read via SSE); the
              arbiter-controls table; link → how-tagteam-works.md#headless.
                                                              [progressive disclosure]
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
mermaid blocks, each mapped 1:1 to a README section and an SVG (contract
table under C). Existing commands,
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
diagrams node-for-node (same labels), portfolio-shaped. **Diagram
contract (1:1):**

| # | README section | mermaid type | SVG | viewBox | portfolio slot |
|---|---|---|---|---|---|
| ① | §2 The loop | `flowchart LR` | `tagteam-loop.svg` | 800×260 | hero (`aegis-flow` slot) |
| ② | §5 One cycle | `stateDiagram-v2` | `tagteam-cycle.svg` | 800×260 | detail hero |
| ③ | §6 Choose how much runs by itself | `flowchart LR` | `tagteam-modes.svg` | 800×240 | tiers (`aegis-tiers` slot) |
| ④ | §6 Headless (subsection) | `flowchart LR` | `tagteam-headless.svg` | 800×260 | detail hero |

Plus `screenshots/cockpit-needs-you.png`, `cockpit-usage.png`, `hub.png`
(1280×800) and `README.md` (manifest: file, viewBox, alt text, slot,
palette + conventions, the exact seed / start / capture recipe, and the
public-safety checklist).

**Screenshots are captured from seeded temporary projects, never from
the live registry or this repo's working tree.** `scripts/showcase_seed.py
DIR` (dev script; may import `tagteam`) builds a disposable
`DIR/registry.json` and three projects with generic names — `demo-api`
(escalated cycle with a brief on disk → *Needs you*), `demo-web` (turn
owed to the reviewer, no watcher → *Waiting · stale*), `demo-docs`
(approved → *Quiet*) — each with `tagteam.yaml`, `handoff-state.json`,
`docs/handoffs/*_rounds.jsonl|_status.json` holding short synthetic
round text authored in the script, and a `usage` table seeded with a
declining per-round token series so the cockpit's Usage tab has a curve.
Start: `tagteam serve --theme cockpit --dir DIR/demo-api --port 8080` and
`tagteam hub --registry DIR/registry.json --port 8090`; capture at a
1280×800 viewport (`?nosse=1` is not needed; capture after the first
render). Public-safety checklist, applied before commit and recorded in
findings: no auth token visible (it lives in a `<meta>`, never rendered
— verified by searching the DOM text), no absolute path in pixels (rows
show `name` + `parent/`; the hub's full path is a `title` tooltip only —
the seed uses a `parent` dir named `demo`), no live registry entry (the
hub is started with `--registry` pointing at the seed), no unreviewed
cycle content (all round text is authored in the seed script and lives
in the repo). PNGs carry no metadata: a stdlib chunk walker in the test
rejects `tEXt` / `iTXt` / `zTXt` chunks and asserts a 1280×800 IHDR
(strip with `python -c` over the chunk list if a capture tool adds any).

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
looks like* (a real cycle's round list from this repo's tracked
`docs/handoffs/`, abridged) · *The numbers* — a block delimited by
`<!-- showcase-numbers:begin as-of=YYYY-MM-DD -->` /
`<!-- showcase-numbers:end -->` whose content is **exactly** the script's
output (test byte-compares; see F) · *Where it is going* (one paragraph,
links). The block header states the as-of date and the exact command.

**E. `scripts/showcase_numbers.py`** — stdlib only, two subcommands.

*`report --as-of DATE [--usage FILE] [--json]`* reads every
`*_status.json` + `*_rounds.jsonl` pair under `docs/handoffs/` and
`.tagteam/legacy/` and prints the numbers block. Methodology (stated in
the block's footnotes and enforced by the fixture test):

- **Cycle** = one `(phase, type)` pair; duplicates resolved in favor of
  `docs/handoffs/`. **As-of filter:** `--as-of YYYY-MM-DD` is an
  inclusive UTC calendar day — the cutoff is `ts < (DATE + 1 day)
  00:00:00Z`; entries at or after the cutoff are dropped and a cycle
  with no entry before the cutoff is excluded (a legacy entry without
  `ts` uses the status file's `date` at 00:00Z). The block is therefore
  stable while later phases keep writing rounds. The **same cutoff
  function** is applied to usage turns in `export-usage` (below), and
  `report` refuses (`exit 2`) a usage file whose embedded `as_of` differs
  from its own `--as-of`.
- **Outcome** from the (filtered) entries: last entry `APPROVE` →
  *approved*; last entry `ESCALATE` / `NEED_HUMAN` → *escalated*; last
  entry a lead submission or `REQUEST_CHANGES` → *in progress* (counted,
  excluded from rounds-to-approval); status `aborted` → *aborted*.
- **Round** = one lead `SUBMIT_FOR_REVIEW` entry. **Rounds-to-approval**
  = the round number of the `APPROVE` entry. `AMEND` entries and arbiter
  rulings (reviewer-role entries whose content starts with
  `[ARBITER RULING by`) are counted in their own rows and are neither
  rounds nor reviewer responses; a ruling `APPROVE` still closes the
  cycle as *approved (by ruling)*.
- **Pushback rate** = `REQUEST_CHANGES` entries ÷ `SUBMIT_FOR_REVIEW`
  entries (a submission draws at most one response). **First-round
  approvals** = cycles approved at round 1.
- **Stale streaks**: for each cycle, the number of consecutive
  *unchanged re-submissions* — equal adjacent transitions between
  successive lead `SUBMIT_FOR_REVIEW` contents, counted with the exact
  production algorithm (`cycle._count_stale_rounds`: the first submission
  is the baseline and is never itself stale, so 11 identical submissions
  = a streak of **10**; the script's function is a copy of that loop and
  the fixture expectation is computed by that loop). Reported as the max
  streak observed against the limit "10 consecutive stale rounds →
  auto-escalation"; the showcase footnote states the baseline rule in one
  sentence. No "round 10" line: total rounds is reported as a
  distribution + max only.
- **Usage** (only with `--usage`, from the sanitized snapshot): turns by
  role and provider; failed / cancelled / timed-out attempts counted by
  outcome and **excluded** from duration and token statistics; where a
  `(cycle, round, role)` has several `ok` rows (a retry), the row with
  the **highest `attempt`** is used for the curve and the extras are
  reported as retries (`attempt` is assigned deterministically at export
  — see below — because `id`/`ts` are stripped); null token
  fields are excluded from sums and reported as "unknown". **The
  round-over-round curve is per (cycle, role, provider) series only** and
  is labeled *descriptive*: input-token accounting differs by provider
  (Codex reports cache reads inside `input_tokens`; Claude reports them
  separately) — the block never places two providers in one series and
  never states a cross-provider efficiency claim. Cost is shown only
  where the provider priced the turn (`n priced / total`).

*`export-usage --as-of YYYY-MM-DD < usage.json > docs/showcase-data/usage-<date>.json`*
is the deterministic sanitizer: reads `tagteam usage --json` on stdin,
**first** drops raw turns whose `ts` is at or after the as-of cutoff
(same inclusive-UTC-day function as `report`), **then** assigns each
surviving turn an `attempt` ordinal — 1, 2, … within its `(phase, type,
round, role)` group in `(ts, id)` order — and only then strips the
sensitive fields. Output: `{"schema": "showcase-usage/1", "as_of": DATE,
"turns": [...]}` where each turn has **only** `phase, type, round, role,
attempt, provider, status, duration_ms, input_tokens, output_tokens,
cache_read_tokens, cache_write_tokens, cost_usd` — no `id`, `ts`,
`agent`, `model`, `exit_code`, `num_turns`, `session_id`, `log_path`;
turns sorted by `(phase, type, round, role, attempt)`, `sort_keys=True`,
so re-exporting after later turns have been recorded is byte-identical
for the same `--as-of`. Exit 2 with a message on unreadable input or a
missing `--as-of`.

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
- `showcase_numbers.py report`: unit test on a fixture dir (known
  counts; an escalated cycle; an in-progress cycle; a cycle approved by
  ruling; an `AMEND`; a legacy-only cycle; a duplicate that
  `docs/handoffs` must win; entries after as-of dropped; a stale streak
  of 3; usage fixture with a failed attempt, a retry, a null token field
  and both providers — asserting the failed/retry/null rules and that no
  series mixes providers; the stale-streak expectation is produced by the
  copied production loop on 3 identical + 1 changed submissions → 2, and
  on 11 identical → 10).
- `export-usage`: fixture with duplicate `(cycle, round, role)` attempts
  in shuffled input order plus unrelated rows interleaved → `attempt`
  ordinals follow `(ts, id)`, output sorted and byte-identical across two
  runs and across an input that has extra turns after the cutoff; a turn
  exactly at `DATE+1 00:00:00Z` is excluded, one at `DATE 23:59:59Z`
  included; `report --as-of` with a snapshot whose `as_of` differs →
  exit 2.
- **Byte-compare guard:** the test runs `report --as-of <date from the
  block header> --usage docs/showcase-data/usage-<date>.json` over the
  repo's tracked files and asserts the output equals the block in
  `docs/showcase.md` byte for byte.
- **Snapshot safety:** every `docs/showcase-data/*.json` has exactly the
  allowed schema (allowed keys incl. `attempt`, no extras), no string value that looks
  like an absolute path (`^/`, `^[A-Za-z]:\\`, `~/`), and no
  `session_id` / `log_path` / `agent` / `model` / `ts` key anywhere
  (recursive walk); `export-usage` on a fixture with those fields drops
  them and is byte-stable across two runs.
- **PNG safety:** each `docs/media/screenshots/*.png` has IHDR 1280×800
  and no `tEXt` / `iTXt` / `zTXt` chunk (stdlib chunk walker); ≤ 400 KB.
- Glossary guard (narrow): README, HTW, showcase and the SVG `<text>`
  labels do not contain `\b10 rounds\b`, `round[- ]10`, "review cycle",
  "handoff session" or "handoff cycle"; README contains the phrase
  "10 consecutive stale rounds" at least once.

**G. Bookkeeping** — `pyproject.toml` → `3.0.0` and `CITATION.cff`
`version` / `date-released` to match (added on this branch by the
arbiter, 139e099); roadmap Phase 36 status;
`docs/phases/visual-story-portfolio-feed-findings.md`; the proposal's
"3.0 is an arc name" note gets a "shipped as 3.0.0" line; the README
license / link-back paragraph (139e099) is kept verbatim in §8.

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

## Success criteria — in-cycle gates (what the impl review judges)

1. README opens with the loop diagram; a reader can name the three roles
   and the two cycles from the first screen without scrolling past
   Quick Start (5-second test — the reviewer judges the rendered
   markdown, not the source).
2. The four mermaid blocks pass the local checks (fence / type / node
   linter in the test) **and** render: rendered locally with mermaid.js
   in a scratch page in Chrome (no push needed), screenshots of all four
   in findings; labels match the SVGs (test).
3. `docs/media/` contains the four SVGs + three screenshots + manifest;
   each SVG meets the Aegis conventions (test) and displays correctly
   when dropped into `aegis.html`'s `<img>` slots (dogfood: opened
   locally in a copy of the portfolio page's markup, screenshot in
   findings); screenshots come from the seed script and pass the
   public-safety checklist (findings records each item).
4. `docs/showcase.md` numbers block == script output (byte-compare test);
   the committed usage snapshot passes the safety test.
5. Coverage: CLI-coverage, link and glossary tests pass; every row of the
   ledger is honored (reviewer spot-checks three deep paragraphs —
   headless `args` validation, retry fingerprint, hub read-only — are in
   HTW verbatim).
6. **Release-readiness gate, all local:** `pytest` green (macOS; CI runs
   on the PR after approval); `git diff origin/main -- tagteam/` empty
   (origin/main == v0.12.0 at branch time; re-checked at submission);
   `pip wheel . --no-deps` builds, `METADATA` says `Version: 3.0.0`, and
   the wheel's `tagteam/` file list + hashes equal the 0.12.0 wheel's
   (downloaded from PyPI into the scratchpad, compared in findings);
   `tagteam upgrade` from the source install on a disposable project set
   up by 0.12.0 changes no file — run **only** through the isolated
   harness `scripts/upgrade_smoke.py` (below), never bare `tagteam
   upgrade` (which reads `~/.tagteam/projects.json`, prunes it, and runs
   setup over every real registered project). Findings record the
   harness output: checksums of the disposable project before/after
   (equal), checksum of the real `~/.tagteam/projects.json` before/after
   (equal), and checksums of every framework file `setup` manages in
   every real registered project before/after (equal). None of these
   needs a PR, a tag, or PyPI.

**`scripts/upgrade_smoke.py --project DIR [--sentinel DIR] [--python EXE]`**
— the isolated harness, and the **only** permitted way to run an upgrade
in any recipe of this phase; **bare `tagteam upgrade` is forbidden in
both the in-cycle gate and the post-approval checklist.** `--python`
selects the interpreter for the helper process (default:
`sys.executable`); the helper prints, before anything else, the imported
`tagteam.__version__` and `tagteam.__file__`, and the parent echoes them
and refuses to proceed (exit 2) unless — when `--expect-version` is given
— the reported version matches and the reported path is under the
`--python` interpreter's `sys.prefix` (so a post-release check cannot
silently exercise the source checkout). `HOME` is never overridden (workspace rule; and it is
unnecessary). In the parent: snapshot the real registry file
(`registry.read_registry_raw()`, non-mutating) and, for every real
registered project, the **existence and sha256** of every managed
destination `setup` writes (the framework file list from
`tagteam.setup`), plus the same existence+hash snapshot of `--project`
and of `--sentinel` (an unrelated dir that is listed in **no** registry
— not the real one, not the temporary one). Create a temporary registry
directory whose `projects.json` lists **exactly one** entry:
`--project`. Spawn a **fresh helper process** (`sys.executable -c …`)
that imports `tagteam.registry`, sets the module globals `REGISTRY_DIR`
and `REGISTRY_FILE` to the explicit temporary paths, **asserts
fail-closed immediately before the call** that both resolved globals
are descendants of the expected temporary directory (`Path.resolve()`,
`is_relative_to`), and only then calls `tagteam.cli.upgrade_command()`.
Back in the parent: re-snapshot everything and fail loudly if the real
registry bytes, or any real project's existence+hash set, or the
sentinel's existence+hash set changed, or if the helper's stdout names
any path other than `--project`; report the disposable project's
before/after diff (empty = no-op) and that the temporary registry still
lists exactly one entry. The real registry is never replaced, edited or
copied over.

Recipe for the 3.0.0 gate: `pip install tagteam==0.12.0` in a scratch
venv → run the 0.12.0 `setup` **the same way** (a fresh process from
that venv: import `tagteam.registry`, patch `REGISTRY_DIR` /
`REGISTRY_FILE` to a scratch registry, assert the same fail-closed
check, then call the 0.12.0 setup function on a scratch project dir — so
0.12.0's own registry write lands in the scratch registry, not the real
one) → source-install `python scripts/upgrade_smoke.py --project <dir>
--sentinel <unrelated dir>`.

Regression test (`tests/test_docs_story.py::test_upgrade_smoke_isolated`):
a source-`setup` disposable project (set up with the registry globals
patched the same way, in-process via monkeypatch) + an unrelated sentinel
dir containing one file; run the harness; assert the sentinel's
existence+hash set is identical before/after and its path is absent from
the helper's output, the temporary registry lists exactly **one** entry
afterwards, and the real registry file — if one exists on the machine —
is byte-identical before/after (checked by the test itself, independent
of the harness's own check). A second test proves the interpreter
contract: run the harness with `--python sys.executable` and assert the
echoed version equals `tagteam.__version__` and the echoed path is the
source checkout's `tagteam/__init__.py`; run it with `--python` pointing
at a tiny fake interpreter script (a shim that prints a different
version/path in the helper protocol) plus `--expect-version 3.0.0` and
assert exit 2 with the mismatch named — the harness selects the target
interpreter and refuses a wrong one.

## Post-approval checklist (release operations, not review gates)

Ask before push; then: PR from `phase-36-visual-story` → CI green
(Ubuntu + Windows) → confirm the four mermaid blocks render on GitHub in
the PR's README view → arbiter merges + tags `v3.0.0` → publish workflow
green → PyPI shows 3.0.0 → installed-wheel verification **through the
harness only**: `pip install tagteam==3.0.0` in a scratch venv, then from
the source checkout `python scripts/upgrade_smoke.py --python
<scratch-venv>/bin/python --expect-version 3.0.0 --project <the
0.12.0-set-up disposable dir> --sentinel <unrelated dir>` — the helper
imports the published wheel (version/path echoed and checked), the
temporary registry holds only the disposable project, the fail-closed
registry-global assertions run before the call, and the sentinel / real
registry / managed-destination existence+hash checks apply exactly as in
the in-cycle gate. Bare `tagteam upgrade` is never run. Results are
recorded in the PR description / release notes and the roadmap status
line; reviewer approval never depends on them.

## Resolved questions (round 1 → 2)

- **Q1** PNG captures — from the seeded temporary projects above, never
  the live registry.
- **Q2** Keep the narrow glossary guard, extended to enforce the accurate
  stale-round phrase (F).
- **Q3** One `docs/how-tagteam-works.md` with stable per-section anchors
  (`#loop`, `#cycle`, `#modes`, `#headless`, `#controls`, `#escalations`,
  `#cockpit`, `#hub`, `#saloon`, `#files`).

## Round-5 change (reviewer r4)

Post-approval installed-wheel verification no longer runs bare `tagteam
upgrade`: it goes through `scripts/upgrade_smoke.py --python <3.0.0
venv python> --expect-version 3.0.0` (helper echoes imported
`tagteam.__version__` / `__file__`, parent refuses a mismatch or a path
outside that interpreter's prefix), with the same temp registry, fail-
closed assertions and sentinel / real-registry / managed-destination
checks as the in-cycle gate; "bare `tagteam upgrade` is forbidden" is
stated for both recipes; interpreter-selection test added.

## Round-3 changes (reviewer r2)

1. *Upgrade smoke isolation:* `scripts/upgrade_smoke.py` — fresh helper
   process with `registry.REGISTRY_DIR` / `REGISTRY_FILE` patched to a
   temp registry listing only the disposable project before
   `upgrade_command()` runs; parent checksums the real registry and every
   real registered project's framework files before/after; regression
   test with an unvisited sentinel; the real registry is never replaced
   or edited. (Round 4: sentinel kept outside every registry, temp
   registry = exactly one entry, no `HOME` override anywhere, fail-closed
   assertion that both registry globals resolve under the temp dir
   immediately before each call — for the 0.12.0 setup step too —
   snapshots record existence + hash so files created in unrelated paths
   are detected.)
2. *Snapshot contract:* `export-usage --as-of YYYY-MM-DD` (required);
   inclusive UTC day, cutoff `ts < DATE+1 00:00Z`, filter before strip;
   identical cutoff in `report`; per-`(phase,type,round,role)` `attempt`
   ordinal in `(ts, id)` order added to schema + sort key; `report`
   rejects a snapshot whose `as_of` ≠ its `--as-of`; tests for shuffled
   duplicates, boundary timestamps and byte-stability with later turns.
3. *Stale streak:* defined as consecutive unchanged re-submissions (equal
   adjacent transitions) using the exact production loop; 11 identical →
   10; fixture expectations computed by that loop; footnote states the
   baseline rule.

## Round-2 changes (reviewer r1)

1. *Public-safe evidence:* sanitized snapshot schema + deterministic
   `export-usage` + safety tests (E, F); screenshots from a seed script
   with generic names and a private `--registry`, PNG chunk test, and a
   public-safety checklist recorded in findings (C).
2. *Diagram contract:* four mermaid blocks ↔ four SVGs ↔ four README
   sections, 1:1 (C); every "≤10 rounds" / "round-10" wording replaced by
   "auto-escalation after 10 consecutive stale rounds; a progressing
   cycle may pass round 10" in the flow, glossary, script (stale-streak
   metric instead of a round-10 line), SVG labels and the glossary test.
3. *Gates split:* in-cycle release-readiness gate (all local: pytest,
   package-tree diff vs origin/main, wheel metadata + `tagteam/` file
   hashes vs the 0.12.0 wheel, source-install `upgrade` no-op on a
   0.12.0-set-up fixture) vs a post-approval checklist (PR / CI / GitHub
   render / tag / PyPI / installed upgrade).
4. *Methodology + drift guard:* explicit inclusion rules for cycles,
   rounds, AMEND, rulings, in-progress cycles, failed / retried / null
   usage rows; per-provider-only curves labeled descriptive; as-of
   pinning; byte-compare test of the marked block.
