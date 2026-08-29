"""Tests for the LTspice ``.asc`` importer (roadmap §3.1-3.3).

Stage 1 (record parsing and encoding detection) and stage 2 (the mapping onto
the Schematic IR) are unit-tested against hand-written fragments; the corpus
under ``tests/corpus/asc/`` is then imported end to end and compared against
golden IR JSON and golden ``.tex`` in ``tests/golden/asc/``.

Regenerate the goldens with ``pytest tests/test_asc_importer.py
--update-golden`` and review the diff in git.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from spice2tikz import schematic_ir
from spice2tikz._serde import IRError
from spice2tikz.asc_importer import (
    GENERATOR,
    ORIENTATION_TO_IR,
    SYMBOL_TABLE,
    AscOrientation,
    _to_ir,
    decode_asc,
    import_asc,
    load_asc,
    parse_asc,
)
from spice2tikz.emit.circuitikz import emit_snippet, emit_standalone
from spice2tikz.schematic_ir import (
    Junction,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    SchematicIR,
    Wire,
)
from spice2tikz.symbols import lookup_symbol, resolve_pins
from spice2tikz.validate import Severity, format_finding, validate

CORPUS = Path(__file__).parent / "corpus" / "asc"
CORPUS_FILES = sorted(CORPUS.glob("*.asc"))
CORPUS_NAMES = [path.stem for path in CORPUS_FILES]

EXPECTED_WARNINGS: dict[str, tuple[str, ...]] = {
    "unknown_symbol": (
        "1 decorative line record(s) not imported",
        "1 decorative rectangle record(s) not imported",
        "1 decorative circle record(s) not imported",
        "1 DATAFLAG record(s) not imported: the Schematic IR has no probe annotations",
        r"unknown symbol 'Opamps\\UniversalOpamp2' (U1): drawn as a generic "
        r"box with 3 inferred pin(s)",
    ),
}
"""Import warnings each corpus file is *expected* to produce.

