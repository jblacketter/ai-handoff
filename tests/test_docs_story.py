"""Phase 36: the visual story stays accurate — README / how-tagteam-works /
showcase / media manifest integrity, the mermaid ↔ SVG diagram contract,
portfolio-asset conventions, screenshot safety, CLI-coverage and the
narrow glossary guard."""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
HTW = REPO / "docs" / "how-tagteam-works.md"
SHOWCASE = REPO / "docs" / "showcase.md"
MEDIA = REPO / "docs" / "media"
MANIFEST = MEDIA / "README.md"
STORY_DOCS = [README, HTW, SHOWCASE, MANIFEST]

# README mermaid block index (in document order) -> the SVG that mirrors it
DIAGRAM_CONTRACT = {
    0: "tagteam-loop.svg",
    1: "tagteam-cycle.svg",
    2: "tagteam-modes.svg",
    3: "tagteam-headless.svg",
}
SVG_VIEWBOX = {"tagteam-loop.svg": "0 0 800 260", "tagteam-cycle.svg": "0 0 800 260",
               "tagteam-modes.svg": "0 0 800 240", "tagteam-headless.svg": "0 0 800 260"}
SCREENSHOTS = ["cockpit-needs-you.png", "cockpit-usage.png", "hub.png", "cockpit-lead.png"]

FENCE_RE = re.compile(r"^```(\w*)\s*$", re.M)
MERMAID_TYPES = ("flowchart LR", "flowchart TD", "flowchart TB", "stateDiagram-v2", "sequenceDiagram")


def _fences(text: str) -> list[tuple[str, str]]:
    """[(lang, body)] for every fenced block; asserts fences are balanced."""
    lines = text.splitlines()
    out, lang, buf, open_ = [], None, [], False
    for line in lines:
        m = re.match(r"^```(\w*)\s*$", line)
        if m and not open_:
            open_, lang, buf = True, m.group(1), []
        elif line.strip() == "```" and open_:
            out.append((lang or "", "\n".join(buf)))
            open_ = False
        elif open_:
            buf.append(line)
    assert not open_, "unbalanced code fence"
    return out


def _mermaid_blocks(text: str) -> list[str]:
    return [body for lang, body in _fences(text) if lang == "mermaid"]


def _text_without_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ------------------------------------------------------------- integrity ----

@pytest.mark.parametrize("doc", STORY_DOCS, ids=lambda p: p.name)
def test_story_docs_exist_with_balanced_fences_and_resolving_links(doc):
    assert doc.exists(), doc
    text = doc.read_text(encoding="utf-8")
    _fences(text)
    # markdown links / images and html img src that are relative paths
    targets = re.findall(r"\]\(([^)#\s]+)(?:#[^)\s]*)?\)", text) + re.findall(r'src="([^"]+)"', text)
    for t in targets:
        if re.match(r"^[a-z]+://", t) or t.startswith("mailto:"):
            continue
        p = (doc.parent / t).resolve()
        assert p.exists(), f"{doc.name}: link target missing: {t}"


