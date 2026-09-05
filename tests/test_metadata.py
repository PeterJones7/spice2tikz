"""Metadata in inline comments: ``; key=value``.

A simulator ignores everything after a ``;``. That makes it the one place a
deck can say something about the *drawing* without changing the circuit, which
is what these tests are about: the parser keeps the comment's ``key=value``
pairs, the IR carries them, and two consumers act on them — ``symbol=opamp``
picks a shape, and ``labels=`` decides what text a component shows.

The rule running through all of it is that an unknown key is not an error. A
deck must still convert on a version of the tool that has never heard of the
key someone wrote.
"""

from __future__ import annotations

import pytest

from spice2tikz import netlist_ir, schematic_ir
from spice2tikz.emit.circuitikz import emit_snippet
from spice2tikz.layout import layout
from spice2tikz.netlist_ir import Kind
from spice2tikz.spice_parser import assemble_lines, parse_spice
from spice2tikz.symbols import BUILTIN_SYMBOLS, OPAMP_PINS, opamp_symbol
from spice2tikz.validate import Severity, validate_netlist, validate_schematic

OPAMP_DECK = """* Inverting amplifier
.subckt oa plus minus out vcc vee ; symbol=opamp
.ends oa
V1 in 0 AC 1
V2 vcc 0 DC 15
V3 vee 0 DC -15
R1 in inv 10k
R2 inv sig 100k
X1 0 inv sig vcc vee oa
RL sig 0 10k
.end
"""


def convert(deck: str) -> tuple[schematic_ir.SchematicIR, list[str]]:
    """Parse and lay out *deck*, returning the sheet and every warning."""
    warnings: list[str] = []
    return layout(parse_spice(deck, warnings=warnings), warnings=warnings), warnings


def component(ir: netlist_ir.NetlistIR, ref: str) -> netlist_ir.Component:
    return next(c for c in ir.circuit.components if c.id == ref)


def drawn(sheet: schematic_ir.SchematicIR, ref: str) -> object:
    for element in sheet.sheets[0].elements:
        if getattr(element, "ref", None) == ref:
            return element
    raise AssertionError(f"{ref} is not on the sheet")


# --- part 1: the parser keeps the comment ------------------------------------


def test_a_comment_yields_its_key_value_pairs():
    (line,) = assemble_lines("* t\nR1 in out 10k ; labels=ref,value\n")[1:]
    assert line.metadata == {"labels": "ref,value"}
    assert line.text == "R1 in out 10k", "the card itself must not change"


def test_several_pairs_on_one_card():
    (line,) = assemble_lines("* t\nR1 in out 10k ; labels=ref symbol=box\n")[1:]
    assert line.metadata == {"labels": "ref", "symbol": "box"}


def test_prose_is_not_metadata_and_not_an_error():
    (line,) = assemble_lines("* t\nR1 in out 10k ; the bias resistor\n")[1:]
    assert line.metadata == {}


def test_a_dollar_comment_carries_metadata_too():
    (line,) = assemble_lines("* t\nR1 in out 10k $ labels=none\n")[1:]
    assert line.metadata == {"labels": "none"}


def test_keys_are_lowercased_and_values_are_not():
    (line,) = assemble_lines("* t\n.subckt s a b ; SYMBOL=OpAmp\n")[1:]
    assert line.metadata == {"symbol": "OpAmp"}


def test_a_continuation_may_carry_the_metadata():
    lines = assemble_lines("* t\nV1 in 0 DC 5\n+ AC 1 ; labels=none\n")
    assert lines[1].text == "V1 in 0 DC 5 AC 1"
    assert lines[1].metadata == {"labels": "none"}


def test_metadata_reaches_the_component():
    ir = parse_spice("* t\nR1 in out 10k ; labels=ref\nV1 in 0 DC 5\n")
    assert component(ir, "R1").meta == {"labels": "ref"}
    assert component(ir, "V1").meta == {}


