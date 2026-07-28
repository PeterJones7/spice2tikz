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
from ..symbols import Point, SymbolDef, lookup_symbol, rotated_size

Coordinate = tuple[float, float]

BIPOLE_NAMES: Final[dict[Kind, str]] = {
    Kind.RESISTOR: "R",
    Kind.CAPACITOR: "C",
    Kind.INDUCTOR: "L",
    Kind.DIODE: "D",
    Kind.VSOURCE: "vsource",
    Kind.ISOURCE: "isource",
    Kind.VCVS: "vcontrolledsource",
    Kind.CCVS: "vcontrolledsource",
    Kind.VCCS: "cccs",
    Kind.CCCS: "cccs",
    Kind.SWITCH: "switch",
    Kind.TLINE: "tline",
    Kind.GENERIC: "generic",
}
"""Best-effort ``kind`` → circuitikz bipole name; the controlled-source and
switch mappings are unverified against a compiler and may be revisited when
CI starts compiling goldens (roadmap 2.5)."""

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
_NODE_LABEL_POSITION: Final[dict[str | None, str]] = {
    None: "above",
    "auto": "above",
    "above": "above",
    "below": "below",
    "left": "left",
    "right": "right",
}
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


def _default_side(a: Point, b: Point) -> str:
    """Return the compass side circuitikz's default label key (``l=``) uses.

    circuitikz places the default label 90° counterclockwise from the
    direction of travel from *a* to *b*.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx, sy = -dy, dx
    if abs(sx) >= abs(sy):
        return "west" if sx < 0 else "east"
    return "south" if sy < 0 else "north"


def _resolve_side(
    default_key: str, flipped_key: str, side: str | None, a: Point, b: Point
) -> str:
    """Return which of *default_key* / *flipped_key* matches *side*.

    A *side* on the axis perpendicular to the path resolves to whichever key
    matches; a *side* on the wrong axis (e.g. "above" on a vertical path)
    falls back to *default_key*, since neither key can express it.
    """
    if side is None or side == "auto":
        return default_key
    compass = _SIDE_COMPASS[side]
    default_compass = _default_side(a, b)
    if compass == default_compass:
        return default_key
    if compass == _OPPOSITE_COMPASS[default_compass]:
        return flipped_key
    return default_key


def _resolve_path_label(
    spec: LabelSpec | None,
    key: str,
    flip_key: str,
    a: Point,
    b: Point,
    derived: str | None,
) -> tuple[str, str] | None:
    """Resolve a path-component label to its ``(option key, text)`` pair."""
    text = _resolve_label(spec, derived)
    if text is None:
        return None
    side = spec.side if spec is not None else None
    return _resolve_side(key, flip_key, side, a, b), text


def _node_label_option(spec: LabelSpec | None, derived: str | None) -> str | None:
    text = _resolve_label(spec, derived)
    if text is None:
        return None
    side = spec.side if spec is not None else None
    position = _NODE_LABEL_POSITION.get(side, "above")
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


def _emit_path(el: PathComponent, style: StyleDefaults) -> list[str]:
    bipole = BIPOLE_NAMES.get(el.kind, "generic")
    ref_derived = derive_ref_label(el.ref) if style.label_refs else None
    label = _resolve_path_label(el.label, "l", "l_", el.a, el.b, ref_derived)
    if label is not None and label[0] == "l":
        options = [f"{bipole}={label[1]}"]
    else:
        options = [bipole]
        if label is not None:
            options.append(f"{label[0]}={label[1]}")
    value = _resolve_path_label(el.value_label, "a", "a_", el.a, el.b, None)
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


def _emit_shape_node(el: NodeComponent, base: str, style: StyleDefaults) -> list[str]:
    options = [base]
    if el.mirror:
        options.append("xscale=-1")
    if el.rot:
        options.append(f"rotate={el.rot}")
    ref_derived = derive_ref_label(el.ref) if style.label_refs else None
    label_opt = _node_label_option(el.label, ref_derived)
    if label_opt is not None:
        options.append(label_opt)
    options.extend(_style_options(el.style))
    return [f"\\node[{', '.join(options)}] at ({_fmt_point(el.at)}) {{}};"]


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


def _emit_node(el: NodeComponent, ir: SchematicIR, style: StyleDefaults) -> list[str]:
    symbol = lookup_symbol(el.symbol, ir.symbols)
    if symbol is not None and symbol.base is not None:
        return _emit_shape_node(el, symbol.base, style)
    return _emit_generic_box(el, symbol, style)


def _emit_element(element: Element, ir: SchematicIR, style: StyleDefaults) -> list[str]:
    if isinstance(element, PathComponent):
        return _emit_path(element, style)
    if isinstance(element, NodeComponent):
        return _emit_node(element, ir, style)
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
    r"""Return ``\ctikzset`` overrides for non-default variants (D11).

    circuitikz's own default is the European resistor/capacitor style, which
    is why the SPEC_IR §5 golden emission needs no override at all; only the
    American variant needs an explicit switch.
    """
    options = []
    if style.resistor_variant == "american":
        options.append("\\ctikzset{american resistors}")
    if style.capacitor_variant == "american":
        options.append("\\ctikzset{american capacitors}")
    return options


# --- document assembly -------------------------------------------------------


def emit_snippet(ir: SchematicIR) -> str:
    """Render ``ir.sheets[0]`` as a circuitikz snippet (no document wrapper, D13)."""
    style = ir.effective_style()
    sheet = ir.sheets[0] if ir.sheets else Sheet()
    lines = [f"\\begin{{circuitikz}}[scale={_format_number(ir.meta.grid.pitch)}]"]
    lines.extend(f"  {line}" for line in _variant_options(style))
    for element in sheet.elements:
        lines.extend(f"  {line}" for line in _emit_element(element, ir, style))
    lines.append("\\end{circuitikz}")
    return "\n".join(lines) + "\n"


def emit_standalone(ir: SchematicIR) -> str:
    """Wrap :func:`emit_snippet` in a compilable standalone document (D13)."""
    style = ir.effective_style()
    lines = [
        "\\documentclass[tikz, border=2pt]{standalone}",
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