def test_readme_anchor_links_into_htw_resolve():
    text = README.read_text(encoding="utf-8")
    htw = HTW.read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', htw))
    for frag in re.findall(r"how-tagteam-works\.md#([\w-]+)", text):
        assert frag in anchors, f"README links to how-tagteam-works.md#{frag} which has no anchor"
    for frag in re.findall(r"\]\(#([\w-]+)\)", text):    # in-README anchors: derived from headings
        heads = [re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-") for h in re.findall(r"^#+\s+(.*)$", text, re.M)]
        assert frag in heads, f"README in-page link #{frag} matches no heading"


# --------------------------------------------------------- mermaid lint ----

def _lint_flowchart(body: str) -> set[str]:
    lines = [l.strip() for l in body.splitlines()[1:] if l.strip()]
    defined, used = set(), set()
    node_def = re.compile(r'([A-Za-z_]\w*)\s*(\[\(|\[\[|\[|\(\(|\(|\{)')
    for line in lines:
        assert line.count("[") == line.count("]") and line.count("(") == line.count(")") and line.count("{") == line.count("}"), f"unbalanced brackets: {line}"
        assert line.count('"') % 2 == 0, f"unbalanced quotes: {line}"
        if line.startswith(("subgraph", "end", "direction", "%%", "classDef", "class ", "style ")):
            continue
        for m in node_def.finditer(line):
            defined.add(m.group(1))
        # ids on either side of an edge
        parts = re.split(r"\s*(?:-->|-\.->|\.->|-\.\s*\"[^\"]*\"\s*\.->|--\s*\"[^\"]*\"\s*-->|---|-\.-|==>)\s*", line)
        if len(parts) > 1:
            for part in parts:
                m = re.match(r"([A-Za-z_]\w*)", part.strip())
                if m:
                    used.add(m.group(1))
    undefined = used - defined
    assert not undefined, f"edge references undefined node(s): {sorted(undefined)}"
    return defined


def _lint_state(body: str) -> None:
    for line in [l.strip() for l in body.splitlines()[1:] if l.strip()]:
        assert re.match(r"^(\[\*\]|[A-Za-z_]\w*)\s*-->\s*(\[\*\]|[A-Za-z_]\w*)(\s*:\s*.+)?$", line) or line.startswith(("state ", "%%")), f"unexpected stateDiagram line: {line}"


def test_readme_mermaid_blocks_are_four_known_and_lint_clean():
    blocks = _mermaid_blocks(README.read_text(encoding="utf-8"))
    assert len(blocks) == 4, "README must carry exactly the four diagrams of the contract"
    for i, b in enumerate(blocks):
        first = b.strip().splitlines()[0].strip()
        assert first in MERMAID_TYPES, f"block {i}: unknown diagram type {first!r}"
        if first.startswith("flowchart"):
            _lint_flowchart(b)
        elif first == "stateDiagram-v2":
            _lint_state(b)
    assert blocks[1].strip().startswith("stateDiagram-v2")


# ------------------------------------------------------- SVG conventions ----

_FORBIDDEN_SVG = ("<image", "<foreignObject", "@import", "<script", "xlink:href")
_EXTERNAL_URL_RE = re.compile(r"url\((?!#)")   # url(#marker) is an internal reference; anything else is external


@pytest.mark.parametrize("name", sorted(DIAGRAM_CONTRACT.values()))
def test_svg_meets_portfolio_conventions(name):
    p = MEDIA / name
    assert p.exists(), p
    raw = p.read_text(encoding="utf-8")
    assert p.stat().st_size <= 20 * 1024, f"{name} is larger than 20 KB"
    for bad in _FORBIDDEN_SVG:
        assert bad not in raw, f"{name} contains {bad!r}"
    assert not _EXTERNAL_URL_RE.search(raw), f"{name} contains an external url()"
    assert 'xmlns="http://www.w3.org/2000/svg"' in raw
    assert len(re.findall(r"https?://", raw)) == 1, f"{name}: external reference (only the svg xmlns URL is allowed)"
    root = ET.fromstring(raw)
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == SVG_VIEWBOX[name], f"{name}: viewBox {root.get('viewBox')!r}"
    assert root.get("role") == "img"
    assert (root.get("aria-label") or "").strip(), f"{name}: aria-label required"


def _svg_labels(name: str, cls: str) -> list[str]:
    root = ET.fromstring((MEDIA / name).read_text(encoding="utf-8"))
    out = []
    for el in root.iter():
        if el.tag.endswith("text") and (el.get("class") or "").split().count(cls):
            txt = "".join(el.itertext())
            out.append(re.sub(r"\s+", " ", txt).strip())
    return out


def _svg_node_labels(name: str) -> list[str]:
    return _svg_labels(name, "node")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("<br/>", " ").replace("<br>", " ")).strip().lower()


