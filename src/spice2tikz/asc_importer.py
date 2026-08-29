"""LTspice ``.asc`` schematic import (roadmap §3.1-3.3).

An ``.asc`` file already carries geometry, so importing one needs no layout
engine at all — this is the path that makes the tool useful at v0.1
(``docs/DESIGN.md`` §3).  The work splits in two:

*Stage 1* (:func:`parse_asc`) turns the text into :class:`AscFile`, a faithful
record-by-record view of the file with no interpretation.  Records this build
does not model are skipped with a warning rather than an error, because a
partial schematic beats none (``docs/DESIGN.md`` §6).

*Stage 2* (:func:`import_asc`) maps those records onto the Schematic IR:
LTspice's y-down 16-unit grid becomes the IR's y-up unit grid (design decision
D5), symbols become path or node components through the data table in
:data:`SYMBOL_TABLE` (``docs/DESIGN.md`` §7: per-symbol pin offsets belong in a
table, not in code), nets are inferred by connectivity, and junctions are
inferred from wire topology and then written out explicitly (D7).

Everything survivable is reported through the optional ``warnings`` sink;
:class:`spice2tikz._serde.IRError` is reserved for input that cannot be read at
all, which the CLI maps to exit code 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from ._serde import IRError
from .emit.circuitikz import derive_ref_label, escape_latex, format_quantity
from .netlist_ir import Kind
from .quantity import parse_quantity
from .schematic_ir import (
    Element,
    Junction,
    LabelSpec,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    PortDirection,
    SchematicIR,
    SchematicMeta,
    Sheet,
    StyleDefaults,
    Wire,
)
from .symbols import (
    PinDef,
    Point,
    Rotation,
    SymbolDef,
    lookup_symbol,
    resolve_pins,
)

AscPoint = tuple[int, int]
"""A coordinate in the file's own frame: integers, y-**down**, 16 units/grid."""

GRID: Final = 16
"""LTspice grid pitch in file units; one grid square is one IR unit (D5)."""

GENERATOR: Final = "spice2tikz asc_importer"
"""Written to ``meta.generator``.  Deliberately carries no version and no
timestamp: golden outputs must not churn on a release (CLAUDE.md rule 4)."""

GROUND_NET: Final = "0"
"""The LTspice flag name that means ground (``docs/DESIGN.md`` §7)."""

SYNTHETIC_NET_FORMAT: Final = "N{:03d}"
"""Name template for a net that carries no flag."""

UNKNOWN_PIN_RADIUS: Final = 160
"""How far (file units) a dangling wire end may sit from an unrecognised symbol
and still be taken for one of its pins — ten grid squares, comfortably larger
than any stock LTspice symbol and small enough not to swallow the schematic."""


# --- stage 1: raw records ----------------------------------------------------


KNOWN_RECORDS: Final[frozenset[str]] = frozenset(
    {
        "VERSION",
        "SHEET",
        "WIRE",
        "FLAG",
        "IOPIN",
        "SYMBOL",
        "SYMATTR",
        "WINDOW",
        "TEXT",
        "DATAFLAG",
        "LINE",
        "RECTANGLE",
        "CIRCLE",
    }
)
"""Record keywords this build understands, compared case-insensitively."""

_ORIENTATION_RE: Final = re.compile(r"^(R|M)(0|90|180|270)$")

IO_DIRECTIONS: Final[dict[str, PortDirection]] = {
    "in": "left",
    "out": "right",
    "bidir": "right",
}
"""``IOPIN`` direction → the side of the pin its name is drawn on.

``Port.direction`` is a drawing hint, not an electrical one: an input arrives
from the left of the sheet, so its name reads to the left of the pin.  A
bidirectional pin has no natural side and follows the output convention.
"""


class _RecordError(ValueError):
    """A single record is malformed; the caller warns and skips it."""


@dataclass(frozen=True)
class AscOrientation:
    """One of LTspice's eight symbol placements, ``R0`` … ``M270``.

    ``rot`` is the angle LTspice writes and ``mirror`` the ``M`` prefix.  In the
    file's own y-down frame the placement is *rotate, then mirror across the
    vertical axis* — see :meth:`place`.
    """

    rot: int
    mirror: bool = False

    @classmethod
    def parse(cls, text: str) -> AscOrientation:
        """Parse an orientation field such as ``R90`` or ``M180``."""
        match = _ORIENTATION_RE.match(text.strip())
        if match is None:
            raise _RecordError(f"unknown orientation {text!r}")
        return cls(rot=int(match.group(2)), mirror=match.group(1) == "M")

    @property
    def text(self) -> str:
        """Return the field as LTspice spells it."""
        return f"{'M' if self.mirror else 'R'}{self.rot}"

    def place(self, offset: AscPoint) -> AscPoint:
        """Return *offset* placed by this orientation, still in file coords.

        The eight LTspice matrices are the four rotations of the y-down frame
        followed, for an ``M`` placement, by a flip of the x axis:
        ``R90`` sends ``(x, y)`` to ``(-y, x)`` and ``M90`` sends it to
        ``(y, x)``.
        """
        x, y = offset
        if self.rot == 90:
            x, y = -y, x
        elif self.rot == 180:
            x, y = -x, -y
        elif self.rot == 270:
            x, y = y, -x
        if self.mirror:
            x = -x
        return (x, y)

    def to_ir(self) -> tuple[Rotation, bool]:
        """Return the IR ``(rot, mirror)`` equivalent of this placement.

        The IR is y-up and rotates counterclockwise with the mirror applied
        *before* the rotation (:func:`spice2tikz.symbols.transform_offset`),
        while LTspice is y-down and mirrors *after*.  Writing ``F`` for the
        y-flip that relates the two frames, the IR transform must equal
        ``F ∘ T ∘ F`` for LTspice's matrix ``T``.  Conjugating a rotation by a
        y-flip inverts it, and ``F ∘ MirrorX ∘ F = MirrorX``, so::

            F ∘ R_lt(θ) ∘ F              = R_ir(-θ)
            F ∘ (MirrorX ∘ R_lt(θ)) ∘ F  = MirrorX ∘ R_ir(-θ)
                                         = R_ir(θ) ∘ MirrorX

        which is exactly ``transform_offset(rot=θ, mirror=True)``.  Hence an
        unmirrored placement negates the angle while a mirrored one keeps it.
        """
        return ORIENTATION_TO_IR[self.text]


