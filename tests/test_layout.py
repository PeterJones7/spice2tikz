"""Layout engine tests: Netlist IR → Schematic IR (roadmap §5).

Three layers:

* unit tests for the connectivity graph, the placer's invariants, the router's
  geometry helpers, and the metrics;
* an end-to-end pass over every ``tests/corpus/spice/*.sp`` deck — parse, lay
  out, validate, emit, compare to a golden, and do it twice to prove
  determinism;
* the **electrical** check the IR validator cannot make: a laid-out sheet must
  not contain a connection the netlist does not have.  ``validate.py`` sees
  geometry, not nets, so a wire drawn through somebody else's terminal passes
  every invariant while being flatly wrong.  That test is the one that matters
  most in this file.

Metrics for each circuit are recorded in ``tests/golden/metrics.json`` and act
as a ratchet: no circuit may get more crossings, longer wires, a bigger box, or
worse alignment than it has today (``docs/DESIGN.md`` §5).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from spice2tikz import netlist_ir, schematic_ir, spice_parser
from spice2tikz.emit.circuitikz import emit_snippet, emit_standalone
from spice2tikz.layout import build_graph, layout, measure, place, route
from spice2tikz.layout.graph import (
    column_order,
    input_net,
    parallel_groups,
    pick_input_source,
    pick_output_net,
    rank_nets,
    series_chains,
)
from spice2tikz.layout.metrics import (
    BETTER_WHEN_HIGHER,
    BETTER_WHEN_LOWER,
    Metrics,
    format_metrics,
)
from spice2tikz.layout.place import NODE_INSET
from spice2tikz.netlist_ir import NetlistIR
from spice2tikz.schematic_ir import (
    NodeComponent,
    PathComponent,
    SchematicIR,
    Wire,
)
from spice2tikz.symbols import Point
from spice2tikz.validate import Severity, format_finding, validate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import cross_validate

CORPUS = Path(__file__).parent / "corpus" / "spice"
GOLDEN = Path(__file__).parent / "golden"
METRICS_FILE = GOLDEN / "metrics.json"

DECKS = sorted(CORPUS.glob("*.sp"))
NAMES = [path.stem for path in DECKS]


def parse(name: str) -> NetlistIR:
    """Parse one corpus deck."""
    return spice_parser.load_spice(CORPUS / f"{name}.sp")


def lay_out(name: str) -> SchematicIR:
    """Parse and lay out one corpus deck."""
    return layout(parse(name))


# --- §5.1 the connectivity graph --------------------------------------------


def test_graph_indexes_terminals_by_net():
    graph = build_graph(parse("rc_lowpass"))
    assert set(graph.components) == {"V1", "R1", "C1"}
    assert graph.degree("in") == 2
    assert [terminal.component for terminal in graph.terminals["in"]] == ["V1", "R1"]


def test_graph_classes_nets():
    graph = build_graph(parse("rc_lowpass"))
    assert graph.ground_nets == ("0",)
    assert graph.is_ground("0")
    assert graph.is_rail("0")
    assert not graph.is_rail("in")


def test_supply_nets_are_rails_not_columns():
    graph = build_graph(parse("common_source_amp"))
    assert "vdd" in graph.supply_nets
    assert graph.is_rail("vdd")


def test_two_terminal_components_are_path_components():
    graph = build_graph(parse("common_source_amp"))
    assert graph.is_path("RD")
    assert not graph.is_path("M1")


def test_ranking_walks_outwards_from_the_input():
    graph = build_graph(parse("rlc_series"))
    source = pick_input_source(graph)
    ranks = rank_nets(graph, input_net(graph, source))
    assert ranks["in"] == 0
    assert ranks["mid"] == 1
    assert ranks["vc"] == 2


def test_ranking_does_not_travel_through_ground():
    """Every node is two hops from every other through ground; that is useless."""
    graph = build_graph(parse("rlc_series"))
    ranks = rank_nets(graph, "in")
    assert "0" not in ranks
    assert ranks["vc"] == 2  # not 2 via ground, but 2 through R1 and L1


def test_ranking_is_total_even_with_no_source():
    graph = build_graph(parse("rlc_series"))
    ranks = rank_nets(graph, None)
    assert set(ranks) == set(graph.signal_nets)


def test_columns_are_unique_per_net():
    for name in NAMES:
        graph = build_graph(parse(name))
        ranks = rank_nets(graph, input_net(graph, pick_input_source(graph)))
        columns = column_order(graph, ranks)
        assert len(set(columns.values())) == len(columns), name


def test_input_source_prefers_a_stimulus_over_a_supply():
    graph = build_graph(parse("common_source_amp"))
    # VDD is a plain DC source; V1 carries a SIN specification.
    assert pick_input_source(graph) == "V1"


def test_output_net_prefers_a_net_that_names_itself():
    graph = build_graph(parse("common_source_amp"))
    ranks = rank_nets(graph, input_net(graph, pick_input_source(graph)))
    assert pick_output_net(graph, ranks) == "out"


def test_series_chain_detection():
    # The whole loop is one chain: every interior net joins exactly two
    # two-terminal components, the source included.
    chains = series_chains(build_graph(parse("rlc_series")))
    assert len(chains) == 1
    assert set(chains[0]) == {"V1", "R1", "L1", "C1"}
    ordered = chains[0] if chains[0][0] == "V1" else list(reversed(chains[0]))
    assert ordered == ["V1", "R1", "L1", "C1"]


def test_parallel_group_detection():
    groups = parallel_groups(build_graph(parse("bjt_amp")))
    assert any(set(group) == {"RE1", "CE"} for group in groups)


def test_no_parallel_group_in_a_plain_series_circuit():
    assert parallel_groups(build_graph(parse("rlc_series"))) == []


# --- §5.2/§5.3 placement and routing invariants -----------------------------


def node_components(ir: SchematicIR) -> list[NodeComponent]:
    """Return every node component of the first sheet."""
    return [
        element
        for element in ir.sheets[0].elements
        if isinstance(element, NodeComponent)
    ]


def pin_owners(netlist: NetlistIR, ir: SchematicIR) -> dict[Point, set[str]]:
    """Map every terminal position to the nets the netlist puts there."""
    components = {component.id: component for component in netlist.circuit.components}
    owners: dict[Point, set[str]] = {}
    for element in ir.sheets[0].elements:
        if isinstance(element, PathComponent):
            nets = list(components[element.ref].pins.values())
            for point, net in zip((element.a, element.b), nets, strict=False):
                owners.setdefault(point, set()).add(net)
        elif isinstance(element, NodeComponent):
            for pin, point in element.pins.items():
                net = components[element.ref].pins.get(pin)
                if net is not None:
                    owners.setdefault(point, set()).add(net)
    return owners


def on_segment(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* lies anywhere on the closed segment."""
    if start[0] == end[0]:
        return point[0] == start[0] and (
            min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )
    if start[1] == end[1]:
        return point[1] == start[1] and (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
        )
    return False


