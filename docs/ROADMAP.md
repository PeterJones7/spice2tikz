<!-- FILE: docs/ROADMAP.md -->

# spice2tikz — Implementation Roadmap

Rules: complete subsections in order; each subsection ends with all
tests passing and a commit. Each **section** ends with: clean tree,
full test suite green, `CHANGELOG.md` updated, **push to remote**,
and tag if specified. Do not proceed past the point the user names.

---

## Section 0 — Repository scaffold

- **0.1** `pyproject.toml` (name `spice2tikz`, src layout, entry point
  `spice2tikz = spice2tikz.cli:main`, Python ≥3.10; dev extras:
  pytest, ruff, mypy). `LICENSE` (MIT), `.gitignore` (Python + LaTeX),
  `src/spice2tikz/__init__.py` with `__version__ = "0.0.1"`,
  stub `cli.py` (prints version, exits 0), `CHANGELOG.md`,
  `docs/DECISIONS.md` (empty log). Minimal `README.md`: one-paragraph
  description, "status: pre-alpha, under active development",
  install-from-source instructions.
- **0.2** Tooling config: ruff + mypy settings in `pyproject.toml`,
  `tests/test_smoke.py` (imports package, runs CLI `--version`).
  `.github/workflows/ci.yml`: on push/PR — ruff, mypy, pytest on
  Python 3.10 and 3.12.
- **0.3** Verify `pip install -e .[dev]`, all checks green.
  **Push.** Tag `v0.0.1`.

## Section 1 — IR core

- **1.1** `quantity.py`: SPICE number parsing (suffixes f p n u µ m k
  meg g t, case-insensitive, `meg`≠`m`; unit canonicalization).
  Exhaustive unit tests including the meg/milli trap and unparseable
  passthrough (`Quantity(raw=...)` only).
- **1.2** `netlist_ir.py`: dataclasses per SPEC_IR §1, `to_json`/
  `from_json`, kind taxonomy + pin-name tables. Tests: construction,
  serde round-trip equality, spec §5 example file (add to
  `tests/corpus/rc_lowpass.netlist.json`) loads and re-dumps
  byte-identically.
- **1.3** `schematic_ir.py`: dataclasses per SPEC_IR §2 incl. element
  discrimination on load. `symbols.py`: SymbolDef machinery +
  rotation/mirror pin-resolution math (`resolve_pins(symbol, at, rot,
  mirror)`), built-ins for nmos, pmos, npn, pnp (opamp deferred).
  Tests: serde round-trip; rotation/mirror math against hand-computed
  cases (all 8 orientations of one symbol); spec §5 schematic example
  as corpus file.
- **1.4** `validate.py`: all invariants from SPEC_IR §4, returning a
  list of `(severity, message, location)` findings. Tests: one
  deliberately-broken corpus file per invariant, asserting the finding
  fires and nothing else does.
- **1.5** CLI: `spice2tikz file.json --from schematic-ir` validates and
  reports (no emission yet); exit codes per contract. Update README
  feature table. **Push.** Tag `v0.0.2`.

## Section 2 — CircuiTikZ emitter

- **2.1** `emit/circuitikz.py`: path components, wires, junctions,
  net_symbols, labels; derived-label formatting + LaTeX escaping per
  SPEC_IR §3; style defaults (D11/D12). Snippet output only.
  Unit tests for escaping and label derivation.
- **2.2** Node components: `\node[shape, ...]` emission with
  rotation/mirror translation to circuitikz options; generated
  subcircuit boxes as plain rectangles with pin stubs and labels.
  `--standalone` wrapper (documentclass standalone, circuitikz,
  siunitx).

## 2.3 Golden tests

Golden tests: create ≥6 hand-written Schematic IR corpus files:

- rc_lowpass (from spec)
- voltage_divider
- rlc_series
- bridge_rectifier (diodes, junctions)
- common_source_amp (nmos node component, all four rotations exercised across files)
- opamp_placeholder (generic box)

Golden `.tex` for each, snippet and standalone.

