<!-- FILE: docs/SPEC_IR.md -->

# spice2tikz — Intermediate Representation Specification v1.0

Both IRs are UTF-8 JSON, snake_case keys. Optional fields are omitted,
never null. Files self-identify: `"ir": "netlist" | "schematic"`,
`"version": "1.0"`. Version policy: minor = additive optional fields
only; unknown major → reject; unknown fields → warn, ignore.
No timestamps anywhere (determinism).

Types below use TypeScript-like notation. Implement as frozen-ish
dataclasses with `to_json()` / `from_json()`.

### Canonical serialization

*Amended 2026-07-28; previously `json.dumps(..., indent=2,
sort_keys=False)`, which put every coordinate on its own line and made
hand-editing the Schematic IR unpleasant. Field order and determinism are
unchanged.*

Documents are written by one canonical printer
(`_serde.dumps`), so the same content always yields byte-identical text:

- Two-space indentation; UTF-8, no `\uXXXX` escaping of printable
  characters; exactly one trailing newline; no trailing whitespace.
- Deliberate field order matching this spec — **never** sorted keys.
- Objects always put one field per line, so diffs stay line-oriented.
- An array is written on **one line** when its elements are all scalars,
  or all arrays of scalars, and the line fits within 88 columns
  (counting indentation and the key). Otherwise one element per line.
  Coordinate pairs (`"a": [0, 4]`) and short point lists
  (`"points": [[0, 0], [6, 0]]`) therefore stay inline.

The listings in §5 are illustrative and wrap more aggressively than the
printer does; `tests/corpus/rc_lowpass.*.json` are the normative examples
of the canonical format.

## 1. Netlist IR

```ts
NetlistIR {
  ir: "netlist"; version: "1.0";
  meta: { title?: string; source?: string; generator?: string;
          dialect?: string }
  circuit: Scope
  subcircuits: { [name: string]: SubcktDef }   // keys lowercase
  models?: { [name: string]: { type: string; params: ParamMap;
                               raw: string } }
}

Scope     { components: Component[]; nets: { [net_id: string]: Net } }
SubcktDef extends Scope { ports: string[]; params?: ParamMap;
                          meta?: MetaMap }   // meta.symbol picks a shape

Net { name: string;                      // net_id == name
      class: "signal" | "ground" | "supply";
      supply_voltage?: Quantity }

Component {
  id: string                             // refdes "R1"; unique in scope
  kind: Kind
  pins: { [pin_name: string]: string }   // pin -> net_id
  value?: Quantity
  model?: string                         // key into models
  subckt?: string                        // for kind "subcircuit"
  control?: string                       // for ccvs/cccs: controlling V id
  params?: ParamMap
  meta?: MetaMap                         // drawing metadata; see below
  raw: string                            // original SPICE card verbatim
}

Quantity { raw: string; value?: number; unit?: string }  // unit: SI canonical
ParamMap { [key: string]: Quantity }
MetaMap  { [key: string]: string }
```

### Metadata

`meta` carries what an inline `;` comment said in `key=value` form:

```spice
R1 in out 10k                          ; labels=ref,value
.subckt LM741 PLUS MINUS OUT VCC VEE   ; symbol=opamp
```

SPICE ignores comments, so metadata can never change what the circuit *is* —
it says things about the drawing that the netlist has no way to express.
Values are strings; each key is interpreted by whoever consumes it.

**An unknown key is not an error, at any stage.** A document must load, and a
deck must convert, on a version of the tool that has never heard of the key.
Two keys are defined today:

| key | on | meaning |
|---|---|---|
| `symbol` | `.subckt`, or an `X` instance | draw instances as this symbol; `opamp` is the only value implemented |
| `labels` | any component | which of `ref` and `value` to draw; `none` for neither |

An `X` instance inherits `symbol` from its definition and may override it: the
definition says what its instances are by default, the instance has the last
word. `SubcktDef.symbol` is `meta.symbol`, so there is one place it comes
from, and nothing is ever inferred from a subcircuit's *name*.

### Kind taxonomy and fixed pin names (order = SPICE card order)

| kind | SPICE | pins |
|---|---|---|
| resistor / capacitor / inductor | R/C/L | a, b |
| diode | D | a, k |
| vsource / isource | V/I | p, n |
| bjt_npn / bjt_pnp | Q | c, b, e (opt. s) |
| nmos / pmos | M | d, g, s, b |
| njfet / pjfet | J | d, g, s |
| vcvs / vccs | E/G | p, n, cp, cn |
| ccvs / cccs | H/F | p, n (+ `control`) |
| switch | S | p, n, cp, cn |
| tline | T | p1a, p1b, p2a, p2b |
| subcircuit | X | named by definition `ports` |
| generic | any | "1".."n" |