@pytest.mark.parametrize("name", NAMES)
def test_no_wire_touches_another_nets_terminal(name: str):
    """The check ``validate.py`` cannot make: no invented connections.

    The validator sees geometry, not nets, so a wire drawn along another net's
    pin satisfies every IR invariant while shorting two nodes together.  This
    is the failure mode that makes a generated schematic worse than none.
    """
    netlist = parse(name)
    ir = layout(netlist)
    owners = pin_owners(netlist, ir)
    offenders = []
    for element in ir.sheets[0].elements:
        if not isinstance(element, Wire):
            continue
        for start, end in element.segments():
            for point, nets in owners.items():
                if element.net not in nets and on_segment(point, start, end):
                    offenders.append(
                        f"wire {element.net!r} runs through a {sorted(nets)} "
                        f"terminal at {point}"
                    )
    assert not offenders, "\n".join(sorted(set(offenders)))


@pytest.mark.parametrize("name", NAMES)
def test_no_two_nets_share_a_terminal_position(name: str):
    netlist = parse(name)
    owners = pin_owners(netlist, layout(netlist))
    shared = {point: nets for point, nets in owners.items() if len(nets) > 1}
    assert not shared, f"terminals of different nets coincide: {shared}"


@pytest.mark.parametrize("name", NAMES)
def test_node_terminals_never_sit_on_a_net_column(name: str):
    """The parity invariant the placer relies on (see ``place.NODE_INSET``)."""
    netlist = parse(name)
    graph = build_graph(netlist)
    ranks = rank_nets(graph, input_net(graph, pick_input_source(graph)))
    placement = place(graph, ranks)
    columns = set(placement.columns.values())
    for element in placement.components:
        if not isinstance(element, NodeComponent):
            continue
        for point in element.pins.values():
            assert point[0] not in columns, f"{element.ref} terminal on a column"


