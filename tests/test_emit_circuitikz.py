"""Unit tests for the circuitikz emitter (roadmap §2.1, §2.2)."""

from __future__ import annotations

import pytest

from spice2tikz.emit.circuitikz import (
    BIPOLE_NAMES,
    derive_ref_label,
    emit,
    emit_snippet,
    emit_standalone,
    escape_latex,
    format_quantity,
)
from spice2tikz.netlist_ir import Kind
from spice2tikz.quantity import Quantity
from spice2tikz.schematic_ir import (
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
)
from spice2tikz.symbols import PinDef, SymbolDef

# --- escaping (D12) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("R_1", "R\\_1"),
        ("50%", "50\\%"),
        ("A&B", "A\\&B"),
        ("#tag", "\\#tag"),
        ("$5", "\\$5"),
        ("{x}", "\\{x\\}"),
        ("a~b", "a\\textasciitilde{}b"),
        ("a^b", "a\\textasciicircum{}b"),
        ("a\\b", "a\\textbackslash{}b"),
    ],
)
def test_escape_latex_covers_every_special_character(raw: str, escaped: str):
    assert escape_latex(raw) == escaped


def test_escape_latex_does_not_double_escape_the_backslash_it_introduces():
    # A naive sequential str.replace would re-escape the backslash just
    # inserted for "_", turning "R_1" into "R\textbackslash{}_1".
    assert escape_latex("_") == "\\_"


def test_escape_latex_is_the_identity_on_plain_text():
    assert escape_latex("vout") == "vout"


# --- derived ref labels (SPEC_IR §3) -----------------------------------------


@pytest.mark.parametrize(
    ("ref", "label"),
    [
        ("R1", "$R_1$"),
        ("V1", "$V_1$"),
        ("C12", "$C_{12}$"),
        ("Q100", "$Q_{100}$"),
    ],
)
def test_derive_ref_label_subscripts_trailing_digits(ref: str, label: str):
    assert derive_ref_label(ref) == label


def test_derive_ref_label_single_digit_has_no_braces():
    # SPEC_IR §5's normative golden emission is literally "$R_1$", not
    # "$R_{1}$"; multi-digit refs need the braces to subscript correctly.
    assert derive_ref_label("R1") == "$R_1$"


def test_derive_ref_label_escapes_the_fallback_form():
    assert derive_ref_label("X_1") == "$\\mathrm{X\\_1}$"


def test_derive_ref_label_falls_back_for_refs_without_trailing_digits():
    assert derive_ref_label("Rfoo") == "$\\mathrm{Rfoo}$"
    assert derive_ref_label("XU_1") == "$\\mathrm{XU\\_1}$"


# --- quantity formatting (SPEC_IR §3) ----------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (10000.0, "ohm", "\\SI{10}{\\kilo\\ohm}"),
        (1e-7, "F", "\\SI{100}{\\nano\\farad}"),
        (5.0, "V", "\\SI{5}{\\volt}"),
        (4700.0, "ohm", "\\SI{4.7}{\\kilo\\ohm}"),
        (0.001, "H", "\\SI{1}{\\milli\\henry}"),
        (2.2e6, "Hz", "\\SI{2.2}{\\mega\\hertz}"),
    ],
)
def test_format_quantity_matches_the_spec_examples(
    value: float, unit: str, expected: str
):
    quantity = Quantity(raw="ignored", value=value, unit=unit)
    assert format_quantity(quantity, siunitx=True) == expected


def test_format_quantity_falls_back_to_escaped_raw_when_unparseable():
    quantity = Quantity(raw="AC 1")
    assert format_quantity(quantity, siunitx=True) == "AC 1"


def test_format_quantity_falls_back_when_unit_is_unrecognised():
    quantity = Quantity(raw="10 furlongs", value=10.0, unit="furlong")
    assert format_quantity(quantity, siunitx=True) == "10 furlongs"


def test_format_quantity_escapes_the_raw_fallback():
    quantity = Quantity(raw="50%_typ")
    assert format_quantity(quantity, siunitx=True) == "50\\%\\_typ"


def test_format_quantity_ignores_siunitx_when_disabled():
    quantity = Quantity(raw="10k", value=10000.0, unit="ohm")
    assert format_quantity(quantity, siunitx=False) == "10k"


