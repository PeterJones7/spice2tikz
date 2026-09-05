# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, the CLI and IR formats may change between
minor releases.

## [Unreleased]

### Changed

- An independent voltage or current source now shows its DC value beside its
  own symbol (`V1` … `\SI{5}{olt}`), and a supply rail whose source is drawn
  on the sheet is labelled with just the net name instead of `vdd = 5`. A
  component's value belongs on the component; putting it on the net stated the
  same fact twice and attached it to connectivity rather than to the part that
  establishes it. A rail the netlist declares without a drawn source still
  carries its voltage, since nothing else would say it. Time-varying sources
  still show no value: amplitude, offset and frequency are three numbers, not
  one, and the waveform belongs in the caption.

### Fixed

- Mirrored node components were drawn wrongly at 90° and 270°. The emitter
  listed `xscale=-1` before `rotate`, on the assumption that TikZ applies node
  transformations left to right; it post-multiplies, so that order mirrors
  *after* rotating and disagrees with the IR's convention. The control
  terminal's lead was drawn through the body of the device. The two orders
  agree at 0° and 180°, which is why it went unnoticed.

- Every PMOS, PNP and JFET was drawn with leads doubling back across the body
  of the device. circuitikz draws each p-type shape the other way up from its
  n-type counterpart — a PMOS source above its drain, a PNP emitter above its
  collector — but the built-in symbols declared identical pin offsets for both
  polarities, so the leads from the real anchors to the declared positions
  crossed the symbol. The p-type built-ins now carry their real geometry, the
  layout engine reads which terminal is uppermost from the symbol rather than
  from a fixed table, and `tests/test_symbols.py` pins the ordering. Reported
  by a user from a rendered figure.

- The layout engine drew a net's column wire *through* a rail-connected
  component when the net also reached a device terminal below the component's
  row — a resistor with a wire down the middle of it, which reads as a
  connection to the body of the part. Rail-connected components now reach the
  terminal they actually serve, and the router treats two-terminal components
  as obstacles so no wire can be drawn along one again.
- A wire could end on another net's terminal: the obstacle check exempted a
  foreign terminal when it was the segment's own endpoint, which is the worst
  case rather than a safe one.
- Supply glyphs sat in the middle of their rail, where they became a third
  conductor and earned a junction dot, so the arrow, the dot and the voltage
  label printed on top of each other. They now sit just past the end of the
  rail, where no dot is needed.
- Tap labels are placed on whichever side of their terminal is free, instead
  of always above — the output net's label was being printed across a
  transistor.

### Added

- `tools/contact_sheet.py`: renders every golden, plus generated reference
  sheets for every built-in symbol in all eight orientations, every
  path-component kind and every net-symbol variant, into one self-contained
  HTML page with the review checklist inline. The reference sheets cover
  emitter paths no corpus circuit reaches.
- `tests/test_anchor_geometry.py`: compiles the emitter's own output, has TeX
  report where circuitikz really places each anchor, and checks it against
  `resolve_pins`. This is the check both symbol bugs slipped past.
- `tests/test_end_to_end.py`: SPICE text in, CircuiTikZ out, with no fixtures
  in between — and, most importantly, a test that reads the finished drawing
  back as a person would and checks the recovered connectivity against the
  netlist it came from. Nothing else in the suite asks whether the figure is
  the same circuit; a sheet that shorts two nodes satisfies all thirteen IR
  invariants. Two sabotage tests keep the check honest.

## [0.3.0] — 2026-08-29

Public-release polish (roadmap section 6). No behaviour changes to the
conversion itself.

### Added

- `examples/`: `build.sh` and a `Makefile` that run the tool over eight corpus
  circuits — six netlists laid out automatically, two LTspice schematics whose
  geometry is preserved — compile each one and render it to PNG. The generated
  `.tex` and `.png` files are committed, so the README gallery works for anyone
  reading the repository on the web, and `tests/test_examples.py` regenerates
  the `.tex` to prove the gallery has not drifted from the tool.
- `README.md`: CI, PyPI, Python and licence badges; a quick start; the gallery;
  and an explicit non-features list.
