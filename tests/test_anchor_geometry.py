r"""Ask circuitikz where it really puts each anchor, and check we agree.

Two bugs reached rendered output because nothing verified this:

* the p-type built-ins reused their n-type pin offsets, but circuitikz draws a
  PMOS source-up and a PNP emitter-up, so every p-type terminal was declared on
  the wrong side of the body;
* the emitter listed ``xscale=-1`` before ``rotate``, believing TikZ applies
  node transformations left to right.  It post-multiplies, so that order
  mirrors *after* rotating — which disagrees with the IR for 90° and 270°.

Both are invisible to every other test. The geometry is self-consistent, the
document compiles, the goldens match; the only symptom is a lead drawn back
across the body of the device, which needs an eye or this.

So this test compiles a document holding every built-in symbol in all eight
orientations, has TeX report the coordinate of each anchor through
``\\typeout``, and checks the *direction* from the node's centre against
:func:`~spice2tikz.symbols.resolve_pins`. Directions only: circuitikz places
anchors at its own absolute sizes, which have no relation to the grid, but
which side of the body a terminal is on is exactly what went wrong.

Skipped when no LaTeX toolchain is installed; always runs in CI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from spice2tikz.emit.circuitikz import emit_snippet, tikz_node_name
from spice2tikz.netlist_ir import Kind
from spice2tikz.schematic_ir import NodeComponent, SchematicIR, Sheet
from spice2tikz.symbols import (
    BASE_PIN_ANCHORS,
    BUILTIN_SYMBOLS,
    Point,
    resolve_pins,
)
from test_compile import find_latexmk

requires_latexmk = pytest.mark.skipif(
    find_latexmk() is None,
    reason="latexmk not installed; anchor geometry is checked in CI",
)

ORIENTATIONS = [(rot, mirror) for mirror in (False, True) for rot in (0, 90, 180, 270)]

REPORT = re.compile(r"S2TANCHOR (\S+) (\S+) ([-0-9.]+)pt ([-0-9.]+)pt")

PREAMBLE = r"""\documentclass{standalone}
\usepackage{circuitikz}
\makeatletter
% \pgf@process forces the point to be evaluated into \pgf@x/\pgf@y;
% \pgfgetlastxy alone reports whatever was computed previously.
\newcommand{\reportanchor}[3]{%
  \pgf@process{\pgfpointanchor{#1}{#2}}%
  \typeout{S2TANCHOR #3 #2 \the\pgf@x\space\the\pgf@y}%
}
\makeatother
\begin{document}
"""


def _case_id(symbol: str, rot: int, mirror: bool) -> str:
    return f"{symbol}-{rot}-{'m' if mirror else 'n'}"


def probe_cases() -> list[tuple[str, str, int, bool]]:
    """Return ``(case id, symbol, rot, mirror)`` for every orientation tested."""
    return [
        (_case_id(name, rot, mirror), name, rot, mirror)
        for name in sorted(BUILTIN_SYMBOLS)
        for rot, mirror in ORIENTATIONS
    ]


def build_probe() -> str:
    """Return a document that reports every anchor of every orientation.

    The nodes come from :func:`~spice2tikz.emit.circuitikz.emit_snippet`, not
    from options assembled here: the point is to check what the **emitter**
    produces. Rebuilding the option list locally would test only that this
    file agrees with itself, which is how the transform-order bug survived its
    first test.
    """
    elements: list[NodeComponent] = []
    for index, (_case, name, rot, mirror) in enumerate(probe_cases()):
        symbol = BUILTIN_SYMBOLS[name]
        at = (index * 12, 0)
        elements.append(
            NodeComponent(
                ref=f"X{index}",
                kind=Kind.GENERIC,
                symbol=name,
                at=at,
                rot=rot,  # type: ignore[arg-type]
                mirror=mirror,
                pins=resolve_pins(symbol, at, rot, mirror),  # type: ignore[arg-type]
            )
        )
    snippet = emit_snippet(
        SchematicIR(sheets=[Sheet(name="main", elements=list(elements))])
    )

    reports: list[str] = []
    for index, (case, name, _rot, _mirror) in enumerate(probe_cases()):
        node = tikz_node_name(index)
        reports.append(f"\\reportanchor{{{node}}}{{center}}{{{case}}}")
        anchors = BASE_PIN_ANCHORS.get(BUILTIN_SYMBOLS[name].base or "", {})
        for anchor in anchors.values():
            reports.append(f"\\reportanchor{{{node}}}{{{anchor}}}{{{case}}}")

    body = snippet.replace(
        "\\end{circuitikz}", "\n".join(reports) + "\n\\end{circuitikz}"
    )
    return PREAMBLE + body + "\\end{document}\n"


def run_probe(work: Path) -> dict[tuple[str, str], Point]:
    """Compile the probe and return ``{(case, anchor): (x, y)}`` in points."""
    source = work / "probe.tex"
    source.write_text(build_probe(), encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["latexmk", "-pdf", "-interaction=nonstopmode", str(source.name)],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    log = work / "probe.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    reported: dict[tuple[str, str], Point] = {}
    for case, anchor, x, y in REPORT.findall(result.stdout + "\n" + text):
        reported[(case, anchor)] = (float(x), float(y))
    if not reported:
        pytest.fail(f"the probe reported no anchors:\n{result.stdout[-2000:]}")
    return reported


@pytest.fixture(scope="module")
def anchors(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], Point]:
    """Compile the probe once for the whole module."""
    return run_probe(tmp_path_factory.mktemp("anchors"))


def dominant(point: Point) -> tuple[int, int]:
    """Return which side of the body *point* lies on: one of the four compass steps.

    The comparison is on the dominant axis rather than the exact sign pair
    because some anchors are placed diagonally — circuitikz offsets a JFET's
    gate upwards as well as sideways — while what matters here is only which
    side of the device a terminal is on. A lead between two points on the same
    side never crosses the body; one between opposite sides always does, which
    is the failure this exists to catch.
    """
    x, y = point
    if abs(x) >= abs(y):
        return (1 if x > 0 else -1, 0) if x else (0, 0)
    return (0, 1 if y > 0 else -1)


@requires_latexmk
@pytest.mark.parametrize(
    ("name", "rot", "mirror"),
    [(name, rot, mirror) for _case, name, rot, mirror in probe_cases()],
)
def test_anchor_directions_match_the_symbol(
    name: str,
    rot: int,
    mirror: bool,
    anchors: dict[tuple[str, str], Point],
) -> None:
    symbol = BUILTIN_SYMBOLS[name]
    mapping = BASE_PIN_ANCHORS.get(symbol.base or "", {})
    case = _case_id(name, rot, mirror)
    centre = anchors.get((case, "center"))
    assert centre is not None, f"no centre reported for {case}"

    expected = resolve_pins(symbol, (0, 0), rot, mirror)  # type: ignore[arg-type]
    for pin, anchor in mapping.items():
        placed = anchors.get((case, anchor))
        if placed is None:
            continue
        drawn = (placed[0] - centre[0], placed[1] - centre[1])
        declared = expected[pin]
        if declared == (0, 0):
            continue  # a bulk terminal sits on the origin; no direction to check
        assert dominant(drawn) == dominant(declared), (
            f"{name} at rot={rot} mirror={mirror}: circuitikz draws anchor "
            f"{anchor!r} on the {dominant(drawn)} side, but the symbol declares "
            f"pin {pin!r} on the {dominant(declared)} side. A lead between them "
            f"crosses the body of the device."
        )
