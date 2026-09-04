"""
`tagteam usage` (Phase 32): per-turn token usage for this project from the
`usage` table (written by the headless engine since Phase 31), with
roll-ups by role, by cycle (phase+type), and totals. `--json` for scripts.
No cross-project mode here — that is the Phase 35 hub.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _empty_bucket() -> dict:
    return {"turns": 0, "ok": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
            "cost_known_turns": 0, "duration_ms_total": 0, "duration_known_turns": 0,
            "mean_duration_ms": None}


def _add(bucket: dict, row: dict) -> None:
    bucket["turns"] += 1
    if row.get("status") == "ok":
        bucket["ok"] += 1
    else:
        bucket["failed"] += 1
    for k in _TOKEN_KEYS:
        v = row.get(k)
        if isinstance(v, (int, float)):
            bucket[k] += int(v)
    c = row.get("cost_usd")
    if isinstance(c, (int, float)):
        bucket["cost_usd"] += float(c)
        bucket["cost_known_turns"] += 1
    d = row.get("duration_ms")
    if isinstance(d, (int, float)):
        bucket["duration_ms_total"] += int(d)
        bucket["duration_known_turns"] += 1


def _finish(bucket: dict) -> dict:
    if bucket["duration_known_turns"]:
        bucket["mean_duration_ms"] = int(bucket["duration_ms_total"] / bucket["duration_known_turns"])
    bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return bucket


def aggregate(rows: list[dict]) -> dict:
    """Pure roll-up: {"turns": rows, "by_role": {...}, "by_cycle": {...}, "totals": {...}}."""
    by_role: dict[str, dict] = {}
    by_cycle: dict[str, dict] = {}
    totals = _empty_bucket()
    for r in rows:
        role = r.get("role") or "?"
        cyc = f"{r.get('phase') or '?'}/{r.get('type') or '?'}"
        _add(by_role.setdefault(role, _empty_bucket()), r)
        _add(by_cycle.setdefault(cyc, _empty_bucket()), r)
        _add(totals, r)
    return {
        "turns": list(rows),
        "by_role": {k: _finish(v) for k, v in by_role.items()},
        "by_cycle": {k: _finish(v) for k, v in by_cycle.items()},
        "totals": _finish(totals),
    }


def _fmt_int(v) -> str:
    return f"{int(v):,}" if isinstance(v, (int, float)) else "-"


def _fmt_cost(v) -> str:
    return f"${v:.3f}" if isinstance(v, (int, float)) else "-"


def render_text(agg: dict) -> str:
    lines = []
    rows = agg["turns"]
    if not rows:
        return "No usage rows yet (headless turns record them; see `tagteam watch --mode headless`)."
    lines.append(f"{'ts':<26} {'cycle':<34} {'role':<8} {'prov':<6} {'status':<12} "
                 f"{'dur':>7} {'in':>10} {'out':>8} {'cache_r':>10} {'cache_w':>9} {'cost':>8}")
    for r in rows:
        cyc = f"{r.get('phase') or '?'}/{r.get('type') or '?'} r{r.get('round')}"
        dur = r.get("duration_ms")
        dur_s = f"{dur/1000:.0f}s" if isinstance(dur, (int, float)) else "-"
        lines.append(f"{(r.get('ts') or '')[:25]:<26} {cyc[:34]:<34} {str(r.get('role'))[:8]:<8} "
                     f"{str(r.get('provider'))[:6]:<6} {str(r.get('status'))[:12]:<12} {dur_s:>7} "
                     f"{_fmt_int(r.get('input_tokens')):>10} {_fmt_int(r.get('output_tokens')):>8} "
                     f"{_fmt_int(r.get('cache_read_tokens')):>10} "
                     f"{_fmt_int(r.get('cache_write_tokens')):>9} {_fmt_cost(r.get('cost_usd')):>8}")

    def summary(b: dict) -> str:
        priced = ("" if b["cost_known_turns"] == b["turns"]
                  else f" ({b['cost_known_turns']}/{b['turns']} priced)")
        mean = (f"{b['mean_duration_ms']/1000:.0f}s"
                if b["mean_duration_ms"] is not None else "-")
        cost = _fmt_cost(b["cost_usd"]) if b["cost_known_turns"] else "-"
        return (f"turns={b['turns']} (ok {b['ok']}, failed {b['failed']})  "
                f"in={_fmt_int(b['input_tokens'])} out={_fmt_int(b['output_tokens'])} "
                f"cache_r={_fmt_int(b['cache_read_tokens'])} "
                f"cache_w={_fmt_int(b['cache_write_tokens'])} "
                f"cost={cost}{priced} mean={mean}")

    def block(title: str, buckets: dict):
        lines.append("")
        lines.append(title)
        for k, b in buckets.items():
            lines.append(f"  {k:<40} {summary(b)}")

    block("By role:", agg["by_role"])
    block("By cycle:", agg["by_cycle"])
    lines.append("")
    lines.append("Totals: " + summary(agg["totals"]))
    return "\n".join(lines)


def usage_command(args: list[str], project_root: str | Path | None = None,
                  out=None) -> int:
    out = out or sys.stdout
    phase = ctype = role = None
    limit = None
    as_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--type" and i + 1 < len(args):
            ctype = args[i + 1]; i += 2
        elif a == "--role" and i + 1 < len(args):
            role = args[i + 1]; i += 2
        elif a == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                print(f"--limit must be an integer, got {args[i+1]!r}", file=out); return 1
            i += 2
        elif a == "--json":
            as_json = True; i += 1
        elif a in ("-h", "--help"):
            print("Usage: tagteam usage [--phase P] [--type plan|impl] [--role lead|reviewer] "
                  "[--limit N] [--json]", file=out)
            return 0
        else:
            print(f"Unknown argument: {a}", file=out); return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    from tagteam import db
    from tagteam.dualwrite import DatabaseMissing
    try:
        conn = db.connect(project_dir=str(project_root))
    except DatabaseMissing:
        rows = []          # Phase 50: read-only and no DB — there is no usage
    else:
        try:
            rows = db.get_usage(conn, phase=phase, cycle_type=ctype)
        finally:
            conn.close()
    if role:
        rows = [r for r in rows if r.get("role") == role]
    if limit is not None:
        rows = rows[-limit:] if limit > 0 else []
    agg = aggregate(rows)
    if as_json:
        print(json.dumps(agg, indent=2), file=out)
    else:
        print(render_text(agg), file=out)
    return 0
