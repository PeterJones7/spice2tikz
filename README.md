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

**Status: pre-alpha, under active development.** Nothing is converted yet:
this release is the repository scaffold, and the command-line tool only
reports its version. See `docs/ROADMAP.md` for the implementation plan.

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