def test_an_unknown_key_is_carried_and_never_an_error():
    warnings: list[str] = []
    ir = parse_spice("* t\nR1 in 0 10k ; wavelength=650nm\n", warnings=warnings)
    assert component(ir, "R1").meta == {"wavelength": "650nm"}
    assert warnings == []
    assert not [f for f in validate_netlist(ir) if f.severity is Severity.ERROR]


def test_metadata_survives_a_json_round_trip():
    ir = parse_spice("* t\nR1 in 0 10k ; labels=none\nV1 in 0 DC 5\n")
    reloaded = netlist_ir.loads(netlist_ir.dumps(ir))
    assert component(reloaded, "R1").meta == {"labels": "none"}
    assert netlist_ir.dumps(reloaded) == netlist_ir.dumps(ir)


def test_a_subckt_definition_carries_its_own_metadata():
    ir = parse_spice(OPAMP_DECK)
    assert ir.subcircuits["oa"].meta == {"symbol": "opamp"}
    assert ir.subcircuits["oa"].symbol == "opamp"


def test_a_definition_without_metadata_asks_for_no_symbol():
    ir = parse_spice("* t\n.subckt s a b\n.ends\nX1 in 0 s\nV1 in 0 DC 5\n")
    assert ir.subcircuits["s"].symbol is None


def test_a_subckt_round_trips_with_its_metadata():
    ir = parse_spice(OPAMP_DECK)
    reloaded = netlist_ir.loads(netlist_ir.dumps(ir))
    assert reloaded.subcircuits["oa"].symbol == "opamp"


# --- part 2: symbol=opamp ----------------------------------------------------


def test_an_instance_inherits_the_symbol_its_definition_asks_for():
    ir = parse_spice(OPAMP_DECK)
    assert component(ir, "X1").meta["symbol"] == "opamp"


def test_the_definition_may_come_after_the_instance():
    """A deck is not required to define a subcircuit before using it."""
    deck = OPAMP_DECK.replace(
        ".subckt oa plus minus out vcc vee ; symbol=opamp\n.ends oa\n", ""
    ).replace(
        ".end\n", ".subckt oa plus minus out vcc vee ; symbol=opamp\n.ends oa\n.end\n"
    )
    assert component(parse_spice(deck), "X1").meta["symbol"] == "opamp"


def test_an_opamp_is_emitted_as_the_circuitikz_shape():
    sheet, warnings = convert(OPAMP_DECK)
    assert warnings == []
    assert "\\node[op amp" in emit_snippet(sheet)


def test_a_subcircuit_without_the_metadata_stays_a_box():
    """Nothing is inferred from a name: this one is called `lm741`."""
    deck = OPAMP_DECK.replace(" ; symbol=opamp", "").replace("oa", "lm741")
    text = emit_snippet(convert(deck)[0])
    assert "op amp" not in text
    assert "rectangle" in text


def test_the_port_names_are_not_drawn():
    """The shape carries its own + and - markings; the deck's words are not.

    The ports are given names no net in this deck shares, so that a rail
    label cannot be mistaken for a port label.
    """
    deck = OPAMP_DECK.replace(
        ".subckt oa plus minus out vcc vee", ".subckt oa pport mport oport up1 dn1"
    )
    text = emit_snippet(convert(deck)[0])
    for port in ("pport", "mport", "oport", "up1", "dn1"):
        assert port not in text, f"{port} is drawn as text"


def test_the_ports_map_onto_the_anchors_by_position():
    sheet, _ = convert(OPAMP_DECK)
    symbol = sheet.symbols["opamp:oa"]
    assert [pin.anchor for pin in symbol.pins.values()] == list(OPAMP_PINS)
    assert list(symbol.pins) == ["plus", "minus", "out", "vcc", "vee"]


def test_the_pins_keep_the_names_the_netlist_uses():
    """The sheet must stay checkable against the circuit it came from."""
    sheet, _ = convert(OPAMP_DECK)
    assert set(drawn(sheet, "X1").pins) == {"plus", "minus", "out", "vcc", "vee"}


