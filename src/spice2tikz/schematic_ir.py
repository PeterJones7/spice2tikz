"""Schematic IR: placed components on an integer grid (``docs/SPEC_IR.md`` §2).

The Schematic IR is what the emitter renders and what users hand-tweak.  It
is fully self-contained: coordinates are integers, y-up (design decision
D5), every wire and path component is axis-aligned, and symbols that are not
built in are carried in the file itself.

Elements are discriminated on load by their ``type`` field (and ``mode`` for
components), so a sheet is a flat list of heterogeneous elements just as in
the JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, cast

from . import _serde
from ._serde import (
    IRError,
    check_header,
    check_keys,
    optional_field,
    require_bool,
    require_choice,
    require_field,
    require_list,
    require_mapping,
    require_number,
    require_str,
)
from .netlist_ir import Kind
from .symbols import (
    Point,
    Rotation,
    SymbolDef,
    require_point,
    require_rotation,
)

IR_KIND: Final = "schematic"

LabelSide = Literal["auto", "above", "below", "left", "right"]
LABEL_SIDES: Final[tuple[str, ...]] = ("auto", "above", "below", "left", "right")

NetSymbolVariant = Literal["ground", "sground", "vcc", "vee", "tap"]
NET_SYMBOL_VARIANTS: Final[tuple[str, ...]] = (
    "ground",
    "sground",
    "vcc",
    "vee",
    "tap",
)

PortDirection = Literal["left", "right", "up", "down"]
PORT_DIRECTIONS: Final[tuple[str, ...]] = ("left", "right", "up", "down")

LabelAnchor = Literal["north", "south", "east", "west", "center"]
LABEL_ANCHORS: Final[tuple[str, ...]] = ("north", "south", "east", "west", "center")

ComponentVariant = Literal["american", "european"]
COMPONENT_VARIANTS: Final[tuple[str, ...]] = ("american", "european")

InductorVariant = Literal["american", "european", "cute"]
INDUCTOR_VARIANTS: Final[tuple[str, ...]] = ("american", "european", "cute")
"""Inductors have three circuitikz styles, not two; ``cute`` is its default."""

DEFAULT_GRID_PITCH: Final = 0.5
"""Centimetres per grid unit, unless a file says otherwise."""


@dataclass
class LabelSpec:
    """How to label a component.

    An absent ``text`` means "derive it from the ref or value"; the literal
    ``"-"`` suppresses the label; anything else is used verbatim.
    """

    text: str | None = None
    side: LabelSide | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting absent fields."""
        data: dict[str, Any] = {}
        if self.text is not None:
            data["text"] = self.text
        if self.side is not None:
            data["side"] = self.side
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> LabelSpec:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("text", "side"), location, warnings)
        raw_text = optional_field(mapping, "text", location)
        raw_side = optional_field(mapping, "side", location)
        side = (
            None
            if raw_side is None
            else cast(
                LabelSide, require_choice(raw_side, LABEL_SIDES, f"{location}.side")
            )
        )
        return cls(
            text=None
            if raw_text is None
            else require_str(raw_text, f"{location}.text"),
            side=side,
        )


