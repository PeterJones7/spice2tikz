"""Tests for the SPICE netlist parser (roadmap §4.1, §4.2, §4.3).

The file is in three parts, matching the roadmap subsections: line assembly,
card-by-card mapping onto the Netlist IR, and the ``tests/corpus/spice``
golden corpus.  Regenerate the goldens with ``pytest --update-golden``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from spice2tikz import netlist_ir
from spice2tikz._serde import IRError
from spice2tikz.netlist_ir import Component, Kind, NetlistIR
from spice2tikz.spice_parser import (
    GENERATOR,
    SpiceLine,
    assemble_lines,
    load_spice,
    parse_spice,
)
from spice2tikz.validate import Severity, format_finding, validate

CORPUS = Path(__file__).parent / "corpus" / "spice"
SUFFIX = ".sp"
CORPUS_FILES = sorted(CORPUS.glob(f"*{SUFFIX}"))
CORPUS_NAMES = [path.name[: -len(SUFFIX)] for path in CORPUS_FILES]

REQUIRED_CIRCUITS = frozenset(
    {
        "rc_lowpass",
        "voltage_divider",
        "rlc_series",
        "bridge_rectifier",
        "common_source_amp",
        "subckt_rc_stages",
        "controlled_sources",
        "bjt_amp",
        "messy_realworld",
    }
)
"""Circuits the roadmap §4.3 names explicitly; extras are welcome."""

MINIMUM_CORPUS_SIZE = 10
"""``.sp`` files roadmap §4.3 asks for."""

EXPECTED_WARNINGS: dict[str, tuple[str, ...]] = {
    "messy_realworld": (
        "messy_realworld.sp:18:1: 2 line(s) after '.end' ignored",
        "messy_realworld.sp:13:1: unknown card letter 'Z'; mapped to a "
        "generic component",
        "messy_realworld.sp:14:1: unknown dot command '.wrdata' ignored",
    ),
}
"""Warnings each corpus deck is expected to raise; absent means none."""

RC_LOWPASS_DECK = "* RC low-pass\nV1 in 0 AC 1\nR1 in out 10k\nC1 out 0 100n\n.end\n"
"""The SPICE input of ``docs/SPEC_IR.md`` §5, which is normative."""


def deck(*cards: str) -> str:
    """Return a complete deck: a title line, *cards*, then ``.end``."""
    return "\n".join(("* test deck", *cards, ".end", ""))


def parse(*cards: str, warnings: list[str] | None = None) -> NetlistIR:
    """Parse a deck built from *cards*."""
    return parse_spice(deck(*cards), warnings=warnings)


def one(ir: NetlistIR, refdes: str) -> Component:
    """Return the top-level component named *refdes*."""
    return next(item for item in ir.circuit.components if item.id == refdes)


def cards(text: str, warnings: list[str] | None = None) -> list[SpiceLine]:
    """Assemble *text* and drop the title line."""
    return [line for line in assemble_lines(text, warnings) if not line.is_title]


def texts(text: str, warnings: list[str] | None = None) -> list[str]:
    """Return the assembled card texts of *text*."""
    return [line.text for line in cards(text, warnings)]


# --- §4.1 stage 1: line assembly --------------------------------------------


def test_first_line_is_the_title():
    lines = assemble_lines("* RC low-pass\nR1 in out 10k\n")
    assert lines[0] == SpiceLine(text="RC low-pass", number=1, is_title=True)
    assert [line.text for line in lines[1:]] == ["R1 in out 10k"]


def test_first_line_is_the_title_even_when_it_looks_like_a_card():
    lines = assemble_lines("R1 in out 10k\nR2 out 0 1k\n")
    assert lines[0].is_title
    assert lines[0].text == "R1 in out 10k"
    assert [line.text for line in lines[1:]] == ["R2 out 0 1k"]


def test_a_leading_title_card_is_not_consumed_as_the_title_line():
    lines = assemble_lines(".TITLE My deck\nR1 in out 10k\n")
    assert not any(line.is_title for line in lines)
    assert [line.text for line in lines] == [".TITLE My deck", "R1 in out 10k"]


def test_title_card_sets_the_meta_title():
    ir = parse_spice(".title My deck\nR1 in out 10k\n.end\n")
    assert ir.meta.title == "My deck"


def test_a_later_title_card_overrides_the_first_line():
    ir = parse_spice("first line\n.title real title\nR1 in out 10k\n.end\n")
    assert ir.meta.title == "real title"


def test_an_empty_title_line_leaves_the_title_unset():
    ir = parse_spice("\nR1 in out 10k\n.end\n")
    assert ir.meta.title is None


def test_continuation_lines_are_appended():
    assert texts("t\nR1 in out\n+ 10k\n") == ["R1 in out 10k"]


def test_a_continuation_of_a_continuation_is_appended():
    assert texts("t\nV1 in 0 DC 0\n+ AC 1\n+ SIN(0 1 1k)\n") == [
        "V1 in 0 DC 0 AC 1 SIN(0 1 1k)"
    ]


def test_a_comment_between_a_card_and_its_continuation_is_transparent():
    text = "t\nR1 in out\n* explanatory comment\n\n+ 10k\n"
    assert texts(text) == ["R1 in out 10k"]


def test_a_continuation_keeps_the_line_number_of_its_first_line():
    line = cards("t\nR1 in out\n+ 10k\n")[0]
    assert (line.number, line.column) == (2, 1)


def test_a_continuation_with_nothing_to_continue_is_a_parse_error():
    with pytest.raises(IRError) as error:
        assemble_lines("t\n  + 10k\n")
    assert "<input>:2:3" in str(error.value)


def test_a_continuation_cannot_continue_the_title():
    with pytest.raises(IRError):
        assemble_lines("* title\n+ more title\n")


def test_star_comment_lines_are_dropped_even_when_indented():
    assert texts("t\n* one\n   * two\nR1 in out 10k\n") == ["R1 in out 10k"]


def test_semicolon_starts_an_inline_comment():
    assert texts("t\nR1 in out 10k ; the load resistor\n") == ["R1 in out 10k"]


def test_dollar_after_whitespace_starts_an_inline_comment():
    assert texts("t\nR1 in out 10k $ the load resistor\n") == ["R1 in out 10k"]


def test_dollar_inside_a_token_is_not_a_comment():
    assert texts("t\nR1 in out 10k$suffix\n") == ["R1 in out 10k$suffix"]


def test_blank_lines_are_dropped():
    assert texts("t\n\nR1 in out 10k\n   \n") == ["R1 in out 10k"]


def test_end_terminates_the_deck_and_warns_about_the_remainder():
    warnings: list[str] = []
    assert texts("t\nR1 in out 10k\n.end\nR2 out 0 1k\n", warnings) == ["R1 in out 10k"]
    assert warnings == ["<input>:3:1: 1 line(s) after '.end' ignored"]


def test_end_without_trailing_content_does_not_warn():
    warnings: list[str] = []
    texts("t\nR1 in out 10k\n.end\n\n", warnings)
    assert warnings == []


def test_crlf_line_endings_are_normalised():
    lines = cards("t\r\nR1 in out 10k\r\nR2 out 0 1k\r\n")
    assert [(line.text, line.number) for line in lines] == [
        ("R1 in out 10k", 2),
        ("R2 out 0 1k", 3),
    ]


def test_a_utf8_bom_is_stripped_from_the_title():
    lines = assemble_lines("﻿* RC low-pass\nR1 in out 10k\n")
    assert lines[0].text == "RC low-pass"


def test_card_text_keeps_its_original_case_and_spacing():
    assert texts("t\n  R1   IN  Out   10K\n") == ["R1   IN  Out   10K"]


def test_the_column_of_an_indented_card_is_reported():
    assert cards("t\n    R1 in out 10k\n")[0].column == 5


def test_a_dot_end_card_may_carry_trailing_text():
    warnings: list[str] = []
    assert texts("t\nR1 in out 10k\n.end of deck\n", warnings) == ["R1 in out 10k"]


# --- §4.2 stage 2: passive cards --------------------------------------------


@pytest.mark.parametrize(
    ("card", "kind", "unit", "value"),
    [
        ("R1 in out 10k", Kind.RESISTOR, "ohm", 10000.0),
        ("C1 in out 100n", Kind.CAPACITOR, "F", 1e-7),
        ("L1 in out 10m", Kind.INDUCTOR, "H", 0.01),
    ],
)
def test_passive_cards(card: str, kind: Kind, unit: str, value: float):
    component = one(parse(card), card.split()[0])
    assert component.kind is kind
    assert component.pins == {"a": "in", "b": "out"}
    assert component.value is not None
    assert (component.value.value, component.value.unit) == (value, unit)
    assert component.raw == card


def test_a_passive_value_may_be_given_as_a_keyword():
    component = one(parse("R1 in out R=4.7k"), "R1")
    assert component.value is not None
    assert component.value.value == 4700.0
    assert component.params == {}


def test_passive_keyword_parameters_become_params():
    component = one(parse("C1 in out 1u IC=2.5"), "C1")
    assert set(component.params) == {"ic"}
    assert component.params["ic"].value == 2.5


def test_an_unparseable_value_keeps_only_its_raw_text():
    component = one(parse("R1 in out {rload*2}"), "R1")
    assert component.value is not None
    assert component.value.raw == "{rload*2}"
    assert component.value.value is None


def test_diode_card():
    ir = parse("D1 anode cathode DMOD", ".model DMOD D (IS=1e-14)")
    component = one(ir, "D1")
    assert component.kind is Kind.DIODE
    assert component.pins == {"a": "anode", "k": "cathode"}
    assert component.model == "dmod"


def test_a_diode_area_factor_becomes_a_param():
    component = one(parse("D1 a k DMOD 2", ".model DMOD D"), "D1")
    assert component.params["area"].value == 2.0


# --- §4.2 stage 2: independent sources --------------------------------------


def test_a_bare_source_value_is_a_dc_value():
    component = one(parse("V1 in 0 5"), "V1")
    assert component.kind is Kind.VSOURCE
    assert component.pins == {"p": "in", "n": "0"}
    assert component.value is not None
    assert component.value.raw == "5"
    assert component.params["dc"].value == 5.0
    assert component.params["dc"].unit == "V"


def test_explicit_dc_specification():
    component = one(parse("V1 in 0 DC 5"), "V1")
    assert component.value is not None
    assert component.value.raw == "DC 5"
    assert component.value.value is None
    assert component.params["dc"].value == 5.0


def test_dc_may_be_written_with_an_equals_sign():
    assert one(parse("V1 in 0 dc=5"), "V1").params["dc"].value == 5.0


def test_ac_specification_matches_the_spec_example():
    component = one(parse("V1 in 0 AC 1"), "V1")
    assert component.value is not None
    assert component.value.to_json() == {"raw": "AC 1"}
    assert component.params["ac"].to_json() == {"raw": "1", "value": 1.0, "unit": "V"}


def test_ac_specification_with_a_phase():
    params = one(parse("V1 in 0 AC 2 30"), "V1").params
    assert params["ac"].value == 2.0
    assert params["ac_phase"].value == 30.0
    assert params["ac_phase"].unit is None


def test_sine_specification():
    params = one(parse("V1 in 0 SIN(0 1 1k 1m 0 90)"), "V1").params
    assert [(key, params[key].value) for key in params] == [
        ("sin_offset", 0.0),
        ("sin_amplitude", 1.0),
        ("sin_freq", 1000.0),
        ("sin_delay", 0.001),
        ("sin_theta", 0.0),
        ("sin_phase", 90.0),
    ]
    assert params["sin_freq"].unit == "Hz"
    assert params["sin_delay"].unit == "s"
    assert params["sin_amplitude"].unit == "V"


def test_sine_specification_without_parentheses():
    params = one(parse("V1 in 0 SIN 0 1 1k"), "V1").params
    assert params["sin_amplitude"].value == 1.0
    assert params["sin_freq"].value == 1000.0


def test_pulse_specification():
    params = one(parse("V1 in 0 PULSE(0 5 1n 2n 3n 4n 5n)"), "V1").params
    assert list(params) == [
        "pulse_initial",
        "pulse_pulsed",
        "pulse_delay",
        "pulse_rise",
        "pulse_fall",
        "pulse_width",
        "pulse_period",
    ]
    assert params["pulse_period"].value == 5e-9
    assert params["pulse_period"].unit == "s"
    assert params["pulse_pulsed"].unit == "V"


def test_pwl_specification():
    params = one(parse("I1 0 out PWL(0 0 1u 5m 2u 0)"), "I1").params
    assert list(params) == ["pwl_t1", "pwl_v1", "pwl_t2", "pwl_v2", "pwl_t3", "pwl_v3"]
    assert params["pwl_t2"].value == 1e-6
    assert params["pwl_v2"].value == 0.005
    assert params["pwl_v2"].unit == "A"


def test_exp_specification():
    params = one(parse("V1 in 0 EXP(0 5 1n 2n 3n 4n)"), "V1").params
    assert list(params) == [
        "exp_initial",
        "exp_pulsed",
        "exp_rise_delay",
        "exp_rise_tau",
        "exp_fall_delay",
        "exp_fall_tau",
    ]
    assert params["exp_fall_tau"].value == 4e-9


def test_combined_source_specification():
    component = one(parse("V1 in 0 DC 0 AC 1 SIN(0 1 1k)"), "V1")
    assert component.value is not None
    assert component.value.raw == "DC 0 AC 1 SIN(0 1 1k)"
    assert set(component.params) == {
        "dc",
        "ac",
        "sin_offset",
        "sin_amplitude",
        "sin_freq",
    }


def test_current_source_amplitudes_are_amperes():
    component = one(parse("I1 0 out DC 1m"), "I1")
    assert component.kind is Kind.ISOURCE
    assert component.pins == {"p": "0", "n": "out"}
    assert component.params["dc"].unit == "A"


# --- §4.2 stage 2: transistors and model polarity ---------------------------


@pytest.mark.parametrize(
    ("model_type", "kind"),
    [("NPN", Kind.BJT_NPN), ("PNP", Kind.BJT_PNP)],
)
def test_bjt_polarity_comes_from_the_model(model_type: str, kind: Kind):
    ir = parse("Q1 c b e QMOD", f".model QMOD {model_type} (BF=100)")
    component = one(ir, "Q1")
    assert component.kind is kind
    assert component.pins == {"c": "c", "b": "b", "e": "e"}
    assert component.model == "qmod"


def test_a_four_terminal_bjt_keeps_its_substrate_pin():
    ir = parse("Q1 nc nb ne ns QMOD", ".model QMOD NPN")
    assert one(ir, "Q1").pins == {"c": "nc", "b": "nb", "e": "ne", "s": "ns"}


def test_a_three_terminal_bjt_with_an_area_factor_is_not_four_terminal():
    ir = parse("Q1 nc nb ne QMOD 2", ".model QMOD PNP")
    component = one(ir, "Q1")
    assert component.pins == {"c": "nc", "b": "nb", "e": "ne"}
    assert component.params["area"].value == 2.0


def test_an_unknown_bjt_model_defaults_to_npn_with_a_warning():
    warnings: list[str] = []
    ir = parse("Q1 c b e MISSING", warnings=warnings)
    assert one(ir, "Q1").kind is Kind.BJT_NPN
    assert warnings == [
        "<input>:2:1: no '.model missing' in this deck; assuming bjt_npn"
    ]


def test_a_bjt_model_of_the_wrong_type_warns():
    warnings: list[str] = []
    parse("Q1 c b e DMOD", ".model DMOD D", warnings=warnings)
    assert warnings == [
        "<input>:2:1: model 'dmod' has type 'd', which is not a bjt type; "
        "assuming bjt_npn"
    ]


@pytest.mark.parametrize(
    ("model_type", "kind"),
    [("NMOS", Kind.NMOS), ("PMOS", Kind.PMOS)],
)
def test_mos_polarity_comes_from_the_model(model_type: str, kind: Kind):
    ir = parse("M1 d g s b MMOD L=1u W=10u", f".model MMOD {model_type}")
    component = one(ir, "M1")
    assert component.kind is kind
    assert component.pins == {"d": "d", "g": "g", "s": "s", "b": "b"}
    assert component.params["l"].value == 1e-6
    assert component.params["w"].value == 1e-5


@pytest.mark.parametrize(
    ("model_type", "kind"),
    [("NJF", Kind.NJFET), ("PJF", Kind.PJFET)],
)
def test_jfet_polarity_comes_from_the_model(model_type: str, kind: Kind):
    ir = parse("J1 d g s JMOD", f".model JMOD {model_type}")
    component = one(ir, "J1")
    assert component.kind is kind
    assert component.pins == {"d": "d", "g": "g", "s": "s"}


# --- §4.2 stage 2: controlled sources, switch, tline ------------------------


@pytest.mark.parametrize(
    ("card", "kind", "unit"),
    [("E1 p n cp cn 10", Kind.VCVS, None), ("G1 p n cp cn 1m", Kind.VCCS, None)],
)
def test_voltage_controlled_sources(card: str, kind: Kind, unit: str | None):
    component = one(parse(card), card.split()[0])
    assert component.kind is kind
    assert component.pins == {"p": "p", "n": "n", "cp": "cp", "cn": "cn"}
    assert component.value is not None
    assert component.value.unit == unit


def test_a_behavioural_e_card_degrades_to_generic():
    warnings: list[str] = []
    ir = parse("E1 out 0 VALUE={V(a)*2}", warnings=warnings)
    assert one(ir, "E1").kind is Kind.GENERIC
    assert "behavioural" in warnings[0]


@pytest.mark.parametrize(
    ("card", "kind", "unit"),
    [("H1 p n VS 50", Kind.CCVS, "ohm"), ("F1 p n VS 2", Kind.CCCS, None)],
)
def test_current_controlled_sources(card: str, kind: Kind, unit: str | None):
    ir = parse("VS s 0 DC 0", card)
    component = one(ir, card.split()[0])
    assert component.kind is kind
    assert component.pins == {"p": "p", "n": "n"}
    assert component.control == "VS"
    assert component.value is not None
    assert component.value.unit == unit


def test_a_control_reference_resolves_case_insensitively():
    ir = parse("VSense s 0 DC 0", "H1 p n vsense 50")
    assert one(ir, "H1").control == "VSense"


def test_an_unresolvable_control_reference_warns():
    warnings: list[str] = []
    parse("H1 p n VMISSING 50", warnings=warnings)
    assert warnings == [
        "<input>:2:1: controlling source 'VMISSING' is not defined in this scope"
    ]


def test_switch_card():
    ir = parse("S1 p n cp cn SWMOD", ".model SWMOD SW (RON=1)")
    component = one(ir, "S1")
    assert component.kind is Kind.SWITCH
    assert component.pins == {"p": "p", "n": "n", "cp": "cp", "cn": "cn"}
    assert component.model == "swmod"


def test_tline_card():
    component = one(parse("T1 a b c d Z0=50 TD=10n"), "T1")
    assert component.kind is Kind.TLINE
    assert component.pins == {"p1a": "a", "p1b": "b", "p2a": "c", "p2b": "d"}
    assert component.params["z0"].unit == "ohm"
    assert component.params["td"].value == 1e-8


# --- §4.2 stage 2: subcircuits ----------------------------------------------


def test_subckt_instance_pins_are_named_by_the_definition():
    ir = parse(
        ".subckt divider top mid bottom",
        "R1 top mid 1k",
        "R2 mid bottom 1k",
        ".ends",
        "X1 in out 0 divider",
    )
    component = one(ir, "X1")
    assert component.kind is Kind.SUBCIRCUIT
    assert component.subckt == "divider"
    assert component.pins == {"top": "in", "mid": "out", "bottom": "0"}


def test_a_subckt_may_be_defined_after_it_is_used():
    ir = parse(
        "X1 in out divider",
        ".subckt divider a b",
        "R1 a b 1k",
        ".ends divider",
    )
    assert one(ir, "X1").pins == {"a": "in", "b": "out"}


def test_an_unknown_subckt_falls_back_to_generic_pin_names():
    warnings: list[str] = []
    ir = parse("X1 in out missing", warnings=warnings)
    component = one(ir, "X1")
    assert component.pins == {"1": "in", "2": "out"}
    assert component.subckt == "missing"
    assert warnings == [
        "<input>:2:1: no '.subckt missing' in this deck; pins fall back to "
        "generic names"
    ]


def test_a_port_count_mismatch_falls_back_to_generic_pin_names():
    warnings: list[str] = []
    ir = parse(
        ".subckt pair a b", "R1 a b 1k", ".ends", "X1 x y z pair", warnings=warnings
    )
    assert one(ir, "X1").pins == {"1": "x", "2": "y", "3": "z"}
    assert "connects 3" in warnings[0]


def test_nested_subckt_definitions_are_hoisted_and_scoped():
    ir = parse(
        ".subckt outer a b",
        "R1 a mid 1k",
        ".subckt inner p q",
        "R2 p q 2k",
        ".ends inner",
        "Xi mid b inner",
        ".ends outer",
        "X1 in out outer",
    )
    assert list(ir.subcircuits) == ["outer", "inner"]
    outer = ir.subcircuits["outer"]
    assert [item.id for item in outer.components] == ["R1", "Xi"]
    assert [item.id for item in ir.subcircuits["inner"].components] == ["R2"]
    assert [item.id for item in ir.circuit.components] == ["X1"]


def test_subckt_ports_are_registered_as_nets_in_port_order():
    ir = parse(".subckt stage inp outp", "R1 inp outp 1k", ".ends")
    assert list(ir.subcircuits["stage"].nets) == ["inp", "outp"]


def test_subckt_default_parameters_are_recorded():
    ir = parse(".subckt stage a b params: gain=10", "R1 a b 1k", ".ends")
    assert ir.subcircuits["stage"].params["gain"].value == 10.0


def test_subckt_names_are_lowercased():
    ir = parse(".SUBCKT Stage A B", "R1 A B 1k", ".ENDS", "X1 in out STAGE")
    assert list(ir.subcircuits) == ["stage"]
    assert one(ir, "X1").subckt == "stage"


def test_ends_outside_a_subckt_warns():
    warnings: list[str] = []
    parse(".ends", warnings=warnings)
    assert warnings == ["<input>:2:1: '.ends' outside a '.subckt'; ignored"]


def test_an_unterminated_subckt_warns():
    warnings: list[str] = []
    parse(".subckt stage a b", "R1 a b 1k", warnings=warnings)
    assert warnings == ["<input>:3:1: unterminated '.subckt' closed at end of deck"]


# --- §4.2 stage 2: dot commands ---------------------------------------------


def test_model_cards_are_recorded():
    ir = parse(".model QMOD NPN (BF = 200 IS=1e-15)")
    model = ir.models["qmod"]
    assert model.type == "npn"
    assert model.params["bf"].value == 200.0
    assert model.params["is"].value == 1e-15
    assert model.raw == ".model QMOD NPN (BF = 200 IS=1e-15)"


def test_model_parameters_may_span_continuations_without_parentheses():
    ir = parse_spice("* t\n.model QMOD NPN BF=200\n+ IS=1e-15\n+ VAF = 100\n.end\n")
    assert set(ir.models["qmod"].params) == {"bf", "is", "vaf"}


def test_a_model_without_a_type_is_ignored_with_a_warning():
    warnings: list[str] = []
    ir = parse(".model LONELY", warnings=warnings)
    assert ir.models == {}
    assert warnings == ["<input>:2:1: '.model' needs a name and a type; card ignored"]


@pytest.mark.parametrize(
    "card",
    [
        ".tran 1u 1m",
        ".ac dec 10 1 100k",
        ".dc V1 0 5 0.1",
        ".op",
        ".print tran v(out)",
        ".probe",
        ".options savecurrents",
        ".param rload=1k",
        ".include devices.lib",
        ".lib models.lib nom",
        ".global vdd",
        ".ic v(out)=0",
    ],
)
def test_analysis_directives_are_ignored_quietly(card: str):
    warnings: list[str] = []
    parse(card, warnings=warnings)
    assert warnings == []


def test_an_unknown_dot_command_warns():
    warnings: list[str] = []
    parse(".wrdata out.csv v(out)", warnings=warnings)
    assert warnings == ["<input>:2:1: unknown dot command '.wrdata' ignored"]


def test_control_blocks_are_dropped():
    warnings: list[str] = []
    ir = parse(
        "R1 in out 1k",
        ".control",
        "run",
        "plot v(out)",
        ".endc",
        warnings=warnings,
    )
    assert [item.id for item in ir.circuit.components] == ["R1"]
    assert warnings == []


# --- §4.2 stage 2: unknown cards --------------------------------------------


def test_an_unknown_card_becomes_a_generic_component_with_a_warning():
    warnings: list[str] = []
    ir = parse("Zq1 a b c", warnings=warnings)
    component = one(ir, "Zq1")
    assert component.kind is Kind.GENERIC
    assert component.pins == {"1": "a", "2": "b", "3": "c"}
    assert warnings == [
        "<input>:2:1: unknown card letter 'Z'; mapped to a generic component"
    ]


def test_a_generic_cards_trailing_number_becomes_its_value():
    ir = parse("Yx a b 2.5")
    component = one(ir, "Yx")
    assert component.pins == {"1": "a", "2": "b"}
    assert component.value is not None
    assert component.value.value == 2.5


def test_a_generic_cards_trailing_model_name_becomes_its_model():
    ir = parse("Yx a b WEIRD", ".model WEIRD D")
    component = one(ir, "Yx")
    assert component.pins == {"1": "a", "2": "b"}
    assert component.model == "weird"


def test_a_card_with_too_few_nodes_degrades_to_generic():
    warnings: list[str] = []
    ir = parse("M1 d g", warnings=warnings)
    assert one(ir, "M1").kind is Kind.GENERIC
    assert "needs 4 node(s)" in warnings[0]


def test_parsing_never_raises_on_a_nonsense_deck():
    warnings: list[str] = []
    ir = parse("???", "Q", ".nonsense", "X1", warnings=warnings)
    assert warnings
    assert isinstance(ir, NetlistIR)


# --- §4.2 stage 2: names and nets -------------------------------------------


def test_node_names_are_lowercased_and_merged():
    ir = parse("R1 IN Out 1k", "R2 in OUT 2k")
    assert list(ir.circuit.nets) == ["in", "out"]
    assert one(ir, "R2").pins == {"a": "in", "b": "out"}


def test_a_refdes_keeps_the_spelling_it_was_written_with():
    ir = parse("rLoad in 0 1k")
    assert [item.id for item in ir.circuit.components] == ["rLoad"]


def test_nets_are_recorded_in_first_appearance_order():
    ir = parse("V1 in 0 AC 1", "R1 in out 10k", "C1 out 0 100n")
    assert list(ir.circuit.nets) == ["in", "0", "out"]


@pytest.mark.parametrize("name", ["0", "gnd", "GND", "gnd!"])
def test_ground_net_names(name: str):
    ir = parse(f"R1 a {name} 1k")
    assert ir.circuit.nets[name.lower()].net_class == "ground"


def test_a_dc_source_against_ground_makes_a_supply_net():
    ir = parse("VDD vdd 0 DC 5", "R1 vdd out 1k", "R2 out 0 1k")
    supply = ir.circuit.nets["vdd"]
    assert supply.net_class == "supply"
    assert supply.supply_voltage is not None
    assert supply.supply_voltage.value == 5.0
    assert supply.supply_voltage.unit == "V"
    assert ir.circuit.nets["out"].net_class == "signal"


def test_a_supply_on_the_negative_terminal_is_negated():
    ir = parse("VEE 0 vee DC 5", "R1 vee 0 1k")
    supply = ir.circuit.nets["vee"]
    assert supply.net_class == "supply"
    assert supply.supply_voltage is not None
    assert supply.supply_voltage.raw == "-5"
    assert supply.supply_voltage.value == -5.0


def test_a_zero_volt_source_is_an_ammeter_not_a_supply():
    ir = parse("VS sense 0 DC 0", "R1 sense out 1k")
    assert ir.circuit.nets["sense"].net_class == "signal"


def test_an_ac_source_does_not_make_a_supply():
    ir = parse("V1 in 0 DC 5 AC 1", "R1 in out 1k")
    assert ir.circuit.nets["in"].net_class == "signal"


def test_a_transient_source_does_not_make_a_supply():
    ir = parse("V1 in 0 DC 5 SIN(0 1 1k)", "R1 in out 1k")
    assert ir.circuit.nets["in"].net_class == "signal"


def test_a_floating_dc_source_does_not_make_a_supply():
    ir = parse("V1 a b DC 5", "R1 a 0 1k", "R2 b 0 1k")
    assert ir.circuit.nets["a"].net_class == "signal"
    assert ir.circuit.nets["b"].net_class == "signal"


def test_two_dc_sources_on_one_net_make_no_supply():
    ir = parse("V1 vdd 0 DC 5", "V2 vdd 0 DC 5", "R1 vdd 0 1k")
    assert ir.circuit.nets["vdd"].net_class == "signal"


def test_a_second_voltage_source_on_a_rail_blocks_supply_inference():
    ir = parse("VDD vdd 0 DC 5", "E1 vdd 0 a 0 2", "R1 a 0 1k")
    assert ir.circuit.nets["vdd"].net_class == "signal"


def test_a_current_source_on_a_rail_does_not_block_supply_inference():
    ir = parse("VDD vdd 0 DC 5", "IB vdd bias DC 50u", "R1 bias 0 10k")
    assert ir.circuit.nets["vdd"].net_class == "supply"
    assert ir.circuit.nets["bias"].net_class == "signal"


def test_nets_inside_a_subcircuit_are_classed_too():
    ir = parse(".subckt stage a b", "VDD vdd 0 DC 5", "R1 a b 1k", ".ends")
    nets = ir.subcircuits["stage"].nets
    assert nets["0"].net_class == "ground"
    assert nets["vdd"].net_class == "supply"


# --- public API --------------------------------------------------------------


def test_meta_records_the_dialect_and_the_deck_title():
    ir = parse_spice(RC_LOWPASS_DECK)
    assert ir.meta.dialect == "ngspice"
    assert ir.meta.title == "RC low-pass"
    assert ir.meta.source is None
    assert ir.meta.generator is None


def test_naming_a_source_records_provenance():
    ir = parse_spice(RC_LOWPASS_DECK, source="rc_lowpass.sp")
    assert ir.meta.source == "rc_lowpass.sp"
    assert ir.meta.generator == GENERATOR


def test_load_spice_reads_a_file_and_names_it(tmp_path: Path):
    path = tmp_path / "deck.sp"
    path.write_text(RC_LOWPASS_DECK, encoding="utf-8")
    ir = load_spice(path)
    assert ir.meta.source == "deck.sp"
    assert [item.id for item in ir.circuit.components] == ["V1", "R1", "C1"]


def test_load_spice_accepts_a_utf8_bom_and_crlf(tmp_path: Path):
    path = tmp_path / "deck.sp"
    path.write_bytes(b"\xef\xbb\xbf* Title\r\nR1 in out 10k\r\n.end\r\n")
    ir = load_spice(path)
    assert ir.meta.title == "Title"
    assert one(ir, "R1").raw == "R1 in out 10k"


def test_load_spice_falls_back_to_latin1(tmp_path: Path):
    path = tmp_path / "deck.sp"
    path.write_bytes(b"* Caf\xe9 filter\nR1 in out 10k\n.end\n")
    ir = load_spice(path)
    assert ir.meta.title == "Café filter"


def test_load_spice_reports_a_parse_error(tmp_path: Path):
    path = tmp_path / "deck.sp"
    path.write_text("* Title\n+ orphan\n", encoding="utf-8")
    with pytest.raises(IRError) as error:
        load_spice(path)
    assert "deck.sp:2:1" in str(error.value)


# --- the SPEC_IR §5 normative example ---------------------------------------


def test_rc_lowpass_corpus_file_is_the_spec_input_verbatim():
    assert (CORPUS / "rc_lowpass.sp").read_text(encoding="utf-8") == RC_LOWPASS_DECK


def test_rc_lowpass_matches_the_normative_netlist_ir():
    # The hand-written corpus file lists its nets as in/out/0 while the parser
    # emits them in first-appearance order (in/0/out).  The documents are
    # otherwise identical, and JSON object key order carries no meaning here,
    # so dict equality is the right comparison; see docs/_notes_section4.md.
    normative = json.loads(
        (Path(__file__).parent / "corpus" / "rc_lowpass.netlist.json").read_text(
            encoding="utf-8"
        )
    )
    assert parse_spice(RC_LOWPASS_DECK).to_json() == normative


def test_rc_lowpass_components_match_the_spec_listing():
    ir = parse_spice(RC_LOWPASS_DECK)
    assert [(item.id, str(item.kind)) for item in ir.circuit.components] == [
        ("V1", "vsource"),
        ("R1", "resistor"),
        ("C1", "capacitor"),
    ]
    assert one(ir, "V1").value is not None
    assert one(ir, "V1").value.to_json() == {"raw": "AC 1"}  # type: ignore[union-attr]
    assert one(ir, "V1").params["ac"].to_json() == {
        "raw": "1",
        "value": 1.0,
        "unit": "V",
    }
    assert one(ir, "R1").value.to_json() == {  # type: ignore[union-attr]
        "raw": "10k",
        "value": 10000.0,
        "unit": "ohm",
    }
    assert one(ir, "C1").value.to_json() == {  # type: ignore[union-attr]
        "raw": "100n",
        "value": 1e-07,
        "unit": "F",
    }
    assert {name: net.net_class for name, net in ir.circuit.nets.items()} == {
        "in": "signal",
        "out": "signal",
        "0": "ground",
    }


# --- §4.3 corpus and goldens -------------------------------------------------


def load(name: str) -> NetlistIR:
    """Parse the corpus deck *name*, asserting its expected warning set."""
    warnings: list[str] = []
    ir = load_spice(CORPUS / f"{name}{SUFFIX}", warnings)
    assert tuple(warnings) == EXPECTED_WARNINGS.get(name, ())
    return ir


def test_corpus_contains_the_required_circuits():
    assert set(CORPUS_NAMES) >= REQUIRED_CIRCUITS


def test_corpus_has_at_least_ten_decks():
    assert len(CORPUS_NAMES) >= MINIMUM_CORPUS_SIZE


def test_corpus_exercises_every_kind_of_the_taxonomy():
    found = {
        item.kind
        for name in CORPUS_NAMES
        for _, scope in load(name).scopes()
        for item in scope.components
    }
    assert found == set(Kind)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_deck_parses(name: str):
    assert isinstance(load(name), NetlistIR)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_deck_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"spice/{name}.netlist.json", netlist_ir.dumps(load(name)))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_deck_validates_without_errors(name: str):
    findings = validate(load(name))
    errors = [item for item in findings if item.severity is Severity.ERROR]
    assert not errors, "\n".join(format_finding(item) for item in errors)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_deck_validates_without_findings_at_all(name: str):
    findings = validate(load(name))
    assert not findings, "\n".join(format_finding(item) for item in findings)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_parsing_is_deterministic(name: str):
    assert netlist_ir.dumps(load(name)) == netlist_ir.dumps(load(name))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_json_round_trip_is_lossless(name: str):
    ir = load(name)
    text = netlist_ir.dumps(ir)
    reloaded = netlist_ir.loads(text)
    assert reloaded == ir
    assert netlist_ir.dumps(reloaded) == text


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_golden_files_end_with_exactly_one_newline(name: str):
    path = Path(__file__).parent / "golden" / "spice" / f"{name}.netlist.json"
    if not path.exists():  # first --update-golden run
        return
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
