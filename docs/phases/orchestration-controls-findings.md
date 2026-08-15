# Phase 32 — Orchestration Controls & Usage Surfacing: Findings

Dogfood and release-check record for `docs/phases/orchestration-controls-usage-surfacing-30-arc.md`.

## Plan cycle (closes Phase 31 finding 9(b))

Every reviewer turn of this phase's *plan* cycle ran headless on tagteam 0.8.0 (Codex via
`codex exec --json`), 7 rounds:

| Round | Outcome | Wall |
|---|---|---|
| 1 | REQUEST_CHANGES (3) | 141.8 s |
| 2 | REQUEST_CHANGES (3) | 194.6 s |
| 3 | REQUEST_CHANGES (2) | 152.1 s |
| 4 | REQUEST_CHANGES (1) | 98.8 s |
| 5 | REQUEST_CHANGES (1) | 135.6 s |
| 6 | REQUEST_CHANGES (1) | 132.2 s |
| 7 | APPROVE | 89.7 s |

Rounds 4–7 were a single thread on the retry-gate fingerprint (embedded repositories,
`.gitmodules` whitelists, newline paths) that ended in a simpler design: recurse into every
gitlink from a NUL-framed `ls-files --stage -z`, no `foreach`, no whitelist. Worth noting for
future plans: when a reviewer keeps finding holes in a whitelist, remove the whitelist.

## Trimmed skill contract — closed: keep full

Phase 31 reviewer turns consumed 1.69 M / 716 K / 282 K input tokens, 91–96 % cache-read; the
SKILL.md contract is ≈4–5 K tokens per turn — under 1 % of the smallest turn. No trimming.

## Dogfood (impl cycle)

_filled in below_

## Downgrade proof (0.8.0 opens a v4 project)

_filled in below_