Transistor polarity (npn/pnp, nmos/pmos) is resolved from the `.model`
card at parse time and baked into `kind`.

## 2. Schematic IR

Coordinates: **integers**, **y-up**. All wires/path-components
axis-aligned: consecutive points differ in exactly one coordinate.

```ts
SchematicIR {
  ir: "schematic"; version: "1.0";
  meta: { title?: string; source_netlist?: string; generator?: string;
          grid: { pitch: number } }      // cm per grid unit; default 0.5
  style?: StyleDefaults
  symbols?: SymbolLib                     // overrides + generated symbols
  sheets: Sheet[]                         // sheets[0] = top ("main")
}

Sheet { name: string; elements: Element[] }

Element = PathComponent | NodeComponent | Wire | Junction
        | NetSymbol | Port | Label       // discriminated by "type"/"mode"

PathComponent {                          // → \draw (a) to[R=...] (b);
  type: "component"; mode: "path";
  ref: string; kind: Kind;
  a: [int, int]; b: [int, int];          // pin a / p / anode at "a"
  label?: LabelSpec; value_label?: LabelSpec; style?: StyleOverride
}

NodeComponent {                          // → \node[nmos, ...] at ...;
  type: "component"; mode: "node";
  ref: string; kind: Kind;
  symbol: string;                        // key into SymbolLib/built-ins
  at: [int, int];
  rot: 0 | 90 | 180 | 270;               // counterclockwise
  mirror: boolean;                       // flip across vertical axis,
                                         // applied BEFORE rotation
  pins: { [pin: string]: [int, int] };   // resolved absolute positions;
                                         // redundant; validated for
                                         // consistency with symbol geometry
  label?: LabelSpec; value_label?: LabelSpec; style?: StyleOverride
}

Wire     { type: "wire"; net: string; points: [int, int][] }  // ≥2 pts
Junction { type: "junction"; at: [int, int] }   // dot; ≥3 conductors meet

NetSymbol { type: "net_symbol"; net: string;
            variant: "ground" | "sground" | "vcc" | "vee" | "tap";
            at: [int, int]; rot: 0|90|180|270; text?: string }

Port  { type: "port"; name: string; at: [int, int];
        direction: "left"|"right"|"up"|"down" }

Label { type: "label"; at: [int, int]; text: string;   // raw LaTeX OK
        anchor?: "north"|"south"|"east"|"west"|"center" }

LabelSpec { text?: string;    // absent → derive from ref/value;
                              // "-" → suppress; explicit → verbatim
            side?: "auto"|"above"|"below"|"left"|"right" }

StyleDefaults { resistor_variant: "american"|"european";   // default european
                inductor_variant: "american"|"european"|"cute";  // default cute
                siunitx: boolean;                          // default true
                label_refs: boolean;                       // default true
                extra_preamble?: string[] }
StyleOverride { circuitikz_options?: string; color?: string }

// There is deliberately no capacitor variant: both the American and European
// standards draw a non-polarized capacitor as two parallel plates, and
// circuitikz has no american/european capacitor style. Polarized and
// electrolytic capacitors are different *devices*, so they belong in the kind
// taxonomy rather than here. (`capacitor_variant` existed in an earlier draft
// of this spec; files that still carry it load with an unknown-field warning.)
// Inductors, by contrast, have three genuinely different symbols, so the
// emitter always states the chosen one rather than inheriting a package
// default.

SymbolLib { [name: string]: SymbolDef }
SymbolDef {
  base?: string                    // circuitikz node shape if one exists
  size: [int, int]                 // bbox in grid units, centered on origin
  pins: { [pin: string]: { offset: [int, int];   // from origin, unrotated
                           label?: string;
                           anchor?: string } }    // circuitikz anchor, when
                                                  // the pin name is not it
}
```

Built-in symbols (nmos, pmos, npn, pnp, njfet, pjfet, opamp) ship in
`symbols.py` with pin offsets matched to circuitikz node shapes;
the file-level `symbols` block carries only overrides and generated
symbols — subcircuit boxes (named `subckt:<defname>`, ports distributed
left/right) and op amps (named `opamp:<defname>`) — a schematic file must
render identically forever without tool-internal lookups for non-built-ins.

A built-in's pin names are this tool's own vocabulary (`d`, `g`, `s`), so the
circuitikz anchor is looked up from the shape. A generated symbol's pin names
come from the deck (`PLUS`, `VCC`), which no table can map, so each pin states
its `anchor` instead. The pin names on a sheet are therefore always the ones
the netlist uses, which is what lets a drawing be checked against its circuit.

