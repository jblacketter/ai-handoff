# Phase 37 — Cockpit Launchpad & Lead Conversation: findings

Plan: `docs/phases/cockpit-launchpad-lead-conversation-31.md` (approved
round 5; flow designed first with the `ux-design-guide` skill from the
arbiter's own walk-through). Branch `phase-37-cockpit-launchpad`, release
**3.1.0**.

## What shipped

- **Cockpit by default.** `tagteam serve` (no flags, no config key) opens
  the cockpit on `127.0.0.1`; `--theme saloon` / `serve: {theme: saloon}`
  is the legacy dashboard, byte-identical to the pre-3.1 bare `serve`
  (test retargeted). Banner: `Tagteam cockpit — <project> — <phase · type
  · rN · state | no active cycle> → http://127.0.0.1:8080`.
- **Port lease** (`tagteam/portlease.py`): `serve` and `hub` claim
  `~/.tagteam/ports/<port>.json` (pid + creation identity, `O_EXCL`)
  before binding and release it on shutdown — including on the early
  "occupied" exits (a bug found by the test: the first version leaked the
  lease on the probe-occupied path). Stale (dead pid / definitive identity
  mismatch) → replaced; live-but-unverifiable → held. Bind
  (`EADDRINUSE`) stays authoritative for unrelated occupants; a pre-bind
  connect probe of the connectable host catches the loopback/wildcard
  shadow; the message names the holder only from a verified lease /
  `/api/info` (`{"app": "tagteam", "kind": …, "project": …}`, added to
  cockpit, saloon and hub). Observed live: the arbiter's two stray
  `tagteam serve` (3.0.0) on 8080 → `port 8080 is in use on 127.0.0.1 —
  use --port 8081`.
- **Turn slot** (`headless.claim_turn_slot` / `update_turn_slot` /
  `release_turn_slot` / `slot_status`): the ONE owner of
  `.tagteam/turns/inflight.json`, decided under `dualwrite.writer_lock`
  (held for the claim only), owner token, existing field contract kept
  (`pid`, `child_ident`, `watcher_pid`, `watcher_ident`, parent) +
  `kind` (`cycle` | `conversation` | `briefer`) + conversation fields;
  release unlinks only the owner's marker; recovery is definitive-only
  (dead pid, or recorded non-null identity mismatch); live-unverifiable
  and legacy-without-identity stay Busy. Wired into
  `HeadlessEngine._run_attempt` (Busy → the tick is declined,
  `engine.slot_busy` set), the briefer (Busy → refused cleanly), and the
  watcher's headless tick, which **re-dispatches the still-owed turn once
  the slot frees** (new tick branch, tested). `cockpit_api.watcher_status`
  no longer treats a conversation/briefer runner as a watcher.
- **Launch intent** (`tagteam/launch.py::launch_intent`) — the state
  machine from the plan, one function consumed by the Start card, the
  copy command, `start_payload`, the composite launch and the hub row;
  `roadmap.is_terminal_status` normalizes Complete / ✅ Complete… /
  Absorbed / Deferred / Superseded (and `get_incomplete_phases` uses it —
  `tagteam roadmap queue` on this repo now starts at the first open
  phase instead of Phase 20). `start_payload` gates Start headless on
  `HeadlessEngine.validate()`.
- **Composite launch** (`launch.launch`): claim persisted in `launches`
  (UNIQUE key = sha256(intent.command + observed), owner pid/identity,
  attempt, per-step references, partial JSON) under a short writer-lock
  hold; watcher spawn (`start_watcher`: detached, `--pidfile`, ≤5 s wait
  for an identity-bound pidfile or early exit) and the lead's first turn
  outside the lock; repeat → existing turn; concurrent → one insert;
  orphaned pending → reconciled from references (`reconcile_launches`,
  on request and on start); `retry: true` = atomic failed→pending
  (attempt+1) rerunning only the missing steps (alive watcher reused,
  existing turn returned; a persisted conversation whose turn never got
  created is reused). `stop_watcher` (identity-checked pidfile only),
  `start_session` (`session.ensure_session`, manual backend returns the
  commands).
- **Lead conversation** (`tagteam/lead_chat.py`): `new_conversation`,
  `send` (one adapter turn: claude `--session-id`/`--resume`; codex
  `build_conversation_argv` → `exec --json -C root <policy>
  --skip-git-repo-check resume <id> -` when `codex_resume_supported()`
  probes true, else budgeted transcript replay; first-turn header;
  slot claim kind=conversation; usage row `kind=conversation`; turn row
  running → ok|failed|cancelled with reply/continuity; transcript.md +
  `<n>.events.jsonl` + `<n>.log` under `.tagteam/conversations/<id>/`),
  `cancel` (bind_inflight rule), `reconcile` (orphaned running → failed),
  `turn_events` (retained events with ids `<n>:<seq>` / `<n>:end`,
  replay from cursor), `extract_reply`. Ids `c-<12 hex>`, regex + DB
  lookup before any path join, path containment asserted, 32 KB message
  cap. Codex-lead continuity probe cached per executable.
- **Server**: `GET /api/start`, `/api/lead`, `/api/lead/<cid>`,
  `/api/lead/<cid>/events` (SSE with `Last-Event-ID` / `?after=` replay
  then live, shared cap), `/api/info`; `POST /api/start/launch` (the
  legacy Saloon keeps `/api/launch`), `/api/watch/start|stop`,
  `/api/session/start`, `/api/lead/new`, `/api/lead/<cid>/send` (worker
  thread; 202; 409 busy with reason; 413 oversize; 400 empty; dry-run
  preview), `/api/lead/<cid>/cancel`. All token+Origin guarded as before.
- **Cockpit UI**: Start card (exact intent; Copy command / Launch
  terminals / **Start headless** only when headless validates, else
  terminals primary + reason; not-set-up card), watcher chip **Start /
  Stop**, **Lead** tab first (conversation select, New, transcript built
  with `createElement`/`textContent` only, live tool lines, composer with
  Cmd/Ctrl-Enter, busy state with "interject instead", Cancel, per-
  conversation SSE with cursor), empty-state text from the intent.
- **Hub**: rows carry `intent` (computed read-only from prefetched state
  / cycle status / pause — no DB); `Start →` (plan/implementation) only
  when `intent.command` exists, linking `/p/<id>/#start`; `/api/hub/info`
  and `/api/info` identity.
- **CLI**: `tagteam lead "message" [--new] [--conversation ID] [--json]`,
  `tagteam lead --list` (exit 3 = busy).
- **Schema v7** (additive): `conversations`, `conversation_turns`,
  `launches`, `usage.kind`; `NON_FILE_BACKED_TABLES` in parent-before-
  child order; v5/v6 DBs open and migrate.
- **Docs**: README (Talk to the lead, launch, watch and steer; ladder ③
  label + SVG updated together; new `cockpit-lead.png`), HTW `#cockpit`
  (default, lease, launchpad table) + new `#lead` + hub/saloon/files
  rows, manifest, showcase sentence; version 3.1.0 (pyproject +
  CITATION).
- **Tests**: `tests/test_lead_chat.py` (18: slot claim/release/owner-only
  unlink, definitive-only recovery, fail-closed cases, barrier race, argv
  shapes + codex parser smoke + probe cache, chat end-to-end with resume
  argv assertions, codex replay/resume, refuse-while-cycle-turn + marker
  kind, failed turn without pause marker, orphan reconcile, events
  replay/cursor, id/size boundaries, not-configured, roadmap terminal
  normalization against a copy of this repo's roadmap, watcher slot
  re-dispatch), `tests/test_launchpad.py` (17: intent matrix, exhausted /
  not-set-up / paused, impl-approved skip-by-name, headless gate,
  idempotent launch, concurrent barrier, watcher early-exit + partial
  state + retry reuse, three crash windows, endpoints incl. SSE fast-
  finish / reconnect / after-end, busy 409 + boundaries + 413 + dry-run,
  hostile reply round-trip + JS-source no-innerHTML guard, watch/session/
  launch endpoints + legacy `/api/launch` untouched, port lease both
  orders/stale/unverifiable/foreign token, `serve` refusal + Tagteam
  holder named + immediate restart, hub rows, CLI; round 2 adds lifecycle-injection, lease-atomicity and body-cap tests). Suite: 981 passed / 5 skipped.

## Deviations / notes for the reviewer

- **Endpoint name**: the composite launch is `POST /api/start/launch`
  because the legacy Saloon already owns `POST /api/launch` (its Mayor
  flow) — the plan's `launch` name refers to the operation; the route is
  namespaced under `/api/start`.
- **Turn-row `reply` / `continuity` columns** were added to
  `conversation_turns` (v7) so the panel and `tagteam lead` can show the
  reply without re-parsing events; still additive.
- **The composite persists `conversation_id`/`turn_n=1` before the turn
  runs** (recoverable trace); on retry the code checks the turn row
  actually exists — a persisted conversation whose turn never got created
  is reused, not returned as "existing" (caught by the partial-state
  test).
- **Playwright hostile-markup check** was run by hand (below) plus the
  JS-source guard in the test suite; Playwright is not a test
  dependency of this repo.
- **`start_session` on macOS/CI**: the endpoint test monkeypatches
  `launch.start_session` (no terminals in CI); the manual-backend path is
  the one exercised.
- **The seed** now installs the handoff skill contract into each demo
  project (`HeadlessEngine.validate()` requires it, so Start headless is
  offered in the capture) and adds `demo-idle` with a canned two-turn
  conversation; the seed's brief path is project-relative as before.

## Round 2 (reviewer impl r1)

1. **Port lease publish is atomic** — the complete record is written to a
   private temp file and `os.link`ed into place (`FileExistsError` = held;
   O_EXCL + single write only where hard links are unavailable), so a
   contender can never observe a half-written lease. An unreadable /
   malformed lease is re-read a few times and then **fails closed** with
   an actionable message (`port N has an unreadable lease at … — remove it
   if no tagteam server is running, or use --port N+1`); it is never
   unlinked on the guess that it is stale. Tests: a barrier race at the
   publish window (`os.link` blocked until both contenders have written
   their record) → exactly one `Lease`, the loser gets `PortHeld` naming
   the winner; a malformed lease → `PortHeld`, file kept byte-for-byte.
2. **`send` owns its post-claim lifecycle** — split into `start_turn`
   (validate, continuity, claim, `running` row, marker fields; on ANY
   failure after the claim the slot is released and an already-created row
   is ended `failed: setup failed …`) and `run_turn` (spawn + finalize;
   the slot is released in `finally`; an unexpected runner exception ends
   the row `failed: runner error …` and re-raises; a failure while
   finalizing ends it `failed: finalize failed …`). `send` = the two in
   sequence. Tests inject failure before the row (`add_conversation_turn`),
   after the row (`_append_transcript`), a non-`SpawnError` runner
   exception, and a finalize exception — asserting the slot is free, no
   turn stays `running`, and the conversation is usable again.
3. **Start / Send are asynchronous** — `POST /api/lead/<cid>/send`
   accepts synchronously (`start_turn`: slot claimed, `running` row + turn
   number persisted; Busy answers 409 right there) and runs the turn on a
   worker, returning **202 `{conversation_id, turn_n}`**; the composite
   `launch(background=True)` does the same after the watcher step,
   persists the reference on the `launches` row, returns **202 pending**
   immediately, and the worker finalizes the row (`succeeded`, or `failed`
   with the reason if `run_turn` raised); a repeat while pending returns
   202 with the same reference. UI: `act()`'s `onDone` opens the Lead tab
   and subscribes the per-conversation SSE on the 202 (the transcript
   streams while the lead works). Test (`test_watch_session_and_launch_
   endpoints`) with a slow fake: the POST returns in < 1.5 s while the
   turn is `running` and the slot is held (kind=conversation), SSE emits
   `line` frames before release, a concurrent repeat returns the same
   `conversation_id` (one watcher start, one message), completion
   finalizes turn (`ok`, reply) and launch (repeat → 200 `existing`).
4. **64 KiB JSON body cap** — `_read_json_body` checks `Content-Length`
   BEFORE reading in cockpit mode (`> 65536` → 413 "request body exceeds
   65536 bytes"; malformed / negative → 400) and the legacy routes under
   cockpit mode get the same check before their own reads; the 32 KiB
   `text` cap remains. Tests: a > 64 KiB object with a small `text` → 413
   and no turn created; `/api/state` under cockpit → 413; `Content-Length:
   abc` / `-5` → 400 without reading; small bodies unaffected.

## Round 3 (reviewer impl r2)

1. **Dispatch failure aborts the accepted turn.** New owner-safe
   `lead_chat.abort_turn(handle, reason)` (releases only the handle's slot
   token, ends its row `failed: aborted before running: …`, notes the
   transcript) and one worker-start seam `lead_chat.start_worker` used by
   both `/api/lead/<cid>/send` and the composite; if starting the worker
   raises, Send answers 503 with the reason and the composite fails its
   claim truthfully (`lead: could not start the worker thread …; the
   accepted turn was aborted`). Tests: composite and HTTP Send with the
   worker start raising → error response, slot free, turn `failed
   (aborted…)`, launch `failed`; a subsequent retry / send works, and a
   never-ran turn is re-sent (nothing was delivered).
2. **Launch status follows the persisted turn status**
   (`_finalize_from_turn`): only `ok` → `succeeded`; `failed` /
   `cancelled` → launch `failed` with the turn reference + error in
   `partial` and the message "send again from the Lead panel (this launch
   will not re-send)"; `running` → 202 pending. Applied to the background
   worker, the synchronous `send=` path and the existing-turn retry path
   (a failed/cancelled existing turn is neither re-sent nor relabelled;
   an existing turn that never ran — `aborted before running` / `setup
   failed` — may be sent again). Tests: sync + background nonzero and
   cancelled turns → launch failed with matching partial and no re-send
   on retry; a persisted running turn → pending; an ok existing turn →
   succeeded without a second message.

## In-cycle gates

1. **Walk-through as a user (seed, Chrome/Playwright, 1280×800):** idle
   `demo-idle` → `tagteam serve` (bare) → cockpit; **Start card** "Start:
   csv-export — plan", body "The lead will be told: /handoff start
   csv-export", buttons Copy command / Launch terminals / **Start
   headless** (primary; headless validated); watcher chip "no watcher ·
   Start"; **Lead** tab: two-turn conversation, "continuity: resumed
   session · 2 turn(s)", composer enabled. Captured as
   `docs/media/screenshots/cockpit-lead.png` (1280×800, no text chunks;
   token absent from visible text; no absolute path). Hub over the seed
   registry: rows carry intents (`demo-idle` → `/handoff start
   csv-export`; active/escalated rows none) — verified via `/api/hub`.
   The Start-headless click path is covered end-to-end by
   `test_watch_session_and_launch_endpoints` (server → composite → watcher
   (stubbed) → conversation turn with the fake agent → repeat idempotent).
2. **Real `claude -p` conversation on this repo:** `tagteam lead --new
   "…what would you check first before starting Phase 37's impl review…"`
   → reply in 6 s ("confirm the branch's dirty working tree … is
   committed and the full pytest suite passes"); `tagteam lead "In one
   sentence: what did I just ask you about?"` → **resumed session**, the
   lead restated the first message; `tagteam lead --list` shows
   `c-43d667ccf0c3 2 turn(s) … resumed session`; transcript.md + `1.log`
   / `1.events.jsonl` / `2.*` on disk; two `usage` rows `role=lead,
   kind=conversation, status=ok` with session ids; slot free afterwards.
   Refusal while a cycle turn holds the slot and the watcher's
   re-dispatch after a conversation are covered by tests (409 with the
   reason; `test_headless_watcher_redispatches_once_the_slot_frees`).
3. **Hostile markup (browser):** a reply `<img src=x
   onerror="document.title='pwned'"><script>…</script><b>bold?</b>` and a
   user message `<i>hostile</i> user text <script>x</script>` injected
   into the seeded conversation; after reload the panel shows them
   verbatim as text, `document.title` still "Tagteam Cockpit", zero
   `script/img/b/i` elements inside the transcript container.
4. **Port**: two `serve` on one port refuse (lease names the holder;
   unrelated listener → generic message; immediate restart after shutdown
   passes) — tests + the live observation above.
5. `pytest`: **981 passed, 5 skipped** (macOS, 2 m 55 s; round 3);
   `--theme saloon` byte-identical to the pre-3.1 bare page (test); v6 DBs
   open under v7 (test); hub read-only AST test unchanged and green.