@pytest.mark.parametrize("index,name", sorted(DIAGRAM_CONTRACT.items()))
def test_svg_node_labels_appear_in_the_matching_mermaid_block(index, name):
    blocks = _mermaid_blocks(README.read_text(encoding="utf-8"))
    mermaid = _norm(blocks[index])
    labels = _svg_node_labels(name)
    assert labels, f"{name}: mark node labels with class=\"node\""
    for label in labels:
        assert _norm(label) in mermaid, f"{name}: node label {label!r} not found in README mermaid block {index}"
    for label in _svg_labels(name, "edge"):
        assert _norm(label) in mermaid, f"{name}: edge label {label!r} not found in README mermaid block {index}"
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert name in manifest, f"{name} missing from docs/media/README.md"


_EDGE_RE = re.compile(r'^\s*([A-Za-z_]\w*)(?:\[[^\]]*\]|\([^)]*\)|\[\([^)]*\)\])?\s*(?:--\s*"([^"]*)"\s*-->|-\.\s*"([^"]*)"\s*\.->|-->|-\.->)\s*([A-Za-z_]\w*)', re.M)


def _edges(block: str) -> dict[tuple[str, str], str]:
    out = {}
    for m in _EDGE_RE.finditer(block):
        out[(m.group(1), m.group(4))] = _norm(m.group(2) or m.group(3) or "")
    return out


def test_loop_diagram_routes_the_arbiters_ruling_correctly():
    """The hero diagram's semantics, not just its labels: both reviews can
    escalate; the arbiter's ruling takes the reviewer's seat — request
    changes goes back to the lead step, approving the plan goes to
    implementation, approving the implementation advances the roadmap.
    A generic 'ruling' edge (which would suggest approval also returns to
    the lead) is not allowed."""
    block = _mermaid_blocks(README.read_text(encoding="utf-8"))[0]
    edges = _edges(block)
    assert edges[("PR", "A")] == "escalate" and edges[("IR", "A")] == "escalate"
    assert "request changes" in edges[("A", "P")] and "approve" not in edges[("A", "P")]
    assert "approve" in edges[("A", "I")] and "request changes" in edges[("A", "I")]
    assert "approve" in edges[("A", "R")] and "request changes" not in edges[("A", "R")]
    assert edges[("A", "R")].endswith("(impl)") and "(plan)" in edges[("A", "P")]
    for (src, dst), label in edges.items():
        if src == "A":
            assert label != "ruling", f"generic ruling edge A->{dst}"
    # the SVG mirrors the same three ruling outcomes and both escalations
    edge_labels = [_norm(l) for l in _svg_labels("tagteam-loop.svg", "edge")]
    assert edge_labels.count("escalate") == 2
    assert any("request changes (plan)" in l for l in edge_labels)
    assert any("approve (plan)" in l and "request changes (impl)" in l for l in edge_labels)
    assert any(l == "approve (impl)" for l in edge_labels)
    for doc in (README, SHOWCASE, MANIFEST):
        assert "reviewer's seat" in doc.read_text(encoding="utf-8"), f"{doc.name}: alt text / prose must state the ruling semantics"


# --------------------------------------------------------- screenshots ----

def _png_chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8: pos + 8 + length]
        yield ctype.decode("latin1"), body
        pos += 12 + length


@pytest.mark.parametrize("name", SCREENSHOTS)
def test_screenshots_are_1280x800_and_carry_no_metadata(name):
    p = MEDIA / "screenshots" / name
    assert p.exists(), p
    data = p.read_bytes()
    assert len(data) <= 400 * 1024, f"{name} is larger than 400 KB"
    chunks = list(_png_chunks(data))
    assert chunks[0][0] == "IHDR"
    w, h = struct.unpack(">II", chunks[0][1][:8])
    assert (w, h) == (1280, 800), f"{name}: {w}x{h}"
    types = {c for c, _ in chunks}
    assert not types & {"tEXt", "iTXt", "zTXt"}, f"{name}: text metadata chunks present: {types & {'tEXt', 'iTXt', 'zTXt'}}"
    assert MANIFEST.read_text(encoding="utf-8").count(name) >= 1


# ---------------------------------------------------------- CLI coverage ----

