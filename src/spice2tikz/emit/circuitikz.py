r"""Schematic IR → CircuiTikZ emission (``docs/SPEC_IR.md`` §3, roadmap §2).

The emitter is a pure function of the Schematic IR: given a document it
renders a circuitikz snippet (design decision D13), or a compilable
standalone document when wrapped by :func:`emit_standalone`. It reads
``sheets[0]`` only; multi-sheet composition is future work.

Two kinds of text end up in the output: *derived* text (refdes labels, net
names, unparseable values) is always escaped per D12, while the handful of
fields the spec documents as carrying raw LaTeX verbatim — ``LabelSpec.text``
when explicit, the free-standing ``Label.text``, ``extra_preamble``, and
``StyleOverride.circuitikz_options`` — are emitted exactly as given.
"""

from __future__ import annotations

import math
import re
from typing import Final

from ..netlist_ir import Kind
from ..quantity import Quantity
from ..schematic_ir import (
    Element,
    Junction,
    Label,
    LabelSpec,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    SchematicIR,
    Sheet,
    StyleDefaults,
    StyleOverride,
    Wire,
)
from ..symbols import Point, SymbolDef, lookup_symbol, pin_anchor, rotated_size

Coordinate = tuple[float, float]

BIPOLE_NAMES: Final[dict[Kind, str]] = {
    Kind.RESISTOR: "R",
    Kind.CAPACITOR: "C",
    Kind.INDUCTOR: "L",
    Kind.DIODE: "D",
    Kind.VSOURCE: "american voltage source",
    Kind.ISOURCE: "american current source",
    Kind.VCVS: "american controlled voltage source",
    Kind.CCVS: "american controlled voltage source",
    Kind.VCCS: "american controlled current source",
    Kind.CCCS: "american controlled current source",
    Kind.SWITCH: "switch",
    Kind.TLINE: "tline",
    Kind.GENERIC: "generic",
}
"""``kind`` → circuitikz bipole name. Every name here has been checked to
compile against circuitikz 1.4.6; ``generic`` is the deliberate placeholder for
kinds with no dedicated symbol (DESIGN §6).

Sources name the American shape outright rather than using the ``vsource`` /
``isource`` shorthands. Those shorthands follow circuitikz's
``europeanvoltages`` flag, whose default draws a bar with no polarity marks; a
schematic should show which terminal is positive. The long spelling is also the
portable one — ``vsourceAM`` only exists from circuitikz 1.8 — and it avoids
``\\ctikzset{american voltages}``, which would additionally switch voltage
*annotation* arrows to +/- signs.
"""

ACTIVE_KINDS: Final[frozenset[Kind]] = frozenset(
    {
        Kind.VSOURCE,
        Kind.ISOURCE,
        Kind.VCVS,
        Kind.VCCS,
        Kind.CCVS,
        Kind.CCCS,
    }
)
"""Kinds whose label must never use the ``to[<bipole>=text]`` shorthand.

The CircuiTikZ manual §5.1.1 warns that for sources the shorthand sets the
*voltage* or *current* property rather than the label, which renders as an
annotated arrow instead of a name.  These kinds always get an explicit ``l=``.
"""

_ESCAPE_MAP: Final[dict[str, str]] = {
    "\\": "\\textbackslash{}",
    "{": "\\{",
    "}": "\\}",
    "$": "\\$",
    "&": "\\&",
    "#": "\\#",
    "_": "\\_",
    "%": "\\%",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
}
_ESCAPE_RE: Final = re.compile("|".join(re.escape(char) for char in _ESCAPE_MAP))

_REF_RE: Final = re.compile(r"^([A-Za-z]+)(\d+)$")

_PREFIX_MACROS: Final[dict[int, str]] = {
    -15: "\\femto",
    -12: "\\pico",
    -9: "\\nano",
    -6: "\\micro",
    -3: "\\milli",
    0: "",
    3: "\\kilo",
    6: "\\mega",
    9: "\\giga",
    12: "\\tera",
}
_UNIT_MACROS: Final[dict[str, str]] = {
    "ohm": "\\ohm",
    "F": "\\farad",
    "H": "\\henry",
    "V": "\\volt",
    "A": "\\ampere",
    "s": "\\second",
    "Hz": "\\hertz",
    "W": "\\watt",
}
_MIN_EXPONENT: Final = min(_PREFIX_MACROS)
_MAX_EXPONENT: Final = max(_PREFIX_MACROS)

