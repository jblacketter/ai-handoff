# Phase 45: Cockpit lanes — Lead | Reviewer, each in its own pane (3.8)

## Status
- [x] Planning
- [x] In Review (round 1: Codex — three implementation-contract gaps: the `#tab-lead-name` write, the all-activity support elements, the refresh path for the always-visible lead lane; all specified in round 2)
- [x] Approved (round 2)
- [x] Implementation (branch `phase-45-cockpit-lanes`)
- [ ] Implementation Review
- [ ] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary

**What:** replace the cockpit's shared, newest-first Activity log (Phase 43)
with **two panes, one per agent, side by side in the order the loop runs**:
**`<lead name> — lead`** on the left and **`<reviewer name> — reviewer`** on
the right, the turn token between them. Each pane is a *timeline in time
order, newest at the foot* — like a chat. The lead pane **is** the chat
(today's Lead tab: messages, composer, conversation picker) with the lead's
cycle turns as compact cards *between* the messages; the reviewer pane holds
the reviewer's reviews — per round: pre-check → review → verdict — streaming
in place while they run. Whichever pane is working pulses; nothing has to be
scrolled to. The tabs shrink to **Rounds · Diff · Usage · Notes** (the chat
tab is gone — the chat lives in the lead pane); the flat "all activity" list
survives as a disclosure on the Rounds tab. Decision briefs, starts and lost
turns stay in the Needs-you banner (they are the arbiter's, not an agent's).

**Why:** the arbiter's second walk-through on 3.7.1 (`docs/cockpit-issues.md`,
2026-08-17, later): "yes I can see the reviewer now, but it's a bit awkward
to scroll up, and it shows activity. Lead (Claude) and Reviewer (Codex) should
be clearly labeled — and since we start with the lead, it's unexpected to
have the second process of reviewer up above." One newest-first list for
two agents puts Codex's running review *above* the Claude chat you were
typing into, interleaved with pre-checks and starts — the wrong container
(Gestalt common region), the wrong order (mental model: lead → reviewer,
left → right, time top → bottom), and the thing you want to see is
somewhere else on the page (visibility of status).

**Depends on:** Phase 43 (`/api/activity`, the turn-log SSE, `now.turn_kind`
/ `last_turn`, the shared stream registry), Phase 37 (the chat), the UX
passes on `main` (one column, the arbiter's words, one Start). **Size:**
medium (front-end; one small read-model addition). Branch
`phase-45-cockpit-lanes`, PR at the end. **Release:** 3.8.0 (the saloon
rethink moves to 3.9).

**Compatibility rule.** Additive server-side (one new key on `/api/rounds`
entries is *not* needed — verdicts are read from what `/api/rounds` already
returns); no schema change; no CLI change; the saloon and the hub untouched;
`/api/activity` unchanged (the Rounds-tab disclosure and tests still use it).
Existing ids the tests pin (`now`, `needs-you`, `watch`, `conn`,
`data-tab="feed"`, `chip-gate`, `.feed-item.gate`) stay. Ids that **move or
go** (and every JS write to them is retargeted or removed in scope C — no
write to a removed id survives; the guards in §F check the id set):
`#tab-lead` / `#tab-lead-name` / `#lead-dot` / `#panel-lead` (the tab) →
gone; the lead's name is written to `#lane-lead-name` (`renderNow` retargets
its `$('tab-lead-name')` write there); `#lead-title` → gone (the lane header
is the title); `#cycle-line` / `#activity-head` → gone; the all-activity
support elements move *with* the list into the disclosure (§B).

## UX design (flow first — `ux-design-guide`)