def test_node_inset_is_odd():
    """Everything above rests on this; a silent change to an even value breaks it."""
    assert NODE_INSET % 2 == 1


@pytest.mark.parametrize("name", NAMES)
def test_every_wire_is_orthogonal(name: str):
    for element in lay_out(name).sheets[0].elements:
        if isinstance(element, Wire):
            for start, end in element.segments():
                assert (start[0] == end[0]) != (start[1] == end[1])


@pytest.mark.parametrize("name", NAMES)
def test_the_sheet_starts_at_the_origin(name: str):
    ir = lay_out(name)
    points = [point for element in ir.sheets[0].elements for point in _points(element)]
    assert min(point[0] for point in points) == 0
    assert min(point[1] for point in points) == 0


def _points(element: object) -> list[Point]:
    if isinstance(element, PathComponent):
        return [element.a, element.b]
    if isinstance(element, NodeComponent):
        return [element.at, *element.pins.values()]
    if isinstance(element, Wire):
        return list(element.points)
    at = getattr(element, "at", None)
    return [at] if at is not None else []


def test_a_supply_side_device_is_drawn_source_up():
    """A PMOS with its source on the rail: source above drain, gate still left.

    Asserted as an outcome, not as a particular rot/mirror pair: circuitikz
    draws a PMOS source-up already, so the right answer here is to leave it
    alone. What matters is where the terminals end up.
    """
    ir = lay_out("cmos_inverter")
    mp = next(node for node in node_components(ir) if node.ref == "MP")
    assert mp.pins["s"][1] > mp.pins["d"][1]
    assert mp.pins["g"][0] < mp.at[0]


def test_a_ground_side_pmos_is_turned_over():
    """The converse: a PMOS whose *drain* wants the rail must be flipped."""
    netlist = spice_parser.parse_spice(
        "pmos to ground\n"
        "V1 in 0 AC 1\n"
        "VDD vdd 0 DC 5\n"
        "M1 vdd in out out PMOSMOD\n"
        "R1 out 0 1k\n"
        ".model PMOSMOD PMOS\n"
        ".end\n"
    )
    ir = layout(netlist)
    m1 = next(node for node in node_components(ir) if node.ref == "M1")
    assert m1.pins["d"][1] > m1.pins["s"][1]
    assert m1.pins["g"][0] < m1.at[0]


def test_a_ground_side_device_is_not_turned():
    ir = lay_out("cmos_inverter")
    mn = next(node for node in node_components(ir) if node.ref == "MN")
    assert (mn.rot, mn.mirror) == (0, False)
    assert mn.pins["d"][1] > mn.pins["s"][1]


def test_the_body_terminal_is_declared_but_not_wired():
    """Invariant 8 needs the pin; a wire out of the body centre helps nobody."""
    ir = lay_out("cmos_inverter")
    mn = next(node for node in node_components(ir) if node.ref == "MN")
    assert mn.pins["b"] == mn.at
    wires = [e for e in ir.sheets[0].elements if isinstance(e, Wire)]
    assert not any(mn.at in wire.points for wire in wires)


def test_an_untied_body_terminal_warns():
    netlist = spice_parser.parse_spice(
        "untied bulk\n"
        "V1 in 0 AC 1\n"
        "M1 out in mid 0 NMOSMOD\n"
        "R1 out 0 1k\n"
        "R2 mid 0 1k\n"
        ".model NMOSMOD NMOS\n"
        ".end\n"
    )
    warnings: list[str] = []
    layout(netlist, warnings=warnings)
    assert any("body terminal" in warning for warning in warnings)


def test_generated_boxes_are_written_into_the_document():
    ir = lay_out("subckt_rc_stages")
    assert "subckt:rcstage" in ir.symbols
    symbol = ir.symbols["subckt:rcstage"]
    assert set(symbol.pins) == {"in", "out"}
    assert symbol.base is None  # circuitikz has no shape for it


