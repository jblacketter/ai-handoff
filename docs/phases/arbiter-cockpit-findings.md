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
  cockpit mode the `*` header is dropped on **all** responses, including `/`
  — the page embeds the token, so a wildcard on the HTML would let any origin
  `fetch()` the page and read it, defeating the token. Legacy mode is
  untouched (`*` everywhere, as in 0.10.0).
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

- `pytest`: **861 passed, 5 skipped** on the working tree (before the impl
  submission commit). New: `tests/test_cockpit_api.py` (25),
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

**Still to record during the impl cycle:** the Feed live while a headless
reviewer turn is in flight (in-flight chip + Cancel turn in the drawer).
The impl-cycle reviewer turns will exercise it; I will amend this section
in the round that follows.