Every other file must import silently: a warning that nobody asked for is a
bug, and pinning the exact text keeps a change to the diagnostics visible.
"""

MINIMAL = """\
Version 4
SHEET 1 880 680
WIRE 0 0 96 0
FLAG 48 0 0
SYMBOL res -16 -80 R0
SYMATTR InstName R1
SYMATTR Value 1k
"""


def load(name: str) -> SchematicIR:
    """Import the corpus file *name* (warnings discarded)."""
    return load_asc(CORPUS / f"{name}.asc")


def elements_of(ir: SchematicIR, kind: type) -> list:
    """Return every element of ``sheets[0]`` that is an instance of *kind*."""
    return [element for element in ir.sheets[0].elements if isinstance(element, kind)]


def sole(items: list):
    """Return the only item of *items*, asserting there is exactly one."""
    assert len(items) == 1, items
    return items[0]


# --- 3.1 encoding detection --------------------------------------------------


def test_decode_plain_ascii():
    assert decode_asc(b"Version 4\n") == "Version 4\n"


def test_decode_utf16_le_with_bom():
    text = "Version 4\r\nSHEET 1 880 680\r\n"
    assert decode_asc(b"\xff\xfe" + text.encode("utf-16-le")) == text


def test_decode_utf16_be_with_bom():
    text = "Version 4\r\n"
    assert decode_asc(b"\xfe\xff" + text.encode("utf-16-be")) == text


def test_decode_utf8_with_bom():
    assert decode_asc(b"\xef\xbb\xbfVersion 4\n") == "Version 4\n"


def test_decode_utf8_without_bom_keeps_non_ascii():
    assert decode_asc("TEXT 0 0 Left 2 ;µF\n".encode()) == "TEXT 0 0 Left 2 ;µF\n"


def test_decode_falls_back_to_latin_1():
    # 0xB5 is MICRO SIGN in Latin-1 and an invalid UTF-8 start byte.
    assert decode_asc(b"TEXT 0 0 Left 2 ;\xb5F\n") == "TEXT 0 0 Left 2 ;µF\n"


def test_utf16_corpus_file_really_is_utf16():
    data = (CORPUS / "utf16_divider.asc").read_bytes()
    assert data.startswith(b"\xff\xfe")
    assert "SYMATTR InstName R1" in decode_asc(data)


# --- 3.1 record parsing ------------------------------------------------------


def test_parses_every_record_type():
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 96 96 96 64\n"
        "FLAG 96 208 0\n"
        "IOPIN 96 64 In\n"
        "SYMBOL res 80 96 R0\n"
        "WINDOW 0 -32 40 Left 2\n"
        "SYMATTR InstName R1\n"
        "SYMATTR Value 10k\n"
        "DATAFLAG 128 32 V(out)\n"
        "LINE Normal 0 0 64 0\n"
        "RECTANGLE Normal 0 0 64 64 2\n"
        "CIRCLE Normal 0 0 32 32\n"
        "TEXT -48 264 Left 2 !.tran 1m\n"
    )
    asc = parse_asc(text)
    assert asc.version == 4
    assert len(asc.sheets) == 1
    assert (asc.sheets[0].number, asc.sheets[0].width, asc.sheets[0].height) == (
        1,
        880,
        680,
    )
    assert (asc.wires[0].start, asc.wires[0].end) == ((96, 96), (96, 64))
    assert (asc.flags[0].at, asc.flags[0].name) == ((96, 208), "0")
    assert asc.flags[0].is_ground
    assert (asc.iopins[0].at, asc.iopins[0].direction) == ((96, 64), "In")
    assert (asc.symbols[0].name, asc.symbols[0].at) == ("res", (80, 96))
    assert asc.symbols[0].orientation == AscOrientation(0, False)
    assert asc.symbols[0].attrs == {"InstName": "R1", "Value": "10k"}
    assert asc.symbols[0].windows[0].number == 0
    assert asc.dataflags[0].expression == "V(out)"
    assert [shape.shape for shape in asc.shapes] == ["line", "rectangle", "circle"]
    assert asc.texts[0].text == "!.tran 1m"
    assert asc.texts[0].is_directive


def test_handles_crlf_and_blank_lines():
    asc = parse_asc("Version 4\r\n\r\nWIRE 0 0 16 0\r\n   \r\n")
    assert asc.version == 4
    assert len(asc.wires) == 1


def test_unknown_record_warns_and_is_ignored():
    warnings: list[str] = []
    asc = parse_asc("Version 4\nBANANA 1 2 3\nWIRE 0 0 16 0\n", warnings=warnings)
    assert len(asc.wires) == 1
    assert warnings == ["line 2: unknown record type 'BANANA' ignored"]


def test_malformed_record_warns_and_is_skipped():
    warnings: list[str] = []
    asc = parse_asc("Version 4\nWIRE 0 0 sixteen 0\nWIRE 0 0 16 0\n", warnings=warnings)
    assert len(asc.wires) == 1
    assert warnings == ["line 2: x2 'sixteen' is not an integer — record ignored"]


def test_symattr_before_any_symbol_warns():
    warnings: list[str] = []
    parse_asc("Version 4\nSYMATTR InstName R1\n", warnings=warnings)
    assert warnings == ["line 2: SYMATTR before any SYMBOL — record ignored"]


def test_symbol_name_may_carry_a_library_path():
    asc = parse_asc("SYMBOL Opamps\\UniversalOpamp2 128 -160 R0\n")
    assert asc.symbols[0].name == "Opamps\\UniversalOpamp2"
    assert asc.symbols[0].base_name == "universalopamp2"


def test_attribute_values_keep_their_spaces():
    asc = parse_asc("SYMBOL voltage 0 0 R0\nSYMATTR Value SINE(0 1 1k)\n")
    assert asc.symbols[0].attr("value") == "SINE(0 1 1k)"
    assert asc.symbols[0].attr("VALUE") == "SINE(0 1 1k)"
    assert asc.symbols[0].attr("Missing") is None


def test_empty_input_is_a_parse_error():
    with pytest.raises(IRError):
        parse_asc("")


def test_a_file_with_no_records_is_a_parse_error():
    with pytest.raises(IRError, match="not an LTspice schematic"):
        parse_asc("this is not a schematic\njust some prose\n")


def test_orientation_round_trips_its_text():
    for text in ORIENTATION_TO_IR:
        assert AscOrientation.parse(text).text == text


def test_orientation_place_matches_the_ltspice_matrices():
    assert AscOrientation.parse("R0").place((10, 3)) == (10, 3)
    assert AscOrientation.parse("R90").place((10, 3)) == (-3, 10)
    assert AscOrientation.parse("R180").place((10, 3)) == (-10, -3)
    assert AscOrientation.parse("R270").place((10, 3)) == (3, -10)
    assert AscOrientation.parse("M0").place((10, 3)) == (-10, 3)
    assert AscOrientation.parse("M90").place((10, 3)) == (3, 10)
    assert AscOrientation.parse("M180").place((10, 3)) == (10, -3)
    assert AscOrientation.parse("M270").place((10, 3)) == (-3, -10)


# --- 3.2 coordinates ---------------------------------------------------------


def test_grid_rescale_and_y_flip():
    ir = import_asc(MINIMAL)
    resistor = sole(elements_of(ir, PathComponent))
    # res pins sit at (16, 16) and (16, 96) from the symbol origin (-16, -80):
    # (0, -64) and (0, 16) in file units, which is (0, 4) and (0, -1) y-up.
    assert (resistor.a, resistor.b) == ((0, 4), (0, -1))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (16, 1), (-16, -1), (8, 1), (-8, -1), (24, 2), (-24, -2), (20, 1)],
)
def test_off_grid_rounding_is_symmetric(value: int, expected: int):
    assert _to_ir((value, 0))[0] == expected


def test_off_grid_coordinates_are_rounded_with_a_warning():
    warnings: list[str] = []
    ir = import_asc(
        "Version 4\nWIRE 0 0 20 0\nFLAG 0 0 0\nFLAG 20 0 0\n", warnings=warnings
    )
    wire = sole(elements_of(ir, Wire))
    assert wire.points == [(0, 0), (1, 0)]
    assert warnings == [
        "wire end at (20, 0) is not on the 16-unit grid; rounded to (1, 0)",
        "flag '0' at (20, 0) is not on the 16-unit grid; rounded to (1, 0)",
    ]


# --- 3.2 orientations --------------------------------------------------------


RES_PIN_POSITIONS = {
    "R0": ((1, -1), (1, -6)),
    "R90": ((-1, -1), (-6, -1)),
    "R180": ((-1, 1), (-1, 6)),
    "R270": ((1, 1), (6, 1)),
    "M0": ((-1, -1), (-1, -6)),
    "M90": ((1, -1), (6, -1)),
    "M180": ((1, 1), (1, 6)),
    "M270": ((-1, 1), (-6, 1)),
}


@pytest.mark.parametrize("orientation", sorted(RES_PIN_POSITIONS))
def test_path_component_pins_follow_every_orientation(orientation: str):
    ir = import_asc(f"Version 4\nSYMBOL res 0 0 {orientation}\nSYMATTR InstName R1\n")
    resistor = sole(elements_of(ir, PathComponent))
    assert (resistor.a, resistor.b) == RES_PIN_POSITIONS[orientation]


NMOS_PLACEMENTS = {
    "R0": ((3, -3), 0, False),
    "R90": ((-3, -3), 270, False),
    "R180": ((-3, 3), 180, False),
    "R270": ((3, 3), 90, False),
    "M0": ((-3, -3), 0, True),
    "M90": ((3, -3), 90, True),
    "M180": ((3, 3), 180, True),
    "M270": ((-3, 3), 270, True),
}


@pytest.mark.parametrize("orientation", sorted(NMOS_PLACEMENTS))
def test_node_component_placement_for_every_orientation(orientation: str):
    ir = import_asc(f"Version 4\nSYMBOL nmos4 0 0 {orientation}\nSYMATTR InstName M1\n")
    node = sole(elements_of(ir, NodeComponent))
    assert (node.at, node.rot, node.mirror) == NMOS_PLACEMENTS[orientation]


@pytest.mark.parametrize("orientation", sorted(NMOS_PLACEMENTS))
def test_node_pins_agree_with_symbol_geometry(orientation: str):
    ir = import_asc(f"Version 4\nSYMBOL nmos4 0 0 {orientation}\nSYMATTR InstName M1\n")
    node = sole(elements_of(ir, NodeComponent))
    symbol = lookup_symbol(node.symbol)
    assert symbol is not None
    assert node.pins == resolve_pins(symbol, node.at, node.rot, node.mirror)


@pytest.mark.parametrize("orientation", sorted(NMOS_PLACEMENTS))
def test_node_pins_point_the_way_ltspice_puts_them(orientation: str):
    """Each IR pin must leave the body on the side LTspice's own pin does.

    This is the check that the y-down/y-up rotation translation is right: the
    built-in symbol is idealised, so the two positions differ, but the dominant
    axis each pin lies on — and its sign — must agree.
    """
    placement = AscOrientation.parse(orientation)
    definition = SYMBOL_TABLE["nmos4"]
    ir = import_asc(f"Version 4\nSYMBOL nmos4 0 0 {orientation}\nSYMATTR InstName M1\n")
    node = sole(elements_of(ir, NodeComponent))
    for pin, offset in definition.pins:
        source = _to_ir(placement.place(offset))
        expected = (source[0] - node.at[0], source[1] - node.at[1])
        actual = (node.pins[pin][0] - node.at[0], node.pins[pin][1] - node.at[1])
        assert _dominant(expected) == _dominant(actual), pin


def _dominant(vector: tuple[int, int]) -> tuple[int, int]:
    """Return the unit vector of *vector*'s dominant axis."""
    x, y = vector
    if abs(x) > abs(y):
        return (1 if x > 0 else -1, 0)
    if abs(y) > abs(x):
        return (0, 1 if y > 0 else -1)
    return (0, 0)