- `docs/CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1),
  GitHub issue forms and a pull-request template.
- `.github/workflows/release.yml`: on a `v*` tag, checks the tag matches
  `__version__`, builds an sdist and a wheel, runs `twine check --strict`,
  installs both into fresh virtualenvs and runs `spice2tikz --version`, then
  publishes to PyPI through trusted publishing. The one-time PyPI setup a human
  must do is documented in the workflow's header.
- `Documentation` and `Issues` project URLs; `examples/` and `tools/` are
  included in the sdist.

## [0.2.0] — 2026-08-29

Roadmap sections 3, 4 and 5 are complete. The whole pipeline works: LTspice
schematics and SPICE netlists both convert to CircuiTikZ, the latter through
an automatic layout engine. Status moves from pre-alpha to **alpha**.

### Added

- `asc_importer.py`: LTspice `.asc` import. Stage one parses `Version`,
  `SHEET`, `WIRE`, `FLAG`, `IOPIN`, `SYMBOL`, `SYMATTR`, `WINDOW` and `TEXT`
  records, detecting UTF-16 LE/BE and UTF-8 byte-order marks — UTF-16 `.asc`
  files exist in the wild. Stage two maps them to the Schematic IR: y-flip,
  16-to-1 grid rescale, all eight LTspice orientations, a per-symbol pin-offset
  table checked against the shipped `.asy` files, net inference from wire
  topology, explicit junctions, and generated boxes for unknown symbols.
  Because a `.asc` file already carries a layout, this path preserves the
  geometry a person made.
- `spice_parser.py`: an ngspice SPICE netlist parser. Stage one assembles
  logical lines (title, `+` continuations, `*` and `;`/`$` comments, `.end`,
  CRLF, BOM) and reports positions as `file:line:column`. Stage two maps
  `R C L D V I Q M J E G H F S T X` cards onto the SPEC_IR §1 kind taxonomy,
  resolves transistor polarity from `.model`, parses `V`/`I` source
  specifications (`DC`, `AC`, `SIN`, `PULSE`, `PWL`, `EXP` and combinations),
  handles nested `.subckt`, and classes nets — ground by name, supply inferred
  from DC-source-fed nets. Unknown cards become `generic` components with a
  warning rather than failing the parse.
- `layout/`: the automatic layout engine (Netlist IR → Schematic IR).
  `graph.py` builds the connectivity graph, classes nets, ranks them by
  distance from the input source, and detects series chains and parallel
  groups. `place.py` gives every signal net its own column and every rail a
  horizontal line, and turns devices so the terminal wanting the supply faces
  up. `route.py` wires each net as a spine plus obstacle-avoiding stubs and
  computes junction dots with the validator's own counting rule. `metrics.py`
  measures crossings, wire length, bounding-box area and alignment.
- CLI: the pipeline runs end to end. `.sp`/`.cir`/`.net` and `.asc` inputs
  convert to LaTeX, `--dump-netlist` and `--dump-layout` write either IR as
  JSON, and `-v` reports layout metrics.
- Corpora and goldens: eleven `.asc` files (one saved as UTF-16 LE) with golden
  Schematic IR and `.tex`; eleven `.sp` decks covering all twenty component
  kinds with golden Netlist IR; and a golden Schematic IR and `.tex` for every
  automatically laid-out deck. All twenty-nine standalone goldens compile
  against TeX Live in CI.
- `tests/golden/metrics.json` and a regression ratchet: no circuit may gain
  crossings or wire length, grow, or lose alignment.
- `tools/cross_validate.py`: reports the automatic layout's metrics beside the
  human `.asc` layout for the circuits present in both corpora.
- `docs/USAGE.md`, `docs/LAYOUT.md`, `docs/CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, GitHub issue forms, and a PyPI trusted-publishing
  release workflow.
- Built-in `njfet` and `pjfet` symbols.

### Changed

- A Netlist IR input is now laid out and emitted rather than only validated;
  the "layout not yet implemented" message is gone. A netlist that fails its
  own invariants still exits 2 without being drawn.
- `README.md` describes the real workflows; status is alpha.

### Fixed

- `pytest --update-golden` wrote CRLF goldens when run on Windows, so the same
  content differed between contributors. The fixture now writes LF, a
  `.gitattributes` pins the repository to `eol=lf`, and a test fails if a CRLF
  text file ever lands again.

## [0.1.1] — 2026-08-29

### Added

- SPICE netlist parsing, reachable from the CLI through `--dump-netlist`.

## [0.1.0] — 2026-08-29

### Added

- LTspice `.asc` import: the first genuinely useful release, converting a
  captured schematic to CircuiTikZ with its geometry intact and no layout
  engine involved.

## [0.0.3] — 2026-08-29

Roadmap section 2 (CircuiTikZ emitter) is complete: the Schematic IR →
CircuiTikZ half of the pipeline works end to end.

### Added

- `emit/circuitikz.py`: renders a Schematic IR sheet as a CircuiTikZ snippet —
  path components, wires, junctions, net symbols, ports and labels, plus node
  components (`
ode[nmos, …]` with rotation/mirror, and generated subcircuit
  boxes as rectangles with pin stubs). Derived-label formatting and LaTeX
  escaping follow `docs/SPEC_IR.md` §3.
- CLI: `spice2tikz circuit.schematic.json > circuit.tex` now converts.
  `-o FILE` writes the LaTeX to a file, `--standalone` wraps it in a
  compilable `standalone` document loading `circuitikz` and `siunitx`, and
  `--dump-layout FILE` writes the Schematic IR back out (the hand-tweak escape
  hatch). Validation errors suppress emission and still exit 2.