@dataclass
class StyleOverride:
    """Per-element style escape hatch (raw circuitikz options, per D12)."""

    circuitikz_options: str | None = None
    color: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting absent fields."""
        data: dict[str, Any] = {}
        if self.circuitikz_options is not None:
            data["circuitikz_options"] = self.circuitikz_options
        if self.color is not None:
            data["color"] = self.color
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> StyleOverride:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("circuitikz_options", "color"), location, warnings)
        raw_options = optional_field(mapping, "circuitikz_options", location)
        raw_color = optional_field(mapping, "color", location)
        return cls(
            circuitikz_options=(
                None
                if raw_options is None
                else require_str(raw_options, f"{location}.circuitikz_options")
            ),
            color=(
                None
                if raw_color is None
                else require_str(raw_color, f"{location}.color")
            ),
        )


@dataclass
class StyleDefaults:
    """Document-wide drawing style (defaults per D11 and D12)."""

    resistor_variant: ComponentVariant = "european"
    inductor_variant: InductorVariant = "cute"
    siunitx: bool = True
    label_refs: bool = True
    extra_preamble: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Serialise; ``extra_preamble`` is omitted when empty."""
        data: dict[str, Any] = {
            "resistor_variant": self.resistor_variant,
            "inductor_variant": self.inductor_variant,
            "siunitx": self.siunitx,
            "label_refs": self.label_refs,
        }
        if self.extra_preamble:
            data["extra_preamble"] = list(self.extra_preamble)
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> StyleDefaults:
        """Load from JSON; absent fields fall back to the defaults."""
        mapping = require_mapping(data, location)
        known = (
            "resistor_variant",
            "inductor_variant",
            "siunitx",
            "label_refs",
            "extra_preamble",
        )
        check_keys(mapping, known, location, warnings)
        style = cls()
        raw_resistor = optional_field(mapping, "resistor_variant", location)
        if raw_resistor is not None:
            style.resistor_variant = cast(
                ComponentVariant,
                require_choice(
                    raw_resistor, COMPONENT_VARIANTS, f"{location}.resistor_variant"
                ),
            )
        raw_inductor = optional_field(mapping, "inductor_variant", location)
        if raw_inductor is not None:
            style.inductor_variant = cast(
                InductorVariant,
                require_choice(
                    raw_inductor, INDUCTOR_VARIANTS, f"{location}.inductor_variant"
                ),
            )
        for name in ("siunitx", "label_refs"):
            raw = optional_field(mapping, name, location)
            if raw is not None:
                setattr(style, name, require_bool(raw, f"{location}.{name}"))
        raw_preamble = optional_field(mapping, "extra_preamble", location)
        if raw_preamble is not None:
            items = require_list(raw_preamble, f"{location}.extra_preamble")
            style.extra_preamble = [
                require_str(item, f"{location}.extra_preamble[{index}]")
                for index, item in enumerate(items)
            ]
        return style


@dataclass
class Grid:
    """Physical scale of the drawing grid."""

    pitch: float = DEFAULT_GRID_PITCH

    def to_json(self) -> dict[str, Any]:
        """Serialise."""
        return {"pitch": self.pitch}

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Grid:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("pitch",), location, warnings)
        raw_pitch = optional_field(mapping, "pitch", location)
        pitch = (
            DEFAULT_GRID_PITCH
            if raw_pitch is None
            else require_number(raw_pitch, f"{location}.pitch")
        )
        return cls(pitch=pitch)


