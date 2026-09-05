"""Placement: components onto the integer grid (roadmap §5.2).

The model is the one a person uses at a whiteboard, and it is what makes the
result readable without a general graph-layout algorithm (D15):

* every **signal net** owns a *column* — a vertical line ordered by distance
  from the input source, so signal flows left to right;
* every **ground net** owns a horizontal rail at the bottom, and every
  **supply net** a horizontal rail at the top;
* a two-terminal component between two signal nets is drawn **horizontally**
  between their columns; one that reaches a rail is drawn **vertically**
  between its column and that rail; one that spans two rails gets a reserved
  column of its own, to the left of everything else, which is where a supply
  or stimulus source belongs;
* a multi-terminal component is placed as a node beside the column of its
  output net (drain or collector), turned so that the terminal wanting the
  supply faces up and the one wanting ground faces down.

Two rules keep the result from containing wires that are not in the netlist,
which is the failure mode that matters most here — a schematic showing a
connection the circuit does not have is worse than no schematic at all:

* **Columns are even, node pins are odd** (see :data:`NODE_INSET`), so a net's
  vertical wire can never run through another net's terminal.
* **Rows are allocated, not assumed** (see :class:`_RowAllocator`), so two
  horizontal runs never share a row where they would overlap, and a horizontal
  run never passes through a terminal sitting on a column it crosses.

Placement produces components and the pin positions each net must reach;
:mod:`spice2tikz.layout.route` turns those into wires.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from .._serde import warn
from ..emit.circuitikz import escape_latex, format_quantity
from ..netlist_ir import Component, Kind
from ..quantity import Quantity
from ..schematic_ir import LabelSide, LabelSpec, NodeComponent, PathComponent
from ..symbols import (
    BUILTIN_SYMBOLS,
    OPAMP_BASE,
    OPAMP_PINS,
    SYMBOL_FOR_KIND,
    PinDef,
    Point,
    Rotation,
    SymbolDef,
    opamp_symbol,
    resolve_pins,
)
from .graph import CircuitGraph, column_order

MIN_COLUMN_PITCH: Final = 8
"""Horizontal distance between adjacent net columns, in grid units.

Eight leaves room for a four-unit-wide device body between two columns, and
makes every horizontal component at least eight units long — comfortably over
the two-unit minimum of invariant 7.
"""

ROW_PITCH: Final = 6
"""Vertical distance between stacked rows of horizontal runs.

Six, not four: a device body is four units tall, so rows four apart would
let two stacked devices touch and their terminals coincide."""

BRANCH_PITCH: Final = 6
"""Horizontal offset between parallel components hanging off one net.

Each branch carries a refdes label on one side and a value on the other, so six
units (3 cm at the default grid pitch) is what it takes to stop the value of one
branch running into the refdes of the next.
"""

MAIN_Y: Final = 6
"""The first row: where horizontal components and device bodies go by default."""

RAIL_GAP: Final = 4
"""Clearance between the tallest drawn thing and the supply rail above it."""

SUPPLY_DROP: Final = 4
"""Length of a component hanging from a supply rail."""

NODE_INSET: Final = 3
"""How far left of its net's column a node body sits.

This is the single most important number in the placer.  A transistor has its
drain, source and bulk on one vertical line, so a wire running down that line
would short all three together.  Offsetting the body by an **odd** amount does
two things at once: every node pin lands on an odd x while every column is
even, so no column can pass through a foreign terminal; and three units clears
the two-unit half-width of the body, so no column passes through a body either.
"""

ANCHOR_PREFERENCE: Final[tuple[str, ...]] = ("d", "c", "out", "s", "e", "g", "b")
"""Which pin fixes a node's column, most preferred first.

The output terminal wins: it is the net the rest of the circuit continues from,
so putting it beside its own column keeps the signal flowing rightwards.

These are *symbol* pin names, so this is consulted only for symbols that draw a
real shape.  The pins of a generated box are the subcircuit's own port names,
and a port that happens to be called ``b`` is not a transistor base.
"""

CHANNEL_PAIRS: Final[tuple[tuple[str, str], ...]] = (("d", "s"), ("c", "e"))
"""The two channel terminals of a three-terminal device, in taxonomy order.

