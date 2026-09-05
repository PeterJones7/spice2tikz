"""Symbol definitions and the placement maths for node components.

A :class:`SymbolDef` is the geometry of a multi-terminal component: a
bounding box centred on the origin plus one offset per pin, both in grid
units (``docs/SPEC_IR.md`` §2).  Node components place a symbol at an
integer position with a rotation and an optional mirror, and
:func:`resolve_pins` turns that into absolute pin coordinates.

Coordinates are y-up (design decision D5), rotation is counterclockwise,
and the mirror flips across the vertical axis *before* rotation — exactly
the order the spec mandates.

Built-in symbols live in :data:`BUILTIN_SYMBOLS`.  A schematic file may
override a built-in or add its own symbols, so that a file renders
identically forever without depending on tool internals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

from ._serde import (
    IRError,
    check_keys,
    optional_field,
    require_field,
    require_list,
    require_mapping,
    require_number,
    require_str,
)
from .netlist_ir import Kind

Point = tuple[int, int]
Rotation = Literal[0, 90, 180, 270]
ROTATIONS: Final[tuple[int, ...]] = (0, 90, 180, 270)


@dataclass
class PinDef:
    """One pin of a symbol: its offset from the origin, unrotated."""

    offset: Point
    label: str | None = None
    anchor: str | None = None
    """The circuitikz anchor this pin is drawn from, when the pin's own name
    is not it.

    A transistor's pins are named in the tool's own vocabulary, so ``d`` finds
    the ``D`` anchor through :data:`BASE_PIN_ANCHORS` and this stays unset.  A
    subcircuit's pins are named by whoever wrote the deck — ``PLUS``, ``IN+``,
    ``VP`` — and no table can map those, so the symbol that gives such a pin a
    position says which anchor it belongs to at the same time.  That keeps the
    *netlist's* pin names on the sheet, which is what lets the drawing be
    checked back against the circuit.
    """

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting an absent label."""
        data: dict[str, Any] = {"offset": list(self.offset)}
        if self.label is not None:
            data["label"] = self.label
        if self.anchor is not None:
            data["anchor"] = self.anchor
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> PinDef:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("offset", "label", "anchor"), location, warnings)
        offset = require_point(
            require_field(mapping, "offset", location), f"{location}.offset"
        )
        raw_label = optional_field(mapping, "label", location)
        label = (
            None if raw_label is None else require_str(raw_label, f"{location}.label")
        )
        raw_anchor = optional_field(mapping, "anchor", location)
        anchor = (
            None
            if raw_anchor is None
            else require_str(raw_anchor, f"{location}.anchor")
        )
        return cls(offset=offset, label=label, anchor=anchor)