# --- path components, wires, junctions, net symbols, labels (§2.1) ---------


def _sheet(*elements: object) -> SchematicIR:
    return SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(),
        sheets=[Sheet(name="main", elements=list(elements))],  # type: ignore[arg-type]
    )


def test_resistor_label_folds_into_the_bipole_slot_on_the_default_side():
    ir = _sheet(PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 4), b=(6, 4)))
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,4) to[R=$R_1$] (6,4);" in lines


def test_vsource_label_on_the_flipped_side_is_a_separate_option():
    ir = _sheet(
        PathComponent(
            ref="V1",
            kind=Kind.VSOURCE,
            a=(0, 4),
            b=(0, 0),
            label=LabelSpec(side="left"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,4) to[american voltage source, l_=$V_1$] (0,0);" in lines


def test_explicit_label_text_is_used_verbatim():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 0),
            b=(4, 0),
            label=LabelSpec(text="$R_x$"),
        )
    )
    assert "R=$R_x$" in emit_snippet(ir)


def test_suppressed_label_omits_the_option():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 0),
            b=(4, 0),
            label=LabelSpec(text="-"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) to[R] (4,0);" in lines


def test_label_refs_false_suppresses_the_default_derivation():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(label_refs=False),
        sheets=[
            Sheet(
                name="main",
                elements=[
                    PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(4, 0))
                ],
            )
        ],
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) to[R] (4,0);" in lines


def test_value_label_is_a_separate_option_never_folded():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 4),
            b=(6, 4),
            value_label=LabelSpec(text="\\SI{10}{\\kilo\\ohm}"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,4) to[R=$R_1$, a=\\SI{10}{\\kilo\\ohm}] (6,4);" in lines


def test_value_label_avoids_the_side_the_flipped_ref_label_took():
    # circuitikz's `a` annotation already sits opposite a plain `l`, so a ref
    # label flipped to `l_` would land on top of a plain `a`. The value has to
    # flip to `a_` to stay on the free side.
    ir = _sheet(
        PathComponent(
            ref="V1",
            kind=Kind.VSOURCE,
            a=(0, 6),
            b=(0, 0),
            label=LabelSpec(side="left"),
            value_label=LabelSpec(text="\\SI{1}{\\volt}"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    expected = (
        "  \\draw (0,6) to[american voltage source, "
        "l_=$V_1$, a^=\\SI{1}{\\volt}] (0,0);"
    )
    assert expected in lines


def test_value_label_stays_on_the_annotation_side_for_an_unflipped_label():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 0),
            b=(4, 0),
            value_label=LabelSpec(text="\\SI{1}{\\ohm}"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) to[R=$R_1$, a=\\SI{1}{\\ohm}] (4,0);" in lines


def test_suppressed_ref_label_leaves_the_value_on_the_annotation_side():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 0),
            b=(4, 0),
            label=LabelSpec(text="-"),
            value_label=LabelSpec(text="\\SI{1}{\\ohm}"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) to[R, a=\\SI{1}{\\ohm}] (4,0);" in lines


@pytest.mark.parametrize(
    ("a", "b"),
    [((0, 0), (4, 0)), ((4, 0), (0, 0)), ((0, 0), (0, 4)), ((0, 4), (0, 0))],
)
def test_ref_and_value_labels_never_share_a_side(
    a: tuple[int, int], b: tuple[int, int]
):
    for side in ("auto", "above", "below", "left", "right"):
        ir = _sheet(
            PathComponent(
                ref="R1",
                kind=Kind.RESISTOR,
                a=a,
                b=b,
                label=LabelSpec(side=side),  # type: ignore[arg-type]
                value_label=LabelSpec(text="v"),
            )
        )
        text = emit_snippet(ir)
        # `l` sits on the natural side, `l_` opposite; `a` sits opposite and only
        # `a^` crosses to the natural side. So the safe pairings are l+a and
        # l_+a^, and exactly one of the two keys carries a suffix.
        label_flipped = "l_=" in text
        value_crossed = "a^=" in text
        assert label_flipped == value_crossed, text


def test_unknown_kind_falls_back_to_generic_bipole():
    assert BIPOLE_NAMES.get(Kind.GENERIC) == "generic"


@pytest.mark.parametrize("kind", list(Kind))
def test_every_path_eligible_kind_has_a_bipole_name(kind: Kind):
    # Kinds drawn as node components (transistors) and subcircuits are not
    # path components; everything else must map to a real circuitikz bipole.
    node_kinds = {
        Kind.BJT_NPN,
        Kind.BJT_PNP,
        Kind.NMOS,
        Kind.PMOS,
        Kind.NJFET,
        Kind.PJFET,
        Kind.SUBCIRCUIT,
    }
    if kind in node_kinds:
        return
    assert kind in BIPOLE_NAMES


def test_capacitors_have_no_style_variant():
    # Both standards draw a non-polarized capacitor as two parallel plates, and
    # circuitikz has no american/european capacitor key, so there is nothing to
    # switch -- see DECISIONS 2.3.
    ir = _sheet(PathComponent(ref="C1", kind=Kind.CAPACITOR, a=(0, 0), b=(0, 4)))
    text = emit_snippet(ir)
    assert "to[C=$C_1$]" in text
    assert "cC" not in text
    assert "capacitors" not in text  # never a bogus \ctikzset key


@pytest.mark.parametrize("variant", ["american", "european", "cute"])
def test_inductor_variant_is_always_declared(variant: str):
    # circuitikz defaults to `cuteinductors`, so the chosen style must be stated
    # or the rendering silently depends on the package default.
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(inductor_variant=variant),  # type: ignore[arg-type]
        sheets=[Sheet(name="main", elements=[])],
    )
    assert f"\\ctikzset{{{variant} inductors}}" in emit_snippet(ir)


