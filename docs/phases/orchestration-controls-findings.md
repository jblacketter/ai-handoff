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

## How the branch CLI is invoked (reproducibility)

The globally installed `tagteam` on this machine is the uv-tool **0.8.0** release and does
not have the Phase 32 commands. Everything below uses the source checkout on branch
`phase-32-orchestration-controls` via the repo's editable venv:
`/Users/jackblacketter/projects/tagteam/.venv/bin/tagteam` (equivalently
`.venv/bin/python -m tagteam`). The headless watcher was started with
`PATH=$PWD/.venv/bin:$PATH tagteam watch --mode headless --interval 5 --turn-timeout 60`
so the spawned agents' own `tagteam cycle add` calls also resolve to the branch build.

## Dogfood (impl cycle, 2026-08-15)

### `cancel-turn` against a live real turn (scratch project `greet-cli`)

Plan cycle `cancel-probe` opened; headless watcher spawned a real `codex exec` reviewer
turn. `inflight.json` recorded `pid 49549`, `watcher_pid 49546`,
`watcher_ident "49546:Sat Aug 15 00:51:31 2026"`, `child_ident "49549:Sat Aug 15 00:51:31 2026"`.
`tagteam cancel-turn --by jack` → bound (identities + parent), marker written, tree
signalled; the engine recorded outcome **`cancelled`** 4.1 s after spawn, wrote a
`headless_turn_cancelled` diagnostic, and paused with reason "cancelled by jack";
`usage` row status `cancelled`; the cancel marker was gone afterwards.

### `pause` / `interject` / `usage` on this repo (impl cycle)

- The hold during each headless reviewer turn is now `tagteam pause --reason … --by
  claude-interactive` (marker `source: cli`), not a hand-written file; `tagteam resume`
  clears it before the next round is submitted.
- Interjection `#1` (`--to reviewer`, by jack) was written **while round-1's reviewer turn
  was in flight**; as designed it was *not* in that prompt ("No arbiter interjections were
  attached to round 1" — Codex, r1) and stays `pending` until the round-2 reviewer turn,
  which stamps `delivered_stem`. **Round-2 evidence:** the r2 reviewer turn's
  `inflight.json` carried `interjection_ids: [1]`; Codex's r2 verdict opens with "I saw
  arbiter interjection #1 in this headless prompt under `=== ARBITER INTERJECTIONS
  (unconsumed) ===`" and then performs the audit the note asked for; after the turn's
  `ok`, `tagteam interject --list` shows
  `#1 … delivered → reviewer r2 (orchestration-controls-usage-surfacing-30-arc_impl_r2_reviewer_20260815T080229Z)`
  (`delivered_role=reviewer`, `delivered_round=2`, `delivered_ts` = turn end, 2026-08-15T08:06Z).
  The audit itself was useful: it found the retry-gate tests did not mirror the plan's
  criteria (a)–(l) one-to-one, which round 3 fixed with the exact fixture (registered
  pre-dirty submodule, registered nested sub-submodule, real newline-path submodule via
  `git submodule add --name`, committed declared-only `.gitmodules`, embedded repos).
- Real `tagteam usage` output on this repo after r1 (11 headless turns since Phase 31,
  all reviewer/codex, cost unpriced for codex):

```
By role:
  reviewer   turns=11 (ok 11, failed 0)  in=6,115,780 out=57,884 cache_r=5,436,160 cache_w=0 cost=- (0/11 priced) mean=156s
By cycle:
  headless-turn-engine-30-arc/impl                    turns=3  in=2,686,588 out=15,184 cache_r=2,455,040 mean=154s
  orchestration-controls-usage-surfacing-30-arc/plan  turns=7  in=1,841,866 out=34,116 cache_r=1,505,024 mean=135s
  orchestration-controls-usage-surfacing-30-arc/impl  turns=1  in=1,587,326 out=8,584  cache_r=1,476,096 mean=307s
```

### Reviewer-sandbox lessons (r1)

Codex's headless review ran the suite inside a sandbox that **denies `ps`** and while the
repo carried the operator's pause marker. Both exposed real isolation flaws, now fixed:
`bind_inflight` unit tests are hermetic (helpers stubbed) with real-process checks gated on
capability; `tests/conftest.py` isolates `_StateProcessor(project_dir=".")` from the
checkout's marker; interjection assertions inspect the injected block, not the whole
prompt (the SKILL.md copy in the prompt now mentions the header itself).

## Downgrade proof (0.8.0 opens a v4 project) — done 2026-08-15

Throwaway venv `pip install tagteam==0.8.0` (SCHEMA_VERSION 3) against a copy of the
scratch project (DB `user_version = 4`, `interjections` table present, a `cancelled`
usage row): `cycle rounds`, `tail`, and `cycle add … APPROVE` all work; DB stays at
`user_version 4`; `usage` is (as expected) an unknown command in 0.8.0. Revert recipe
unchanged: `pip install tagteam==0.8.0` + `tagteam upgrade`.