_SIDE_COMPASS: Final[dict[str, str]] = {
    "above": "north",
    "below": "south",
    "left": "west",
    "right": "east",
}
_OPPOSITE_COMPASS: Final[dict[str, str]] = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}
_NODE_LABEL_POSITION: Final[dict[str, str]] = {
    "above": "above",
    "below": "below",
    "left": "left",
    "right": "right",
}
"""Explicit ``LabelSpec.side`` → TikZ label position. ``auto`` is absent on
purpose: it means "let the emitter choose" (see :func:`_free_node_side`)."""
_ROTATION_POSITION: Final[dict[int, str]] = {
    0: "right",
    90: "above",
    180: "left",
    270: "below",
}
_PORT_POSITION: Final[dict[str, str]] = {
    "left": "left",
    "right": "right",
    "up": "above",
    "down": "below",
}


# --- derived-label formatting (SPEC_IR §3) ----------------------------------


def escape_latex(text: str) -> str:
    """Escape the LaTeX-special characters of design decision D12 in *text*."""
    return _ESCAPE_RE.sub(lambda match: _ESCAPE_MAP[match.group(0)], text)


def derive_ref_label(ref: str) -> str:
    """Return the derived math-mode label for refdes *ref*: ``R1`` → ``$R_1$``."""
    match = _REF_RE.match(ref)
    if match is None:
        return f"$\\mathrm{{{escape_latex(ref)}}}$"
    letters, digits = match.groups()
    subscript = digits if len(digits) == 1 else f"{{{digits}}}"
    return f"${escape_latex(letters)}_{subscript}$"


def format_quantity(quantity: Quantity, *, siunitx: bool) -> str:
    r"""Format *quantity* per SPEC_IR §3: a ``\SI`` macro, or escaped raw text."""
    if siunitx and quantity.value is not None and quantity.unit is not None:
        unit_macro = _UNIT_MACROS.get(quantity.unit)
        if unit_macro is not None:
            exponent = _engineering_exponent(quantity.value)
            mantissa = quantity.value / (10.0**exponent)
            prefix = _PREFIX_MACROS[exponent]
            return f"\\SI{{{_format_number(mantissa)}}}{{{prefix}{unit_macro}}}"
    return escape_latex(quantity.raw)


def _engineering_exponent(value: float) -> int:
    """Return the multiple-of-3 exponent that puts *value*'s mantissa in [1, 1000)."""
    if value == 0:
        return 0
    magnitude = abs(value)
    exponent = math.floor(math.log10(magnitude) / 3.0) * 3
    exponent = max(_MIN_EXPONENT, min(_MAX_EXPONENT, exponent))
    while exponent < _MAX_EXPONENT and magnitude / (10.0**exponent) >= 1000:
        exponent += 3
    while exponent > _MIN_EXPONENT and magnitude / (10.0**exponent) < 1:
        exponent -= 3
    return exponent


def _format_number(value: float) -> str:
    """Render *value* as compactly as LaTeX needs: no trailing zeros or dot."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _fmt_point(point: Coordinate) -> str:
    return f"{_format_number(point[0])},{_format_number(point[1])}"


def _resolve_label(spec: LabelSpec | None, derived: str | None) -> str | None:
    """Resolve a label's text: suppressed, explicit verbatim, or *derived*.

    An explicit ``text`` is used verbatim (SPEC_IR §2's specific "explicit →
    verbatim" contract for ``LabelSpec``, which takes precedence here over the
    more general D12 list of raw-LaTeX sites).
    """
    if spec is not None and spec.text == "-":
        return None
    if spec is not None and spec.text is not None:
        return spec.text
    return derived


def _path_sides(a: Point, b: Point) -> tuple[str, str]:
    """Return the ``(natural, opposite)`` compass sides of the path *a* → *b*.

    ``natural`` is where circuitikz puts a plain ``l=`` label: 90°
    counterclockwise from the direction of travel. ``a=`` annotations go on the
    ``opposite`` side, and the underscore forms (``l_``, ``a_``) swap the two.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx, sy = -dy, dx
    if abs(sx) >= abs(sy):
        natural = "west" if sx < 0 else "east"
    else:
        natural = "south" if sy < 0 else "north"
    return natural, _OPPOSITE_COMPASS[natural]


def _requested_compass(spec: LabelSpec | None) -> str | None:
    """Return the compass side *spec* asks for, or ``None`` for "wherever"."""
    if spec is None or spec.side is None or spec.side == "auto":
        return None
    return _SIDE_COMPASS[spec.side]