def test_source_label_never_uses_the_bipole_shorthand():
    # For sources `to[vsource=$V_1$]` sets the *voltage* property, drawing an
    # annotated arrow instead of a name (CircuiTikZ manual §5.1.1).
    ir = _sheet(PathComponent(ref="V1", kind=Kind.VSOURCE, a=(0, 4), b=(0, 0)))
    text = emit_snippet(ir)
    assert "to[american voltage source, l=$V_1$]" in text
    assert "american voltage source=" not in text


@pytest.mark.parametrize("kind", [Kind.ISOURCE, Kind.VCVS, Kind.VCCS, Kind.CCVS])
def test_no_source_kind_uses_the_bipole_shorthand(kind: Kind):
    ir = _sheet(PathComponent(ref="X1", kind=kind, a=(0, 4), b=(0, 0)))
    bipole = BIPOLE_NAMES[kind]
    assert f"{bipole}=" not in emit_snippet(ir)


def test_passive_components_still_use_the_shorthand():
    # SPEC_IR §5's normative emission relies on it for R and C.
    ir = _sheet(PathComponent(ref="R1", kind=Kind.RESISTOR, a=(0, 0), b=(4, 0)))
    assert "to[R=$R_1$]" in emit_snippet(ir)


def test_style_override_adds_circuitikz_options_and_color():
    ir = _sheet(
        PathComponent(
            ref="R1",
            kind=Kind.RESISTOR,
            a=(0, 0),
            b=(4, 0),
            label=LabelSpec(text="-"),
            style=StyleOverride(circuitikz_options="thick", color="red"),
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) to[R, thick, color=red] (4,0);" in lines


def test_wire_chains_every_point():
    ir = _sheet(Wire(net="n1", points=[(0, 0), (4, 0), (4, 4)]))
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (0,0) -- (4,0) -- (4,4);" in lines


def test_junction_draws_a_circ_node():
    ir = _sheet(Junction(at=(3, 0)))
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (3,0) node[circ]{};" in lines


def test_ground_net_symbol():
    ir = _sheet(NetSymbol(net="0", variant="ground", at=(3, 0), rot=0))
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (3,0) node[ground]{};" in lines


def test_net_symbol_rotation_becomes_a_rotate_option():
    ir = _sheet(NetSymbol(net="0", variant="ground", at=(3, 0), rot=90))
    assert "node[ground, rotate=90]" in emit_snippet(ir)


def test_tap_net_symbol_is_a_plain_text_node_matching_the_golden_example():
    ir = _sheet(NetSymbol(net="out", variant="tap", at=(6, 4), rot=0, text="vout"))
    lines = emit_snippet(ir).splitlines()
    assert "  \\node[right] at (6,4) {vout};" in lines


def test_tap_net_symbol_escapes_its_text():
    ir = _sheet(NetSymbol(net="out", variant="tap", at=(6, 4), rot=0, text="v_out"))
    assert "{v\\_out}" in emit_snippet(ir)


def test_tap_net_symbol_falls_back_to_the_net_name():
    ir = _sheet(NetSymbol(net="vref", variant="tap", at=(0, 0), rot=0))
    assert "{vref}" in emit_snippet(ir)


def test_port_direction_maps_to_a_tikz_position():
    ir = _sheet(Port(name="in", at=(0, 0), direction="up"))
    assert "\\node[above] at (0,0) {in};" in emit_snippet(ir)


def test_free_standing_label_text_is_raw_latex():
    ir = _sheet(Label(at=(1, 1), text="$V_{cc}$", anchor="south"))
    assert "\\node[anchor=south] at (1,1) {$V_{cc}$};" in emit_snippet(ir)


def test_scale_comes_from_the_grid_pitch():
    ir = SchematicIR(meta=SchematicMeta(), sheets=[Sheet(name="main", elements=[])])
    assert emit_snippet(ir).splitlines()[0] == "\\begin{circuitikz}[scale=0.5]"


def test_american_resistor_variant_is_declared():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(resistor_variant="american"),
        sheets=[Sheet(name="main", elements=[])],
    )
    assert "\\ctikzset{american resistors}" in emit_snippet(ir)


