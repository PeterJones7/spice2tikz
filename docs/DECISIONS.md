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
- 2026-07-28 | 1.1 | Added `src/spice2tikz/_serde.py` (not in the CLAUDE.md target layout) holding the JSON primitives shared by both IR modules — version/unknown-field/type-check handling would otherwise be duplicated; the leading underscore marks it internal.
- 2026-07-28 | 1.1 | Loader diagnostics use an optional `warnings: list[str]` sink parameter rather than the `warnings` module or global state — deterministic, testable, and the caller decides where they go.
- 2026-07-28 | 1.1 | An optional IR field present but explicitly `null` is an `IRError`, not a silently-absent field — SPEC_IR requires omission, and a clear parse error beats guessing.
- 2026-07-28 | 1.1 | A SPICE number carries an exponent *or* a scale suffix, never both (`3.3e-6F` is 3.3 µ with unit F); trailing unit text is canonicalised when recognised and otherwise ignored, as SPICE ignores it.
- 2026-07-28 | 1.1 | Consequently `1F` parses as 1 femto (SPICE reads `F` as the femto scale factor); the `raw` text is always preserved so nothing is lost.
- 2026-07-28 | 1.1 | Text left over after the suffix that is not purely alphabetic (`1k*2`, `1k 2`) makes the value unparseable → `Quantity(raw=...)` only, rather than silently dropping the remainder.
- 2026-07-28 | 1.1 | Scale suffixes are stored as powers of ten and applied through a float literal (`100n` → `float("100e-9")`) — exact decimal scaling, so values match the spec examples bit-for-bit.
- 2026-07-28 | 1.1 | Suffix set is exactly the roadmap list (f p n u µ/μ m k meg g t); `mil` is not a suffix (its unit text is ignored instead).