@dataclass
class SymbolDef:
    """The geometry of one symbol: bounding box and pin offsets."""

    size: Point
    pins: dict[str, PinDef] = field(default_factory=dict)
    base: str | None = None
    """Name of the circuitikz node shape to draw, when one exists."""

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order (``base``, ``size``, ``pins``)."""
        data: dict[str, Any] = {}
        if self.base is not None:
            data["base"] = self.base
        data["size"] = list(self.size)
        data["pins"] = {name: pin.to_json() for name, pin in self.pins.items()}
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> SymbolDef:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("base", "size", "pins"), location, warnings)
        raw_base = optional_field(mapping, "base", location)
        base = None if raw_base is None else require_str(raw_base, f"{location}.base")
        size = require_point(
            require_field(mapping, "size", location), f"{location}.size"
        )
        pins_mapping = require_mapping(
            require_field(mapping, "pins", location), f"{location}.pins"
        )
        pins = {
            name: PinDef.from_json(pin, f"{location}.pins.{name}", warnings)
            for name, pin in pins_mapping.items()
        }
        return cls(size=size, pins=pins, base=base)


def require_point(value: Any, location: str) -> Point:  # noqa: ANN401
    """Return *value* as an integer coordinate pair.

    Whole numbers written as floats (``4.0``) are normalised to ``int``.
    Fractional coordinates are kept verbatim so that ``validate.py`` can
    report them as invariant 6 errors rather than the loader rejecting the
    file outright.
    """
    items = require_list(value, location)
    if len(items) != 2:
        raise IRError(f"{location}: expected a coordinate pair [x, y]")
    coords = []
    for index, item in enumerate(items):
        number = require_number(item, f"{location}[{index}]")
        coords.append(int(number) if number.is_integer() else number)
    return cast(Point, tuple(coords))


def require_rotation(value: Any, location: str) -> Rotation:  # noqa: ANN401
    """Return *value* as one of the four permitted rotations."""
    number = require_number(value, location)
    if not number.is_integer() or int(number) not in ROTATIONS:
        raise IRError(f"{location}: expected one of 0, 90, 180, 270, got {value!r}")
    return cast(Rotation, int(number))


def transform_offset(offset: Point, rot: int, mirror: bool) -> Point:
    """Apply *mirror* then a counterclockwise rotation *rot* to *offset*."""
    if rot not in ROTATIONS:
        raise ValueError(f"rotation must be one of {ROTATIONS}, got {rot!r}")
    x, y = offset
    if mirror:
        x = -x
    if rot == 90:
        x, y = -y, x
    elif rot == 180:
        x, y = -x, -y
    elif rot == 270:
        x, y = y, -x
    return (x, y)


def resolve_pins(
    symbol: SymbolDef, at: Point, rot: int, mirror: bool
) -> dict[str, Point]:
    """Return absolute pin positions for *symbol* placed at *at*.

    The mirror is applied across the vertical axis before the counterclockwise
    rotation, then the result is translated by *at*.  Pin order follows the
    symbol definition.
    """
    origin_x, origin_y = at
    resolved: dict[str, Point] = {}
    for name, pin in symbol.pins.items():
        dx, dy = transform_offset(pin.offset, rot, mirror)
        resolved[name] = (origin_x + dx, origin_y + dy)
    return resolved


def rotated_size(size: Point, rot: int) -> Point:
    """Return the bounding box of *size* after a rotation by *rot*."""
    if rot not in ROTATIONS:
        raise ValueError(f"rotation must be one of {ROTATIONS}, got {rot!r}")
    width, height = size
    return (height, width) if rot in (90, 270) else (width, height)


def lookup_symbol(
    name: str, library: dict[str, SymbolDef] | None = None
) -> SymbolDef | None:
    """Return the symbol *name*, preferring *library* over the built-ins."""
    if library is not None and name in library:
        return library[name]
    return BUILTIN_SYMBOLS.get(name)


def _mos(base: str, *, drain_up: bool) -> SymbolDef:
    """Build a four-terminal MOS symbol: gate left, channel terminals vertical.

    ``drain_up`` follows the circuitikz shape rather than the device: the
    ``nmos`` shape puts D at the top and S at the bottom, and ``pmos`` puts them
    the other way round, because that is how each is conventionally drawn.
    """
    top, bottom = ("d", "s") if drain_up else ("s", "d")
    return SymbolDef(
        size=(4, 4),
        pins={
            top: PinDef(offset=(0, 2), label=top.upper()),
            "g": PinDef(offset=(-2, 0), label="G"),
            bottom: PinDef(offset=(0, -2), label=bottom.upper()),
            "b": PinDef(offset=(0, 0), label="B"),
        },
        base=base,
    )


def _bjt(base: str, *, collector_up: bool) -> SymbolDef:
    """Build a bipolar symbol: base left, channel terminals vertical.

    ``collector_up`` follows the circuitikz shape: ``npn`` puts C at the top and
    E at the bottom, ``pnp`` the other way round.
    """
    top, bottom = ("c", "e") if collector_up else ("e", "c")
    return SymbolDef(
        size=(4, 4),
        pins={
            top: PinDef(offset=(0, 2), label=top.upper()),
            "b": PinDef(offset=(-2, 0), label="B"),
            bottom: PinDef(offset=(0, -2), label=bottom.upper()),
        },
        base=base,
    )


def _jfet(base: str, *, drain_up: bool) -> SymbolDef:
    """Build a three-terminal JFET symbol: gate left, channel vertical.

    ``drain_up`` follows the circuitikz shape, as for the MOS devices.
    """
    top, bottom = ("d", "s") if drain_up else ("s", "d")
    return SymbolDef(
        size=(4, 4),
        pins={
            top: PinDef(offset=(0, 2), label=top.upper()),
            "g": PinDef(offset=(-2, 0), label="G"),
            bottom: PinDef(offset=(0, -2), label=bottom.upper()),
        },
        base=base,
    )


OPAMP_BASE: Final = "op amp"
"""The circuitikz shape name; note the space."""

OPAMP_PINS: Final[tuple[str, ...]] = ("+", "-", "out", "up", "down")
"""Op-amp pins, in the order a ``.subckt`` is expected to declare its ports.

The mapping is positional and nothing else: port 1 is the non-inverting input,
port 2 the inverting input, port 3 the output, and ports 4 and 5 the positive
and negative supplies.  Port *names* are documentation for whoever wrote the
deck — ``PLUS``/``IN+``/``VP`` all mean the same thing here — so nothing tries
to read them, which is what keeps this free of synonym lists.
"""

OPAMP_OFFSETS: Final[dict[str, Point]] = {
    "+": (-4, -2),
    "-": (-4, 2),
    "out": (4, 0),
    "up": (0, 4),
    "down": (0, -4),
}
"""Where each op-amp pin sits, in grid units, unrotated.

circuitikz draws the non-inverting input *below* the inverting one, which is
its convention and not a choice made here; the offsets follow the shape, as
they must, or the leads would cross the body.  Measured from the shape itself:
at the default 0.5 cm pitch the inputs sit 2.38 units out and 0.98 up or down,
and the supplies 1.08 above and below the origin.

