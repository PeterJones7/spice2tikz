Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

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