ORIENTATION_TO_IR: Final[dict[str, tuple[Rotation, bool]]] = {
    "R0": (0, False),
    "R90": (270, False),
    "R180": (180, False),
    "R270": (90, False),
    "M0": (0, True),
    "M90": (90, True),
    "M180": (180, True),
    "M270": (270, True),
}
"""LTspice placement → IR ``(rot, mirror)``; derived in :meth:`AscOrientation.to_ir`."""


@dataclass(frozen=True)
class AscSheet:
    """A ``SHEET`` record: sheet number and canvas size."""

    number: int
    width: int
    height: int


@dataclass(frozen=True)
class AscWire:
    """A ``WIRE`` record: one two-point segment."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def start(self) -> AscPoint:
        """Return the first endpoint."""
        return (self.x1, self.y1)

    @property
    def end(self) -> AscPoint:
        """Return the second endpoint."""
        return (self.x2, self.y2)


@dataclass(frozen=True)
class AscFlag:
    """A ``FLAG`` record: a net label, or ground when named ``0``."""

    x: int
    y: int
    name: str

    @property
    def at(self) -> AscPoint:
        """Return the flag position."""
        return (self.x, self.y)

    @property
    def is_ground(self) -> bool:
        """Return ``True`` for the ground flag."""
        return self.name == GROUND_NET


@dataclass(frozen=True)
class AscIoPin:
    """An ``IOPIN`` record: a sheet connection with a direction."""

    x: int
    y: int
    direction: str

    @property
    def at(self) -> AscPoint:
        """Return the pin position."""
        return (self.x, self.y)


@dataclass(frozen=True)
class AscWindow:
    """A ``WINDOW`` record: where a symbol draws one of its attributes.

    Purely cosmetic, so stage 2 ignores it; it is modelled anyway so that a
    round-trip view of the file is complete.
    """

    number: int
    x: int
    y: int
    justification: str
    size: int


@dataclass
class AscSymbol:
    """A ``SYMBOL`` record together with the ``SYMATTR``/``WINDOW`` lines under it."""

    name: str
    x: int
    y: int
    orientation: AscOrientation
    attrs: dict[str, str] = field(default_factory=dict)
    windows: list[AscWindow] = field(default_factory=list)

    @property
    def at(self) -> AscPoint:
        """Return the symbol placement point."""
        return (self.x, self.y)

    def attr(self, name: str) -> str | None:
        """Return the ``SYMATTR`` *name*, matched case-insensitively."""
        lowered = name.lower()
        for key, value in self.attrs.items():
            if key.lower() == lowered:
                return value
        return None

    @property
    def base_name(self) -> str:
        r"""Return the symbol name without its library path, lowercased.

        LTspice writes library symbols with a path (``Opamps\\UniversalOpamp2``)
        and preserves the case the file was saved with; the lookup table is
        keyed by the bare lowercase name.
        """
        base = self.name.replace("\\", "/").rsplit("/", 1)[-1]
        return base.lower()


@dataclass(frozen=True)
class AscText:
    """A ``TEXT`` record: a SPICE directive (``!``) or a comment (``;``)."""

    x: int
    y: int
    justification: str
    size: int
    text: str

    @property
    def is_directive(self) -> bool:
        """Return ``True`` when the text is a SPICE directive."""
        return self.text.startswith("!")


@dataclass(frozen=True)
class AscDataFlag:
    """A ``DATAFLAG`` record: a simulation probe annotation."""

    x: int
    y: int
    expression: str


@dataclass(frozen=True)
class AscShape:
    """A ``LINE``, ``RECTANGLE``, or ``CIRCLE`` record: freehand decoration."""

    shape: str
    style: str
    x1: int
    y1: int
    x2: int
    y2: int
    line_style: int | None = None


@dataclass
class AscFile:
    """Everything :func:`parse_asc` could make of one ``.asc`` file."""

    version: int | None = None
    sheets: list[AscSheet] = field(default_factory=list)
    wires: list[AscWire] = field(default_factory=list)
    flags: list[AscFlag] = field(default_factory=list)
    iopins: list[AscIoPin] = field(default_factory=list)
    symbols: list[AscSymbol] = field(default_factory=list)
    texts: list[AscText] = field(default_factory=list)
    dataflags: list[AscDataFlag] = field(default_factory=list)
    shapes: list[AscShape] = field(default_factory=list)


def decode_asc(data: bytes) -> str:
    """Decode ``.asc`` *data*, detecting the encodings found in the wild.

    LTspice writes UTF-16 LE with a byte-order mark on Windows, but plenty of
    files (and everything hand-written) are plain ASCII or Latin-1.  A BOM wins
    when present; otherwise UTF-8 is tried and Latin-1 is the fallback that
    cannot fail.
    """
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Latin-1 maps every byte, so this branch always succeeds; the file may
        # be mojibake, but a mis-decoded comment beats refusing the schematic.
        return data.decode("latin-1")


def parse_asc(text: str, warnings: list[str] | None = None) -> AscFile:
    """Parse ``.asc`` *text* into raw records (stage 1).

    Blank lines and both Windows and Unix line endings are handled.  A record
    of a known type that will not parse, and a record of an unknown type, are
    both reported through *warnings* and skipped.  Only a file with no
    recognisable record at all raises :class:`~spice2tikz._serde.IRError`.
    """
    asc = AscFile()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    recognised = 0
    symbol: AscSymbol | None = None
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        keyword = line.split(maxsplit=1)[0]
        upper = keyword.upper()
        if upper not in KNOWN_RECORDS:
            _warn(warnings, f"line {number}: unknown record type {keyword!r} ignored")
            continue
        recognised += 1
        try:
            symbol = _parse_record(asc, upper, line, symbol)
        except _RecordError as exc:
            _warn(warnings, f"line {number}: {exc} — record ignored")
    if recognised == 0:
        raise IRError("not an LTspice schematic: no recognisable .asc records")
    return asc


def _parse_record(
    asc: AscFile, keyword: str, line: str, symbol: AscSymbol | None
) -> AscSymbol | None:
    """Parse one record into *asc*; return the symbol later attributes attach to."""
    parts = line.split()
    if keyword == "VERSION":
        asc.version = _require_int(parts, 1, "version")
    elif keyword == "SHEET":
        asc.sheets.append(
            AscSheet(
                number=_require_int(parts, 1, "sheet number"),
                width=_require_int(parts, 2, "sheet width"),
                height=_require_int(parts, 3, "sheet height"),
            )
        )
    elif keyword == "WIRE":
        asc.wires.append(
            AscWire(
                x1=_require_int(parts, 1, "x1"),
                y1=_require_int(parts, 2, "y1"),
                x2=_require_int(parts, 3, "x2"),
                y2=_require_int(parts, 4, "y2"),
            )
        )
    elif keyword == "FLAG":
        asc.flags.append(
            AscFlag(
                x=_require_int(parts, 1, "x"),
                y=_require_int(parts, 2, "y"),
                name=_tail(line, 3, "flag name"),
            )
        )
    elif keyword == "IOPIN":
        direction = _tail(line, 3, "pin direction")
        if direction.lower() not in IO_DIRECTIONS:
            raise _RecordError(f"unknown IOPIN direction {direction!r}")
        asc.iopins.append(
            AscIoPin(
                x=_require_int(parts, 1, "x"),
                y=_require_int(parts, 2, "y"),
                direction=direction,
            )
        )
    elif keyword == "SYMBOL":
        # The name may itself contain spaces, so it is whatever sits between the
        # keyword and the three trailing fields.
        if len(parts) < 5:
            raise _RecordError("SYMBOL needs a name, x, y, and an orientation")
        symbol = AscSymbol(
            name=" ".join(parts[1:-3]),
            x=_require_int(parts, len(parts) - 3, "x"),
            y=_require_int(parts, len(parts) - 2, "y"),
            orientation=AscOrientation.parse(parts[-1]),
        )
        asc.symbols.append(symbol)
    elif keyword == "SYMATTR":
        if symbol is None:
            raise _RecordError("SYMATTR before any SYMBOL")
        if len(parts) < 2:
            raise _RecordError("SYMATTR needs an attribute name")
        symbol.attrs[parts[1]] = _tail(line, 2, "attribute value", allow_empty=True)
    elif keyword == "WINDOW":
        if symbol is None:
            raise _RecordError("WINDOW before any SYMBOL")
        symbol.windows.append(
            AscWindow(
                number=_require_int(parts, 1, "window number"),
                x=_require_int(parts, 2, "x"),
                y=_require_int(parts, 3, "y"),
                justification=_field(parts, 4, "justification"),
                size=_require_int(parts, 5, "size"),
            )
        )
    elif keyword == "TEXT":
        asc.texts.append(
            AscText(
                x=_require_int(parts, 1, "x"),
                y=_require_int(parts, 2, "y"),
                justification=_field(parts, 3, "justification"),
                size=_require_int(parts, 4, "size"),
                text=_tail(line, 5, "text", allow_empty=True),
            )
        )
    elif keyword == "DATAFLAG":
        asc.dataflags.append(
            AscDataFlag(
                x=_require_int(parts, 1, "x"),
                y=_require_int(parts, 2, "y"),
                expression=_tail(line, 3, "expression", allow_empty=True),
            )
        )
    else:  # LINE, RECTANGLE, CIRCLE
        asc.shapes.append(
            AscShape(
                shape=keyword.lower(),
                style=_field(parts, 1, "style"),
                x1=_require_int(parts, 2, "x1"),
                y1=_require_int(parts, 3, "y1"),
                x2=_require_int(parts, 4, "x2"),
                y2=_require_int(parts, 5, "y2"),
                line_style=int(parts[6])
                if len(parts) > 6 and _is_int(parts[6])
                else None,
            )
        )
    return symbol


def _field(parts: list[str], index: int, name: str) -> str:
    if index >= len(parts):
        raise _RecordError(f"missing {name}")
    return parts[index]


def _require_int(parts: list[str], index: int, name: str) -> int:
    text = _field(parts, index, name)
    if not _is_int(text):
        raise _RecordError(f"{name} {text!r} is not an integer")
    return int(text)


def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", text))


def _tail(line: str, index: int, name: str, *, allow_empty: bool = False) -> str:
    """Return everything from token *index* onwards, verbatim."""
    parts = line.split(maxsplit=index)
    if len(parts) <= index:
        if allow_empty:
            return ""
        raise _RecordError(f"missing {name}")
    return parts[index]


def _warn(warnings: list[str] | None, message: str) -> None:
    """Append *message* to the sink when the caller supplied one."""
    if warnings is not None:
        warnings.append(message)


# --- stage 2: the symbol table ----------------------------------------------


@dataclass(frozen=True)
class AscSymbolDef:
    """How one LTspice symbol maps onto the Schematic IR.

    ``pins`` are the LTspice ``PIN`` offsets from the symbol placement point, in
    file units and y-down, paired with the IR pin name they carry.  For a
    two-terminal symbol the names are ``a`` and ``b`` in SPICE card order, so
    ``a`` is the positive terminal or the anode (``docs/SPEC_IR.md`` §2).  A
    symbol with a ``symbol`` set becomes a node component drawn with that
    built-in, and ``origin`` says which symbol-local point the built-in's origin
    sits on.
    """

    kind: Kind
    pins: tuple[tuple[str, AscPoint], ...]
    prefix: str
    unit: str | None = None
    symbol: str | None = None
    origin: AscPoint | None = None

    @property
    def is_node(self) -> bool:
        """Return ``True`` when this symbol becomes a node component (D6)."""
        return self.symbol is not None


def _two_terminal(
    kind: Kind, a: AscPoint, b: AscPoint, prefix: str, unit: str | None = None
) -> AscSymbolDef:
    """Build a path-component entry from its two LTspice pin offsets."""
    return AscSymbolDef(kind=kind, pins=(("a", a), ("b", b)), prefix=prefix, unit=unit)


def _mos(symbol: str, *, bulk: bool) -> AscSymbolDef:
    """Build a MOS entry; ``nmos``/``pmos`` differ from the 4-pin forms only in B."""
    pins: tuple[tuple[str, AscPoint], ...] = (
        ("d", (48, 0)),
        ("g", (0, 80)),
        ("s", (48, 96)),
    )
    if bulk:
        pins += (("b", (48, 48)),)
    return AscSymbolDef(
        kind=Kind.NMOS if symbol == "nmos" else Kind.PMOS,
        pins=pins,
        prefix="M",
        symbol=symbol,
        origin=(48, 48),
    )


def _bjt(symbol: str) -> AscSymbolDef:
    """Build a bipolar entry."""
    return AscSymbolDef(
        kind=Kind.BJT_NPN if symbol == "npn" else Kind.BJT_PNP,
        pins=(("c", (64, 0)), ("b", (0, 48)), ("e", (64, 96))),
        prefix="Q",
        symbol=symbol,
        origin=(64, 48),
    )


SYMBOL_TABLE: Final[dict[str, AscSymbolDef]] = {
    "res": _two_terminal(Kind.RESISTOR, (16, 16), (16, 96), "R", "ohm"),
    "res2": _two_terminal(Kind.RESISTOR, (16, 0), (16, 64), "R", "ohm"),
    "cap": _two_terminal(Kind.CAPACITOR, (16, 0), (16, 64), "C", "F"),
    "polcap": _two_terminal(Kind.CAPACITOR, (16, 0), (16, 64), "C", "F"),
    "ind": _two_terminal(Kind.INDUCTOR, (16, 16), (16, 96), "L", "H"),
    "ind2": _two_terminal(Kind.INDUCTOR, (16, 16), (16, 96), "L", "H"),
    "diode": _two_terminal(Kind.DIODE, (16, 0), (16, 64), "D"),
    "zener": _two_terminal(Kind.DIODE, (16, 0), (16, 64), "D"),
    "voltage": _two_terminal(Kind.VSOURCE, (0, 16), (0, 96), "V", "V"),
    "current": _two_terminal(Kind.ISOURCE, (0, 0), (0, 80), "I", "A"),
    "nmos": _mos("nmos", bulk=False),
    "nmos4": _mos("nmos", bulk=True),
    "pmos": _mos("pmos", bulk=False),
    "pmos4": _mos("pmos", bulk=True),
    "npn": _bjt("npn"),
    "pnp": _bjt("pnp"),
}
"""LTspice symbol name → IR mapping, keyed by bare lowercase name.

