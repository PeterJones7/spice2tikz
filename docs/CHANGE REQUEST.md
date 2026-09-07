Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

---

# Change request: Remove supply rail glyphs and annotations

Current behaviour

Supply nets may be rendered using a supply rail glyph (arrow symbol). Following the recent change, an explicit source shows its DC value on the source symbol, while a supply rail whose source is drawn is labelled only with the net name. The changelog also describes a special case where a rail may still carry a voltage annotation if the establishing source is not drawn.

Proposed behaviour

Always draw explicit voltage and current sources.
Never draw supply rail glyphs (arrow symbols).
Never display voltage values on nets.
Never display supply-net labels solely because a net is a supply.
Display values only on the source component that establishes them.

Rationale

A source is a component and should always be visible. A net represents connectivity and should not carry component information such as voltage values. Showing values on supply rails duplicates information already shown on the source component and introduces special-case behaviour that is difficult to justify. The rendering rule becomes simple and consistent:

Components show values. Nets show connectivity.

Example

SPICE:

V1 IN 0 DC 5

R1 IN OUT 10k ; labels=value
R2 IN OUT 10k ; labels=value

R3 OUT N1 10k ; labels=value
R4 N1 0 10k ; labels=value

.end


Current behaviour may suppress or replace the source with a supply-rail representation because IN is inferred to be a supply net.

Proposed behaviour:

Draw V1 as a normal 5 V source.
Show 5 V beside V1.
Treat IN as an ordinary net.
Draw no supply glyph.
Draw no IN = 5 V or similar rail annotation.

This makes the origin of the supply explicit and avoids duplicating information.

# change 2 LTSPICE .asc export
Change Request: LTspice .asc Export (Editable Layout Workflow)
Objective

Add a modest, reliable LTspice .asc exporter whose purpose is:

Export an automatically-generated schematic into LTspice so a human can refine component placement and wiring visually, then re-import the edited .asc back into spice2tikz.

This is not intended initially as a complete LTspice simulation exporter.

Success means:

SPICE
 → layout
 → .asc
 → open in LTspice
 → move components
 → save
 → import back into spice2tikz
 → emit CircuiTikZ


while preserving connectivity and geometry.

The project architecture already treats LTspice schematics as an important geometry source and positions LTspice import as an early capability. The Schematic IR exists specifically as the physical representation of a laid-out circuit.

Important Design Principle

The exporter must be:

Geometry-driven

The exporter shall emit LTspice geometry from the placed Schematic IR.

It must not:

reconstruct a schematic from connectivity
attempt a second layout pass
infer positions from the netlist

Instead:

Schematic IR
     ↓
exact pin locations
     ↓
LTspice symbols and wires


This avoids the class of errors seen in preliminary experiments where:

symbols overlap
wires enter symbol bodies
components connect to origins rather than pins
LTspice connectivity appears visually wrong
Milestone 1: LTspice Geometry Library
Goal

Create a shared LTspice symbol geometry database.

New module:

ltspice_symbols.py


Provide:

LTspice symbol name
electrical pins
pin coordinates
supported orientations
transform functions

Example:

SymbolGeometry(
    name="res",
    pins={
        "a": (-16, 0),
        "b": (16, 0),
    }
)

Requirements

Must be shared by:

ASC importer
ASC exporter

There must be one source of truth.

Tests

For every supported symbol:

local pin coords
 → transform
 → inverse transform


must exactly reproduce the original.

Milestone 2: Minimal Exporter
Goal

Export only:

resistor
capacitor
inductor
independent voltage source
wires
ground
net labels

Nothing else.

Supported Output
VERSION
SHEET
SYMBOL
WIRE
FLAG
SYMATTR


only.

Validation

The resulting file must:

open cleanly in LTspice
show a connected circuit
allow interactive movement of components
Milestone 3: Pin-Accurate Round Trip
Goal

Prove geometry correctness.

Test:

Schematic IR
 → ASC
 → ASC importer
 → Schematic IR


Verify:

same components
same nets
same pin locations

Allow small coordinate scaling differences only if necessary.

Key Requirement

A symbol origin is derived from pin locations.

Never the reverse.

Milestone 4: Passive-Circuit Corpus

Add corpus examples:

RC low-pass
voltage divider
RLC chain
bridge

The exported ASC must:

appear visually correct
import correctly
survive round trip

Note:

A "visually correct" voltage divider should resemble a normal schematic:

      V1
       |
       |
      R1
       |
------out------
       |
      R2
       |
      GND


rather than disconnected symbols scattered around the page.

Milestone 5: Semiconductors

Add support for:

diode
NMOS
PMOS
NPN
PNP
Requirements

Exporter and importer must agree on:

orientations
pin mapping
mirror conventions

The PMOS and PNP orientation issues previously encountered demonstrate that this area needs explicit tests.

Round-trip tests

Required for every supported device.

Milestone 6: Operational Amplifiers
Scope

Only support op amps already represented internally as:

symbol=opamp


Do not attempt generic subcircuit export.

Mapping

Export as LTspice:

UniversalOpamp2


or equivalent standard symbol, if available in the importer geometry library.

Map:

+
-
out
up
down


to LTspice pins.

Requirement

Op amp placement must be pin-accurate.

The goal is editable geometry, not simulation fidelity.

If an exact mapping cannot be established, emit a warning and omit export.

Milestone 7: CLI Integration

Support:

spice2tikz circuit.sp -o circuit.asc


following the existing output-extension model.

Explicitly Out of Scope

Not for this change:

generated .asy files
arbitrary custom symbols
generic subcircuits
model export
simulation directives
.tran
.ac
.dc
waveform annotations
simulator-ready guarantees

Unsupported items should produce clear warnings.

Acceptance Criteria (for human to try)

The feature is complete when the following workflow works reliably:

RC low-pass SPICE deck
    ↓
layout
    ↓
ASC export
    ↓
open in LTspice
    ↓
move R1 slightly
    ↓
save ASC
    ↓
import ASC
    ↓
emit CircuiTikZ


and the final schematic preserves:

component identities
connectivity
edited geometry

without requiring manual JSON editing.

That delivers the practical benefit sought here: LTspice becomes a graphical editor for refining spice2tikz layouts, while Schematic IR remains the authoritative internal representation.
