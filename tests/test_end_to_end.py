"""SPICE in, CircuiTikZ out, and a check that the drawing says the same thing.

This is the file to look in for "does converting a netlist actually work".
Everything here starts from SPICE source text or a `.sp`/`.asc` file and ends
at LaTeX, with no fixtures in between.

The important test is :func:`test_the_drawing_matches_the_netlist`. Every other
check in this repository looks at the output as *geometry*: are the coordinates
integers, do the wires meet, is the golden byte-identical. None of them asks
the only question a reader of the figure cares about — **is this the same
circuit?** A schematic can satisfy all thirteen IR invariants, compile
beautifully, and still join two nodes that the netlist keeps apart, or leave a
component floating. So that test throws the drawing away and reads it back the
way a person does — follow the ink, a dot is a connection, an undotted crossing
is not, all ground symbols are one node — and compares the resulting partition
of terminals against the netlist it came from.

:func:`test_the_readback_catches_a_broken_drawing` exists because a test that
cannot fail is worse than no test: it deliberately cuts a wire and checks the
readback notices.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from spice2tikz import asc_importer, spice_parser
from spice2tikz.emit.circuitikz import emit_snippet, emit_standalone
from spice2tikz.layout import layout
from spice2tikz.layout.route import _body_box, _crosses_box, _touches
from spice2tikz.netlist_ir import Kind, NetlistIR
from spice2tikz.schematic_ir import (
    Junction,
    NetSymbol,
    NodeComponent,
    PathComponent,
    SchematicIR,
    Wire,
)
from spice2tikz.symbols import Point

REPO_ROOT = Path(__file__).resolve().parent.parent
SPICE_CORPUS = REPO_ROOT / "tests" / "corpus" / "spice"
ASC_CORPUS = REPO_ROOT / "tests" / "corpus" / "asc"

DECKS = sorted(SPICE_CORPUS.glob("*.sp"))
NAMES = [path.stem for path in DECKS]

RC_LOWPASS = """\
* RC low-pass
V1 in 0 AC 1
R1 in out 10k
C1 out 0 100n
.end
"""


def convert(text: str) -> str:
    """Run the whole pipeline over SPICE *text* and return the LaTeX."""
    return emit_snippet(layout(spice_parser.parse_spice(text)))


# --- the headline: SPICE text in, LaTeX out ---------------------------------


def test_a_netlist_becomes_circuitikz():
    latex = convert(RC_LOWPASS)
    assert latex.startswith(r"\begin{circuitikz}")
    assert latex.rstrip().endswith(r"\end{circuitikz}")
    # One drawn element per component, with the values the deck gave.
    assert latex.count(r"\draw") >= 3
    assert "R=$R_1$" in latex
    assert "C=$C_1$" in latex
    # `AC 1` is a stimulus, so it gets the sine symbol rather than a battery.
    assert "to[sV" in latex
    assert r"\SI{10}{\kilo\ohm}" in latex
    assert r"\SI{100}{\nano\farad}" in latex
    assert "node[ground]" in latex


def test_every_component_of_the_deck_is_drawn():
    netlist = spice_parser.parse_spice(RC_LOWPASS)
    ir = layout(netlist)
    drawn = {
        element.ref
        for element in ir.sheets[0].elements
        if isinstance(element, PathComponent | NodeComponent)
    }
    assert drawn == {component.id for component in netlist.circuit.components}


def test_conversion_is_deterministic():
    assert convert(RC_LOWPASS) == convert(RC_LOWPASS)


def test_an_unknown_card_still_converts_and_warns():
    """DESIGN §6: a partial schematic beats none."""
    warnings: list[str] = []
    netlist = spice_parser.parse_spice(
        "odd deck\nV1 in 0 AC 1\nZ1 in out 1\nR1 out 0 1k\n.end\n",
        warnings=warnings,
    )
    assert any("unknown card" in warning for warning in warnings)
    assert any(component.id == "Z1" for component in netlist.circuit.components)
    latex = emit_snippet(layout(netlist))
    assert "$Z_1$" in latex  # drawn as a labelled placeholder box


@pytest.mark.parametrize("name", NAMES)
def test_every_corpus_deck_converts(name: str):
    latex = emit_standalone(
        layout(spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp"))
    )
    assert r"\begin{document}" in latex
    assert r"\begin{circuitikz}" in latex


def test_the_command_line_converts_a_deck(tmp_path: Path):
    """The whole thing, in a real process, the way a user runs it."""
    target = tmp_path / "out.tex"
    deck = tmp_path / "rc.sp"
    deck.write_text(RC_LOWPASS, encoding="utf-8", newline="\n")
    with target.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "spice2tikz.cli", str(deck)],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert result.returncode == 0, result.stderr.decode()
    assert target.read_text(encoding="utf-8") == convert(RC_LOWPASS)


def test_an_ltspice_schematic_converts():
    ir = asc_importer.load_asc(ASC_CORPUS / "rc_lowpass.asc")
    latex = emit_snippet(ir)
    assert latex.startswith(r"\begin{circuitikz}")
    assert "R=$R_1$" in latex


# --- reading the drawing back --------------------------------------------


class _Nodes:
    """Union-find over the points of a drawing."""

    def __init__(self) -> None:
        self._parent: dict[object, object] = {}

    def find(self, item: object) -> object:
        """Return the representative of *item*'s group."""
        self._parent.setdefault(item, item)
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, first: object, second: object) -> None:
        """Put *first* and *second* in the same group."""
        left, right = self.find(first), self.find(second)
        if left != right:
            self._parent[left] = right