Pin offsets are the ``PIN`` records of the stock LTspice symbol library, which
is why they look irregular: ``res`` runs from (16, 16) to (16, 96) while
``res2`` runs from (16, 0) to (16, 64), and a MOSFET's gate attaches two grid
squares *below* the channel centre rather than level with it.  Encoding those
quirks as data instead of logic is exactly what ``docs/DESIGN.md`` §7 asks for.
"""


# --- stage 2: coordinates ----------------------------------------------------


def _scale(value: int) -> int:
    """Return *value* in IR units, rounding halves away from zero."""
    quotient, remainder = divmod(value, GRID)
    if remainder * 2 > GRID or (remainder * 2 == GRID and value > 0):
        quotient += 1
    return quotient


def _to_ir(point: AscPoint) -> Point:
    """Return *point* in IR coordinates: 16 file units per unit, y flipped (D5)."""
    return (_scale(point[0]), -_scale(point[1]))


def _off_grid(point: AscPoint) -> bool:
    return point[0] % GRID != 0 or point[1] % GRID != 0


def _check_grid(asc: AscFile, warnings: list[str] | None) -> None:
    """Warn about coordinates that do not sit on LTspice's own 16-unit grid.

    LTspice snaps everything it places, so an off-grid coordinate means the file
    was edited by hand or by another tool.  Rounding to the nearest grid point
    keeps the schematic importable (``docs/DESIGN.md`` §6) but can move a pin,
    so it is never silent.
    """
    seen: set[str] = set()

    def report(point: AscPoint, what: str) -> None:
        if not _off_grid(point):
            return
        message = (
            f"{what} at ({point[0]}, {point[1]}) is not on the 16-unit grid; "
            f"rounded to {_to_ir(point)}"
        )
        if message not in seen:
            seen.add(message)
            _warn(warnings, message)

    for wire in asc.wires:
        report(wire.start, "wire end")
        report(wire.end, "wire end")
    for flag in asc.flags:
        report(flag.at, f"flag {flag.name!r}")
    for iopin in asc.iopins:
        report(iopin.at, "io pin")
    for symbol in asc.symbols:
        report(symbol.at, f"symbol {symbol.name!r}")


def _reading_key(point: AscPoint) -> AscPoint:
    """Return the sort key that reads a sheet top-to-bottom, then left-to-right."""
    return (point[1], point[0])


def _on_segment(point: AscPoint, start: AscPoint, end: AscPoint) -> bool:
    """Return ``True`` when *point* lies on the closed segment ``start``-``end``."""
    if start[0] == end[0]:
        return point[0] == start[0] and (
            min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )
    if start[1] == end[1]:
        return point[1] == start[1] and (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
        )
    return False


def _lead_points(start: Point, end: Point) -> list[Point]:
    """Return an orthogonal polyline from *start* to *end*.

    The longer axis is travelled first, so the final segment runs along the
    shorter one — which is the direction the pin points, and therefore the
    direction the schematic's own wire arrives from.
    """
    if start == end:
        return []
    if start[0] == end[0] or start[1] == end[1]:
        return [start, end]
    if abs(end[1] - start[1]) >= abs(end[0] - start[0]):
        return [start, (start[0], end[1]), end]
    return [start, (end[0], start[1]), end]


# --- stage 2: connectivity ---------------------------------------------------


class _UnionFind:
    """Minimal union-find over LTspice points, used to infer nets."""

    def __init__(self) -> None:
        self._parent: dict[AscPoint, AscPoint] = {}

    def add(self, point: AscPoint) -> None:
        """Register *point* as its own set when it is new."""
        self._parent.setdefault(point, point)

    def find(self, point: AscPoint) -> AscPoint:
        """Return the representative of *point*'s set."""
        self.add(point)
        root = point
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[point] != root:
            self._parent[point], point = root, self._parent[point]
        return root

    def union(self, first: AscPoint, second: AscPoint) -> None:
        """Merge the sets of *first* and *second*."""
        root_a, root_b = self.find(first), self.find(second)
        if root_a != root_b:
            # The smaller point always wins, so the forest — and therefore every
            # derived name — is a pure function of the input.
            low, high = sorted((root_a, root_b))
            self._parent[high] = low

    def groups(self) -> dict[AscPoint, list[AscPoint]]:
        """Return every set, keyed by representative, members in sorted order."""
        result: dict[AscPoint, list[AscPoint]] = {}
        for point in sorted(self._parent):
            result.setdefault(self.find(point), []).append(point)
        return result