**Goal (arbiter's words):** "When it goes to the reviewer, I want to see it —
without hunting. Lead and Reviewer clearly labeled; we start with the lead, so
the lead comes first."

**Diagnosis.** *Gestalt common region* (two agents share one list);
*mental model* (lead → reviewer, left → right; time top → bottom, like a
chat); *visibility of status* (the working thing should be where the eye
already is); *feature consolidation* (chat vs. lead turns were two homes for
the lead).

**Principles (3).**
1. **Common region + mental model** — one pane per agent, labeled with the
   agent's name and role, lead left / reviewer right, each a timeline newest
   at the foot.
2. **Visibility of status without moving** — the working pane pulses and its
   newest card streams in place; the token says whose turn it is.
3. **One home per thing** — the lead pane is the chat + the lead's turns; the
   reviewer pane is the reviews (+ their pre-checks); the arbiter's things
   are in Needs you.

**Flow.**

```
┌ github-profile · githubio-showcase — implementation, round 1 · codex is working · watcher: on ─┐
│ Needs you: quiet line / cards                                                                 │
├──────────────────────────────┬─●─┬───────────────────────────────────────────────────────────┤
│ CLAUDE — lead                │   │ CODEX — reviewer                                          │
│ (time order, newest at foot) │ t │ (time order, newest at foot)                              │
│  you: "lets do a minor…"     │ o │  pre-check · round 1 — passed 3m01s              [log]     │
│  claude: chat reply …        │ k │  ▶ review · round 1 — WORKING 0m41s              [log]     │
│  claude: implementation turn │ e │     [codex] reading the diff …  (streams here)             │
│    round 1 — done 4m  [log]  │ n │                                                           │
│  ┌ composer ────────────┐    │   │  finished reviews collapse to one line:                    │
│  │ Message claude…      │    │   │  "review · round 1 — changes requested · 2m10s" — click    │
│  └──────────────────────┘    │   │  for the round text                                        │
├──────────────────────────────┴───┴───────────────────────────────────────────────────────────┤
│ Rounds · Diff · Usage · Notes                                                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Strip** unchanged (project · phase · who is working / waiting on ·
   watcher · connection). The Phase 43 "cycle status line" merges into it —
   its facts are already there.                                     [status]
2. **Needs you** unchanged.
3. **Lanes** (full width, under Needs you): `.lane.lead` | `.token` |
   `.lane.reviewer`. Each lane: a **header** (`CLAUDE — lead`, pulsing when
   its agent is working; the state text `working · implementation turn, round
   1 · 1m12s` / `its turn — the watcher will start it` / `waiting`) and a
   **timeline** (`.timeline`, scrollable, newest at the foot, auto-scroll
   when the reader is at the foot).                     [common region, model]
4. **Lead lane timeline** = the current conversation's messages (as today:
   *you* / *claude* bubbles, streamed lines kept under a disclosure) **merged
   in time order** with the lead's cycle-turn cards (`implementation turn ·
   round 1 — working · 0m41s` streaming its log; then `done · 4m00s [log]`).
   The conversation picker + **New** stay in the lane header; the composer at
   the foot. While the lead is on a cycle turn, the composer's status line
   reads "claude is working on its turn (round 1) — streaming above · Stop
   turn"; while a chat message runs, the working banner from the UX pass.
                                                       [one home, status]
5. **Reviewer lane timeline** = per round, in time order: the pre-check card
   (`pre-check · round 1 — passed · 3m01s [log]`, or *bounced* — with the
   reason on click), the review card (`review · round 1 — working · 0m41s`
   streaming; then `— approved / changes requested / escalated / question ·
   2m10s [log]`, the verdict from the round's reviewer entry, its text on
   click), and review-lens cards for panels. Nothing of the lead's appears
   here.                                                          [one home]
6. **Token** between the lanes: on the lead's side / the reviewer's side / red
   for *you* / grey when nobody's turn.                              [status]
7. **Tabs**: Rounds · Diff · Usage · Notes. Rounds gains a collapsed **"all
   activity"** disclosure that renders the Phase 43 flat list (kept for the
   record and for the tests). The chat tab is removed.
8. **Narrow screens** (< 1000 px): lanes stack, lead first.

Deferred: a full-history view beyond the disclosure; per-lane filters;
theme. Absorbed: which lane streams (`inflight.role` + `turn_kind`), the
order (timestamps), the labels (agent names from `tagteam.yaml`), verdict
words (from the round entries).

## Scope

### In

**A. Read model** (`tagteam/cockpit_api.py`) — nothing new is required:
`/api/activity` (role, kind, round, status, stem, ref), `/api/lead/<cid>`
(messages), `/api/rounds/<cycle>` (per-round `entries` with `role`/`action`
→ verdict word), `/api/now` (lanes' state). One helper for the client's
benefit is allowed if the merge needs it: `activity_payload(..., role=)` as
an optional filter (query `?role=lead|reviewer`) — additive; the default is
unchanged.

**B. Markup** (`cockpit.html`)
- Replace `#cycle` (lanes + `#activity`) and the Lead tab panel with the two
  lanes: `#lane-lead` (`.lane-head` with `#lane-lead-name`, `#lane-lead-state`,
  the conversation `select` + **New**; `#lead-timeline`; the composer
  `#lead-form` with `#lead-status`, `#btn-lead-cancel` **Stop turn**,
  `#btn-lead-send`), `#lane-token`, `#lane-reviewer` (`.lane-head` with
  `#lane-reviewer-name`, `#lane-reviewer-state`; `#reviewer-timeline`).
- Tabs: `data-tab="feed"` (label **Rounds**), diff, usage, notes; the Rounds
  panel gains the disclosure with **all** the elements `loadActivity()`
  writes, so that function is reused unchanged:
  ```html
  <details id="all-activity"><summary>all activity <span class="muted small" id="activity-meta"></span></summary>
    <div class="activity" id="activity"></div>
    <div class="empty small hidden" id="activity-empty">No turns recorded yet for this project.</div>
    <div class="hint hidden" id="activity-more">Older turns: <code>tagteam tail</code> and <code>.tagteam/turns/</code>.</div>
  </details>
  ```
  (`#activity-meta` moves from the removed `#activity-head` into the summary;
  the guards in §F assert all four ids are present inside `#all-activity`.)
- Remove `#tab-lead` (+ `#tab-lead-name`, `#lead-dot`), `#panel-lead`,
  `#lead-title`, `#cycle-line`, `#activity-head`; every JS reference to them
  is retargeted (`tab-lead-name` → `lane-lead-name`; `lead-dot` → the lane
  header's pulse class) or removed (`lead-title`, `showTab('lead')`,
  `activeTab() === 'lead'`).

**C. JS** (`cockpit.js`)
- **Lane state**: `renderLanes(n)` — headers, state text, pulse classes,
  token (from `renderCycle`, minus the status line).
- **Reviewer timeline**: `upsertReviewerCard(item)` — activity items with
  `role in (reviewer, gatekeeper)` and `kind in (cycle, gate, panel,
  panel_lens)`; keyed by item id, patched in place, inserted in **ascending**
  `started_at`; the running card expanded and streaming (the Phase 43 stream
  registry, `attachActStream`); finished cards collapsed to one line with the
  outcome + duration + `[log]`; the **verdict word** for a finished review is
  read from `/api/rounds` (the reviewer entry of that round: `APPROVE →
  approved`, `REQUEST_CHANGES → changes requested`, `ESCALATE → escalated`,
  `NEED_HUMAN → question for you`; a gate entry `GATE_PASS/BOUNCE → passed /
  bounced`), the round text on click (expand).
- **Lead timeline**: `renderLeadTimeline()` — merges `LEAD.conv.turns`
  (messages) with activity items `role == lead && kind == cycle` by time; the
  chat rendering stays as it is (bubbles, kept lines, working state); the
  cycle-turn cards use the same card component as the reviewer lane. Rows
  keyed by id (`msg:<cid>:<n>` / item id) and patched — no wipe of the
  container on refresh (the Phase 37 `renderConversation` wipe is replaced
  by keyed patching, same XSS discipline: text nodes only).
- **Auto-scroll**: each timeline sticks to the foot while the reader is at
  the foot (the Phase 43 rule), never jumps otherwise.
- **All-activity disclosure**: `loadActivity()` keeps feeding `#activity`
  (now inside the Rounds tab); the lane upserts read the same payload (one
  fetch per refresh).
- **Gate line / composer**: "claude is working on its turn (round N) —
  streaming above · Stop turn" when the lead's cycle turn holds the slot;
  the UX-pass working banner while a chat message runs.
- **Refresh path** (`refreshAll`): the lead lane is always visible, so the
  tab condition goes: every refresh runs `renderNow(n)`, `loadFeed()`,
  `loadActivity()` **and `loadLead(false)`** (which loads the conversation
  list + the current conversation and (re)subscribes its stream via the
  registry — cheap: two GETs; the SSE `end` handler already calls
  `loadConversation` + `refreshAll`). `loadActivity()`'s payload feeds both
  lanes and the disclosure (one fetch); `renderLeadTimeline()` runs after
  *either* `loadConversation` or `loadActivity` resolves (it merges whatever
  both have so far — keyed upsert makes the order of arrival irrelevant).
  The 30 s live-mode safety tick and the polling fallback call the same
  `refreshAll`. **Empty state (no conversation yet):** the lead timeline
  shows the lead's cycle-turn cards if any and, at the foot above the
  composer, "No messages yet. Brainstorm, plan, or say `/handoff start
  <phase>` to begin a phase." (today's `#lead-empty` text); the composer is
  enabled when the lead validates for headless turns (`LEAD.cfg.ok`) —
  sending creates the conversation as today; when it does not validate, the
  composer is disabled with the reason ("This page cannot run the lead
  yet: … — set agents.lead.headless in tagteam.yaml").
- Remove: `showTab('lead')` paths (Start's `onDone` now focuses the composer
  in the lead lane), `focusRunningActivity` → `focusWorkingLane`; the
  `activeTab() === 'lead'` branch in `refreshAll` and `showTab`.

**D. CSS** (`cockpit.css`) — `.lanes` grid `1fr 44px 1fr` (stack under
1000 px, lead first), `.lane-head` (name — role, state, pulse), `.timeline`
(max-height, overflow auto), the card component (`.turn-card`, `.working`,
`.s-<outcome>`, verdict tags), the composer pinned at the foot of the lead
lane. Remove `.cycle-line`, `.activity-head`, the Lead tab panel styles that
no longer apply.

**E. Docs** — HTW `#cockpit` (lanes replace "Cycle region + Activity log";
the chat lives in the lead lane; the all-activity disclosure) and `#lead`;
README cockpit paragraph + `cockpit-cycle.png` and `cockpit-lead.png`
recaptured (media manifest updated); `docs/cockpit-issues.md` resolution
note for the 2026-08-17 (later) entry.

**F. Tests** (`tests/test_cockpit_activity.py` — extend; `tests/test_docs_story.py`)
- Source guards: `#lane-lead`, `#lane-reviewer`, `#lead-timeline`,
  `#reviewer-timeline`, `#all-activity` (containing `#activity`,
  `#activity-empty`, `#activity-more`, `#activity-meta`) present; `#tab-lead`,
  `#tab-lead-name`, `#lead-dot`, `#panel-lead`, `#lead-title`, `#cycle-line`,
  `#activity-head` absent from the HTML **and no `$('<that id>')` in the
  JS** (a guard iterates the removed ids); `refreshAll` calls `loadLead(`
  unconditionally (source guard) and no `activeTab() === 'lead'` remains;
  the lane block builds DOM with text nodes only (no `innerHTML`); the
  verdict map lists exactly `APPROVE / REQUEST_CHANGES / ESCALATE /
  NEED_HUMAN / GATE_PASS / GATE_BOUNCE`; the lead-lane block keeps the
  Phase 37 XSS guard (block boundaries kept: `Phase 37: Lead panel` …
  `Live connection`); "Rounds" label; the strip words unchanged.
- Behavioural (node + the DOM stub): (1) reviewer timeline — items inserted
  ascending, a running review at the foot streams, patched in place on
  finish with the verdict word, same node, lines intact; (2) lead timeline —
  messages and lead cycle turns merge in time order, keyed and patched (a
  refresh does not wipe); (3) role split — a reviewer item never lands in
  the lead lane and vice-versa; gate/panel items land in the reviewer lane.
- If `activity_payload(role=)` is added: its filter test.
- Screenshot registration for the recaptured images.

### Out

- The saloon rethink (Phase 44 → 3.9); the hub; theme; mobile beyond
  stacking; any engine change; changing `tagteam tail` / the CLI.
- Editing round text or verdicts from the lanes (Needs you keeps the
  arbiter's actions).

## Success criteria — in-cycle gates (all local)

1. **The reviewer is visible where you are.** With a review running, the
   reviewer lane's header pulses and its newest card streams — without
   scrolling away from the lead lane / composer (verified by the guards + the
   manual walk on the seed).
2. **Labels and order.** Lanes read `<lead> — lead` | `<reviewer> —
   reviewer`, left → right; each timeline is ascending in time with the
   newest at the foot (behavioural tests).
3. **One home.** No lead item in the reviewer lane and vice-versa; gate /
   panel items in the reviewer lane; the chat only in the lead lane
   (behavioural + guards).
4. **Nothing rebuilt.** Both timelines are keyed and patched (guards on the
   containers: no `innerHTML = ''` on `#lead-timeline` / `#reviewer-timeline`
   in the lane block; the running card is the same node after finish).
5. Full suite green via the gate on submit; the XSS guards pass; saloon/hub
   byte-identical in behaviour.

## Post-approval checklist (not review gates)

- PR from `phase-45-cockpit-lanes`; `scripts/release.py 3.8.0` after merge;
  roadmap status; screenshots; cockpit-issues note.

## Round-2 changes (reviewer r1)

1. **`#tab-lead-name` write.** The tab (and `#tab-lead-name`, `#lead-dot`,
   `#panel-lead`, `#lead-title`) goes; `renderNow` retargets the lead's
   name to `#lane-lead-name`; a guard asserts no `$()` of a removed id
   remains in the JS. Compatibility rule rewritten to list what moves/goes.
2. **All-activity support elements.** The disclosure carries `#activity`,
   `#activity-empty`, `#activity-more` and `#activity-meta` (moved into the
   summary), so `loadActivity()` is reused unchanged; markup + guard added.
3. **Refresh path.** `refreshAll` calls `loadLead(false)` on every refresh
   (the lane is always visible); one activity fetch feeds both lanes and the
   disclosure; `renderLeadTimeline()` is a keyed merge that runs after
   either source resolves; the no-conversation empty state and the disabled
   composer case are specified.
