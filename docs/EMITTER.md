<!-- FILE: docs/EMITTER.md -->

# spice2tikz — the CircuiTikZ emitter

How `src/spice2tikz/emit/circuitikz.py` turns a Schematic IR document into
LaTeX, which knobs change the result, and what to do when you need a symbol
the emitter does not yet know.

The emitter is a **pure function of the Schematic IR**: nothing else is
consulted, no state is carried between runs, and no timestamps are written, so
the same document always produces byte-identical output (CLAUDE.md working
rule 4). It renders `sheets[0]` only; multi-sheet composition is future work.

---

## 1. Running it

```sh
spice2tikz circuit.schematic.json > circuit.tex        # snippet (default, D13)
spice2tikz circuit.schematic.json -o circuit.tex       # same, without the shell
spice2tikz circuit.schematic.json --standalone > c.tex # compilable document
```

`stdout` carries the generated LaTeX and nothing else; diagnostics go to
`stderr`. Output bytes are written directly, so a redirect produces LF line
endings on every platform.

Validation runs first. Warnings are reported and emission continues; **errors
suppress emission entirely** and exit 2, because a schematic that violates the
IR invariants would render as silently-wrong output (`docs/DESIGN.md` §6).

From Python:

```python
from spice2tikz import schematic_ir
from spice2tikz.emit.circuitikz import emit, emit_snippet, emit_standalone

ir = schematic_ir.load(pathlib.Path("circuit.schematic.json"))
print(emit(ir, standalone=True))
```

---

## 2. Emission rules, element by element

The document opens with `\begin{circuitikz}[scale=<meta.grid.pitch>]`, so one
IR grid unit is `pitch` cm. Style declarations follow (§3), then one or more
lines per element, in document order — the order of `sheet.elements` is the
order of the output, which keeps generated `.tex` diffs readable.

### PathComponent → `\draw (a) to[...] (b);`

```json
{ "type": "component", "mode": "path", "ref": "R1", "kind": "resistor",
  "a": [0, 4], "b": [6, 4] }
```
```latex
\draw (0,4) to[R=$R_1$] (6,4);
```

- `kind` selects the circuitikz bipole from the `BIPOLE_NAMES` table. Every
  name in it has been checked against circuitikz 1.4.6; a kind with no
  dedicated symbol falls back to `generic` (a labelled box) rather than
  guessing.
- **Sources are drawn as the American shapes** (`american voltage source`,
  `american current source` and the controlled equivalents) so the polarity of
  a source is visible. The `vsource`/`isource` shorthands follow circuitikz's
  `europeanvoltages` flag, whose default is an unmarked bar.
- The ref label folds into the bipole slot (`to[R=$R_1$]`) only for **passive**
  kinds and only when it sits on circuitikz's own default label side. On a
  source that shorthand sets the *voltage*, not the label, so sources always
  get an explicit `l=`.
- The value label is placed with `a=` / `a^=` on whichever side the ref label
  left free. `a` and `a_` both sit opposite the natural `l` side and only `a^`
  crosses over, so the emitter pairs `l`+`a` and `l_`+`a^`.
- `LabelSpec.side` resolves geometrically: circuitikz's default label side is
  90° counterclockwise from the a→b direction. A side on the axis a path
  cannot express (e.g. `"above"` on a horizontal path) falls back to the
  default side.

### NodeComponent with a circuitikz shape → `\node[nmos, ...]`

```latex
\node[nmos, xscale=-1, rotate=90, label=right:{$M_1$}] (s2t3) at (4,2) {};
\draw (s2t3.G) -| (2,2);
```

- Options are listed `shape, xscale=-1 (if mirrored), rotate=R`: TikZ composes
  node transforms left to right, which matches the IR's "mirror before rotate"
  convention.
- Node shapes are drawn at their own absolute size and are **not** scaled by
  the environment `scale=`, so a rendered terminal never lands exactly on an
  integer grid point (an unrotated `nmos` drain sits 0.77 cm out). The emitter
  therefore names each node `s2t<element index>` and draws a short orthogonal
  lead from each documented anchor to the pin position the IR declares.
  Anchors are resolved by TeX *after* the node's own transform, so leads follow
  rotation and mirroring for free.
