Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

# 1 Show source values on voltage/current source symbols rather than creating separate rail labels

Current behaviour

For supply rails the generated schematic may emit a separate net label such as: (eg from voltage divider example)

    \draw (10,10) node[vcc]{} node[right]{in = 5};

while the actual source is drawn elsewhere as:

    \draw (...) to[american voltage source, l=$V_1$] (...);

This duplicates information and associates the voltage value with the net rather than with the component that establishes that voltage.

Requested behaviour

For independent voltage and current sources, emit the source value as the component value label on the source symbol itself, using the same mechanism already used for resistors, capacitors and inductors.

Example:

Current:
    V1 labelled "V1"
    separate rail label "in = 5"

Preferred:
    V1 labelled "V1"
    V1 value label "5 V"
    no automatically generated "in = 5" rail annotation

Rationale

- Follows normal schematic convention: component values belong on components.
- Avoids duplicating information already present in the netlist.
- Keeps net labels focused on connectivity rather than operating-point information.
- Produces more consistent output because passive components already display their values on the component body.
- Generalises naturally to all independent sources:
    - DC voltage sources
    - DC current sources
    - supply generators represented by sources in the netlist

Implementation note

The layout engine should continue to create supply and ground symbols as required, but the emitter should prefer source value labels on the source symbol and suppress automatically generated "<net> = <value>" rail annotations when that value originates from an identifiable source component.


# 2 Fix PMOS layout error
I spotted a layout error in the PMOS transistor of  cmos_inverter.png.  Extra nodes appear and a wire crossing the terminals.  After investigation the following was reported by GPT as requiring fixing.  Read the report to get you started, then fix the issue.

/begin GPT report
You were right that the defect is introduced during import, but the rotate=180 + mirror=true combination is not itself the primary fault. The precise cause is a mismatch between LTspice PMOS orientation semantics and the built-in CircuiTikZ PMOS symbol, followed by the junction detector counting the resulting overlapping leads.

What actually happens

The source cmos_inverter.asc places the NMOS as R0 and the PMOS as M180. Its original four wires form the conventional inverter geometry.

1. The importer translates M180 mechanically

asc_importer.py converts LTspice M180 to:

"M180": (180, True)


The conversion is generic and does not account for differences between the native orientation of LTspice's pmos4 symbol and CircuiTikZ's pmos symbol.

For M2, the importer therefore creates:

at     = (11,15)
rot    = 180
mirror = true

2. CircuiTikZ's PMOS is already reversed relative to its NMOS

The repository now documents that CircuiTikZ's unrotated PMOS has:

S at the top
D at the bottom


whereas an unrotated NMOS has:

D at the top
S at the bottom


This is explicitly encoded in symbols.py:

"nmos": _mos("nmos", drain_up=True),
"pmos": _mos("pmos", drain_up=False),


and documented in CIRCUITIKZ_NOTES.md.

Consequently, the LTspice PMOS's M180 transformation should not simply be copied onto the CircuiTikZ PMOS. CircuiTikZ's PMOS has already incorporated the source/drain reversal needed for the conventional PMOS-above-NMOS drawing.

The exact geometry error

After applying rot=180, mirror=true to the built-in PMOS definition, the idealised IR pins become:

S = (11,13)
G = (9,15)
D = (11,17)
B = (11,15)


But the actual LTspice connection points derived from the .asc symbol are approximately:

D = (11,12)
G = (8,17)
S = (11,18)
B = (11,15)


The importer deliberately bridges its idealised symbol pins to the original LTspice pin positions. asc_importer.py says that _pin_leads() keeps both positions and draws stubs between them.

Therefore it generates:

\draw (11,17) -- (11,12);   % IR D to LTspice D
\draw (11,13) -- (11,18);   % IR S to LTspice S


Those two leads overlap between (11,13) and (11,17). This is the decisive error. They visually short the PMOS source and drain and run directly through the device origin. The problematic lines are present in cmos_inverter.tex.

The gate lead:

\draw (9,15) -- (9,17) -- (8,17);


is not primarily caused by TikZ transform ordering. It is the bridge between the incorrectly transformed idealised gate position and LTspice's real gate position.

