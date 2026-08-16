"""Phase 34 tests: cockpit payload builders (`tagteam.cockpit_api`) —
now / rounds / interjections / briefs / brief-current rule / usage series +
rate-limit signal / scope-diff per-file caps / tail / SSE signature — and
the write wrappers (`run_action`, `cli_preview`) over the Phase 32/33
command functions. No HTTP here (see test_server_cockpit.py)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import controls, db, headless as h
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=False).stdout


def _gitify(root: Path) -> None:
    (root / ".gitignore").write_text(".tagteam/\nhandoff-state.json\n")
    (root / "docs" / "handoffs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "handoffs" / ".gitkeep").write_text("")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


def _escalate(project: Path, phase="feat-x", how="ESCALATE"):
    _init_cycle(project, phase=phase)
    cycle_mod.add_round(phase, "plan", "reviewer", how, 1, f"{how} because reasons",
                        str(project), updated_by="Codex")


# ---------------------------------------------------------------------------
# /api/now
# ---------------------------------------------------------------------------

class TestNow:
    def test_empty_project_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
        (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
        n = capi.now_payload(tmp_path)
        assert set(n) >= {"ts", "state", "cycle", "owed", "inflight", "paused", "watcher",
                          "briefer_enabled", "agents", "pending_notes", "project_dir"}
        assert n["owed"] is None and n["inflight"] is None and n["paused"] is None
        assert n["agents"] == {"lead": "A", "reviewer": "B"}
        assert n["briefer_enabled"] is False
        assert n["cycle"] is None

    def test_no_config_at_all(self, tmp_path):
        n = capi.now_payload(tmp_path)
        assert n["agents"] == {"lead": None, "reviewer": None}
        assert n["state"] == {}

    def test_owed_and_pause_and_inflight(self, project):
        _init_cycle(project)  # reviewer owed
        n = capi.now_payload(project)
        assert n["owed"]["role"] == "reviewer" and n["owed"]["agent"] == "Codex"
        assert n["owed"]["age_s"] is not None and n["owed"]["age_s"] >= 0
        assert n["cycle"]["state"] == "in-progress"
        controls.pause_command(["--reason", "hold it", "--by", "jack"], project_root=project)
        h.turns_dir(project).mkdir(parents=True, exist_ok=True)
        h.inflight_path(project).write_text(json.dumps({
            "stem": "s1", "pid": 999999, "watcher_pid": os.getpid(), "provider": "claude",
            "agent": "Codex", "role": "reviewer", "started_at": h._now_iso(),
            "log_path": str(h.turns_dir(project) / "s1.log"),
            "events_path": str(h.turns_dir(project) / "s1.events.jsonl")}))
        n = capi.now_payload(project)
        assert n["paused"]["reason"] == "hold it" and n["paused"]["age_s"] >= 0
        assert n["inflight"]["stem"] == "s1" and n["inflight"]["age_s"] >= 0
        assert n["inflight"]["pid_alive"] is False
        # the inflight watcher pid (this process, no ident recorded) counts as a live watcher
        assert n["watcher"]["running"] is True and n["watcher"]["pid"] == os.getpid()

    def test_pending_notes_count_scoped(self, project):
        _init_cycle(project)
        controls.interject_command(["a"], project_root=project)
        controls.interject_command(["b", "--to", "lead"], project_root=project)
        n = capi.now_payload(project)
        assert n["pending_notes"] == 2
        conn = db.connect(project_dir=str(project))
        try:
            db.retire_interjection(conn, 1, by="x", ts="t")
        finally:
            conn.close()
        assert capi.now_payload(project)["pending_notes"] == 1


# ---------------------------------------------------------------------------
# rounds / interjections / briefs
# ---------------------------------------------------------------------------

class TestRoundsAndNotes:
    def test_rounds_payload_has_entries_rulings_interjections(self, project):
        _init_cycle(project)
        controls.interject_command(["note r1"], project_root=project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix",
                            str(project), updated_by="Codex")
        p = capi.rounds_payload(project, "feat-x", "plan")
        assert p["rounds"][0]["round"] == 1
        r = p["rounds"][0]
        assert [e["action"] for e in r["entries"]] == ["SUBMIT_FOR_REVIEW", "REQUEST_CHANGES"]
        assert r["rulings"] == []
        assert [i["note"] for i in r["interjections"]] == ["note r1"]
        assert "html" in p

    def test_rounds_payload_missing_cycle(self, project):
        assert capi.rounds_payload(project, "nope", "plan") == {"rounds": [], "html": ""}

    def test_interjections_payload_status(self, project):
        _init_cycle(project)
        controls.interject_command(["a"], project_root=project)
        controls.interject_command(["b"], project_root=project)
        conn = db.connect(project_dir=str(project))
        try:
            db.retire_interjection(conn, 2, by="x", ts="t")
            db.mark_interjections_delivered(conn, [1], role="reviewer", round_=1, stem="s", ts="t")
        finally:
            conn.close()
        p = capi.interjections_payload(project, "feat-x", "plan")
        assert [i["status"] for i in p["interjections"]] == ["delivered", "retired"]
        assert p["pending"] == 0
        # unscoped notes (no cycle owed) are included too
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok", str(project),
                            updated_by="Codex")
        controls.interject_command(["c"], project_root=project)
        p = capi.interjections_payload(project, "feat-x", "plan")
        assert [i["note"] for i in p["interjections"]] == ["a", "b", "c"]
        assert p["pending"] == 1

    def test_briefs_payload_strips_content(self, project):
        _escalate(project)
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                    cycle_state="escalated", event_key="k", kind="manual",
                                    runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid, status="ok", ts="t2", content="## Positions\nx", path="/p")
        finally:
            conn.close()
        p = capi.briefs_payload(project, "feat-x", "plan")
        assert p["briefs"][0]["has_content"] is True and "content" not in p["briefs"][0]
        full = capi.brief_payload(project, rid)
        assert full["content"].startswith("## Positions")
        assert capi.brief_payload(project, 999) is None

    def test_brief_current_rule(self, project):
        _init_cycle(project)
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["event"] is None and "not escalated" in p["reason"]
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                            updated_by="Codex")
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["event"]["cycle_state"] == "escalated" and p["event"]["content"] == "stuck"
        assert p["brief"] is None and p["attempts"] == []
        key = p["event"]["event_key"]
        conn = db.connect(project_dir=str(project))
        try:
            rid, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                    cycle_state="escalated", event_key=key, kind="auto",
                                    runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid, status="failed", ts="t2", reason="boom")
        finally:
            conn.close()
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["brief"] is None and [a["status"] for a in p["attempts"]] == ["failed"]
        conn = db.connect(project_dir=str(project))
        try:
            rid2, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                     cycle_state="escalated", event_key=key, kind="manual",
                                     runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid2, status="ok", ts="t3", content="## Positions", path="/x")
            # a brief for ANOTHER event must never be selected
            rid3, _ = db.claim_brief(conn, ts="t", phase="feat-x", cycle_type="plan", round_=1,
                                     cycle_state="escalated", event_key="other", kind="manual",
                                     runner_pid=1, runner_ident="x")
            db.finish_brief(conn, rid3, status="ok", ts="t4", content="wrong", path="/y")
        finally:
            conn.close()
        p = capi.brief_current_payload(project, "feat-x", "plan")
        assert p["brief"]["id"] == rid2
        assert capi.brief_current_payload(project, None, None)["event"] is None


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

class TestUsage:
    def _rows(self, project):
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="t1", phase="feat-x", type="plan", round=1, role="reviewer",
                         agent="Codex", provider="codex", status="ok", input_tokens=10,
                         output_tokens=5, cost_usd=None, duration_ms=1000)
            db.add_usage(conn, ts="t2", phase="feat-x", type="plan", round=2, role="lead",
                         agent="Claude", provider="claude", status="ok", input_tokens=20,
                         output_tokens=7, cache_read_tokens=100, cost_usd=0.5, duration_ms=2000)
            db.add_usage(conn, ts="t3", phase="other", type="impl", round=1, role="briefer",
                         agent="briefer", provider="claude", status="timeout")
            db.upsert_rate_limit(conn, provider="claude", kind="five_hour", status="allowed",
                                 resets_at="2099-01-01T00:00:00+00:00", payload={"x": 1}, ts="t3")
        finally:
            conn.close()

    def test_usage_payload_shape_and_filters(self, project):
        self._rows(project)
        p = capi.usage_payload(project)
        assert p["totals"]["turns"] == 3
        assert set(p["by_role"]) == {"reviewer", "lead", "briefer"}
        assert "Claude (lead)" in p["by_agent"]
        assert [s["round"] for s in p["series"]] == [1, 2, 1]
        assert p["series"][1]["input"] == 20 and p["series"][1]["cache_read"] == 100
        assert p["rate_limits"][0]["kind"] == "five_hour" and p["rate_limits"][0]["payload"] == {"x": 1}
        assert p["rate_limits"][0]["resets_in_s"] > 0
        p = capi.usage_payload(project, phase="feat-x", ctype="plan")
        assert p["totals"]["turns"] == 2
        p = capi.usage_payload(project, phase="feat-x", ctype="plan", role="lead")
        assert p["totals"]["turns"] == 1 and p["filter"]["role"] == "lead"

    def test_usage_payload_empty(self, tmp_path):
        p = capi.usage_payload(tmp_path)
        assert p["totals"]["turns"] == 0 and p["series"] == [] and p["rate_limits"] == []


# ---------------------------------------------------------------------------
# scope diff
# ---------------------------------------------------------------------------

class TestScopeDiff:
    def _cycle_with_changes(self, project):
        _gitify(project)
        _init_cycle(project)                      # baseline = HEAD, clean-ish
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("print(1)\nprint(2)\n")     # untracked
        (project / "tagteam.yaml").write_text((project / "tagteam.yaml").read_text() + "# x\n")  # modified tracked
        (project / "bin.dat").write_bytes(b"\x00\x01\x02\xff\x00")         # binary untracked
        _git(project, "add", "src/a.py")
        _git(project, "commit", "-qm", "add a")                            # committed since baseline
        (project / "src" / "a.py").write_text("print(1)\nprint(3)\n")     # + uncommitted edit

    def test_per_file_structure(self, project):
        self._cycle_with_changes(project)
        p = capi.scope_diff_payload(project, "feat-x", "plan")
        assert p["error"] is None
        assert p["paths"] == ["bin.dat", "src/a.py", "tagteam.yaml"]
        by = {f["path"]: f for f in p["files"]}
        a = by["src/a.py"]
        assert a["binary"] is False and a["additions"] == 2 and a["deletions"] == 0
        assert "+print(3)" in a["patch"] and "+print(1)" in a["patch"]   # committed + working tree vs baseline
        y = by["tagteam.yaml"]
        assert y["status"] == "modified" and "+# x" in y["patch"]
        b = by["bin.dat"]
        assert b["binary"] is True and b["patch"] is None and b["status"] == "untracked"
        assert p["truncated"] is False and p["omitted_files"] == 0
        # matches the CLI's path list exactly
        cli = cycle_mod.compute_scope_diff("feat-x", "plan", str(project))["paths"]
        assert cli == p["paths"]

    def test_caps_bytes_and_files(self, project):
        self._cycle_with_changes(project)
        p = capi.scope_diff_payload(project, "feat-x", "plan", max_bytes=40)
        assert p["truncated"] is True
        sizes = [f["bytes"] for f in p["files"] if f["patch"] is not None]
        assert any(f["truncated"] for f in p["files"])
        assert p["bytes"] <= 40
        p = capi.scope_diff_payload(project, "feat-x", "plan", max_files=1)
        assert len(p["files"]) == 1 and p["omitted_files"] == 2 and p["truncated"] is True
        assert p["paths"] == ["bin.dat", "src/a.py", "tagteam.yaml"]  # the full list is still reported

    def test_error_shape(self, project):
        p = capi.scope_diff_payload(project, "nope", "plan")
        assert p["error"] == "No cycle found: nope_plan" and p["files"] == []

    def test_cli_byte_identical(self, project, capsys):
        self._cycle_with_changes(project)
        rc = cycle_mod._cli_scope_diff(["--phase", "feat-x", "--type", "plan"])
        assert rc == 0
        assert capsys.readouterr().out == "bin.dat\nsrc/a.py\ntagteam.yaml\n"
        rc = cycle_mod._cli_scope_diff(["--phase", "nope", "--type", "plan"])
        assert rc == 1 and capsys.readouterr().out == "No cycle found: nope_plan\n"


# ---------------------------------------------------------------------------
# tail + signature
# ---------------------------------------------------------------------------

class TestTailAndSignature:
    def test_tail_empty_then_latest(self, project):
        p = capi.tail_payload(project, 10)
        assert p["path"] is None and "No headless turn logs" in p["message"]
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "a.log").write_text("\n".join(f"l{i}" for i in range(100)))
        p = capi.tail_payload(project, 5)
        assert p["lines"] == ["l95", "l96", "l97", "l98", "l99"] and p["inflight"] is False
        # capped
        assert len(capi.tail_payload(project, 10 ** 9)["lines"]) == 100

    def test_signature_changes_on_each_signal(self, project):
        _init_cycle(project)
        s0 = capi.events_signature(project); i0 = capi.signature_id(s0)
        assert set(s0) >= {"seq", "rounds", "interjections", "briefs", "usage", "paused", "inflight"}
        controls.interject_command(["n"], project_root=project)
        s1 = capi.events_signature(project); i1 = capi.signature_id(s1)
        assert i1 != i0 and s1["interjections"] == 1
        controls.pause_command([], project_root=project)
        i2 = capi.signature_id(capi.events_signature(project)); assert i2 != i1
        conn = db.connect(project_dir=str(project))
        try:
            db.add_usage(conn, ts="t", status="ok")
        finally:
            conn.close()
        i3 = capi.signature_id(capi.events_signature(project)); assert i3 != i2
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "x", str(project))
        i4 = capi.signature_id(capi.events_signature(project)); assert i4 != i3
        # unchanged → same id (inflight age excluded from the id)
        assert capi.signature_id(capi.events_signature(project)) == i4


# ---------------------------------------------------------------------------
# write wrappers
# ---------------------------------------------------------------------------

class TestActions:
    def test_pause_resume_by_web_user(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
        r = capi.run_action("pause", {"reason": "from web"}, project)
        assert r["ok"] and "Paused" in r["message"] and r["cli"].startswith("tagteam pause")
        assert h.read_pause(project)["by"] == "web:jack" and h.read_pause(project)["reason"] == "from web"
        r = capi.run_action("resume", {}, project)
        assert r["ok"] and "Resumed" in r["message"] and h.read_pause(project) is None
        r = capi.run_action("resume", {}, project)
        assert not r["ok"] and "Not paused" in r["message"]

    def test_interject_and_retire(self, project):
        _init_cycle(project)
        r = capi.run_action("interject", {"note": "hi there", "to": "lead"}, project)
        assert r["ok"] and "Interjection #1" in r["message"]
        conn = db.connect(project_dir=str(project))
        try:
            row = db.get_interjections(conn)[0]
        finally:
            conn.close()
        assert row["note"] == "hi there" and row["target_role"] == "lead" and row["by"].startswith("web:")
        assert not capi.run_action("interject", {"note": ""}, project)["ok"]
        assert capi.run_action("interject", {"note": "x", "to": "nobody"}, project)["rc"] == 400
        r = capi.run_action("interject/retire", {"id": 1}, project)
        assert r["ok"] and "Retired" in r["message"]
        assert not capi.run_action("interject/retire", {"id": 1}, project)["ok"]
        assert capi.run_action("interject/retire", {"id": "x"}, project)["rc"] == 400

    def test_cancel_turn_nothing_inflight(self, project):
        r = capi.run_action("cancel-turn", {}, project)
        assert not r["ok"] and "Nothing in flight" in r["message"]

    def test_rule_paths(self, project):
        _init_cycle(project)
        r = capi.run_action("rule", {"ruling": "approve"}, project)
        assert not r["ok"] and "Nothing to rule on" in r["message"] and r["rc"] == 1
        assert capi.run_action("rule", {"ruling": "bogus"}, project)["rc"] == 400
        assert capi.run_action("rule", {"ruling": "request-changes"}, project)["rc"] == 400
        assert capi.run_action("rule", {"ruling": "approve", "to": "lead"}, project)["rc"] == 400
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                            updated_by="Codex")
        r = capi.run_action("rule", {"ruling": "request-changes", "content": "try again"}, project)
        assert r["ok"] and "changes requested" in r["message"]
        st = cycle_mod.read_status("feat-x", "plan", str(project))
        assert st["state"] == "in-progress" and st["ready_for"] == "lead"
        conn = db.connect(project_dir=str(project))
        try:
            kinds = [json.loads(r["payload_json"]) for r in db._rows(conn.execute(
                "SELECT payload_json FROM diagnostics WHERE kind='arbiter_ruling'"))]
        finally:
            conn.close()
        assert kinds and kinds[-1]["by"].startswith("web:") and kinds[-1]["ruling"] == "request-changes"

    def test_rule_answer_defaults_to_reviewer(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "NEED_HUMAN", 1, "which db?", str(project),
                            updated_by="Codex")
        r = capi.run_action("rule", {"ruling": "answer", "content": "sqlite"}, project)
        assert r["ok"] and "reviewer's turn" in r["message"]
        st = state_mod.read_state(str(project))
        assert st["turn"] == "reviewer" and st["status"] == "ready"

    def test_brief_generate_when_disabled(self, project):
        _escalate(project)
        r = capi.run_action("brief/generate", {}, project)
        assert not r["ok"] and "not enabled" in r["message"]

    def test_unknown_action_and_preview(self, project, monkeypatch):
        assert capi.run_action("nope", {}, project)["rc"] == 400
        monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
        assert capi.cli_preview("rule", {"ruling": "approve", "content": "fine by me"}) == \
            "tagteam rule approve --content 'fine by me' --by web:jack"
        assert capi.cli_preview("cancel-turn", {}) == "tagteam cancel-turn --by web:jack"
        assert capi.cli_preview("brief/generate", {}) == "tagteam brief --generate"
        with pytest.raises(ValueError):
            capi.cli_preview("rule", {"ruling": "answer"})

    def test_wrapper_never_raises(self, project, monkeypatch):
        def boom(args, project_root=None, out=None):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(controls, "pause_command", boom)
        r = capi.run_action("pause", {}, project)
        assert not r["ok"] and "kaboom" in r["message"]
