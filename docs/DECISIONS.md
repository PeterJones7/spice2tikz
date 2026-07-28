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
- 2026-07-28 | 1.2 | `Kind` is a `str`-subclass `Enum` with `__str__` returning its value — comparisons and f-strings behave like the JSON text on every supported Python version, while typos are caught at load time.
- 2026-07-28 | 1.2 | `ir` and `version` are `ClassVar`s, not dataclass fields — they are properties of the format, not of a document, and this keeps round-trip equality about content.
- 2026-07-28 | 1.2 | `SubcktDef` serialises as `ports`, `params?`, `components`, `nets` (declaration order of "extends Scope"); `subcircuits` is always emitted (spec §5 shows `{}`) while empty `models` is omitted, being optional.
- 2026-07-28 | 1.2 | Net dictionaries and component lists keep insertion order rather than being sorted — determinism comes from preserving source order, which also keeps diffs readable (D9).
- 2026-07-28 | 1.3 | `SymbolDef`/`PinDef` live in `symbols.py` (imported by `schematic_ir.py`) rather than the other way round — the geometry type and the rotation/mirror maths that consume it stay together, and there is no import cycle.
- 2026-07-28 | 1.3 | Built-in transistor geometry is a 4×4 box with the control terminal at (-2, 0) and the two channel terminals at (2, ±2), MOS bulk at (2, 0) — mirrors the circuitikz transistor anchors (G/B left, D up, S down) on even grid coordinates so half-boxes stay integral.
- 2026-07-28 | 1.3 | `pmos` reuses the `nmos` offsets (drain up, source down) rather than swapping them: circuitikz anchors are geometric, and pin positions are explicit in the IR anyway, so orientation is the placer's job.
- 2026-07-28 | 1.3 | Loading keeps fractional coordinates verbatim instead of rejecting them, so `validate.py` reports them as invariant-6 errors (exit 2) rather than the loader failing as a parse error (exit 1); whole-number floats are normalised to `int`.
- 2026-07-28 | 1.3 | Canonical JSON follows SPEC_IR literally (`json.dumps(indent=2)`), so coordinate pairs serialise across several lines; the compact pairs in SPEC_IR §5 are illustrative formatting, not the output format.
- 2026-07-28 | 1.3 | `style` fields absent from a file fall back to the documented defaults instead of being an error — hand-written schematics can carry a partial style block.
- 2026-07-28 | 1.4 | `Finding` is a `NamedTuple` of exactly `(severity, message, location)` — matches the roadmap's contract and unpacks like a tuple, while still being readable by attribute.
- 2026-07-28 | 1.4 | Findings are emitted invariant by invariant (6, 7, 8, … 13) and in document order within each, never sorted by severity — the report order is part of the deterministic output.
- 2026-07-28 | 1.4 | Invariant 5 also warns when a flat design declares *more than one* ground-class net, not only when it declares none: "exactly one" is the invariant, but neither direction is fatal.
- 2026-07-28 | 1.4 | Added two checks in the spirit of their neighbours: a net's dictionary key must equal its `name` (SPEC_IR §1 says `net_id == name`), and a ccvs/cccs without `control` is an error (invariant 3 is meaningless otherwise).
- 2026-07-28 | 1.4 | `tap` net symbols are annotations, not conductors, for invariants 9 and 10 — a voltage label next to two pins must not demand a junction dot; ground/supply/sground symbols do connect.
- 2026-07-28 | 1.4 | Conductor counting at a point: a wire end counts 1, a wire passing through counts 2, each component pin, port, or connecting net symbol counts 1. Net identity is not consulted, since path components carry no net in the Schematic IR.
- 2026-07-28 | 1.4 | Invariant 12 reports only genuine overlaps: positive-area intersections, or two flattened boxes overlapping along the same line (two path components drawn on top of each other). Components that merely touch at a point or along an edge are how circuits connect. Bounding boxes are compared in doubled units so odd symbol sizes stay exact.
- 2026-07-28 | 1.4 | Invariant 9 accepts a wire end that lands anywhere on another same-net wire, including mid-segment (a T), but not on a wire of a different net.
