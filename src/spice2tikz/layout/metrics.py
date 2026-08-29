"""Layout quality metrics (roadmap §5.4).

Four numbers, measured on a finished Schematic IR sheet:

``crossings``
    pairs of wire segments from *different* nets that cross at a point interior
    to both.  Crossings are the single most legible-versus-illegible property
    of a schematic.
``wire_length``
    total orthogonal wire length in grid units.  Shorter is tidier, and it also
    proxies for how far apart related things ended up.
``bbox_area``
    the drawing's bounding-box area in grid units.  A sprawling layout wastes
    page space.
``alignment``
    the fraction of component endpoints that share a row or a column with some
    other component's endpoint, in ``[0, 1]``.  Aligned parts read as a
    circuit; scattered ones read as a graph.

These are a **ratchet**, not a score: the regression test in
``tests/test_layout.py`` asserts no metric gets worse, which is a claim about
this project's own history rather than about layout quality in the abstract
(``docs/DESIGN.md`` §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..schematic_ir import (
    Element,
    NodeComponent,
    PathComponent,
    SchematicIR,
    Wire,
)
from ..symbols import Point

Segment = tuple[Point, Point]

BETTER_WHEN_LOWER = ("crossings", "wire_length", "bbox_area")
BETTER_WHEN_HIGHER = ("alignment",)


@dataclass(frozen=True)
class Metrics:
    """Measured quality of one laid-out sheet."""

    components: int
    wires: int
    crossings: int
    wire_length: int
    bbox_area: int
    alignment: float

    def to_json(self) -> dict[str, Any]:
        """Serialise, rounding ``alignment`` so the value is stable in a file."""
        return {
            "components": self.components,
            "wires": self.wires,
            "crossings": self.crossings,
            "wire_length": self.wire_length,
            "bbox_area": self.bbox_area,
            "alignment": round(self.alignment, 3),
        }


def measure(ir: SchematicIR) -> Metrics:
    """Measure ``ir.sheets[0]``."""
    elements = ir.sheets[0].elements if ir.sheets else []
    wires = [element for element in elements if isinstance(element, Wire)]
    components = [
        element
        for element in elements
        if isinstance(element, PathComponent | NodeComponent)
    ]
    segments = [(segment, wire.net) for wire in wires for segment in wire.segments()]
    return Metrics(
        components=len(components),
        wires=len(wires),
        crossings=_crossings(segments),
        wire_length=sum(_length(segment) for segment, _ in segments),
        bbox_area=_bbox_area(elements),
        alignment=_alignment(components),
    )


def _length(segment: Segment) -> int:
    (x1, y1), (x2, y2) = segment
    return abs(x2 - x1) + abs(y2 - y1)


def _crossings(segments: list[tuple[Segment, str]]) -> int:
    """Count perpendicular crossings between segments of different nets."""
    total = 0
    for index, (first, net_a) in enumerate(segments):
        for second, net_b in segments[index + 1 :]:
            if net_a == net_b:
                continue
            if _crosses(first, second):
                total += 1
    return total


def _crosses(first: Segment, second: Segment) -> bool:
    """Return ``True`` when the two orthogonal segments properly cross."""
    horizontal, vertical = _orient(first, second)
    if horizontal is None or vertical is None:
        return False
    (hx1, hy), (hx2, _) = horizontal
    (vx, vy1), (_, vy2) = vertical
    return min(hx1, hx2) < vx < max(hx1, hx2) and min(vy1, vy2) < hy < max(vy1, vy2)


def _orient(first: Segment, second: Segment) -> tuple[Segment | None, Segment | None]:
    """Return ``(horizontal, vertical)`` when the pair is one of each."""
    first_horizontal = first[0][1] == first[1][1]
    second_horizontal = second[0][1] == second[1][1]
    if first_horizontal and not second_horizontal:
        return first, second
    if second_horizontal and not first_horizontal:
        return second, first
    return None, None


def _bbox_area(elements: list[Element]) -> int:
    """Return the area of the drawing's bounding box, in grid units."""
    points = [point for element in elements for point in _points(element)]
    if not points:
        return 0
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _points(element: Element) -> list[Point]:
    """Return every grid point *element* occupies."""
    if isinstance(element, PathComponent):
        return [element.a, element.b]
    if isinstance(element, NodeComponent):
        return [element.at, *element.pins.values()]
    if isinstance(element, Wire):
        return list(element.points)
    return [element.at]


def _alignment(components: list[PathComponent | NodeComponent]) -> float:
    """Return the share of component endpoints aligned with another component's."""
    owned: list[tuple[int, Point]] = []
    for index, element in enumerate(components):
        for point in _points(element):
            owned.append((index, point))
    if not owned:
        return 1.0
    aligned = 0
    for index, point in owned:
        if any(
            other != index and (point[0] == candidate[0] or point[1] == candidate[1])
            for other, candidate in owned
        ):
            aligned += 1
    return aligned / len(owned)


def format_metrics(name: str, metrics: Metrics) -> str:
    """Return a one-line ``-v`` report of *metrics*."""
    return (
        f"{name}: {metrics.components} component(s), {metrics.wires} wire(s), "
        f"{metrics.crossings} crossing(s), wire length {metrics.wire_length}, "
        f"bbox area {metrics.bbox_area}, alignment {metrics.alignment:.2f}"
    )
