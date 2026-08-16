"""Phase 35 tests: the hub server — hub routes, per-project cockpit mounts
via `CockpitRouter` (two projects concurrently: reads / writes / SSE / auth
isolated), exact mounted asset URLs + base meta, standalone cockpit page and
requests identical to 0.11.0, hub SSE, `--list` output, and the registry
CLI's non-mutation guarantee."""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path

import pytest

from tagteam import controls, db, headless as h, hub, hub_api, registry as registry_mod, server as srv
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401
from tests.test_server_cockpit import Client, SSEReader, Served  # noqa: F401
from tests.test_hub_api import _mk, _state, _cycle_status, YAML, NO_SCRATCH  # noqa: F401


class HubServed:
    """A real hub server over an explicit registry file (never ~/.tagteam)."""

    def __init__(self, registry_file: Path, **ctx_kw):
        self.registry_file = registry_file
        self.ctx_kw = ctx_kw

    def __enter__(self):
        reader, reg = hub._registry_reader(str(self.registry_file))
        kw = dict(interval_s=0.2, heartbeat_s=0.6, scratch_prefixes=NO_SCRATCH)
        kw.update(self.ctx_kw)
        self.ctx = hub.HubContext(registry_reader=reader, registry_file=reg, token=srv.new_token(), **kw)
        handler = hub.make_hub_handler(self.ctx)
        self.server = srv.TagteamHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.token = self.ctx.token
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.client = Client("127.0.0.1", self.port)
        return self

    def __exit__(self, *exc):
        self.server.stop()
        self.thread.join(timeout=5)

    def auth(self, origin=True):
        hdrs = {"X-Tagteam-Token": self.token}
        if origin:
            hdrs["Origin"] = f"http://127.0.0.1:{self.port}"
        return hdrs


def _two_projects(tmp_path):
    """Two real tagteam projects (cycles initialised) + a registry file."""
    from tests.test_headless import SKILL_SRC
    dirs = []
    for name in ("alpha", "beta"):
        d = tmp_path / name
        d.mkdir()
        (d / "tagteam.yaml").write_text(YAML)
        (d / "docs" / "handoffs").mkdir(parents=True)
        skill = d / h.SKILL_RELPATH
        skill.parent.mkdir(parents=True)
        skill.write_text(SKILL_SRC.read_text(encoding="utf-8"), encoding="utf-8")
        cycle_mod.init_cycle("feat-" + name, "plan", "Claude", "Codex", "first", str(d), updated_by="Claude")
        dirs.append(d)
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps([str(x) for x in dirs], indent=2) + "\n")
    return dirs[0], dirs[1], reg


def _pid(d: Path) -> str:
    return hub_api.project_id(str(d))


# ---------------------------------------------------------------------------
# hub routes
# ---------------------------------------------------------------------------

