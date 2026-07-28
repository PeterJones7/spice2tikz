"""IR validation: the invariants of ``docs/SPEC_IR.md`` §4.

:func:`validate` returns a list of ``(severity, message, location)`` findings
in a fixed order — invariant by invariant, then document order — so reports
are deterministic.  Errors mean the document cannot be trusted (CLI exit
code 2); warnings are drawing-quality complaints that do not stop the
pipeline.

Invariants 1-5 apply to the Netlist IR, 6-13 to the Schematic IR; the
numbering below matches the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from .netlist_ir import (
    CONTROL_KINDS,
    Component,
    Kind,
    NetlistIR,
    Scope,
    generic_pin_names,
    optional_pins,
    required_pins,
)
from .schematic_ir import (
    ComponentElement,
    Junction,
    Label,
    NetSymbol,
    NodeComponent,
    PathComponent,
    Port,
    SchematicIR,
    Sheet,
    Wire,
)
from .symbols import Point, SymbolDef, lookup_symbol, resolve_pins, rotated_size

MIN_PATH_LENGTH = 2
"""Grid units a path component needs to be drawable (``docs/DESIGN.md`` §7)."""

MIN_JUNCTION_CONDUCTORS = 3
"""Conductors that must meet at a point for a junction to be meaningful."""


class Severity(str, Enum):
    """How bad a finding is."""

    ERROR = "error"
    WARNING = "warning"

    def __str__(self) -> str:
        return self.value


class Finding(NamedTuple):
    """One validation result: its severity, what is wrong, and where."""

    severity: Severity
    message: str
    location: str


def validate(ir: NetlistIR | SchematicIR) -> list[Finding]:
    """Validate either IR, dispatching on its type."""
    if isinstance(ir, NetlistIR):
        return validate_netlist(ir)
    return validate_schematic(ir)


def has_errors(findings: list[Finding]) -> bool:
    """Return ``True`` when any finding is an error."""
    return any(finding.severity is Severity.ERROR for finding in findings)


def count_by_severity(findings: list[Finding]) -> tuple[int, int]:
    """Return the ``(errors, warnings)`` counts of *findings*."""
    errors = sum(1 for finding in findings if finding.severity is Severity.ERROR)
    return errors, len(findings) - errors


def _plural(count: float, noun: str) -> str:
    """Return ``"1 wire"`` / ``"2 wires"`` for a count and a singular noun."""
    return f"{count:g} {noun}" if count == 1 else f"{count:g} {noun}s"


def format_finding(finding: Finding) -> str:
    """Render a finding as a one-line diagnostic."""
    return f"{finding.severity}: {finding.location}: {finding.message}"


# --- Netlist IR -------------------------------------------------------------


def validate_netlist(ir: NetlistIR) -> list[Finding]:
    """Check invariants 1-5 of ``docs/SPEC_IR.md`` §4."""
    findings: list[Finding] = []
    _check_ids_and_pin_names(ir, findings)
    _check_nets(ir, findings)
    _check_controls(ir, findings)
    _check_subcircuit_references(ir, findings)
    _check_ground_nets(ir, findings)
    return findings


def _component_location(scope_name: str, index: int, component: Component) -> str:
    return f"{scope_name}.components[{index}] ({component.id})"


def _check_ids_and_pin_names(ir: NetlistIR, findings: list[Finding]) -> None:
    """Invariant 1: ids unique per scope, pin names match the kind taxonomy."""
    for scope_name, scope in ir.scopes():
        seen: set[str] = set()
        for index, component in enumerate(scope.components):
            location = _component_location(scope_name, index, component)
            if component.id in seen:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"duplicate component id {component.id!r} in this scope",
                        location,
                    )
                )
            seen.add(component.id)
            _check_pin_names(component, location, findings)


def _check_pin_names(
    component: Component,
    location: str,
    findings: list[Finding],
) -> None:
    if component.kind is Kind.GENERIC:
        expected = generic_pin_names(len(component.pins))
        if tuple(component.pins) != expected:
            findings.append(
                Finding(
                    Severity.ERROR,
                    "generic component pins must be named "
                    f"{', '.join(expected) or '(none)'}, got "
                    f"{', '.join(component.pins) or '(none)'}",
                    location,
                )
            )
        return
    if component.kind is Kind.SUBCIRCUIT:
        # Pin names come from the definition; invariant 4 checks them.
        return
    allowed = required_pins(component.kind)
    optional = optional_pins(component.kind)
    for pin in component.pins:
        if pin not in allowed and pin not in optional:
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"pin {pin!r} is not a pin of kind {component.kind}"
                    f" (expected {', '.join(allowed + optional)})",
                    location,
                )
            )
    for pin in allowed:
        if pin not in component.pins:
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"kind {component.kind} requires pin {pin!r}",
                    location,
                )
            )


def _check_nets(ir: NetlistIR, findings: list[Finding]) -> None:
    """Invariant 2: pins reference existing nets, and net ids match names."""
    for scope_name, scope in ir.scopes():
        for net_id, net in scope.nets.items():
            if net.name != net_id:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"net id {net_id!r} does not match its name {net.name!r}",
                        f"{scope_name}.nets[{net_id!r}]",
                    )
                )
        for index, component in enumerate(scope.components):
            location = _component_location(scope_name, index, component)
            for pin, net_id in component.pins.items():
                if net_id not in scope.nets:
                    findings.append(
                        Finding(
                            Severity.ERROR,
                            f"pin {pin!r} references undeclared net {net_id!r}",
                            location,
                        )
                    )


def _check_controls(ir: NetlistIR, findings: list[Finding]) -> None:
    """Invariant 3: ``control`` names an existing voltage source."""
    for scope_name, scope in ir.scopes():
        sources = {
            component.id
            for component in scope.components
            if component.kind is Kind.VSOURCE
        }
        known = {component.id for component in scope.components}
        for index, component in enumerate(scope.components):
            location = _component_location(scope_name, index, component)
            if component.control is None:
                if component.kind in CONTROL_KINDS:
                    findings.append(
                        Finding(
                            Severity.ERROR,
                            f"kind {component.kind} requires a controlling "
                            "voltage source in 'control'",
                            location,
                        )
                    )
                continue
            if component.control not in known:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"control references unknown component {component.control!r}",
                        location,
                    )
                )
            elif component.control not in sources:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"control references {component.control!r}, which is "
                        "not a vsource",
                        location,
                    )
                )


def _check_subcircuit_references(ir: NetlistIR, findings: list[Finding]) -> None:
    """Invariant 4: ``subckt`` resolves and its pins match the definition ports."""
    for scope_name, scope in ir.scopes():
        for index, component in enumerate(scope.components):
            location = _component_location(scope_name, index, component)
            if component.kind is Kind.SUBCIRCUIT and component.subckt is None:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        "kind subcircuit requires a definition name in 'subckt'",
                        location,
                    )
                )
                continue
            if component.subckt is None:
                continue
            definition = ir.subcircuits.get(component.subckt.lower())
            if definition is None:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"unknown subcircuit definition {component.subckt!r}",
                        location,
                    )
                )
                continue
            if tuple(component.pins) != tuple(definition.ports):
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"pins {', '.join(component.pins) or '(none)'} do not match "
                        f"the ports of subcircuit {component.subckt!r} "
                        f"({', '.join(definition.ports) or '(none)'})",
                        location,
                    )
                )


def _check_ground_nets(ir: NetlistIR, findings: list[Finding]) -> None:
    """Invariant 5: exactly one ground-class net in the top-level circuit."""
    grounds = _ground_nets(ir.circuit)
    if not grounds:
        findings.append(
            Finding(
                Severity.WARNING,
                "no ground-class net: the schematic will have no reference node",
                "circuit.nets",
            )
        )
    elif len(grounds) > 1:
        findings.append(
            Finding(
                Severity.WARNING,
                f"{len(grounds)} ground-class nets ({', '.join(grounds)}); "
                "a flat design should have exactly one",
                "circuit.nets",
            )
        )


def _ground_nets(scope: Scope) -> list[str]:
    return [net_id for net_id, net in scope.nets.items() if net.net_class == "ground"]


# --- Schematic IR -----------------------------------------------------------


def validate_schematic(ir: SchematicIR) -> list[Finding]:
    """Check invariants 6-13 of ``docs/SPEC_IR.md`` §4."""
    findings: list[Finding] = []
    for index, sheet in enumerate(ir.sheets):
        context = _SheetContext.build(ir, sheet, index)
        _check_geometry(context, findings)
        _check_path_lengths(context, findings)
        _check_node_pins(context, findings)
        _check_wire_endpoints(context, findings)
        _check_junctions(context, findings)
        _check_symbol_references(context, findings)
        _check_overlaps(context, findings)
        _check_duplicate_refs(context, findings)
    return findings


@dataclass
class _Placed:
    """A component of a sheet together with its location and geometry."""

    index: int
    element: PathComponent | NodeComponent
    location: str
    symbol: SymbolDef | None = None
    orthogonal: bool = True


@dataclass
class _SheetContext:
    """Pre-computed views of one sheet, shared by the invariant checks."""

    ir: SchematicIR
    sheet: Sheet
    prefix: str
    components: list[_Placed] = field(default_factory=list)
    wires: list[tuple[int, Wire]] = field(default_factory=list)
    junctions: list[tuple[int, Junction]] = field(default_factory=list)
    net_symbols: list[tuple[int, NetSymbol]] = field(default_factory=list)
    ports: list[tuple[int, Port]] = field(default_factory=list)
    labels: list[tuple[int, Label]] = field(default_factory=list)

    @classmethod
    def build(cls, ir: SchematicIR, sheet: Sheet, sheet_index: int) -> _SheetContext:
        """Index the elements of *sheet* and resolve their symbols."""
        prefix = f"sheets[{sheet_index}]"
        context = cls(ir=ir, sheet=sheet, prefix=prefix)
        for index, element in enumerate(sheet.elements):
            if isinstance(element, ComponentElement):
                placed = _Placed(
                    index=index,
                    element=element,
                    location=cls._element_location(prefix, index, element.ref),
                )
                if isinstance(element, NodeComponent):
                    placed.symbol = lookup_symbol(element.symbol, ir.symbols)
                context.components.append(placed)
            elif isinstance(element, Wire):
                context.wires.append((index, element))
            elif isinstance(element, Junction):
                context.junctions.append((index, element))
            elif isinstance(element, NetSymbol):
                context.net_symbols.append((index, element))
            elif isinstance(element, Port):
                context.ports.append((index, element))
            else:
                context.labels.append((index, element))
        return context

    @staticmethod
    def _element_location(prefix: str, index: int, ref: str | None = None) -> str:
        suffix = f" ({ref})" if ref else ""
        return f"{prefix}.elements[{index}]{suffix}"

    def location(self, index: int, ref: str | None = None) -> str:
        """Return the report location of the element at *index*."""
        return self._element_location(self.prefix, index, ref)

    def pin_points(self) -> list[Point]:
        """Return every component pin position on the sheet."""
        points: list[Point] = []
        for placed in self.components:
            if isinstance(placed.element, PathComponent):
                points.extend((placed.element.a, placed.element.b))
            else:
                points.extend(placed.element.pins.values())
        return points

    def conductor_points(self) -> list[Point]:
        """Return the points worth checking for junctions."""
        points: list[Point] = list(self.pin_points())
        for _, wire in self.wires:
            points.extend(wire.points)
        points.extend(
            net_symbol.at
            for _, net_symbol in self.net_symbols
            if _is_conductive(net_symbol)
        )
        points.extend(port.at for _, port in self.ports)
        points.extend(junction.at for _, junction in self.junctions)
        return points

    def conductor_count(self, point: Point) -> int:
        """Count conductors meeting at *point*.

        A wire contributes one conductor when *point* is one of its ends, two
        when the wire passes through it; component pins, ports, and connecting
        net symbols contribute one each.  ``tap`` net symbols are annotations,
        not conductors.
        """
        count = 0
        for _, wire in self.wires:
            for start, end in wire.segments():
                if not _is_orthogonal(start, end):
                    continue
                if point in (start, end):
                    count += 1
                elif _strictly_inside(point, start, end):
                    count += 2
        count += sum(1 for pin in self.pin_points() if pin == point)
        count += sum(
            1
            for _, net_symbol in self.net_symbols
            if _is_conductive(net_symbol) and net_symbol.at == point
        )
        count += sum(1 for _, port in self.ports if port.at == point)
        return count


def _is_conductive(net_symbol: NetSymbol) -> bool:
    return net_symbol.variant != "tap"


def _is_orthogonal(start: Point, end: Point) -> bool:
    """Return ``True`` when the two points differ in exactly one coordinate."""
    return (start[0] == end[0]) != (start[1] == end[1])


def _segment_length(start: Point, end: Point) -> float:
    return abs(end[0] - start[0]) + abs(end[1] - start[1])


def _strictly_inside(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* lies on the open segment ``start``-``end``."""
    if point in (start, end):
        return False
    if start[0] == end[0]:
        return point[0] == start[0] and min(start[1], end[1]) < point[1] < max(
            start[1], end[1]
        )
    if start[1] == end[1]:
        return point[1] == start[1] and min(start[0], end[0]) < point[0] < max(
            start[0], end[0]
        )
    return False