Which of the pair is drawn *above* the other depends on the symbol, not on this
tuple: circuitikz draws a PMOS source-up and a PNP emitter-up.  The orientation
heuristic reads that off the symbol's own offsets.
"""

Band = str  # "ground" | "supply" | "float"


@dataclass
class Placement:
    """Where everything ended up, and what still has to be wired together."""

    graph: CircuitGraph
    components: list[PathComponent | NodeComponent] = field(default_factory=list)
    symbols: dict[str, SymbolDef] = field(default_factory=dict)
    net_pins: dict[str, list[Point]] = field(default_factory=dict)
    columns: dict[str, int] = field(default_factory=dict)
    rail_y: dict[str, int] = field(default_factory=dict)
    input_net: str | None = None
    output_net: str | None = None
    skipped_pins: set[tuple[str, str]] = field(default_factory=set)

    def add_pin(self, net: str, point: Point) -> None:
        """Record that *net* must reach *point*."""
        self.net_pins.setdefault(net, []).append(point)

    def symbol(self, name: str) -> SymbolDef:
        """Return a placed node's symbol, generated or built-in."""
        found = self.symbols.get(name) or BUILTIN_SYMBOLS.get(name)
        return found if found is not None else SymbolDef(size=(4, 4))


@dataclass
class _Vertical:
    """A two-terminal component hanging between one column and one rail."""

    ref: str
    signal: str
    rail: str
    band: Band
    x: int


class _RowAllocator:
    """Assigns rows to everything that occupies horizontal space.

    A horizontal path component has a zero-height bounding box, so two of them
    on one row with overlapping spans really do lie on top of each other
    (invariant 12).  Worse, a horizontal run that crosses a column at the row
    where something is attached to that column would join the two nets without
    the netlist saying so.  Both are prevented here: a row must be free of
    overlapping *spans* and must not contain a *point* strictly inside the span
    being placed.
    """

    def __init__(self) -> None:
        self._spans: list[list[tuple[int, int]]] = [[]]
        self._points: list[set[int]] = [set()]

    def _ensure(self, row: int) -> None:
        while len(self._spans) <= row:
            self._spans.append([])
            self._points.append(set())

    def block_point(self, row: int, x: int) -> None:
        """Record a terminal at *x* on *row* that no run may cross."""
        self._ensure(row)
        self._points[row].add(x)

    def fits(self, row: int, span: tuple[int, int]) -> bool:
        """Return ``True`` when *span* can be added to *row*."""
        self._ensure(row)
        low, high = min(span), max(span)
        if any(min(high, b) - max(low, a) > 0 for a, b in self._spans[row]):
            return False
        return not any(low < point < high for point in self._points[row])

    def allocate(self, span: tuple[int, int]) -> int:
        """Return the lowest row on which *span* fits, reserving it."""
        row = 0
        while not self.fits(row, span):
            row += 1
        self._ensure(row)
        self._spans[row].append((min(span), max(span)))
        return row

    def reserve(self, row: int, span: tuple[int, int]) -> None:
        """Mark *span* as taken on *row* without searching."""
        self._ensure(row)
        self._spans[row].append((min(span), max(span)))

    def top(self) -> int:
        """Return the y of the highest row in use."""
        return MAIN_Y + ROW_PITCH * max(len(self._spans) - 1, 0)


def row_y(row: int) -> int:
    """Return the y coordinate of allocation row *row*."""
    return MAIN_Y + ROW_PITCH * row


class _ReservedColumns:
    """Hands out columns outside the signal-net grid.

    Rail-to-rail components (a supply source, a decoupling capacitor) go to the
    left of column zero, which is where the roadmap wants sources; components
    with nothing to anchor to go to the right of the last signal column.
    """

    def __init__(self, pitch: int) -> None:
        self.pitch = pitch
        self._left = 0
        self._right = 0

    def left(self) -> int:
        """Return the next free column to the left of the signal columns."""
        self._left += 1
        return -self.pitch * self._left

    def right(self, last_column: int) -> int:
        """Return the next free column to the right of *last_column*."""
        self._right += 1
        return last_column + self.pitch * self._right


