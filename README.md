# spice2tikz

`spice2tikz` converts circuit descriptions — SPICE netlists and LTspice
`.asc` schematics — into publication-quality
[CircuiTikZ](https://ctan.org/pkg/circuitikz) LaTeX code, so that circuits
that already exist in machine-readable form do not have to be redrawn by
hand for a paper, thesis, or lecture note. Conversion runs through two
JSON intermediate representations: a *Netlist IR* (logical connectivity)
and a *Schematic IR* (placed components on an integer grid). The Schematic
IR can be dumped, hand-tweaked, and re-emitted, and the whole pipeline is
deterministic — the same input always produces byte-identical output, so
generated `.tex` diffs cleanly in version control.

**Status: pre-alpha, under active development.** No conversion happens yet:
so far the two intermediate representations and their validator exist, and
the command-line tool can load and check IR files. See `docs/ROADMAP.md` for
the implementation plan.

## Features

| Stage | What it does | Status |
|---|---|---|
| Netlist IR | logical connectivity: components, pins, nets, subcircuits | ✅ 0.0.2 |
| Schematic IR | placed components, wires, junctions, labels on an integer grid | ✅ 0.0.2 |
| Symbol library | pin geometry for `nmos`, `pmos`, `npn`, `pnp` incl. rotation/mirror | ✅ 0.0.2 |
| Validation | the 13 IR invariants of `docs/SPEC_IR.md` §4, as errors and warnings | ✅ 0.0.2 |
| CircuiTikZ emitter | IR → `.tex` snippet or standalone document | planned (§2) |
| LTspice `.asc` import | schematic capture → Schematic IR, geometry preserved | planned (§3) |
| SPICE netlist parser | `.sp`/`.cir`/`.net` → Netlist IR | planned (§4) |
| Layout engine | Netlist IR → Schematic IR (automatic placement and routing) | planned (§5) |

Explicit non-features: no simulation, no netlist editing, no AI/heuristic
"redrawing" of arbitrary images, no GUI.

## Requirements

- Python ≥ 3.10
- No runtime dependencies (standard library only)

## Install from source

```sh
git clone https://github.com/PeterJones7/spice2tikz.git
cd spice2tikz
python -m pip install -e .
```

For development (pytest, ruff, mypy):

```sh
python -m pip install -e ".[dev]"
```

Check the installation:

```sh
spice2tikz --version
```

## Usage so far

Validate an IR file (format deduced from the extension, or forced with
`--from {spice,asc,netlist-ir,schematic-ir}`):

```sh
spice2tikz circuit.schematic.json          # findings on stderr
spice2tikz circuit.netlist.json -v         # plus a summary of the document
spice2tikz circuit.schematic.json -q       # errors only
```

Exit codes: `0` ok, `1` input parse error, `2` validation error, `3`
internal error. Diagnostics go to stderr; stdout is reserved for generated
LaTeX.

## Development

```sh
ruff check .        # lint
ruff format --diff  # formatting
mypy                # type-check src/ in strict mode
pytest              # test suite
```

## Documentation

- `docs/DESIGN.md` — motivation, architecture, design decisions
- `docs/SPEC_IR.md` — the two intermediate representations
- `docs/ROADMAP.md` — implementation plan
- `docs/DECISIONS.md` — log of small implementation decisions

## License

MIT — see [LICENSE](LICENSE).
