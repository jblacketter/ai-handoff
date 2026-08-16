# Phase 34 — Arbiter Cockpit: findings

Plan: `docs/phases/arbiter-cockpit-30-arc.md` (approved round 3, including a
UX-design round). Branch `phase-34-arbiter-cockpit`, release 0.11.0.

## What shipped

- **Modes.** Bare `tagteam serve` is legacy — 0.10.0-identical (Saloon at `/`,
  bind all interfaces, four legacy POSTs without a token, `*` CORS, no meta
  token, every new endpoint 404). `--theme cockpit` or `serve: {theme: cockpit}`
  is cockpit mode: cockpit at `/`, Saloon at `/?theme=saloon`, loopback bind
  (`--host` overrides in both modes), per-run token on every POST incl. the
  legacy four, Origin/Referer check, no `*` CORS. Only shared change:
  `TagteamHTTPServer(ThreadingHTTPServer)`.
- **`tagteam/cockpit_api.py`** — pure payload builders (`now_payload`,
  `rounds_payload`, `interjections_payload`, `briefs_payload`,
  `brief_current_payload` (same rule as `tagteam brief`), `usage_payload`
  (aggregate + by-agent + per-turn series + rate-limit signal),
  `scope_diff_payload` (per-file, capped), `tail_payload`, `events_signature`
  / `signature_id`) and the write wrappers (`run_action`, `cli_preview`) that
  call the Phase 32/33 command functions with a captured `out`.
- **`cycle.compute_scope_diff`** extracted; `_cli_scope_diff` is a thin caller
  and its output/messages are unchanged (`ScopeDiffError` carries the CLI
  message text; test asserts byte-identical stdout).