def test_european_resistor_variant_is_declared_too():
    # circuitikz's own default is the American zigzag, so "european" must be
    # stated outright or the IR's default variant renders as the wrong symbol.
    ir = SchematicIR(meta=SchematicMeta(), sheets=[Sheet(name="main", elements=[])])
    assert "\\ctikzset{european resistors}" in emit_snippet(ir)


def test_emit_snippet_uses_only_the_first_sheet():
    ir = SchematicIR(
        meta=SchematicMeta(),
        sheets=[
            Sheet(name="main", elements=[Junction(at=(0, 0))]),
            Sheet(name="detail", elements=[Junction(at=(9, 9))]),
        ],
    )
    text = emit_snippet(ir)
    assert "(0,0)" in text
    assert "(9,9)" not in text


def test_rc_lowpass_matches_the_normative_structure():
    ir = SchematicIR(
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
    # SPEC_IR §5's expected-emission block, plus the explicit resistor-variant
    # declaration the spec listing leaves implicit (circuitikz's default is
    # American, so "european" has to be stated) and minus the `a=\SI{...}` value
    # labels, which the §5 *IR* listing carries no data for. See DECISIONS 2.3.
    assert emit_snippet(ir) == (
        "\\begin{circuitikz}[scale=0.5]\n"
        "  \\ctikzset{european resistors}\n"
        "  \\ctikzset{cute inductors}\n"
        "  \\draw (0,4) to[american voltage source, l_=$V_1$] (0,0);\n"
        "  \\draw (0,4) to[R=$R_1$] (6,4);\n"
        "  \\draw (6,4) to[C=$C_1$] (6,0);\n"
        "  \\draw (0,0) -- (6,0);\n"
        "  \\draw (3,0) node[ground]{};\n"
        "  \\draw (3,0) node[circ]{};\n"
        "  \\node[right] at (6,4) {vout};\n"
        "\\end{circuitikz}\n"
    )


# --- node components (§2.2) --------------------------------------------------


def test_builtin_shape_node_uses_its_base_as_the_node_style():
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(0, 0),
            pins={"d": (0, 2), "g": (-2, 0), "s": (0, -2), "b": (0, 0)},
        )
    )
    # The gate and body sit left of the origin, so the label defaults to the
    # right, where no pin lead runs.
    assert "\\node[nmos, label=right:{$M_1$}] (s2t0) at (0,0) {};" in emit_snippet(ir)