Implement `--update-golden` regeneration flag.

Implement a helper script:

```text
tools/render_goldens.py
```

(or equivalent)

which:

1. compiles every standalone golden `.tex` file,
2. renders each resulting PDF to PNG,
3. writes images to a dedicated output directory.

Determinism test (emit twice, byte-equal).

### Human review required

After regenerating goldens:

```bash/btw
spice2tikz --update-golden
python tools/render_goldens.py
```

Review every rendered PNG and verify:

- all expected components are present,
- component orientation and mirroring are correct,
- labels are present and readable,
- wires connect the intended pins,
- junction dots appear where expected,
- there are no obvious overlaps, collisions, or disconnected elements.

Only accept regenerated golden files after visual inspection confirms the rendered schematics match the intended circuit topology.

---

## 2.4 CLI wired

CLI wired:

```bash
spice2tikz x.schematic.json > x.tex
```

works end-to-end.

Write `docs/EMITTER.md`:

- emission rules,
- style options,
- how to add a symbol.

---

## 2.5 CI

CI: add TeX Live container job compiling every golden standalone `.tex` with:

```bash
latexmk -pdf
```

local auto-skip without `latexmk`.

Fix any compile failures in goldens.

### Human review required

After CI setup is complete, regenerate and render all goldens again and confirm there are no unexpected visual changes from the previously approved outputs.

Any change to a golden file should be accompanied by inspection of the corresponding rendered image.

---

## 2.6 Release checkpoint

CHANGELOG, README (add example snippet).

Include at least one rendered example image in the README if repository conventions permit.

Final checks before release:

- clean working tree,
- full test suite green,
- golden tests passing,
- standalone TeX compilation passing.

**Push.**

Tag `v0.0.3`.

## Section 3 — LTspice `.asc` importer  → first useful release

- **3.1** `asc_importer.py` stage 1: parse `.asc` text (SHEET, WIRE,
  SYMBOL, SYMATTR, FLAG, IOPIN records) into raw structures. Unit
  tests on record parsing, incl. Windows encodings (UTF-16 LE files
  exist in the wild — detect BOM).
- **3.2** Stage 2: map to Schematic IR — y-flip, grid rescale (LTspice
  16-unit grid → 1 IR unit), symbol table for common LTspice symbols
  (res, cap, ind, diode, voltage, current, nmos4/pmos4, npn/pnp) with
  per-symbol pin-offset data tables; FLAG 0 → ground net_symbols;
  junction inference from wire topology (then explicit per D7).
  Unknown symbols → generic box + warning.
- **3.3** Corpus: create ≥8 `.asc` files covering the symbol table
  (write them as text; they are simple). Golden Schematic IR JSON and
  golden `.tex` for each. End-to-end tests, determinism test.
- **3.4** CLI: `.asc` autodetection, `--dump-layout`. The hand-tweak
  workflow now exists: asc → JSON → edit → tex. Test the re-entry path
  explicitly.
- **3.5** Write `docs/USAGE.md` (install, all workflows, options,
  worked example with before/after tweak). README: real usage section,
  status → alpha. CHANGELOG. **Push.** Tag **`v0.1.0`**.

## Section 4 — SPICE netlist parser

- **4.1** `spice_parser.py` stage 1: line assembly (title line,
  `+` continuations, `*` and `;` comments, case normalization,
  `.end`), ngspice dialect. Unit tests.
- **4.2** Stage 2: element cards → Netlist IR per taxonomy; `.model`
  (bjt/mos polarity → kind), `.subckt`/`.ends` incl. nesting, X
  instances, E/F/G/H sources, net classing (node 0, `gnd`; supply
  inference from DC-source-fed nets). Unknown cards → generic +
  warning (D-philosophy §6). Unit tests per card type.
- **4.3** Corpus: ≥10 `.sp` files — the schematic corpus circuits as
  netlists plus subckt example, controlled-source example, a messy
  real-world-style file (continuations, comments, mixed case).
  Golden Netlist IR JSON for each; determinism test.