class TestHubRoutes:
    def test_page_payload_info_and_assets(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        with HubServed(reg) as s:
            r = s.client.get("/")
            assert r["status"] == 200
            html = r["raw"].decode()
            assert f'<meta name="tagteam-token" content="{s.token}">' in html
            assert 'id="needs-you"' in html and 'id="waiting"' in html and 'id="quiet"' in html and 'id="conn"' in html
            assert "access-control-allow-origin" not in r["headers"]
            for asset in ("/hub.css", "/hub.js", "/cockpit.css"):
                a_ = s.client.get(asset)
                assert a_["status"] == 200 and len(a_["raw"]) > 500, asset
                assert "access-control-allow-origin" not in a_["headers"]
            p = s.client.get("/api/hub")["json"]
            assert p["totals"]["projects"] == 2 and p["totals"]["waiting"] == 2
            assert {r_["name"] for r_ in p["groups"]["waiting"]} == {"alpha", "beta"}
            u = s.client.get("/api/hub/usage?window=7d")["json"]
            assert u["window"] == "7d" and "turns" in u["usage"]
            info = s.client.get("/api/hub/info")["json"]
            assert info["mode"] == "hub" and info["mounted"] == []
            assert s.client.get("/api/nope")["status"] == 404
            # the hub itself has no write endpoints: token required, then 404
            assert s.client.post("/api/hub", {})["status"] == 403
            assert s.client.post("/api/hub", {}, headers=s.auth())["status"] == 404

    def test_registry_file_untouched_by_reads(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        # add a missing dir + a no-yaml dir: reads must not prune them
        (tmp_path / "noyaml").mkdir()
        reg.write_text(json.dumps([str(a), str(b), str(tmp_path / "gone"), str(tmp_path / "noyaml")], indent=2) + "\n")
        before = reg.read_bytes()
        with HubServed(reg) as s:
            s.client.get("/api/hub"); s.client.get("/api/hub?all=1"); s.client.get("/api/hub/usage"); s.client.get("/")
            p = s.client.get("/api/hub?all=1")["json"]
            assert p["registry"]["total"] == 4
        out = io.StringIO()
        assert hub.hub_command(["--list", "--registry", str(reg)], out=out, scratch_prefixes=NO_SCRATCH) == 0
        assert "WAITING (2)" in out.getvalue() and "HIDDEN (2)" in out.getvalue()
        out = io.StringIO()
        assert hub.hub_command(["--list", "--json", "--all", "--registry", str(reg)], out=out, scratch_prefixes=NO_SCRATCH) == 0
        j = json.loads(out.getvalue())
        assert j["registry"]["show_all"] is True and [x["kind"] for x in j["groups"]["hidden"]] == ["missing"]
        assert reg.read_bytes() == before                                   # byte-for-byte

    def test_hub_options(self):
        assert hub.resolve_hub_options([])["port"] == 8090 and hub.resolve_hub_options([])["host"] == "127.0.0.1"
        o = hub.resolve_hub_options(["--port", "9001", "--host", "0.0.0.0", "--interval", "1", "--max-sse", "2", "--all", "--list", "--json"])
        assert (o["port"], o["host"], o["interval"], o["max_sse"], o["all"], o["list"], o["json"]) == (9001, "0.0.0.0", 1.0, 2, True, True, True)
        assert isinstance(hub.resolve_hub_options(["--port", "x"]), str)
        assert isinstance(hub.resolve_hub_options(["--bogus"]), str)
        out = io.StringIO(); assert hub.hub_command(["--help"], out=out) == 0 and "--list" in out.getvalue()


# ---------------------------------------------------------------------------
# mounts
# ---------------------------------------------------------------------------

class TestMounts:
    def test_two_projects_isolated(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        pa, pb = _pid(a), _pid(b)
        with HubServed(reg) as s:
            c = s.client
            # pages: base-aware asset URLs + base meta + token; exact mounted URLs resolve
            for pid, d in ((pa, a), (pb, b)):
                r = c.get(f"/p/{pid}/")
                assert r["status"] == 200, pid
                html = r["raw"].decode()
                assert f'<meta name="tagteam-base" content="/p/{pid}">' in html
                assert f'href="/p/{pid}/cockpit.css"' in html and f'src="/p/{pid}/cockpit.js"' in html
                assert f'content="{s.token}"' in html
                assert 'href="/cockpit.css"' not in html
                assert c.get(f"/p/{pid}/cockpit.css")["status"] == 200
                assert c.get(f"/p/{pid}/cockpit.js")["status"] == 200
                # reads resolve against THAT project
                now = c.get(f"/p/{pid}/api/now")["json"]
                assert now["state"]["phase"] == "feat-" + d.name and now["project_dir"] == str(d)
                assert c.get(f"/p/{pid}/api/rounds/feat-{d.name}_plan")["json"]["rounds"][0]["round"] == 1
                # ?theme=saloon under a mount still serves the cockpit
                assert "Tagteam Cockpit" in c.get(f"/p/{pid}/?theme=saloon")["raw"].decode()
            # unknown id
            assert c.get("/p/nope-000000/")["status"] == 404 and c.get("/p/nope-000000/api/now")["status"] == 404
            # writes: token + origin required on mounts; pause A only
            assert c.post(f"/p/{pa}/api/pause", {})["status"] == 403
            assert c.post(f"/p/{pa}/api/pause", {}, headers={"X-Tagteam-Token": s.token, "Origin": "http://evil"})["status"] == 403
            r = c.post(f"/p/{pa}/api/pause", {"reason": "hold A"}, headers=s.auth())
            assert r["json"]["ok"] is True
            assert h.read_pause(a)["reason"] == "hold A" and h.read_pause(b) is None
            assert c.get(f"/p/{pa}/api/now")["json"]["paused"]["reason"] == "hold A"
            assert c.get(f"/p/{pb}/api/now")["json"]["paused"] is None
            assert c.post(f"/p/{pa}/api/resume", {}, headers=s.auth())["json"]["ok"] is True
            # a ruling through B's mount records in B only
            cycle_mod.add_round("feat-beta", "plan", "reviewer", "ESCALATE", 1, "stuck", str(b), updated_by="Codex")
            r = c.post(f"/p/{pb}/api/rule", {"ruling": "approve", "content": "ship"}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["ok"], r
            assert cycle_mod.read_status("feat-beta", "plan", str(b))["state"] == "approved"
            assert cycle_mod.read_status("feat-alpha", "plan", str(a))["state"] == "in-progress"
            rounds_b = cycle_mod.read_rounds("feat-beta", "plan", str(b))
            assert rounds_b[-1]["content"].startswith("[ARBITER RULING by web:")
            conn = db.connect(project_dir=str(b))
            try:
                assert conn.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='arbiter_ruling'").fetchone()[0] == 1
            finally:
                conn.close()
            conn = db.connect(project_dir=str(a))
            try:
                assert conn.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='arbiter_ruling'").fetchone()[0] == 0
            finally:
                conn.close()
            # A's escalation ruled from A's mount is unaffected by B's state
            assert c.post(f"/p/{pa}/api/rule", {"ruling": "approve"}, headers=s.auth())["status"] == 409
            info = c.get("/api/hub/info")["json"]
            assert sorted(info["mounted"]) == sorted([pa, pb])
            # hub payload reflects both changes
            p = c.get("/api/hub")["json"]
            names = {r_["name"]: r_ for g in ("needs_you", "waiting", "quiet") for r_ in p["groups"][g]}
            assert names["beta"]["cycle_state"] == "approved" and names["alpha"]["cycle_state"] == "in-progress"

    def test_unregistered_project_is_unmounted_immediately(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        pa, pb = _pid(a), _pid(b)
        with HubServed(reg) as s:
            c = s.client
            assert c.get(f"/p/{pa}/api/now")["status"] == 200               # mounted + cached
            assert c.post(f"/p/{pa}/api/pause", {}, headers=s.auth())["json"]["ok"] is True
            assert c.post(f"/p/{pa}/api/resume", {}, headers=s.auth())["json"]["ok"] is True
            assert pa in c.get("/api/hub/info")["json"]["mounted"]
            # remove A from the registry (the reader sees the file live)
            reg.write_text(json.dumps([str(b)]) + "\n")
            assert c.get(f"/p/{pa}/")["status"] == 404
            assert c.get(f"/p/{pa}/api/now")["status"] == 404
            r = c.post(f"/p/{pa}/api/pause", {"reason": "sneaky"}, headers=s.auth())
            assert r["status"] == 404 and h.read_pause(a) is None           # cannot mutate the removed project
            assert pa not in c.get("/api/hub/info")["json"]["mounted"]
            assert c.get(f"/p/{pb}/api/now")["status"] == 200               # B unaffected
            # re-adding it mounts again; a re-used id for a moved path rebuilds the router
            reg.write_text(json.dumps([str(a), str(b)]) + "\n")
            assert c.get(f"/p/{pa}/api/now")["json"]["project_dir"] == str(a)

    def test_mounted_sse_per_project_and_caps(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        pa, pb = _pid(a), _pid(b)
        with HubServed(reg, max_sse=1) as s:
            ea = SSEReader(s.port, path=f"/p/{pa}/api/events"); eb = SSEReader(s.port, path=f"/p/{pb}/api/events")
            ea.wait_frames(1); eb.wait_frames(1)
            assert ea.status == 200 and eb.status == 200
            assert ea.frames[0]["data"]["phase"] == "feat-alpha" and eb.frames[0]["data"]["phase"] == "feat-beta"
            # a change in A fires A's stream, not B's
            controls.pause_command([], project_root=a)
            ea.wait_frames(2, 3.0); eb.pump(1.0)
            assert len(ea.frames) >= 2 and ea.frames[-1]["data"]["paused"] is True
            assert all(f["data"]["paused"] is False for f in eb.frames)
            # per-mount cap: a second stream on A is refused, B still has room
            ea2 = SSEReader(s.port, path=f"/p/{pa}/api/events"); ea2.pump(0.8)
            assert ea2.status == 503
            ea.close(); eb.close(); ea2.close()

    def test_hub_sse_fires_on_any_project_change_and_db_only_write(self, tmp_path):
        a, b, reg = _two_projects(tmp_path)
        with HubServed(reg) as s:
            e = SSEReader(s.port, path="/api/hub/events")
            e.wait_frames(1)
            assert e.status == 200 and e.frames[0]["event"] == "change" and e.frames[0]["data"]["projects"] == 2
            first = e.frames[0]["id"]
            # DB-only write in B
            conn = db.connect(project_dir=str(b))
            try:
                db.add_usage(conn, ts="t", status="ok")
            finally:
                conn.close()
            e.wait_frames(2, 3.0)
            assert len(e.frames) >= 2 and e.frames[-1]["id"] != first
            n = len(e.frames)
            # a state change in A
            cycle_mod.add_round("feat-alpha", "plan", "reviewer", "REQUEST_CHANGES", 1, "x", str(a), updated_by="Codex")
            e.wait_frames(n + 1, 3.0); assert len(e.frames) > n
            n = len(e.frames)
            # registry edit
            reg.write_text(json.dumps([str(a)]) + "\n")
            e.wait_frames(n + 1, 3.0); assert len(e.frames) > n
            # heartbeat while idle
            e.comments.clear(); e.pump(2.5)
            assert any(c.startswith(": heartbeat") for c in e.comments)
            e.close()
            # cap
        with HubServed(reg, max_sse=1) as s:
            e1 = SSEReader(s.port, path="/api/hub/events"); e1.wait_frames(1)
            e2 = SSEReader(s.port, path="/api/hub/events"); e2.pump(0.8)
            assert e2.status == 503
            e1.close(); e2.close()


# ---------------------------------------------------------------------------
# standalone cockpit unchanged
# ---------------------------------------------------------------------------

class TestStandaloneUnchanged:
    def test_standalone_page_identical_to_0_11(self, project):
        with Served(project, "cockpit") as s:
            r = s.client.get("/")
            html = r["raw"]
            packaged = (srv._WEB_DIR / "cockpit.html").read_bytes()
            expected = packaged.replace(b'<meta charset="UTF-8">',
                                        b'<meta charset="UTF-8">\n<meta name="tagteam-token" content="' + s.token.encode() + b'">', 1)
            assert html == expected                     # only the token meta, no base meta, no URL rewrite
            assert b"tagteam-base" not in html and b'href="/cockpit.css"' in html
            assert s.client.get("/cockpit.css")["status"] == 200
            assert "The Handoff Saloon" in s.client.get("/?theme=saloon")["raw"].decode()   # Saloon still offered standalone
        with Served(project) as s:                       # legacy verbatim
            assert s.client.get("/")["raw"] == (srv._WEB_DIR / "index.html").read_bytes()

    def test_dashboard_html_base_rewrite(self):
        html = srv._get_dashboard_html("cockpit", "tok", "/p/x-1")
        assert b'<meta name="tagteam-base" content="/p/x-1">' in html
        assert b'href="/p/x-1/cockpit.css"' in html and b'src="/p/x-1/cockpit.js"' in html
        assert b'href="/cockpit.css"' not in html
        # protocol-relative URLs are not touched; standalone unchanged
        assert srv._get_dashboard_html("cockpit", "tok") == srv._get_dashboard_html("cockpit", "tok", "")


# ---------------------------------------------------------------------------
# registry CLI
# ---------------------------------------------------------------------------

class TestRegistryCli:
    def test_list_raw_and_unregister_only_mutation(self, tmp_path, monkeypatch):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr(registry_mod, "REGISTRY_FILE", reg)
        monkeypatch.setattr(registry_mod, "REGISTRY_DIR", tmp_path)
        ok = _mk(tmp_path, "ok", state=_state())
        noyaml = _mk(tmp_path, "noyaml", yaml=False)
        gone = tmp_path / "gone"
        reg.write_text(json.dumps([str(ok), str(noyaml), str(gone)], indent=2) + "\n")
        before = reg.read_bytes()
        assert registry_mod.read_registry_raw() == [str(ok), str(noyaml), str(gone)]
        assert registry_mod.registry_path() == reg
        out = io.StringIO()
        assert hub.registry_command(["list"], out=out, scratch_prefixes=NO_SCRATCH) == 0
        txt = out.getvalue()
        assert "missing" in txt and str(gone) in txt and "no-yaml" in txt
        out = io.StringIO()
        assert hub.registry_command(["list", "--json"], out=out, scratch_prefixes=NO_SCRATCH) == 0
        assert [e["kind"] for e in json.loads(out.getvalue())] == ["ok", "no-yaml", "missing"]
        assert reg.read_bytes() == before                                    # list never writes
        # the pruning reader (used by upgrade/rollback) is unchanged
        assert str(gone) not in registry_mod.get_registered_projects()
        assert reg.read_bytes() != before                                    # ...and it does prune (pre-existing behavior)
        reg.write_text(before.decode())
        out = io.StringIO()
        assert hub.registry_command(["unregister", str(noyaml)], out=out) == 0
        assert registry_mod.read_registry_raw() == [str(ok), str(gone)]
        out = io.StringIO()
        assert hub.registry_command(["unregister", str(tmp_path / "never")], out=out) == 1
        assert hub.registry_command([], out=io.StringIO()) == 1
        assert hub.registry_command(["--help"], out=io.StringIO()) == 0