Why the three dots appear

The junction routine in asc_importer.py:

includes every NodeComponent.pins position, including the bulk pin;
counts a wire passing through a point as two conductors;
deliberately ignores net identity;
adds a dot when the count is at least three.

Because the source and drain bridge wires overlap:

(11,13) is an IR source pin plus overlapping wire conductors
(11,17) is an IR drain pin plus overlapping wire conductors
(11,15) is the nominal bulk pin plus both wires passing through it

So all three positions satisfy the junction threshold.

The middle dot is particularly revealing. circuitikz.py explicitly skips the MOS bulk lead when its offset is (0,0), while CIRCUITIKZ_NOTES.md says the bulk terminal is not drawn unless the CircuiTikZ bulk option is requested. Nevertheless, _junction_points() counts that invisible, unconnected bulk position as a component terminal.

Conclusion

There are two bugs, but the first one causes nearly all the visible damage.

Primary bug: PMOS orientation adaptation

The generic LTspice-to-IR orientation conversion is being applied to a CircuiTikZ PMOS whose native source/drain orientation differs from the LTspice symbol being replaced.

For this particular LTspice pmos4 ... M180, the CircuiTikZ node should effectively be emitted in its conventional unrotated orientation, with source above and drain below. Applying rot=180, mirror=true swaps the idealised source and drain positions relative to the preserved LTspice connection points, causing the two bridge leads to cross through and overlap over the device.

This belongs in asc_importer.py, probably as a symbol-specific orientation adaptation rather than a change to the generic ORIENTATION_TO_IR table. The generic table's coordinate mathematics is internally consistent.

Secondary bug: invisible bulk counted as a conductor

The junction calculation should not count an unconnected MOS bulk pin that the emitter does not actually draw. That is what creates the otherwise inexplicable dot at the transistor origin.

Documentation discrepancy

DESIGN.md says the .asc route uses existing geometry and requires no layout engine. That is broadly true for the original wires.

However, the current implementation does not preserve symbol connection geometry exactly. asc_importer.py replaces LTspice transistor geometry with an idealised 4x4 built-in symbol and inserts additional bridge leads between the idealised pins and original LTspice positions.

So the most accurate issue statement is:

LTspice pmos4 M180 is mapped mechanically to IR rot=180, mirror=true, although CircuiTikZ's native PMOS already has source above and drain below. This reverses the idealised PMOS source and drain relative to the preserved LTspice pin positions. The importer then draws overlapping source and drain bridge leads through the device. Junction inference compounds the problem by placing dots at both channel pins and at the invisible, unconnected bulk pin.

The fix should be accompanied by a regression test using this exact cmos_inverter.asc, asserting that no lead paths overlap, no wire passes through the PMOS body, and no junction is emitted at the unconnected bulk origin.
/end GPT report

# 3 Change request: rendered PDF, PNG and SVG output

Add optional rendered output to the existing `spice2tikz` command.


## Required behaviour

Infer the output format from the case-insensitive extension supplied with `-o`:

```text
spice2tikz input.asc -o output.tex
spice2tikz input.asc -o output.pdf
spice2tikz input.asc -o output.png
spice2tikz input.asc -o output.svg
```


## Implementation guidance

The goal is to add rendered output support while preserving the existing project architecture and design philosophy.

Prefer small, incremental changes that reuse existing code and patterns already present in the repository.

In particular:

- Reuse or refactor logic from `tools/render_goldens.py` where practical.
- Keep rendering concerns separate from CircuiTikZ emission.
- Keep `cli.py` thin and focused on argument handling and orchestration.
- Preserve deterministic `.tex` generation exactly as today.
- Preserve the existing exit-code contract and diagnostic style.
- Preserve the zero-runtime-dependency design rule.

## Things to avoid

Avoid introducing:

- mandatory third-party Python packages
- a second executable such as `spice2png`
- a separate `--format` option unless genuinely required
- rendering code inside `emit/circuitikz.py`
- platform-specific shell commands
- duplicated dependency-detection logic
- duplicated rendering pipelines
- special-case code paths that bypass the normal CircuiTikZ emitter

