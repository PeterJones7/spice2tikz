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

from dataclasses import dataclass, field
from typing import Final

from ..emit.circuitikz import format_quantity
from ..netlist_ir import Component, Kind
from ..schematic_ir import LabelSpec, NodeComponent, PathComponent
from ..symbols import (
    BUILTIN_SYMBOLS,
    SYMBOL_FOR_KIND,
    PinDef,
    Point,
    Rotation,
    SymbolDef,
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

ANCHOR_PREFERENCE: Final[tuple[str, ...]] = ("d", "c", "s", "e", "g", "b")
"""Which pin fixes a node's column, most preferred first.

The output terminal wins: it is the net the rest of the circuit continues from,
so putting it beside its own column keeps the signal flowing rightwards.
"""

CHANNEL_PINS: Final[tuple[tuple[str, str], ...]] = (("d", "s"), ("c", "e"))
"""``(upper, lower)`` terminal pairs of a three-terminal device, unturned."""

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
            _place_node(graph, placement, ref, reserved, rows, warnings)

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
                graph, placement, ref, planned.get(ref), reserved, rows, siunitx
            )

    # The supply rails sit above everything already drawn, so they can only be
    # positioned once the rest of the sheet has a height.
    top = max(
        [MAIN_Y, rows.top(), *placement.rail_y.values(), *_component_tops(placement)]
    )
    for index, net in enumerate(graph.supply_nets):
        placement.rail_y[net] = top + RAIL_GAP + ROW_PITCH * index
    for ref in deferred:
        _place_path(graph, placement, ref, planned.get(ref), reserved, rows, siunitx)
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
    warnings: list[str] | None,
) -> None:
    """Place one multi-terminal component as a node with resolved pins."""
    component = graph.components[ref]
    name, symbol = _symbol_for(component, placement)
    anchor = _anchor(graph, component, symbol)
    rot, mirror = _orientation(graph, component, symbol)

    if anchor is not None and anchor in placement.columns:
        x = placement.columns[anchor] - NODE_INSET
    else:
        x = reserved.right(max(placement.columns.values(), default=0)) - NODE_INSET

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

    A device is drawn unturned — output terminal up, control terminal left —
    unless its *lower* terminal is the one that wants to be at the top: a PMOS
    with its source on the supply rail, or a PNP emitter-follower.  Turning it
    through 180° **and** mirroring puts the source or emitter up and the drain
    or collector down while leaving the control terminal on the left, which is
    exactly how such a stage is drawn by hand.
    """
    for upper, lower in CHANNEL_PINS:
        if upper not in symbol.pins or lower not in symbol.pins:
            continue
        top_net, bottom_net = component.pins.get(upper), component.pins.get(lower)
        if top_net is None or bottom_net is None:
            continue
        if _rail_preference(graph, bottom_net) > _rail_preference(graph, top_net):
            return 180, True
        return 0, False
    return 0, False


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
    preferred: list[str] = [pin for pin in ANCHOR_PREFERENCE if pin in symbol.pins]
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


def _symbol_for(component: Component, placement: Placement) -> tuple[str, SymbolDef]:
    """Return the symbol name and geometry to draw *component* with.

    Built-in shapes are used where circuitikz has one; everything else gets a
    generated box written into the document's own ``symbols`` block, so the
    file renders without tool-internal lookups (SPEC_IR §2).
    """
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
            else row_y(_ground_row(rows, vertical.x))
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

    placement.components.append(
        PathComponent(
            ref=component.id,
            kind=component.kind,
            a=a,
            b=b,
            value_label=_value_label(component, siunitx),
        )
    )
    placement.add_pin(first, a)
    placement.add_pin(second, b)


def _ground_row(rows: _RowAllocator, x: int) -> int:
    """Return the row a ground-side component's top terminal should sit on.

    Row zero unless something already crosses this column there, in which case
    the component reaches one row higher rather than have a wire pass through
    its terminal.
    """
    row = 0
    while not rows.fits(row, (x, x)):
        row += 1
    rows.block_point(row, x)
    return row


def _value_label(component: Component, siunitx: bool) -> LabelSpec | None:
    """Return the component's value as an explicit, already-formatted label.

    ``LabelSpec`` carries text, not a quantity, so the formatting the emitter
    would apply to a derived value has to happen here — which is exactly what
    ``emit.format_quantity`` exists for (see the §2.3 decision log).

    Only a value that parsed to a *number* is shown.  A source specification
    (``DC 0 AC 1 SIN(0 10m 1k)``) is several times wider than the symbol it
    labels and would collide with everything around it; the refdes still names
    the component, and the waveform belongs in the figure caption.
    """
    if component.value is None or component.value.value is None:
        return None
    return LabelSpec(text=format_quantity(component.value, siunitx=siunitx))