**No two pins share a row.**  The supplies are pushed out to four rather than
kept beside the inputs at two, because a router given two terminals on one row
will run the wire for one of them straight through the other — with ``+`` on
ground and ``down`` on the negative supply, that is a short, and the validator
cannot see it because both wires are legal on their own.
"""


def opamp_symbol(ports: Sequence[str] = OPAMP_PINS) -> SymbolDef:
    """Return op-amp geometry for *ports*, mapped onto the pins by position.

    The pins keep the subcircuit's own port names — ``PLUS`` stays ``PLUS`` —
    and each records the anchor it is drawn from, so the sheet can still be
    read back against the netlist. Ports beyond the fifth are not mapped, and
    a subcircuit that declares three is an ideal op amp with no supply
    terminals: drawing leads it has not got would be an invention.
    """
    return SymbolDef(
        size=(8, 8),
        pins={
            port: PinDef(offset=OPAMP_OFFSETS[anchor], label=anchor, anchor=anchor)
            # Extra ports are left unmapped; strict= would make that fatal.
            for port, anchor in zip(ports, OPAMP_PINS)  # noqa: B905
        },
        base=OPAMP_BASE,
    )


BUILTIN_SYMBOLS: Final[dict[str, SymbolDef]] = {
    "nmos": _mos("nmos", drain_up=True),
    "pmos": _mos("pmos", drain_up=False),
    "npn": _bjt("npn", collector_up=True),
    "pnp": _bjt("pnp", collector_up=False),
    "njfet": _jfet("njfet", drain_up=True),
    "pjfet": _jfet("pjfet", drain_up=False),
    "opamp": opamp_symbol(),
}
"""Symbols every schematic may reference without declaring them.

Offsets follow the *directions* of the circuitikz transistor anchors, measured
from the shapes themselves: the control terminal (G/B) due left of the origin,
and the two channel terminals (D/S, C/E) directly above and below it, with the
MOS bulk on the origin.  circuitikz places those anchors at non-integer grid
distances (D sits 0.77 cm out, which is 1.54 units at the default 0.5 cm
pitch), so the emitter draws a short lead from each anchor to the pin position
declared here rather than pretending the two coincide.

The p-type shapes carry their channel terminals the *other way up* from the
n-type ones — ``pmos`` and ``pjfet`` put S at the top and D at the bottom, and
``pnp`` puts E at the top and C at the bottom — because that is how each device
is conventionally drawn.  The offsets here follow the shapes, not the device:
the emitter draws a lead from each real anchor to the pin position declared
here, so an offset on the wrong side of the body produces a lead that doubles
back across the symbol.

``size`` is a deliberately conservative box: the real shapes extend only to the
left of the origin, but SPEC_IR §2 defines ``size`` as centred on it.

``opamp`` is the five-terminal form.  Unlike the transistors it is never
chosen from a component's *kind* — a subcircuit asks for it by name, with
``; symbol=opamp`` on its ``.subckt`` card — and an instance with fewer ports
gets a matching symbol from :func:`opamp_symbol` instead of this one.
"""

BASE_PIN_ANCHORS: Final[dict[str, dict[str, str]]] = {
    "nmos": {"d": "D", "g": "G", "s": "S", "b": "bulk"},
    "pmos": {"d": "D", "g": "G", "s": "S", "b": "bulk"},
    "npn": {"c": "C", "b": "B", "e": "E"},
    "pnp": {"c": "C", "b": "B", "e": "E"},
    "njfet": {"d": "D", "g": "G", "s": "S"},
    "pjfet": {"d": "D", "g": "G", "s": "S"},
    # The op amp's pins are named after its anchors, so this maps to itself.
    OPAMP_BASE: {pin: pin for pin in OPAMP_PINS},
}
"""Pin name → circuitikz node anchor, per built-in ``base`` shape.

Anchor names are the documented ones (CircuiTikZ manual §4.15.9: MOS devices
expose ``base``/``gate``/``source``/``drain``, abbreviated ``B``/``G``/``S``/
``D``, plus a ``bulk`` anchor; bipolars expose ``B``/``C``/``E``).  The emitter
needs them to draw leads from the rendered terminal to the declared pin.

The op amp exposes ``+``, ``-``, ``out``, ``up`` and ``down``.  Those names
contain characters no other anchor does, and they are used verbatim inside
``(node.+)``, which TeX accepts.
"""


def pin_anchor(
    base: str | None, pin: str, definition: PinDef | None = None
) -> str | None:
    """Return the circuitikz anchor for *pin* of shape *base*, if known.

    A pin that names its own anchor wins: that is the only way a symbol whose
    pin names come from a deck rather than from this module can be drawn.
    """
    if definition is not None and definition.anchor is not None:
        return definition.anchor
    if base is None:
        return None
    return BASE_PIN_ANCHORS.get(base, {}).get(pin)


SYMBOL_FOR_KIND: Final[dict[Kind, str]] = {
    Kind.NMOS: "nmos",
    Kind.PMOS: "pmos",
    Kind.BJT_NPN: "npn",
    Kind.BJT_PNP: "pnp",
    Kind.NJFET: "njfet",
    Kind.PJFET: "pjfet",
}
"""Default built-in symbol for the kinds that have one."""