def _place_path_label(
    spec: LabelSpec | None,
    derived: str | None,
    keys: dict[str, str],
    fallback: str,
) -> tuple[str, str, str] | None:
    """Resolve a path label to ``(option key, text, compass side)``.

    *keys* maps each of the two possible compass sides to the circuitikz option
    key that puts a label there. A requested side on the axis the path cannot
    express (e.g. "above" on a horizontal path) falls back to *fallback*.
    """
    text = _resolve_label(spec, derived)
    if text is None:
        return None
    requested = _requested_compass(spec)
    compass = requested if requested in keys else fallback
    return keys[compass], text, compass


def _free_node_side(el: NodeComponent) -> str:
    """Return the side of a node component that its pins leave clear.

    The pins of a transistor fan out from the body on three sides, so the
    quietest place for a label is opposite their centre of mass: right of an
    unrotated MOS (whose body and gate sit to the left), and rotating with the
    component. Falls back to "above" for a symbol with no usable pins.
    """
    total_x = sum(point[0] - el.at[0] for point in el.pins.values())
    total_y = sum(point[1] - el.at[1] for point in el.pins.values())
    if total_x == 0 and total_y == 0:
        return "above"
    if abs(total_x) >= abs(total_y):
        return "left" if total_x > 0 else "right"
    return "below" if total_y > 0 else "above"


def _node_label_option(
    spec: LabelSpec | None, derived: str | None, fallback: str
) -> str | None:
    text = _resolve_label(spec, derived)
    if text is None:
        return None
    side = spec.side if spec is not None else None
    position = _NODE_LABEL_POSITION.get(side or "", fallback)
    return f"label={position}:{{{text}}}"


def _style_options(style: StyleOverride | None) -> list[str]:
    if style is None:
        return []
    options = []
    if style.circuitikz_options:
        options.append(style.circuitikz_options)
    if style.color:
        options.append(f"color={style.color}")
    return options


# --- element emission --------------------------------------------------------


def _bipole_name(kind: Kind) -> str:
    """Return the circuitikz bipole to draw *kind* with.

    ``style.capacitor_variant`` is deliberately not consulted: circuitikz has no
    American/European capacitor style, and its only alternative plate shape
    (``cC``) is documented as the *polarized* capacitor, so honouring the IR
    field would change what the symbol means rather than how it looks.  See
    DECISIONS 2.3.
    """
    return BIPOLE_NAMES.get(kind, "generic")


def _emit_path(el: PathComponent, style: StyleDefaults) -> list[str]:
    bipole = _bipole_name(el.kind)
    natural, opposite = _path_sides(el.a, el.b)

    ref_derived = derive_ref_label(el.ref) if style.label_refs else None
    label = _place_path_label(
        el.label, ref_derived, {natural: "l", opposite: "l_"}, natural
    )
    # `l` folds into the bipole slot (`to[R=$R_1$]`), but only for passive
    # components: on a source that shorthand sets the voltage/current instead.
    foldable = label is not None and label[0] == "l" and el.kind not in ACTIVE_KINDS
    if foldable and label is not None:
        options = [f"{bipole}={label[1]}"]
    else:
        options = [bipole]
        if label is not None:
            options.append(f"{label[0]}={label[1]}")

    # The value goes on whichever side the ref label left free, so the two never
    # collide. The annotation keys are NOT symmetric with the label keys: `a` and
    # `a_` both sit opposite the *natural* label side, and only `a^` crosses over
    # to it (verified by rendering against circuitikz 1.4.6).
    free = _OPPOSITE_COMPASS[label[2]] if label is not None else opposite
    value = _place_path_label(
        el.value_label, None, {opposite: "a", natural: "a^"}, free
    )
    if value is not None:
        options.append(f"{value[0]}={value[1]}")

    options.extend(_style_options(el.style))
    joined = ", ".join(options)
    return [f"\\draw ({_fmt_point(el.a)}) to[{joined}] ({_fmt_point(el.b)});"]


def _emit_wire(el: Wire) -> list[str]:
    chain = " -- ".join(f"({_fmt_point(point)})" for point in el.points)
    return [f"\\draw {chain};"]


def _emit_junction(el: Junction) -> list[str]:
    return [f"\\draw ({_fmt_point(el.at)}) node[circ]{{}};"]


