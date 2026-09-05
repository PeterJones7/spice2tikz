Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

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
