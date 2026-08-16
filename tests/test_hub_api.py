"""Phase 35 tests: `tagteam.hub_api` — registry classification, read-only
readers (never create / never migrate / WAL reader vs writer), per-project
summaries and error isolation, ranking (needs_you / waiting / quiet, stale ⊃
abandoned), aggregate usage windows, per-(provider, kind) shared rate limits,
the exhaustive SSE signature, and text rendering."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import controls, db, headless as h, hub_api, procs, registry
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod
from tagteam import watcher as watcher_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401
from tests.test_controls import needs_proc_inspection  # noqa: F401

# pytest's tmp dirs live under /tmp or /private/var/folders — the hub's
# default scratch filter would hide every fixture. Tests disable it and
# cover the filter explicitly.
NO_SCRATCH: tuple[str, ...] = ()

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
YAML = "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"


def _mk(tmp_path: Path, name: str, *, yaml=True, state: dict | None = None) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    if yaml:
        (d / "tagteam.yaml").write_text(YAML)
    if state is not None:
        (d / "handoff-state.json").write_text(json.dumps(state))
    return d


def _state(phase="p", ctype="plan", rnd=1, turn="reviewer", status="ready", result=None, age_s=60):
    return {"phase": phase, "type": ctype, "round": rnd, "turn": turn, "status": status,
            "result": result, "updated_at": (NOW - timedelta(seconds=age_s)).isoformat(), "seq": 1}


def _cycle_status(d: Path, phase, ctype, state):
    hd = d / "docs" / "handoffs"; hd.mkdir(parents=True, exist_ok=True)
    (hd / f"{phase}_{ctype}_status.json").write_text(json.dumps({"state": state, "round": 1, "phase": phase, "type": ctype}))
    (hd / f"{phase}_{ctype}_rounds.jsonl").write_text(json.dumps({"round": 1, "role": "reviewer", "action": "ESCALATE", "content": "x", "ts": "2026-08-16T11:00:00+00:00"}) + "\n")


# ---------------------------------------------------------------------------
# hard rules
# ---------------------------------------------------------------------------

class TestHardRules:
    def test_no_migrating_calls_in_hub_api(self):
        """The hub must never call anything that connects+migrates another
        project's DB or prunes the registry — checked on the AST (calls),
        so docstrings mentioning the names don't count."""
        import ast
        tree = ast.parse(Path(hub_api.__file__).read_text(encoding="utf-8"))
        banned = {"connect", "now_payload", "read_status", "read_rounds", "event_for_cycle",
                  "get_registered_projects", "tail_rounds"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if name in banned:
                    # sqlite3.connect(...) inside read_only_connect is the ONE allowed connect
                    if name == "connect" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "sqlite3":
                        continue
                    offenders.append(f"{name} @ line {node.lineno}")
        assert offenders == [], offenders

    def test_read_only_connect_never_creates_or_migrates(self, tmp_path):
        d = _mk(tmp_path, "a")
        assert hub_api.read_only_connect(d) is None
        assert not (d / ".tagteam").exists()                       # absent stays absent
        p = hub_api.project_summary(d, now=NOW)
        assert p["error"] is None and not (d / ".tagteam").exists()
        # v3 / v4 / v5 DBs keep their user_version byte-for-byte
        for ver, ddls in ((3, (db._SCHEMA_V1, db._SCHEMA_V3)),
                          (4, (db._SCHEMA_V1, db._SCHEMA_V3, db._SCHEMA_V4)),
                          (5, (db._SCHEMA_V1, db._SCHEMA_V3, db._SCHEMA_V4, db._SCHEMA_V5))):
            e = _mk(tmp_path, f"v{ver}", state=_state())
            dbp = e / ".tagteam" / "tagteam.db"; dbp.parent.mkdir()
            raw = sqlite3.connect(dbp)
            for ddl in ddls:
                raw.executescript(ddl)
            raw.execute(f"PRAGMA user_version = {ver}"); raw.commit(); raw.close()
            before = dbp.read_bytes()
            s = hub_api.project_summary(e, now=NOW)
            assert s["error"] is None
            hub_api.aggregate_usage([str(e)], now=NOW); hub_api.shared_rate_limits([str(e)])
            assert dbp.read_bytes() == before, ver
            c = sqlite3.connect(dbp); assert c.execute("PRAGMA user_version").fetchone()[0] == ver; c.close()
        # read-only really is read-only
        conn = hub_api.read_only_connect(tmp_path / "v5")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO usage (ts, status) VALUES ('t','ok')")
        conn.close()

    def test_corrupt_db_is_a_row_error_not_a_raise(self, tmp_path):
        d = _mk(tmp_path, "bad", state=_state())
        (d / ".tagteam").mkdir(); (d / ".tagteam" / "tagteam.db").write_bytes(b"not a database at all" * 10)
        s = hub_api.project_summary(d, now=NOW)
        assert s["phase"] == "p"                                    # state still read
        assert s["error"] and "db:" in s["error"]                   # corruption is visible
        p = hub_api.hub_payload([str(d)], now=NOW, scratch_prefixes=NO_SCRATCH)
        assert p["totals"]["projects"] == 1
        row = [r for g in ("needs_you", "waiting", "quiet") for r in p["groups"][g]][0]
        assert row["error"] and row["group"] == "waiting"           # isolated: still classified from the state
        # aggregates skip the broken project, never raise
        assert hub_api.aggregate_usage([str(d)], now=NOW)["all"]["turns"] == 0
        assert hub_api.shared_rate_limits([str(d)]) == []

    def test_malformed_or_unreadable_state_is_a_row_error(self, tmp_path):
        d = _mk(tmp_path, "m")
        (d / "handoff-state.json").write_text("{not json")
        s = hub_api.project_summary(d, now=NOW)
        assert s["error"] and "malformed JSON" in s["error"] and s["state"] is None
        row = hub_api.classify_row(s)
        assert row["group"] == "quiet" and row["error"]              # never presented as healthy
        p = hub_api.hub_payload([str(d)], now=NOW, scratch_prefixes=NO_SCRATCH)
        assert p["groups"]["quiet"][0]["error"]
        (d / "handoff-state.json").write_text("[1, 2]")
        assert "expected a JSON object" in hub_api.project_summary(d, now=NOW)["error"]
        # malformed pause / inflight markers surface too, state still read
        (d / "handoff-state.json").write_text(json.dumps(_state()))
        h.turns_dir(d).mkdir(parents=True); h.inflight_path(d).write_text("{{")
        s = hub_api.project_summary(d, now=NOW)
        assert s["phase"] == "p" and s["error"] and "inflight.json" in s["error"]
        # the signature tolerates it (row error is a signal too) and does not raise
        sig = hub_api.hub_signature([str(d)], None, procs_snapshot=[])
        assert sig["projects"][str(d)]["inflight_file"] is not None
        (d / "handoff-state.json").write_text("{nope")
        sig = hub_api.hub_signature([str(d)], None, procs_snapshot=[])
        assert "state_error" in sig["projects"][str(d)]

    def test_old_schema_absence_stays_null_not_error(self, tmp_path):
        """A v3 DB has no `briefs` / `rate_limits` tables: that is expected
        absence (nulls), not an error."""
        e = _mk(tmp_path, "v3", state=_state(status="escalated", turn=None))
        _cycle_status(e, "p", "plan", "escalated")
        dbp = e / ".tagteam" / "tagteam.db"; dbp.parent.mkdir()
        raw = sqlite3.connect(dbp)
        raw.executescript(db._SCHEMA_V1); raw.executescript(db._SCHEMA_V3)
        raw.execute("PRAGMA user_version = 3"); raw.commit(); raw.close()
        s = hub_api.project_summary(e, now=NOW)
        assert s["error"] is None and s["brief_ready"] is False and s["brief_attempts"] == []
        assert s["usage"]["turns"] == 0
        assert hub_api.shared_rate_limits([str(e)]) == []

    def test_reader_does_not_block_on_writer_transaction(self, project):
        _init_cycle(project)
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="2026-08-16T11:59:00+00:00", status="ok", input_tokens=5, output_tokens=6)
        finally:
            conn.close()
        writer = db.connect(project_dir=str(project))
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO usage (ts, status, input_tokens) VALUES ('2026-08-16T11:59:30+00:00','ok', 1000)")
        try:
            t0 = time.monotonic()
            agg = hub_api.aggregate_usage([str(project)], now=NOW)
            assert time.monotonic() - t0 < 2.0
            assert agg["all"]["turns"] == 1 and agg["all"]["input_tokens"] == 5      # pre-transaction data
            s = hub_api.project_summary(project, now=NOW)
            assert s["error"] is None and s["usage"]["turns"] == 1
        finally:
            writer.rollback(); writer.close()