def _emit_net_symbol(el: NetSymbol) -> list[str]:
    position = _ROTATION_POSITION.get(el.rot, "right")
    if el.variant == "tap":
        text = escape_latex(el.text) if el.text is not None else escape_latex(el.net)
        return [f"\\node[{position}] at ({_fmt_point(el.at)}) {{{text}}};"]
    options: list[str] = [el.variant]
    if el.rot:
        options.append(f"rotate={el.rot}")
    line = f"\\draw ({_fmt_point(el.at)}) node[{', '.join(options)}]{{}}"
    if el.text is not None:
        line += f" node[{position}]{{{escape_latex(el.text)}}}"
    return [line + ";"]


def _emit_port(el: Port) -> list[str]:
    position = _PORT_POSITION[el.direction]
    return [f"\\node[{position}] at ({_fmt_point(el.at)}) {{{escape_latex(el.name)}}};"]


def _emit_label(el: Label) -> list[str]:
    options = f"[anchor={el.anchor}]" if el.anchor and el.anchor != "center" else ""
    return [f"\\node{options} at ({_fmt_point(el.at)}) {{{el.text}}};"]


def _box_edge_point(pin: Point, at: Point, half_w: float, half_h: float) -> Coordinate:
    """Return where a pin stub should meet a box of half-extents *half_w/h*."""
    dx, dy = pin[0] - at[0], pin[1] - at[1]
    horizontal_ratio = abs(dx) / half_w if half_w else float("inf")
    vertical_ratio = abs(dy) / half_h if half_h else float("inf")
    if horizontal_ratio >= vertical_ratio:
        edge_x = at[0] + (half_w if dx >= 0 else -half_w)
        return (edge_x, pin[1])
    edge_y = at[1] + (half_h if dy >= 0 else -half_h)
    return (pin[0], edge_y)


def tikz_node_name(index: int) -> str:
    """Return the TikZ node name used for the element at *index*.

    Positional rather than derived from the refdes: a refdes may contain
    characters TikZ will not accept in a node name, and the index is unique and
    stable for a given document.
    """
    return f"s2t{index}"


def _emit_shape_node(
    el: NodeComponent, symbol: SymbolDef, base: str, index: int, style: StyleDefaults
) -> list[str]:
    """Place a circuitikz shape and wire its anchors out to the declared pins."""
    options = [base]
    if el.mirror:
        options.append("xscale=-1")
    if el.rot:
        options.append(f"rotate={el.rot}")
    ref_derived = derive_ref_label(el.ref) if style.label_refs else None
    label_opt = _node_label_option(el.label, ref_derived, _free_node_side(el))
    if label_opt is not None:
        options.append(label_opt)
    options.extend(_style_options(el.style))
    name = tikz_node_name(index)
    lines = [
        f"\\node[{', '.join(options)}] ({name}) at ({_fmt_point(el.at)}) {{}};",
        *_emit_pin_leads(el, symbol, base, name),
    ]
    return lines


def _emit_pin_leads(
    el: NodeComponent, symbol: SymbolDef, base: str, name: str
) -> list[str]:
    """Draw a lead from each rendered terminal to its declared pin position.

    circuitikz shapes are drawn at their own size, unaffected by the ``scale``
    the grid pitch sets, so a terminal almost never lands exactly on the integer
    grid point the IR declares for it.  Rather than pretend otherwise (which
    leaves visibly unconnected components), each pin gets a short orthogonal
    lead from the shape's documented anchor to the declared coordinate.  Anchors
    are resolved by TeX after the node's own rotate/xscale, so this works for
    every orientation.
    """
    leads = []
    for pin, point in el.pins.items():
        anchor = pin_anchor(base, pin)
        definition = symbol.pins.get(pin)
        if anchor is None or definition is None:
            continue
        if definition.offset == (0, 0):
            # Pin sits on the node origin (a MOS bulk): the anchor is already
            # there, so a lead would be zero length.
            continue
        # Leave the body along the axis the pin lies on, then turn. Use the
        # placed direction, not the symbol's own offset, so that a rotated
        # component leaves along its rotated axis.
        dx, dy = point[0] - el.at[0], point[1] - el.at[1]
        joint = "-|" if abs(dx) > abs(dy) else "|-"
        leads.append(f"\\draw ({name}.{anchor}) {joint} ({_fmt_point(point)});")
    return leads


