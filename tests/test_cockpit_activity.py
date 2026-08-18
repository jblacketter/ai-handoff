"""Phase 43 tests: the cockpit's activity read model (`activity_payload`,
`normalize_outcome`, `last_turn`, `launch_view`), the `now_payload` keys
(`turn_kind` / `launch` / `last_turn`), the SSE signature's new sources
(conversation turns, launches, in-flight log growth), `tail_payload(stem=)`,
the `/api/activity` + turn-log SSE endpoints, and the source guards on the
shipped cockpit (no innerHTML in the Cycle/Activity block, the ids the page
needs, the tail drawer gone, exactly seven outcomes)."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import db, headless as h, launch as L, lead_chat as lc, procs
from tagteam import state as state_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401
from tests.test_lead_chat import fake_path as chat_fake_path  # noqa: F401  (chat-mode fake agents)
from tests.test_server_cockpit import Served, SSEReader

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "tagteam" / "data" / "web"


def _iso(delta_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _conn(project: Path):
    return db.connect(project_dir=str(project))


_SEQ = {"n": 0}


def _seed_usage(project: Path, **over) -> int:
    _SEQ["n"] += 1
    fields = dict(ts=_iso(-60), phase="feat-x", type="plan", round=1, role="lead", agent="Claude",
                  provider="claude", status="ok", duration_ms=1500,
                  log_path=str(h.turns_dir(project) / f"feat-x_plan_r1_lead_x{_SEQ['n']}.log"))
    fields.update(over)
    conn = _conn(project)
    try:
        return db.add_usage(conn, **fields)
    finally:
        conn.close()


def _seed_conversation_turn(project: Path, *, status="ok", error=None, text="hello",
                            ts=None, finished=None) -> tuple[str, int]:
    cid = lc.new_conversation(project, provider="claude")["id"]
    conn = _conn(project)
    try:
        t = db.add_conversation_turn(conn, conversation_id=cid, ts=ts or _iso(-30), user_text=text,
                                     owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()),
                                     log_path=str(project / ".tagteam" / "conversations" / cid / "1.log"))
        if status != "running":
            db.finish_conversation_turn(conn, cid, t["n"], status=status, ts=finished or _iso(-20), error=error)
    finally:
        conn.close()
    return cid, t["n"]


def _seed_gate(project: Path, *, status="pass", stem="feat-x_plan_r1_gate_x") -> int:
    conn = _conn(project)
    try:
        claimed = db.claim_gate(conn, ts=_iso(-50), phase="feat-x", cycle_type="impl", round_=1,
                                submission_seq=1, event_key="k1", kind="auto",
                                runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))
        gid = claimed[0]
        if status != "running":
            db.finish_gate(conn, gid, status=status, ts=_iso(-40), duration_s=9.5, stem=stem, reason="tests ok")
        else:
            db.update_gate(conn, gid, ts=_iso(-49), stem=stem)
        return gid
    finally:
        conn.close()


def _seed_panel(project: Path, *, status="merged", stem="feat-x_plan_r1_panel_x") -> int:
    conn = _conn(project)
    try:
        claimed = db.claim_panel(conn, ts=_iso(-45), phase="feat-x", cycle_type="plan", round_=1,
                                 submission_seq=1, event_key="p1", kind="auto",
                                 runner_pid=os.getpid(), runner_ident=procs.identity(os.getpid()))
        pid = claimed[0]
        if status != "running":
            db.finish_panel(conn, pid, status=status, ts=_iso(-35), duration_s=20.0, stem=stem,
                            decision="APPROVE")
        return pid
    finally:
        conn.close()


def _seed_launch(project: Path, *, status="pending", intent=None, cid=None, turn_n=None,
                 created=None, finished=None, error=None, key=None) -> str:
    intent = intent or {"phase": "gamma-work", "type": "plan", "command": "/handoff start gamma-work",
                        "observed": {"seq": None, "phase": None, "type": None, "round": None, "state": None}}
    key = key or L.launch_key(intent)
    conn = _conn(project)
    try:
        db.claim_launch(conn, key=key, ts=created or _iso(-10), intent_json=json.dumps(intent),
                        owner_pid=os.getpid(), owner_ident=procs.identity(os.getpid()))
        fields = {"status": status}
        if cid is not None:
            fields.update(conversation_id=cid, turn_n=turn_n)
        if finished:
            fields["finished_at"] = finished
        if error:
            fields["error"] = error
        db.update_launch(conn, key, ts=_iso(-9), **fields)
    finally:
        conn.close()
    return key


def _marker(project: Path, **over) -> dict:
    m = {"kind": "cycle", "role": "reviewer", "agent": "Codex", "provider": "codex",
         "phase": "feat-x", "type": "plan", "round": 2, "stem": "feat-x_plan_r2_reviewer_now",
         "log_path": str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.log"),
         "events_path": str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.events.jsonl"),
         "started_at": _iso(-5), "pid": os.getpid(), "watcher_pid": os.getpid(), "owner_token": "t"}
    m.update(over)
    h.turns_dir(project).mkdir(parents=True, exist_ok=True)
    h.inflight_path(project).write_text(json.dumps(m))
    return m


# ---------------------------------------------------------------------------
# outcome vocabulary
# ---------------------------------------------------------------------------

class TestOutcomes:
    def test_vocabulary_is_exactly_seven(self):
        assert capi.OUTCOMES == ("running", "finished", "cancelled", "failed", "timed_out",
                                 "process_gone", "orphaned")

    @pytest.mark.parametrize("raw,expect", [
        ("ok", "finished"), ("running", "running"), ("cancelled", "cancelled"),
        ("timeout", "timed_out"), ("nonzero_exit", "failed"), ("no_round", "failed"),
        ("spawn_failed", "failed"), ("failed", "failed"), ("error", "failed"),
        ("pass", "finished"), ("bounce", "finished"), ("merged", "finished"), ("fallback", "finished"),
        ("superseded", "finished"), ("abandoned", "orphaned"), ("pending", "running"),
        ("succeeded", "finished"), ("partial", "finished"), ("something-new", "failed"), (None, "failed"),
    ])
    def test_normalize_table(self, raw, expect):
        assert capi.normalize_outcome(raw) == expect

    def test_normalize_liveness_and_orphan_error(self):
        assert capi.normalize_outcome("running", pid_alive=False) == "process_gone"
        assert capi.normalize_outcome("running", pid_alive=True) == "running"
        assert capi.normalize_outcome("failed", error="orphaned at t (owner process gone)") == "orphaned"
        assert capi.normalize_outcome("failed", error="aborted before running: x") == "failed"


# ---------------------------------------------------------------------------
# activity_payload
# ---------------------------------------------------------------------------

class TestActivity:
    def test_empty_project(self, project):
        a = capi.activity_payload(project)
        assert a == {"items": [], "truncated": False, "limit": capi.ACTIVITY_DEFAULT_LIMIT}

    def test_every_source_is_represented_and_normalised(self, project):
        _seed_usage(project, log_path=str(h.turns_dir(project) / "feat-x_plan_r1_lead_x.log"))   # cycle turn, finished
        _seed_usage(project, ts=_iso(-55), role="reviewer", agent="Codex", status="timeout",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r1_reviewer_y.log"))
        _seed_usage(project, ts=_iso(-54), role="reviewer", agent="Codex", status="ok", kind="panel:scope",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r1_reviewer_lens.log"))
        _seed_usage(project, ts=_iso(-53), role="briefer", agent="briefer", status="ok", kind="briefer")
        _seed_usage(project, ts=_iso(-52), role="lead", status="ok", kind="conversation")   # skipped (conversation_turns is the source)
        cid, n = _seed_conversation_turn(project, status="ok", text="brainstorm the plan\nmore")
        _seed_conversation_turn(project, status="failed", error="orphaned at t (owner process gone)", ts=_iso(-25))
        _seed_gate(project, status="bounce")
        _seed_panel(project, status="merged")
        _seed_launch(project, status="failed", error="lead turn failed: boom", finished=_iso(-8))
        a = capi.activity_payload(project)
        items = a["items"]
        by_kind = {}
        for it in items:
            by_kind.setdefault(it["kind"], []).append(it)
        assert set(by_kind) == {"cycle", "panel_lens", "briefer", "conversation", "gate", "panel", "launch"}
        assert not any(it["source"] == "usage" and it["raw_status"] == "ok" and it["kind"] == "conversation" for it in items)
        cyc = sorted(by_kind["cycle"], key=lambda i: i["role"])
        assert [c["status"] for c in cyc] == ["finished", "timed_out"]
        assert cyc[0]["ref"] == {"log": "feat-x_plan_r1_lead_x"} and cyc[0]["stem"] == "feat-x_plan_r1_lead_x"
        assert cyc[0]["id"] == "turn:feat-x_plan_r1_lead_x" and by_kind["gate"][0]["id"] == "turn:feat-x_plan_r1_gate_x"
        assert cyc[0]["duration_ms"] == 1500 and cyc[0]["started_at"] < cyc[0]["ended_at"]
        assert by_kind["panel_lens"][0]["detail"] == "scope"
        assert by_kind["briefer"][0]["role"] == "briefer"
        convs = sorted(by_kind["conversation"], key=lambda i: i["status"])
        assert [c["status"] for c in convs] == ["finished", "orphaned"]
        ok = [c for c in convs if c["status"] == "finished"][0]
        assert ok["ref"] == {"conversation": cid, "turn": n} and ok["detail"] == "brainstorm the plan"
        assert by_kind["gate"][0]["status"] == "finished" and by_kind["gate"][0]["raw_status"] == "bounce"
        assert by_kind["gate"][0]["detail"].startswith("bounce") and by_kind["gate"][0]["duration_ms"] == 9500
        assert by_kind["panel"][0]["status"] == "finished" and "APPROVE" in by_kind["panel"][0]["detail"]
        assert by_kind["launch"][0]["status"] == "failed" and "/handoff start gamma-work" in by_kind["launch"][0]["detail"]
        # every item has the documented shape and a vocabulary status
        for it in items:
            assert set(it) >= {"id", "source", "kind", "role", "agent", "phase", "type", "round", "status",
                               "raw_status", "started_at", "ended_at", "duration_ms", "log_path", "stem",
                               "detail", "ref", "pid_alive", "age_s"}
            assert it["status"] in capi.OUTCOMES
        # newest first
        starts = [it["started_at"] or "" for it in items]
        assert starts == sorted(starts, reverse=True)
        assert len({it["id"] for it in items}) == len(items)

    def test_succeeded_launches_are_not_activity(self, project):
        _seed_launch(project, status="succeeded", finished=_iso(-1))
        assert capi.activity_payload(project)["items"] == []

    def test_a_launch_that_reached_its_turn_is_that_conversation_row(self, project):
        cid, n = _seed_conversation_turn(project, status="running", text="/handoff start gamma-work")
        _seed_launch(project, status="pending", cid=cid, turn_n=n)
        items = capi.activity_payload(project)["items"]
        assert [i["kind"] for i in items] == ["conversation"]
        # a launch that never got a turn stays visible (it is the only record of the attempt)
        _seed_launch(project, status="failed", error="orphaned: the launching process died", finished=_iso(-1),
                     key="other-key")
        kinds = sorted(i["kind"] for i in capi.activity_payload(project)["items"])
        assert kinds == ["conversation", "launch"]

    def test_inflight_marker_becomes_running_row_or_marks_the_matching_row(self, project):
        _seed_usage(project)
        _marker(project)                                        # a reviewer cycle turn, live pid
        a = capi.activity_payload(project)
        top = a["items"][0]
        assert top["source"] == "inflight" and top["status"] == "running" and top["kind"] == "cycle"
        assert top["role"] == "reviewer" and top["agent"] == "Codex" and top["round"] == 2
        assert top["ref"] == {"log": "feat-x_plan_r2_reviewer_now"} and top["pid_alive"] is True
        assert top["id"] == "turn:feat-x_plan_r2_reviewer_now"
        # dead pid → process_gone (never "running" by inference)
        _marker(project, pid=999999)
        assert capi.activity_payload(project)["items"][0]["status"] == "process_gone"
        # claimed-not-spawned (pid None) is running, not gone
        _marker(project, pid=None)
        assert capi.activity_payload(project)["items"][0]["status"] == "running"
        # a running gate row with the same stem is marked, not duplicated
        h.inflight_path(project).unlink()
        _seed_gate(project, status="running", stem="gate-stem-1")
        _marker(project, kind="gate", role="gatekeeper", agent="gate", stem="gate-stem-1", round=1)
        items = capi.activity_payload(project)["items"]
        gates = [i for i in items if i["kind"] == "gate"]
        assert len(gates) == 1 and gates[0]["status"] == "running" and gates[0]["source"] == "gate"
        assert not any(i["source"] == "inflight" for i in items)
        # a running conversation turn is marked through its conversation ref
        h.inflight_path(project).unlink()
        cid, n = _seed_conversation_turn(project, status="running")
        _marker(project, kind="conversation", role="lead", agent="Claude", stem="conv-stem",
                conversation_id=cid, turn_n=n, round=None)
        items = capi.activity_payload(project)["items"]
        convs = [i for i in items if i["kind"] == "conversation"]
        assert len(convs) == 1 and convs[0]["status"] == "running" and convs[0]["source"] == "conversation"
        assert convs[0]["pid_alive"] is True and items[0] is convs[0]     # running rows sort first
        # once the engine records a stem-bearing turn, the SAME id carries the outcome (one row, patched)
        h.inflight_path(project).unlink()
        _marker(project)
        assert capi.activity_payload(project)["items"][0]["id"] == "turn:feat-x_plan_r2_reviewer_now"
        _seed_usage(project, ts=_iso(), role="reviewer", agent="Codex", round=2, status="ok",
                    log_path=str(h.turns_dir(project) / "feat-x_plan_r2_reviewer_now.log"))
        same = [i for i in capi.activity_payload(project)["items"] if i["id"] == "turn:feat-x_plan_r2_reviewer_now"]
        assert len(same) == 1 and same[0]["status"] == "finished" and same[0]["source"] == "usage"   # a lingering marker: the record wins
        h.inflight_path(project).unlink()
        same = [i for i in capi.activity_payload(project)["items"] if i["id"] == "turn:feat-x_plan_r2_reviewer_now"]
        assert len(same) == 1 and same[0]["status"] == "finished"

    def test_cap_and_truncated(self, project):
        for i in range(12):
            _seed_usage(project, ts=_iso(-100 + i))
        a = capi.activity_payload(project, limit=5)
        assert len(a["items"]) == 5 and a["truncated"] is True and a["limit"] == 5
        assert capi.activity_payload(project, limit=10 ** 9)["limit"] == capi.ACTIVITY_MAX_LIMIT
        assert capi.activity_payload(project, limit="x")["limit"] == capi.ACTIVITY_DEFAULT_LIMIT

    def test_last_turn_skips_running_and_launches(self, project):
        assert capi.last_turn([]) is None
        _seed_launch(project, status="failed", error="x", finished=_iso(-1))
        _seed_usage(project, ts=_iso(-3), status="cancelled")
        _seed_usage(project, ts=_iso(-30), status="ok")
        _marker(project)
        items = capi.activity_payload(project)["items"]
        lt = capi.last_turn(items)
        assert lt["kind"] == "cycle" and lt["status"] == "cancelled"


# ---------------------------------------------------------------------------
# now_payload keys + launch_view
# ---------------------------------------------------------------------------

class TestNowKeys:
    def test_now_carries_turn_kind_launch_last_turn(self, project):
        n = capi.now_payload(project)
        assert n["turn_kind"] is None and n["launch"] is None and n["last_turn"] is None
        _seed_usage(project, status="ok")
        _marker(project, kind="conversation", role="lead", agent="Claude")
        n = capi.now_payload(project)
        assert n["turn_kind"] == "conversation"
        assert n["last_turn"]["kind"] == "cycle" and n["last_turn"]["status"] == "finished"
        # a legacy marker without `kind` is a cycle turn
        _marker(project, kind=None)
        m = h.read_inflight(project); m.pop("kind"); h.inflight_path(project).write_text(json.dumps(m))
        assert capi.now_payload(project)["turn_kind"] == "cycle"

    def test_launch_view_pending_follows_the_turn(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        assert it["command"] == "/handoff start gamma-work"
        cid, n = _seed_conversation_turn(project, status="running", text=it["command"])
        _seed_launch(project, status="pending", intent=it, cid=cid, turn_n=n)
        lv = capi.launch_view(project)
        assert lv["status"] == "pending" and lv["command"] == it["command"] and lv["conversation_id"] == cid
        assert lv["phase"] == "gamma-work" and lv["age_s"] is not None
        # the turn fails → the launch is failed (with the turn's error), for the current intent
        conn = _conn(project)
        try:
            db.finish_conversation_turn(conn, cid, n, status="failed", ts=_iso(), error="boom")
        finally:
            conn.close()
        lv = capi.launch_view(project)
        assert lv["status"] == "failed" and "lead turn failed: boom" in lv["error"]
        # the turn succeeds → nothing to acknowledge
        conn = _conn(project)
        try:
            conn.execute("UPDATE conversation_turns SET status='ok', error=NULL"); conn.commit()
        finally:
            conn.close()
        assert capi.launch_view(project) is None

    def test_launch_view_failed_only_recent_and_for_current_intent(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        _seed_launch(project, status="failed", intent=it, error="lead turn cancelled", finished=_iso(-100))
        assert capi.launch_view(project)["status"] == "failed"
        # stale (>24h) → hidden
        conn = _conn(project)
        try:
            conn.execute("UPDATE launches SET finished_at=?, updated_at=?, created_at=?",
                         (_iso(-90000), _iso(-90000), _iso(-90000))); conn.commit()
        finally:
            conn.close()
        assert capi.launch_view(project) is None
        # a failed launch for a DIFFERENT intent (state moved on) → hidden
        other = dict(it, command="/handoff start other", observed=dict(it["observed"], seq=99))
        _seed_launch(project, status="failed", intent=other, error="x", finished=_iso(-5))
        assert capi.launch_view(project) is None

    def test_launch_view_orphaned_pending_row(self, project):
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "roadmap.md").write_text("# R\n### Phase 1: Gamma Work\n- **Status:** Not started\n")
        it = L.launch_intent(project)
        key = _seed_launch(project, status="pending", intent=it)
        conn = _conn(project)
        try:
            db.update_launch(conn, key, ts=_iso(), owner_pid=999999, owner_ident="dead:ident")
        finally:
            conn.close()
        lv = capi.launch_view(project)
        assert lv["status"] == "failed" and "orphaned" in lv["error"]


# ---------------------------------------------------------------------------
# events_signature: new sources
# ---------------------------------------------------------------------------

class TestSignature:
    def test_conversation_turn_and_launch_and_log_growth_change_the_signature(self, project):
        _init_cycle(project)
        i0 = capi.signature_id(capi.events_signature(project))
        cid, n = _seed_conversation_turn(project, status="running")
        s1 = capi.events_signature(project); i1 = capi.signature_id(s1)
        assert i1 != i0 and s1["conversation_turns"][1] == 1
        conn = _conn(project)
        try:
            db.finish_conversation_turn(conn, cid, n, status="ok", ts=_iso())
        finally:
            conn.close()
        i2 = capi.signature_id(capi.events_signature(project)); assert i2 != i1
        key = _seed_launch(project, status="pending")
        s3 = capi.events_signature(project); i3 = capi.signature_id(s3)
        assert i3 != i2 and s3["launches"][1] == 1
        conn = _conn(project)
        try:
            db.update_launch(conn, key, ts=_iso(), status="failed", finished_at=_iso(), error="x")
        finally:
            conn.close()
        i4 = capi.signature_id(capi.events_signature(project)); assert i4 != i3
        # unchanged → same id
        assert capi.signature_id(capi.events_signature(project)) == i4
        # in-flight log growth: coarse steps (LOG_SIGNAL_STEP), not per line
        m = _marker(project)
        log = Path(m["log_path"]); log.write_text("a\n")
        s5 = capi.events_signature(project); i5 = capi.signature_id(s5)
        assert i5 != i4 and s5["inflight"]["kind"] == "cycle" and s5["inflight"]["log_step"] == 0
        log.write_text("a\n" * 100)                            # < one step
        assert capi.signature_id(capi.events_signature(project)) == i5
        log.write_text("x" * (capi.LOG_SIGNAL_STEP + 10) + "\n")
        s6 = capi.events_signature(project); i6 = capi.signature_id(s6)
        assert i6 != i5 and s6["inflight"]["log_step"] == 1

    def test_signature_cost_stays_bounded(self, project):
        _init_cycle(project)
        for i in range(200):
            _seed_usage(project, ts=_iso(-1000 + i))
        for _ in range(20):
            _seed_conversation_turn(project, status="ok")
        t0 = time.perf_counter()
        for _ in range(20):
            capi.events_signature(project)
        per = (time.perf_counter() - t0) / 20
        assert per < 0.25, f"events_signature took {per:.3f}s"


# ---------------------------------------------------------------------------
# tail_payload(stem=) + turn_log_path
# ---------------------------------------------------------------------------

class TestTailStem:
    def test_stem_resolves_only_under_turns_dir(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "s1.log").write_text("\n".join(f"l{i}" for i in range(10)))
        (project / "secret.log").write_text("nope")
        assert capi.turn_log_path(project, "s1") == (d / "s1.log").resolve()
        for bad in ("../secret", "..", "a/b", "", None, "s1.log/../../secret"):
            assert capi.turn_log_path(project, bad) is None, bad
        p = capi.tail_payload(project, 3, stem="s1")
        assert p["lines"] == ["l7", "l8", "l9"] and p["stem"] == "s1" and p["inflight"] is False
        assert p["path"] == str((d / "s1.log").resolve())
        p = capi.tail_payload(project, 3, stem="missing")
        assert p["path"] is None and p["lines"] == [] and "no turn log" in p["message"]
        p = capi.tail_payload(project, 3, stem="../secret")
        assert p["path"] is None and p["lines"] == []
        # inflight flag reflects THIS stem
        _marker(project, stem="s1", log_path=str(d / "s1.log"))
        assert capi.tail_payload(project, 3, stem="s1")["inflight"] is True
        assert capi.tail_payload(project, 3, stem="other")["inflight"] is False


# ---------------------------------------------------------------------------
# server: /api/activity, the turn-log SSE, /api/tail?stem=
# ---------------------------------------------------------------------------

class TestServer:
    def test_activity_endpoint_shape_and_legacy_404(self, project):
        _seed_usage(project)
        with Served(project, mode="cockpit") as s:
            r = s.client.get("/api/activity")
            assert r["status"] == 200 and r["json"]["items"][0]["kind"] == "cycle"
            assert r["json"]["truncated"] is False
            r = s.client.get("/api/activity?limit=1")
            assert r["json"]["limit"] == 1
            n = s.client.get("/api/now")["json"]
            assert "turn_kind" in n and "launch" in n and "last_turn" in n
        with Served(project, mode="legacy") as s:
            assert s.client.get("/api/activity").get("status") == 404
            assert s.client.get("/api/activity/log/x/events").get("status") == 404

    def test_tail_stem_query(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "s1.log").write_text("a\nb\nc\n")
        with Served(project, mode="cockpit") as s:
            r = s.client.get("/api/tail?stem=s1&lines=2")
            assert r["json"]["lines"] == ["b", "c"] and r["json"]["stem"] == "s1"
            r = s.client.get("/api/tail?stem=..%2Fs1&lines=2")
            assert r["json"]["path"] is None

    def test_log_sse_streams_replays_and_ends(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        log = d / "run1.log"
        log.write_text("first\nsecond\n")
        m = _marker(project, stem="run1", log_path=str(log))
        with Served(project, mode="cockpit") as s:
            rd = SSEReader(s.port, path="/api/activity/log/run1/events")
            rd.wait_frames(2, timeout=5)
            assert rd.status == 200
            assert [f["data"]["text"] for f in rd.frames[:2]] == ["first", "second"]
            off2 = int(rd.frames[1]["id"])
            assert off2 == len("first\nsecond\n")
            # live follow: an appended line arrives with the next offset as its id
            with open(log, "a") as f:
                f.write("third\n")
            rd.wait_frames(3, timeout=5)
            assert rd.frames[2]["data"]["text"] == "third" and int(rd.frames[2]["id"]) == off2 + len("third\n")
            # a partial line is held back while the writer lives …
            with open(log, "a") as f:
                f.write("part")
            rd.pump(1.0)
            assert len(rd.frames) == 3
            # … and flushed as the final line once the marker is gone, then `end`
            h.inflight_path(project).unlink()
            rd.wait_frames(5, timeout=5)
            assert rd.frames[3]["data"]["text"] == "part" and rd.frames[3]["event"] == "line"
            assert rd.frames[4]["event"] == "end" and rd.frames[4]["data"]["stem"] == "run1"
            assert rd.frames[4]["id"].endswith(":end")
            rd.close()
            # replay from a byte offset (Last-Event-ID) — only what follows it, then end
            rd2 = SSEReader(s.port, headers={"Last-Event-ID": str(off2)}, path="/api/activity/log/run1/events")
            rd2.wait_frames(3, timeout=5)
            texts = [f["data"].get("text") for f in rd2.frames if f["event"] == "line"]
            assert texts == ["third", "part"] and rd2.frames[-1]["event"] == "end"
            rd2.close()
            # ?after= with the `:end` id form replays nothing new
            rd3 = SSEReader(s.port, path=f"/api/activity/log/run1/events?after={rd.frames[4]['id']}")
            rd3.wait_frames(1, timeout=5)
            assert rd3.frames[0]["event"] == "end"
            rd3.close()
            # a finished stem streams its whole log then ends (no marker at all)
            (d / "done.log").write_text("x\ny\n")
            rd4 = SSEReader(s.port, path="/api/activity/log/done/events")
            rd4.wait_frames(3, timeout=5)
            assert [f["event"] for f in rd4.frames] == ["line", "line", "end"]
            rd4.close()

    def test_log_sse_validation_and_cap(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "ok.log").write_text("x\n")
        with Served(project, mode="cockpit", max_sse=1) as s:
            assert s.client.get("/api/activity/log/..%2Fok/events")["status"] == 400
            assert s.client.get("/api/activity/log/a%2Fb/events")["status"] == 400
            assert s.client.get("/api/activity/log/nope/events")["status"] == 404
            rd = SSEReader(s.port, path="/api/activity/log/ok/events")
            rd.wait_frames(1, timeout=5)
            assert rd.status == 200
            # (the stream ended: no marker → drained → end; the slot is released)
            rd.wait_frames(2, timeout=5); rd.close()
            time.sleep(0.3)
            _marker(project, stem="ok", log_path=str(d / "ok.log"))
            rd2 = SSEReader(s.port, path="/api/activity/log/ok/events")
            rd2.wait_frames(1, timeout=5)
            assert rd2.status == 200
            r = s.client.get("/api/events")
            assert r["status"] == 503                                   # the shared cap counts this stream
            rd2.close()

    def test_lead_sse_still_replays_and_ends_after_the_shared_poller(self, tmp_path, chat_fake_path, monkeypatch):
        # the Phase 37 conversation stream rides the same poller: replay + end unchanged
        from tagteam.config import read_config
        from tests.test_lead_chat import _project as _chat_project
        monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
        project = _chat_project(tmp_path)
        cfg = read_config(project / "tagteam.yaml")
        cid = lc.new_conversation(project, provider="claude")["id"]
        t = lc.send(project, cid, "hi", config=cfg)
        assert t["status"] == "ok"
        with Served(project, mode="cockpit") as s:
            rd = SSEReader(s.port, path=f"/api/lead/{cid}/events")
            rd.wait_frames(1, timeout=5)
            rd.pump(1.0)
            assert rd.frames[-1]["event"] == "end" and rd.frames[-1]["data"]["status"] == "ok"
            last = rd.frames[-1]["id"]
            rd.close()
            rd2 = SSEReader(s.port, headers={"Last-Event-ID": last}, path=f"/api/lead/{cid}/events")
            rd2.pump(1.5)
            assert rd2.frames == [] and rd2.status == 200
            rd2.close()


# ---------------------------------------------------------------------------
# source guards on the shipped cockpit
# ---------------------------------------------------------------------------

class TestSourceGuards:
    def test_html_has_the_cycle_region_and_no_tail_drawer(self):
        html = (WEB / "cockpit.html").read_text(encoding="utf-8")
        for id_ in ("cycle", "lane-lead", "lane-reviewer", "lane-token", "cycle-line", "activity",
                    "activity-empty", "chip-owed", "chip-inflight", "chip-watcher", "now-version", "tab-lead-name"):
            assert f'id="{id_}"' in html, id_
        assert "btn-tail" not in html and "tail-drawer" not in html and "btn-cancel-turn" not in html
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        assert "btn-tail" not in js and "tail-drawer" not in js
        # UX pass 2026-08-17: one Start (the cockpit's own engine); no terminals from the page;
        # the tab for the lead is named after the lead; Feed reads "Rounds"
        assert "Launch terminals" not in js and "/api/session/start" not in js and "Start headless" not in js
        assert ">Rounds<" in html and 'id="tab-lead-name"' in html
        assert 'name="tagteam-version"' not in html          # injected by the server, not shipped

    def test_cycle_activity_block_builds_dom_with_text_nodes_only(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        start = js.index("Phase 43: Cycle region + Activity log")
        end = js.index("Phase 37: Lead panel")
        block = js[start:end]
        assert "innerHTML" not in block, "the Cycle/Activity block must build DOM with createElement/textContent only"
        # rows are keyed and patched — the container is never wiped
        assert "ACT.rows[it.id]" in block and "$('activity').innerHTML" not in js
        assert "function upsertActivity" in block and "function patchActRow" in block

    def test_outcome_label_lists_exactly_the_seven_outcomes(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        m = re.search(r"var OUTCOME_LABEL = \{([^}]*)\};", js)
        assert m, "OUTCOME_LABEL missing"
        keys = re.findall(r"(\w+):\s*'", m.group(1))
        assert keys == list(capi.OUTCOMES), keys

    def test_lead_panel_keeps_lines_and_names_the_cycle_turn(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        lead_block = js[js.index("Phase 37: Lead panel"):js.index("Live connection: SSE with polling fallback")]
        assert "details" in lead_block and "activity (" in lead_block          # retained lines disclosure
        assert "watch it above" in lead_block
        assert "innerHTML" not in lead_block.replace("innerHTML = ''", "").replace("innerHTML=''", "")

    def test_css_has_the_lane_and_outcome_styles(self):
        css = (WEB / "cockpit.css").read_text(encoding="utf-8")
        for sel in (".lane.running", ".token.lead", ".token.reviewer", ".act-row.s-running",
                    ".act-row.s-cancelled", ".act-row.s-process_gone", ".act-lines"):
            assert sel in css, sel
        assert ".tail-drawer" not in css


# ---------------------------------------------------------------------------
# behavioural: the real Activity-log code under a minimal DOM stub (node)
# ---------------------------------------------------------------------------

_DOM_STUB = r"""
// Minimal DOM: enough for the Cycle/Activity block (createElement/textContent,
// classList, dataset, appendChild/insertBefore/children, addEventListener).
function Node(tag) {
  this.tagName = tag; this.children = []; this.parentNode = null; this.textContent = '';
  this.dataset = {}; this._cls = []; this.style = {}; this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
  var self = this;
  this.classList = {
    add: function (c) { if (self._cls.indexOf(c) < 0) self._cls.push(c); },
    remove: function (c) { self._cls = self._cls.filter(function (x) { return x !== c; }); },
    toggle: function (c, force) { var has = self._cls.indexOf(c) >= 0; var want = (force === undefined) ? !has : !!force; if (want && !has) self._cls.push(c); if (!want && has) self.classList.remove(c); return want; },
    contains: function (c) { return self._cls.indexOf(c) >= 0; }
  };
}
Object.defineProperty(Node.prototype, 'className', { get: function () { return this._cls.join(' '); }, set: function (v) { this._cls = String(v || '').split(/\s+/).filter(Boolean); } });
Object.defineProperty(Node.prototype, 'firstChild', { get: function () { return this.children[0] || null; } });
Node.prototype.appendChild = function (n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.children.push(n); return n; };
Node.prototype.insertBefore = function (n, ref) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; var i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); return n; };
Node.prototype.removeChild = function (n) { var i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); n.parentNode = null; return n; };
Node.prototype.addEventListener = function () {};
Node.prototype.querySelector = function () { return null; };
Node.prototype.scrollIntoView = function () {};
var BY_ID = {};
function el(tag, cls, text) { var e = new Node(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function $(id) { if (!BY_ID[id]) { BY_ID[id] = new Node('div'); BY_ID[id].id = id; } return BY_ID[id]; }
var document = { getElementById: $, createElement: function (t) { return new Node(t); }, querySelector: function () { return null; }, querySelectorAll: function () { return []; } };
var window = { EventSource: undefined };
var localStorage = { getItem: function () { return null; }, setItem: function () {} };
var console = { error: function () {}, log: function () {} };
function esc(s) { return String(s == null ? '' : s); }
function fmtAge(s) { return String(s) + 's'; }
function fmtTs(ts) { return String(ts || ''); }
function url(p) { return p; }
function getJSON() { return Promise.resolve({ ok: false }); }
function act() {}
function refreshAll() {}
function showTab() {}
function loadLead() { return Promise.resolve(); }
function leadStreamPath(cid) { return '/api/lead/' + cid + '/events'; }
var LEAD = { lines: {}, cursor: {} };
var NOW = null;
"""


def _run_activity_harness(js_body: str) -> dict:
    """Evaluate the real Phase 43 block from cockpit.js under the DOM stub and
    run `js_body` after it (which must set `RESULT`). Returns RESULT as JSON."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed — the behavioural activity-log test needs it")
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    block = js[js.index("// ---------- Phase 43: Cycle region + Activity log"):js.index("// ---------- Phase 37: Lead panel")]
    prog = _DOM_STUB + "\n" + block + "\n" + js_body + "\nprocess.stdout.write(JSON.stringify(RESULT));\n"
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.js"
        f.write_text(prog, encoding="utf-8")
        r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestActivityLogBehaviour:
    def test_running_to_terminal_reorders_without_rebuilding(self):
        """A long-running turn that ends must move below newer terminal rows
        (running first, then newest first) — moved, not rebuilt: same node,
        its lines intact, the container never wiped."""
        res = _run_activity_harness(r"""
var list = $('activity');
function item(id, status, started) { return { id: id, kind: 'cycle', role: 'lead', agent: 'A', status: status, started_at: started, ref: { log: id }, stem: id, age_s: 1, duration_ms: 1000 }; }
// t=10 a running turn; t=20 a newer finished turn → running sits on top
upsertActivity(item('turn:old', 'running', '2026-01-01T00:00:10+00:00'), list);
upsertActivity(item('turn:new', 'finished', '2026-01-01T00:00:20+00:00'), list);
var oldRow = ACT.rows['turn:old'].row;
appendActLine(ACT.rows['turn:old'], 'line one'); appendActLine(ACT.rows['turn:old'], 'line two');
var before = list.children.map(function (r) { return r.dataset.id; });
// the old turn ends → its record says finished (same id, same started_at)
upsertActivity(item('turn:old', 'finished', '2026-01-01T00:00:10+00:00'), list);
var after = list.children.map(function (r) { return r.dataset.id; });
// and a newer running turn appears → on top
upsertActivity(item('turn:run2', 'running', '2026-01-01T00:00:05+00:00'), list);
var after2 = list.children.map(function (r) { return r.dataset.id; });
// a terminal → running flip (a lingering marker re-claims a stem) moves it back up
upsertActivity(item('turn:new', 'running', '2026-01-01T00:00:20+00:00'), list);
var after3 = list.children.map(function (r) { return r.dataset.id; });
var RESULT = { before: before, after: after, after2: after2, after3: after3,
               sameNode: ACT.rows['turn:old'].row === oldRow, lines: ACT.rows['turn:old'].lines,
               boxKids: ACT.rows['turn:old'].box.children.length, oldStatus: ACT.rows['turn:old'].statusEl.textContent,
               oldKey: oldRow.dataset.key, rowsInDom: list.children.length, count: Object.keys(ACT.rows).length };
""")
        assert res["before"] == ["turn:old", "turn:new"]
        assert res["after"] == ["turn:new", "turn:old"], res            # running → terminal: moved below the newer terminal row
        assert res["after2"] == ["turn:run2", "turn:new", "turn:old"]   # a running row always sits on top
        assert res["after3"] == ["turn:new", "turn:run2", "turn:old"]   # terminal → running (newer started) moves up
        assert res["sameNode"] is True and res["lines"] == ["line one", "line two"] and res["boxKids"] == 2
        assert res["oldStatus"].startswith("done") and res["oldKey"].startswith("0|")
        assert res["rowsInDom"] == 3 and res["count"] == 3

    def test_source_guard_reinserts_on_key_change(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        start = js.index("function upsertActivity("); end = js.index("function insertActRow(")
        body = js[start:end]
        assert "actSortKey(it)" in body and "insertActRow(list, rec)" in body, \
            "an existing row must be re-inserted when its sort key changes"


class TestUxPassWords:
    """UX pass 2026-08-17: the page speaks the arbiter's words; the CLI's names
    live in tooltips and the confirm modal."""

    def test_outcome_labels_are_plain_words(self):
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        m = re.search(r"var OUTCOME_LABEL = \{([^}]*)\};", js)
        vals = dict(re.findall(r"(\w+):\s*'([^']*)'", m.group(1)))
        assert vals == {"running": "working", "finished": "done", "cancelled": "cancelled", "failed": "failed",
                        "timed_out": "timed out", "process_gone": "process disappeared",
                        "orphaned": "no result recorded"}

    def test_strip_and_cards_avoid_engine_jargon(self):
        """User-facing strings in the shipped JS must not use the engine's
        words for the primary path. (Tooltips / confirm bodies may name the CLI.)"""
        js = (WEB / "cockpit.js").read_text(encoding="utf-8")
        html = (WEB / "cockpit.html").read_text(encoding="utf-8")
        # visible labels that must be gone
        for bad in ("in flight:", "no turn owed", "· owed ", "owed to ", "Start headless", "no watcher", "Interject</button>",
                    "Dispatch is on hold", "nothing is dispatching", "In-flight pointer", "Talk to the lead"):
            assert bad not in js and bad not in html, bad
        # and the words that replace them
        for good in ("is working", "waiting on", "watcher: on", "watcher: off", "Turn on", "Turns are paused",
                     "Chat with", "Leave note", "Rounds"):
            assert good in js or good in html, good

    def test_version_meta_is_injected_in_cockpit_mode(self, project):
        from tagteam import __version__
        with Served(project, mode="cockpit") as s:
            html = s.client.get("/")["raw"].decode()
            assert f'<meta name="tagteam-version" content="{__version__}">' in html
        with Served(project, mode="legacy") as s:
            assert "tagteam-version" not in s.client.get("/")["raw"].decode()
