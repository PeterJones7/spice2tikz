"""The layout entry point: Netlist IR in, Schematic IR out (roadmap §5).

This module is only the assembly line.  The judgement lives in
:mod:`~spice2tikz.layout.graph` (what is connected to what, and which way the
signal runs), :mod:`~spice2tikz.layout.place` (where things go) and
:mod:`~spice2tikz.layout.route` (how they are joined).
"""

from __future__ import annotations

from ..netlist_ir import NetlistIR, Scope
from ..schematic_ir import (
    Element,
    Grid,
    NodeComponent,
    PathComponent,
    SchematicIR,
    SchematicMeta,
    Sheet,
    StyleDefaults,
    Wire,
)
from ..symbols import Point, SymbolDef
from .graph import (
    build_graph,
    input_net,
    pick_input_source,
    pick_output_net,
    rank_nets,
)
from .place import place
from .route import route

GENERATOR = "spice2tikz"
"""Written into ``meta.generator``.

Deliberately version-free: embedding ``__version__`` would rewrite every golden
file on each release, against the determinism promise (CLAUDE.md rule 4).
"""


def layout(
    ir: NetlistIR,
    *,
    style: StyleDefaults | None = None,
    warnings: list[str] | None = None,
) -> SchematicIR:
    """Lay out *ir*'s top-level circuit and return a Schematic IR document."""
    effective = style if style is not None else StyleDefaults()
    sheet, symbols = layout_scope(
        ir, ir.circuit, siunitx=effective.siunitx, warnings=warnings
    )
    return SchematicIR(
        meta=SchematicMeta(
            title=ir.meta.title,
            source_netlist=ir.meta.source,
            generator=GENERATOR,
            grid=Grid(),
        ),
        style=style,
        symbols=symbols,
        sheets=[sheet],
    )


def layout_scope(
    ir: NetlistIR,
    scope: Scope,
    *,
    siunitx: bool = True,
    warnings: list[str] | None = None,
) -> tuple[Sheet, dict[str, SymbolDef]]:
    """Lay out one scope and return its sheet and the symbols it generated."""
    graph = build_graph(ir, scope)
    source = pick_input_source(graph)
    start = input_net(graph, source)
    ranks = rank_nets(graph, start)
    output = pick_output_net(graph, ranks)
    placement = place(
        graph,
        ranks,
        input_net=start,
        output_net=output,
        siunitx=siunitx,
        warnings=warnings,
    )
    elements = route(placement)
    _normalise(elements)
    return Sheet(name="main", elements=elements), dict(placement.symbols)


def _normalise(elements: list[Element]) -> None:
    """Shift the sheet so its lowest-left point sits at the origin.

    Placement is free to use negative coordinates — a supply source is put to
    the left of column zero, a second ground rail below the first — because it
    is easier to reason about offsets from the main row than about absolute
    positions.  Everything is translated back here, once, so the emitted
    drawing starts at ``(0, 0)`` like a hand-written one.
    """
    points = [point for element in elements for point in _mutable_points(element)]
    if not points:
        return
    shift = (-min(point[0] for point in points), -min(point[1] for point in points))
    if shift == (0, 0):
        return
    for element in elements:
        _translate(element, shift)


def _mutable_points(element: Element) -> list[Point]:
    """Return every grid point *element* occupies."""
    if isinstance(element, PathComponent):
        return [element.a, element.b]
    if isinstance(element, NodeComponent):
        return [element.at, *element.pins.values()]
    if isinstance(element, Wire):
        return list(element.points)
    return [element.at]


def _translate(element: Element, shift: Point) -> None:
    """Move one element by *shift*, in place."""
    dx, dy = shift
    if isinstance(element, PathComponent):
        element.a = (element.a[0] + dx, element.a[1] + dy)
        element.b = (element.b[0] + dx, element.b[1] + dy)
        return
    if isinstance(element, NodeComponent):
        element.at = (element.at[0] + dx, element.at[1] + dy)
        element.pins = {
            pin: (point[0] + dx, point[1] + dy) for pin, point in element.pins.items()
        }
        return
    if isinstance(element, Wire):
        element.points = [(x + dx, y + dy) for x, y in element.points]
        return
    element.at = (element.at[0] + dx, element.at[1] + dy)
