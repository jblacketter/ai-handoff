#!/usr/bin/env python3
"""Regenerate tagteam/data/vendored_contract_hashes.json from git history.

    python scripts/contract_hashes.py [--root DIR] [--check]

Every distinct blob of ``tagteam/data/.claude/skills/handoff/SKILL.md`` that
was ever committed is a contract ``tagteam setup`` may have vendored into a
project. Its sha256 is recorded with the first release tag containing it (or
``<sha>`` when untagged). ``tagteam setup`` removes a project-local handoff
skill only when its content hash is in this file.

``--check`` exits 1 if the file on disk differs from what git history yields.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

CONTRACT = "tagteam/data/.claude/skills/handoff/SKILL.md"
OUT = "tagteam/data/vendored_contract_hashes.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _is_shallow(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
    except subprocess.CalledProcessError:
        return False


def compute(root: Path) -> dict:
    commits = _git(root, "log", "--format=%H", "--follow", "--", CONTRACT).split()
    hashes: dict[str, str] = {}
    for c in reversed(commits):  # oldest first, so the first tag wins
        try:
            blob = subprocess.run(["git", "-C", str(root), "show", f"{c}:{CONTRACT}"],
                                  check=True, capture_output=True).stdout
        except subprocess.CalledProcessError:
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if digest in hashes:
            continue
        tag = _git(root, "tag", "--contains", c, "--list", "v*", "--sort=v:refname").split()
        hashes[digest] = tag[0].lstrip("v") if tag else c[:12]
    # the working-tree copy (an unreleased edit) counts too
    wt = root / CONTRACT
    if wt.is_file():
        hashes.setdefault(hashlib.sha256(wt.read_bytes()).hexdigest(), "working-tree")
    return {"contract": CONTRACT, "hashes": hashes}


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    check = False
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1]).resolve(); i += 2
        elif argv[i] == "--check":
            check = True; i += 1
        else:
            print(__doc__.strip(), file=sys.stderr); return 1
    out = root / OUT
    if check and _is_shallow(root):
        print(f"{OUT}: check skipped — shallow clone (history incomplete); fetch-depth 0 to verify")
        return 0
    data = compute(root)
    text = render(data)
    if check:
        # Compare the hash SET only. Labels (first release tag containing the
        # blob) legitimately change when a tag is created after the file was
        # generated — the release commit is tagged after release.py ran — and
        # only the set gates removal in `tagteam setup`.
        try:
            current = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"{OUT} missing or unreadable — run scripts/contract_hashes.py", file=sys.stderr)
            return 1
        have = set((current.get("hashes") or {}).keys())
        want = set(data["hashes"].keys())
        if have != want:
            print(f"{OUT} is stale — run scripts/contract_hashes.py "
                  f"(missing {len(want - have)}, extra {len(have - want)})", file=sys.stderr)
            return 1
        drift = sum(1 for k in want if current["hashes"].get(k) != data["hashes"][k])
        print(f"{OUT} up to date ({len(want)} hashes"
              + (f"; {drift} label(s) would change on regeneration)" if drift else ")"))
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}: {len(json.loads(text)['hashes'])} contract version(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
