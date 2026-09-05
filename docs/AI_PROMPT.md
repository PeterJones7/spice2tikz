# Writing SPICE for spice2tikz — a prompt for AI tools

spice2tikz converts a netlist to a schematic, and a netlist does not say where
anything goes. The layout engine works that out — one column per signal net,
rails for ground and supplies, signal flow inferred from the input source — so
*how* a deck is written changes how the drawing comes out, even when two decks
describe the same circuit.

Paste the block below into whatever is generating the netlist. §2 explains
what each rule is actually doing, and every claim in it is a rule you can find
in the code.

---

## 1. The prompt

```text
When writing SPICE for spice2tikz, optimise for automatic schematic layout.

Signal flow
- Give the circuit one obvious input: a source with an AC, SIN, PULSE, PWL or
  EXP specification, with one terminal on ground. That source fixes which way
  the drawing flows.
- Name the output net OUT, VOUT, OUTPUT or VO. Any of those wins outright.
- Make the path from input to output a chain, so it can be drawn left to right.
- Use meaningful net names, not N001, N002.

Nets
- Use one ground net, named 0 (GND and GND! also work; nothing else does).
- Give each supply its own net, driven by exactly one pure-DC voltage source
  to ground, with no other voltage source on it. That is what makes it a rail
  and puts it at the top of the sheet — the name VDD or VCC is for the reader,
  not the tool. A DC 0 source is an ammeter idiom, not a rail.
- Do not invent intermediate nets for nodes that are not real nodes.
- Keep series chains genuinely in series, and parallel parts across the same
  two nets, so they are recognised as such.

Components
- Keep transistor stages directional: input on the gate or base, output on the
  drain or collector.
- Give every source the specification it really has. A SIN or PULSE source is
  drawn with a sine or square-wave symbol; DC 5 AC 1 is a biased supply and is
  drawn as one.
- For an op amp, write a subcircuit and mark it:
      .subckt OPA plus minus out vpos vneg ; symbol=opamp
      .ends
      X1 in fb out vcc vee OPA
  The ports map by position: 1 non-inverting, 2 inverting, 3 output, 4 positive
  supply, 5 negative supply. Their names do not matter. Three ports is an ideal
  op amp with no supply pins. Without the metadata it is drawn as a box —
  nothing is inferred from the subcircuit's name.

Metadata (optional; SPICE ignores it, spice2tikz reads it)
- ; labels=ref | value | ref,value | none  — what text a part shows.
- ; symbol=opamp                            — on a .subckt, as above.
- No spaces inside a value: `labels=ref,value`, never `labels=ref, value`.
- Unknown keys are ignored, never an error.

Avoid
- Feedback that is not in the circuit.
- Two nets that are really the same supply.
- Nets that exist only to name a wire.
- Relying on devices being stacked vertically to be readable — the placer does
  not stack them.
```

---

## 2. Why each rule

| Rule | What the tool does with it |
|---|---|
| One grounded AC/SIN/PULSE/PWL/EXP source | `graph.py` picks it as the stimulus and ranks every net by distance from it. That ranking is the left-to-right order. A deck with no such source still converts, but the direction is a guess. |
| `OUT` / `VOUT` / `OUTPUT` / `VO` | `pick_output_net` matches these case-insensitively, exact match first, then prefix. A named output wins outright over the distance ranking, "because a human who bothered to name it meant it". |
| One ground net | Ground is the one class decided by **name**: `0`, `gnd`, `gnd!`, and nothing else. Ground nets become the bottom rails. |
| Supplies are structural, not named | A net is a supply when exactly one voltage source sits between it and ground, that source is pure DC and non-zero, and no other voltage source touches it. `VDD` is a name for the reader; the *shape* is what promotes the net to a top rail. |
| True series and parallel | `series_chains` and `parallel_groups` find these and place them as a run or a fan, instead of giving each net a column of its own. |
| Few, meaningful nets | Every signal net gets its own column, so an extra net is an extra column and a wider drawing. |
| Directional transistor stages | The placer reads which terminal faces the supply to decide whether to turn a device over. A stage wired backwards is drawn backwards, correctly and confusingly. |
| Real source specifications | The symbol is chosen from the waveform: `SIN` → sine, `PULSE` → square, `DC` → the plain circle. |
| `; symbol=opamp` | The only way a subcircuit becomes anything but a labelled box. Ports map by position, so no synonym list can go stale — and `LM741` is an op amp while `LM317` is a regulator, which is why nothing guesses from names. |
| `; labels=` | Sets the label and value on the component at layout time, so a dumped layout already shows the decision. |

## 3. What it will not fix

A prompt cannot make the layout engine cleverer. It has no notion of stacking
two devices in one column, it draws feedback the long way round, and it counts
crossings rather than minimising them — see `docs/LAYOUT.md` §5. When a
generated deck still comes out badly, the answer is the dump-and-edit workflow
in `docs/USAGE.md` §6, not a longer prompt.

Nor should a prompt talk the model into writing a *different circuit*. Every
rule above is about how a circuit is expressed. If following one would change
what the circuit does, the circuit wins.