# ---------------------------------------------------------------------------
# classification / ranking
# ---------------------------------------------------------------------------

class TestClassify:
    def test_registry_classification(self, tmp_path):
        ok = _mk(tmp_path, "ok", state=_state())
        noyaml = _mk(tmp_path, "noyaml", yaml=False)
        legacy = _mk(tmp_path, "legacy", yaml=False, state=_state())
        missing = tmp_path / "gone"
        paths = [str(ok), str(noyaml), str(legacy), str(missing), "/private/tmp/x/scratch"]
        ent = {e["path"]: e for e in hub_api.classify_registry(paths, scratch_prefixes=NO_SCRATCH)}
        assert hub_api.classify_registry([str(ok)])[0]["kind"] in ("ok", "scratch")   # default filter applies to tmp dirs on some OSes
        assert hub_api.is_scratch_path("/private/tmp/x") and hub_api.is_scratch_path("/tmp/y/") and not hub_api.is_scratch_path("/Users/x/projects/y")
        assert ent[str(ok)]["kind"] == "ok" and not ent[str(ok)]["hidden"]
        assert ent[str(noyaml)]["kind"] == "no-yaml" and ent[str(noyaml)]["hidden"]
        assert ent[str(legacy)]["kind"] == "legacy" and not ent[str(legacy)]["hidden"]
        assert ent[str(missing)]["kind"] == "missing" and ent[str(missing)]["hidden"]
        assert ent["/private/tmp/x/scratch"]["kind"] in ("missing", "scratch") and ent["/private/tmp/x/scratch"]["hidden"]
        assert hub_api.project_id(str(ok)) == hub_api.project_id(str(ok)) and hub_api.project_id(str(ok)) != hub_api.project_id(str(legacy))
        assert re.match(r"^ok-[0-9a-f]{6}$", hub_api.project_id(str(ok)))

    def test_groups_and_ranking(self, tmp_path):
        esc = _mk(tmp_path, "esc", state=_state(status="escalated", turn=None, age_s=3600))
        _cycle_status(esc, "p", "plan", "escalated")
        q = _mk(tmp_path, "q", state=_state(status="ready", turn=None, age_s=100))
        _cycle_status(q, "p", "plan", "needs-human")
        fresh = _mk(tmp_path, "fresh", state=_state(age_s=120))
        stale = _mk(tmp_path, "stale", state=_state(age_s=31 * 60))
        boundary_lo = _mk(tmp_path, "b_lo", state=_state(age_s=30 * 60 - 1))
        boundary_hi = _mk(tmp_path, "b_hi", state=_state(age_s=30 * 60))
        gone = _mk(tmp_path, "gone", state=_state(age_s=25 * 3600))
        done = _mk(tmp_path, "done", state=_state(status="done", turn=None, result="approved", age_s=86400 * 3))
        nostate = _mk(tmp_path, "nostate")
        # a paused-after-failure project needs you
        failed = _mk(tmp_path, "failed", state=_state(age_s=600))
        h.write_pause(failed, {"reason": "headless turn timeout: x", "outcome": "timeout", "ts": (NOW - timedelta(seconds=300)).isoformat()})
        paths = [str(x) for x in (esc, q, fresh, stale, boundary_lo, boundary_hi, gone, done, nostate, failed)]
        p = hub_api.hub_payload(paths, now=NOW, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        g = p["groups"]
        assert [r["name"] for r in g["needs_you"]] == ["esc", "q", "failed"]
        assert g["needs_you"][0]["why"].startswith("escalated r1 · no brief")
        assert g["needs_you"][1]["why"].startswith("question")
        assert g["needs_you"][2]["why"].startswith("paused: timeout")
        w = {r["name"]: r for r in g["waiting"]}
        assert set(w) == {"fresh", "stale", "b_lo", "b_hi", "gone"}
        assert not w["fresh"]["stale"] and not w["b_lo"]["stale"]
        assert w["b_hi"]["stale"] and not w["b_hi"]["abandoned"]
        assert w["stale"]["stale"] and not w["stale"]["abandoned"]
        assert w["gone"]["stale"] and w["gone"]["abandoned"]
        assert [r["name"] for r in g["waiting"]][0] == "gone"                # abandoned first
        assert "tagteam watch" in w["gone"]["hint"]
        assert [r["name"] for r in g["quiet"]] == ["done", "nostate"] or [r["name"] for r in g["quiet"]] == ["nostate", "done"]
        assert p["totals"]["stale"] == 3 and p["totals"]["needs_you"] == 3

    def test_live_long_turn_is_not_stale_or_abandoned(self, tmp_path):
        d = _mk(tmp_path, "live", state=_state(age_s=25 * 3600))
        h.turns_dir(d).mkdir(parents=True)
        h.inflight_path(d).write_text(json.dumps({"stem": "s", "pid": os.getpid(), "role": "reviewer",
                                                  "started_at": (NOW - timedelta(hours=1)).isoformat()}))
        row = hub_api.classify_row(hub_api.project_summary(d, now=NOW, procs_snapshot=[]))
        assert row["group"] == "waiting" and row["live"] and not row["stale"] and not row["abandoned"]
        # a running watcher (pidfile) also keeps it live
        h.inflight_path(d).unlink()
        watcher_mod.write_pidfile(d, "headless")
        row = hub_api.classify_row(hub_api.project_summary(d, now=NOW, procs_snapshot=[]))
        assert row["live"] and not row["stale"]
        # dead pointer + no watcher → abandoned
        watcher_mod.pidfile_path(d).unlink()
        h.inflight_path(d).write_text(json.dumps({"stem": "s", "pid": 999999, "started_at": NOW.isoformat()}))
        row = hub_api.classify_row(hub_api.project_summary(d, now=NOW, procs_snapshot=[]))
        assert not row["live"] and row["stale"] and row["abandoned"]

    def test_brief_ready_flag_from_read_only_db(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project), updated_by="Codex")
        s = hub_api.project_summary(project, procs_snapshot=[])
        assert s["cycle_state"] == "escalated" and s["brief_ready"] is False and s["brief_attempts"] == []
        from tagteam import briefer
        ev, _ = briefer.event_for_cycle(project, "feat-x", "plan")
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1, cycle_state="escalated",
                                    event_key=ev.event_key, kind="manual", runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid, status="ok", ts="t2", content="## Positions", path="/p")
        finally:
            conn.close()
        s = hub_api.project_summary(project, procs_snapshot=[])
        assert s["brief_ready"] is True
        row = hub_api.classify_row(s)
        assert row["group"] == "needs_you" and "brief ready" in row["why"]

    def test_hidden_and_show_all(self, tmp_path):
        ok = _mk(tmp_path, "ok", state=_state())
        noyaml = _mk(tmp_path, "noyaml", yaml=False)
        paths = [str(ok), str(noyaml), str(tmp_path / "missing")]
        p = hub_api.hub_payload(paths, now=NOW, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        assert p["registry"] == {"total": 3, "visible": 1, "hidden": 2, "show_all": False}
        assert {x["kind"] for x in p["groups"]["hidden"]} == {"no-yaml", "missing"}
        p = hub_api.hub_payload(paths, now=NOW, show_all=True, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        assert p["registry"]["visible"] == 2                                # missing stays hidden (nothing to read)
        assert [x["kind"] for x in p["groups"]["hidden"]] == ["missing"]
        assert any(r["name"] == "noyaml" for r in p["groups"]["quiet"])


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------

class TestAggregates:
    def _usage(self, d: Path, rows):
        conn = db.connect(project_dir=str(d))
        try:
            for ts, i, o, c in rows:
                db.add_usage(conn, ts=ts, status="ok", input_tokens=i, output_tokens=o, cost_usd=c)
        finally:
            conn.close()

    def test_windows_sum_across_projects(self, tmp_path):
        a = _mk(tmp_path, "a", state=_state()); b = _mk(tmp_path, "b", state=_state()); c = _mk(tmp_path, "c", state=_state())
        self._usage(a, [((NOW - timedelta(hours=1)).isoformat(), 10, 1, 0.5),
                        ((NOW - timedelta(days=3)).isoformat(), 20, 2, None)])
        self._usage(b, [((NOW - timedelta(days=30)).isoformat(), 40, 4, 1.0)])
        agg = hub_api.aggregate_usage([str(a), str(b), str(c)], now=NOW)
        assert agg["24h"] == {"turns": 1, "input_tokens": 10, "output_tokens": 1, "cache_read_tokens": 0,
                              "cost_usd": 0.5, "priced_turns": 1, "projects": 1}
        assert agg["7d"]["turns"] == 2 and agg["7d"]["input_tokens"] == 30 and agg["7d"]["priced_turns"] == 1
        assert agg["all"]["turns"] == 3 and agg["all"]["input_tokens"] == 70 and agg["all"]["cost_usd"] == 1.5
        assert agg["all"]["projects"] == 2
        # matches `tagteam usage` totals summed
        from tagteam import usage as usage_mod
        tot = 0
        for d in (a, b):
            conn = db.connect(project_dir=str(d))
            try:
                tot += usage_mod.aggregate(db.get_usage(conn))["totals"]["input_tokens"]
            finally:
                conn.close()
        assert tot == agg["all"]["input_tokens"]

    def test_shared_rate_limits_per_kind_with_competing_timestamps(self, tmp_path):
        a = _mk(tmp_path, "a", state=_state()); b = _mk(tmp_path, "b", state=_state())
        ca = db.connect(project_dir=str(a)); cb = db.connect(project_dir=str(b))
        try:
            db.upsert_rate_limit(ca, provider="claude", kind="five_hour", status="allowed", resets_at="r1", payload=None, ts="2026-08-16T10:00:00+00:00")
            db.upsert_rate_limit(ca, provider="claude", kind="seven_day", status="allowed", resets_at="r2", payload=None, ts="2026-08-16T09:00:00+00:00")
            db.upsert_rate_limit(cb, provider="claude", kind="five_hour", status="allowed_warning", resets_at="r3", payload=None, ts="2026-08-16T09:30:00+00:00")
            db.upsert_rate_limit(cb, provider="claude", kind="seven_day", status="rejected", resets_at="r4", payload=None, ts="2026-08-16T11:00:00+00:00")
        finally:
            ca.close(); cb.close()
        rl = hub_api.shared_rate_limits([str(a), str(b)])
        by = {(r["provider"], r["kind"]): r for r in rl}
        assert by[("claude", "five_hour")]["project"] == str(a) and by[("claude", "five_hour")]["status"] == "allowed"
        assert by[("claude", "seven_day")]["project"] == str(b) and by[("claude", "seven_day")]["status"] == "rejected"
        # equal ts → earlier registry order wins
        cb = db.connect(project_dir=str(b))
        try:
            db.upsert_rate_limit(cb, provider="claude", kind="five_hour", status="tie", resets_at="r5", payload=None, ts="2026-08-16T10:00:00+00:00")
        finally:
            cb.close()
        rl = {r["kind"]: r for r in hub_api.shared_rate_limits([str(a), str(b)])}
        assert rl["five_hour"]["project"] == str(a)
        rl = {r["kind"]: r for r in hub_api.shared_rate_limits([str(b), str(a)])}
        assert rl["five_hour"]["project"] == str(b)

    def test_shared_window_reads_hidden_projects_too(self, tmp_path):
        """The subscription is one pool: the only/newest signal living in a
        HIDDEN registered project (scratch path, no tagteam.yaml) still wins."""
        shown = _mk(tmp_path, "shown", state=_state())
        hidden = _mk(tmp_path, "hidden", yaml=False)                # no yaml, no state → hidden
        cs = db.connect(project_dir=str(shown)); ch = db.connect(project_dir=str(hidden))
        try:
            db.upsert_rate_limit(ch, provider="claude", kind="five_hour", status="allowed_warning", resets_at="rh",
                                 payload=None, ts="2026-08-16T11:00:00+00:00")
            db.upsert_rate_limit(cs, provider="claude", kind="seven_day", status="allowed", resets_at="rs",
                                 payload=None, ts="2026-08-16T10:00:00+00:00")
        finally:
            cs.close(); ch.close()
        p = hub_api.hub_payload([str(shown), str(hidden)], now=NOW, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        assert p["registry"]["hidden"] == 1
        by = {r["kind"]: r for r in p["rate_limits"]}
        assert by["five_hour"]["project"] == str(hidden) and by["five_hour"]["status"] == "allowed_warning"
        assert by["seven_day"]["project"] == str(shown)
        # a scratch-prefixed hidden project counts as well
        p = hub_api.hub_payload([str(shown), str(hidden)], now=NOW, procs_snapshot=[],
                                scratch_prefixes=(str(hidden.parent) + "/hidden",))
        assert {r["kind"] for r in p["rate_limits"]} == {"five_hour", "seven_day"}
        # burn totals stay visibility-scoped (documented): hidden usage is not summed
        ch = db.connect(project_dir=str(hidden))
        try:
            db.add_usage(ch, ts=NOW.isoformat(), status="ok", input_tokens=999)
        finally:
            ch.close()
        p = hub_api.hub_payload([str(shown), str(hidden)], now=NOW, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        assert p["usage"]["all"]["input_tokens"] == 0


# ---------------------------------------------------------------------------
# signature
# ---------------------------------------------------------------------------

class TestSignature:
    def test_every_signal_fires(self, tmp_path, project):
        a = _mk(tmp_path, "a", state=_state())
        reg = tmp_path / "projects.json"; reg.write_text(json.dumps([str(a), str(project)]))
        paths = [str(a), str(project)]
        sid = lambda: hub_api.signature_id(hub_api.hub_signature(paths, reg, procs_snapshot=[]))
        s0 = sid()
        # state change
        (a / "handoff-state.json").write_text(json.dumps(_state(rnd=2))); s1 = sid(); assert s1 != s0
        # DB-only write (usage row through a normal writer connection)
        _init_cycle(project); s2 = sid(); assert s2 != s1
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="t", status="ok")
        finally:
            conn.close()
        s3 = sid(); assert s3 != s2
        # pause marker appears…
        controls.pause_command(["--reason", "first"], project_root=project); s4 = sid(); assert s4 != s3
        # …and is REWRITTEN in place with a different reason/outcome (same presence)
        h.write_pause(project, {"reason": "other", "outcome": "crash", "ts": h._now_iso()})
        s4b = sid(); assert s4b != s4
        s4 = s4b
        # inflight appears / dies
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        h.inflight_path(project).write_text(json.dumps({"stem": "s", "pid": os.getpid(), "started_at": "t", "role": "lead", "agent": "Claude"}))
        s5 = sid(); assert s5 != s4
        # displayed metadata changes with the same stem/pid (started_at / role / agent)
        h.inflight_path(project).write_text(json.dumps({"stem": "s", "pid": os.getpid(), "started_at": "t2", "role": "reviewer", "agent": "Codex"}))
        s5b = sid(); assert s5b != s5
        h.inflight_path(project).write_text(json.dumps({"stem": "s", "pid": 999999, "started_at": "t2", "role": "reviewer", "agent": "Codex"}))
        s6 = sid(); assert s6 != s5b
        # watcher pidfile appears / changes mode (same pid) / dies
        watcher_mod.write_pidfile(project, "headless"); s7 = sid(); assert s7 != s6
        watcher_mod.write_pidfile(project, "iterm2"); s7b = sid(); assert s7b != s7
        watcher_mod.pidfile_path(project).write_text(json.dumps({"pid": 999999, "mode": "iterm2"}))
        s8 = sid(); assert s8 != s7b
        # registry edit
        reg.write_text(json.dumps([str(a)])); s9 = sid(); assert s9 != s8
        # unchanged → same
        assert sid() == s9

    @needs_proc_inspection
    @pytest.mark.skipif(sys.platform == "win32", reason="process scan is POSIX-only")
    def test_watcher_without_pidfile_start_and_exit_fire(self, tmp_path):
        a = _mk(tmp_path, "a", state=_state())
        fake_bin = a / ".fakebin"; fake_bin.mkdir(); script = fake_bin / "tagteam"
        script.write_text("import time; time.sleep(120)\n")
        from tagteam import cockpit_api as capi
        snap = lambda: procs.list_processes(capi.WATCH_ARGV_RE.pattern)
        s0 = hub_api.signature_id(hub_api.hub_signature([str(a)], None, procs_snapshot=snap()))
        proc = subprocess.Popen([sys.executable, str(script), "watch", "--mode", "notify"], cwd=str(a),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            s1 = s0
            while s1 == s0 and time.monotonic() < deadline:
                time.sleep(0.2); s1 = hub_api.signature_id(hub_api.hub_signature([str(a)], None, procs_snapshot=snap()))
            assert s1 != s0
            sig = hub_api.hub_signature([str(a)], None, procs_snapshot=snap())
            assert sig["projects"][str(a)]["watcher"][:2] == [True, proc.pid]
            assert sig["projects"][str(a)]["watcher"][2:] == ["notify", "process-scan", False]
        finally:
            proc.kill(); proc.wait()
        s2 = hub_api.signature_id(hub_api.hub_signature([str(a)], None, procs_snapshot=snap()))
        assert s2 != s1


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_text_groups(self, tmp_path):
        esc = _mk(tmp_path, "esc", state=_state(status="escalated", turn=None, age_s=3600)); _cycle_status(esc, "p", "plan", "escalated")
        gone = _mk(tmp_path, "gone", state=_state(age_s=25 * 3600))
        done = _mk(tmp_path, "done", state=_state(status="done", turn=None, result="approved"))
        p = hub_api.hub_payload([str(esc), str(gone), str(done), str(tmp_path / "nope")], now=NOW, procs_snapshot=[], scratch_prefixes=NO_SCRATCH)
        txt = hub_api.render_text(p)
        assert "NEEDS YOU (1)" in txt and "esc" in txt and "escalated r1" in txt
        assert "WAITING (1)" in txt and "ABANDONED?" in txt and "tagteam watch --mode headless" in txt
        assert "QUIET (1)" in txt and "done — approved" in txt
        assert "HIDDEN (1)" in txt and "use --all" in txt
        assert "Subscription window: n/a" in txt
