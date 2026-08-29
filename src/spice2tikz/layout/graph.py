"""Netlist IR → connectivity graph, with the heuristics placement needs (§5.1).

The graph is a thin, ordered view of one :class:`~spice2tikz.netlist_ir.Scope`:
which terminals sit on which net, which nets are rails, how far each net is
from the input source, and which components form series chains or parallel
groups.  Nothing here has coordinates; everything here is deterministic, and
every collection preserves source order so that two runs of the tool cannot
disagree (CLAUDE.md working rule 4).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Final

from ..netlist_ir import Component, Kind, Net, NetlistIR, Scope

PATH_KINDS: Final[frozenset[Kind]] = frozenset(
    {
        Kind.RESISTOR,
        Kind.CAPACITOR,
        Kind.INDUCTOR,
        Kind.DIODE,
        Kind.VSOURCE,
        Kind.ISOURCE,
        Kind.CCVS,
        Kind.CCCS,
    }
)
"""Kinds drawn as circuitikz *path* components, between two endpoints (D6).

``ccvs``/``cccs`` belong here because their controlling quantity is a refdes
(``Component.control``), not a pair of pins: they really are two-terminal.
``vcvs``/``vccs`` have four pins and so must be node components.
"""

SOURCE_KINDS: Final[frozenset[Kind]] = frozenset({Kind.VSOURCE, Kind.ISOURCE})

CONTROL_PINS: Final[dict[Kind, str]] = {
    Kind.NMOS: "g",
    Kind.PMOS: "g",
    Kind.NJFET: "g",
    Kind.PJFET: "g",
    Kind.BJT_NPN: "b",
    Kind.BJT_PNP: "b",
}
"""The 'input' pin of a three-terminal active device: gate or base."""

OUTPUT_PINS: Final[dict[Kind, str]] = {
    Kind.NMOS: "d",
    Kind.PMOS: "d",
    Kind.NJFET: "d",
    Kind.PJFET: "d",
    Kind.BJT_NPN: "c",
    Kind.BJT_PNP: "c",
}
"""The 'output' pin of a three-terminal active device: drain or collector."""

OUTPUT_NET_HINTS: Final[tuple[str, ...]] = ("out", "vout", "output", "vo")
"""Net names that name themselves as the output, tried in this order."""


@dataclass(frozen=True)
class Terminal:
    """One pin of one component, and the net it sits on."""

    component: str
    pin: str
    net: str


@dataclass
class CircuitGraph:
    """An ordered connectivity view of one scope.

    ``components`` and ``nets`` keep the netlist's own order, so the layout of
    a circuit changes only when the circuit does.
    """

    scope: Scope
    components: dict[str, Component] = field(default_factory=dict)
    nets: dict[str, Net] = field(default_factory=dict)
    terminals: dict[str, list[Terminal]] = field(default_factory=dict)
    ground_nets: tuple[str, ...] = ()
    supply_nets: tuple[str, ...] = ()
    signal_nets: tuple[str, ...] = ()

    def is_rail(self, net: str) -> bool:
        """Return ``True`` when *net* is drawn as a horizontal rail."""
        return net in self.ground_nets or net in self.supply_nets

    def is_ground(self, net: str) -> bool:
        """Return ``True`` when *net* carries the ground class."""
        return net in self.ground_nets

    def degree(self, net: str) -> int:
        """Return how many terminals sit on *net*."""
        return len(self.terminals.get(net, ()))

    def component_nets(self, ref: str) -> list[str]:
        """Return the nets of *ref*'s pins, in pin order, duplicates kept."""
        component = self.components[ref]
        return [component.pins[pin] for pin in _pin_sequence(component)]

    def is_path(self, ref: str) -> bool:
        """Return ``True`` when *ref* is drawn as a two-terminal path component."""
        component = self.components[ref]
        return component.kind in PATH_KINDS and len(component.pins) == 2


def _pin_sequence(component: Component) -> list[str]:
    """Return *component*'s pin names in a stable, spec-ordered sequence."""
    return list(component.pins)