# --- 3.2 flags, ports, nets, junctions ---------------------------------------


def test_ground_flag_becomes_a_ground_net_symbol():
    ir = import_asc(MINIMAL)
    symbol = sole(elements_of(ir, NetSymbol))
    assert (symbol.variant, symbol.net, symbol.at) == ("ground", "0", (3, 0))
    assert symbol.text is None


def test_named_flag_becomes_a_tap():
    ir = import_asc(MINIMAL.replace("FLAG 48 0 0", "FLAG 48 0 vout"))
    symbol = sole(elements_of(ir, NetSymbol))
    assert (symbol.variant, symbol.net, symbol.text) == ("tap", "vout", "vout")


def test_iopin_becomes_a_port_and_suppresses_the_tap():
    ir = import_asc(
        "Version 4\n"
        "WIRE 0 0 96 0\n"
        "FLAG 0 0 in\n"
        "IOPIN 0 0 In\n"
        "FLAG 96 0 out\n"
        "IOPIN 96 0 Out\n"
    )
    assert elements_of(ir, NetSymbol) == []
    ports = elements_of(ir, Port)
    assert [(port.name, port.at, port.direction) for port in ports] == [
        ("in", (0, 0), "left"),
        ("out", (6, 0), "right"),
    ]


def test_bidirectional_iopin():
    ir = import_asc("Version 4\nWIRE 0 0 96 0\nFLAG 0 0 bus\nIOPIN 0 0 BiDir\n")
    assert sole(elements_of(ir, Port)).direction == "right"


