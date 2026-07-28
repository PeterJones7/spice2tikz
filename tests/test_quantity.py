"""Unit tests for SPICE value parsing (roadmap §1.1)."""

from __future__ import annotations

import pytest

from spice2tikz._serde import IRError
from spice2tikz.quantity import Quantity, canonical_unit, parse_quantity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Plain numbers.
        ("0", 0.0),
        ("1", 1.0),
        ("-1", -1.0),
        ("+2.5", 2.5),
        (".5", 0.5),
        ("1.", 1.0),
        ("1000", 1000.0),
        # Exponents.
        ("1e3", 1000.0),
        ("1E3", 1000.0),
        ("1.0e3", 1000.0),
        ("-2.5e-3", -0.0025),
        ("3.3e+2", 330.0),
        # Scale suffixes, lowercase.
        ("1f", 1e-15),
        ("1p", 1e-12),
        ("1n", 1e-9),
        ("1u", 1e-6),
        ("1µ", 1e-6),  # MICRO SIGN
        ("1μ", 1e-6),  # GREEK SMALL LETTER MU
        ("1m", 1e-3),
        ("1k", 1e3),
        ("1meg", 1e6),
        ("1g", 1e9),
        ("1t", 1e12),
        # Scale suffixes, uppercase and mixed case.
        ("1F", 1e-15),
        ("1P", 1e-12),
        ("1N", 1e-9),
        ("1U", 1e-6),
        ("1K", 1e3),
        ("1MEG", 1e6),
        ("1Meg", 1e6),
        ("1mEg", 1e6),
        ("1G", 1e9),
        ("1T", 1e12),
        # Suffix plus ignored unit text.
        ("10k", 1e4),
        ("10kohm", 1e4),
        ("10kOhm", 1e4),
        ("100n", 1e-7),
        ("100nF", 1e-7),
        ("4.7uF", 4.7e-6),
        ("2.2mH", 2.2e-3),
        ("1mA", 1e-3),
        ("1megohm", 1e6),
        # Whitespace around the value is tolerated.
        ("  10k  ", 1e4),
    ],
)
def test_parse_value(text: str, expected: float):
    quantity = parse_quantity(text)
    assert quantity.raw == text
    assert quantity.value == pytest.approx(expected)


def test_volt_suffix_is_a_unit_not_a_scale():
    # "V" is not a SPICE scale suffix, so 5V is five volts, not femtovolts.
    quantity = parse_quantity("5V")
    assert quantity.value == 5.0
    assert quantity.unit == "V"


def test_meg_versus_milli_trap():
    assert parse_quantity("1m").value == 1e-3
    assert parse_quantity("1M").value == 1e-3
    assert parse_quantity("1meg").value == 1e6
    assert parse_quantity("1MEG").value == 1e6
    assert parse_quantity("1M").value != parse_quantity("1MEG").value


def test_farad_letter_is_femto_per_spice_rules():
    # SPICE reads the letter immediately after the number as a scale factor,
    # so a capacitor written "1F" is one femtofarad, not one farad.
    assert parse_quantity("1F", "F").value == 1e-15
    assert parse_quantity("1F", "F").unit == "F"


def test_exact_decimal_scaling():
    # Scaling goes through the float literal, so no binary rounding creeps in.
    assert parse_quantity("100n").value == 1e-07
    assert parse_quantity("10k").value == 10000.0
    assert parse_quantity("470p").value == 470e-12


@pytest.mark.parametrize(
    ("text", "expected_unit"),
    [
        ("10kohm", "ohm"),
        ("10kohms", "ohm"),
        ("10kΩ", "ohm"),  # OHM SIGN
        ("10kΩ", "ohm"),  # GREEK CAPITAL LETTER OMEGA
        ("100nF", "F"),
        ("100nfarad", "F"),
        ("1mH", "H"),
        ("1mhenry", "H"),
        ("5volts", "V"),
        ("1mA", "A"),
        ("1amps", "A"),
        ("1ms", "s"),
        ("1kHz", "Hz"),
        ("1khertz", "Hz"),
        ("1kW", "W"),
    ],
)
def test_unit_canonicalisation_from_text(text: str, expected_unit: str):
    assert parse_quantity(text).unit == expected_unit


def test_default_unit_is_used_when_text_has_none():
    assert parse_quantity("10k", "ohm").unit == "ohm"
    assert parse_quantity("100n", "F").unit == "F"
    assert parse_quantity("1e3").unit is None


def test_text_unit_overrides_default_unit():
    assert parse_quantity("1kHz", "ohm").unit == "Hz"


def test_unrecognised_unit_text_is_ignored():
    quantity = parse_quantity("1mil", "m")
    assert quantity.value == 1e-3
    assert quantity.unit == "m"


def test_exponent_excludes_a_scale_suffix():
    # A number may carry an exponent or a suffix, never both: the trailing
    # letter is treated as unit text.
    quantity = parse_quantity("3.3e-6F")
    assert quantity.value == pytest.approx(3.3e-6)
    assert quantity.unit == "F"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "AC 1",
        "DC 5 AC 1",
        "{R*2}",
        "1k*2",
        "1k 2",
        "1k5",
        "pulse(0 1 0 1n 1n 1u 2u)",
        "abc",
        "-",
        ".",
        "e3",
        "TABLE",
    ],
)
def test_unparseable_text_passes_through(text: str):
    quantity = parse_quantity(text, "ohm")
    assert quantity == Quantity(raw=text)
    assert quantity.value is None
    assert quantity.unit is None
    assert not quantity.parsed


def test_canonical_unit_helper():
    assert canonical_unit("Ohms") == "ohm"
    assert canonical_unit(" F ") == "F"
    assert canonical_unit("bananas") is None


def test_parsed_flag():
    assert parse_quantity("1k").parsed
    assert not parse_quantity("{x}").parsed


def test_quantity_is_frozen():
    quantity = parse_quantity("1k", "ohm")
    with pytest.raises(AttributeError):
        quantity.value = 2.0  # type: ignore[misc]


def test_to_json_omits_absent_fields():
    assert Quantity(raw="AC 1").to_json() == {"raw": "AC 1"}
    assert Quantity(raw="10k", value=10000.0, unit="ohm").to_json() == {
        "raw": "10k",
        "value": 10000.0,
        "unit": "ohm",
    }


def test_json_round_trip():
    for text in ("10k", "AC 1", "100nF", "{expr}"):
        original = parse_quantity(text, "ohm")
        assert Quantity.from_json(original.to_json(), "q") == original


def test_from_json_warns_about_unknown_fields():
    warnings: list[str] = []
    quantity = Quantity.from_json({"raw": "1k", "junk": 1}, "q", warnings)
    assert quantity == Quantity(raw="1k")
    assert warnings == ["q: unknown field 'junk' ignored"]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"value": 1.0},
        {"raw": 10},
        {"raw": "1k", "value": "big"},
        {"raw": "1k", "value": True},
        {"raw": "1k", "unit": 5},
        [],
        "10k",
    ],
)
def test_from_json_rejects_bad_input(data: object):
    with pytest.raises(IRError):
        Quantity.from_json(data, "q")