- **4.4** CLI: `--dump-netlist`; running a `.sp` without layout engine
  exits with clear "layout not yet implemented; use --dump-netlist"
  message (exit 0 when dump requested). **Push.** Tag `v0.1.1`.

## Section 5 — Layout engine v1

- **5.1** `layout/graph.py`: Netlist IR → connectivity graph;
  series/parallel pattern detection; classify nets (ground, supply,
  signal); pick input source and output net heuristically.
- **5.2** `layout/place.py`: placement — sources leftmost column,
  signal flow left→right by graph distance from source, ground rail
  at y=0, supplies at top; series chains as rows/columns,
  parallel groups stacked; node components oriented by convention
  (mos: gate left, source down toward ground).
- **5.3** `layout/route.py`: orthogonal wire routing on the grid
  (L-shaped and Z-shaped routes, simple collision avoidance),
  junction generation, ground/supply net_symbol placement,
  path-component minimum-length enforcement (SPEC_IR inv. 7).
- **5.4** `layout/metrics.py`: crossings, total wire length, bounding
  box area, alignment score. Emit with `-v`.
- **5.5** End-to-end tests: every §4 corpus netlist → schematic IR →
  validate (zero errors) → emit → golden `.tex` → CI-compiles.
  Record metrics per circuit in `tests/golden/metrics.json`;
  regression test asserts no metric worsens (ratchet).
- **5.6** Cross-validation: for circuits present in both `.asc` and
  `.sp` corpora, report (not assert) metric comparison auto vs human
  layout — foundation for future evaluation. Write `docs/LAYOUT.md`:
  algorithm description, heuristics list, known limitations, how the
  JSON tweak workflow compensates.
- **5.7** CHANGELOG, README (netlist path now works; tempered claims
  about layout quality). **Push.** Tag **`v0.2.0`**.

## Section 6 — Public release polish

- **6.1** `examples/`: script (`examples/build.sh` + Makefile) that
  runs the tool on corpus circuits and compiles PDFs/PNGs; commit
  generated `.tex` and PNG images; README gallery table
  (input → rendered image).
- **6.2** `docs/CONTRIBUTING.md` (dev setup, test/golden workflow,
  how to add symbols/dialects/importers), issue templates
  (`.github/ISSUE_TEMPLATE/`: bug with attach-input, feature),
  `CODE_OF_CONDUCT.md` (Contributor Covenant).
- **6.3** Release workflow: `.github/workflows/release.yml` — on tag,
  build sdist/wheel, publish to PyPI (trusted publishing; document
  the one-time PyPI setup step for the user in the workflow file
  header comment). Verify `pipx install` path.
- **6.4** Final README pass: badges (CI, PyPI, license), gallery,
  quick start, feature matrix incl. explicit non-features, roadmap
  pointer. CHANGELOG. **Push.** Tag **`v0.3.0`**.

## Section 7 — Future work (do not start unless instructed)

There is one canonical emitter: Schematic IR → CircuiTikZ. PDF, PNG and SVG
are *rendered derivatives* of that output, produced by `render.py` driving a
LaTeX toolchain, and a second native emitter is deliberately not planned —
two drawing back ends would drift apart, and the CircuiTikZ one is what makes
the symbols publication-quality in the first place.

Subcircuit symbol recognition, once 7.6, is done: a `.subckt` asks for a
symbol with `; symbol=opamp` metadata rather than a separate mapping file,
and nothing is inferred from its name. Further symbols (`comparator`,
`instamp`) reuse that mechanism and need no new plumbing.

- 7.1 KiCad `.kicad_sch` importer (s-expressions; geometry present).
- 7.2 Layout v2: layered orthogonal placement, crossing minimization,
  evaluation against the `.asc` human-layout corpus.
- 7.3 Current/voltage annotations (`i=`, `v=`) as optional
  PathComponent fields (IR v1.1, additive).
- 7.4 Additional SPICE dialect quirks (LTspice netlist, PSpice).
