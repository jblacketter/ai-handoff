#!/usr/bin/env python3
"""Reproducible numbers for docs/showcase.md (Phase 36). Stdlib only.

    python scripts/showcase_numbers.py report --as-of YYYY-MM-DD \
        [--usage docs/showcase-data/usage-YYYY-MM-DD.json] [--root DIR] [--json]
    tagteam usage --json | python scripts/showcase_numbers.py export-usage \
        --as-of YYYY-MM-DD > docs/showcase-data/usage-YYYY-MM-DD.json

`report` reads every `*_status.json` + `*_rounds.jsonl` pair under
`docs/handoffs/` and `.tagteam/legacy/` (a `(phase, type)` present in both
is taken from `docs/handoffs/`) and prints the markdown block that lives
between `<!-- showcase-numbers:begin as-of=DATE -->` and
`<!-- showcase-numbers:end -->` in `docs/showcase.md`. The test suite
regenerates it and byte-compares.

`export-usage` is the deterministic sanitizer for `tagteam usage --json`:
turns at or after the as-of cutoff are dropped FIRST, each surviving turn
gets an `attempt` ordinal (1, 2, ... within its (phase, type, round, role)
group in (ts, id) order), and only then are the sensitive / unneeded
fields stripped. Output keys are exactly USAGE_TURN_KEYS.

Methodology (also stated in the block's footnotes):
- as-of is an inclusive UTC calendar day: cutoff = DATE + 1 day, 00:00:00Z.
  Entries with ts >= cutoff are dropped; a cycle with no entry before the
  cutoff is excluded. A legacy entry without ts uses the status file's
  `date` at 00:00:00Z.
- outcome = last surviving entry: APPROVE -> approved (by ruling if the
  content carries the arbiter-ruling prefix); ESCALATE / NEED_HUMAN ->
  escalated; anything else -> in progress; status state `aborted` -> aborted.
- round = one lead SUBMIT_FOR_REVIEW; rounds-to-approval = the APPROVE
  entry's round; AMEND and arbiter rulings are counted in their own rows.
- pushback rate = REQUEST_CHANGES / SUBMIT_FOR_REVIEW.
- stale streak = consecutive unchanged re-submissions, i.e. equal adjacent
  transitions between successive lead submissions (the production rule in
  tagteam.cycle._count_stale_rounds: the first submission is the baseline
  and never stale; 11 identical submissions = 10).
- usage: only status "ok" rows enter duration/token statistics; other
  outcomes are counted; among several ok rows for one (cycle, round, role)
  the highest attempt is used and the rest are reported as retries; null
  token fields are excluded from sums and counted as unknown; curves are
  per (cycle, role, provider) series only and are descriptive.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

RULING_PREFIX = "[ARBITER RULING by "   # tagteam.cycle.RULING_PREFIX
STALE_ROUND_LIMIT = 10                  # tagteam.cycle.STALE_ROUND_LIMIT

USAGE_SCHEMA = "showcase-usage/1"
USAGE_TURN_KEYS = (
    "phase", "type", "round", "role", "attempt", "provider", "status",
    "duration_ms", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "cost_usd",
)
USAGE_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")

CYCLE_DIRS = ("docs/handoffs", ".tagteam/legacy")


class ShowcaseError(Exception):
    pass


# ---------------------------------------------------------------- as-of ----

def parse_as_of(text: str) -> _dt.datetime:
    """`YYYY-MM-DD` -> the exclusive cutoff: next day 00:00:00 UTC."""
    try:
        day = _dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ShowcaseError(f"--as-of must be YYYY-MM-DD, got {text!r}") from exc
    return _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=1)


def parse_ts(text: str | None) -> _dt.datetime | None:
    if not text:
        return None
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def before_cutoff(ts: _dt.datetime | None, cutoff: _dt.datetime) -> bool:
    return ts is not None and ts < cutoff


# --------------------------------------------------------------- cycles ----

def _stale_streak(submissions: list[str]) -> int:
    """Copy of tagteam.cycle._count_stale_rounds' loop, applied to the whole
    history: the longest run of equal adjacent transitions."""
    best = 0
    run = 0
    for i in range(1, len(submissions)):
        if submissions[i] == submissions[i - 1]:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def load_cycles(root: Path, cutoff: _dt.datetime) -> list[dict]:
    """Every (phase, type) with a status file, docs/handoffs winning.

    Precedence is decided BEFORE the as-of filter: the first directory in
    CYCLE_DIRS that has a status file for a (phase, type) owns that key,
    even if none of its entries survives the cutoff — a superseded legacy
    copy must never resurface just because the canonical cycle is newer
    than the as-of date."""
    seen: dict[tuple[str, str], dict] = OrderedDict()
    reserved: set[tuple[str, str]] = set()
    for sub in CYCLE_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for status_file in sorted(d.glob("*_status.json")):
            stem = status_file.name[: -len("_status.json")]
            try:
                status = json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ShowcaseError(f"unreadable status file {status_file}: {exc}") from exc
            phase = status.get("phase") or stem.rsplit("_", 1)[0]
            ctype = status.get("type") or stem.rsplit("_", 1)[1]
            key = (phase, ctype)
            if key in reserved:
                continue
            reserved.add(key)
            rounds_file = d / f"{stem}_rounds.jsonl"
            entries: list[dict] = []
            if rounds_file.exists():
                for line in rounds_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except ValueError as exc:
                            raise ShowcaseError(f"bad JSONL in {rounds_file}: {exc}") from exc
            fallback = parse_ts((status.get("date") or "") + "T00:00:00+00:00") if status.get("date") else None
            kept = []
            for e in entries:
                ts = parse_ts(e.get("ts")) or fallback
                if before_cutoff(ts, cutoff):
                    kept.append(e)
            if not kept:
                continue
            seen[key] = {"phase": phase, "type": ctype, "status": status, "entries": kept, "source": sub}
    return list(seen.values())


def analyze_cycle(c: dict) -> dict:
    entries = c["entries"]
    subs = [e for e in entries if e.get("role") == "lead" and e.get("action") == "SUBMIT_FOR_REVIEW"]
    last = entries[-1]
    if (c["status"].get("state") or c["status"].get("status")) == "aborted":
        outcome = "aborted"
    elif last.get("action") == "APPROVE":
        outcome = "approved by ruling" if str(last.get("content", "")).startswith(RULING_PREFIX) else "approved"
    elif last.get("action") in ("ESCALATE", "NEED_HUMAN"):
        outcome = "escalated"
    else:
        outcome = "in progress"
    approve_round = int(last.get("round") or 0) if outcome.startswith("approved") else None
    actions = Counter()
    for e in entries:
        a = e.get("action")
        if a in ("APPROVE", "REQUEST_CHANGES") and str(e.get("content", "")).startswith(RULING_PREFIX):
            actions["RULING"] += 1
        else:
            actions[a] += 1
    return {
        "phase": c["phase"], "type": c["type"], "outcome": outcome,
        "rounds": len(subs), "approve_round": approve_round,
        "actions": actions, "stale_streak": _stale_streak([s.get("content", "") for s in subs]),
    }


# ---------------------------------------------------------------- usage ----

def export_usage(raw: dict, as_of: str) -> dict:
    cutoff = parse_as_of(as_of)
    turns = raw.get("turns") if isinstance(raw, dict) else None
    if not isinstance(turns, list):
        raise ShowcaseError("usage input must be the JSON of `tagteam usage --json` (an object with a `turns` list)")
    kept = []
    for t in turns:
        if not isinstance(t, dict):
            raise ShowcaseError("every turn must be an object")
        ts = parse_ts(t.get("ts"))
        if before_cutoff(ts, cutoff):
            kept.append((ts, t.get("id") if isinstance(t.get("id"), int) else 0, t))
    kept.sort(key=lambda k: (k[0], k[1]))
    ordinal: Counter = Counter()
    out = []
    for _ts, _id, t in kept:
        g = (t.get("phase"), t.get("type"), t.get("round"), t.get("role"))
        ordinal[g] += 1
        row = {k: t.get(k) for k in USAGE_TURN_KEYS if k != "attempt"}
        row["attempt"] = ordinal[g]
        out.append(row)
    out.sort(key=lambda r: (str(r["phase"]), str(r["type"]), int(r["round"] or 0), str(r["role"]), r["attempt"]))
    return {"schema": USAGE_SCHEMA, "as_of": as_of, "turns": out}


def validate_usage(snap: dict, as_of: str) -> list[dict]:
    if not isinstance(snap, dict) or snap.get("schema") != USAGE_SCHEMA:
        raise ShowcaseError(f"usage snapshot must have schema {USAGE_SCHEMA!r}")
    if snap.get("as_of") != as_of:
        raise ShowcaseError(f"usage snapshot as_of {snap.get('as_of')!r} != report --as-of {as_of!r}")
    turns = snap.get("turns")
    if not isinstance(turns, list):
        raise ShowcaseError("usage snapshot `turns` must be a list")
    allowed = set(USAGE_TURN_KEYS)
    for t in turns:
        if not isinstance(t, dict) or set(t) != allowed:
            raise ShowcaseError(f"usage turn keys must be exactly {sorted(allowed)}")
    return turns


def analyze_usage(turns: list[dict]) -> dict:
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for t in turns:
        by_key[(t["phase"], t["type"], t["round"], t["role"])].append(t)
    outcomes: Counter = Counter()
    groups: dict[tuple, dict] = OrderedDict()   # (role, provider) -> stats
    curves: dict[tuple, dict[int, int | None]] = defaultdict(dict)  # (phase,type,role,provider) -> round -> input
    for key in sorted(by_key, key=lambda k: (str(k[0]), str(k[1]), int(k[2] or 0), str(k[3]))):
        rows = sorted(by_key[key], key=lambda r: r["attempt"])
        ok = [r for r in rows if r["status"] == "ok"]
        for r in rows:
            if r["status"] != "ok":
                outcomes[r["status"] or "unknown"] += 1
        if not ok:
            continue
        chosen = ok[-1]
        retries = len(ok) - 1
        g = groups.setdefault((chosen["role"], chosen["provider"]), {
            "turns": 0, "retries": 0, "durations": [], "priced": 0, "cost": 0.0,
            "sums": Counter(), "unknown": Counter()})
        g["turns"] += 1
        g["retries"] += retries
        if isinstance(chosen.get("duration_ms"), (int, float)):
            g["durations"].append(chosen["duration_ms"] / 1000.0)
        for k in USAGE_TOKEN_KEYS:
            v = chosen.get(k)
            if isinstance(v, (int, float)):
                g["sums"][k] += int(v)
            else:
                g["unknown"][k] += 1
        if isinstance(chosen.get("cost_usd"), (int, float)):
            g["priced"] += 1
            g["cost"] += float(chosen["cost_usd"])
        v = chosen.get("input_tokens")
        curves[(chosen["phase"], chosen["type"], chosen["role"], chosen["provider"])][int(chosen["round"] or 0)] = (
            int(v) if isinstance(v, (int, float)) else None)
    return {"outcomes": outcomes, "groups": groups, "curves": curves}


# --------------------------------------------------------------- render ----

def _fmt_int(n) -> str:
    return f"{int(n):,}"


def _stats(values: list[int]) -> str:
    if not values:
        return "—"
    return f"median {statistics.median(values):g} · mean {statistics.mean(values):.2f} · max {max(values)}"


def _dist(values: list[int]) -> str:
    if not values:
        return "—"
    c = Counter(values)
    return ", ".join(f"{k}: {c[k]}" for k in sorted(c))


def render_report(as_of: str, cycles: list[dict], usage: dict | None) -> str:
    rows = [analyze_cycle(c) for c in cycles]
    types = ("plan", "impl")
    L: list[str] = []
    L.append(f"### Review cycles (as of {as_of}, UTC)")
    L.append("")
    L.append("| | plan | impl | all |")
    L.append("|---|---|---|---|")

    def col(fn):
        vals = [fn([r for r in rows if r["type"] == t]) for t in types] + [fn(rows)]
        return " | ".join(str(v) for v in vals)

    L.append(f"| cycles | {col(len)} |")
    for outcome in ("approved", "approved by ruling", "escalated", "in progress", "aborted"):
        L.append(f"| {outcome} | {col(lambda rs, o=outcome: sum(1 for r in rs if r['outcome'] == o))} |")
    L.append(f"| approved at round 1 | {col(lambda rs: sum(1 for r in rs if r['approve_round'] == 1))} |")
    L.append(f"| rounds to approval | {col(lambda rs: _stats([r['approve_round'] for r in rs if r['approve_round']]))} |")
    L.append(f"| rounds-to-approval distribution (rounds: cycles) | {col(lambda rs: _dist([r['approve_round'] for r in rs if r['approve_round']]))} |")
    L.append(f"| longest stale streak in any cycle | {col(lambda rs: max([r['stale_streak'] for r in rs], default=0))} |")
    L.append("")
    total = Counter()
    for r in rows:
        total.update(r["actions"])
    subs, rc = total.get("SUBMIT_FOR_REVIEW", 0), total.get("REQUEST_CHANGES", 0)
    L.append("### Rounds")
    L.append("")
    L.append("| entries | count |")
    L.append("|---|---|")
    for label, key in (("lead submissions (rounds)", "SUBMIT_FOR_REVIEW"), ("reviewer: request changes", "REQUEST_CHANGES"),
                       ("reviewer: approve", "APPROVE"), ("reviewer: escalate", "ESCALATE"), ("reviewer: need human", "NEED_HUMAN"),
                       ("lead amendments", "AMEND"), ("arbiter rulings", "RULING")):
        L.append(f"| {label} | {total.get(key, 0)} |")
    L.append(f"| pushback rate (request changes ÷ submissions) | {(rc / subs * 100):.0f}% |" if subs else "| pushback rate | — |")
    L.append(f"| auto-escalation limit | {STALE_ROUND_LIMIT} consecutive stale rounds (never reached) |"
             if max([r["stale_streak"] for r in rows], default=0) < STALE_ROUND_LIMIT
             else f"| auto-escalation limit | {STALE_ROUND_LIMIT} consecutive stale rounds |")
    L.append("")
    if usage is not None:
        L.append(f"### Headless turns (as of {as_of}, UTC)")
        L.append("")
        L.append("| role · provider | turns | retries | mean duration | input tokens | output tokens | cache read | cache write | priced |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for (role, provider), g in usage["groups"].items():
            dur = f"{statistics.mean(g['durations']):.0f} s" if g["durations"] else "—"
            def tok(k):
                s = _fmt_int(g["sums"][k])
                return s + (f" (+{g['unknown'][k]} unknown)" if g["unknown"][k] else "")
            priced = f"{g['priced']}/{g['turns']}" + (f" · ${g['cost']:.2f}" if g["priced"] else "")
            L.append(f"| {role} · {provider} | {g['turns']} | {g['retries']} | {dur} | {tok('input_tokens')} | {tok('output_tokens')} | {tok('cache_read_tokens')} | {tok('cache_write_tokens')} | {priced} |")
        if usage["outcomes"]:
            L.append("")
            L.append("Attempts not counted above (by outcome): " + ", ".join(f"{k}: {v}" for k, v in sorted(usage["outcomes"].items())) + ".")
        L.append("")
        L.append("Round-over-round input tokens per cycle (one provider per series; descriptive only):")
        L.append("")
        L.append("| cycle | role · provider | r1 → rN |")
        L.append("|---|---|---|")
        for (phase, ctype, role, provider), pts in usage["curves"].items():
            series = " → ".join(_fmt_int(pts[r]) if pts[r] is not None else "?" for r in sorted(pts))
            L.append(f"| {phase}/{ctype} | {role} · {provider} | {series} |")
        L.append("")
    L.append("<sub>Method: cycles = one (phase, type) with a status file under `docs/handoffs/` or `.tagteam/legacy/` "
             "(`docs/handoffs/` wins); as-of is an inclusive UTC day (entries at or after the next 00:00Z dropped, cycles with no earlier entry excluded); "
             "outcome from the last surviving entry; a round is one lead submission; rounds to approval = the approving entry's round; "
             "amendments and arbiter rulings are their own rows; pushback = request-changes ÷ submissions; "
             f"a stale streak counts consecutive unchanged re-submissions (the first submission is the baseline and is not stale) and auto-escalation fires at {STALE_ROUND_LIMIT}. "
             "Headless: only `ok` turns enter duration/token statistics, other outcomes are listed, the highest attempt per (cycle, round, role) is used and earlier ok attempts count as retries, "
             "null token fields are excluded and shown as unknown; token accounting differs by provider (Codex reports cache reads inside input tokens, Claude reports them separately), "
             "so series never mix providers and no cross-provider comparison is implied; cost only where the provider priced the turn.</sub>")
    return "\n".join(L) + "\n"


def report_json(as_of: str, cycles: list[dict], usage: dict | None) -> dict:
    rows = [analyze_cycle(c) for c in cycles]
    for r in rows:
        r["actions"] = dict(r["actions"])
    out = {"as_of": as_of, "cycles": rows}
    if usage is not None:
        out["usage"] = {
            "outcomes": dict(usage["outcomes"]),
            "groups": {f"{k[0]}·{k[1]}": {"turns": g["turns"], "retries": g["retries"], "priced": g["priced"], "cost": g["cost"],
                                          "sums": dict(g["sums"]), "unknown": dict(g["unknown"]),
                                          "mean_duration_s": (statistics.mean(g["durations"]) if g["durations"] else None)}
                       for k, g in usage["groups"].items()},
            "curves": {"/".join(str(x) for x in k): {str(r): v for r, v in pts.items()} for k, pts in usage["curves"].items()},
        }
    return out


# ------------------------------------------------------------------ CLI ----

def build_report(root: Path, as_of: str, usage_file: Path | None) -> tuple[list[dict], dict | None]:
    cutoff = parse_as_of(as_of)
    cycles = load_cycles(root, cutoff)
    usage = None
    if usage_file is not None:
        try:
            snap = json.loads(usage_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ShowcaseError(f"unreadable usage snapshot {usage_file}: {exc}") from exc
        usage = analyze_usage(validate_usage(snap, as_of))
    return cycles, usage


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="showcase_numbers.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="print the numbers block")
    r.add_argument("--as-of", required=True)
    r.add_argument("--usage", type=Path, default=None)
    r.add_argument("--root", type=Path, default=Path("."))
    r.add_argument("--json", action="store_true")
    e = sub.add_parser("export-usage", help="sanitize `tagteam usage --json` from stdin")
    e.add_argument("--as-of", required=True)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stdin):      # the block carries "→" / "÷"; Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        if args.cmd == "report":
            cycles, usage = build_report(args.root, args.as_of, args.usage)
            if args.json:
                sys.stdout.write(json.dumps(report_json(args.as_of, cycles, usage), indent=1, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_report(args.as_of, cycles, usage))
            return 0
        raw_text = sys.stdin.read()
        try:
            raw = json.loads(raw_text)
        except ValueError as exc:
            raise ShowcaseError(f"stdin is not JSON: {exc}") from exc
        parse_as_of(args.as_of)
        sys.stdout.write(json.dumps(export_usage(raw, args.as_of), indent=1, sort_keys=True) + "\n")
        return 0
    except ShowcaseError as exc:
        print(f"showcase_numbers: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