def place(
    graph: CircuitGraph,
    ranks: dict[str, int],
    *,
    input_net: str | None = None,
    output_net: str | None = None,
    siunitx: bool = True,
    warnings: list[str] | None = None,
) -> Placement:
    """Place every component of *graph* and return the resulting geometry."""
    placement = Placement(graph=graph, input_net=input_net, output_net=output_net)
    pitch = _column_pitch(graph)
    order = column_order(graph, ranks)
    placement.columns = {net: pitch * index for net, index in order.items()}
    placement.rail_y.update(
        {net: -ROW_PITCH * index for index, net in enumerate(graph.ground_nets)}
    )

    reserved = _ReservedColumns(pitch)
    rows = _RowAllocator()

    # Work out where every rail-connected component hangs *before* placing
    # anything, so that a horizontal run knows which columns already carry a
    # terminal on the main row and can step over to a free row instead.
    verticals = _plan_verticals(graph, placement)
    for vertical in verticals:
        if vertical.band != "supply":
            rows.block_point(0, vertical.x)

    for ref in graph.components:
        if not graph.is_path(ref):
            _place_node(graph, placement, ref, reserved, rows, siunitx, warnings)

    planned = {vertical.ref: vertical for vertical in verticals}
    # Anything touching a supply net has to wait for the supply rail's height.
    deferred = [
        ref
        for ref in graph.components
        if graph.is_path(ref)
        and any(net in graph.supply_nets for net in graph.component_nets(ref))
    ]
    for ref in graph.components:
        if graph.is_path(ref) and ref not in deferred:
            _place_path(
                graph,
                placement,
                ref,
                planned.get(ref),
                reserved,
                rows,
                siunitx,
                warnings,
            )

    # The supply rails sit above everything already drawn, so they can only be
    # positioned once the rest of the sheet has a height.
    top = max(
        [MAIN_Y, rows.top(), *placement.rail_y.values(), *_component_tops(placement)]
    )
    for index, net in enumerate(graph.supply_nets):
        placement.rail_y[net] = top + RAIL_GAP + ROW_PITCH * index
    for ref in deferred:
        _place_path(
            graph, placement, ref, planned.get(ref), reserved, rows, siunitx, warnings
        )
    return placement


def _plan_verticals(graph: CircuitGraph, placement: Placement) -> list[_Vertical]:
    """Return every rail-connected two-terminal component and the x it hangs at.

    Parallel branches on the same net and the same rail band are fanned out
    sideways so they cannot be drawn on top of one another; the bands are
    counted separately because the ground-side and supply-side stacks occupy
    disjoint heights and can share an x safely.
    """
    branches: dict[tuple[str, Band], int] = {}
    planned: list[_Vertical] = []
    for ref in graph.components:
        if not graph.is_path(ref):
            continue
        nets = graph.component_nets(ref)
        rails = [net for net in nets if graph.is_rail(net)]
        if len(rails) != 1 or nets[0] == nets[1]:
            continue
        rail = rails[0]
        signal = nets[1] if rail == nets[0] else nets[0]
        band = _band(graph, rail)
        index = branches.get((signal, band), 0)
        branches[(signal, band)] = index + 1
        planned.append(
            _Vertical(
                ref=ref,
                signal=signal,
                rail=rail,
                band=band,
                x=placement.columns.get(signal, 0) + BRANCH_PITCH * index,
            )
        )
    return planned


def _column_pitch(graph: CircuitGraph) -> int:
    """Return a column pitch wide enough for the busiest net's parallel branches."""
    counts: dict[tuple[str, Band], int] = {}
    for ref in graph.components:
        if not graph.is_path(ref):
            continue
        nets = graph.component_nets(ref)
        signal = [net for net in nets if not graph.is_rail(net)]
        if len(signal) != 1:
            continue
        band = _band(graph, next(net for net in nets if graph.is_rail(net)))
        key = (signal[0], band)
        counts[key] = counts.get(key, 0) + 1
    widest = max(counts.values(), default=1)
    # Only the *extra* branches need room beyond the minimum pitch: one branch
    # sits on the column itself.
    return MIN_COLUMN_PITCH + BRANCH_PITCH * (widest - 1)