def _build_nets(
    asc: AscFile,
    pin_points: list[AscPoint],
    warnings: list[str] | None,
) -> dict[AscPoint, str]:
    """Return the net name of every connection point, in LTspice coordinates.

    Connectivity is computed before any rescaling, so it is exact: two things
    are on the same net when a wire joins them, or when one sits anywhere on a
    wire (LTspice's own T-junction rule).  A net takes the name of its first
    flag in file order, ``"0"`` when any of its flags is the ground flag, and
    otherwise a synthetic ``N001`` numbered in reading order of the net's
    topmost-leftmost point.
    """
    union = _UnionFind()
    points: set[AscPoint] = set(pin_points)
    for wire in asc.wires:
        union.add(wire.start)
        union.add(wire.end)
        union.union(wire.start, wire.end)
        points.update((wire.start, wire.end))
    for flag in asc.flags:
        points.add(flag.at)
    for iopin in asc.iopins:
        points.add(iopin.at)
    for point in sorted(points):
        union.add(point)
        for wire in asc.wires:
            if _on_segment(point, wire.start, wire.end):
                union.union(point, wire.start)

    flag_names: dict[AscPoint, list[str]] = {}
    for flag in asc.flags:
        flag_names.setdefault(union.find(flag.at), []).append(flag.name)

    groups = union.groups()
    named: dict[AscPoint, str] = {}
    for root, names in flag_names.items():
        chosen = GROUND_NET if GROUND_NET in names else names[0]
        distinct = sorted(set(names))
        if len(distinct) > 1:
            _warn(
                warnings,
                f"net labelled {', '.join(repr(name) for name in distinct)} by "
                f"several flags; using {chosen!r}",
            )
        named[root] = chosen

    unnamed = sorted(
        (root for root in groups if root not in named),
        key=lambda root: _reading_key(min(groups[root], key=_reading_key)),
    )
    for index, root in enumerate(unnamed, start=1):
        named[root] = SYNTHETIC_NET_FORMAT.format(index)

    return {point: named[root] for root, group in groups.items() for point in group}