def test_every_cli_command_is_documented():
    cli = (REPO / "tagteam" / "cli.py").read_text(encoding="utf-8")
    main_src = cli[cli.index("def main("):]
    commands = sorted(set(re.findall(r'command == "([\w-]+)"', main_src)))
    assert commands, "could not find the dispatch table"
    docs = README.read_text(encoding="utf-8") + HTW.read_text(encoding="utf-8")
    undocumented = [c for c in commands if f"tagteam {c}" not in docs]
    assert not undocumented, f"CLI commands missing from README/how-tagteam-works: {undocumented}"


# ------------------------------------------------------ glossary guard ----

_BANNED_PHRASES = [r"\b10 rounds\b", r"\bround[- ]10\b", r"\breview cycle\b", r"\bhandoff session\b", r"\bhandoff cycle\b"]


def test_glossary_guard():
    for doc in (README, HTW, SHOWCASE):
        text = _text_without_fences(doc.read_text(encoding="utf-8"))
        for pat in _BANNED_PHRASES:
            m = re.search(pat, text, re.I)
            assert not m, f"{doc.name}: banned phrase {pat!r} near: {text[max(0, m.start()-40):m.end()+40]!r}"
    for name in DIAGRAM_CONTRACT.values():
        root = ET.fromstring((MEDIA / name).read_text(encoding="utf-8"))
        for el in root.iter():
            if el.tag.endswith("text"):
                t = "".join(el.itertext())
                for pat in _BANNED_PHRASES[:2]:
                    assert not re.search(pat, t, re.I), f"{name}: label {t!r}"
    assert "10 consecutive stale rounds" in README.read_text(encoding="utf-8")
    # the shipped cockpit must not describe its churn marker as a round-number rule
    for f in ("cockpit.html", "cockpit.js"):
        text = (REPO / "tagteam" / "data" / "web" / f).read_text(encoding="utf-8")
        for pat in (r"round-10 line", r"r10 auto-escalate", r"\bround[- ]10\b", r"\b10 rounds\b"):
            assert not re.search(pat, text, re.I), f"{f}: {pat!r}"
    assert "10 consecutive stale rounds" in (REPO / "tagteam" / "data" / "web" / "cockpit.html").read_text(encoding="utf-8")


def test_cockpit_churn_chart_draws_no_threshold_at_a_fixed_round():
    """The churn chart's x-axis is the absolute round number; a stale-round
    limit is a count of consecutive unchanged re-submissions and has no
    fixed x. The chart must not draw any line/label anchored at a literal
    round (X(10) or any X(<number>)), nor any 'stale'/'escalat' text."""
    js = (REPO / "tagteam" / "data" / "web" / "cockpit.js").read_text(encoding="utf-8")
    start = js.index("function drawChurn(")
    depth, i = 0, js.index("{", start)
    while True:                       # find the matching closing brace of drawChurn
        c = js[i]
        depth += (c == "{") - (c == "}")
        i += 1
        if depth == 0:
            break
    body = js[start:i]
    assert not re.search(r"X\(\s*\d+(\.\d+)?\s*\)", body), "drawChurn anchors something at a literal round"
    for m in re.finditer(r"text\([^;]*?'([^']*)'", body):
        assert not re.search(r"stale|escalat|limit", m.group(1), re.I), f"drawChurn draws a threshold label: {m.group(1)!r}"
    # no dashed rule is drawn at all: every line(...) call is a plain axis (a dash
    # pattern is a quoted digits string passed as the 6th argument)
    calls = re.findall(r"\bline\(([^;]*)\);", body)
    for args in calls:
        assert not re.search(r"'[\d ]+'\s*(,|$)", args), f"dashed rule drawn in drawChurn: line({args})"


def test_readme_opens_with_the_loop_and_names_the_roles_first():
    text = README.read_text(encoding="utf-8")
    first_h2 = text.index("\n## ")
    assert text[first_h2:].startswith("\n## The loop")
    quick = text.index("pip install tagteam")
    head = text[:quick]
    for role in ("Lead", "Reviewer", "Arbiter"):
        assert f"**{role}**" in head, f"{role} must be introduced before Quick Start"
    assert "**plan**" in head and "**impl**" in head
    assert head.count("```mermaid") == 1