## 3. Derived-label formatting (emitter rules)

- refdes `R1` → `$R_1$` (letters, then subscripted trailing digits)
- values via siunitx when enabled: `10k`+ohm → `\SI{10}{\kilo\ohm}`;
  fall back to escaped raw text when unparseable
- Escape `_ $ % # & { } ~ ^ \` in all derived text (D12)

## 4. Validation invariants (implemented in `validate.py`)

Netlist IR:
1. Component ids unique per scope; pin names match kind taxonomy.
2. Every pin references an existing net in its scope.
3. `control` references an existing component of kind vsource.
4. `subckt` references an existing definition; pin names match ports.
5. Exactly one ground-class net per flat design (warning if zero).

Schematic IR:
6. All coordinates integers; wires/path-components orthogonal.
7. Path component segment length ≥ 2 (warning below).
8. NodeComponent `pins` consistent with symbol geometry under
   at/rot/mirror (error).
9. Every wire endpoint coincides with a component pin, another wire
   point on the same net, a net_symbol, or a port (error: dangling).
10. Junctions have ≥3 conductors meeting (warning), and every point
    where ≥3 conductors of one net meet has a junction (warning).
11. `symbol` keys resolve (built-in or file-local).
12. No two components overlap bounding boxes (warning).
13. Elements on one sheet referencing the same `ref` — duplicate (error).

Severity: errors → exit 2; warnings → stderr, continue.

## 5. Worked example (RC low-pass) — normative

Input SPICE:
```spice
* RC low-pass
V1 in 0 AC 1
R1 in out 10k
C1 out 0 100n
.end
```

Netlist IR (abridged where marked; tests use full versions):
```json
{ "ir": "netlist", "version": "1.0",
  "meta": { "title": "RC low-pass", "dialect": "ngspice" },
  "circuit": {
    "components": [
      { "id": "V1", "kind": "vsource", "pins": { "p": "in", "n": "0" },
        "value": { "raw": "AC 1" },
        "params": { "ac": { "raw": "1", "value": 1.0, "unit": "V" } },
        "raw": "V1 in 0 AC 1" },
      { "id": "R1", "kind": "resistor", "pins": { "a": "in", "b": "out" },
        "value": { "raw": "10k", "value": 10000.0, "unit": "ohm" },
        "raw": "R1 in out 10k" },
      { "id": "C1", "kind": "capacitor", "pins": { "a": "out", "b": "0" },
        "value": { "raw": "100n", "value": 1e-07, "unit": "F" },
        "raw": "C1 out 0 100n" } ],
    "nets": { "in":  { "name": "in",  "class": "signal" },
              "out": { "name": "out", "class": "signal" },
              "0":   { "name": "0",   "class": "ground" } } },
  "subcircuits": {} }
```

Schematic IR:
```json
{ "ir": "schematic", "version": "1.0",
  "meta": { "title": "RC low-pass", "grid": { "pitch": 0.5 } },
  "style": { "resistor_variant": "european", "inductor_variant": "cute",
             "siunitx": true, "label_refs": true },
  "sheets": [ { "name": "main", "elements": [
    { "type": "component", "mode": "path", "ref": "V1", "kind": "vsource",
      "a": [0, 4], "b": [0, 0], "label": { "side": "left" } },
    { "type": "component", "mode": "path", "ref": "R1", "kind": "resistor",
      "a": [0, 4], "b": [6, 4] },
    { "type": "component", "mode": "path", "ref": "C1", "kind": "capacitor",
      "a": [6, 4], "b": [6, 0] },
    { "type": "wire", "net": "0", "points": [[0, 0], [6, 0]] },
    { "type": "net_symbol", "net": "0", "variant": "ground",
      "at": [3, 0], "rot": 0 },
    { "type": "junction", "at": [3, 0] },
    { "type": "net_symbol", "net": "out", "variant": "tap",
      "at": [6, 4], "rot": 0, "text": "vout" } ] } ] }
```

Expected emission (normative golden file, snippet mode):
```latex
\begin{circuitikz}[scale=0.5]
  \draw (0,4) to[vsource, l_=$V_1$] (0,0);
  \draw (0,4) to[R=$R_1$, a=\SI{10}{\kilo\ohm}] (6,4);
  \draw (6,4) to[C=$C_1$, a=\SI{100}{\nano\farad}] (6,0);
  \draw (0,0) -- (6,0);
  \draw (3,0) node[ground]{} node[circ]{};
  \node[right] at (6,4) {vout};
\end{circuitikz}
```