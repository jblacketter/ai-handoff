#!/usr/bin/env python3
"""Seed disposable demo projects for the docs/media screenshots (Phase 36).

    python scripts/showcase_seed.py DIR            # DIR must not exist yet
    tagteam serve --theme cockpit --dir DIR/demo/demo-api --port 8080
    tagteam hub --registry DIR/registry.json --port 8090

Builds, under DIR/demo/, three projects with generic names and synthetic
round text authored right here (nothing is copied from a real project):

  demo-api   plan cycle ESCALATED at round 3, a decision brief on disk and
             in the DB, a declining per-round usage series  -> Needs you
  demo-web   impl cycle, reviewer turn owed for hours, no watcher -> Waiting · stale
  demo-docs  impl cycle approved                             -> Quiet

plus DIR/registry.json listing exactly those three paths, so `tagteam hub
--registry DIR/registry.json` never reads ~/.tagteam/projects.json.

Pick DIR outside /tmp, /private/tmp and /var/folders (the hub hides those
as scratch), e.g. ~/tagteam-demo. Delete DIR when done. This script imports
tagteam (dev script) and writes only under DIR.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEAD, REVIEWER = "Claude", "Codex"

API_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Plan for the rate-limit middleware: token bucket per API key, "
     "limits from config, 429 with Retry-After. Files: middleware/ratelimit.py, tests/test_ratelimit.py."),
    ("reviewer", "REQUEST_CHANGES", 1, "The bucket is keyed on the raw header — hash it before it reaches "
     "the store, and say what happens when the store is unreachable (fail open or closed?)."),
    ("lead", "SUBMIT_FOR_REVIEW", 2, "Key is now sha256(api_key). Store outage: fail OPEN with a warning "
     "log — availability over strictness for this service."),
    ("reviewer", "REQUEST_CHANGES", 2, "Fail-open on a store outage means the limit disappears exactly when "
     "the system is under stress. Fail closed with a short in-memory fallback bucket instead."),
    ("lead", "SUBMIT_FOR_REVIEW", 3, "I disagree: this API's SLO is availability, and a closed limiter "
     "during a Redis blip would page the on-call for a self-inflicted outage. Keeping fail-open, adding "
     "a metric + alert on the fallback path."),
    ("reviewer", "ESCALATE", 3, "Genuine product decision, not a code question: fail-open (availability) "
     "vs fail-closed (protection). Both positions are defensible; the arbiter should pick."),
]

WEB_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Implemented the checkout form validation per the approved plan; "
     "12 new tests, all green."),
    ("reviewer", "REQUEST_CHANGES", 1, "Postal-code rule rejects valid Canadian codes with a space. Add "
     "the case and normalize before validating."),
    ("lead", "SUBMIT_FOR_REVIEW", 2, "Normalized whitespace and casing before the postal-code check; "
     "added CA/UK cases."),
]

DOCS_ROUNDS = [
    ("lead", "SUBMIT_FOR_REVIEW", 1, "Implemented the docs build: mkdocs config, nav from the roadmap, CI job."),
    ("reviewer", "APPROVE", 1, "Approved. Build is reproducible and the nav matches the roadmap."),
]

BRIEF = """# Decision brief — demo-api / plan r3 (escalated)

**Crux:** what a rate limiter should do when its backing store is unreachable.

**Lead (Claude):** fail *open* — this API's SLO is availability; a closed
limiter during a Redis blip would page on-call for a self-inflicted outage.
Adds a metric and alert on the fallback path.

**Reviewer (Codex):** fail *closed* with a small in-memory fallback bucket —
the limit must not vanish exactly when the system is under stress.

**What was checked:** the plan text (rounds 1–3), the config surface,
the SLO doc referenced in round 3.

**Recommendation (confidence: medium):** fail open **with** the in-memory
fallback bucket the reviewer proposed as a ceiling — availability first,
but not unbounded. Rule `request-changes` asking for that hybrid.

```
tagteam rule request-changes --content "Fail open, but keep the reviewer's in-memory fallback bucket as a ceiling."
tagteam rule approve --content "Fail open with the metric/alert; ship it."
```
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _write_yaml(project: Path) -> None:
    (project / "tagteam.yaml").write_text(
        "agents:\n"
        f"  lead:\n    name: {LEAD}\n    command: claude\n"
        f"  reviewer:\n    name: {REVIEWER}\n    command: codex\n"
        "briefer:\n  enabled: true\n"
        "serve:\n  theme: cockpit\n", encoding="utf-8")


def _play(project: Path, phase: str, ctype: str, rounds) -> None:
    from tagteam import cycle
    first = True
    for role, action, rnd, text in rounds:
        if first:
            cycle.init_cycle(phase, ctype, LEAD, REVIEWER, text, project_dir=str(project), updated_by=LEAD)
            first = False
        else:
            cycle.add_round(phase, ctype, role, action, rnd, text, project_dir=str(project),
                            updated_by=LEAD if role == "lead" else REVIEWER)


