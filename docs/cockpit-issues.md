# Cockpit issues (arbiter notes for a later UX phase)

Running list of problems seen while using the cockpit for real. Kept as evidence for the
cockpit-hardening / UX phase that will follow the current run of engine phases; the cockpit is
not being used for day-to-day work until then (the CLI loop is).

## 2026-08-16 — a running handoff is not legible as a handoff — RESOLVED by Phase 43 (3.7)

*Resolution (2026-08-17, `docs/phases/cockpit-hardening.md`):* the Watch zone opens with a **Cycle**
region — Lead / Reviewer lanes with the turn token on the owed side, the running agent's lane pulsing
and its kind named (`cycle turn r2` vs `lead conversation` vs `gate` …) — over a persistent **Activity**
log of every agent turn (both roles, gates, panel lenses, briefs, conversations) whose running row
streams its log and whose finished rows *stay* with a named outcome (`finished · cancelled · failed ·
timed out · process gone · orphaned`); rows are patched, never rebuilt. The Lead panel keeps a finished
turn's streamed lines under a collapsed disclosure; the Start card is replaced by the region's
*starting* state within one refresh; the Now strip names the in-flight kind and the last outcome; the
SSE signal covers conversation turns, launches and log growth. The tail drawer is gone (the running row
is the tail). Original report kept below as evidence.


Reported by Jack after starting a handoff from the cockpit:

- When a handoff starts it is not clear that a **handoff cycle** is in progress, as opposed to
  the lead merely working on its own. Activity was sometimes visible in a window *inside* the
  Lead panel, but that window would sometimes collapse, leaving only the previous conversation
  visible.
  1. Once it collapsed there was no way to tell whether the handoff was **still running or
     cancelled**.
  2. There was **no visible activity from Codex** (the reviewer) at all, which made it harder
     still to tell that the handoff was in process.
- Need: when a cycle starts, the cockpit must make it unmistakable that a **cycle** is running
  (not just the lead), and give a persistent **window into the activity** (both agents' turns,
  in-flight state, what happened last) that does not collapse away.

Related surfaces to look at when this is addressed: Now strip (in-flight chip), the Lead panel's
turn view vs. the cycle turn view (they are different things and should look different), the
Feed tab, `tagteam tail` equivalence in the browser, and the Needs-you/Start card transition
from "start" to "running".

Also parked for the same phase: `docs/saloon-rethink.md` (archetype cast & theme packs).