def _emit_generic_box(
    el: NodeComponent, symbol: SymbolDef | None, style: StyleDefaults
) -> list[str]:
    """Draw an unrecognized-shape node as a rectangle with pin stubs (DESIGN §6)."""
    size = symbol.size if symbol is not None else (2, 2)
    width, height = rotated_size(size, el.rot)
    half_w, half_h = width / 2, height / 2
    corner_a = (el.at[0] - half_w, el.at[1] - half_h)
    corner_b = (el.at[0] + half_w, el.at[1] + half_h)
    box_options = _style_options(el.style)
    prefix = f"[{', '.join(box_options)}] " if box_options else " "
    lines = [
        f"\\draw{prefix}({_fmt_point(corner_a)}) rectangle ({_fmt_point(corner_b)});"
    ]
    for pin in el.pins.values():
        edge = _box_edge_point(pin, el.at, half_w, half_h)
        if edge != pin:
            lines.append(f"\\draw ({_fmt_point(pin)}) -- ({_fmt_point(edge)});")
    ref_derived = derive_ref_label(el.ref) if style.label_refs else None
    label = _resolve_label(el.label, ref_derived)
    if label is not None:
        lines.append(f"\\node at ({_fmt_point(el.at)}) {{{label}}};")
    return lines


def _emit_node(
    el: NodeComponent, index: int, ir: SchematicIR, style: StyleDefaults
) -> list[str]:
    symbol = lookup_symbol(el.symbol, ir.symbols)
    if symbol is not None and symbol.base is not None:
        return _emit_shape_node(el, symbol, symbol.base, index, style)
    return _emit_generic_box(el, symbol, style)


def _emit_element(
    element: Element, index: int, ir: SchematicIR, style: StyleDefaults
) -> list[str]:
    if isinstance(element, PathComponent):
        return _emit_path(element, style)
    if isinstance(element, NodeComponent):
        return _emit_node(element, index, ir, style)
    if isinstance(element, Wire):
        return _emit_wire(element)
    if isinstance(element, Junction):
        return _emit_junction(element)
    if isinstance(element, NetSymbol):
        return _emit_net_symbol(element)
    if isinstance(element, Port):
        return _emit_port(element)
    return _emit_label(element)


def _variant_options(style: StyleDefaults) -> list[str]:
    r"""Return the ``\ctikzset`` style declarations for this document (D11).

    The resistor variant is *always* declared, never left implicit: circuitikz's
    own default is the American zigzag (``\newif\ifpgf@circuit@europeanresistor``
    defaults false), so the IR's European default would silently render as
    American if the emitter only spoke up for the non-default case. Declaring it
    outright also means the rendering cannot drift with a circuitikz release.

    The capacitor variant has no equivalent style key in circuitikz and is
    handled per component by :func:`_bipole_name`.
    """
    return [f"\\ctikzset{{{style.resistor_variant} resistors}}"]


# --- document assembly -------------------------------------------------------


def emit_snippet(ir: SchematicIR) -> str:
    """Render ``ir.sheets[0]`` as a circuitikz snippet (no document wrapper, D13)."""
    style = ir.effective_style()
    sheet = ir.sheets[0] if ir.sheets else Sheet()
    lines = [f"\\begin{{circuitikz}}[scale={_format_number(ir.meta.grid.pitch)}]"]
    lines.extend(f"  {line}" for line in _variant_options(style))
    for index, element in enumerate(sheet.elements):
        lines.extend(f"  {line}" for line in _emit_element(element, index, ir, style))
    lines.append("\\end{circuitikz}")
    return "\n".join(lines) + "\n"


def emit_standalone(ir: SchematicIR) -> str:
    """Wrap :func:`emit_snippet` in a compilable standalone document (D13)."""
    style = ir.effective_style()
    lines = [
        # `border` alone crops to the drawing; adding standalone's `tikz` class
        # option defeats the cropping when circuitikz is loaded separately, and
        # yields a full letter page.
        "\\documentclass[border=2pt]{standalone}",
        "\\usepackage{circuitikz}",
        "\\usepackage{siunitx}",
        *style.extra_preamble,
        "\\begin{document}",
        emit_snippet(ir).rstrip("\n"),
        "\\end{document}",
    ]
    return "\n".join(lines) + "\n"


def emit(ir: SchematicIR, *, standalone: bool = False) -> str:
    """Render *ir*: a snippet by default, or a standalone document."""
    return emit_standalone(ir) if standalone else emit_snippet(ir)