def test_a_value_that_did_not_parse_is_not_labelled():
    """``DC 0 AC 1 SIN(...)`` is wider than the symbol and belongs in a caption."""
    ir = lay_out("common_source_amp")
    v1 = next(
        element
        for element in ir.sheets[0].elements
        if isinstance(element, PathComponent) and element.ref == "V1"
    )
    assert v1.value_label is None
    rd = next(
        element
        for element in ir.sheets[0].elements
        if isinstance(element, PathComponent) and element.ref == "RD"
    )
    assert rd.value_label is not None
    assert rd.value_label.text == r"\SI{4.7}{\kilo\ohm}"


def test_ground_gets_exactly_one_symbol():
    ir = lay_out("rc_lowpass")
    grounds = [
        element
        for element in ir.sheets[0].elements
        if getattr(element, "variant", None) == "ground"
    ]
    assert len(grounds) == 1
    assert grounds[0].at[1] == 0


def test_a_supply_rail_is_labelled_with_its_voltage():
    ir = lay_out("common_source_amp")
    supplies = [
        element
        for element in ir.sheets[0].elements
        if getattr(element, "variant", None) == "vcc"
    ]
    assert len(supplies) == 1
    assert supplies[0].text is not None
    assert "vdd" in supplies[0].text


def test_routing_is_a_pure_function_of_the_placement():
    graph = build_graph(parse("bjt_amp"))
    ranks = rank_nets(graph, input_net(graph, pick_input_source(graph)))
    first = route(place(graph, ranks))
    second = route(place(graph, ranks))
    assert [element.to_json() for element in first] == [
        element.to_json() for element in second
    ]


# --- §5.5 end to end ---------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_layout_validates_without_errors(name: str):
    findings = validate(lay_out(name))
    errors = [f for f in findings if f.severity is Severity.ERROR]
    assert not errors, "\n".join(format_finding(finding) for finding in errors)


@pytest.mark.parametrize("name", NAMES)
def test_layout_validates_without_any_findings(name: str):
    """Warnings are tolerated by the contract, but the corpus should be clean."""
    findings = validate(lay_out(name))
    assert not findings, "\n".join(format_finding(finding) for finding in findings)


@pytest.mark.parametrize("name", NAMES)
def test_layout_matches_the_golden_ir(name: str, golden: Callable[[str, str], None]):
    golden(f"layout/{name}.schematic.json", schematic_ir.dumps(lay_out(name)))


@pytest.mark.parametrize("name", NAMES)
def test_layout_matches_the_golden_tex(name: str, golden: Callable[[str, str], None]):
    golden(f"layout/{name}.tex", emit_snippet(lay_out(name)))


@pytest.mark.parametrize("name", NAMES)
def test_layout_matches_the_golden_standalone(
    name: str, golden: Callable[[str, str], None]
):
    golden(f"layout/{name}.standalone.tex", emit_standalone(lay_out(name)))


@pytest.mark.parametrize("name", NAMES)
def test_layout_is_deterministic(name: str):
    assert schematic_ir.dumps(lay_out(name)) == schematic_ir.dumps(lay_out(name))
    assert emit_snippet(lay_out(name)) == emit_snippet(lay_out(name))


@pytest.mark.parametrize("name", NAMES)
def test_layout_survives_a_json_round_trip(name: str):
    ir = lay_out(name)
    reloaded = schematic_ir.loads(schematic_ir.dumps(ir))
    assert emit_snippet(reloaded) == emit_snippet(ir)


@pytest.mark.parametrize("name", NAMES)
def test_layout_from_a_dumped_netlist_is_identical(name: str):
    """The netlist JSON round trip must not change the drawing."""
    netlist = parse(name)
    reloaded = netlist_ir.loads(netlist_ir.dumps(netlist))
    assert schematic_ir.dumps(layout(reloaded)) == schematic_ir.dumps(layout(netlist))


def test_every_corpus_deck_is_covered():
    assert len(NAMES) >= 10, "roadmap §4.3 asks for at least ten decks"


# --- §5.4 metrics and the regression ratchet --------------------------------


def test_metrics_count_a_crossing():
    ir = schematic_ir.loads(
        '{"ir": "schematic", "version": "1.0", "meta": {"grid": {"pitch": 0.5}},'
        ' "sheets": [{"name": "main", "elements": ['
        '{"type": "wire", "net": "a", "points": [[0, 2], [4, 2]]},'
        '{"type": "wire", "net": "b", "points": [[2, 0], [2, 4]]}'
        "]}]}"
    )
    assert measure(ir).crossings == 1


