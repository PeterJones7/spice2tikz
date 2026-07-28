"""Validator tests: one deliberately-broken corpus file per invariant (§1.4).

Each corpus file under ``tests/corpus/broken/`` violates exactly one
invariant, and the expected findings below are the *complete* result for that
file — so a check that fires too eagerly fails these tests too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spice2tikz.netlist_ir import (
    Component,
    Kind,
    Net,
    NetlistIR,
    Scope,
    SubcktDef,
)
from spice2tikz.netlist_ir import load as load_netlist
from spice2tikz.schematic_ir import (
    Junction,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    SchematicIR,
    Sheet,
    Wire,
)
from spice2tikz.schematic_ir import load as load_schematic
from spice2tikz.symbols import BUILTIN_SYMBOLS, PinDef, SymbolDef, resolve_pins
from spice2tikz.validate import (
    Finding,
    Severity,
    count_by_severity,
    format_finding,
    has_errors,
    validate,
    validate_netlist,
    validate_schematic,
)

CORPUS = Path(__file__).parent / "corpus"
BROKEN = CORPUS / "broken"

ERROR = Severity.ERROR
WARNING = Severity.WARNING

# file name -> the complete list of expected findings
EXPECTED: dict[str, list[Finding]] = {
    # Invariant 1: component ids unique per scope; pin names match the kind.
    "n1a_duplicate_id.netlist.json": [
        Finding(
            ERROR,
            "duplicate component id 'R1' in this scope",
            "circuit.components[3] (R1)",
        )
    ],
    "n1b_pin_name.netlist.json": [
        Finding(
            ERROR,
            "pin 'x' is not a pin of kind resistor (expected a, b)",
            "circuit.components[1] (R1)",
        ),
        Finding(
            ERROR,
            "kind resistor requires pin 'b'",
            "circuit.components[1] (R1)",
        ),
    ],
    # Invariant 2: every pin references a net of its scope.
    "n2_missing_net.netlist.json": [
        Finding(
            ERROR,
            "pin 'b' references undeclared net 'mid'",
            "circuit.components[1] (R1)",
        )
    ],
    # Invariant 3: control references an existing vsource.
    "n3_bad_control.netlist.json": [
        Finding(
            ERROR,
            "control references unknown component 'V9'",
            "circuit.components[3] (H1)",
        )
    ],
    # Invariant 4: subckt references an existing definition.
    "n4_bad_subckt.netlist.json": [
        Finding(
            ERROR,
            "unknown subcircuit definition 'amp'",
            "circuit.components[3] (X1)",
        )
    ],
    # Invariant 5: exactly one ground-class net.
    "n5_no_ground.netlist.json": [
        Finding(
            WARNING,
            "no ground-class net: the schematic will have no reference node",
            "circuit.nets",
        )
    ],
    # Invariant 6: integer coordinates, orthogonal wires and path components.
    "s6a_diagonal_component.schematic.json": [
        Finding(
            ERROR,
            "path component is not axis-aligned: (0, 4) to (6, 5)",
            "sheets[0].elements[1] (R1)",
        )
    ],
    "s6b_diagonal_wire.schematic.json": [
        Finding(
            ERROR,
            "wire segment 0 is not axis-aligned: (0, 0) to (6, 4)",
            "sheets[0].elements[2]",
        )
    ],
    "s6c_fractional_coordinate.schematic.json": [
        Finding(
            ERROR,
            "pin b has non-integer x coordinate 6.5",
            "sheets[0].elements[1] (R1)",
        )
    ],
    # Invariant 7: path components need room to be drawn.
    "s7_short_component.schematic.json": [
        Finding(
            WARNING,
            "path component spans 1 grid unit; circuitikz needs at least "
            "2 grid units to draw it legibly",
            "sheets[0].elements[1] (R1)",
        )
    ],
    # Invariant 8: node pins agree with the symbol geometry.
    "s8_pin_mismatch.schematic.json": [
        Finding(
            ERROR,
            "pin 'g' is at (2, 5) but symbol 'nmos' at (4, 4) rot 0 puts it at (2, 4)",
            "sheets[0].elements[0] (M1)",
        )
    ],
    # Invariant 9: no dangling wire ends.
    "s9_dangling_wire.schematic.json": [
        Finding(
            ERROR,
            "dangling wire end at (6, 0): no component pin, wire, net symbol, "
            "or port there",
            "sheets[0].elements[1]",
        )
    ],
    # Invariant 10: junctions exactly where conductors meet.
    "s10a_junction_too_few.schematic.json": [
        Finding(
            WARNING,
            "junction at (0, 0) joins 2 conductors; a dot is only meaningful from 3",
            "sheets[0].elements[3]",
        )
    ],
    "s10b_missing_junction.schematic.json": [
        Finding(
            WARNING,
            "3 conductors meet at (3, 0) without a junction",
            "sheets[0]",
        )
    ],
    # Invariant 11: symbol names resolve.
    "s11_unknown_symbol.schematic.json": [
        Finding(
            ERROR,
            "unknown symbol 'mystery_fet': not a built-in and not declared in "
            "the file's symbols block",
            "sheets[0].elements[0] (M1)",
        )
    ],
    # Invariant 12: components do not overlap.
    "s12_overlapping_components.schematic.json": [
        Finding(
            WARNING,
            "bounding box overlaps component 'R2'",
            "sheets[0].elements[0] (R1)",
        )
    ],
    # Invariant 13: one refdes per sheet.
    "s13_duplicate_ref.schematic.json": [
        Finding(
            ERROR,
            "duplicate ref 'R1' on this sheet",
            "sheets[0].elements[1] (R1)",
        )
    ],
}


def load_any(path: Path) -> NetlistIR | SchematicIR:
    """Load a corpus file, choosing the IR by file name."""
    if ".netlist." in path.name:
        return load_netlist(path)
    return load_schematic(path)


def test_every_broken_corpus_file_is_covered():
    on_disk = {path.name for path in BROKEN.iterdir() if path.suffix == ".json"}
    assert on_disk == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_broken_file_reports_exactly_its_invariant(name: str):
    findings = validate(load_any(BROKEN / name))
    assert findings == EXPECTED[name]


@pytest.mark.parametrize(
    "name", ["rc_lowpass.netlist.json", "rc_lowpass.schematic.json"]
)
def test_the_worked_examples_are_clean(name: str):
    assert validate(load_any(CORPUS / name)) == []


def test_validation_is_deterministic():
    for name in sorted(EXPECTED):
        ir = load_any(BROKEN / name)
        assert validate(ir) == validate(ir)


# --- finding plumbing -------------------------------------------------------


def test_has_errors_and_counts():
    findings = validate(load_any(BROKEN / "n1b_pin_name.netlist.json"))
    assert has_errors(findings)
    assert count_by_severity(findings) == (2, 0)
    warnings = validate(load_any(BROKEN / "n5_no_ground.netlist.json"))
    assert not has_errors(warnings)
    assert count_by_severity(warnings) == (0, 1)
    assert count_by_severity([]) == (0, 0)


def test_findings_unpack_as_severity_message_location():
    severity, message, location = validate(
        load_any(BROKEN / "n2_missing_net.netlist.json")
    )[0]
    assert severity is Severity.ERROR
    assert "undeclared net" in message
    assert location == "circuit.components[1] (R1)"


def test_format_finding():
    finding = Finding(Severity.WARNING, "something is odd", "circuit.nets")
    assert format_finding(finding) == "warning: circuit.nets: something is odd"


def test_severity_stringifies_as_its_value():
    assert str(Severity.ERROR) == "error"
    assert f"{Severity.WARNING}" == "warning"


# --- cases the corpus files do not cover ------------------------------------


def test_pin_name_check_understands_optional_and_dynamic_pins():
    ir = NetlistIR(
        circuit=Scope(
            components=[
                Component(
                    id="Q1",
                    kind=Kind.BJT_NPN,
                    pins={"c": "a", "b": "b", "e": "0", "s": "0"},
                ),
                Component(id="U1", kind=Kind.GENERIC, pins={"1": "a", "2": "b"}),
            ],
            nets={"a": Net("a"), "b": Net("b"), "0": Net("0", "ground")},
        )
    )
    assert validate_netlist(ir) == []


def test_generic_pins_must_be_numbered_from_one():
    ir = NetlistIR(
        circuit=Scope(
            components=[
                Component(id="U1", kind=Kind.GENERIC, pins={"1": "a", "3": "a"})
            ],
            nets={"a": Net("a"), "0": Net("0", "ground")},
        )
    )
    assert validate_netlist(ir) == [
        Finding(
            ERROR,
            "generic component pins must be named 1, 2, got 1, 3",
            "circuit.components[0] (U1)",
        )
    ]


def test_control_must_point_at_a_vsource_not_just_any_component():
    ir = NetlistIR(
        circuit=Scope(
            components=[
                Component(id="R1", kind=Kind.RESISTOR, pins={"a": "a", "b": "0"}),
                Component(
                    id="F1", kind=Kind.CCCS, pins={"p": "a", "n": "0"}, control="R1"
                ),
            ],
            nets={"a": Net("a"), "0": Net("0", "ground")},
        )
    )
    assert validate_netlist(ir) == [
        Finding(
            ERROR,
            "control references 'R1', which is not a vsource",
            "circuit.components[1] (F1)",
        )
    ]


def test_controlled_source_without_control_is_an_error():
    ir = NetlistIR(
        circuit=Scope(
            components=[Component(id="H1", kind=Kind.CCVS, pins={"p": "a", "n": "0"})],
            nets={"a": Net("a"), "0": Net("0", "ground")},
        )
    )
    assert validate_netlist(ir) == [
        Finding(
            ERROR,
            "kind ccvs requires a controlling voltage source in 'control'",
            "circuit.components[0] (H1)",
        )
    ]


def test_subcircuit_pins_must_match_the_definition_ports():
    ir = NetlistIR(
        circuit=Scope(
            components=[
                Component(
                    id="X1",
                    kind=Kind.SUBCIRCUIT,
                    pins={"in": "a", "vcc": "a"},
                    subckt="amp",
                )
            ],
            nets={"a": Net("a"), "0": Net("0", "ground")},
        ),
        subcircuits={"amp": SubcktDef(ports=["in", "out"])},
    )
    assert validate_netlist(ir) == [
        Finding(
            ERROR,
            "pins in, vcc do not match the ports of subcircuit 'amp' (in, out)",
            "circuit.components[0] (X1)",
        )
    ]


def test_subcircuit_lookup_is_case_insensitive():
    ir = NetlistIR(
        circuit=Scope(
            components=[
                Component(id="X1", kind=Kind.SUBCIRCUIT, pins={"in": "a"}, subckt="AMP")
            ],
            nets={"a": Net("a"), "0": Net("0", "ground")},
        ),
        subcircuits={"amp": SubcktDef(ports=["in"])},
    )
    assert validate_netlist(ir) == []


def test_two_ground_nets_warn():
    ir = NetlistIR(
        circuit=Scope(
            nets={"0": Net("0", "ground"), "gnd": Net("gnd", "ground")},
        )
    )
    assert validate_netlist(ir) == [
        Finding(
            WARNING,
            "2 ground-class nets (0, gnd); a flat design should have exactly one",
            "circuit.nets",
        )
    ]


def test_net_id_must_equal_net_name():
    ir = NetlistIR(circuit=Scope(nets={"0": Net("ground")}))
    findings = validate_netlist(ir)
    assert findings[0] == Finding(
        ERROR,
        "net id '0' does not match its name 'ground'",
        "circuit.nets['0']",
    )


def test_subcircuit_scopes_are_validated_too():
    ir = NetlistIR(
        circuit=Scope(nets={"0": Net("0", "ground")}),
        subcircuits={
            "amp": SubcktDef(
                ports=["in"],
                components=[
                    Component(id="R1", kind=Kind.RESISTOR, pins={"a": "in", "b": "out"})
                ],
                nets={"in": Net("in")},
            )
        },
    )
    assert validate_netlist(ir) == [
        Finding(
            ERROR,
            "pin 'b' references undeclared net 'out'",
            "subcircuits.amp.components[0] (R1)",
        )
    ]


def test_node_component_with_correct_pins_is_clean_in_every_orientation():
    for rot in (0, 90, 180, 270):
        for mirror in (False, True):
            node = NodeComponent(
                ref="M1",
                kind=Kind.NMOS,
                symbol="nmos",
                at=(10, 10),
                rot=rot,  # type: ignore[arg-type]
                mirror=mirror,
                pins=resolve_pins(BUILTIN_SYMBOLS["nmos"], (10, 10), rot, mirror),
            )
            ir = SchematicIR(sheets=[Sheet(elements=[node])])
            assert validate_schematic(ir) == []


def test_node_component_pins_may_not_be_missing_or_extra():
    pins = resolve_pins(BUILTIN_SYMBOLS["npn"], (4, 4), 0, False)
    del pins["e"]
    pins["x"] = (4, 4)
    node = NodeComponent(
        ref="Q1", kind=Kind.BJT_NPN, symbol="npn", at=(4, 4), pins=pins
    )
    assert validate_schematic(SchematicIR(sheets=[Sheet(elements=[node])])) == [
        Finding(
            ERROR,
            "pin 'e' of symbol 'npn' is missing (expected at (4, 2))",
            "sheets[0].elements[0] (Q1)",
        ),
        Finding(
            ERROR,
            "pin 'x' is not a pin of symbol 'npn'",
            "sheets[0].elements[0] (Q1)",
        ),
    ]


def test_file_local_symbols_resolve():
    symbol = SymbolDef(
        size=(4, 2),
        pins={"1": PinDef(offset=(-2, 0)), "2": PinDef(offset=(2, 0))},
    )
    ir = SchematicIR(
        symbols={"subckt:amp": symbol},
        sheets=[
            Sheet(
                elements=[
                    NodeComponent(
                        ref="X1",
                        kind=Kind.SUBCIRCUIT,
                        symbol="subckt:amp",
                        at=(4, 4),
                        pins={"1": (2, 4), "2": (6, 4)},
                    )
                ]
            )
        ],
    )
    assert validate_schematic(ir) == []


def test_wire_ends_may_land_on_another_wire_of_the_same_net():
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(6, 0)),
                    PathComponent(ref="R2", kind=Kind.RESISTOR, a=(0, 4), b=(6, 4)),
                    PathComponent(ref="R3", kind=Kind.RESISTOR, a=(10, 0), b=(10, 4)),
                    Wire(net="n", points=[(6, 0), (10, 0)]),
                    # ends on the interior of the wire above, forming a T
                    Wire(net="n", points=[(6, 4), (8, 4), (8, 0)]),
                    Junction(at=(8, 0)),
                ]
            )
        ]
    )
    assert validate_schematic(ir) == []


def test_wire_end_on_a_different_net_is_still_dangling():
    # Both wires stop at (8, 0), but they carry different nets, so neither end
    # counts as connected.
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(6, 0)),
                    Wire(net="a", points=[(6, 0), (8, 0)]),
                    Wire(net="b", points=[(8, 0), (8, 4)]),
                    NetSymbol(net="b", variant="ground", at=(8, 4)),
                ]
            )
        ]
    )
    dangling = "dangling wire end at (8, 0): no component pin, wire, net symbol, "
    assert validate_schematic(ir) == [
        Finding(ERROR, dangling + "or port there", "sheets[0].elements[1]"),
        Finding(ERROR, dangling + "or port there", "sheets[0].elements[2]"),
    ]


def test_ports_and_net_symbols_anchor_wire_ends():
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    Port(name="in", at=(0, 0), direction="left"),
                    Wire(net="n", points=[(0, 0), (4, 0)]),
                    NetSymbol(net="n", variant="ground", at=(4, 0)),
                ]
            )
        ]
    )
    assert validate_schematic(ir) == []


def test_tap_net_symbols_do_not_count_as_conductors():
    # Two pins plus a tap label at the same point must not demand a junction.
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 4), b=(6, 4)),
                    PathComponent(ref="C1", kind=Kind.CAPACITOR, a=(6, 4), b=(6, 0)),
                    NetSymbol(net="out", variant="tap", at=(6, 4), text="vout"),
                ]
            )
        ]
    )
    assert validate_schematic(ir) == []


def test_wire_needs_at_least_two_points():
    ir = SchematicIR(sheets=[Sheet(elements=[Wire(net="n", points=[(0, 0)])])])
    assert validate_schematic(ir) == [
        Finding(
            ERROR,
            "wire on net 'n' needs at least two points",
            "sheets[0].elements[0]",
        )
    ]


def test_zero_length_wire_segment_is_not_axis_aligned():
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(4, 0)),
                    Wire(net="n", points=[(4, 0), (4, 0)]),
                ]
            )
        ]
    )
    messages = [finding.message for finding in validate_schematic(ir)]
    assert messages == ["wire segment 0 is not axis-aligned: (4, 0) to (4, 0)"]


def test_touching_components_do_not_count_as_overlapping():
    ir = SchematicIR(
        sheets=[
            Sheet(
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 4), b=(6, 4)),
                    PathComponent(ref="C1", kind=Kind.CAPACITOR, a=(6, 4), b=(6, 0)),
                    NodeComponent(
                        ref="M1",
                        kind=Kind.NMOS,
                        symbol="nmos",
                        at=(10, 4),
                        pins=resolve_pins(BUILTIN_SYMBOLS["nmos"], (10, 4), 0, False),
                    ),
                ]
            )
        ]
    )
    assert validate_schematic(ir) == []


def test_node_components_sharing_space_overlap():
    def node(ref: str, at: tuple[int, int]) -> NodeComponent:
        return NodeComponent(
            ref=ref,
            kind=Kind.NMOS,
            symbol="nmos",
            at=at,
            pins=resolve_pins(BUILTIN_SYMBOLS["nmos"], at, 0, False),
        )

    ir = SchematicIR(sheets=[Sheet(elements=[node("M1", (0, 0)), node("M2", (1, 1))])])
    messages = [finding.message for finding in validate_schematic(ir)]
    assert messages == ["bounding box overlaps component 'M2'"]


def test_odd_sized_symbols_do_not_false_positive_on_touching():
    symbol = SymbolDef(size=(3, 3), pins={"1": PinDef(offset=(0, 1))})
    ir = SchematicIR(
        symbols={"odd": symbol},
        sheets=[
            Sheet(
                elements=[
                    NodeComponent(
                        ref="U1",
                        kind=Kind.GENERIC,
                        symbol="odd",
                        at=(0, 0),
                        pins={"1": (0, 1)},
                    ),
                    NodeComponent(
                        ref="U2",
                        kind=Kind.GENERIC,
                        symbol="odd",
                        at=(3, 0),
                        pins={"1": (3, 1)},
                    ),
                ]
            )
        ],
    )
    assert validate_schematic(ir) == []


def test_every_sheet_is_validated():
    ir = SchematicIR(
        sheets=[
            Sheet(name="main", elements=[]),
            Sheet(
                name="second",
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(1, 0))
                ],
            ),
        ]
    )
    findings = validate_schematic(ir)
    assert len(findings) == 1
    assert findings[0].location.startswith("sheets[1]")