# --- stage 2: symbol placement ----------------------------------------------


@dataclass
class _Placed:
    """One ``SYMBOL`` record resolved against the table."""

    symbol: AscSymbol
    ref: str
    definition: AscSymbolDef | None
    pins: dict[str, AscPoint] = field(default_factory=dict)
    """IR pin name → the point LTspice puts that pin at, in file coordinates."""


def _assign_refs(asc: AscFile, warnings: list[str] | None) -> list[_Placed]:
    """Resolve every symbol against the table and give it a refdes."""
    placed: list[_Placed] = []
    counters: dict[str, int] = {}
    for symbol in asc.symbols:
        definition = SYMBOL_TABLE.get(symbol.base_name)
        ref = symbol.attr("InstName")
        if not ref:
            prefix = definition.prefix if definition is not None else "X"
            counters[prefix] = counters.get(prefix, 0) + 1
            ref = f"{prefix}{counters[prefix]}"
            _warn(
                warnings,
                f"symbol {symbol.name!r} at {symbol.at} has no InstName; named {ref!r}",
            )
        pins: dict[str, AscPoint] = {}
        if definition is not None:
            for name, offset in definition.pins:
                dx, dy = symbol.orientation.place(offset)
                pins[name] = (symbol.x + dx, symbol.y + dy)
        placed.append(_Placed(symbol=symbol, ref=ref, definition=definition, pins=pins))
    return placed