def test_unknown_iopin_direction_warns():
    warnings: list[str] = []
    import_asc("Version 4\nWIRE 0 0 96 0\nIOPIN 0 0 Sideways\n", warnings=warnings)
    assert warnings == ["line 3: unknown IOPIN direction 'Sideways' — record ignored"]


def test_nets_take_their_flag_name_and_ground_wins():
    ir = import_asc(
        "Version 4\n"
        "WIRE 0 0 96 0\n"
        "WIRE 0 -64 96 -64\n"
        "FLAG 0 0 0\n"
        "FLAG 96 0 gnd_alias\n"
        "FLAG 0 -64 sig\n"
    )
    assert [wire.net for wire in elements_of(ir, Wire)] == ["0", "sig"]


def test_conflicting_flag_names_warn():
    warnings: list[str] = []
    import_asc(
        "Version 4\nWIRE 0 0 96 0\nFLAG 0 0 alpha\nFLAG 96 0 beta\n", warnings=warnings
    )
    assert warnings == ["net labelled 'alpha', 'beta' by several flags; using 'alpha'"]


def test_unflagged_nets_get_synthetic_names_in_reading_order():
    ir = import_asc(
        "Version 4\nWIRE 0 0 96 0\nWIRE 0 -64 96 -64\nWIRE 0 -128 96 -128\n"
    )
    # Reading order is top-to-bottom, so the highest wire on screen is N001.
    assert [wire.net for wire in elements_of(ir, Wire)] == ["N003", "N002", "N001"]