def _on_segment(point: Point, start: Point, end: Point) -> bool:
    """Return ``True`` when *point* lies anywhere on the closed segment."""
    return point in (start, end) or _strictly_inside(point, start, end)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_geometry(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 6: integer coordinates, orthogonal wires and path components."""
    for index, element in enumerate(context.sheet.elements):
        for name, point in _element_points(element):
            for axis, value in zip("xy", point, strict=True):
                if not _is_integer(value):
                    findings.append(
                        Finding(
                            Severity.ERROR,
                            f"{name} has non-integer {axis} coordinate {value!r}",
                            context.location(index, _ref_of(element)),
                        )
                    )
    for placed in context.components:
        element = placed.element
        if isinstance(element, PathComponent) and not _is_orthogonal(
            element.a, element.b
        ):
            placed.orthogonal = False
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"path component is not axis-aligned: {_fmt(element.a)} to "
                    f"{_fmt(element.b)}",
                    placed.location,
                )
            )
    for index, wire in context.wires:
        location = context.location(index)
        if len(wire.points) < 2:
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"wire on net {wire.net!r} needs at least two points",
                    location,
                )
            )
        for segment_index, (start, end) in enumerate(wire.segments()):
            if not _is_orthogonal(start, end):
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"wire segment {segment_index} is not axis-aligned: "
                        f"{_fmt(start)} to {_fmt(end)}",
                        location,
                    )
                )


def _check_path_lengths(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 7: path components need a segment of at least two units."""
    for placed in context.components:
        element = placed.element
        if not isinstance(element, PathComponent) or not placed.orthogonal:
            continue
        length = _segment_length(element.a, element.b)
        if length < MIN_PATH_LENGTH:
            findings.append(
                Finding(
                    Severity.WARNING,
                    f"path component spans {_plural(length, 'grid unit')}; "
                    f"circuitikz needs at least "
                    f"{_plural(MIN_PATH_LENGTH, 'grid unit')} to draw it legibly",
                    placed.location,
                )
            )


def _check_node_pins(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 8: declared node pins match the symbol geometry."""
    for placed in context.components:
        element = placed.element
        if not isinstance(element, NodeComponent) or placed.symbol is None:
            continue
        expected = resolve_pins(placed.symbol, element.at, element.rot, element.mirror)
        for pin, point in expected.items():
            if pin not in element.pins:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"pin {pin!r} of symbol {element.symbol!r} is missing "
                        f"(expected at {_fmt(point)})",
                        placed.location,
                    )
                )
            elif element.pins[pin] != point:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"pin {pin!r} is at {_fmt(element.pins[pin])} but symbol "
                        f"{element.symbol!r} at {_fmt(element.at)} rot "
                        f"{element.rot}{' mirrored' if element.mirror else ''} "
                        f"puts it at {_fmt(point)}",
                        placed.location,
                    )
                )
        for pin in element.pins:
            if pin not in expected:
                findings.append(
                    Finding(
                        Severity.ERROR,
                        f"pin {pin!r} is not a pin of symbol {element.symbol!r}",
                        placed.location,
                    )
                )


def _check_wire_endpoints(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 9: wire ends must connect to something."""
    anchors = set(context.pin_points())
    anchors.update(net_symbol.at for _, net_symbol in context.net_symbols)
    anchors.update(port.at for _, port in context.ports)
    for index, wire in context.wires:
        segment_count = len(wire.segments())
        if segment_count == 0:
            continue
        ends = ((0, wire.points[0]), (segment_count - 1, wire.points[-1]))
        for own_segment, end in ends:
            if end in anchors or _touches_a_wire(context, wire, end, own_segment):
                continue
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"dangling wire end at {_fmt(end)}: no component pin, wire, "
                    "net symbol, or port there",
                    context.location(index),
                )
            )