def build_graph(ir: NetlistIR, scope: Scope | None = None) -> CircuitGraph:
    """Build the connectivity graph of *scope* (the top-level circuit by default)."""
    target = ir.circuit if scope is None else scope
    graph = CircuitGraph(scope=target)
    graph.components = {component.id: component for component in target.components}
    graph.nets = dict(target.nets)

    terminals: dict[str, list[Terminal]] = {name: [] for name in graph.nets}
    for component in target.components:
        for pin, net in component.pins.items():
            terminals.setdefault(net, []).append(
                Terminal(component=component.id, pin=pin, net=net)
            )
    graph.terminals = terminals

    ground: list[str] = []
    supply: list[str] = []
    signal: list[str] = []
    for name, declared in graph.nets.items():
        if declared.net_class == "ground":
            ground.append(name)
        elif declared.net_class == "supply":
            supply.append(name)
        else:
            signal.append(name)
    # A net referenced by a pin but missing from the net table is a validation
    # error, not a layout problem; treat it as a signal net so we still draw
    # something and let validate.py report it.
    for name in terminals:
        if name not in graph.nets:
            signal.append(name)
    graph.ground_nets = tuple(ground)
    graph.supply_nets = tuple(supply)
    graph.signal_nets = tuple(signal)
    return graph


# --- flow direction and ranking ---------------------------------------------


def flow_edges(graph: CircuitGraph, ref: str) -> list[tuple[str, str]]:
    """Return the directed net pairs *ref* propagates signal along.

    Two-terminal components conduct both ways.  A transistor is directional:
    signal enters at the gate or base and leaves at the drain or collector, so
    the ranking pushes its output one column to the right of its input, which
    is what makes an amplifier read left-to-right.  Everything else (boxes,
    subcircuits) is treated as fully connected, which is the safe default.
    """
    component = graph.components[ref]
    pins = component.pins
    control = CONTROL_PINS.get(component.kind)
    output = OUTPUT_PINS.get(component.kind)
    if control is not None and output is not None and control in pins:
        edges = []
        for pin, net in pins.items():
            if pin == control:
                continue
            edges.append((pins[control], net))
        return edges
    nets = [pins[pin] for pin in _pin_sequence(component)]
    return [
        (first, second)
        for index, first in enumerate(nets)
        for second in nets[index + 1 :]
    ] + [
        (second, first)
        for index, first in enumerate(nets)
        for second in nets[index + 1 :]
    ]


def rank_nets(graph: CircuitGraph, start: str | None) -> dict[str, int]:
    """Return each signal net's distance from *start*, in components traversed.

    Rails are excluded: ground and supply are drawn as horizontal rails, so
    they carry no column and must not act as shortcuts between distant parts of
    the circuit (every node in a circuit is two hops from every other one
    through ground).
    """
    ranks: dict[str, int] = {}
    if start is None or start not in graph.terminals:
        pass
    elif not graph.is_rail(start):
        ranks[start] = 0
        queue: deque[str] = deque([start])
        while queue:
            net = queue.popleft()
            for neighbour in _neighbours(graph, net):
                if neighbour in ranks or graph.is_rail(neighbour):
                    continue
                ranks[neighbour] = ranks[net] + 1
                queue.append(neighbour)
    # Anything the search could not reach goes after everything it could, in
    # first-appearance order, so the result is total and deterministic.
    unreached = [net for net in graph.signal_nets if net not in ranks]
    if unreached:
        base = max(ranks.values(), default=-1) + 1
        for offset, net in enumerate(unreached):
            ranks[net] = base + offset
    return ranks


def _neighbours(graph: CircuitGraph, net: str) -> list[str]:
    """Return the nets reachable from *net* in one component, in source order."""
    found: list[str] = []
    for terminal in graph.terminals.get(net, ()):
        for source, target in flow_edges(graph, terminal.component):
            if source == net and target != net and target not in found:
                found.append(target)
    return found


def column_order(graph: CircuitGraph, ranks: dict[str, int]) -> dict[str, int]:
    """Return a unique column index per signal net.

    Two nets at the same distance from the source would otherwise share a
    column, and a component between them would have zero length.  Ties break on
    first appearance in the netlist.
    """
    order = {name: index for index, name in enumerate(graph.signal_nets)}
    ordered = sorted(
        graph.signal_nets, key=lambda net: (ranks.get(net, 0), order[net], net)
    )
    return {net: index for index, net in enumerate(ordered)}


# --- heuristics --------------------------------------------------------------


def pick_input_source(graph: CircuitGraph) -> str | None:
    """Return the refdes of the circuit's input source, if it has one.

    Preference order: a voltage source with one terminal on ground and a
    non-DC-only value (an actual stimulus), then any voltage source on ground,
    then any voltage source, then the same for current sources.  Ties break on
    netlist order, so the first source a human wrote wins.
    """
    candidates = [
        ref
        for ref, component in graph.components.items()
        if component.kind in SOURCE_KINDS
    ]
    if not candidates:
        return None
    for kind in (Kind.VSOURCE, Kind.ISOURCE):
        grounded_stimulus = [
            ref
            for ref in candidates
            if graph.components[ref].kind is kind
            and _touches_ground(graph, ref)
            and _is_stimulus(graph.components[ref])
        ]
        if grounded_stimulus:
            return grounded_stimulus[0]
        grounded = [
            ref
            for ref in candidates
            if graph.components[ref].kind is kind and _touches_ground(graph, ref)
        ]
        if grounded:
            return grounded[0]
        of_kind = [ref for ref in candidates if graph.components[ref].kind is kind]
        if of_kind:
            return of_kind[0]
    return None