- CLI style overrides: `--style KEY=VALUE` (repeatable) and `--config FILE`, a
  TOML file whose `[style]` table supplies `resistor_variant`,
  `inductor_variant`, `siunitx`, `label_refs`, and `extra_preamble`.
  Precedence is defaults < the document's own `style` block < `--config` <
  `--style`.
- `_toml.py`: a small TOML reader that uses `tomllib` on Python 3.11+ and falls
  back to an in-tree subset parser on 3.10, so `--config` costs no runtime
  dependency.
- Golden-file tests over a corpus of seven hand-written Schematic IR circuits
  (RC low-pass, voltage divider, series RLC, bridge rectifier, common-source
  amplifier, generic-box opamp placeholder, and a MOS orientation reference
  sheet), each emitted in snippet and standalone form. The same corpus drives
  validation, determinism and JSON round-trip tests. Regenerate goldens with
  `pytest --update-golden`.
- `tests/test_compile.py`: compiles every standalone golden with
  `latexmk -pdf`, skipped automatically when no LaTeX toolchain is present, and
  run in CI in a `texlive/texlive` container. All seven goldens compile against
  TeX Live 2026, and the rendered images were reviewed.
- `tools/render_goldens.py`: compiles every standalone golden and renders it to
  PNG for visual review; now also accepts Ghostscript as the converter.
- `docs/EMITTER.md`: emission rules element by element, the style options, and
  how to add a symbol.
- `tests/test_docs.py`: asserts the README's worked example still matches the
  golden it claims to show.

### Changed

- Canonical IR JSON now keeps coordinate arrays on one line
  (`"a": [0, 4]`, `"points": [[0, 0], [6, 0]]`) instead of spreading every
  number over its own line, while still writing one object field per line.
  `docs/SPEC_IR.md` §0 is amended accordingly; field order and determinism are
  unchanged, and existing files re-dump to the new format on load.
- `docs/SPEC_IR.md` §2: `capacitor_variant` removed from `StyleDefaults` (it
  had no referent in circuitikz or in either drawing standard) and
  `inductor_variant` added, defaulting to `cute`.
- A Netlist IR input with no `--dump-netlist` now exits 1 with "layout not yet
  implemented; use --dump-netlist" instead of exiting 0 after validating: there
  is no route from a netlist to LaTeX until the layout engine lands.

### Fixed

- IR dumps are written with LF newlines on every platform. `Path.write_text`
  without an explicit `newline=` translates `
` to the OS line ending, so
  canonical JSON was CRLF on Windows and the byte-identical-output promise held
  only on POSIX. The CLI likewise writes LaTeX through `sys.stdout.buffer`, so
  a shell redirect produces the same bytes everywhere.

## [0.0.2] — 2026-07-28

### Added

- `quantity.py`: SPICE number parsing (scale suffixes `f p n u µ m k meg g t`,
  case-insensitive, `meg` distinct from `m`) with unit canonicalisation and
  verbatim passthrough of unparseable values.
- `netlist_ir.py`: Netlist IR dataclasses, the component-kind taxonomy with
  its fixed pin-name tables, and canonical JSON serde.
- `schematic_ir.py`: Schematic IR dataclasses with element discrimination on
  load, style defaults, and canonical JSON serde.
- `symbols.py`: symbol definitions with rotation/mirror pin resolution and
  built-ins for `nmos`, `pmos`, `npn`, `pnp`.
- `validate.py`: all thirteen IR invariants of `docs/SPEC_IR.md` §4, reported
  as `(severity, message, location)` findings in a deterministic order.
- CLI: `spice2tikz FILE [--from FORMAT] [-q|-v]` loads an IR file, validates
  it, and reports findings on stderr; exit codes 0/1/2/3 per the contract.
- Test corpus: the spec §5 worked example as both IRs, plus one
  deliberately-broken file per invariant under `tests/corpus/broken/`.

## [0.0.1] — 2026-07-28

### Added

- Repository scaffold: `pyproject.toml` (src layout, `spice2tikz` console
  script, Python ≥ 3.10, zero runtime dependencies), MIT `LICENSE`,
  `.gitignore` for Python and LaTeX artefacts.
- Package skeleton `src/spice2tikz/` with `__version__` and a stub CLI that
  reports its version.
- Tooling configuration: ruff and mypy (`--strict`) settings, smoke tests,
  and a GitHub Actions CI workflow running ruff, mypy, and pytest on
  Python 3.10 and 3.12.
- Initial documentation: `README.md`, `CHANGELOG.md`, `docs/DECISIONS.md`.

[Unreleased]: https://github.com/PeterJones7/spice2tikz/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.3.0
[0.2.0]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.2.0
[0.1.1]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.1.1
[0.1.0]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.1.0
[0.0.3]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.3
[0.0.2]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.2
[0.0.1]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.1
