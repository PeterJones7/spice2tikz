Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

---

# change Add Impedance block, Z

Add support for generic complex impedances using a new Z component.

Rationale:
For AC circuit teaching, students often work directly with impedances (e.g. 10+j5 Ω, -j20 Ω) rather than decomposing them into R, L and C components. Spice2tikz should support these expressions as drawing input even though they are not standard SPICE simulator elements.

Required changes:

- Introduce a new component kind: IMPEDANCE.
- Parse SPICE cards beginning with Z:

    Z1 in out 10+j5
    Z2 out 0 -j20
    Z3 a b 50∠30

- Treat the impedance value as text. Do not attempt to evaluate, simplify, validate, or convert it.
- Store the value verbatim in the Netlist IR.
- Treat Z as a normal two-terminal component for connectivity, graph analysis, placement, routing, series-chain detection and parallel-group detection.
- Emit Z as a generic impedance element (boxed symbol preferred), displaying the supplied impedance text as its value.
- Preserve existing behaviour for R, L and C components.
- update docs and AI prompt, plus log changes in changelog.md etc

Non-goals:

- No simulator compatibility.
- No conversion of complex impedances into equivalent R/L/C networks.
- No frequency-dependent interpretation.
- No automatic symbol selection based on impedance value.
- No special mathematical parsing beyond storing the text.

Acceptance criteria:

- Z cards parse successfully.
- Generated schematics display a two-terminal impedance component with the supplied text.
- Layout and routing behave exactly as for other two-terminal components.
- Existing SPICE decks remain unchanged.
- Add corpus examples and golden files covering:
  - purely real impedance
  - purely imaginary impedance
  - mixed complex impedance
  - multiple impedance elements in series and parallel

# change  LTSPICE .asc export
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