Do not change:

- the existing Schematic IR
- Netlist IR
- emitter contracts
- golden-file testing approach
- standalone `.tex` output behaviour

The feature should feel like a natural extension of the existing CLI rather than a parallel subsystem.

## Practical expectations

The implementation should be robust rather than ambitious.

A good implementation:

- produces correct output files
- reports missing tools clearly
- cleans up temporary files
- preserves useful diagnostics
- works on Windows, Linux and macOS
- passes the existing test suite

The implementation does not need to:

- automatically install external tools
- support every PDF conversion utility ever created
- provide advanced image-processing features
- guarantee byte-identical PDF or image output
- implement a new graphics backend

Prefer simplicity and maintainability over feature breadth.

## Roadmap update

Rendered SVG generated via:

Schematic IR → CircuiTikZ → PDF → SVG

is sufficient to satisfy SVG export requirements.

Remove the planned native SVG emitter from `docs/ROADMAP.md` rather than keeping both concepts in parallel.

The project should continue to have a single canonical emitter:

Schematic IR → CircuiTikZ

with PDF, PNG and SVG treated as rendered derivatives of that output.

# 4 Change Request: Metadata System, Op-Amp Symbol Support and Label Control

Background

The current parser removes inline ';' comments during stage 1 line assembly. This is appropriate for ordinary SPICE comments, but prevents use of schematic-oriented metadata.

Introduce a general metadata mechanism that is preserved through the parsing pipeline and can be used by future schematic-generation features.

This change has two initial use cases:

1. Explicit symbol selection for subcircuits.
2. Explicit control over displayed labels and values.

The metadata system should be designed as a general extensibility mechanism rather than a one-off op-amp feature.

========================================================================
## PART 1 - GENERAL METADATA SUPPORT
========================================================================

Support key=value metadata stored in end-of-line ';' comments.

Examples:

    R1 in out 10k ; labels=ref,value
    C1 out 0 100n ; labels=value

    .SUBCKT LM741 PLUS MINUS OUT VCC VEE ; symbol=opamp

Parser requirements:

- Do not immediately discard inline ';' comments.
- Parse key=value metadata from those comments.
- Allow multiple metadata items.
- Unknown metadata must not generate errors.
- Metadata should be preserved through the pipeline.

Conceptually:

    R1 in out 10k ; labels=ref,value

becomes:

    metadata = {
        "labels": "ref,value"
    }

The metadata system should be generic enough to support future schematic features without parser redesign.

========================================================================
## PART 2 - OP-AMP SYMBOL SUPPORT
========================================================================

Support explicit symbol selection:

    .SUBCKT LM741 PLUS MINUS OUT VCC VEE ; symbol=opamp

No heuristic recognition is permitted.

Do NOT infer op amps from names such as:

    LM741
    TL081
    LM358
    OPAMP

The metadata is the sole trigger.

----------------------------------------------------------------------
IR Changes
----------------------------------------------------------------------

Extend SubcktDef with:

    symbol: str | None

and preserve this through the IR pipeline.

----------------------------------------------------------------------
Symbol implementation
----------------------------------------------------------------------

Add built-in symbol:

    opamp

to symbols.py.

CircuiTikZ base shape:

    node[op amp]

Use the documented anchors:

    +
    -
    out
    up
    down

----------------------------------------------------------------------
Port mapping
----------------------------------------------------------------------

Use a fixed positional mapping.

When symbol=opamp is present:

    Port 1 -> +
    Port 2 -> -
    Port 3 -> out
    Port 4 -> up
    Port 5 -> down

Example:

    .SUBCKT LM741 PLUS MINUS OUT VCC VEE ; symbol=opamp

means:

    PLUS  -> +
    MINUS -> -
    OUT   -> out
    VCC   -> up
    VEE   -> down

The textual port names are ignored for rendering purposes.

They remain useful documentation for the human author only.

Rationale:

- Simple
- Deterministic
- No synonym lists
- No heuristics
- Easy to test

----------------------------------------------------------------------
Rendering behaviour
----------------------------------------------------------------------