def test_junctions_are_inferred_where_three_conductors_meet():
    ir = import_asc(MINIMAL)
    assert [junction.at for junction in elements_of(ir, Junction)] == [(3, 0)]


def test_a_plain_corner_gets_no_junction():
    ir = import_asc("Version 4\nWIRE 0 0 96 0\nWIRE 96 0 96 -64\nFLAG 0 0 0\n")
    assert elements_of(ir, Junction) == []


def test_zero_length_and_diagonal_wires_are_dropped_with_a_warning():
    warnings: list[str] = []
    ir = import_asc(
        "Version 4\nWIRE 0 0 0 0\nWIRE 0 0 96 -64\nFLAG 0 0 0\n", warnings=warnings
    )
    assert elements_of(ir, Wire) == []
    assert warnings == [
        "zero-length wire at (0, 0) ignored",
        "diagonal wire (0, 0) to (96, -64) ignored: the Schematic IR is "
        "orthogonal (SPEC_IR invariant 6)",
    ]


# --- 3.2 values, labels, and unknown symbols ---------------------------------


def test_value_is_formatted_through_siunitx():
    ir = import_asc(MINIMAL)
    label = sole(elements_of(ir, PathComponent)).value_label
    assert label is not None
    assert label.text == "\\SI{1}{\\kilo\\ohm}"


def test_unparseable_value_falls_back_to_escaped_raw_text():
    ir = import_asc(MINIMAL.replace("SYMATTR Value 1k", "SYMATTR Value 4k7_hand"))
    label = sole(elements_of(ir, PathComponent)).value_label
    assert label is not None
    assert label.text == "4k7\\_hand"


def test_value_with_tikz_special_characters_is_braced():
    ir = import_asc(MINIMAL.replace("SYMATTR Value 1k", "SYMATTR Value PWL(0 0, 1m 1)"))
    label = sole(elements_of(ir, PathComponent)).value_label
    assert label is not None
    # Unbraced, the comma would end the `a=` option it is emitted into.
    assert label.text == "{PWL(0 0, 1m 1)}"


def test_node_component_label_carries_the_model_name():
    ir = import_asc(
        "Version 4\nSYMBOL nmos4 0 0 R0\nSYMATTR InstName M1\nSYMATTR Value BSS_1\n"
    )
    label = sole(elements_of(ir, NodeComponent)).label
    assert label is not None
    assert label.text == "$M_1$ BSS\\_1"


def test_missing_instname_is_synthesised_with_a_warning():
    warnings: list[str] = []
    ir = import_asc("Version 4\nSYMBOL res 0 0 R0\n", warnings=warnings)
    assert sole(elements_of(ir, PathComponent)).ref == "R1"
    assert warnings == ["symbol 'res' at (0, 0) has no InstName; named 'R1'"]


def test_unknown_symbol_becomes_a_generated_box():
    ir = load("unknown_symbol")
    box = sole(elements_of(ir, NodeComponent))
    assert box.symbol == "subckt:universalopamp2"
    assert box.symbol in ir.symbols
    assert ir.symbols[box.symbol].base is None
    assert set(box.pins) == {"1", "2", "3"}
    # The pins are the wire ends the drawing left unexplained.
    assert set(box.pins.values()) == {(6, 11), (11, 10), (6, 9)}


