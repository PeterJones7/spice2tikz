# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, the CLI and IR formats may change between
minor releases.

## [Unreleased]

Roadmap section 2 (CircuiTikZ emitter) is complete through §2.3.

### Added

- `emit/circuitikz.py`: renders a Schematic IR sheet as a CircuiTikZ snippet —
  path components, wires, junctions, net symbols, ports and labels, plus node
  components (`\node[nmos, …]` with rotation/mirror, and generated subcircuit
  boxes as rectangles with pin stubs). Derived-label formatting and LaTeX
  escaping follow `docs/SPEC_IR.md` §3.
- `--standalone`-style output via `emit_standalone()`: a compilable
  `standalone` document loading `circuitikz` and `siunitx`.
- Golden-file tests over a corpus of seven hand-written Schematic IR circuits
  (RC low-pass, voltage divider, series RLC, bridge rectifier, common-source
  amplifier, generic-box opamp placeholder, and a MOS orientation reference
  sheet), each emitted in snippet and standalone form. The same corpus drives
  validation, determinism and JSON round-trip tests. Regenerate goldens with
  `pytest --update-golden`.
- `tools/render_goldens.py`: compiles every standalone golden and renders it to
  PNG for visual review.

### Changed

- Canonical IR JSON now keeps coordinate arrays on one line
  (`"a": [0, 4]`, `"points": [[0, 0], [6, 0]]`) instead of spreading every
  number over its own line, while still writing one object field per line.
  `docs/SPEC_IR.md` §0 is amended accordingly; field order and determinism are
  unchanged, and existing files re-dump to the new format on load.

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

[Unreleased]: https://github.com/PeterJones7/spice2tikz/compare/v0.0.2...HEAD
[0.0.2]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.2
[0.0.1]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.1