@pytest.mark.parametrize(
    ("rot", "position"),
    [(0, "right"), (90, "above"), (180, "left"), (270, "below")],
)
def test_default_node_label_side_rotates_with_the_component(rot: int, position: str):
    from spice2tikz.symbols import BUILTIN_SYMBOLS, resolve_pins

    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(10, 6),
            rot=rot,  # type: ignore[arg-type]
            pins=resolve_pins(BUILTIN_SYMBOLS["nmos"], (10, 6), rot, False),
        )
    )
    assert f"label={position}:{{$M_1$}}" in emit_snippet(ir)


def test_explicit_node_label_side_overrides_the_default():
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(0, 0),
            label=LabelSpec(side="above"),
            pins={"d": (0, 2), "g": (-2, 0), "s": (0, -2), "b": (0, 0)},
        )
    )
    assert "label=above:{$M_1$}" in emit_snippet(ir)


def test_shape_node_draws_leads_from_documented_anchors_to_the_pins():
    # circuitikz shapes are drawn at their own size regardless of the grid
    # pitch, so the rendered terminals do not land on the declared pin
    # coordinates; a lead bridges the gap. Anchors per manual §4.15.9.
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(10, 6),
            pins={"d": (10, 8), "g": (8, 6), "s": (10, 4), "b": (10, 6)},
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (s2t0.D) |- (10,8);" in lines
    assert "  \\draw (s2t0.G) -| (8,6);" in lines
    assert "  \\draw (s2t0.S) |- (10,4);" in lines
    # The bulk pin sits on the node origin, where its anchor already is.
    assert not any(".bulk" in line for line in lines)


def test_leads_follow_the_rotated_axis():
    # At rot 90 the channel terminals lie left/right of the origin, so their
    # leads must leave horizontally rather than vertically.
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(10, 6),
            rot=90,
            pins={"d": (8, 6), "g": (10, 4), "s": (12, 6), "b": (10, 6)},
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (s2t0.D) -| (8,6);" in lines
    assert "  \\draw (s2t0.G) |- (10,4);" in lines
    assert "  \\draw (s2t0.S) -| (12,6);" in lines


def test_bjt_leads_use_the_bipolar_anchor_names():
    ir = _sheet(
        NodeComponent(
            ref="Q1",
            kind=Kind.BJT_NPN,
            symbol="npn",
            at=(4, 4),
            pins={"c": (4, 6), "b": (2, 4), "e": (4, 2)},
        )
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (s2t0.C) |- (4,6);" in lines
    assert "  \\draw (s2t0.B) -| (2,4);" in lines
    assert "  \\draw (s2t0.E) |- (4,2);" in lines


def test_generic_box_nodes_get_no_anchor_leads():
    # A symbol with no circuitikz base has no anchors to lead from; the box
    # draws its own stubs instead.
    ir = _sheet(
        NodeComponent(
            ref="U1", kind=Kind.GENERIC, symbol="nonesuch", at=(0, 0), pins={}
        )
    )
    text = emit_snippet(ir)
    assert "s2t" not in text


def test_mirrored_and_rotated_node_rotates_before_it_mirrors():
    """``rotate`` is listed first so TikZ applies the mirror first.

    TikZ post-multiplies each node transformation onto the current matrix, so
    the option written *last* reaches the shape *first*. Listing rotate then
    xscale therefore means mirror-then-rotate, which is the IR's convention
    (``symbols.transform_offset``). The other way round disagrees at 90 and
    270 degrees; ``tests/test_anchor_geometry.py`` proves it against a real
    compiler.
    """
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(0, 0),
            rot=90,
            mirror=True,
            pins={"d": (-2, 0), "g": (0, 2), "s": (2, 0), "b": (0, 0)},
        )
    )
    line = emit_snippet(ir)
    assert "xscale=-1" in line
    assert "rotate=90" in line
    assert line.index("rotate=90") < line.index("xscale=-1")


def test_node_label_side_maps_to_a_label_position():
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(0, 0),
            label=LabelSpec(side="below"),
            pins={"d": (0, 2), "g": (-2, 0), "s": (0, -2), "b": (0, 0)},
        )
    )
    assert "label=below:{$M_1$}" in emit_snippet(ir)


