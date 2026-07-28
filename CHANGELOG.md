# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, the CLI and IR formats may change between
minor releases.

## [Unreleased]

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

[Unreleased]: https://github.com/PeterJones7/spice2tikz/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/PeterJones7/spice2tikz/releases/tag/v0.0.1
