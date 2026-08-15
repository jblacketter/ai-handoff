# Phase 33 — Escalation Briefer: Findings

## Plan cycle (6 rounds, all reviewer turns headless — with two incidents worth keeping)

| Round | Outcome | Wall |
|---|---|---|
| 1 | REQUEST_CHANGES (4) | 99.3 s |
| 2 | REQUEST_CHANGES (4) | 193.8 s |
| 3 | REQUEST_CHANGES (4) | 122.5 s |
| 4 | REQUEST_CHANGES (4) | 85.0 s |
| 5 | REQUEST_CHANGES (3) | 117.7 s |
| 6 | APPROVE | (interactive Codex; the headless duplicate was cancelled at 52 s — see below) |

**Incident 1 — a headless *lead* answered a plan round autonomously.** Before round 6 the
interactive lead's edit script failed and its `tagteam resume` ran anyway; the watcher
re-dispatched the owed turn — the lead's — and a headless Claude (`claude -p`, 210 s) read
round 5's feedback, edited the plan, committed, and submitted round 6. Its work was
reviewed by the interactive lead and kept as-is (it independently chose the same fixes,
and reused `bind_inflight` for the busy check). Evidence that the plan→lead path works
headless for a *plan* revision, not just implementation.

**Incident 2 — two watchers, and an agent using `cancel-turn`.** A `tagteam watch --mode
iterm2` started the previous evening (pre-Phase-32 code in memory, so it ignored the pause
marker) was still running alongside the headless watcher. On round 6 both dispatched:
the interactive Codex in the iTerm tab reviewed and approved, and ran
`tagteam cancel-turn --by Codex` on the headless duplicate (killed at 52 s, recorded as
`cancelled by Codex`). Two lessons: (a) restart long-lived watchers after upgrading
(they do not reload code); (b) `cancel-turn`'s identity binding worked when invoked by
an agent, and the resulting `cancelled` row/marker made the situation legible.

## Dogfood — real briefs on the scratch project (2026-08-15)

Scratch project `greet-cli`, `briefer: {enabled: true}`, a deliberately arguable plan
(`greet-i18n`: silent English fallback vs `ValueError` for unknown locales), watcher in
**notify** mode (the briefer is mode-independent). Branch CLI = `.venv/bin/tagteam`.

| # | Event | Trigger path | Model | Wall | in / out / cache-read | cost | Outcome |
|---|---|---|---|---|---|---|---|
| 1 | r2 `ESCALATE` | first-poll **bootstrap** (watcher started on an already-escalated cycle) | claude-fable-5 | 56 s | 8 / 3 622 / 114 680 | $0.67 | ok |
| 2 | r3 `NEED_HUMAN` | new-seq `_handle_escalated` | claude-fable-5 | 48 s | 10 / 3 170 / 153 672 | $0.73 | ok |
| 3 | r3 `NEED_HUMAN` (same round, new event after `rule answer`) | new-seq | claude-fable-5 | 49 s | 8 / 3 316 / 120 128 | $0.61 | ok |
| 4 | r3 `NEED_HUMAN` (third same-round event) | new-seq, watcher restarted with `args: [--model, claude-haiku-4-5-20251001]` | **claude-haiku-4-5** | 87 s | 113 / 5 919 / 544 532 | **$0.15** | ok |

What the briefs did (all five headings present, all `ok`):
- Brief 1 found, on its own, that the package **already raises `ValueError` on a bad
  name** and that the CLI already maps `ValueError` to exit 2 — evidence neither agent
  had raised — and recommended `request-changes` (strict) with medium confidence, giving
  three ready-to-run `tagteam rule` commands. The arbiter ran option A; the ruling landed
  as a reviewer-role `REQUEST_CHANGES` at the same round without re-escalation, both the
  `ESCALATE` and the ruling visible in `cycle rounds` `entries`, and the
  `arbiter_ruling` diagnostic linked brief #1 + the event key.
- Brief 2 (needs-human) noticed the **plan file on disk still described the old fallback**
  and told the arbiter to make the lead fix it before approving.
- Brief 4 (Haiku) was coherent and correctly separated the meta-point ("stop escalating on
  wording") from the technical recommendation, at ~¼ the cost but longer wall time and
  ~4× the cache reads (it explored more). **Q5 verdict for now: a lighter model is
  acceptable for `needs-human` style questions; keep the default (lead's provider) for
  genuine disputes; the knob is `briefer.args`.**
- Same-round re-escalation produced **distinct events, files and rows** (`…_r3_<stampA>-a1.md`,
  `…_r3_<stampB>-a1.md`, `…_r3_<stampC>-a1.md`); `tagteam brief` always showed the current
  event; `--list` showed all four.
- Learned: the watcher resolves the briefer spec at **startup** — editing `briefer.args`
  in `tagteam.yaml` needs a watcher restart (documented in README). Also, by design a
  manual `--generate` after a *successful* attempt is refused (prior success satisfies the
  event), so model-tier comparisons need distinct events.

`tagteam usage --role briefer` on the scratch project: 4 turns, 16 027 output tokens,
$2.16 total, mean 60 s.

## Downgrade proof (0.9.0 opens a v5 project) — done 2026-08-15

Throwaway venv `pip install tagteam==0.9.0` (SCHEMA_VERSION 4) against a copy of the
scratch project (DB `user_version = 5`, 4 `briefs` rows): `cycle rounds` (grouped view
without the additive `entries` field, as expected), `usage`, `interject --list`, and
`cycle init` all work; `user_version` stays 5 and the `briefs` rows are untouched.