def test_metrics_ignore_a_crossing_within_one_net():
    ir = schematic_ir.loads(
        '{"ir": "schematic", "version": "1.0", "meta": {"grid": {"pitch": 0.5}},'
        ' "sheets": [{"name": "main", "elements": ['
        '{"type": "wire", "net": "a", "points": [[0, 2], [4, 2]]},'
        '{"type": "wire", "net": "a", "points": [[2, 0], [2, 4]]}'
        "]}]}"
    )
    assert measure(ir).crossings == 0


def test_metrics_measure_wire_length_and_box():
    ir = lay_out("rc_lowpass")
    metrics = measure(ir)
    assert metrics.components == 3
    assert metrics.wire_length > 0
    assert metrics.bbox_area > 0
    assert 0.0 <= metrics.alignment <= 1.0


def test_format_metrics_is_a_single_line():
    line = format_metrics("x", measure(lay_out("rc_lowpass")))
    assert "\n" not in line
    assert line.startswith("x: ")


def recorded_metrics() -> dict[str, dict[str, float]]:
    """Return the metrics ratchet, or an empty table before it is written."""
    if not METRICS_FILE.exists():
        return {}
    return dict(json.loads(METRICS_FILE.read_text(encoding="utf-8")))


def current_metrics() -> dict[str, dict[str, float]]:
    """Measure every corpus circuit."""
    return {name: measure(lay_out(name)).to_json() for name in NAMES}


def test_metrics_file_is_current(update_golden: bool):
    """The recorded table must list exactly today's circuits."""
    measured = current_metrics()
    if update_golden:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        METRICS_FILE.write_text(
            json.dumps(measured, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return
    assert set(recorded_metrics()) == set(measured), (
        "tests/golden/metrics.json does not list the same circuits; "
        "run: pytest --update-golden"
    )


@pytest.mark.parametrize("name", NAMES)
def test_layout_quality_does_not_regress(name: str, update_golden: bool):
    """A ratchet, not a score: today's layout may not be worse than the last.

    Improving a metric is expected to require ``--update-golden``; that is the
    point, since the new number becomes the floor.
    """
    if update_golden:
        pytest.skip("regenerating the ratchet")
    recorded = recorded_metrics().get(name)
    if recorded is None:
        pytest.skip(f"{name} has no recorded metrics yet")
    measured = measure(lay_out(name)).to_json()
    for key in BETTER_WHEN_LOWER:
        assert measured[key] <= recorded[key], (
            f"{name}: {key} rose from {recorded[key]} to {measured[key]}"
        )
    for key in BETTER_WHEN_HIGHER:
        assert measured[key] >= recorded[key], (
            f"{name}: {key} fell from {recorded[key]} to {measured[key]}"
        )


def test_metrics_json_round_trips():
    metrics = Metrics(
        components=1, wires=2, crossings=3, wire_length=4, bbox_area=5, alignment=0.5
    )
    assert metrics.to_json() == {
        "components": 1,
        "wires": 2,
        "crossings": 3,
        "wire_length": 4,
        "bbox_area": 5,
        "alignment": 0.5,
    }


# --- §5.6 cross-validation against the human .asc layouts -------------------


def test_some_circuits_exist_in_both_corpora():
    """Without an overlap there is nothing to compare auto layout against."""
    assert cross_validate.shared_circuits()


def test_cross_validation_reports_a_comparison(capsys: pytest.CaptureFixture[str]):
    """Report, do not assert (roadmap §5.6).

    The engine is not expected to match a hand layout, and on circuits this
    small the numbers are noisy. What matters is that the comparison keeps
    working, so the gap can be watched over releases and so a future layout v2
    has ground truth to be evaluated against. Run
    ``python tools/cross_validate.py`` to read it.
    """
    assert cross_validate.main([]) == 0
    printed = capsys.readouterr().out
    for name in cross_validate.shared_circuits():
        assert name in printed
    assert "wire_length" in printed


@pytest.mark.parametrize("name", cross_validate.shared_circuits())
def test_cross_validation_measures_both_sides(name: str):
    pair = cross_validate.compare(name)
    assert set(pair) == {"human", "auto"}
    for side in pair.values():
        assert side["components"] > 0