- Node names are positional, not derived from the refdes: a refdes may contain
  characters TikZ rejects in a node name.
- The ref label defaults to the side the component's pins leave clear
  (opposite the centre of mass of the pin directions), and rotates with the
  component. An explicit `LabelSpec.side` always wins.

### NodeComponent without a shape → a labelled box

A symbol with no `base` — a generated subcircuit box, or anything circuitikz
has no shape for — is drawn as a plain rectangle sized from `SymbolDef.size`,
with a straight stub from each pin that sits off the box edge and a centred
ref label. An unresolvable symbol falls back to a 2×2 box rather than failing:
`docs/DESIGN.md` §6 requires a labelled placeholder, never silence.

### Wire, Junction, NetSymbol, Port, Label

```latex
\draw (0,0) -- (6,0);                          % wire
\draw (3,0) node[circ]{};                      % junction
\draw (3,0) node[ground]{};                    % net_symbol, variant "ground"
\node[right] at (6,4) {vout};                  % net_symbol, variant "tap"
\node[left] at (0,4) {in};                     % port
\node[anchor=north] at (2,5) {$V_{in}$};       % free label
```

`ground`, `sground`, `vcc` and `vee` become circuitikz node shapes and honour
`rot`; `tap` is an annotation, not a conductor, and renders as plain text.

### Derived-label formatting and escaping (SPEC_IR §3, D12)

| input | output |
|---|---|
| refdes `R1` | `$R_1$` |
| refdes `R12` | `$R_{12}$` |
| refdes `Xamp` (no trailing digits) | `$\mathrm{Xamp}$` |
| `Quantity(10000.0, "ohm")` with `siunitx` | `\SI{10}{\kilo\ohm}` |
| a source's `dc` parameter | `\SI{5}{olt}` on the source symbol |
| unparseable value | the raw text, escaped |