def test_generated_symbols_are_written_into_the_document():
    for name in CORPUS_NAMES:
        ir = load(name)
        for element in elements_of(ir, NodeComponent):
            assert lookup_symbol(element.symbol, ir.symbols) is not None


# --- 3.2 document metadata ---------------------------------------------------


def test_meta_records_provenance_without_a_timestamp():
    ir = import_asc(MINIMAL, source="thing.asc")
    assert ir.meta.source_netlist == "thing.asc"
    assert ir.meta.generator == GENERATOR
    assert ir.meta.title is None


def test_load_asc_reads_bytes_and_names_the_source():
    ir = load_asc(CORPUS / "utf16_divider.asc")
    assert ir.meta.source_netlist == "utf16_divider.asc"
    assert len(elements_of(ir, PathComponent)) == 3


def test_style_block_is_explicit():
    style = import_asc(MINIMAL).style
    assert style is not None
    assert style.resistor_variant == "european"


# --- 3.3 corpus coverage -----------------------------------------------------


def test_corpus_has_at_least_eight_files():
    assert len(CORPUS_NAMES) >= 8


def test_corpus_covers_every_symbol_in_the_table():
    used = {
        symbol.base_name
        for name in CORPUS_NAMES
        for symbol in parse_asc(
            decode_asc((CORPUS / f"{name}.asc").read_bytes())
        ).symbols
    }
    assert set(SYMBOL_TABLE) <= used


def test_corpus_exercises_every_orientation():
    used = {
        symbol.orientation.text
        for name in CORPUS_NAMES
        for symbol in parse_asc(
            decode_asc((CORPUS / f"{name}.asc").read_bytes())
        ).symbols
    }
    assert used == set(ORIENTATION_TO_IR)


def test_corpus_exercises_ports_and_generated_boxes():
    assert any(elements_of(load(name), Port) for name in CORPUS_NAMES)
    assert any(load(name).symbols for name in CORPUS_NAMES)


# --- 3.3 the corpus itself must be well-formed -------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_file_imports_without_raising(name: str):
    assert load(name).sheets[0].elements


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_file_warnings_are_exactly_as_expected(name: str):
    warnings: list[str] = []
    load_asc(CORPUS / f"{name}.asc", warnings=warnings)
    assert warnings == list(EXPECTED_WARNINGS.get(name, ()))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_file_validates_without_findings(name: str):
    findings = validate(load(name))
    assert not findings, "\n".join(format_finding(finding) for finding in findings)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_file_has_no_validation_errors(name: str):
    findings = validate(load(name))
    assert not [f for f in findings if f.severity is Severity.ERROR]


# --- 3.3 golden output -------------------------------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_ir_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"asc/{name}.schematic.json", schematic_ir.dumps(load(name)))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_snippet_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"asc/{name}.tex", emit_snippet(load(name)))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_standalone_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"asc/{name}.standalone.tex", emit_standalone(load(name)))


# --- 3.3 determinism and round-tripping --------------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_import_is_deterministic(name: str):
    first, second = load(name), load(name)
    assert schematic_ir.dumps(first) == schematic_ir.dumps(second)
    assert emit_snippet(first) == emit_snippet(second)
    assert emit_standalone(first) == emit_standalone(second)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_import_warnings_are_deterministic(name: str):
    first: list[str] = []
    second: list[str] = []
    load_asc(CORPUS / f"{name}.asc", first)
    load_asc(CORPUS / f"{name}.asc", second)
    assert first == second


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_json_round_trip_is_lossless(name: str):
    ir = load(name)
    text = schematic_ir.dumps(ir)
    reloaded = schematic_ir.loads(text)
    assert schematic_ir.dumps(reloaded) == text
    assert emit_snippet(reloaded) == emit_snippet(ir)
    assert emit_standalone(reloaded) == emit_standalone(ir)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_round_tripped_document_still_validates(name: str):
    reloaded = schematic_ir.loads(schematic_ir.dumps(load(name)))
    assert not validate(reloaded)
