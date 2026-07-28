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

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting an absent label."""
        data: dict[str, Any] = {"offset": list(self.offset)}
        if self.label is not None:
            data["label"] = self.label
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
        check_keys(mapping, ("offset", "label"), location, warnings)
        offset = require_point(
            require_field(mapping, "offset", location), f"{location}.offset"
        )
        raw_label = optional_field(mapping, "label", location)
        label = (
            None if raw_label is None else require_str(raw_label, f"{location}.label")
        )
        return cls(offset=offset, label=label)


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


def _mos(base: str) -> SymbolDef:
    """Build a four-terminal MOS symbol: gate left, drain up, source down."""
    return SymbolDef(
        size=(4, 4),
        pins={
            "d": PinDef(offset=(2, 2), label="D"),
            "g": PinDef(offset=(-2, 0), label="G"),
            "s": PinDef(offset=(2, -2), label="S"),
            "b": PinDef(offset=(2, 0), label="B"),
        },
        base=base,
    )


def _bjt(base: str) -> SymbolDef:
    """Build a bipolar symbol: base left, collector up, emitter down."""
    return SymbolDef(
        size=(4, 4),
        pins={
            "c": PinDef(offset=(2, 2), label="C"),
            "b": PinDef(offset=(-2, 0), label="B"),
            "e": PinDef(offset=(2, -2), label="E"),
        },
        base=base,
    )


BUILTIN_SYMBOLS: Final[dict[str, SymbolDef]] = {
    "nmos": _mos("nmos"),
    "pmos": _mos("pmos"),
    "npn": _bjt("npn"),
    "pnp": _bjt("pnp"),
}
"""Symbols every schematic may reference without declaring them.

Offsets follow the circuitikz transistor anchors: the control terminal on
the left of the body, the two channel terminals above and below its right
edge, and the MOS bulk on the channel midpoint.  Opamps and other shapes
are deferred to a later roadmap section.
"""

SYMBOL_FOR_KIND: Final[dict[Kind, str]] = {
    Kind.NMOS: "nmos",
    Kind.PMOS: "pmos",
    Kind.BJT_NPN: "npn",
    Kind.BJT_PNP: "pnp",
}
"""Default built-in symbol for the kinds that have one."""