def _touches_ground(graph: CircuitGraph, ref: str) -> bool:
    return any(graph.is_ground(net) for net in graph.component_nets(ref))


def _is_stimulus(component: Component) -> bool:
    """Return ``True`` for a source that drives a signal rather than a rail.

    A source carrying only a DC value is a power supply; one with an AC,
    transient, or pulse specification is the circuit's input.
    """
    params = component.params or {}
    return any(
        key == "ac" or key.split("_")[0] in ("sin", "pulse", "pwl", "exp")
        for key in params
    )


def input_net(graph: CircuitGraph, source: str | None) -> str | None:
    """Return the non-rail net *source* drives, if there is one."""
    if source is None:
        return None
    for net in graph.component_nets(source):
        if not graph.is_rail(net):
            return net
    return None


def pick_output_net(graph: CircuitGraph, ranks: dict[str, int]) -> str | None:
    """Return the net the circuit's output most likely leaves on.

    A net that names itself (``out``, ``vout``, …) wins outright, because a
    human who bothered to name it meant it.  Otherwise the furthest net from
    the source wins, ties broken by netlist order.
    """
    lowered = {net.lower(): net for net in graph.signal_nets}
    for hint in OUTPUT_NET_HINTS:
        if hint in lowered:
            return lowered[hint]
    for name in graph.signal_nets:
        if any(name.lower().startswith(hint) for hint in OUTPUT_NET_HINTS):
            return name
    if not graph.signal_nets:
        return None
    order = {name: index for index, name in enumerate(graph.signal_nets)}
    return max(graph.signal_nets, key=lambda net: (ranks.get(net, 0), -order[net]))


# --- series / parallel structure --------------------------------------------


def parallel_groups(graph: CircuitGraph) -> list[list[str]]:
    """Return groups of two-terminal components sharing the same pair of nets.

    Parallel elements must not be drawn on top of one another, so placement
    fans them out; this is what tells it where to.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for ref in graph.components:
        if not graph.is_path(ref):
            continue
        key = tuple(sorted(graph.component_nets(ref)))
        groups.setdefault(key, []).append(ref)
    return [members for members in groups.values() if len(members) > 1]


def series_chains(graph: CircuitGraph) -> list[list[str]]:
    """Return maximal chains of two-terminal components joined at degree-2 nets.

    An interior net of a chain has exactly two terminals and no rail class and
    no net symbol of its own, which is precisely the "nothing else is connected
    here" condition that lets the two components share a straight line.
    """
    interior = {
        net
        for net in graph.signal_nets
        if graph.degree(net) == 2
        and all(graph.is_path(t.component) for t in graph.terminals[net])
    }
    seen: set[str] = set()
    chains: list[list[str]] = []
    for ref in graph.components:
        if ref in seen or not graph.is_path(ref):
            continue
        chain = _grow_chain(graph, ref, interior)
        seen.update(chain)
        if len(chain) > 1:
            chains.append(chain)
    return chains


def _grow_chain(graph: CircuitGraph, ref: str, interior: set[str]) -> list[str]:
    """Extend a chain from *ref* in both directions through *interior* nets."""
    chain = [ref]
    for direction in (0, 1):
        current = ref
        while True:
            nets = graph.component_nets(current)
            if len(nets) != 2:
                break
            net = nets[direction] if current == ref else _far_net(graph, current, chain)
            if net is None or net not in interior:
                break
            following = [
                terminal.component
                for terminal in graph.terminals[net]
                if terminal.component != current
            ]
            if not following or following[0] in chain:
                break
            if direction == 0:
                chain.insert(0, following[0])
            else:
                chain.append(following[0])
            current = following[0]
    return chain


def _far_net(graph: CircuitGraph, ref: str, chain: list[str]) -> str | None:
    """Return the net of *ref* that does not lead back into *chain*."""
    for net in graph.component_nets(ref):
        others = [
            t.component for t in graph.terminals.get(net, ()) if t.component != ref
        ]
        if others and all(other not in chain for other in others):
            return net
    return None