- **Schema v6 `rate_limits`** (`provider, kind` unique; `id` for repair
  snapshots), `db.upsert_rate_limit` / `latest_rate_limits`,
  `headless.parse_rate_limits` / `record_rate_limits` (latest per kind from
  Claude's `rate_limit_event {rate_limit_info: {...}}`; Codex → nothing),
  wired after usage recording in the engine and the briefer; in
  `NON_FILE_BACKED_TABLES` → repair-preserved (test). The fake agent emits
  the frame so the E2E test proves capture.
- **SSE `/api/events`** — per-connection 1 s sampler over cheap signals
  (state seq, rounds file size+mtime, max ids of interjections/briefs/usage/
  rate_limits, briefs status digest, delivered/retired counts, pause marker,
  inflight stem/pid), `id:` = hash of the signature (inflight age excluded),
  heartbeat comment every 15 s, `Last-Event-ID` → current snapshot,
  `--max-sse` cap → 503 JSON, and a zero-timeout `select` + `MSG_PEEK` per
  tick so a closed tab frees its slot within one interval (found by the cap
  test: without it a slot stayed taken until the next write).
- **Action endpoints** `/api/pause|resume|interject|interject/retire|
  cancel-turn|brief/generate|rule` → `{ok, message, rc, cli}`; `{"dry_run":
  true}` returns the exact CLI line without executing (the confirm modal
  uses it); `by = web:<user>` (`TAGTEAM_ARBITER` respected); validation
  errors 400, CLI refusals 409, never a traceback (500 JSON if a builder
  throws — tested).
- **Frontend** `cockpit.html|css|js` (plain JS): Now strip → Needs you →
  Watch tabs (Feed | Diff | Usage | Notes) per the round-3 UX plan; feedback
  contract (pending spinner → toast with the server message + CLI, `ok:false`
  inline; Live / Polling / Disconnected indicator; SSE change → targeted
  re-fetch); confirmations for Approve / Request changes / Answer / Cancel
  turn show the CLI *with content*; Approve and Cancel turn styled/placed
  apart; Pause⇄Resume is one toggle; teaching empty states; per-file diff
  with expand; churn curve with the r10 line; Notes with Interject/Retire.
  Saloon `app.js`: one `tagteamFetch()` helper (+ a "Cockpit ↗" link shown
  only when the token meta exists).

## Deviations / notes for the reviewer

- **CORS on cockpit GETs.** The plan said "no `*` CORS on POST responses". In
  cockpit mode the `*` header is dropped on **all** responses — HTML, JSON,
  SSE and (since round 2, reviewer r1) static assets too — because the page
  embeds the token, so a wildcard on the HTML would let any origin `fetch()`
  the page and read it, defeating the token. Legacy mode is untouched (`*`
  everywhere, as in 0.10.0). Test asserts no wildcard on `/`, assets, JSON
  reads and 404s in cockpit mode, and `*` on `/` and `/app.js` in legacy.
- **Watcher liveness (round 2, reviewer r1 #1).** Tagteam's own launch shape
  (`python -m tagteam watch --mode X` from the project cwd) puts no project
  path on argv, so the 0.10.0 argv match never bound a watcher to a project.
  Now: `watch()` can keep `.tagteam/watcher.json` (pid + creation identity +
  mode + argv + project_dir) for its lifetime (removed in `finally`; tests)
  — **opt-in only** (round 3, reviewer r2): the file is written when the
  project has opted into the cockpit with `serve: {theme: cockpit}` in
  tagteam.yaml (the existing config gate) or the watcher was started with
  `tagteam watch --pidfile`; a bare `tagteam watch` writes **nothing new**
  (regression test snapshots the tree before/during/after and asserts no
  `.tagteam/` is even created). `cockpit_api.watcher_status()` binds by (1)
  the pidfile when present — dead pid or identity mismatch →
  `stale_pidfile: true`, never trusted; (2) a process scan of `tagteam …
  watch` processes whose argv names the project **or whose cwd is the
  project** (`procs.cwd`: /proc on Linux, `lsof -d cwd` on macOS) — this is
  what finds watchers without a pidfile, e.g. this repo's live `--mode
  iterm2` (pid 74337, verified: `{running: True, pid: 74337, mode:
  'iterm2', source: 'process-scan'}`); (3) the in-flight pointer's watcher
  identity. (Windows: no `/proc`/`ps`, so the scan returns nothing —
  liveness there is the pidfile or the in-flight identity.) So the two ways
  of turning the cockpit on get liveness like this:
  **CLI-only `tagteam serve --theme cockpit`** (no config key) → the watcher
  keeps no pidfile; liveness comes from the cwd-bound scan (all launch
  shapes Tagteam itself creates — session backends run the watcher from the
  project cwd — plus any argv that names the project) and, during headless
  turns, the in-flight identity; `--pidfile` upgrades it to the identity-
  checked record. **Config-enabled cockpit** (`serve.theme: cockpit`) → the
  watcher keeps the pidfile automatically (same gate that switches the
  server), scan/inflight remain the fallback. `/api/now.watcher` =
  `{running, pid, mode, source, stale_pidfile}`; the strip shows `watcher
  <mode> pid N` / `watcher gone (stale record)`. The legacy
  `/api/watcher/status` helper is deliberately unchanged (flag-off
  identity); the cockpit does not use it.
- **Scope diff per file (round 2, reviewer r1 #2).** `git status --porcelain`
  collapses a new tree to `newpkg/`; the CLI output stays exactly that
  (byte-identical), but the cockpit payload expands collapsed untracked
  directories via `git ls-files --others --exclude-standard` into files,
  filters Tagteam bookkeeping **at file level** (the CLI's artifact set plus
  `.tagteam/`), and reports real per-file `additions/deletions/patch` with
  accurate statuses: `untracked` (new, not in the index), `added` (tracked,
  absent from the baseline tree — via `git ls-tree` on the baseline),
  `modified`, `deleted` (unstaged or staged), plus `binary`. `paths` (CLI
  list) and `file_paths` (expanded) are both returned. Tests: a new package
  with two text files + a binary, collapsed `.tagteam/` and `docs/` dirs
  (no .gitignore), and an added / modified / deleted / binary matrix.
- **SSE liveness signal (found in the round-2 dogfood).** When the scratch
  watcher was killed mid-turn, the strip kept showing the in-flight turn and
  the watcher — no file changed, so no frame fired. The signature now
  includes `inflight.alive` (pid alive) and `watcher {pid, alive}` (pidfile),
  and the page does a slow 30 s safety refresh in live mode.
- **`--no-open`** was listed in the plan as "(existing)"; the server never
  auto-opened a browser and no such flag existed. Not added (adding one would
  be a no-op or a legacy behavior change).
- **`/api/rounds/<cycle>` in legacy mode** is unchanged (still `entries` /
  `rulings` from the parser); the additive `interjections` list is only
  attached in cockpit mode so legacy responses stay byte-identical.
- **`?nosse=1`** on the cockpit URL forces the polling path (used to dogfood
  the fallback; harmless otherwise).
- **Reopened plan cycle.** Round 3 (UX review) was appended to the approved
  plan cycle rather than a new slug; `add_round` accepts it (approved →
  in-progress → approved). Worth knowing that this works.

## Verification

- `pytest`: **861 passed, 5 skipped** at the round-1 submission; round 2
  adds watcher-liveness, per-file scope-diff, CORS-on-assets and SSE
  liveness tests (see the round-2 entry). New: `tests/test_cockpit_api.py` (25),
  `tests/test_server_cockpit.py` (25: legacy identity, pages/assets, auth
  incl. the legacy four both ways, reads, writes, SSE two clients / cap /
  heartbeat / Last-Event-ID / non-blocking, serve flags + config gate),
  `tests/test_db.py::TestSchemaV6RateLimits` (3),
  `tests/test_headless.py::TestRateLimitCapture` (3, incl. the recorded
  `claude_stream.jsonl` fixture which already contained a real frame),
  `tests/test_briefer.py::test_repair_preserves_rate_limits`.
- **Downgrade guarantee:** `tagteam==0.10.0` installed in a scratch venv opens
  a v6 project DB (user_version 6, `rate_limits` present) without error.

## Browser dogfood (Chrome, 2026-08-15)

Scratch project (`scratchpad/proj`, `serve.theme: cockpit` in tagteam.yaml,
plan cycle escalated by the reviewer at r1, one CLI interjection):

1. **Cockpit at `/`** — Now strip: `feat-x · plan · r1 · escalated`,
   `turn: you (arbiter)`, `no watcher`, `1 queued note`, **Live** dot; Needs
   you shows one **escalation** card (event text, "No brief yet for this
   event. [Generate brief]", textarea, `Request changes` left / `Approve`
   green right); Feed shows the two round entries.
2. **Ruling from the browser** — clicked `Request changes` with a comment →
   confirm modal with the exact line
   `tagteam rule request-changes --content '…' --by web:jackblacketter` →
   Run → toast "Ruling recorded (web:jackblacketter): changes requested —
   lead's turn. (no brief for this event)" + CLI; the strip flipped to
   `turn: lead (Claude) · owed 2s`, Needs you → "Nothing needs you. Claude is
   on feat-x plan r1 (0s). Watch the Feed.", and the Feed gained the
   `arbiter REQUEST_CHANGES` entry live (SSE, no reload).
3. **Pause / Resume as the hold** — `Pause` in the strip → toast with the
   CLI's message (marker path, "resume with: tagteam resume"), chip
   `paused 2s by web:jackblacketter`, **hold** card with `Resume`; `Resume`
   → toast "Resumed. Was paused for 0m13s (cli)…", card gone.
4. **Notes tab** — the CLI interjection listed as `pending · #1 · jack → next
   turn · next cycle` with `Retire`; Interject textarea + `to` select.
5. **Usage tab (empty)** — "Subscription window: n/a — no rate-limit signal
   recorded yet…", empty chart with the r10 line, "No headless turns recorded
   yet — `tagteam watch --mode headless` writes usage rows."
6. **Diff tab** — file list first (`untracked .tagteam/` — the scratch repo
   has no .gitignore; same list as `tagteam cycle scope-diff`), expand per
   file, Expand all / Refresh.

This repo (`tagteam serve --theme cockpit --port 8766`, real DB: 26 usage
rows):

7. **Feed over the real plan cycle** — 3 rounds / 6 entries, newest first,
   `more` expanders on long entries.
8. **Usage over real rows** — churn curve for `arbiter-cockpit-30-arc plan`
   (reviewer r1 308,073 tokens → r2 lower), By role table (reviewer 25
   turns / lead 1), By cycle / By process collapsed. Rate-limit line "n/a"
   because no headless Claude turn has run since the table was added — the
   impl cycle's reviewer turns are Codex (no equivalent), so the first real
   row will come from a Claude headless turn / brief.
9. **Two-tab SSE** — second tab opened; `tagteam pause --reason "cockpit
   two-tab SSE dogfood"` from the CLI → both tabs showed the `paused … by
   jack` chip and the hold card within ~2 s; `Resume` clicked in tab 2 →
   `tagteam resume` in the CLI says "Not paused." (marker cleared).
10. **Polling fallback** — `/?nosse=1` → indicator **Polling** (amber),
    panels still refresh on the backoff timer.
11. **Tail drawer** — `Tail` → drawer with `tagteam tail --no-follow ·
    <latest stem>.log` and the last 60 lines of the most recent headless
    turn log (the r2 reviewer turn).
12. No console errors in either tab.

### Live headless turn (round 2)

This repo's impl-review turns are dispatched by Jack's `--mode iterm2`
watcher to an interactive Codex tab (no headless turn log for impl r1), and
starting a headless watcher beside it would double-dispatch — so the live
headless observation was done on the scratch project with a **real** turn:
`tagteam watch --mode headless --interval 5 --turn-timeout 10` on
`scratchpad/proj` (lead owed after the browser ruling above), cockpit open:

13. **In flight** — strip: `in flight: Claude (lead) · 29s` (pulsing),
    `watcher headless pid 20552` (green, source `pidfile`), Needs you:
    "Claude is on feat-x plan r1 — in flight now"; **Tail** drawer:
    `tagteam tail · feat-x_plan_r1_lead_…log` streaming the real `claude -p`
    session (`[claude] session … model claude-fable-5`, tool calls, the
    lead's narration), red **Cancel turn** at the drawer's right edge.
14. **Turn completes** (59 s, ok — `lead SUBMIT_FOR_REVIEW at round 2`) —
    the Feed gained the r2 lead entry live; the strip flipped to `turn:
    reviewer (Codex)`; the watcher immediately spawned the Codex reviewer
    turn (`in flight: Codex (reviewer)`).
15. **Rate-limit signal, real** — Usage tab: `claude five hour window:
    allowed, resets 10:20 PM (in 3h30m) · seen Aug 15, 06:48:13 PM` from the
    turn's `rate_limit_event`; churn point r1 lead 3,549 tokens ($0.822).
16. **Stale states** — the watcher was then killed (SIGTERM, no `finally`)
    with the Codex turn in flight: the strip showed `in flight: … · process
    gone` and `watcher gone (stale record)`, Needs you showed the
    **attention** card "In-flight pointer, but the process is gone" with
    `Cancel turn`; confirm modal `tagteam cancel-turn --by web:jackblacketter`
    → toast "Refusing to signal: child pid 21214 is not alive (turn already
    ended) / Removed stale inflight.json (metadata only; nothing was
    killed)." (the CLI's exit 1 shows as a red toast — its own message) →
    the in-flight chip disappeared.