@dataclass
class SchematicMeta:
    """Provenance and grid scale of a schematic document."""

    title: str | None = None
    source_netlist: str | None = None
    generator: str | None = None
    grid: Grid = field(default_factory=Grid)

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting absent optional fields."""
        data: dict[str, Any] = {}
        for name in ("title", "source_netlist", "generator"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        data["grid"] = self.grid.to_json()
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> SchematicMeta:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(
            mapping,
            ("title", "source_netlist", "generator", "grid"),
            location,
            warnings,
        )
        values: dict[str, str | None] = {}
        for name in ("title", "source_netlist", "generator"):
            raw = optional_field(mapping, name, location)
            values[name] = (
                None if raw is None else require_str(raw, f"{location}.{name}")
            )
        raw_grid = optional_field(mapping, "grid", location)
        grid = (
            Grid()
            if raw_grid is None
            else Grid.from_json(raw_grid, f"{location}.grid", warnings)
        )
        return cls(
            title=values["title"],
            source_netlist=values["source_netlist"],
            generator=values["generator"],
            grid=grid,
        )


@dataclass
class PathComponent:
    """A two-terminal component drawn along a segment (design decision D6)."""

    ref: str
    kind: Kind
    a: Point
    b: Point
    label: LabelSpec | None = None
    value_label: LabelSpec | None = None
    style: StyleOverride | None = None

    TYPE: ClassVar[str] = "component"
    MODE: ClassVar[str] = "path"

    def __post_init__(self) -> None:
        self.kind = Kind(self.kind)

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        data: dict[str, Any] = {
            "type": self.TYPE,
            "mode": self.MODE,
            "ref": self.ref,
            "kind": self.kind.value,
            "a": list(self.a),
            "b": list(self.b),
        }
        if self.label is not None:
            data["label"] = self.label.to_json()
        if self.value_label is not None:
            data["value_label"] = self.value_label.to_json()
        if self.style is not None:
            data["style"] = self.style.to_json()
        return data

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> PathComponent:
        """Load from JSON."""
        check_keys(
            data,
            ("type", "mode", "ref", "kind", "a", "b", "label", "value_label", "style"),
            location,
            warnings,
        )
        return cls(
            ref=require_str(require_field(data, "ref", location), f"{location}.ref"),
            kind=_require_kind(data, location),
            a=require_point(require_field(data, "a", location), f"{location}.a"),
            b=require_point(require_field(data, "b", location), f"{location}.b"),
            label=_optional_label(data, "label", location, warnings),
            value_label=_optional_label(data, "value_label", location, warnings),
            style=_optional_style(data, location, warnings),
        )


@dataclass
class NodeComponent:
    """A multi-terminal component placed by origin, rotation, and mirror."""

    ref: str
    kind: Kind
    symbol: str
    at: Point
    rot: Rotation = 0
    mirror: bool = False
    pins: dict[str, Point] = field(default_factory=dict)
    label: LabelSpec | None = None
    value_label: LabelSpec | None = None
    style: StyleOverride | None = None

    TYPE: ClassVar[str] = "component"
    MODE: ClassVar[str] = "node"

    def __post_init__(self) -> None:
        self.kind = Kind(self.kind)

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        data: dict[str, Any] = {
            "type": self.TYPE,
            "mode": self.MODE,
            "ref": self.ref,
            "kind": self.kind.value,
            "symbol": self.symbol,
            "at": list(self.at),
            "rot": self.rot,
            "mirror": self.mirror,
            "pins": {name: list(point) for name, point in self.pins.items()},
        }
        if self.label is not None:
            data["label"] = self.label.to_json()
        if self.value_label is not None:
            data["value_label"] = self.value_label.to_json()
        if self.style is not None:
            data["style"] = self.style.to_json()
        return data

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> NodeComponent:
        """Load from JSON."""
        check_keys(
            data,
            (
                "type",
                "mode",
                "ref",
                "kind",
                "symbol",
                "at",
                "rot",
                "mirror",
                "pins",
                "label",
                "value_label",
                "style",
            ),
            location,
            warnings,
        )
        pins_mapping = require_mapping(
            require_field(data, "pins", location), f"{location}.pins"
        )
        pins = {
            name: require_point(point, f"{location}.pins.{name}")
            for name, point in pins_mapping.items()
        }
        return cls(
            ref=require_str(require_field(data, "ref", location), f"{location}.ref"),
            kind=_require_kind(data, location),
            symbol=require_str(
                require_field(data, "symbol", location), f"{location}.symbol"
            ),
            at=require_point(require_field(data, "at", location), f"{location}.at"),
            rot=require_rotation(
                require_field(data, "rot", location), f"{location}.rot"
            ),
            mirror=require_bool(
                require_field(data, "mirror", location), f"{location}.mirror"
            ),
            pins=pins,
            label=_optional_label(data, "label", location, warnings),
            value_label=_optional_label(data, "value_label", location, warnings),
            style=_optional_style(data, location, warnings),
        )


@dataclass
class Wire:
    """An orthogonal polyline carrying one net."""

    net: str
    points: list[Point] = field(default_factory=list)

    TYPE: ClassVar[str] = "wire"

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        return {
            "type": self.TYPE,
            "net": self.net,
            "points": [list(point) for point in self.points],
        }

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> Wire:
        """Load from JSON."""
        check_keys(data, ("type", "net", "points"), location, warnings)
        items = require_list(
            require_field(data, "points", location), f"{location}.points"
        )
        return cls(
            net=require_str(require_field(data, "net", location), f"{location}.net"),
            points=[
                require_point(point, f"{location}.points[{index}]")
                for index, point in enumerate(items)
            ],
        )

    def segments(self) -> list[tuple[Point, Point]]:
        """Return the consecutive point pairs of this wire."""
        return list(zip(self.points, self.points[1:], strict=False))


@dataclass
class Junction:
    """An explicit connection dot (design decision D7)."""

    at: Point

    TYPE: ClassVar[str] = "junction"

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        return {"type": self.TYPE, "at": list(self.at)}

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> Junction:
        """Load from JSON."""
        check_keys(data, ("type", "at"), location, warnings)
        return cls(
            at=require_point(require_field(data, "at", location), f"{location}.at")
        )


@dataclass
class NetSymbol:
    """A ground, supply, or tap marker attached to a net (design decision D7)."""

    net: str
    variant: NetSymbolVariant
    at: Point
    rot: Rotation = 0
    text: str | None = None

    TYPE: ClassVar[str] = "net_symbol"

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        data: dict[str, Any] = {
            "type": self.TYPE,
            "net": self.net,
            "variant": self.variant,
            "at": list(self.at),
            "rot": self.rot,
        }
        if self.text is not None:
            data["text"] = self.text
        return data

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> NetSymbol:
        """Load from JSON."""
        check_keys(
            data, ("type", "net", "variant", "at", "rot", "text"), location, warnings
        )
        raw_text = optional_field(data, "text", location)
        return cls(
            net=require_str(require_field(data, "net", location), f"{location}.net"),
            variant=cast(
                NetSymbolVariant,
                require_choice(
                    require_field(data, "variant", location),
                    NET_SYMBOL_VARIANTS,
                    f"{location}.variant",
                ),
            ),
            at=require_point(require_field(data, "at", location), f"{location}.at"),
            rot=require_rotation(
                require_field(data, "rot", location), f"{location}.rot"
            ),
            text=None
            if raw_text is None
            else require_str(raw_text, f"{location}.text"),
        )


@dataclass
class Port:
    """A named sheet connection point."""

    name: str
    at: Point
    direction: PortDirection = "right"

    TYPE: ClassVar[str] = "port"

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        return {
            "type": self.TYPE,
            "name": self.name,
            "at": list(self.at),
            "direction": self.direction,
        }

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> Port:
        """Load from JSON."""
        check_keys(data, ("type", "name", "at", "direction"), location, warnings)
        return cls(
            name=require_str(require_field(data, "name", location), f"{location}.name"),
            at=require_point(require_field(data, "at", location), f"{location}.at"),
            direction=cast(
                PortDirection,
                require_choice(
                    require_field(data, "direction", location),
                    PORT_DIRECTIONS,
                    f"{location}.direction",
                ),
            ),
        )


@dataclass
class Label:
    """Free-standing text; ``text`` is raw LaTeX by design (D12)."""

    at: Point
    text: str
    anchor: LabelAnchor | None = None

    TYPE: ClassVar[str] = "label"

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        data: dict[str, Any] = {
            "type": self.TYPE,
            "at": list(self.at),
            "text": self.text,
        }
        if self.anchor is not None:
            data["anchor"] = self.anchor
        return data

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        location: str,
        warnings: list[str] | None = None,
    ) -> Label:
        """Load from JSON."""
        check_keys(data, ("type", "at", "text", "anchor"), location, warnings)
        raw_anchor = optional_field(data, "anchor", location)
        anchor = (
            None
            if raw_anchor is None
            else cast(
                LabelAnchor,
                require_choice(raw_anchor, LABEL_ANCHORS, f"{location}.anchor"),
            )
        )
        return cls(
            at=require_point(require_field(data, "at", location), f"{location}.at"),
            text=require_str(require_field(data, "text", location), f"{location}.text"),
            anchor=anchor,
        )


Element = PathComponent | NodeComponent | Wire | Junction | NetSymbol | Port | Label
ComponentElement = PathComponent | NodeComponent


def element_from_json(
    data: Any,  # noqa: ANN401
    location: str,
    warnings: list[str] | None = None,
) -> Element:
    """Load one element, discriminating on ``type`` (and ``mode``)."""
    mapping = require_mapping(data, location)
    element_type = require_str(
        require_field(mapping, "type", location), f"{location}.type"
    )
    if element_type == "component":
        mode = require_str(require_field(mapping, "mode", location), f"{location}.mode")
        if mode == PathComponent.MODE:
            return PathComponent.from_json(mapping, location, warnings)
        if mode == NodeComponent.MODE:
            return NodeComponent.from_json(mapping, location, warnings)
        raise IRError(f"{location}.mode: expected 'path' or 'node', got {mode!r}")
    if element_type == Wire.TYPE:
        return Wire.from_json(mapping, location, warnings)
    if element_type == Junction.TYPE:
        return Junction.from_json(mapping, location, warnings)
    if element_type == NetSymbol.TYPE:
        return NetSymbol.from_json(mapping, location, warnings)
    if element_type == Port.TYPE:
        return Port.from_json(mapping, location, warnings)
    if element_type == Label.TYPE:
        return Label.from_json(mapping, location, warnings)
    allowed = ", ".join(
        repr(name)
        for name in (
            "component",
            Wire.TYPE,
            Junction.TYPE,
            NetSymbol.TYPE,
            Port.TYPE,
            Label.TYPE,
        )
    )
    raise IRError(f"{location}.type: expected one of {allowed}, got {element_type!r}")


def element_ref(element: Element) -> str | None:
    """Return the refdes of *element* when it is a component."""
    if isinstance(element, ComponentElement):
        return element.ref
    return None


@dataclass
class Sheet:
    """One drawing sheet: a name and a flat list of elements."""

    name: str = "main"
    elements: list[Element] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order."""
        return {
            "name": self.name,
            "elements": [element.to_json() for element in self.elements],
        }

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Sheet:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("name", "elements"), location, warnings)
        items = require_list(
            require_field(mapping, "elements", location), f"{location}.elements"
        )
        return cls(
            name=require_str(
                require_field(mapping, "name", location), f"{location}.name"
            ),
            elements=[
                element_from_json(item, f"{location}.elements[{index}]", warnings)
                for index, item in enumerate(items)
            ],
        )