def _generate_box_symbols(
    asc: AscFile,
    placed: list[_Placed],
    library: dict[str, SymbolDef],
    warnings: list[str] | None,
) -> dict[str, tuple[str, Point]]:
    """Invent a box symbol for every unrecognised ``SYMBOL``.

    An ``.asc`` file names its symbols but does not carry their geometry — that
    lives in the ``.asy`` files — so an unknown symbol's pins have to be
    recovered from the drawing: every wire end that nothing else explains and
    that sits within :data:`UNKNOWN_PIN_RADIUS` of the symbol becomes a pin.  The
    generated :class:`~spice2tikz.symbols.SymbolDef` goes into the document's own
    ``symbols`` block so the file stays self-contained (``docs/SPEC_IR.md`` §2),
    and the instance is placed unrotated because the geometry was synthesised
    from already-placed points.  Returns ref → (symbol name, IR origin).
    """
    unknown = [item for item in placed if item.definition is None]
    if not unknown:
        return {}
    explained: set[AscPoint] = {
        point for item in placed for point in item.pins.values()
    }
    explained.update(flag.at for flag in asc.flags)
    explained.update(iopin.at for iopin in asc.iopins)

    loose: list[AscPoint] = []
    for index, wire in enumerate(asc.wires):
        for end in (wire.start, wire.end):
            if end in explained:
                continue
            if any(
                other_index != index and _on_segment(end, other.start, other.end)
                for other_index, other in enumerate(asc.wires)
            ):
                continue
            if end not in loose:
                loose.append(end)

    claimed: dict[int, list[AscPoint]] = {}
    for point in sorted(loose):
        best: int | None = None
        best_distance = UNKNOWN_PIN_RADIUS + 1
        for index, item in enumerate(unknown):
            distance = abs(point[0] - item.symbol.x) + abs(point[1] - item.symbol.y)
            if distance < best_distance:
                best, best_distance = index, distance
        if best is not None:
            claimed.setdefault(best, []).append(point)

    result: dict[str, tuple[str, Point]] = {}
    for index, item in enumerate(unknown):
        points = sorted(claimed.get(index, []), key=_reading_key)
        ir_points = [_to_ir(point) for point in points]
        at = _box_centre(ir_points, _to_ir(item.symbol.at))
        offsets = [(point[0] - at[0], point[1] - at[1]) for point in ir_points]
        half_w = max([abs(offset[0]) for offset in offsets] + [1])
        half_h = max([abs(offset[1]) for offset in offsets] + [1])
        definition = SymbolDef(
            size=(2 * half_w, 2 * half_h),
            pins={
                str(number): PinDef(offset=offset)
                for number, offset in enumerate(offsets, start=1)
            },
        )
        name = _register_symbol(library, f"subckt:{item.symbol.base_name}", definition)
        result[item.ref] = (name, at)
        _warn(
            warnings,
            f"unknown symbol {item.symbol.name!r} ({item.ref}): drawn as a "
            f"generic box with {len(points)} inferred pin(s)",
        )
    return result


def _box_centre(points: list[Point], fallback: Point) -> Point:
    """Return the integer centre of *points*' bounding box."""
    if not points:
        return fallback
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)


def _register_symbol(
    library: dict[str, SymbolDef], name: str, definition: SymbolDef
) -> str:
    """Store *definition* under *name*, suffixing when the name is taken."""
    candidate = name
    suffix = 1
    while candidate in library:
        if library[candidate] == definition:
            return candidate
        suffix += 1
        candidate = f"{name}_{suffix}"
    library[candidate] = definition
    return candidate


# --- stage 2: element construction ------------------------------------------


_PGFKEYS_SPECIAL: Final = ",[]()="
"""Characters a derived value label must be braced against.

An LTspice ``Value`` is free text — ``SINE(0 1 1k)``, ``V=V(in)*2``, a
comma-separated parameter list — and it is emitted into a ``to[...]`` option,
where a comma ends the option outright and the rest is at best fragile.  A
``\\SI`` macro contains only balanced braces and needs no wrapping, which keeps
the common case reading the way the hand-written corpus does.
"""


def _value_label(text: str, unit: str | None) -> LabelSpec:
    """Return the value label for *text*, formatted per ``docs/SPEC_IR.md`` §3.

    ``LabelSpec.text`` is emitted verbatim, so the importer has to do the
    formatting and the escaping itself.  It reuses the emitter's own
    :func:`~spice2tikz.emit.circuitikz.format_quantity`, which renders a
    parseable value through siunitx and falls back to escaped raw text
    otherwise — duplicating either the unit macros or the D12 escape table here
    would only let the two drift apart.

    The result is wrapped in braces when it carries a character pgfkeys reads
    as structure: an LTspice value is free text and may hold a comma or a
    bracket, which would otherwise end the option it is emitted into.
    """
    formatted = format_quantity(parse_quantity(text, unit), siunitx=True)
    if any(char in formatted for char in _PGFKEYS_SPECIAL):
        formatted = f"{{{formatted}}}"
    return LabelSpec(text=formatted)


def _path_component(item: _Placed, definition: AscSymbolDef) -> PathComponent:
    """Build a two-terminal component from a placed symbol (D6)."""
    value = item.symbol.attr("Value")
    return PathComponent(
        ref=item.ref,
        kind=definition.kind,
        a=_to_ir(item.pins["a"]),
        b=_to_ir(item.pins["b"]),
        value_label=_value_label(value, definition.unit) if value else None,
    )