def test_node_style_override_is_appended():
    ir = _sheet(
        NodeComponent(
            ref="M1",
            kind=Kind.NMOS,
            symbol="nmos",
            at=(0, 0),
            label=LabelSpec(text="-"),
            style=StyleOverride(color="blue"),
            pins={"d": (0, 2), "g": (-2, 0), "s": (0, -2), "b": (0, 0)},
        )
    )
    assert "color=blue" in emit_snippet(ir)


def test_generic_box_draws_a_rectangle_with_a_centered_label():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(),
        symbols={
            "subckt:opamp": SymbolDef(
                size=(6, 4),
                pins={"out": PinDef(offset=(3, 0))},
            )
        },
        sheets=[
            Sheet(
                name="main",
                elements=[
                    NodeComponent(
                        ref="U1",
                        kind=Kind.SUBCIRCUIT,
                        symbol="subckt:opamp",
                        at=(10, 0),
                        pins={"out": (13, 0)},
                    )
                ],
            )
        ],
    )
    lines = emit_snippet(ir).splitlines()
    assert "  \\draw (7,-2) rectangle (13,2);" in lines
    assert "  \\node at (10,0) {$U_1$};" in lines


def test_generic_box_draws_a_stub_for_a_pin_off_the_edge():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(),
        symbols={
            "subckt:opamp": SymbolDef(
                size=(6, 4),
                pins={"in": PinDef(offset=(-4, 1))},
            )
        },
        sheets=[
            Sheet(
                name="main",
                elements=[
                    NodeComponent(
                        ref="U1",
                        kind=Kind.SUBCIRCUIT,
                        symbol="subckt:opamp",
                        at=(10, 0),
                        pins={"in": (6, 1)},
                    )
                ],
            )
        ],
    )
    assert "  \\draw (6,1) -- (7,1);" in emit_snippet(ir).splitlines()


def test_generic_box_omits_a_stub_when_the_pin_is_already_on_the_edge():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(),
        symbols={
            "subckt:opamp": SymbolDef(
                size=(6, 4),
                pins={"out": PinDef(offset=(3, 0))},
            )
        },
        sheets=[
            Sheet(
                name="main",
                elements=[
                    NodeComponent(
                        ref="U1",
                        kind=Kind.SUBCIRCUIT,
                        symbol="subckt:opamp",
                        at=(10, 0),
                        pins={"out": (13, 0)},
                    )
                ],
            )
        ],
    )
    lines = emit_snippet(ir).splitlines()
    assert not any(line.startswith("  \\draw (13,0) --") for line in lines)


def test_unresolvable_symbol_falls_back_to_a_default_size_box():
    ir = _sheet(
        NodeComponent(
            ref="U1", kind=Kind.GENERIC, symbol="nonesuch", at=(0, 0), pins={}
        )
    )
    assert "\\draw (-1,-1) rectangle (1,1);" in emit_snippet(ir)


# --- document assembly (§2.2 standalone wrapper) -----------------------------


def test_standalone_wraps_the_snippet_in_a_compilable_document():
    ir = SchematicIR(meta=SchematicMeta(), sheets=[Sheet(name="main", elements=[])])
    text = emit_standalone(ir)
    # No `tikz` class option: it defeats standalone's cropping here.
    assert text.startswith("\\documentclass[border=2pt]{standalone}\n")
    assert "\\usepackage{circuitikz}\n" in text
    assert "\\usepackage{siunitx}\n" in text
    assert "\\begin{document}\n" in text
    assert text.rstrip("\n").endswith("\\end{document}")
    assert "\\begin{circuitikz}" in text
    assert "\\end{circuitikz}" in text


def test_standalone_includes_extra_preamble_verbatim():
    ir = SchematicIR(
        meta=SchematicMeta(),
        style=StyleDefaults(extra_preamble=["\\usetikzlibrary{fadings}"]),
        sheets=[Sheet(name="main", elements=[])],
    )
    assert "\\usetikzlibrary{fadings}\n" in emit_standalone(ir)


def test_emit_dispatches_on_the_standalone_flag():
    ir = SchematicIR(meta=SchematicMeta(), sheets=[Sheet(name="main", elements=[])])
    assert emit(ir) == emit_snippet(ir)
    assert emit(ir, standalone=True) == emit_standalone(ir)