@dataclass
class SchematicIR:
    """A complete Schematic IR document; ``sheets[0]`` is the top sheet."""

    meta: SchematicMeta = field(default_factory=SchematicMeta)
    style: StyleDefaults | None = None
    symbols: dict[str, SymbolDef] = field(default_factory=dict)
    sheets: list[Sheet] = field(default_factory=list)

    IR: ClassVar[str] = IR_KIND
    VERSION: ClassVar[str] = "1.0"

    def to_json(self) -> dict[str, Any]:
        """Serialise the whole document in spec field order."""
        data: dict[str, Any] = {
            "ir": self.IR,
            "version": self.VERSION,
            "meta": self.meta.to_json(),
        }
        if self.style is not None:
            data["style"] = self.style.to_json()
        if self.symbols:
            data["symbols"] = {
                name: symbol.to_json() for name, symbol in self.symbols.items()
            }
        data["sheets"] = [sheet.to_json() for sheet in self.sheets]
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        warnings: list[str] | None = None,
    ) -> SchematicIR:
        """Load a whole document from JSON."""
        mapping = require_mapping(data, "<root>")
        check_header(mapping, IR_KIND, warnings)
        check_keys(
            mapping,
            ("ir", "version", "meta", "style", "symbols", "sheets"),
            "<root>",
            warnings,
        )
        raw_meta = optional_field(mapping, "meta", "<root>")
        meta = (
            SchematicMeta()
            if raw_meta is None
            else SchematicMeta.from_json(raw_meta, "meta", warnings)
        )
        raw_style = optional_field(mapping, "style", "<root>")
        style = (
            None
            if raw_style is None
            else StyleDefaults.from_json(raw_style, "style", warnings)
        )
        symbols: dict[str, SymbolDef] = {}
        raw_symbols = optional_field(mapping, "symbols", "<root>")
        if raw_symbols is not None:
            for name, symbol in require_mapping(raw_symbols, "symbols").items():
                symbols[name] = SymbolDef.from_json(symbol, f"symbols.{name}", warnings)
        sheet_items = require_list(require_field(mapping, "sheets", "<root>"), "sheets")
        sheets = [
            Sheet.from_json(item, f"sheets[{index}]", warnings)
            for index, item in enumerate(sheet_items)
        ]
        return cls(meta=meta, style=style, symbols=symbols, sheets=sheets)

    def effective_style(self) -> StyleDefaults:
        """Return the document style, or the defaults when none is declared."""
        return self.style if self.style is not None else StyleDefaults()


