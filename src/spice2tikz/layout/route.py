"""Routing: wires, junction dots and net symbols (roadmap §5.3).

Every net is wired as a **spine plus stubs**.  A signal net's spine is the
vertical line of its own column; a ground or supply net's spine is its
horizontal rail.  Each terminal is joined to the spine by the shortest route
that is *clear*: a single perpendicular segment where possible (the degenerate
case of an L route, since the spine absorbs the other leg), otherwise a Z that
steps along the terminal's own line first and crosses further out.

"Clear" means the route passes through no other net's terminal and through no
device body.  That check is the whole point of this module: a wire drawn
through somebody else's terminal is a connection the circuit does not have, and
a schematic that lies is worse than no schematic.  The placer sets things up so
the straight route is almost always clear (see
:data:`~spice2tikz.layout.place.NODE_INSET`); this handles the rest.

The spine's extent is the range its own connection points span, so both of its
ends land on something: dangling wire ends (invariant 9) are impossible by
construction.  Junction dots are then computed with exactly the counting rule
``validate.py`` uses, so invariant 10 holds by construction too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Final

from ..schematic_ir import (
    Element,
    Junction,
    Label,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Wire,
)
from ..symbols import Point, Rotation
from .place import Placement, source_value

MIN_JUNCTION_CONDUCTORS: Final = 3
"""Conductors that must meet before a dot means anything (SPEC_IR §4, inv. 10)."""

DETOUR_STEP: Final = 2
"""How far each successive Z route steps aside looking for a clear lane."""

MAX_DETOURS: Final = 12
"""How many detours to try before giving up and drawing the straight route."""

Box = tuple[int, int, int, int]
Segment = tuple[Point, Point]


@dataclass
class _Obstacles:
    """Everything a wire must not be drawn through."""

    pins: dict[Point, set[str]] = field(default_factory=dict)
    bodies: list[Box] = field(default_factory=list)
    drawn: list[tuple[Segment, str]] = field(default_factory=list)
    paths: list[Segment] = field(default_factory=list)

    def blocked(self, net: str, start: Point, end: Point) -> bool:
        """Return ``True`` when a wire of *net* may not run from *start* to *end*."""
        for point, owners in self.pins.items():
            # Our own terminals are exactly what a wire is for; anyone else's
            # are forbidden anywhere on the segment, its ends included — a wire
            # *ending* on a foreign terminal is the worst case of all, since it
            # is a short that looks deliberate.
            if net in owners:
                continue
            if _on_segment(point, start, end):
                return True
        if any(_crosses_box(start, end, box) for box in self.bodies):
            return True
        if any(_runs_along(segment, (start, end)) for segment in self.paths):
            return True
        return any(
            other != net and _overlaps(segment, (start, end))
            for segment, other in self.drawn
        )


def route(placement: Placement) -> list[Element]:
    """Return the wires, junctions and net symbols joining up *placement*."""
    obstacles = _collect_obstacles(placement)
    wires: list[Wire] = []
    symbols: list[NetSymbol] = []
    for net in _net_order(placement):
        points = _unique(placement.net_pins.get(net, []))
        if not points:
            continue
        # A supply glyph is drawn just past the end of its rail, so extend the
        # rail to reach it before the wires are built.
        stub = _supply_marker(placement, net, points)
        wired = [*points, stub] if stub is not None else points
        net_wires = _wire_net(placement, obstacles, net, wired)
        for wire in net_wires:
            for segment in wire.segments():
                obstacles.drawn.append((segment, net))
        wires.extend(net_wires)
        symbols.extend(_net_symbols(placement, net, points, stub))
    elements: list[Element] = [*placement.components, *wires, *symbols]
    elements.extend(_junctions(elements))
    return elements


def _collect_obstacles(placement: Placement) -> _Obstacles:
    """Index every terminal and device body on the sheet."""
    obstacles = _Obstacles()
    graph = placement.graph
    for element in placement.components:
        component = graph.components.get(element.ref)
        if isinstance(element, PathComponent):
            nets = graph.component_nets(element.ref)
            for point, net in zip((element.a, element.b), nets, strict=False):
                obstacles.pins.setdefault(point, set()).add(net)
            obstacles.paths.append((element.a, element.b))
            continue
        for pin, point in element.pins.items():
            owner = component.pins.get(pin) if component is not None else None
            if owner is not None:
                obstacles.pins.setdefault(point, set()).add(owner)
        size = placement.symbol(element.symbol).size
        half_w, half_h = size[0] // 2, size[1] // 2
        obstacles.bodies.append(
            (
                element.at[0] - half_w,
                element.at[1] - half_h,
                element.at[0] + half_w,
                element.at[1] + half_h,
            )
        )
    return obstacles


def _net_order(placement: Placement) -> list[str]:
    """Return every net with terminals, in netlist order, rails last.

    Rails are wired last so the emitted document reads circuit first, rails
    after, and so the long rail runs are laid over the short stubs rather than
    the other way round.
    """
    graph = placement.graph
    signal = [net for net in graph.signal_nets if net in placement.net_pins]
    rails = [
        net
        for net in (*graph.ground_nets, *graph.supply_nets)
        if net in placement.net_pins
    ]
    extra = [
        net for net in placement.net_pins if net not in signal and net not in rails
    ]
    return [*signal, *rails, *extra]


def _unique(points: list[Point]) -> list[Point]:
    """Return *points* without duplicates, order preserved."""
    seen: set[Point] = set()
    result = []
    for point in points:
        if point not in seen:
            seen.add(point)
            result.append(point)
    return result


def _wire_net(
    placement: Placement, obstacles: _Obstacles, net: str, points: list[Point]
) -> list[Wire]:
    """Return the spine and stubs joining every terminal of *net*."""
    if len(points) < 2:
        # A net with a single terminal has nothing to join it to; a stub to an
        # empty spine would be a dangling wire end (invariant 9).
        return []
    horizontal = net in placement.rail_y
    spine_at = placement.rail_y[net] if horizontal else placement.columns.get(net)
    if spine_at is None:
        # A net with no column and no rail (a stray net referenced by terminals
        # the netlist never declared): chain them so nothing dangles.
        return _chain(net, points)

    stubs: list[Wire] = []
    connections: list[Point] = []
    for point in points:
        polyline = _route_to_spine(obstacles, net, point, spine_at, horizontal)
        connections.append(polyline[-1])
        if len(polyline) > 1:
            stubs.append(Wire(net=net, points=polyline))
    axis = 0 if horizontal else 1
    along = sorted(point[axis] for point in connections)
    wires: list[Wire] = []
    if along[0] != along[-1]:
        ends = (
            [(along[0], spine_at), (along[-1], spine_at)]
            if horizontal
            else [(spine_at, along[0]), (spine_at, along[-1])]
        )
        wires.append(Wire(net=net, points=ends))
    wires.extend(stubs)
    return wires


def _route_to_spine(
    obstacles: _Obstacles,
    net: str,
    pin: Point,
    spine_at: int,
    horizontal: bool,
) -> list[Point]:
    """Return the polyline from *pin* to the spine, ending on it.

    Tried in order: the terminal is already on the spine; a straight
    perpendicular segment; then Z routes that first slide along the terminal's
    own line, alternating outwards, until one is clear.
    """
    if (pin[1] if horizontal else pin[0]) == spine_at:
        return [pin]
    straight = _perpendicular(pin, spine_at, horizontal)
    if _clear(obstacles, net, [pin, straight]):
        return [pin, straight]
    for step in _detours():
        # Slide along the spine's own axis first, then cross: a stub to a
        # horizontal rail steps sideways, a stub to a vertical column steps up
        # or down.
        via = (pin[0] + step, pin[1]) if horizontal else (pin[0], pin[1] + step)
        route = [pin, via, _perpendicular(via, spine_at, horizontal)]
        if _clear(obstacles, net, route):
            return route
    # Still nothing: leave the terminal *away* from the spine first, so the
    # first leg clears whatever sits against it, then cross and land.  This is
    # what a device terminal needs when its own body blocks every direct route.
    for aside in _detours():
        for step in _detours():
            corner = (
                (pin[0], pin[1] + aside) if horizontal else (pin[0] + aside, pin[1])
            )
            via = (
                (corner[0] + step, corner[1])
                if horizontal
                else (corner[0], corner[1] + step)
            )
            route = [pin, corner, via, _perpendicular(via, spine_at, horizontal)]
            if _clear(obstacles, net, route):
                return route
    return [pin, straight]


def _clear(obstacles: _Obstacles, net: str, route: list[Point]) -> bool:
    """Return ``True`` when every leg of *route* may be drawn, and none is empty."""
    legs = list(pairwise(route))
    if any(start == end for start, end in legs):
        return False
    return not any(obstacles.blocked(net, start, end) for start, end in legs)


def _detours() -> list[int]:
    """Return the sideways offsets a Z route tries, nearest first."""
    offsets: list[int] = []
    for index in range(1, MAX_DETOURS // 2 + 1):
        offsets.extend((DETOUR_STEP * index, -DETOUR_STEP * index))
    return offsets


def _perpendicular(point: Point, spine_at: int, horizontal: bool) -> Point:
    """Return where a perpendicular from *point* meets the spine."""
    return (point[0], spine_at) if horizontal else (spine_at, point[1])


def _chain(net: str, points: list[Point]) -> list[Wire]:
    """Join *points* with orthogonal L routes, in order; a last-resort fallback."""
    wires = []
    for start, end in pairwise(points):
        if start[0] == end[0] or start[1] == end[1]:
            wires.append(Wire(net=net, points=[start, end]))
        else:
            wires.append(Wire(net=net, points=[start, (end[0], start[1]), end]))
    return wires


# --- geometry helpers --------------------------------------------------------


def _on_segment(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* lies anywhere on the closed segment."""
    if start[0] == end[0]:
        return point[0] == start[0] and (
            min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )
    if start[1] == end[1]:
        return point[1] == start[1] and (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
        )
    return False


