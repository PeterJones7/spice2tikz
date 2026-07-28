"""Unit tests for the Schematic IR (roadmap §1.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spice2tikz._serde import IRError
from spice2tikz.netlist_ir import Kind
from spice2tikz.schematic_ir import (
    DEFAULT_GRID_PITCH,
    Grid,
    Junction,
    Label,
    LabelSpec,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    SchematicIR,
    SchematicMeta,
    Sheet,
    StyleDefaults,
    StyleOverride,
    Wire,
    dumps,
    element_from_json,
    element_ref,
    load,
    loads,
)
from spice2tikz.symbols import BUILTIN_SYMBOLS, PinDef, SymbolDef, resolve_pins

CORPUS = Path(__file__).parent / "corpus"
RC_LOWPASS = CORPUS / "rc_lowpass.schematic.json"


def rc_lowpass() -> SchematicIR:
    """Build the spec §5 schematic example in code."""
    return SchematicIR(
        meta=SchematicMeta(title="RC low-pass"),
        style=StyleDefaults(),
        sheets=[
            Sheet(
                name="main",
                elements=[
                    PathComponent(
                        ref="V1",
                        kind=Kind.VSOURCE,
                        a=(0, 4),
                        b=(0, 0),
                        label=LabelSpec(side="left"),
                    ),
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 4), b=(6, 4)),
                    PathComponent(ref="C1", kind=Kind.CAPACITOR, a=(6, 4), b=(6, 0)),
                    Wire(net="0", points=[(0, 0), (6, 0)]),
                    NetSymbol(net="0", variant="ground", at=(3, 0), rot=0),
                    Junction(at=(3, 0)),
                    NetSymbol(net="out", variant="tap", at=(6, 4), rot=0, text="vout"),
                ],
            )
        ],
    )


# --- defaults and construction ---------------------------------------------


def test_defaults_follow_the_design_decisions():
    style = StyleDefaults()
    assert style.resistor_variant == "european"  # D11
    assert style.capacitor_variant == "european"
    assert style.siunitx is True
    assert style.label_refs is True
    assert style.extra_preamble == []
    assert Grid().pitch == DEFAULT_GRID_PITCH == 0.5


def test_effective_style_falls_back_to_defaults():
    assert SchematicIR().effective_style() == StyleDefaults()
    custom = StyleDefaults(resistor_variant="american")
    assert SchematicIR(style=custom).effective_style() is custom


def test_component_kind_accepts_text():
    component = PathComponent(ref="R1", kind="resistor", a=(0, 0), b=(2, 0))  # type: ignore[arg-type]
    assert component.kind is Kind.RESISTOR


def test_wire_segments():
    wire = Wire(net="n", points=[(0, 0), (2, 0), (2, 3)])
    assert wire.segments() == [((0, 0), (2, 0)), ((2, 0), (2, 3))]
    assert Wire(net="n", points=[(0, 0)]).segments() == []


def test_element_ref():
    assert element_ref(PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(2, 0)))
    assert element_ref(Junction(at=(0, 0))) is None


# --- serialisation ----------------------------------------------------------


def test_to_json_field_order():
    data = rc_lowpass().to_json()
    assert list(data) == ["ir", "version", "meta", "style", "sheets"]
    assert list(data["meta"]) == ["title", "grid"]
    elements = data["sheets"][0]["elements"]
    assert list(elements[0]) == ["type", "mode", "ref", "kind", "a", "b", "label"]
    assert list(elements[3]) == ["type", "net", "points"]
    assert list(elements[4]) == ["type", "net", "variant", "at", "rot"]
    assert list(elements[5]) == ["type", "at"]
    assert list(elements[6]) == ["type", "net", "variant", "at", "rot", "text"]


def test_symbols_block_is_omitted_when_empty():
    assert "symbols" not in rc_lowpass().to_json()


def test_json_round_trip_equality():
    ir = rc_lowpass()
    assert SchematicIR.from_json(ir.to_json()) == ir
    assert loads(dumps(ir)) == ir


def test_round_trip_of_every_element_type():
    ir = SchematicIR(
        meta=SchematicMeta(
            title="all elements",
            source_netlist="x.netlist.json",
            generator="spice2tikz",
            grid=Grid(pitch=0.25),
        ),
        style=StyleDefaults(
            resistor_variant="american",
            capacitor_variant="american",
            siunitx=False,
            label_refs=False,
            extra_preamble=[r"\usepackage{amsmath}"],
        ),
        symbols={
            "subckt:amp": SymbolDef(
                size=(6, 6),
                pins={
                    "in": PinDef(offset=(-3, 2), label="in"),
                    "out": PinDef(offset=(3, 0)),
                },
            )
        },
        sheets=[
            Sheet(
                name="main",
                elements=[
                    PathComponent(
                        ref="R1",
                        kind=Kind.RESISTOR,
                        a=(0, 0),
                        b=(4, 0),
                        label=LabelSpec(text="-"),
                        value_label=LabelSpec(text=r"1\,k\Omega", side="below"),
                        style=StyleOverride(circuitikz_options="thick", color="blue"),
                    ),
                    NodeComponent(
                        ref="M1",
                        kind=Kind.NMOS,
                        symbol="nmos",
                        at=(8, 4),
                        rot=270,
                        mirror=True,
                        pins=resolve_pins(
                            BUILTIN_SYMBOLS["nmos"], (8, 4), 270, mirror=True
                        ),
                        label=LabelSpec(side="right"),
                        style=StyleOverride(color="red"),
                    ),
                    Wire(net="n1", points=[(4, 0), (8, 0), (8, 2)]),
                    Junction(at=(8, 0)),
                    NetSymbol(net="vcc", variant="vcc", at=(0, 6), rot=90, text="VCC"),
                    Port(name="in", at=(-2, 0), direction="left"),
                    Label(at=(2, 2), text=r"$v_{\mathrm{out}}$", anchor="south"),
                ],
            ),
            Sheet(name="second", elements=[]),
        ],
    )
    assert loads(dumps(ir)) == ir
    data = ir.to_json()
    assert list(data) == ["ir", "version", "meta", "style", "symbols", "sheets"]


def test_node_component_json_shape():
    node = NodeComponent(
        ref="M1",
        kind=Kind.NMOS,
        symbol="nmos",
        at=(4, 4),
        rot=90,
        mirror=False,
        pins={"d": (2, 6)},
    )
    assert node.to_json() == {
        "type": "component",
        "mode": "node",
        "ref": "M1",
        "kind": "nmos",
        "symbol": "nmos",
        "at": [4, 4],
        "rot": 90,
        "mirror": False,
        "pins": {"d": [2, 6]},
    }


def test_style_defaults_are_filled_in_on_load():
    style = StyleDefaults.from_json({"siunitx": False}, "style")
    assert style == StyleDefaults(siunitx=False)


def test_grid_defaults_when_meta_is_terse():
    ir = SchematicIR.from_json(
        {"ir": "schematic", "version": "1.0", "meta": {}, "sheets": []}
    )
    assert ir.meta.grid == Grid()
    assert ir.style is None
    assert ir.sheets == []


# --- element discrimination -------------------------------------------------


def test_element_discrimination_by_type_and_mode():
    ir = load(RC_LOWPASS)
    kinds = [type(element).__name__ for element in ir.sheets[0].elements]
    assert kinds == [
        "PathComponent",
        "PathComponent",
        "PathComponent",
        "Wire",
        "NetSymbol",
        "Junction",
        "NetSymbol",
    ]


def test_node_components_load_as_node_components():
    data = {
        "type": "component",
        "mode": "node",
        "ref": "M1",
        "kind": "nmos",
        "symbol": "nmos",
        "at": [4, 4],
        "rot": 0,
        "mirror": False,
        "pins": {"d": [6, 6], "g": [2, 4], "s": [6, 2], "b": [6, 4]},
    }
    element = element_from_json(data, "sheets[0].elements[0]")
    assert isinstance(element, NodeComponent)
    assert element.pins["g"] == (2, 4)


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"type": "widget"}, "expected one of"),
        ({"type": "component", "mode": "blob"}, "expected 'path' or 'node'"),
        ({"type": "component"}, "missing required field 'mode'"),
        ({}, "missing required field 'type'"),
        ({"type": 7}, "expected a string"),
        ("wire", "expected an object"),
    ],
)
def test_bad_elements_raise_ir_error(data: object, fragment: str):
    with pytest.raises(IRError, match=fragment):
        element_from_json(data, "sheets[0].elements[0]")


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"rot": 45}, "expected one of 0, 90, 180, 270"),
        ({"mirror": "yes"}, "expected true or false"),
        ({"at": [1, 2, 3]}, "expected a coordinate pair"),
        ({"kind": "widget"}, "unknown kind"),
        ({"symbol": 3}, "expected a string"),
    ],
)
def test_bad_node_component_fields_raise_ir_error(
    mutation: dict[str, object], fragment: str
):
    data: dict[str, object] = {
        "type": "component",
        "mode": "node",
        "ref": "M1",
        "kind": "nmos",
        "symbol": "nmos",
        "at": [4, 4],
        "rot": 0,
        "mirror": False,
        "pins": {},
    }
    data.update(mutation)
    with pytest.raises(IRError, match=fragment):
        element_from_json(data, "sheets[0].elements[0]")


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"variant": "spark"}, "expected one of"),
        ({"net": None}, "expected a string, got null"),
        ({"text": None}, "must be omitted rather than null"),
    ],
)
def test_bad_net_symbol_fields_raise_ir_error(
    mutation: dict[str, object], fragment: str
):
    data: dict[str, object] = {
        "type": "net_symbol",
        "net": "0",
        "variant": "ground",
        "at": [0, 0],
        "rot": 0,
    }
    data.update(mutation)
    with pytest.raises(IRError, match=fragment):
        element_from_json(data, "sheets[0].elements[0]")


def test_wrong_document_kind_is_rejected():
    with pytest.raises(IRError, match="expected 'schematic'"):
        SchematicIR.from_json({"ir": "netlist", "version": "1.0", "sheets": []})


# --- corpus file ------------------------------------------------------------


def test_corpus_file_matches_the_worked_example():
    assert load(RC_LOWPASS) == rc_lowpass()


def test_corpus_file_redumps_byte_identically():
    assert dumps(load(RC_LOWPASS)).encode("utf-8") == RC_LOWPASS.read_bytes()


def test_corpus_file_keeps_coordinates_on_one_line():
    # Hand-editing coordinates is a headline workflow, so the canonical
    # format keeps them compact (docs/SPEC_IR.md §0).
    text = RC_LOWPASS.read_text(encoding="utf-8")
    assert '"a": [0, 4],' in text
    assert '"points": [[0, 0], [6, 0]]' in text
    assert '"at": [3, 0]' in text


def test_corpus_file_matches_the_spec_json():
    # The spec §5 schematic listing, verbatim apart from formatting.
    expected = json.loads("""
    { "ir": "schematic", "version": "1.0",
      "meta": { "title": "RC low-pass", "grid": { "pitch": 0.5 } },
      "style": { "resistor_variant": "european",
                 "capacitor_variant": "european",
                 "siunitx": true, "label_refs": true },
      "sheets": [ { "name": "main", "elements": [
        { "type": "component", "mode": "path", "ref": "V1", "kind": "vsource",
          "a": [0, 4], "b": [0, 0], "label": { "side": "left" } },
        { "type": "component", "mode": "path", "ref": "R1", "kind": "resistor",
          "a": [0, 4], "b": [6, 4] },
        { "type": "component", "mode": "path", "ref": "C1", "kind": "capacitor",
          "a": [6, 4], "b": [6, 0] },
        { "type": "wire", "net": "0", "points": [[0, 0], [6, 0]] },
        { "type": "net_symbol", "net": "0", "variant": "ground",
          "at": [3, 0], "rot": 0 },
        { "type": "junction", "at": [3, 0] },
        { "type": "net_symbol", "net": "out", "variant": "tap",
          "at": [6, 4], "rot": 0, "text": "vout" } ] } ] }
    """)
    assert json.loads(RC_LOWPASS.read_text(encoding="utf-8")) == expected


def test_determinism_of_serialisation():
    ir = load(RC_LOWPASS)
    assert dumps(ir) == dumps(ir)
    assert dumps(loads(dumps(ir))) == dumps(ir)


def test_unknown_fields_are_warned_about_and_ignored():
    data = rc_lowpass().to_json()
    data["sheets"][0]["elements"][0]["colour"] = "purple"
    warnings: list[str] = []
    ir = SchematicIR.from_json(data, warnings)
    assert ir == rc_lowpass()
    assert warnings == [
        "sheets[0].elements[0]: unknown field 'colour' ignored",
    ]


def test_dump_writes_canonical_text(tmp_path: Path):
    from spice2tikz.schematic_ir import dump

    target = tmp_path / "out.json"
    dump(rc_lowpass(), target)
    assert target.read_bytes() == RC_LOWPASS.read_bytes()
