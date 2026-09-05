<!-- FILE: docs/LAYOUT.md -->

# spice2tikz — the layout engine

A SPICE netlist has no geometry. Turning pure connectivity into a schematic a
person would accept is the central engineering problem of this project
(`docs/DESIGN.md` §1), and this document says how version 1 does it, what it
is bad at, and what to do when it gets a circuit wrong.

The short version: **the engine only has to get close.** Dumping the generated
Schematic IR, editing coordinates by hand, and re-emitting is a first-class
workflow, not a workaround (`docs/DESIGN.md` §2), so a layout that is 90%
right is 100% useful.

---

## 1. What it is not

It is not a graph layout algorithm. Force-directed and hierarchical graph
drawers optimise for things schematics do not care about and ignore the things
they do: ground at the bottom, supplies at the top, signal flowing left to
right, orthogonal wires, and symbols oriented by convention. Design decision
D15 rules them out; for the circuits this tool targets — under thirty
components — domain heuristics beat generality.

It is also not a router in the PCB sense. There is no channel model, no ripping
up and rerouting, and no global optimisation. Wires are placed by construction
and checked for correctness, not searched for.

---

## 2. The model

```
   supply rail  ────────●────────────────●──────────  y = top
                        │                │
                       RB1              RC1
                        │                │
   row 1     ───────────┼────────────────┼──────
                        │                │
   row 0  ──[ CIN ]─────●────────────|<──┴─────────   the signal row
                        │           Q1
                       RB2           │
                        │           RE1
   ground rail ─────────●────────────●───────────────  y = 0
              col(in)  col(b1)     col(c1)
```

Four kinds of place:

**Columns.** Every *signal* net owns one vertical line. Columns are ordered by
graph distance from the input source, so a signal that passes through three
components lands three columns to the right of where it started. Each net gets
its **own** column, never a shared one: two nets on one column would make the
component between them zero-length, which invariant 7 rejects and circuitikz
cannot draw.

**Rails.** Every *ground* net owns a horizontal line at the bottom of the
sheet, every *supply* net one across the top. Rails carry no column: they touch
everything, and treating them as ordinary nets would collapse the whole circuit
into one column (every node in a circuit is two hops from every other one
through ground).

**Rows.** Horizontal runs — components between two signal nets, and the drive
wires leaving a device's control terminal — are assigned rows, lowest first, so
that no two overlap and none crosses a column where something is attached at
that height.

**Bodies.** Multi-terminal components are placed beside the column of their
output net (drain, collector), turned so the terminal that wants the supply
faces up and the one that wants ground faces down.

### The two invariants that keep it honest

A schematic that shows a connection the circuit does not have is worse than no
schematic. Two rules make that structurally impossible rather than merely
unlikely:

1. **Columns are even; device terminals are odd.** A transistor has its drain,
   source and bulk on a single vertical line, so a net's wire running down that
   line would short all three together. Device bodies are offset from their
   column by an odd number of units (`place.NODE_INSET = 3`), which puts every
   terminal on an odd x while every column stays even — so no column can pass
   through any terminal — and clears the body's own half-width, so no column
   passes through a body either.
2. **Rows are allocated, not assumed.** A horizontal run is given the lowest
   row on which it neither overlaps another run nor crosses a column at a point
   where a terminal sits.

Everything the two rules do not cover is caught by the router's obstacle check
(§4) and, failing that, by `tests/test_layout.py`, which asserts on every
corpus circuit that no wire runs through another net's terminal and that no two
nets share a terminal position. Those tests exist because `validate.py`
*cannot* catch this: the IR validator sees geometry, not nets, so a sheet that
shorts two nodes satisfies all thirteen invariants.

---

## 3. Stage by stage

### `layout/graph.py` — connectivity and direction

Builds an ordered view of one scope: which terminals sit on which net, which
nets are rails, and how far each net is from the input.

- **Net classing** comes from the Netlist IR (`Net.class`), which the SPICE
  parser fills: node `0`/`gnd` is ground, and a net fed by a single DC source
  against ground is a supply.
- **Direction.** A transistor is a directed edge from its control net to its
  channel nets, so its output ranks one column right of its input. Everything
  else conducts both ways.
- **Input source**: the first voltage source that touches ground *and* carries
  an AC, SIN, PULSE, PWL or EXP specification — a stimulus rather than a rail.
  Falling back, any grounded source, then any source at all.
- **Output net**: one that names itself (`out`, `vout`, `output`, `vo`),
  otherwise the furthest net from the input.
- **Series chains** (maximal runs joined at degree-two nets) and **parallel
  groups** (components sharing a net pair) are detected and used to decide how
  much room a column needs.

### `layout/place.py` — where things go

- A two-terminal component between two signal nets is **horizontal**, between
  their columns, on an allocated row.
- One that reaches a rail is **vertical**, between its column and that rail.
  Parallel branches fan out sideways by `BRANCH_PITCH`, counted separately for
  the ground side and the supply side because those occupy disjoint heights.
- One that spans two rails — a supply source, a decoupling capacitor — gets a
  reserved column to the left of everything else, which is where a source
  belongs.
- Multi-terminal components become node components on a built-in circuitikz
  shape where one exists (`nmos`, `pmos`, `npn`, `pnp`, `njfet`, `pjfet`), and
  a **generated box** otherwise, written into the document's own `symbols`
  block with its ports distributed left and right, so the file renders forever
  without tool-internal lookups (SPEC_IR §2).