When symbol=opamp is present:

- Render as a native CircuiTikZ op amp.
- Do not render a generic subcircuit box.
- Do not render port labels.
- Do not render pin names.

Specifically, do NOT emit:

    PLUS
    MINUS
    VCC
    VEE
    OUT

around the device.

The graphical op-amp symbol already contains the + and - markings.

Connections should be made directly to the corresponding op-amp anchors.

If symbol metadata is absent:

- Preserve existing generic subcircuit rendering.
- Preserve existing port label behaviour.

Future symbols such as:

    symbol=comparator
    symbol=instamp
    symbol=adc
    symbol=dac

should be able to reuse the same mechanism.

========================================================================
## PART 3 - LABEL AND VALUE DISPLAY CONTROL
========================================================================

Add metadata-driven control over displayed component labels.

Initial syntax:

    labels=ref
    labels=value
    labels=ref,value
    labels=none

Meaning:

----------------------------------------------------------------------
labels=ref
----------------------------------------------------------------------

Show only reference designator.

Examples:

    R1
    C2
    Q3

----------------------------------------------------------------------
labels=value
----------------------------------------------------------------------

Show only component value.

Examples:

    10k
    100n
    BC547

----------------------------------------------------------------------
labels=ref,value
----------------------------------------------------------------------

Show both reference and value.

Examples:

    R1
    10k

or whatever presentation format the emitter currently uses.

----------------------------------------------------------------------
labels=none
----------------------------------------------------------------------

Suppress both reference and value labels.

----------------------------------------------------------------------
Examples
----------------------------------------------------------------------

    R1 in out 10k ; labels=ref

        shows:
            R1

    R1 in out 10k ; labels=value

        shows:
            10k

    R1 in out 10k ; labels=ref,value

        shows:
            R1
            10k

    R1 in out 10k ; labels=none

        shows:
            nothing

----------------------------------------------------------------------
Default behaviour
----------------------------------------------------------------------

If labels metadata is absent:

- Preserve current emitter behaviour.
- Existing schematics should render unchanged.

Metadata only overrides the default when explicitly present.

========================================================================
## PART 4 - TESTS
========================================================================

Add parser tests for:

    symbol=opamp

    labels=ref
    labels=value
    labels=ref,value
    labels=none

Add IR round-trip tests.

Add emitter tests.

Add golden tests verifying:

1. Generic subcircuit remains a box.

2. symbol=opamp emits:

       node[op amp]

3. Op-amp pin names are not displayed.

4. labels=ref shows reference only.

5. labels=value shows value only.

6. labels=ref,value shows both.

7. labels=none shows neither.

========================================================================
## DESIGN PRINCIPLE
========================================================================

SPICE semantics remain unchanged.

The simulator ignores all metadata.

The metadata exists solely to control schematic generation and visual presentation.

This metadata mechanism should become the standard extension point for future schematic-oriented features.

# Three related spine bugs

## Routing bug: net spines bypass obstacle checking

The router models each net as a "spine plus stubs".

Stub routing is obstacle-aware:
- _route_to_spine() calls _clear()
- _clear() uses obstacles.blocked()
- blocked() prevents wires passing through:
  - foreign terminals
  - node-component bodies
  - path components
  - overlapping wires

However, the spine itself is emitted directly in _wire_net():

    wires.append(Wire(net=net, points=ends))

The spine segment is never passed through _clear() or obstacles.blocked().

Result:
- A long signal/supply/ground spine can be drawn straight through components.
- Rail spines are especially vulnerable because they can span most of the drawing.
- The image showing the N1 rail passing through R3 is consistent with this behaviour.

Requested fix:

Before emitting any spine segment:
- treat it like any other route
- check for collision with all obstacles
- if blocked, reroute or split the spine
- never allow a spine to pass through a component body simply because it is a spine


## Visual obstacle bug: path components only block collinear wires

Current obstacle handling treats a resistor/capacitor/etc. as a PathComponent represented by a single line segment between terminals.

The function _runs_along() intentionally blocks:
- wires that overlap the component segment
- wires that terminate inside the component segment