def _strictly_inside(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* lies strictly between *start* and *end*."""
    return point not in (start, end) and _on_segment(point, start, end)


def _crosses_box(start: Point, end: Point, box: Box) -> bool:
    """Return ``True`` when the segment passes through a box's interior."""
    x0, y0, x1, y1 = box
    if start[0] == end[0]:
        low, high = min(start[1], end[1]), max(start[1], end[1])
        return x0 < start[0] < x1 and low < y1 and high > y0
    if start[1] == end[1]:
        low, high = min(start[0], end[0]), max(start[0], end[0])
        return y0 < start[1] < y1 and low < x1 and high > x0
    return False


def _span_overlap(first: tuple[int, int], second: tuple[int, int]) -> int:
    return min(max(first), max(second)) - max(min(first), min(second))


def _runs_along(component: Segment, wire: Segment) -> bool:
    """Return ``True`` when a wire would be drawn on top of a path component.

    circuitikz draws a two-terminal component *along* its segment, so a wire
    sharing any of that segment is drawn through the body of the part — it
    reads as a connection to the middle of a resistor, which means nothing.
    A wire ending strictly inside the segment is just as wrong, and so is a
    wire whose end lands there. Crossing the component at right angles is left
    alone: it is an ordinary wire crossing, and the metrics count it.
    """
    if _overlaps(component, wire):
        return True
    return any(_strictly_inside(end, *component) for end in wire)


def _overlaps(first: Segment, second: Segment) -> bool:
    """Return ``True`` when two segments lie along each other, not merely cross."""
    (a1, a2), (b1, b2) = first, second
    if a1[0] == a2[0] and b1[0] == b2[0] and a1[0] == b1[0]:
        return _span_overlap((a1[1], a2[1]), (b1[1], b2[1])) > 0
    if a1[1] == a2[1] and b1[1] == b2[1] and a1[1] == b1[1]:
        return _span_overlap((a1[0], a2[0]), (b1[0], b2[0])) > 0
    return False


# --- net symbols -------------------------------------------------------------


def _net_symbols(
    placement: Placement,
    net: str,
    points: list[Point],
    supply_marker: Point | None = None,
) -> list[NetSymbol]:
    """Return the ground, supply, or tap markers this net carries (D7)."""
    graph = placement.graph
    if graph.is_ground(net):
        return [
            NetSymbol(
                net=net, variant="ground", at=_rail_marker(placement, net, points)
            )
        ]
    if net in graph.supply_nets:
        marker = supply_marker or _rail_marker(placement, net, points)
        return [
            NetSymbol(
                net=net,
                variant="vcc",
                at=marker,
                text=_supply_text(placement, net),
            )
        ]
    if net in (placement.input_net, placement.output_net):
        top = max(points, key=lambda point: (point[1], point[0]))
        return [
            NetSymbol(
                net=net,
                variant="tap",
                at=top,
                rot=_free_direction(placement, top),
                text=net,
            )
        ]
    return []


LABEL_REACH: Final = 4
"""How far a label needs to be clear of anything before it is worth putting there."""

_DIRECTIONS: Final[tuple[tuple[Rotation, tuple[int, int]], ...]] = (
    (90, (0, 1)),
    (0, (1, 0)),
    (270, (0, -1)),
    (180, (-1, 0)),
)
"""Rotations the emitter turns into above / right / below / left, best first."""


def _free_direction(placement: Placement, at: Point) -> Rotation:
    """Return the rotation whose side of *at* has room for a label.

    A tap sits on a terminal, and a terminal usually has a component on one
    side and a wire on another. Dropping the text on whichever side is empty is
    the difference between a readable label and one printed over a transistor.
    """
    for rot, (dx, dy) in _DIRECTIONS:
        probes = [
            (at[0] + dx * step, at[1] + dy * step) for step in range(1, LABEL_REACH + 1)
        ]
        if not any(_occupied(placement, probe) for probe in probes):
            return rot
    return 90


def _occupied(placement: Placement, point: Point) -> bool:
    """Return ``True`` when something is drawn at *point*."""
    for element in placement.components:
        if isinstance(element, PathComponent):
            if _on_segment(point, element.a, element.b):
                return True
            continue
        size = placement.symbol(element.symbol).size
        half_w, half_h = size[0] / 2, size[1] / 2
        if (
            abs(point[0] - element.at[0]) <= half_w
            and abs(point[1] - element.at[1]) <= half_h
        ):
            return True
    return False


SUPPLY_STUB: Final = 2
"""How far past the last terminal a supply glyph sits, in grid units."""


def _supply_marker(placement: Placement, net: str, points: list[Point]) -> Point | None:
    """Return where a supply arrow goes: just past the right end of its rail.

    Not in the middle. A glyph placed between two terminals is a third
    conductor meeting there, so it earns a junction dot, and the arrow, the dot
    and the voltage label end up printed on top of each other. Past the last
    terminal only the rail's own end meets it — two conductors, no dot — which
    is how a supply rail is drawn by hand.
    """
    rail_y = placement.rail_y.get(net)
    if rail_y is None or net not in placement.graph.supply_nets:
        return None
    on_rail = [point[0] for point in points if point[1] == rail_y]
    if not on_rail:
        return None
    return (max(on_rail) + SUPPLY_STUB, rail_y)


def _rail_marker(placement: Placement, net: str, points: list[Point]) -> Point:
    """Return where a rail's symbol goes: near the middle, but clear of things.

    The exact midpoint is often a terminal, and a ground or supply glyph drawn
    on top of one collides with the junction dot and with the label. Search
    outwards from the middle for a spot on the rail that nothing else occupies.
    """
    rail_y = placement.rail_y[net]
    on_rail = [point[0] for point in points if point[1] == rail_y] or [
        point[0] for point in points
    ]
    low, high = min(on_rail), max(on_rail)
    middle = (low + high) // 2
    taken = {point[0] for point in points if point[1] == rail_y}
    for offset in range(0, (high - low) // 2 + 1):
        for candidate in (middle - offset, middle + offset):
            if low <= candidate <= high and candidate not in taken:
                return (candidate, rail_y)
    return (middle, rail_y)


def _supply_text(placement: Placement, net: str) -> str:
    """Return the label a supply rail carries.

    Just the net name when a source drawn on this sheet already shows the
    voltage beside its own symbol.  Repeating it on the rail states the same
    fact twice and attaches it to the net rather than to the component that
    establishes it, which is not how a schematic is read (§1 of the change
    request).  A rail whose source is *not* drawn — a supply the netlist only
    declares — still carries its voltage, since nothing else would say it.
    """
    declared = placement.graph.nets.get(net)
    if declared is None or declared.supply_voltage is None:
        return net
    if _voltage_is_shown_on_a_source(placement, net):
        return net
    return f"{net} = {declared.supply_voltage.raw}"


def _voltage_is_shown_on_a_source(placement: Placement, net: str) -> bool:
    """Return ``True`` when a source on *net* already carries its value label."""
    graph = placement.graph
    drawn = {element.ref for element in placement.components}
    for terminal in graph.terminals.get(net, ()):
        if terminal.component not in drawn:
            continue
        component = graph.components.get(terminal.component)
        if component is not None and source_value(component) is not None:
            return True
    return False


# --- junctions ---------------------------------------------------------------


def _junctions(elements: list[Element]) -> list[Junction]:
    """Return a dot at every point where three or more conductors meet.

    The counting rule is ``validate.py``'s: a wire end counts one, a wire
    passing through counts two, and each terminal or conductive net symbol
    counts one.  Computing it here rather than guessing is what makes invariant
    10 hold by construction.
    """
    pins = _pin_points(elements)
    wires = [element for element in elements if isinstance(element, Wire)]
    markers = [
        element.at
        for element in elements
        if isinstance(element, NetSymbol) and element.variant != "tap"
    ]
    candidates = sorted({*pins, *markers, *(p for wire in wires for p in wire.points)})
    dots = []
    for point in candidates:
        count = sum(1 for pin in pins if pin == point)
        count += sum(1 for marker in markers if marker == point)
        for wire in wires:
            for start, end in wire.segments():
                if point in (start, end):
                    count += 1
                elif _strictly_inside(point, start, end):
                    count += 2
        if count >= MIN_JUNCTION_CONDUCTORS:
            dots.append(Junction(at=point))
    return dots


def _pin_points(elements: list[Element]) -> list[Point]:
    """Return every component terminal position among *elements*."""
    points: list[Point] = []
    for element in elements:
        if isinstance(element, PathComponent):
            points.extend((element.a, element.b))
        elif isinstance(element, NodeComponent):
            points.extend(element.pins.values())
    return points


def title_label(text: str, at: Point) -> Label:
    """Return a free-standing label, used for the sheet title."""
    return Label(at=at, text=text, anchor="south")