def test_port_names_do_not_matter():
    """`PLUS`, `IN+` and `VP` all mean the same thing: the first port."""
    renamed = OPAMP_DECK.replace(
        ".subckt oa plus minus out vcc vee", ".subckt oa np nn y vp vn"
    )
    sheet, warnings = convert(renamed)
    assert warnings == []
    symbol = sheet.symbols["opamp:oa"]
    assert symbol.pins["np"].anchor == "+"
    assert symbol.pins["y"].anchor == "out"


def test_three_ports_are_an_ideal_opamp_with_no_supplies():
    deck = OPAMP_DECK.replace(
        ".subckt oa plus minus out vcc vee ; symbol=opamp",
        ".subckt oa plus minus out ; symbol=opamp",
    ).replace("X1 0 inv sig vcc vee oa", "X1 0 inv sig oa")
    sheet, warnings = convert(deck)
    assert warnings == []
    assert list(sheet.symbols["opamp:oa"].pins) == ["plus", "minus", "out"]
    assert "\\node[op amp" in emit_snippet(sheet)


@pytest.mark.parametrize(
    ("ports", "nodes"),
    [("a b", "in 0"), ("a b c d e f", "in 0 a b c d")],
)
def test_a_port_count_it_cannot_map_falls_back_to_a_box(ports: str, nodes: str):
    deck = (
        f"* t\n.subckt oa {ports} ; symbol=opamp\n.ends oa\n"
        f"X1 {nodes} oa\nV1 in 0 DC 5\n.end\n"
    )
    sheet, warnings = convert(deck)
    assert any("symbol=opamp expects 3 to 5 ports" in w for w in warnings)
    assert "op amp" not in emit_snippet(sheet)


def test_an_unknown_symbol_warns_and_draws_a_box():
    deck = OPAMP_DECK.replace("symbol=opamp", "symbol=wombat")
    sheet, warnings = convert(deck)
    assert any("symbol='wombat' is not a symbol" in w for w in warnings)
    assert "rectangle" in emit_snippet(sheet)


def test_the_opamp_sheet_is_valid():
    sheet, _ = convert(OPAMP_DECK)
    assert [f for f in validate_schematic(sheet) if f.severity is Severity.ERROR] == []


def test_the_opamp_sheet_round_trips():
    sheet, _ = convert(OPAMP_DECK)
    reloaded = schematic_ir.loads(schematic_ir.dumps(sheet))
    assert emit_snippet(reloaded) == emit_snippet(sheet)


def test_a_pin_names_the_anchor_it_is_drawn_from():
    """Leads come off the anchor, whatever the pin is called."""
    text = emit_snippet(convert(OPAMP_DECK)[0])
    for anchor in ("+", "-", "out", "up", "down"):
        assert f".{anchor})" in text


def test_the_builtin_opamp_is_the_canonical_five():
    assert BUILTIN_SYMBOLS["opamp"] == opamp_symbol()
    assert list(BUILTIN_SYMBOLS["opamp"].pins) == list(OPAMP_PINS)


# --- part 3: labels= ---------------------------------------------------------


def resistor_options(deck: str, ref: str) -> str:
    """Return the circuitikz options of the `to[...]` drawing *ref*."""
    element = drawn(convert(deck)[0], ref)
    assert isinstance(element, schematic_ir.PathComponent)
    line = next(
        line
        for line in emit_snippet(convert(deck)[0]).splitlines()
        if f"({element.a[0]},{element.a[1]}) to[" in line
    )
    return line[line.index("to[") + 3 : line.rindex("]")]


def deck_with(labels: str) -> str:
    request = f" ; labels={labels}" if labels else ""
    return f"* t\nV1 in 0 DC 5\nR1 in out 10k{request}\nR2 out 0 22k\n.end\n"


def test_labels_ref_shows_only_the_reference():
    assert resistor_options(deck_with("ref"), "R1") == "R=$R_1$"


def test_labels_value_shows_only_the_value():
    options = resistor_options(deck_with("value"), "R1")
    assert options == "R, a=\\SI{10}{\\kilo\\ohm}"