def _on_segment(point: Point, start: Point, end: Point) -> bool:
    if start[0] == end[0]:
        return point[0] == start[0] and (
            min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )
    if start[1] == end[1]:
        return point[1] == start[1] and (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
        )
    return False


def _strictly_inside(point: Point, start: Point, end: Point) -> bool:
    return point not in (start, end) and _on_segment(point, start, end)


def terminals(
    ir: SchematicIR, netlist: NetlistIR | None = None
) -> dict[tuple[str, str], Point]:
    """Return ``{(ref, pin): position}`` for every terminal on the sheet.

    A ``PathComponent`` names its ends ``a`` and ``b`` whatever the part is,
    but the netlist names them per kind — a source has ``p`` and ``n``, a diode
    ``a`` and ``k``. Given the netlist, the real pin names are used, so the
    comparison actually covers sources and diodes instead of quietly skipping
    every terminal whose name does not happen to match.
    """
    components = (
        {component.id: component for component in netlist.circuit.components}
        if netlist is not None
        else {}
    )
    found: dict[tuple[str, str], Point] = {}
    for element in ir.sheets[0].elements:
        if isinstance(element, PathComponent):
            component = components.get(element.ref)
            names = list(component.pins) if component is not None else ["a", "b"]
            if len(names) != 2:
                names = ["a", "b"]
            found[(element.ref, names[0])] = element.a
            found[(element.ref, names[1])] = element.b
        elif isinstance(element, NodeComponent):
            for pin, point in element.pins.items():
                found[(element.ref, pin)] = point
    return found


def read_back(
    ir: SchematicIR, netlist: NetlistIR | None = None
) -> dict[tuple[str, str], object]:
    """Recover ``{(ref, pin): node}`` from the drawing alone.

    Deliberately ignorant of every ``net`` field in the IR: this reads the
    picture, not the labels that produced it, which is the whole point.
    """
    sheet = ir.sheets[0]
    wires = [e for e in sheet.elements if isinstance(e, Wire)]
    junctions = {e.at for e in sheet.elements if isinstance(e, Junction)}
    symbols = [e for e in sheet.elements if isinstance(e, NetSymbol)]
    markers = [s.at for s in symbols if s.variant != "tap"]
    placed = terminals(ir, netlist)
    pins = list(placed.values())

    nodes = _Nodes()
    for index, wire in enumerate(wires):
        for start, end in wire.segments():
            nodes.union(("wire", index), ("at", start))
            nodes.union(("wire", index), ("at", end))

    candidates = {*pins, *markers, *junctions}
    for wire in wires:
        candidates.update(wire.points)
    for point in candidates:
        if _conductors(point, wires, pins, markers) >= 3 and point not in junctions:
            # Three or more conductors and no dot: a reader sees wires crossing,
            # not wires joining.
            continue
        for index, wire in enumerate(wires):
            if any(_on_segment(point, s, e) for s, e in wire.segments()):
                nodes.union(("at", point), ("wire", index))

    # Every ground glyph is the same node, wherever it is drawn; supply glyphs
    # sharing a label likewise.
    grounds = [s.at for s in symbols if s.variant in ("ground", "sground")]
    for point in grounds[1:]:
        nodes.union(("at", grounds[0]), ("at", point))
    rails: dict[str, list[Point]] = {}
    for symbol in symbols:
        if symbol.variant in ("vcc", "vee"):
            rails.setdefault(symbol.text or symbol.net, []).append(symbol.at)
    for group in rails.values():
        for point in group[1:]:
            nodes.union(("at", group[0]), ("at", point))

    return {key: nodes.find(("at", point)) for key, point in placed.items()}