- The MOS bulk and bipolar substrate terminals are declared — invariant 8
  requires every symbol pin — but never wired. They sit on the node origin, so
  any wire from one would leave through the middle of the device. circuitikz
  draws no bulk terminal unless asked, so nothing dangles; a body terminal that
  is *not* tied to the channel produces a warning saying its connection is not
  drawn.
- Value labels are emitted only for values that parsed to a number. A source
  specification like `DC 0 AC 1 SIN(0 10m 1k)` is several times wider than the
  symbol it labels; the refdes still names the part.

### `layout/route.py` — wires, dots, symbols

Each net is a **spine plus stubs**. The spine is the net's column (or its
rail); each terminal joins it by the shortest *clear* route:

1. nothing at all, if the terminal is already on the spine;
2. a single perpendicular segment — the degenerate L, since the spine absorbs
   the other leg;
3. a Z that steps along the spine's axis first and crosses further out, trying
   offsets outwards until one is clear.

"Clear" means the route passes through no other net's terminal, through no
device body, and not collinear with an already-drawn wire of a different net.

The spine spans exactly the range of its own connection points, so both ends
land on something: **dangling wire ends (invariant 9) are impossible by
construction.** Junction dots are then computed with the same counting rule
`validate.py` uses — a wire end counts one, a wire passing through counts two,
each terminal or conductive net symbol counts one — so **invariant 10 holds by
construction too**.

Ground nets get one `ground` symbol at the middle of their rail; supply nets a
`vcc` symbol labelled with the net name and, where the parser worked it out,
its voltage. The input and output nets get `tap` labels.

### `layout/metrics.py` — measuring the result

| metric | meaning | better |
|---|---|---|
| `crossings` | wire segments of different nets crossing | lower |
| `wire_length` | total orthogonal length, grid units | lower |
| `bbox_area` | bounding box area, grid units | lower |
| `alignment` | share of terminals sharing a row or column with another | higher |

`spice2tikz -v` prints them for the circuit being converted.
`tests/golden/metrics.json` records them per corpus circuit, and
`tests/test_layout.py` asserts none of them gets worse — a **ratchet**, not a
score. Improving a layout is expected to require `pytest --update-golden`;
that is the point, since the new number becomes the floor.

---

## 4. How good is it?

`python tools/cross_validate.py` compares the engine against the human layouts
in the `.asc` corpus, on the circuits that exist in both. As of this release:

| circuit | wire length (human → auto) | bbox area (human → auto) | crossings |
|---|---|---|---|
| `rc_lowpass` | 6 → 8 | 25 → 48 | 0 → 0 |
| `rlc_series` | 29 → 16 | 140 → 96 | 0 → 0 |
| `cmos_inverter` | 40 → 110 | 198 → 432 | 0 → 3 |

Read honestly: on simple series circuits the engine is level with a person and
occasionally tighter, because it wastes no space. On a circuit with stacked
devices it uses two to three times the wire and area and introduces crossings,
because it has no notion of putting two transistors in the *same* column with
one above the other. That is the largest single gap, and it is what layout v2
(roadmap §7.2) would attack first.

---

## 5. Known limitations

- **One net, one column.** Real schematics reuse horizontal space; this one
  never does, so wide circuits come out wide. A twelve-net circuit is twelve
  columns across whatever its shape.
- **No stacked totem poles.** A CMOS inverter is drawn with its two devices
  beside the same column rather than in the conventional PMOS-above-NMOS
  arrangement. The result is correct and legible but not idiomatic.
- **Boxes for anything without a circuitikz shape.** Controlled sources with
  four terminals, switches, transmission lines and subcircuits are rectangles
  with labelled pins. Correct, dull, and the right answer until someone maps
  them to real symbols. A subcircuit can ask for one today with
  `; symbol=opamp` on its `.subckt` card; the rest still draw as boxes.
- **Feedback is drawn the long way round.** A net whose column is to the left
  of the device driving it gets a Z route out and back; nothing tries to
  shorten it.
- **No multi-sheet output, no hierarchy expansion.** A subcircuit *instance* is
  one box; its contents are not drawn. `layout_scope` will lay out a
  `SubcktDef` if asked, but nothing composes the sheets.
- **Crossings are counted, not minimised.** The router avoids drawing through
  terminals; it does not try to reduce the number of times wires cross.

---

## 6. When it gets your circuit wrong

Dump it, fix it, re-emit it:

```bash
spice2tikz amplifier.sp --dump-layout amplifier.schematic.json
```

Edit the JSON — the coordinates are integers on a grid with y up, and the
format is documented in `docs/SPEC_IR.md` §2 — and convert the edited file:

```bash
spice2tikz amplifier.schematic.json > amplifier.tex
```

The validator runs on the edited file, so a mistake is reported rather than
drawn: a component whose declared pins no longer match its symbol, a wire that
now dangles, a junction dot where fewer than three conductors meet. Emission is
suppressed on any error.

Nudging one component is usually enough. Moving a `PathComponent` means editing
its `a` and `b`; moving a `NodeComponent` means editing `at` **and** its
`pins`, which must stay consistent with the symbol geometry (invariant 8) —
easiest done by recomputing them with `symbols.resolve_pins`.

If the engine gets a whole *class* of circuit wrong, that is a bug worth
reporting, with the netlist attached: see `docs/CONTRIBUTING.md`.