def test_labels_ref_value_shows_both():
    options = resistor_options(deck_with("ref,value"), "R1")
    assert options == "R=$R_1$, a=\\SI{10}{\\kilo\\ohm}"


def test_labels_none_shows_neither():
    assert resistor_options(deck_with("none"), "R1") == "R"


def test_no_metadata_leaves_the_default_alone():
    """Both, as it always has been: existing decks must render unchanged."""
    assert resistor_options(deck_with(""), "R1") == resistor_options(
        deck_with("ref,value"), "R1"
    )


def test_order_within_the_list_does_not_matter():
    assert resistor_options(deck_with("value,ref"), "R1") == resistor_options(
        deck_with("ref,value"), "R1"
    )


def test_a_space_inside_the_list_is_reported_rather_than_guessed_at():
    """Metadata is whitespace-delimited, so `ref, value` loses the `value`.

    Quietly drawing the reference alone would be a request half-honoured; the
    defaults stand instead, and the deck is told why.
    """
    _, warnings = convert(deck_with("ref, value"))
    assert any("labels='ref,' is not understood" in w for w in warnings)
    assert resistor_options(deck_with("ref, value"), "R1") == resistor_options(
        deck_with(""), "R1"
    )


def test_a_misspelling_warns_and_keeps_the_defaults():
    sheet, warnings = convert(deck_with("refs"))
    assert any("labels='refs' is not understood" in w for w in warnings)
    assert drawn(sheet, "R1").value_label is not None


def test_none_together_with_something_else_warns_and_draws_nothing():
    _, warnings = convert(deck_with("none,ref"))
    assert any("asks for 'none' as well as" in w for w in warnings)
    assert resistor_options(deck_with("none,ref"), "R1") == "R"


def test_a_device_can_show_its_model_as_its_value():
    deck = "* t\nV1 d 0 DC 5\nM1 d g 0 0 nfet ; labels=value\nV2 g 0 DC 1\n"
    deck += ".model nfet nmos\n.end\n"
    text = emit_snippet(convert(deck)[0])
    assert "label=right:{nfet}" in text
    assert "$M_1$" not in text


def test_a_device_shows_no_value_unless_asked():
    """Node components never carried one, and existing sheets must not change."""
    deck = "* t\nV1 d 0 DC 5\nM1 d g 0 0 nfet\nV2 g 0 DC 1\n.model nfet nmos\n.end\n"
    sheet, _ = convert(deck)
    assert drawn(sheet, "M1").value_label is None
    assert "nfet" not in emit_snippet(sheet)


def test_an_opamp_can_show_its_definition_name():
    deck = OPAMP_DECK.replace(
        "X1 0 inv sig vcc vee oa", "X1 0 inv sig vcc vee oa ; labels=ref,value"
    )
    assert "{oa}" in emit_snippet(convert(deck)[0])


def test_a_node_value_label_round_trips():
    deck = "* t\nV1 d 0 DC 5\nM1 d g 0 0 nfet ; labels=value\nV2 g 0 DC 1\n"
    deck += ".model nfet nmos\n.end\n"
    sheet, _ = convert(deck)
    reloaded = schematic_ir.loads(schematic_ir.dumps(sheet))
    assert emit_snippet(reloaded) == emit_snippet(sheet)
    assert drawn(reloaded, "M1").value_label is not None


def test_labels_on_a_subcircuit_box():
    deck = "* t\n.subckt s a b\n.ends\nX1 in 0 s ; labels=none\nV1 in 0 DC 5\n.end\n"
    text = emit_snippet(convert(deck)[0])
    assert "rectangle" in text
    assert "$X_1$" not in text


@pytest.mark.parametrize("kinds", [Kind.RESISTOR, Kind.VSOURCE])
def test_every_kind_of_card_can_carry_metadata(kinds: Kind):
    """`_add` is the one funnel every card goes through, so all of them do."""
    deck = "* t\nV1 in 0 DC 5 ; labels=none\nR1 in 0 10k ; labels=none\n"
    ir = parse_spice(deck)
    assert all(c.meta == {"labels": "none"} for c in ir.circuit.components)
