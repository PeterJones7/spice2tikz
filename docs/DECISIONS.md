<!-- FILE: docs/DECISIONS.md -->

# spice2tikz — Decision log

Append-only log of small implementation decisions taken while resolving
ambiguities, per `CLAUDE.md` working rule 8. One line per decision, newest
last. Final architectural decisions live in `docs/DESIGN.md` §4 (D1–D16) and
are not recorded here.

Format: `YYYY-MM-DD | roadmap § | decision — rationale`

- 2026-07-28 | 0.1 | Build backend is `hatchling`, version read from `src/spice2tikz/__init__.py` — single source of truth for `__version__`, no runtime dependency implied.
- 2026-07-28 | 0.1 | Copyright holder in `LICENSE` is "spice2tikz contributors" — avoids stamping an individual's name into the repository.
- 2026-07-28 | 0.2 | ruff line length 88 with `E,W,F,I,B,C4,UP,SIM,RUF,ANN,D,PTH` selected — docstrings and annotations are enforced from the start, since `mypy --strict` is required anyway.
- 2026-07-28 | 0.2 | CI matrix is Python 3.10 and 3.12 only (per roadmap), with ruff and mypy run once on 3.12 — lint/type results are Python-version-independent here.