def _touches_a_wire(
    context: _SheetContext, wire: Wire, point: Point, own_segment: int
) -> bool:
    """Return ``True`` when *point* lies on a same-net wire other than its own end.

    The segment the end belongs to is skipped, so an end only counts as
    connected when some *other* stretch of wire reaches it.
    """
    for _, other in context.wires:
        if other.net != wire.net:
            continue
        for index, (start, end) in enumerate(other.segments()):
            if other is wire and index == own_segment:
                continue
            if _on_segment(point, start, end):
                return True
    return False


def _check_junctions(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 10: junctions where — and only where — conductors meet."""
    junction_points = {junction.at for _, junction in context.junctions}
    for index, junction in context.junctions:
        count = context.conductor_count(junction.at)
        if count < MIN_JUNCTION_CONDUCTORS:
            findings.append(
                Finding(
                    Severity.WARNING,
                    f"junction at {_fmt(junction.at)} joins "
                    f"{_plural(count, 'conductor')}; a dot is only meaningful "
                    f"from {MIN_JUNCTION_CONDUCTORS}",
                    context.location(index),
                )
            )
    for point in sorted(set(context.conductor_points())):
        if point in junction_points:
            continue
        count = context.conductor_count(point)
        if count >= MIN_JUNCTION_CONDUCTORS:
            findings.append(
                Finding(
                    Severity.WARNING,
                    f"{_plural(count, 'conductor')} meet at {_fmt(point)} "
                    "without a junction",
                    context.prefix,
                )
            )


def _check_symbol_references(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 11: node component symbols resolve."""
    for placed in context.components:
        element = placed.element
        if isinstance(element, NodeComponent) and placed.symbol is None:
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"unknown symbol {element.symbol!r}: not a built-in and not "
                    "declared in the file's symbols block",
                    placed.location,
                )
            )