But it explicitly allows perpendicular crossings:

    "Crossing the component at right angles is left alone"

This assumes the component occupies only the ideal centreline segment.

The problem is that the actual rendered circuitikz component has visual area:
- resistor body
- capacitor plates
- source symbol
- diode body
- etc.

The router has no obstacle representing this visual body.

Result:
- A horizontal wire can cross a vertical resistor.
- Geometrically this is allowed.
- Visually the wire passes through the resistor symbol itself.
- The generated schematic looks wrong even though the connectivity remains correct.

Requested fix:

Add graphical-body obstacles for PathComponents.

For every PathComponent:
- compute an approximate body bounding box
- register that box as a routing obstacle
- continue allowing crossings over leads if desired
- prohibit crossings through the component body region

In effect:

Current model:
    resistor = one ideal line segment

Desired model:
    resistor = leads + body obstacle

The router should be allowed to cross leads but never the body.

## Component-on-spine bug: 

A two-terminal PathComponent may be placed directly on its own net spine.

When this occurs, the spine continues through the centre of the component body,
causing the rendered wire to appear to pass through the resistor/capacitor/source
symbol itself.

This differs from a wire crossing a component:
- the offending wire belongs to the same net as one terminal of the component
- the wire is the net spine/column itself
- the component body is rendered on top of that spine


A PathComponent body should never occupy the same geometric path as a net spine.
The body region should be treated as an obstacle even for wires belonging to the
same net, except at the component terminals themselves.

Regression test:

Assert that no wire segment intersects the body region of any PathComponent,
other than at the component's declared terminal endpoints.

## code to re-create bugs
Example spice code for bugs 1,3:
* Spine-crosses-resistor test

V1 N1 0 DC 10
V2 N3 0 DC 5

R1 N1 N2 100
R2 N2 N3 220

R3 N1 N4 330
R4 N3 N4 470

.op
.end

Example code to recreate crossing bug:
* Crossing-through-resistor-body test

V1 N1 0 DC 10
V2 N3 0 DC 5

R1 N1 N2 1k
R2 N2 N3 1k

R3 N1 N4 1k
R4 N4 N3 1k

R5 N2 N4 1k

.op
.end

# Source type

When a voltage (or current) source is time varying (e.g.  V1 in 0 AC 1 SIN(0 10m 1k)) then an AC source symbol should be used rather than the DC sort.  e.g. \draw (0,0) to[sV] (0,3); 
(this should also be reflected in JSON and IR format)

DC      -> V
SINE    -> sV
PULSE   -> sqV


V % DC voltage source sV % sine-wave voltage source sqV % square-wave voltage source  cV % controlled voltage source (diamond)
to[I] % DC current source to[sI] % sinusoidal current source to[sqI] % square-wave current source

# AI prompt
Add to the documentation a concise prompt for any AI tools writing SPICE for spice2tikz.  Consider how to write spice code to allow for optimum layout, plus also instructions on using key components (e.g. opeamp); metadata usage (e.g. layout)
Here's a first draft (but needs opamp and metadata added)

```
When generating SPICE for spice2tikz, optimise for automatic schematic layout:

- Use one obvious input source (prefer a grounded AC/SIN/PULSE/PWL source).
- Use standard output net names (OUT, VOUT, OUTPUT, VO).
- Create a clear left-to-right signal path from input to output.
- Use a single ground net (0).
- Use dedicated supply nets (VDD, VCC, VEE, VSS, etc.).
- Keep series chains as true series connections.
- Connect parallel components to the same pair of nets.
- Minimise unnecessary intermediate signal nets.
- Use meaningful net names rather than N001, N002, etc.
- Keep transistor stages directional: input at gate/base, output at drain/collector.

Avoid:
- Unnecessary feedback loops.
- Multiple equivalent supply nets.
- Creating extra nets that do not represent meaningful nodes.
- Topologies that rely on vertically stacked devices for readability.

Reason: spice2tikz assigns one column per signal net, detects series chains and parallel groups, infers signal direction from the input source, and places supplies and ground on dedicated rails. Fewer nets and clearer signal flow generally produce more compact and readable layouts.
```