def _conductors(
    point: Point, wires: list[Wire], pins: list[Point], markers: list[Point]
) -> int:
    """Count conductors meeting at *point*, as ``validate.py`` does."""
    count = sum(1 for pin in pins if pin == point)
    count += sum(1 for marker in markers if marker == point)
    for wire in wires:
        for start, end in wire.segments():
            if point in (start, end):
                count += 1
            elif _strictly_inside(point, start, end):
                count += 2
    return count


def body_terminals(netlist: NetlistIR) -> set[tuple[str, str]]:
    """Return the terminals the layout engine deliberately leaves unwired.

    A MOS bulk and a bipolar substrate sit on the middle of the device, so a
    wire from one would cross both channel terminals; they are drawn as the
    implicit tie every schematic uses (``docs/LAYOUT.md`` §3).
    """
    found = set()
    for component in netlist.circuit.components:
        if component.kind in (Kind.NMOS, Kind.PMOS) and "b" in component.pins:
            found.add((component.id, "b"))
        if component.kind in (Kind.BJT_NPN, Kind.BJT_PNP) and "s" in component.pins:
            found.add((component.id, "s"))
    return found


def compare(netlist: NetlistIR, ir: SchematicIR) -> list[str]:
    """Return every way the drawing disagrees with the netlist."""
    recovered = read_back(ir, netlist)
    skip = body_terminals(netlist)
    expected = {
        (component.id, pin): net
        for component in netlist.circuit.components
        for pin, net in component.pins.items()
        if (component.id, pin) not in skip
    }

    missing = sorted(set(expected) - set(recovered))
    problems: list[str] = [
        f"{ref}.{pin} is in the netlist but not on the sheet" for ref, pin in missing
    ]
    grouped: dict[str, list[tuple[str, str]]] = {}
    for key, net in expected.items():
        grouped.setdefault(net, []).append(key)
    for net, keys in sorted(grouped.items()):
        drawn = {recovered[key] for key in keys if key in recovered}
        if len(drawn) > 1:
            names = sorted(f"{ref}.{pin}" for ref, pin in keys)
            problems.append(
                f"net {net!r} is drawn as {len(drawn)} unconnected pieces: {names}"
            )
    seen: dict[object, str] = {}
    for key, net in sorted(expected.items()):
        node = recovered.get(key)
        if node is None:
            continue
        if node in seen and seen[node] != net:
            problems.append(
                f"nets {seen[node]!r} and {net!r} are drawn as one node, "
                f"shorted at {key[0]}.{key[1]}"
            )
        seen.setdefault(node, net)
    return problems


@pytest.mark.parametrize("name", NAMES)
def test_the_drawing_matches_the_netlist(name: str):
    """Read the figure back and check it is the circuit that went in.

    The check every other test in this repository cannot make.
    """
    netlist = spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp")
    problems = compare(netlist, layout(netlist))
    assert not problems, "\n".join(problems)


def test_the_readback_catches_a_broken_drawing():
    """A test that cannot fail is worse than no test."""
    netlist = spice_parser.parse_spice(RC_LOWPASS)
    ir = layout(netlist)
    assert not compare(netlist, ir)

    # Cut the ground rail in half; the two halves must stop being one node.
    wires = [e for e in ir.sheets[0].elements if isinstance(e, Wire)]
    longest = max(wires, key=lambda wire: len(wire.points))
    longest.points = [*longest.points[:1], longest.points[0]]
    assert compare(netlist, ir), "a severed wire went unnoticed"


def test_the_readback_catches_an_invented_connection():
    netlist = spice_parser.parse_spice(RC_LOWPASS)
    ir = layout(netlist)
    assert not compare(netlist, ir)

    # Short the input to ground with a wire nobody asked for.
    points = terminals(ir, netlist)
    ir.sheets[0].elements.append(
        Wire(net="0", points=[points[("V1", "p")], points[("V1", "n")]])
    )
    problems = compare(netlist, ir)
    assert any("shorted" in problem for problem in problems), problems


