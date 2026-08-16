"""Phase 36: scripts/showcase_numbers.py — methodology on a fixture, the
sanitizer contract, snapshot safety, and the byte-compare drift guard for
the numbers block in docs/showcase.md."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "showcase_numbers.py"
SHOWCASE = REPO / "docs" / "showcase.md"
DATA_DIR = REPO / "docs" / "showcase-data"

spec = importlib.util.spec_from_file_location("showcase_numbers", SCRIPT)
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)  # type: ignore[union-attr]

RULING = "[ARBITER RULING by jack] approved."


def _entry(rnd, role, action, content, ts):
    return {"round": rnd, "role": role, "action": action, "content": content, "ts": ts}


def _cycle(root: Path, sub: str, phase: str, ctype: str, entries: list[dict], state="approved", date="2026-05-01"):
    d = root / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{phase}_{ctype}_status.json").write_text(json.dumps(
        {"state": state, "ready_for": None, "round": entries[-1]["round"], "phase": phase, "type": ctype,
         "lead": "Claude", "reviewer": "Codex", "date": date}), encoding="utf-8")
    (d / f"{phase}_{ctype}_rounds.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    T = "2026-05-0{d}T10:00:00+00:00"
    # approved at round 2, one REQUEST_CHANGES
    _cycle(tmp_path, "docs/handoffs", "alpha", "plan", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "plan v1", T.format(d=1)),
        _entry(1, "reviewer", "REQUEST_CHANGES", "no", T.format(d=1)),
        _entry(2, "lead", "SUBMIT_FOR_REVIEW", "plan v2", T.format(d=2)),
        _entry(2, "reviewer", "APPROVE", "ok", T.format(d=2)),
    ])
    # escalated at round 3 with a stale streak of 2 (v1, v1, v1)
    _cycle(tmp_path, "docs/handoffs", "beta", "plan", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "same", T.format(d=1)),
        _entry(1, "reviewer", "REQUEST_CHANGES", "no", T.format(d=1)),
        _entry(2, "lead", "SUBMIT_FOR_REVIEW", "same", T.format(d=2)),
        _entry(2, "reviewer", "REQUEST_CHANGES", "still no", T.format(d=2)),
        _entry(3, "lead", "SUBMIT_FOR_REVIEW", "same", T.format(d=3)),
        _entry(3, "reviewer", "ESCALATE", "stuck", T.format(d=3)),
    ], state="escalated")
    # in progress (lead submitted, no response yet) + an AMEND
    _cycle(tmp_path, "docs/handoffs", "gamma", "impl", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "impl", T.format(d=4)),
        _entry(1, "lead", "AMEND", "small fix", T.format(d=4)),
    ], state="in-progress")
    # approved by ruling at round 1
    _cycle(tmp_path, "docs/handoffs", "delta", "impl", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "impl", T.format(d=2)),
        _entry(1, "reviewer", "ESCALATE", "?", T.format(d=2)),
        _entry(1, "reviewer", "APPROVE", RULING, T.format(d=3)),
    ])
    # legacy-only cycle, approved round 1
    _cycle(tmp_path, ".tagteam/legacy", "legacy-only", "impl", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "x", T.format(d=1)),
        _entry(1, "reviewer", "APPROVE", "ok", T.format(d=1)),
    ])
    # duplicate: legacy says round 5, docs/handoffs says round 1 -> docs/handoffs wins
    _cycle(tmp_path, ".tagteam/legacy", "alpha", "impl", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "x", T.format(d=1)),
        _entry(1, "reviewer", "REQUEST_CHANGES", "no", T.format(d=1)),
        _entry(5, "lead", "SUBMIT_FOR_REVIEW", "y", T.format(d=2)),
        _entry(5, "reviewer", "APPROVE", "ok", T.format(d=2)),
    ])
    _cycle(tmp_path, "docs/handoffs", "alpha", "impl", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "x", T.format(d=1)),
        _entry(1, "reviewer", "APPROVE", "ok", T.format(d=1)),
    ])
    # entirely after the as-of date -> excluded
    _cycle(tmp_path, "docs/handoffs", "future", "plan", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "x", "2026-05-09T00:00:00+00:00"),
        _entry(1, "reviewer", "APPROVE", "ok", "2026-05-09T01:00:00+00:00"),
    ])
    # entries straddling the cutoff: only the pre-cutoff submission survives -> in progress
    _cycle(tmp_path, "docs/handoffs", "straddle", "plan", [
        _entry(1, "lead", "SUBMIT_FOR_REVIEW", "x", "2026-05-08T23:59:59+00:00"),
        _entry(1, "reviewer", "APPROVE", "ok", "2026-05-09T00:00:00+00:00"),
    ])
    return tmp_path


def test_report_methodology(fixture_root):
    cycles, _ = sn.build_report(fixture_root, "2026-05-08", None)
    rows = {(c["phase"], c["type"]): sn.analyze_cycle(c) for c in cycles}
    assert set(rows) == {("alpha", "plan"), ("beta", "plan"), ("gamma", "impl"), ("delta", "impl"),
                         ("legacy-only", "impl"), ("alpha", "impl"), ("straddle", "plan")}
    assert rows[("alpha", "plan")]["outcome"] == "approved" and rows[("alpha", "plan")]["approve_round"] == 2
    assert rows[("beta", "plan")]["outcome"] == "escalated" and rows[("beta", "plan")]["stale_streak"] == 2
    assert rows[("gamma", "impl")]["outcome"] == "in progress" and rows[("gamma", "impl")]["actions"]["AMEND"] == 1
    assert rows[("delta", "impl")]["outcome"] == "approved by ruling" and rows[("delta", "impl")]["actions"]["RULING"] == 1
    assert rows[("alpha", "impl")]["approve_round"] == 1          # docs/handoffs won over legacy round 5
    assert rows[("straddle", "plan")]["outcome"] == "in progress"  # APPROVE at cutoff dropped
    text = sn.render_report("2026-05-08", cycles, None)
    assert "| cycles | 3 | 4 | 7 |" in text
    assert "| approved | 1 | 2 | 3 |" in text
    assert "| approved by ruling | 0 | 1 | 1 |" in text
    assert "| escalated | 1 | 0 | 1 |" in text
    assert "| in progress | 1 | 1 | 2 |" in text
    assert "| lead submissions (rounds) | 10 |" in text
    assert "| reviewer: request changes | 3 |" in text
    assert "| reviewer: escalate | 2 |" in text
    assert "| lead amendments | 1 |" in text
    assert "| arbiter rulings | 1 |" in text
    assert "| pushback rate (request changes ÷ submissions) | 30% |" in text
    assert "| longest stale streak in any cycle | 2 | 0 | 2 |" in text
    assert "10 consecutive stale rounds" in text
    assert re.search(r"\b10 rounds\b", text) is None and re.search(r"round[- ]10", text) is None


def test_stale_streak_matches_production_rule():
    # the copied loop: first submission is the baseline, never stale
    assert sn._stale_streak(["a"] * 11) == 10
    assert sn._stale_streak(["a", "a", "a", "b"]) == 2
    assert sn._stale_streak(["a", "b", "b", "c", "c", "c"]) == 2
    assert sn._stale_streak(["a"]) == 0
    from tagteam import cycle as prod
    import inspect
    src = inspect.getsource(prod._count_stale_rounds)
    assert "submissions[i] == submissions[i - 1]" in src   # the rule the copy mirrors


def _raw_turn(id_, ts, phase, rnd, role, provider, status="ok", inp=100, out=10, extra=None):
    t = {"id": id_, "ts": ts, "phase": phase, "type": "plan", "round": rnd, "role": role,
         "agent": "X", "provider": provider, "model": "m", "status": status, "exit_code": 0,
         "duration_ms": 1000, "input_tokens": inp, "output_tokens": out, "cache_read_tokens": 5,
         "cache_write_tokens": 0, "cost_usd": None, "num_turns": 1,
         "session_id": "sess-123", "log_path": "/Users/someone/proj/.tagteam/turns/x.log"}
    if extra:
        t.update(extra)
    return t


def test_export_usage_contract():
    turns = [
        _raw_turn(5, "2026-05-02T10:00:00+00:00", "p", 1, "reviewer", "codex", inp=300),   # attempt 2 (later ts)
        _raw_turn(9, "2026-05-01T09:00:00+00:00", "q", 1, "lead", "claude", inp=7),         # unrelated, earlier
        _raw_turn(2, "2026-05-01T10:00:00+00:00", "p", 1, "reviewer", "codex", inp=200),   # attempt 1
        _raw_turn(7, "2026-05-01T10:00:00+00:00", "p", 1, "reviewer", "codex", inp=250, status="timeout"),  # same ts, id 7 > 2 -> attempt 2? no: sorted by (ts,id): id2 then id7 -> attempts 1,2; id5 later -> 3
        _raw_turn(11, "2026-05-08T23:59:59+00:00", "r", 1, "reviewer", "codex"),           # boundary in
        _raw_turn(12, "2026-05-09T00:00:00+00:00", "r", 2, "reviewer", "codex"),           # boundary out
    ]
    snap = sn.export_usage({"turns": turns}, "2026-05-08")
    assert snap["schema"] == sn.USAGE_SCHEMA and snap["as_of"] == "2026-05-08"
    keys = [(t["phase"], t["round"], t["role"], t["attempt"], t["input_tokens"]) for t in snap["turns"]]
    assert keys == [("p", 1, "reviewer", 1, 200), ("p", 1, "reviewer", 2, 250), ("p", 1, "reviewer", 3, 300),
                    ("q", 1, "lead", 1, 7), ("r", 1, "reviewer", 1, 100)]
    for t in snap["turns"]:
        assert set(t) == set(sn.USAGE_TURN_KEYS)
    # byte-stable across runs and with extra post-cutoff turns
    a = json.dumps(sn.export_usage({"turns": turns}, "2026-05-08"), sort_keys=True)
    b = json.dumps(sn.export_usage({"turns": list(reversed(turns)) + [_raw_turn(99, "2026-06-01T00:00:00+00:00", "z", 1, "lead", "claude")]}, "2026-05-08"), sort_keys=True)
    assert a == b
    # analysis: highest ok attempt used, timeout counted, retries = 1
    u = sn.analyze_usage(snap["turns"])
    assert u["outcomes"] == {"timeout": 1}
    g = u["groups"][("reviewer", "codex")]
    assert g["turns"] == 2 and g["retries"] == 1 and g["sums"]["input_tokens"] == 300 + 100
    assert u["curves"][("p", "plan", "reviewer", "codex")] == {1: 300}
    assert ("q", "plan", "lead", "claude") in u["curves"]      # providers never share a series
    # null token -> unknown
    snap2 = sn.export_usage({"turns": [_raw_turn(1, "2026-05-01T00:00:00+00:00", "n", 1, "lead", "claude", extra={"input_tokens": None})]}, "2026-05-08")
    u2 = sn.analyze_usage(snap2["turns"])
    assert u2["groups"][("lead", "claude")]["unknown"]["input_tokens"] == 1
    assert u2["curves"][("n", "plan", "lead", "claude")] == {1: None}


def test_report_rejects_mismatched_as_of(fixture_root, tmp_path):
    snap = sn.export_usage({"turns": []}, "2026-05-07")
    f = tmp_path / "u.json"
    f.write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(sn.ShowcaseError, match="as_of"):
        sn.build_report(fixture_root, "2026-05-08", f)
    r = subprocess.run([sys.executable, str(SCRIPT), "report", "--as-of", "2026-05-08", "--usage", str(f), "--root", str(fixture_root)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "as_of" in r.stderr
    r = subprocess.run([sys.executable, str(SCRIPT), "export-usage"], input="{}", capture_output=True, text=True)
    assert r.returncode == 2   # --as-of is required


# ---------------------------------------------------------------- safety ----

_PATHISH = re.compile(r"^(/|[A-Za-z]:\\|~/)")
_BANNED_KEYS = {"session_id", "log_path", "agent", "model", "ts", "id", "exit_code", "num_turns"}


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
            yield (f"{path}.{k}", k, None)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield (path, None, obj)


def test_committed_snapshots_are_safe():
    files = sorted(DATA_DIR.glob("*.json"))
    assert files, "docs/showcase-data/ must hold at least one usage snapshot"
    for f in files:
        snap = json.loads(f.read_text(encoding="utf-8"))
        assert set(snap) == {"schema", "as_of", "turns"}
        assert snap["schema"] == sn.USAGE_SCHEMA
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", snap["as_of"]) and f.name == f"usage-{snap['as_of']}.json"
        for t in snap["turns"]:
            assert set(t) == set(sn.USAGE_TURN_KEYS), f"{f.name}: unexpected keys {set(t) ^ set(sn.USAGE_TURN_KEYS)}"
        for where, key, val in _walk(snap):
            assert key not in _BANNED_KEYS, f"{f.name}: banned key {key} at {where}"
            if isinstance(val, str):
                assert not _PATHISH.match(val), f"{f.name}: path-like value at {where}: {val!r}"


def test_export_strips_sensitive_fields_and_is_stable(tmp_path):
    raw = {"turns": [_raw_turn(1, "2026-05-01T00:00:00+00:00", "p", 1, "lead", "claude")]}
    a = subprocess.run([sys.executable, str(SCRIPT), "export-usage", "--as-of", "2026-05-08"], input=json.dumps(raw), capture_output=True, text=True)
    b = subprocess.run([sys.executable, str(SCRIPT), "export-usage", "--as-of", "2026-05-08"], input=json.dumps(raw), capture_output=True, text=True)
    assert a.returncode == 0 and a.stdout == b.stdout
    for where, key, val in _walk(json.loads(a.stdout)):
        assert key not in _BANNED_KEYS
        if isinstance(val, str):
            assert not _PATHISH.match(val)


# ----------------------------------------------------- byte-compare guard ----

BLOCK_RE = re.compile(r"<!-- showcase-numbers:begin as-of=(\d{4}-\d{2}-\d{2}) -->\n(.*?)<!-- showcase-numbers:end -->", re.S)


def test_showcase_numbers_block_matches_script_output():
    text = SHOWCASE.read_text(encoding="utf-8")
    m = BLOCK_RE.search(text)
    assert m, "docs/showcase.md must contain the marked numbers block"
    as_of, block = m.group(1), m.group(2)
    usage = DATA_DIR / f"usage-{as_of}.json"
    assert usage.exists(), f"missing {usage}"
    r = subprocess.run([sys.executable, str(SCRIPT), "report", "--as-of", as_of, "--usage", str(usage), "--root", str(REPO)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert block == r.stdout, "regenerate: python scripts/showcase_numbers.py report --as-of %s --usage %s" % (as_of, usage.relative_to(REPO))
