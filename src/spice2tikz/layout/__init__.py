"""Netlist IR → Schematic IR: the automatic layout engine (roadmap §5).

A SPICE netlist carries no geometry, so producing a readable schematic from
pure connectivity is the central engineering problem of this project
(``docs/DESIGN.md`` §1).  The engine is deliberately *not* a general graph
layout: generic algorithms produce schematic-hostile results, and for the
small circuits this tool targets, domain conventions beat generality (D15).

The pipeline mirrors the roadmap's own division:

``graph``
    Netlist IR → a connectivity graph, with net classing, series/parallel
    detection, and the input-source / output-net heuristics.
``place``
    components onto an integer grid: sources leftmost, signal flow left to
    right by distance from the source, ground at the bottom, supplies at the
    top.
``route``
    orthogonal wires, junction dots, ground and supply symbols.
``metrics``
    crossings, wire length, bounding-box area and alignment, so that layout
    quality can be tracked rather than argued about.

The result is a plain Schematic IR document, so everything downstream — the
validator, the emitter, and the hand-tweak escape hatch of ``--dump-layout`` —
treats an automatic layout exactly like a hand-written one.
"""

from __future__ import annotations

from .graph import CircuitGraph, Terminal, build_graph
from .layout import layout, layout_scope
from .metrics import Metrics, measure
from .place import Placement, place
from .route import route

__all__ = [
    "CircuitGraph",
    "Metrics",
    "Placement",
    "Terminal",
    "build_graph",
    "layout",
    "layout_scope",
    "measure",
    "place",
    "route",
]