def _band(graph: CircuitGraph, net: str) -> Band:
    if net in graph.ground_nets:
        return "ground"
    if net in graph.supply_nets:
        return "supply"
    return "float"


def _component_tops(placement: Placement) -> list[int]:
    """Return the highest y each placed component reaches."""
    tops: list[int] = []
    for element in placement.components:
        if isinstance(element, PathComponent):
            tops.append(max(element.a[1], element.b[1]))
        else:
            tops.append(element.at[1] + placement.symbol(element.symbol).size[1] // 2)
    return tops


# --- node components ---------------------------------------------------------


def _place_node(
    graph: CircuitGraph,
    placement: Placement,
    ref: str,
    reserved: _ReservedColumns,
    rows: _RowAllocator,
    siunitx: bool,
    warnings: list[str] | None,
) -> None:
    """Place one multi-terminal component as a node with resolved pins."""
    component = _for_requested_symbol(graph.components[ref], warnings)
    name, symbol = _symbol_for(component, placement)
    anchor = _anchor(graph, component, symbol)
    rot, mirror = _orientation(graph, component, symbol)

    if anchor is not None and anchor in placement.columns:
        x = placement.columns[anchor] - NODE_INSET
    else:
        x = reserved.right(max(placement.columns.values(), default=0)) - NODE_INSET

    # A node has never shown a value, so one appears only when asked for.
    label, value_label = _labelling(component, siunitx, warnings, default_value=None)
    if symbol.base == OPAMP_BASE:
        # The default side is opposite the pins' centre of mass, which for a
        # triangle whose inputs are all on the left means "right" — inside the
        # body. Above it is, with the value below.
        label = _with_side(label, "above")
    row = _node_row(graph, placement, component, symbol, x, rows)
    at = (x, row_y(row))
    at = _free_node_position(placement, symbol, at)
    pins = resolve_pins(symbol, at, rot, mirror)

    placement.components.append(
        NodeComponent(
            ref=component.id,
            kind=component.kind,
            symbol=name,
            at=at,
            rot=rot,
            mirror=mirror,
            pins=pins,
            label=label,
            value_label=value_label,
        )
    )
    _reserve_node_rows(rows, at, symbol)

    for pin, net in component.pins.items():
        point = pins.get(pin)
        if point is None:
            if warnings is not None:
                warnings.append(
                    f"{component.id}: pin {pin!r} has no place on symbol {name!r}; "
                    f"net {net!r} is left unconnected there"
                )
            continue
        if _is_body_pin(component, pin):
            # The body terminal sits on the node origin, so any wire from it
            # would leave through the middle of the device and cross both
            # channel terminals.  It is declared for invariant 8 and drawn as
            # the implicit tie every schematic uses; circuitikz draws no bulk
            # terminal unless asked, so nothing dangles.
            placement.skipped_pins.add((component.id, pin))
            if warnings is not None and not _bulk_is_implicit(component, pin):
                warnings.append(
                    f"{component.id}: body terminal {pin!r} is on net {net!r}, not "
                    "tied to the channel; the connection is not drawn"
                )
            continue
        placement.add_pin(net, point)


def _node_row(
    graph: CircuitGraph,
    placement: Placement,
    component: Component,
    symbol: SymbolDef,
    x: int,
    rows: _RowAllocator,
) -> int:
    """Allocate a row for this node, counting the reach of its own stubs.

    The span offered to the allocator is the body plus every horizontal wire
    that will later leave it, so that two devices sharing a row cannot have one
    device's gate wire run through the other device's terminals.
    """
    half_w = symbol.size[0] // 2
    low, high = x - half_w, x + half_w
    for pin, net in component.pins.items():
        definition = symbol.pins.get(pin)
        if definition is None or graph.is_rail(net):
            continue  # a rail-bound pin leaves vertically, using no row
        target = placement.columns.get(net)
        if target is None:
            continue
        low = min(low, target, x + definition.offset[0])
        high = max(high, target, x + definition.offset[0])
    return rows.allocate((low, high))


def _orientation(
    graph: CircuitGraph, component: Component, symbol: SymbolDef
) -> tuple[Rotation, bool]:
    """Return the ``(rot, mirror)`` that points each terminal the right way.

    A device is drawn unturned unless the terminal the symbol puts at the
    *bottom* is the one that wants to be at the top.  Turning it through 180°
    **and** mirroring flips it vertically while leaving the control terminal on
    the left, which is how such a stage is drawn by hand.

    Which terminal the symbol puts on top is read from the symbol itself: a
    PMOS is drawn source-up and a PNP emitter-up, so a PMOS whose source is on
    the supply rail already needs no turning at all.
    """
    for first, second in CHANNEL_PAIRS:
        upper, lower = _vertical_order(symbol, first, second)
        if upper is None or lower is None:
            continue
        top_net, bottom_net = component.pins.get(upper), component.pins.get(lower)
        if top_net is None or bottom_net is None:
            continue
        if _rail_preference(graph, bottom_net) > _rail_preference(graph, top_net):
            return 180, True
        return 0, False
    return 0, False


def _vertical_order(
    symbol: SymbolDef, first: str, second: str
) -> tuple[str | None, str | None]:
    """Return the pair ``(upper, lower)`` as *this symbol* draws them."""
    above = symbol.pins.get(first)
    below = symbol.pins.get(second)
    if above is None or below is None:
        return None, None
    if above.offset[1] >= below.offset[1]:
        return first, second
    return second, first


def _rail_preference(graph: CircuitGraph, net: str) -> int:
    """Return how much *net* wants to be drawn at the top of the sheet."""
    if net in graph.supply_nets:
        return 1
    if net in graph.ground_nets:
        return -1
    return 0


def _is_body_pin(component: Component, pin: str) -> bool:
    """Return ``True`` for a MOS bulk or a bipolar substrate terminal."""
    if component.kind in (Kind.NMOS, Kind.PMOS):
        return pin == "b"
    if component.kind in (Kind.BJT_NPN, Kind.BJT_PNP):
        return pin == "s"
    return False


def _bulk_is_implicit(component: Component, pin: str) -> bool:
    """Return ``True`` when the body terminal is tied to the source or emitter."""
    channel = "s" if component.kind in (Kind.NMOS, Kind.PMOS) else "e"
    return component.pins.get(pin) == component.pins.get(channel)


def _anchor(graph: CircuitGraph, component: Component, symbol: SymbolDef) -> str | None:
    """Return the net whose column this node is placed against."""
    # A pin is preferred by the anchor it is drawn from, which is the tool's
    # own vocabulary; a pin named after a port falls back to that name, and a
    # generated box has no anchors at all, so its ports are never matched
    # against this list — a port called `b` is not a transistor base.
    anchors = (
        {pin: (definition.anchor or pin) for pin, definition in symbol.pins.items()}
        if symbol.base is not None
        else {}
    )
    preferred: list[str] = [
        pin
        for name in ANCHOR_PREFERENCE
        for pin, anchor in anchors.items()
        if anchor == name
    ]
    preferred.extend(pin for pin in symbol.pins if pin not in preferred)
    for pin in preferred:
        net = component.pins.get(pin)
        if net is not None and not graph.is_rail(net):
            return net
    return None


def _free_node_position(placement: Placement, symbol: SymbolDef, at: Point) -> Point:
    """Move *at* upwards until this node's box clears every placed node.

    Two devices sharing an output net would otherwise be drawn on top of each
    other.  Stacking keeps them beside the same column, which is what the
    shared net means.
    """
    step = ROW_PITCH
    candidate = at
    while any(
        _boxes_clash(placement, candidate, symbol, other)
        for other in placement.components
        if isinstance(other, NodeComponent)
    ):
        candidate = (candidate[0], candidate[1] + step)
    return candidate


def _boxes_clash(
    placement: Placement, at: Point, symbol: SymbolDef, other: NodeComponent
) -> bool:
    """Return ``True`` when a node at *at* would overlap *other* with real area."""
    size = placement.symbol(other.symbol).size
    dx = abs(at[0] - other.at[0]) * 2
    dy = abs(at[1] - other.at[1]) * 2
    return dx < symbol.size[0] + size[0] and dy < symbol.size[1] + size[1]


def _reserve_node_rows(rows: _RowAllocator, at: Point, symbol: SymbolDef) -> None:
    """Reserve every row this node's body crosses."""
    half_w, half_h = symbol.size[0] // 2, symbol.size[1] // 2
    span = (at[0] - half_w, at[0] + half_w)
    low, high = at[1] - half_h, at[1] + half_h
    row = 0
    while row_y(row) <= high:
        if row_y(row) >= low:
            rows.reserve(row, span)
        row += 1


def _for_requested_symbol(
    component: Component, warnings: list[str] | None
) -> Component:
    """Return *component* with any symbol request it cannot honour removed.

    ``.subckt LM741 PLUS MINUS OUT VCC VEE ; symbol=opamp`` is the only way a
    subcircuit becomes anything but a labelled box, and the ports map onto the
    symbol by position alone — port 1 to ``+``, port 2 to ``-``, then ``out``,
    ``up`` and ``down``. Nothing reads the port names: ``PLUS``, ``IN+`` and
    ``VP`` all mean the same thing, and a list of the spellings people use
    would never be finished, nor be right about ``LM317``.

    The names are *kept*, though: the symbol records which anchor each pin is
    drawn from, so the sheet still says ``PLUS`` where the netlist does and
    the drawing can be read back against the circuit.
    """
    requested = component.meta.get("symbol")
    if requested is None:
        return component
    if requested != "opamp":
        warn(
            warnings,
            f"{component.id}: symbol={requested!r} is not a symbol this version "
            "draws; falling back to a labelled box",
        )
        return _without_symbol(component)
    ports = list(component.pins)
    if not 3 <= len(ports) <= len(OPAMP_PINS):
        warn(
            warnings,
            f"{component.id}: symbol=opamp expects 3 to {len(OPAMP_PINS)} ports "
            f"(+, -, out, up, down) but this has {len(ports)}; falling back to "
            "a labelled box",
        )
        return _without_symbol(component)
    return component


def _with_side(label: LabelSpec | None, side: LabelSide) -> LabelSpec | None:
    """Ask for *side*, unless the label is suppressed or already placed."""
    if label is None:
        return LabelSpec(side=side)
    if label.text == SUPPRESSED or label.side is not None:
        return label
    return replace(label, side=side)


def _without_symbol(component: Component) -> Component:
    """Return *component* with its unusable ``symbol`` request dropped."""
    return replace(
        component,
        meta={key: value for key, value in component.meta.items() if key != "symbol"},
    )


def _symbol_for(component: Component, placement: Placement) -> tuple[str, SymbolDef]:
    """Return the symbol name and geometry to draw *component* with.

    Built-in shapes are used where circuitikz has one; everything else gets a
    generated box written into the document's own ``symbols`` block, so the
    file renders without tool-internal lookups (SPEC_IR §2).
    """
    if component.meta.get("symbol") == "opamp":
        # The geometry is the built-in's, but the pin names are the
        # subcircuit's own ports, so each instance kind declares its own
        # symbol in the document — exactly as a generated box does.
        ports = list(component.pins)
        if tuple(ports) == OPAMP_PINS:
            return "opamp", BUILTIN_SYMBOLS["opamp"]
        name = f"opamp:{component.subckt.lower()}" if component.subckt else "opamp:x"
        if name not in placement.symbols:
            placement.symbols[name] = opamp_symbol(ports)
        return name, placement.symbols[name]
    builtin = SYMBOL_FOR_KIND.get(component.kind)
    if builtin is not None and builtin in BUILTIN_SYMBOLS:
        symbol = BUILTIN_SYMBOLS[builtin]
        if all(pin in component.pins for pin in symbol.pins):
            return builtin, symbol
    pins = list(component.pins)
    name = _box_name(component, pins)
    if name not in placement.symbols:
        placement.symbols[name] = generated_box(pins)
    return name, placement.symbols[name]


def _box_name(component: Component, pins: list[str]) -> str:
    if component.kind is Kind.SUBCIRCUIT and component.subckt:
        return f"subckt:{component.subckt.lower()}"
    if component.kind is Kind.GENERIC:
        return f"box:generic{len(pins)}"
    return f"box:{component.kind.value}"


BOX_WIDTH: Final = 4
"""Width of a generated box symbol, in grid units.

Even, so that a box placed at an odd x keeps its pins on odd x too — the parity
invariant of :data:`NODE_INSET`.
"""


def generated_box(pins: list[str]) -> SymbolDef:
    """Return a box symbol with *pins* distributed down the left and right sides.

    The first half of the pin list goes down the left edge and the rest down
    the right, in declaration order, spaced two units apart so every pin lands
    on an integer grid point.  Pins sit exactly on the edge, so the emitter
    draws no stub for them.
    """
    count = max(len(pins), 1)
    left_count = (count + 1) // 2
    left, right = pins[:left_count], pins[left_count:]
    tall = max(len(left), len(right), 1)
    height = 2 * tall + 2
    half_width = BOX_WIDTH // 2
    definitions: dict[str, PinDef] = {}
    for index, pin in enumerate(left):
        definitions[pin] = PinDef(offset=(-half_width, tall - 1 - 2 * index), label=pin)
    for index, pin in enumerate(right):
        definitions[pin] = PinDef(offset=(half_width, tall - 1 - 2 * index), label=pin)
    return SymbolDef(size=(BOX_WIDTH, height), pins=definitions)


# --- path components ---------------------------------------------------------


def _place_path(
    graph: CircuitGraph,
    placement: Placement,
    ref: str,
    vertical: _Vertical | None,
    reserved: _ReservedColumns,
    rows: _RowAllocator,
    siunitx: bool,
    warnings: list[str] | None,
) -> None:
    """Place one two-terminal component as a segment between two grid points."""
    component = graph.components[ref]
    nets = graph.component_nets(ref)
    first, second = nets[0], nets[1]
    ends: dict[str, Point] = {}

    if vertical is not None:
        rail_y = placement.rail_y[vertical.rail]
        inner = (
            rail_y - SUPPLY_DROP
            if vertical.band == "supply"
            else _ground_inner(placement, rows, vertical)
        )
        ends = {
            vertical.rail: (vertical.x, rail_y),
            vertical.signal: (vertical.x, inner),
        }
        a, b = ends[first], ends[second]
    elif first == second:
        # A short: both ends on one net.  Draw it standing on its own column so
        # nothing else has to make room for it.
        x = reserved.left()
        a, b = (x, row_y(1)), (x, MAIN_Y)
    elif graph.is_rail(first) and graph.is_rail(second):
        x = reserved.left()
        a = (x, placement.rail_y[first])
        b = (x, placement.rail_y[second])
    else:
        left, right = placement.columns[first], placement.columns[second]
        row = rows.allocate((left, right))
        y = row_y(row)
        a, b = (left, y), (right, y)

    # A path component has always shown its value, so that is the default.
    label, value_label = _labelling(
        component, siunitx, warnings, default_value=_value_label(component, siunitx)
    )
    placement.components.append(
        PathComponent(
            ref=component.id,
            kind=component.kind,
            a=a,
            b=b,
            label=label,
            value_label=value_label,
        )
    )
    placement.add_pin(first, a)
    placement.add_pin(second, b)


def _ground_inner(
    placement: Placement, rows: _RowAllocator, vertical: _Vertical
) -> int:
    """Return the y of a ground-side component's upper terminal.

    Where the net already reaches a device terminal — a transistor emitter or
    source, which sits below the row its body is on — the component reaches up
    to *that* height. Otherwise the wire joining them would have to climb the
    column the component itself occupies, and be drawn straight down the middle
    of the part: a connection to the body of a resistor, which means nothing.

    With no device to meet, the component tops out on the lowest row that has
    nothing crossing this column, so no wire passes through its terminal.
    """
    reached = [
        point[1]
        for point in placement.net_pins.get(vertical.signal, [])
        if point[1] > placement.rail_y[vertical.rail] + 1
    ]
    if reached:
        return min(reached)
    row = 0
    while not rows.fits(row, (vertical.x, vertical.x)):
        row += 1
    rows.block_point(row, vertical.x)
    return row_y(row)


def source_value(component: Component) -> Quantity | None:
    """Return the operating value to print beside an independent source.

    A source's ``value`` is its whole specification (``DC 0 AC 1 SIN(...)``),
    which never parses to a single number, so the figure would otherwise carry
    no value at all for the one component that sets the circuit's levels.  The
    DC parameter is the value a reader wants: it is what a supply *is*, and it
    is the only part of a specification that is a single quantity.

    A time-varying specification deliberately yields nothing.  Its amplitude,
    offset and frequency are three numbers, not one; they are several times
    wider than the symbol, and the waveform belongs in the caption.  The
    symbol itself says the source is time-varying.
    """
    if component.kind not in (Kind.VSOURCE, Kind.ISOURCE):
        return None
    dc = (component.params or {}).get("dc")
    if dc is None or dc.value is None:
        return None
    return dc


LABEL_PARTS: Final[tuple[str, ...]] = ("ref", "value", "none")
"""What ``; labels=`` accepts: ``ref``, ``value``, both, or ``none``."""

SUPPRESSED: Final = "-"
"""``LabelSpec.text`` that means "draw nothing here" (SPEC_IR §2)."""


def _requested_labels(
    component: Component, warnings: list[str] | None
) -> tuple[bool, bool] | None:
    """Return ``(show_ref, show_value)`` from ``; labels=``, or ``None``.

    ``None`` means the card said nothing, which is not the same as saying
    ``none``: the defaults then stand, so a deck that carries no metadata
    draws exactly as it did before this existed.

    A misspelling is worth a warning — it is a request that will silently not
    happen otherwise — but never an error, because a deck must still convert.
    """
    raw = component.meta.get("labels")
    if raw is None:
        return None
    # Empty parts are kept so that they fall into `unknown` below: metadata is
    # whitespace-delimited, so `labels=ref, value` reaches here as `ref,` with
    # the `value` dropped as a separate token, and saying so beats guessing.
    wanted = [part.strip().lower() for part in raw.split(",")]
    unknown = [part for part in wanted if part not in LABEL_PARTS]
    if unknown or not wanted:
        warn(
            warnings,
            f"{component.id}: labels={raw!r} is not understood "
            f"(expected {', '.join(LABEL_PARTS)}); the defaults are used",
        )
        return None
    if "none" in wanted:
        if len(wanted) > 1:
            warn(
                warnings,
                f"{component.id}: labels={raw!r} asks for 'none' as well as "
                f"{', '.join(p for p in wanted if p != 'none')}; nothing is drawn",
            )
        return False, False
    return "ref" in wanted, "value" in wanted


def _labelling(
    component: Component,
    siunitx: bool,
    warnings: list[str] | None,
    *,
    default_value: LabelSpec | None,
) -> tuple[LabelSpec | None, LabelSpec | None]:
    """Return the ``(label, value_label)`` a component should carry.

    *default_value* is what it would show with no metadata at all — a path
    component's value, and nothing for a node, which never carried one.
    """
    request = _requested_labels(component, warnings)
    if request is None:
        return None, default_value
    show_ref, show_value = request
    # A label with no text means "derive it from the ref", which is how a ref
    # is drawn; the sentinel is the only way to say "draw nothing".
    label = None if show_ref else LabelSpec(text=SUPPRESSED)
    return label, _shown_value(component, siunitx) if show_value else None


def _shown_value(component: Component, siunitx: bool) -> LabelSpec | None:
    """Return what ``labels=value`` means for *component*.

    A resistor has a value; a transistor has a model name and a subcircuit has
    a definition name, which is the thing a reader would call its value.
    """
    explicit = _value_label(component, siunitx)
    if explicit is not None:
        return explicit
    name = component.model or component.subckt
    return None if name is None else LabelSpec(text=escape_latex(name))


def _value_label(component: Component, siunitx: bool) -> LabelSpec | None:
    """Return the component's value as an explicit, already-formatted label.

    ``LabelSpec`` carries text, not a quantity, so the formatting the emitter
    would apply to a derived value has to happen here — which is exactly what
    ``emit.format_quantity`` exists for (see the §2.3 decision log).

    Only a value that parsed to a *number* is shown.  A source specification
    is several times wider than the symbol it labels and would collide with
    everything around it, so a source shows its DC operating value instead —
    a component's value belongs on the component, not on a net (§1 of the
    change request).
    """
    value = source_value(component) or component.value
    if value is None or value.value is None:
        return None
    return LabelSpec(text=format_quantity(value, siunitx=siunitx))
