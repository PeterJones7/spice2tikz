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
from ..symbols import Point
from .place import Placement

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

    def blocked(self, net: str, start: Point, end: Point) -> bool:
        """Return ``True`` when a wire of *net* may not run from *start* to *end*."""
        for point, owners in self.pins.items():
            if point in (start, end):
                continue
            if net not in owners and _on_segment(point, start, end):
                return True
        if any(_crosses_box(start, end, box) for box in self.bodies):
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
        net_wires = _wire_net(placement, obstacles, net, points)
        for wire in net_wires:
            for segment in wire.segments():
                obstacles.drawn.append((segment, net))
        wires.extend(net_wires)
        symbols.extend(_net_symbols(placement, net, points))
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
    if not obstacles.blocked(net, pin, straight):
        return [pin, straight]
    for step in _detours():
        # Slide along the spine's own axis first, then cross: a stub to a
        # horizontal rail steps sideways, a stub to a vertical column steps up
        # or down.
        via = (pin[0] + step, pin[1]) if horizontal else (pin[0], pin[1] + step)
        landing = _perpendicular(via, spine_at, horizontal)
        if not obstacles.blocked(net, pin, via) and not obstacles.blocked(
            net, via, landing
        ):
            return [pin, via, landing]
    return [pin, straight]


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
    placement: Placement, net: str, points: list[Point]
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
        return [
            NetSymbol(
                net=net,
                variant="vcc",
                at=_rail_marker(placement, net, points),
                text=_supply_text(placement, net),
            )
        ]
    if net in (placement.input_net, placement.output_net):
        top = max(points, key=lambda point: (point[1], point[0]))
        # rot 90 puts the emitter's text above the point rather than beside it,
        # where it would sit on top of the wire arriving from the left.
        return [NetSymbol(net=net, variant="tap", at=top, rot=90, text=net)]
    return []


def _rail_marker(placement: Placement, net: str, points: list[Point]) -> Point:
    """Return where a rail's symbol goes: the middle of the rail."""
    rail_y = placement.rail_y[net]
    on_rail = [point[0] for point in points if point[1] == rail_y] or [
        point[0] for point in points
    ]
    return ((min(on_rail) + max(on_rail)) // 2, rail_y)


def _supply_text(placement: Placement, net: str) -> str:
    """Return the label a supply rail carries: its name, and its voltage if known."""
    declared = placement.graph.nets.get(net)
    if declared is not None and declared.supply_voltage is not None:
        return f"{net} = {declared.supply_voltage.raw}"
    return net


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