def _age_state(project: Path, hours: float) -> None:
    """Make the current turn look `hours` old (state file `updated_at`)."""
    p = project / "handoff-state.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    then = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
    st["updated_at"] = then
    if st.get("history"):
        st["history"][-1]["timestamp"] = then
    p.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def _seed_usage(project: Path, phase: str, ctype: str, series: list[tuple[int, str, str, int, int]]) -> None:
    from tagteam import db
    conn = db.connect(project_dir=str(project))
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for i, (rnd, role, provider, inp, out) in enumerate(series):
            db.add_usage(conn, ts=_iso(base + timedelta(minutes=9 * i)), phase=phase, type=ctype, round=rnd,
                         role=role, agent=(LEAD if role == "lead" else REVIEWER), provider=provider,
                         status="ok", exit_code=0, duration_ms=90_000 + 20_000 * (i % 3),
                         input_tokens=inp, output_tokens=out,
                         cache_read_tokens=int(inp * 0.9), cache_write_tokens=0,
                         cost_usd=(round(inp / 1_000_000 * 3 + out / 1_000_000 * 15, 3) if provider == "claude" else None),
                         num_turns=1)
        db.upsert_rate_limit(conn, provider="claude", kind="five_hour", status="allowed",
                             resets_at=_iso(datetime.now(timezone.utc) + timedelta(hours=2, minutes=40)),
                             payload={"utilization": 0.42}, ts=_iso(datetime.now(timezone.utc)))
    finally:
        conn.close()


def _seed_brief(project: Path, phase: str, ctype: str) -> None:
    from tagteam import briefer, db
    ev, why = briefer.event_for_cycle(project, phase, ctype)
    if ev is None:
        raise SystemExit(f"seed: no escalation event for {phase}/{ctype}: {why}")
    conn = db.connect(project_dir=str(project))
    try:
        now = _iso(datetime.now(timezone.utc))
        claimed = db.claim_brief(conn, ts=now, phase=phase, cycle_type=ctype, round_=ev.round,
                                 cycle_state=ev.cycle_state, event_key=ev.event_key, kind="auto",
                                 runner_pid=None, runner_ident=None, event_row_id=ev.event_row_id,
                                 provider="claude")
        if claimed is None:
            raise SystemExit("seed: brief claim refused")
        brief_id, attempt = claimed
        path = briefer.brief_path(project, ev, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BRIEF, encoding="utf-8")
        briefer.alias_path(project, phase, ctype).write_text(BRIEF, encoding="utf-8")
        db.set_brief_stem(conn, brief_id, path.stem)
        db.finish_brief(conn, brief_id, status="ok", ts=now, stem=path.stem, path=str(path),
                        content=BRIEF, model="claude", duration_ms=48_000)
    finally:
        conn.close()


def seed(root: Path) -> dict:
    if root.exists():
        raise SystemExit(f"seed: {root} already exists — pick a fresh directory")
    demo = root / "demo"
    projects = {}
    for name in ("demo-api", "demo-web", "demo-docs"):
        p = demo / name
        (p / "docs" / "handoffs").mkdir(parents=True)
        (p / "docs" / "escalations").mkdir(parents=True)
        (p / ".tagteam").mkdir()
        _write_yaml(p)
        (p / "docs" / "roadmap.md").write_text(
            "# Roadmap\n\n### Phase 1: demo\n- **Status:** In progress\n", encoding="utf-8")
        projects[name] = p

    _play(projects["demo-api"], "rate-limit-middleware", "plan", API_ROUNDS)
    _seed_usage(projects["demo-api"], "rate-limit-middleware", "plan", [
        (1, "reviewer", "codex", 1_412_000, 6_100), (1, "lead", "claude", 52, 14_800),
        (2, "reviewer", "codex", 968_000, 5_300), (2, "lead", "claude", 61, 12_100),
        (3, "reviewer", "codex", 611_000, 4_200), (3, "lead", "claude", 48, 9_900),
    ])
    _seed_brief(projects["demo-api"], "rate-limit-middleware", "plan")
    _age_state(projects["demo-api"], hours=1.2)

    _play(projects["demo-web"], "checkout-validation", "impl", WEB_ROUNDS)
    _age_state(projects["demo-web"], hours=5.5)

    _play(projects["demo-docs"], "docs-build", "impl", DOCS_ROUNDS)
    _age_state(projects["demo-docs"], hours=30)

    reg = root / "registry.json"
    reg.write_text(json.dumps([str(p.resolve()) for p in projects.values()], indent=2) + "\n", encoding="utf-8")
    return {"registry": str(reg), "projects": {k: str(v) for k, v in projects.items()}}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    info = seed(Path(argv[0]).expanduser())
    print(json.dumps(info, indent=2))
    print()
    print("cockpit:  tagteam serve --theme cockpit --dir", info["projects"]["demo-api"], "--port 8080")
    print("hub:      tagteam hub --registry", info["registry"], "--port 8090")
    return 0


if __name__ == "__main__":
    sys.exit(main())