def _node_component(
    item: _Placed, definition: AscSymbolDef, symbol: SymbolDef
) -> NodeComponent:
    """Build a multi-terminal component from a placed symbol (D6)."""
    rot, mirror = item.symbol.orientation.to_ir()
    origin = definition.origin or (0, 0)
    dx, dy = item.symbol.orientation.place(origin)
    at = _to_ir((item.symbol.x + dx, item.symbol.y + dy))
    return NodeComponent(
        ref=item.ref,
        kind=definition.kind,
        symbol=cast(str, definition.symbol),
        at=at,
        rot=rot,
        mirror=mirror,
        # Invariant 8 demands the declared pins agree with the symbol geometry,
        # so they are computed from it rather than from the LTspice offsets;
        # _pin_leads() bridges the gap between the two.
        pins=resolve_pins(symbol, at, rot, mirror),
        label=_node_label(item),
    )


def _pin_leads(
    item: _Placed,
    element: NodeComponent,
    nets: dict[AscPoint, str],
    attached: set[AscPoint],
) -> list[Wire]:
    """Return short wires joining a node component's IR pins to LTspice's.

    The built-in transistor symbols are idealised — a 4x4 box with the channel
    terminals two units out — while LTspice's are three units out with the gate
    offset sideways.  Rather than move the schematic's own wires (which could
    make them diagonal), the importer keeps both positions and draws the stub
    between them, but only where LTspice's position actually has something
    attached; an unconnected bulk pin gets no dangling stub.
    """
    leads: list[Wire] = []
    for pin, source in item.pins.items():
        target = element.pins.get(pin)
        if target is None or source not in attached:
            continue
        points = _lead_points(target, _to_ir(source))
        if points:
            leads.append(Wire(net=nets[source], points=points))
    return leads


def _node_label(item: _Placed) -> LabelSpec | None:
    """Return the label of a node component: its refdes and, if any, its value.

    A :class:`~spice2tikz.schematic_ir.NodeComponent` has one label slot, unlike
    a path component's separate ``label`` and ``value_label``, so a transistor's
    model name joins the refdes in that one slot rather than becoming a
    free-standing :class:`~spice2tikz.schematic_ir.Label` at a position this
    importer would have to invent (and could not keep clear of the wires).
    Returning ``None`` leaves the emitter to derive the refdes label itself.
    """
    value = item.symbol.attr("Value")
    if not value:
        return None
    return LabelSpec(text=f"{derive_ref_label(item.ref)} {escape_latex(value)}")


def _junction_points(
    elements: list[Element],
) -> list[Point]:
    """Return every point that needs an explicit junction dot (D7).

    The counting rule is ``validate.py``'s, so that an imported document never
    trips invariant 10: a wire end counts one, a wire passing through counts
    two, and each component pin, port, or conducting net symbol counts one.  Net
    identity is deliberately not consulted.
    """
    wires = [element for element in elements if isinstance(element, Wire)]
    pins: list[Point] = []
    for element in elements:
        if isinstance(element, PathComponent):
            pins.extend((element.a, element.b))
        elif isinstance(element, NodeComponent):
            pins.extend(element.pins.values())
    conductors: list[Point] = [
        element.at
        for element in elements
        if isinstance(element, NetSymbol) and element.variant != "tap"
    ]
    conductors.extend(element.at for element in elements if isinstance(element, Port))

    candidates: set[Point] = set(pins) | set(conductors)
    for wire in wires:
        candidates.update(wire.points)

    junctions: list[Point] = []
    for point in sorted(candidates):
        count = pins.count(point) + conductors.count(point)
        for wire in wires:
            for start, end in wire.segments():
                if point in (start, end):
                    count += 1
                elif _between(point, start, end):
                    count += 2
        if count >= 3:
            junctions.append(point)
    return junctions