def dumps(ir: SchematicIR) -> str:
    """Serialise *ir* as canonical JSON text."""
    return _serde.dumps(ir.to_json())


def loads(text: str, warnings: list[str] | None = None) -> SchematicIR:
    """Load a Schematic IR document from JSON *text*."""
    return SchematicIR.from_json(_serde.loads(text), warnings)


def load(path: Path, warnings: list[str] | None = None) -> SchematicIR:
    """Load a Schematic IR document from *path*."""
    return loads(path.read_text(encoding="utf-8"), warnings)


def dump(ir: SchematicIR, path: Path) -> None:
    """Write *ir* to *path* as canonical JSON text."""
    # newline="\n" keeps the bytes identical on every platform: without
    # it Python translates "\n" to the OS line ending, and determinism
    # (CLAUDE.md working rule 4) would hold only on POSIX.
    path.write_text(dumps(ir), encoding="utf-8", newline="\n")


def _require_kind(data: dict[str, Any], location: str) -> Kind:
    text = require_str(require_field(data, "kind", location), f"{location}.kind")
    try:
        return Kind(text)
    except ValueError as exc:
        raise IRError(f"{location}.kind: unknown kind {text!r}") from exc


def _optional_label(
    data: dict[str, Any],
    key: str,
    location: str,
    warnings: list[str] | None,
) -> LabelSpec | None:
    raw = optional_field(data, key, location)
    return (
        None if raw is None else LabelSpec.from_json(raw, f"{location}.{key}", warnings)
    )


def _optional_style(
    data: dict[str, Any], location: str, warnings: list[str] | None
) -> StyleOverride | None:
    raw = optional_field(data, "style", location)
    return (
        None
        if raw is None
        else StyleOverride.from_json(raw, f"{location}.style", warnings)
    )
