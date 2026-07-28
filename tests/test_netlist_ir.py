"""Unit tests for the Netlist IR (roadmap §1.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spice2tikz._serde import IRError
from spice2tikz.netlist_ir import (
    Component,
    Kind,
    ModelDef,
    Net,
    NetlistIR,
    NetlistMeta,
    Scope,
    SubcktDef,
    dumps,
    generic_pin_names,
    load,
    loads,
    optional_pins,
    pin_order,
    required_pins,
)
from spice2tikz.quantity import Quantity, parse_quantity

CORPUS = Path(__file__).parent / "corpus"
RC_LOWPASS = CORPUS / "rc_lowpass.netlist.json"


def rc_lowpass() -> NetlistIR:
    """Build the spec §5 worked example in code."""
    return NetlistIR(
        meta=NetlistMeta(title="RC low-pass", dialect="ngspice"),
        circuit=Scope(
            components=[
                Component(
                    id="V1",
                    kind=Kind.VSOURCE,
                    pins={"p": "in", "n": "0"},
                    value=Quantity(raw="AC 1"),
                    params={"ac": Quantity(raw="1", value=1.0, unit="V")},
                    raw="V1 in 0 AC 1",
                ),
                Component(
                    id="R1",
                    kind=Kind.RESISTOR,
                    pins={"a": "in", "b": "out"},
                    value=parse_quantity("10k", "ohm"),
                    raw="R1 in out 10k",
                ),
                Component(
                    id="C1",
                    kind=Kind.CAPACITOR,
                    pins={"a": "out", "b": "0"},
                    value=parse_quantity("100n", "F"),
                    raw="C1 out 0 100n",
                ),
            ],
            nets={
                "in": Net(name="in", net_class="signal"),
                "out": Net(name="out", net_class="signal"),
                "0": Net(name="0", net_class="ground"),
            },
        ),
    )


# --- taxonomy ---------------------------------------------------------------


def test_every_kind_has_a_pin_table():
    for kind in Kind:
        assert required_pins(kind) == tuple(required_pins(kind))


def test_pin_tables_match_the_spec():
    assert required_pins(Kind.RESISTOR) == ("a", "b")
    assert required_pins(Kind.DIODE) == ("a", "k")
    assert required_pins(Kind.VSOURCE) == ("p", "n")
    assert required_pins(Kind.NMOS) == ("d", "g", "s", "b")
    assert required_pins(Kind.NJFET) == ("d", "g", "s")
    assert required_pins(Kind.VCVS) == ("p", "n", "cp", "cn")
    assert required_pins(Kind.CCVS) == ("p", "n")
    assert required_pins(Kind.TLINE) == ("p1a", "p1b", "p2a", "p2b")
    assert required_pins(Kind.SUBCIRCUIT) == ()
    assert required_pins(Kind.GENERIC) == ()


def test_bjt_substrate_pin_is_optional():
    assert optional_pins(Kind.BJT_NPN) == ("s",)
    assert pin_order(Kind.BJT_PNP) == ("c", "b", "e", "s")
    assert optional_pins(Kind.RESISTOR) == ()


def test_generic_pin_names():
    assert generic_pin_names(3) == ("1", "2", "3")
    assert generic_pin_names(0) == ()


def test_kind_stringifies_as_its_value():
    assert str(Kind.BJT_NPN) == "bjt_npn"
    assert f"{Kind.NMOS}" == "nmos"
    assert Kind.NMOS == "nmos"


def test_component_accepts_kind_as_text():
    component = Component(id="R1", kind="resistor")  # type: ignore[arg-type]
    assert component.kind is Kind.RESISTOR


# --- construction and serialisation ----------------------------------------


def test_construction_of_the_worked_example():
    ir = rc_lowpass()
    assert [component.id for component in ir.circuit.components] == ["V1", "R1", "C1"]
    assert ir.circuit.components[1].value == Quantity("10k", 10000.0, "ohm")
    assert ir.circuit.nets["0"].net_class == "ground"
    assert ir.subcircuits == {}
    assert ir.models == {}


def test_to_json_field_order():
    data = rc_lowpass().to_json()
    assert list(data) == ["ir", "version", "meta", "circuit", "subcircuits"]
    assert list(data["circuit"]) == ["components", "nets"]
    assert list(data["circuit"]["components"][0]) == [
        "id",
        "kind",
        "pins",
        "value",
        "params",
        "raw",
    ]
    assert list(data["circuit"]["nets"]["0"]) == ["name", "class"]


def test_optional_fields_are_omitted_never_null():
    data = rc_lowpass().to_json()
    resistor = data["circuit"]["components"][1]
    assert "model" not in resistor
    assert "params" not in resistor
    assert "models" not in data
    assert None not in _nested_values(data)


def test_json_round_trip_equality():
    ir = rc_lowpass()
    assert NetlistIR.from_json(ir.to_json()) == ir
    assert loads(dumps(ir)) == ir


def test_round_trip_of_all_features():
    ir = NetlistIR(
        meta=NetlistMeta(
            title="everything", source="x.sp", generator="spice2tikz", dialect="ngspice"
        ),
        circuit=Scope(
            components=[
                Component(
                    id="X1",
                    kind=Kind.SUBCIRCUIT,
                    pins={"in": "a", "out": "b"},
                    subckt="amp",
                    params={"gain": parse_quantity("10")},
                    raw="X1 a b amp gain=10",
                ),
                Component(
                    id="Q1",
                    kind=Kind.BJT_NPN,
                    pins={"c": "b", "b": "a", "e": "0"},
                    model="bc547",
                    raw="Q1 b a 0 bc547",
                ),
                Component(
                    id="H1",
                    kind=Kind.CCVS,
                    pins={"p": "b", "n": "0"},
                    control="V1",
                    value=parse_quantity("2"),
                    raw="H1 b 0 V1 2",
                ),
                Component(
                    id="U1",
                    kind=Kind.GENERIC,
                    pins={"1": "a", "2": "b", "3": "0"},
                    raw="U1 a b 0 mystery",
                ),
            ],
            nets={
                "a": Net(name="a", net_class="signal"),
                "b": Net(name="b", net_class="signal"),
                "0": Net(name="0", net_class="ground"),
                "vcc": Net(
                    name="vcc",
                    net_class="supply",
                    supply_voltage=parse_quantity("5", "V"),
                ),
            },
        ),
        subcircuits={
            "amp": SubcktDef(
                ports=["in", "out"],
                params={"gain": parse_quantity("1")},
                components=[
                    Component(
                        id="R1",
                        kind=Kind.RESISTOR,
                        pins={"a": "in", "b": "out"},
                        value=parse_quantity("1k", "ohm"),
                        raw="R1 in out 1k",
                    )
                ],
                nets={
                    "in": Net(name="in", net_class="signal"),
                    "out": Net(name="out", net_class="signal"),
                },
            )
        },
        models={
            "bc547": ModelDef(
                type="npn",
                params={"bf": parse_quantity("300")},
                raw=".model bc547 npn (bf=300)",
            )
        },
    )
    assert loads(dumps(ir)) == ir
    data = ir.to_json()
    assert list(data["subcircuits"]["amp"]) == [
        "ports",
        "params",
        "components",
        "nets",
    ]
    assert list(data["models"]["bc547"]) == ["type", "params", "raw"]


def test_scopes_helper():
    ir = NetlistIR(subcircuits={"amp": SubcktDef(ports=["in"])})
    assert [name for name, _ in ir.scopes()] == ["circuit", "subcircuits.amp"]


# --- corpus file ------------------------------------------------------------


def test_corpus_file_matches_the_worked_example():
    assert load(RC_LOWPASS) == rc_lowpass()


def test_corpus_file_redumps_byte_identically():
    original = RC_LOWPASS.read_bytes()
    assert dumps(load(RC_LOWPASS)).encode("utf-8") == original


def test_corpus_file_matches_the_spec_json():
    # The spec §5 listing, verbatim apart from formatting.
    expected = json.loads("""
    { "ir": "netlist", "version": "1.0",
      "meta": { "title": "RC low-pass", "dialect": "ngspice" },
      "circuit": {
        "components": [
          { "id": "V1", "kind": "vsource", "pins": { "p": "in", "n": "0" },
            "value": { "raw": "AC 1" },
            "params": { "ac": { "raw": "1", "value": 1.0, "unit": "V" } },
            "raw": "V1 in 0 AC 1" },
          { "id": "R1", "kind": "resistor", "pins": { "a": "in", "b": "out" },
            "value": { "raw": "10k", "value": 10000.0, "unit": "ohm" },
            "raw": "R1 in out 10k" },
          { "id": "C1", "kind": "capacitor", "pins": { "a": "out", "b": "0" },
            "value": { "raw": "100n", "value": 1e-07, "unit": "F" },
            "raw": "C1 out 0 100n" } ],
        "nets": { "in":  { "name": "in",  "class": "signal" },
                  "out": { "name": "out", "class": "signal" },
                  "0":   { "name": "0",   "class": "ground" } } },
      "subcircuits": {} }
    """)
    assert json.loads(RC_LOWPASS.read_text(encoding="utf-8")) == expected


def test_determinism_of_serialisation():
    ir = load(RC_LOWPASS)
    assert dumps(ir) == dumps(ir)
    assert dumps(loads(dumps(ir))) == dumps(ir)


# --- loader diagnostics ----------------------------------------------------


def test_unknown_fields_are_warned_about_and_ignored():
    data = rc_lowpass().to_json()
    data["extra"] = 1
    data["circuit"]["components"][0]["extra"] = 2
    warnings: list[str] = []
    ir = NetlistIR.from_json(data, warnings)
    assert ir == rc_lowpass()
    assert warnings == [
        "<root>: unknown field 'extra' ignored",
        "circuit.components[0]: unknown field 'extra' ignored",
    ]


def test_newer_minor_version_warns():
    data = rc_lowpass().to_json()
    data["version"] = "1.9"
    warnings: list[str] = []
    NetlistIR.from_json(data, warnings)
    assert warnings and "1.9" in warnings[0]


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"ir": "schematic"}, "expected 'netlist'"),
        ({"version": "2.0"}, "unsupported major version"),
        ({"version": "one"}, "major.minor"),
        ({"circuit": None}, "null"),
        ({"circuit": []}, "expected an object"),
        ({"meta": {"title": 7}}, "expected a string"),
    ],
)
def test_bad_documents_raise_ir_error(mutation: dict[str, object], fragment: str):
    data = rc_lowpass().to_json()
    data.update(mutation)
    with pytest.raises(IRError, match=fragment):
        NetlistIR.from_json(data)


def test_unknown_kind_raises_ir_error():
    data = rc_lowpass().to_json()
    data["circuit"]["components"][0]["kind"] = "flux_capacitor"
    with pytest.raises(IRError, match="unknown kind"):
        NetlistIR.from_json(data)


def test_missing_required_field_raises_ir_error():
    data = rc_lowpass().to_json()
    del data["circuit"]["components"][0]["raw"]
    with pytest.raises(IRError, match="missing required field 'raw'"):
        NetlistIR.from_json(data)


def test_invalid_json_text_raises_ir_error():
    with pytest.raises(IRError, match="invalid JSON"):
        loads("{not json")


def test_dump_writes_canonical_text(tmp_path: Path):
    from spice2tikz.netlist_ir import dump

    target = tmp_path / "out.json"
    dump(rc_lowpass(), target)
    assert target.read_bytes() == RC_LOWPASS.read_bytes()


def _nested_values(data: object) -> list[object]:
    if isinstance(data, dict):
        return [value for item in data.values() for value in _nested_values(item)]
    if isinstance(data, list):
        return [value for item in data for value in _nested_values(item)]
    return [data]