def _between(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* is strictly inside the segment."""
    if point in (start, end):
        return False
    if start[0] == end[0]:
        return point[0] == start[0] and (
            min(start[1], end[1]) < point[1] < max(start[1], end[1])
        )
    if start[1] == end[1]:
        return point[1] == start[1] and (
            min(start[0], end[0]) < point[0] < max(start[0], end[0])
        )
    return False


# --- stage 2: the importer ---------------------------------------------------


def import_asc(
    text: str,
    *,
    source: str | None = None,
    warnings: list[str] | None = None,
) -> SchematicIR:
    """Import ``.asc`` *text* as a Schematic IR document (stages 1 and 2).

    *source* is recorded in ``meta.source_netlist``; pass a bare file name
    rather than a path so that the output does not depend on where the file
    lives.  Everything recoverable is appended to *warnings*.
    """
    asc = parse_asc(text, warnings)
    _check_grid(asc, warnings)
    _warn_dropped(asc, warnings)

    placed = _assign_refs(asc, warnings)
    nets = _build_nets(
        asc,
        [point for item in placed for point in item.pins.values()],
        warnings,
    )
    library: dict[str, SymbolDef] = {}
    boxes = _generate_box_symbols(asc, placed, library, warnings)

    attached = _attached_points(asc, placed)
    components: list[Element] = []
    leads: list[Wire] = []
    for item in placed:
        element, item_leads = _build_component(item, boxes, library, nets, attached)
        components.append(element)
        leads.extend(item_leads)

    wires = _build_wires(asc, nets, warnings)
    net_symbols, ports = _build_net_symbols(asc, nets)

    # Elements are grouped by kind rather than interleaved in file order:
    # components, then conductors, then the dots that sit on top of them.  The
    # order is what the emitter draws in, and grouping keeps it predictable.
    elements: list[Element] = [*components, *wires, *leads, *net_symbols, *ports]
    elements.extend(Junction(at=point) for point in _junction_points(elements))

    return SchematicIR(
        meta=SchematicMeta(source_netlist=source, generator=GENERATOR),
        style=StyleDefaults(),
        symbols=library,
        sheets=[Sheet(name="main", elements=elements)],
    )


def _warn_dropped(asc: AscFile, warnings: list[str] | None) -> None:
    """Report the records stage 2 has nowhere to put.

    ``LINE``/``RECTANGLE``/``CIRCLE`` and ``DATAFLAG`` are visible content that
    the Schematic IR cannot express, so losing them is worth saying.  ``TEXT``
    and ``WINDOW`` are not: a ``TEXT`` record is a simulation directive or a
    comment rather than part of the circuit, and a ``WINDOW`` only says where
    LTspice drew an attribute.
    """
    for shape in ("line", "rectangle", "circle"):
        count = sum(1 for item in asc.shapes if item.shape == shape)
        if count:
            _warn(warnings, f"{count} decorative {shape} record(s) not imported")
    if asc.dataflags:
        _warn(
            warnings,
            f"{len(asc.dataflags)} DATAFLAG record(s) not imported: the "
            "Schematic IR has no probe annotations",
        )


def _attached_points(asc: AscFile, placed: list[_Placed]) -> set[AscPoint]:
    """Return the LTspice points that something is actually connected to."""
    attached: set[AscPoint] = set()
    for wire in asc.wires:
        attached.update((wire.start, wire.end))
    attached.update(flag.at for flag in asc.flags)
    attached.update(iopin.at for iopin in asc.iopins)
    counts: dict[AscPoint, int] = {}
    for item in placed:
        for point in item.pins.values():
            counts[point] = counts.get(point, 0) + 1
    attached.update(point for point, count in counts.items() if count > 1)
    # A pin that sits partway along a wire is connected too (LTspice's T rule).
    for point in list(counts):
        if any(_on_segment(point, wire.start, wire.end) for wire in asc.wires):
            attached.add(point)
    return attached


def _build_component(
    item: _Placed,
    boxes: dict[str, tuple[str, Point]],
    library: dict[str, SymbolDef],
    nets: dict[AscPoint, str],
    attached: set[AscPoint],
) -> tuple[Element, list[Wire]]:
    """Build one component element plus the stubs joining it to the drawing."""
    if item.definition is None:
        name, at = boxes[item.ref]
        symbol = library[name]
        return (
            NodeComponent(
                ref=item.ref,
                kind=Kind.SUBCIRCUIT,
                symbol=name,
                at=at,
                rot=0,
                mirror=False,
                pins=resolve_pins(symbol, at, 0, False),
                label=_node_label(item),
            ),
            [],
        )
    if not item.definition.is_node:
        return _path_component(item, item.definition), []
    symbol = cast(SymbolDef, lookup_symbol(cast(str, item.definition.symbol)))
    node = _node_component(item, item.definition, symbol)
    return node, _pin_leads(item, node, nets, attached)


def _build_wires(
    asc: AscFile, nets: dict[AscPoint, str], warnings: list[str] | None
) -> list[Wire]:
    """Turn ``WIRE`` records into IR wires, one per record.

    Collinear segments are deliberately not merged: LTspice splits a wire
    wherever a branch joins it, and keeping the split preserves the file's own
    topology (and therefore its diffs) exactly.
    """
    wires: list[Wire] = []
    for wire in asc.wires:
        if wire.start == wire.end:
            _warn(warnings, f"zero-length wire at {wire.start} ignored")
            continue
        if wire.x1 != wire.x2 and wire.y1 != wire.y2:
            _warn(
                warnings,
                f"diagonal wire {wire.start} to {wire.end} ignored: the "
                "Schematic IR is orthogonal (SPEC_IR invariant 6)",
            )
            continue
        wires.append(
            Wire(
                net=nets[wire.start],
                points=[_to_ir(wire.start), _to_ir(wire.end)],
            )
        )
    return wires


def _build_net_symbols(
    asc: AscFile, nets: dict[AscPoint, str]
) -> tuple[list[NetSymbol], list[Port]]:
    """Turn ``FLAG`` and ``IOPIN`` records into net symbols and ports (D7)."""
    io_points = {iopin.at for iopin in asc.iopins}
    net_symbols: list[NetSymbol] = []
    for flag in asc.flags:
        if flag.is_ground:
            net_symbols.append(
                NetSymbol(net=GROUND_NET, variant="ground", at=_to_ir(flag.at), rot=0)
            )
        elif flag.at not in io_points:
            # A flag that shares a point with an IOPIN is only there to name it;
            # the port already draws the name, so a tap would double it up.
            net_symbols.append(
                NetSymbol(
                    net=flag.name,
                    variant="tap",
                    at=_to_ir(flag.at),
                    rot=0,
                    text=flag.name,
                )
            )
    # An IOPIN is labelled by the flag sitting on it, which is not always the
    # name the whole net ended up with (two flags on one node disagree).
    flag_at: dict[AscPoint, str] = {}
    for flag in asc.flags:
        flag_at.setdefault(flag.at, flag.name)
    ports = [
        Port(
            name=flag_at.get(iopin.at) or nets.get(iopin.at, iopin.direction),
            at=_to_ir(iopin.at),
            direction=IO_DIRECTIONS[iopin.direction.lower()],
        )
        for iopin in asc.iopins
    ]
    return net_symbols, ports


def load_asc(path: Path, warnings: list[str] | None = None) -> SchematicIR:
    """Load the ``.asc`` file at *path* as a Schematic IR document.

    The file is read as bytes and decoded by :func:`decode_asc`, because
    LTspice's own UTF-16 output is not valid UTF-8.
    """
    return import_asc(
        decode_asc(path.read_bytes()), source=path.name, warnings=warnings
    )