def _check_overlaps(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 12: component bounding boxes should not overlap."""
    boxes: list[tuple[_Placed, tuple[int, int, int, int]]] = []
    for placed in context.components:
        box = _doubled_box(placed)
        if box is not None:
            boxes.append((placed, box))
    for first in range(len(boxes)):
        for second in range(first + 1, len(boxes)):
            placed_a, box_a = boxes[first]
            placed_b, box_b = boxes[second]
            if _boxes_overlap(box_a, box_b):
                findings.append(
                    Finding(
                        Severity.WARNING,
                        f"bounding box overlaps component {placed_b.element.ref!r}",
                        placed_a.location,
                    )
                )


def _doubled_box(placed: _Placed) -> tuple[int, int, int, int] | None:
    """Return the bounding box in half-grid units, or ``None`` if unknown.

    Doubling keeps odd-sized symbols exact: a symbol of size 3 centred on an
    integer point has half-integer edges.
    """
    element = placed.element
    if isinstance(element, PathComponent):
        if not all(_is_integer(value) for value in (*element.a, *element.b)):
            return None
        return (
            2 * min(element.a[0], element.b[0]),
            2 * min(element.a[1], element.b[1]),
            2 * max(element.a[0], element.b[0]),
            2 * max(element.a[1], element.b[1]),
        )
    if placed.symbol is None or not all(_is_integer(value) for value in element.at):
        return None
    width, height = rotated_size(placed.symbol.size, element.rot)
    return (
        2 * element.at[0] - width,
        2 * element.at[1] - height,
        2 * element.at[0] + width,
        2 * element.at[1] + height,
    )


def _boxes_overlap(
    box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]
) -> bool:
    """Return ``True`` for a real overlap, not a mere touch.

    Boxes that share only a point or an edge are how components connect, so
    they do not count.  Two flattened boxes lying along the same line do
    overlap, which is how two path components drawn on top of each other are
    caught.
    """
    width = min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])
    height = min(box_a[3], box_b[3]) - max(box_a[1], box_b[1])
    if width < 0 or height < 0:
        return False
    if width > 0 and height > 0:
        return True
    flat_a = (box_a[2] - box_a[0], box_a[3] - box_a[1])
    flat_b = (box_b[2] - box_b[0], box_b[3] - box_b[1])
    if height == 0 and width > 0:
        return flat_a[1] == 0 and flat_b[1] == 0
    if width == 0 and height > 0:
        return flat_a[0] == 0 and flat_b[0] == 0
    return False


def _check_duplicate_refs(context: _SheetContext, findings: list[Finding]) -> None:
    """Invariant 13: one refdes per sheet."""
    seen: set[str] = set()
    for placed in context.components:
        ref = placed.element.ref
        if ref in seen:
            findings.append(
                Finding(
                    Severity.ERROR,
                    f"duplicate ref {ref!r} on this sheet",
                    placed.location,
                )
            )
        seen.add(ref)


def _element_points(element: object) -> list[tuple[str, Point]]:
    if isinstance(element, PathComponent):
        return [("pin a", element.a), ("pin b", element.b)]
    if isinstance(element, NodeComponent):
        points = [("origin", element.at)]
        points.extend((f"pin {pin!r}", point) for pin, point in element.pins.items())
        return points
    if isinstance(element, Wire):
        return [(f"point {index}", point) for index, point in enumerate(element.points)]
    if isinstance(element, (Junction, NetSymbol, Port, Label)):
        return [("position", element.at)]
    return []


def _ref_of(element: object) -> str | None:
    return element.ref if isinstance(element, ComponentElement) else None


def _fmt(point: Point) -> str:
    return f"({point[0]:g}, {point[1]:g})"