@pytest.mark.parametrize("name", NAMES)
def test_only_body_terminals_are_left_unwired(name: str):
    """Anything else floating would be a connection quietly dropped."""
    netlist = spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp")
    ir = layout(netlist)
    recovered = read_back(ir, netlist)
    alone = {
        key
        for key, node in recovered.items()
        if sum(1 for other in recovered.values() if other == node) == 1
    }
    # A net with a single terminal in the netlist has nothing to be joined to;
    # the drawing is right to leave it floating.
    lonely_nets = {
        net
        for net in {n for c in netlist.circuit.components for n in c.pins.values()}
        if sum(
            1 for c in netlist.circuit.components for n in c.pins.values() if n == net
        )
        == 1
    }
    expected_alone = body_terminals(netlist) | {
        (component.id, pin)
        for component in netlist.circuit.components
        for pin, net in component.pins.items()
        if net in lonely_nets
    }
    unexpected = sorted(alone - expected_alone)
    assert not unexpected, f"terminals connected to nothing: {unexpected}"


# --- the drawing must not be drawn over itself ------------------------------


def _overlaps(first: tuple[Point, Point], second: tuple[Point, Point]) -> bool:
    (a1, a2), (b1, b2) = first, second
    if a1[0] == a2[0] == b1[0] == b2[0]:
        return min(max(a1[1], a2[1]), max(b1[1], b2[1])) > max(
            min(a1[1], a2[1]), min(b1[1], b2[1])
        )
    if a1[1] == a2[1] == b1[1] == b2[1]:
        return min(max(a1[0], a2[0]), max(b1[0], b2[0])) > max(
            min(a1[0], a2[0]), min(b1[0], b2[0])
        )
    return False


@pytest.mark.parametrize("name", NAMES)
def test_no_wire_is_drawn_over_a_component(name: str):
    """Circuitikz draws a two-terminal part *along* its segment.

    A wire sharing that segment is drawn through the body of the part, which
    reads as a connection to the middle of a resistor — meaningless, and how
    this was caught in the first place.
    """
    ir = layout(spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp"))
    elements = ir.sheets[0].elements
    parts = [e for e in elements if isinstance(e, PathComponent)]
    problems = []
    for wire in (e for e in elements if isinstance(e, Wire)):
        for segment in wire.segments():
            for part in parts:
                if _overlaps(segment, (part.a, part.b)):
                    problems.append(f"wire {wire.net!r} runs along {part.ref}")
                elif any(_strictly_inside(end, part.a, part.b) for end in segment):
                    problems.append(f"wire {wire.net!r} ends inside {part.ref}")
    assert not problems, "\n".join(sorted(set(problems)))


@pytest.mark.parametrize("name", NAMES)
def test_no_wire_crosses_the_body_of_a_component(name: str):
    """A wire may cross a lead. It may not cross the drawn symbol.

    The IR gives a two-terminal part two endpoints, and for a long time every
    obstacle check treated it as the line between them — which is where the
    *wire* is, not where the *drawing* is. circuitikz puts the rectangle, the
    circle or the plates around the middle of that line, so a wire crossing at
    right angles went straight through the symbol while breaking no rule about
    connectivity.

    The body region is the router's own, so this asserts the router obeys the
    obstacles it is given: the bug was never a wrong box, it was the net spine
    being emitted without consulting one.
    """
    ir = layout(spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp"))
    elements = ir.sheets[0].elements
    parts = [e for e in elements if isinstance(e, PathComponent)]
    problems = []
    for wire in (e for e in elements if isinstance(e, Wire)):
        for segment in wire.segments():
            for part in parts:
                if _crosses_box(segment[0], segment[1], _body_box(part.a, part.b)):
                    problems.append(f"wire {wire.net!r} crosses the body of {part.ref}")
    assert not problems, "\n".join(sorted(set(problems)))


@pytest.mark.parametrize("name", NAMES)
def test_no_two_nets_touch(name: str):
    """Wires of different nets may cross. Meeting is a connection.

    A wire ending on another net's wire is a T-junction, and one running along
    another is worse; either is a short the netlist does not have. Neither
    shows up in a terminal-by-terminal check, because the offending point
    belongs to no component at all — which is how a supply rail came to end
    exactly on another net's column, dot and all.
    """
    ir = layout(spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp"))
    wires = [e for e in ir.sheets[0].elements if isinstance(e, Wire)]
    problems = []
    for index, wire in enumerate(wires):
        for other in wires[index + 1 :]:
            if wire.net == other.net:
                continue
            for segment in wire.segments():
                for against in other.segments():
                    if _touches(against, segment):
                        problems.append(
                            f"nets {wire.net!r} and {other.net!r} meet at "
                            f"{segment} / {against}"
                        )
    assert not problems, "\n".join(sorted(set(problems))[:6])