`_ $ % # & { } ~ ^ \` are escaped in **all derived text** — refdes labels,
net-symbol text, port names, unparseable values.

Raw LaTeX passes through verbatim in exactly four places, and nowhere else:

- an explicit `LabelSpec.text` on a component's `label` / `value_label`,
- the free-standing `Label.text`,
- `StyleDefaults.extra_preamble`,
- `StyleOverride.circuitikz_options`.

A `LabelSpec.text` of `"-"` suppresses that label.

---

## 3. Style options

`StyleDefaults` (SPEC_IR §2) lives in the document's `style` block. The CLI can
override any of it without editing the file:

```sh
spice2tikz c.schematic.json --style resistor_variant=american
spice2tikz c.schematic.json --style label_refs=false --style siunitx=false
spice2tikz c.schematic.json --config style.toml
```

```toml
# style.toml
[style]
resistor_variant = "american"
inductor_variant = "european"
siunitx = true
label_refs = true
extra_preamble = ['\usepackage{amsmath}']
```

Precedence, lowest to highest: built-in defaults → the document's own `style`
block → `--config` → `--style`. `--style` is repeatable, and repeating
`extra_preamble` appends one line per flag. Booleans accept
`true/false/yes/no/on/off/1/0`.

| key | values | default | effect |
|---|---|---|---|
| `resistor_variant` | `american`, `european` | `european` | `\ctikzset{... resistors}` |
| `inductor_variant` | `american`, `european`, `cute` | `cute` | `\ctikzset{... inductors}` |
| `siunitx` | boolean | `true` | format parsed values as `\SI{}{}` |
| `label_refs` | boolean | `true` | emit derived `$R_1$` labels at all |
| `extra_preamble` | list of strings | empty | verbatim lines in the standalone preamble |

Both variants are **always declared**, never left implicit. circuitikz's own
default resistor is the American zigzag, so the IR's European default would
silently render as American if the emitter only spoke up for the non-default
case; declaring both also means the rendering cannot drift with a circuitikz
release.

There is deliberately **no capacitor variant**: circuitikz has no
american/european capacitor style, and both standards draw a non-polarized
capacitor as two parallel plates. Polarized and electrolytic capacitors are
different *devices* and belong in the kind taxonomy.

Per-element overrides come from `StyleOverride`:

```json
"style": { "color": "red", "circuitikz_options": "thick, dashed" }
```

`circuitikz_options` is injected verbatim (D12) — it is an escape hatch, and
nothing validates it.

`--standalone` wraps the snippet in:

```latex
\documentclass[border=2pt]{standalone}
\usepackage{circuitikz}
\usepackage{siunitx}
<extra_preamble lines>
\begin{document}
...
\end{document}
```

`border=2pt` alone crops to the drawing; adding standalone's `tikz` class
option would defeat the cropping when circuitikz is loaded with
`\usepackage`, yielding a full letter page.

---

## 4. How to add a symbol

Symbols come from two places: the built-ins in `src/spice2tikz/symbols.py`,
and the document's own `symbols` block, which always wins. A schematic file
carrying its own `SymbolDef` renders identically forever, with no tool-internal
lookups — that is the point of the block.

### 4.1 A one-off symbol, in the document

Add it to the file's `symbols` map and point a `NodeComponent` at it:

```json
"symbols": {
  "my_opamp": {
    "size": [4, 4],
    "pins": { "in_p": { "offset": [-2, 1] },
              "in_n": { "offset": [-2, -1] },
              "out":  { "offset": [2, 0] } }
  }
}
```

With no `base`, this draws as a labelled box with pin stubs — good enough for
a placeholder, and it needs no code change. `size` is the bounding box in grid
units, centred on the origin; `offset` is measured from the origin *before*
rotation and mirroring.

### 4.2 A built-in backed by a real circuitikz shape

1. **Check the CircuiTikZ manual first.** `docs/CIRCUITIKZ_NOTES.md` is the
   distilled reference; consult `docs/circuitikz_manual.MD` when a shape,
   anchor, or option is not in the notes, and add what you learn back to the
   notes file. Do not guess shape or anchor names (CLAUDE.md, "CircuiTikZ
   Reference Policy").
2. Add a `SymbolDef` to `BUILTIN_SYMBOLS` in `symbols.py` with `base` set to
   the circuitikz shape name, `size` as its bounding box in grid units, and
   one `PinDef` per terminal. Keep offsets on **even** coordinates so that
   half-boxes stay integral, and point them in the direction of the real
   circuitikz anchors — a lead is drawn from the anchor to the declared pin,
   so a wrong direction shows up as a kinked wire.
3. Add the pin→anchor mapping to `BASE_PIN_ANCHORS`, keyed by the `base` name.
   A pin with no entry simply gets no lead.
4. If the symbol is a 2-terminal device that should be placed by its endpoints
   instead, it belongs in `BIPOLE_NAMES` in `emit/circuitikz.py` as a path
   component, not in the symbol library at all.
5. Add a corpus circuit under `tests/corpus/` that uses it, run
   `pytest --update-golden`, and **look at the rendered image**:

   ```sh
   pytest --update-golden
   python tools/render_goldens.py
   ```

   Check the component is present, oriented and mirrored correctly, labelled,
   wired to the intended pins, with junction dots where expected and no
   overlaps. Golden `.tex` diffs prove the output changed; only a rendering
   proves it is right.
6. Record any CircuiTikZ-specific assumption in `docs/DECISIONS.md`.

### 4.3 Rotation and mirroring

`resolve_pins(symbol, at, rot, mirror)` in `symbols.py` is the single source of
truth: **mirror first** (flip across the vertical axis), **then rotate**
counterclockwise. `NodeComponent.pins` holds the resolved absolute positions
and is validated against the symbol geometry (invariant 8), so a file whose
pins disagree with its symbol is rejected rather than mis-drawn. Never
hand-type rotated pin coordinates — compute them with `resolve_pins`.

---

## 5. Testing changes to the emitter

- `tests/test_emit_circuitikz.py` — unit tests for escaping, label derivation,
  quantity formatting, and per-element emission.
- `tests/test_golden.py` — every `tests/corpus/*.schematic.json` emitted in
  snippet and standalone form and compared byte-for-byte, plus validation,
  determinism and JSON-round-trip tests over the same corpus. Adding a corpus
  file is enough to be covered by all of them.
- `tests/test_compile.py` — compiles every standalone golden with `latexmk`,
  automatically skipped when no LaTeX toolchain is installed.
- `pytest --update-golden` regenerates the goldens; review the diff in git,
  and re-render the images before accepting it.
