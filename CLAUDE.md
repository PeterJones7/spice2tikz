<!-- FILE: CLAUDE.md (repository root) -->

# spice2tikz — Project Instructions for Claude Code

## What this project is

A command-line tool (Python) that converts circuit descriptions —
SPICE netlists and LTspice `.asc` schematics — into publication-quality
**CircuiTikZ** LaTeX code. Free/open-source; no existing free tool does
this well. Target users: academics, students, and engineers writing
LaTeX documents.

## Authoritative documents — read before coding

- `docs/DESIGN.md` — background, motivation, architecture, all design
  decisions. **Decisions there are final; do not revisit them.**
- `docs/SPEC_IR.md` — the two intermediate representations (Netlist IR,
  Schematic IR). This is the contract between all modules.
- `docs/ROADMAP.md` — numbered implementation plan. The user will
  instruct you to implement up to a specific section (e.g. "up to end
  of 2.3"). **Never proceed past the instructed point.**

## Working rules

1. **Follow the roadmap strictly.** Complete subsections in order.
   Each subsection ends with passing tests before moving on.
2. **Commit discipline.** One commit per roadmap subsection minimum,
   message format: `feat(1.2): schematic IR dataclasses + serde`.
   Use `feat/fix/test/docs/chore` prefixes. At the end of each major
   roadmap **section**, ensure the working tree is clean, all tests
   pass, and push to the remote (`git push`). If a tag is specified in
   the roadmap, create and push it.
3. **Tests are not optional.** Every subsection that says "tests"
   means: write them, run them, make them pass. Golden-file tests are
   the backbone — see `docs/DESIGN.md` §Testing.
4. **Determinism is a core promise.** Same input → byte-identical
   output. No timestamps in outputs, no dict-ordering dependence
   (use sorted iteration or insertion-ordered structures deliberately),
   no randomness without a fixed seed.
5. **Zero runtime dependencies** for the core package. Standard
   library only (`argparse`, `json`, `dataclasses`, etc.). Dev
   dependencies (pytest, ruff, mypy) are fine.
6. **Python ≥ 3.10**, `src/` layout, type hints everywhere,
   `ruff` clean, `mypy --strict` clean on `src/`.
7. **Docs are deliverables.** The roadmap schedules creation of
   user-facing docs at specific points — write them then, not before,
   to avoid churn. Keep `README.md` accurate at every push.
8. **When something is ambiguous**, make the smallest reasonable
   decision consistent with `docs/DESIGN.md`, record it in
   `docs/DECISIONS.md` (append-only log, one line per decision), and
   continue. Do not block waiting for input.

## Repository layout (target state)

```
spice2tikz/
├── CLAUDE.md
├── README.md
├── LICENSE                  (MIT)
├── CHANGELOG.md
├── pyproject.toml
├── .gitignore
├── .github/workflows/ci.yml
├── docs/
│   ├── DESIGN.md
│   ├── SPEC_IR.md
│   ├── ROADMAP.md
│   ├── DECISIONS.md
│   ├── USAGE.md              (created in roadmap §3)
│   ├── EMITTER.md            (created in roadmap §2)
│   ├── LAYOUT.md             (created in roadmap §5)
│   └── CONTRIBUTING.md       (created in roadmap §6)
├── src/spice2tikz/
│   ├── __init__.py           (holds __version__)
│   ├── cli.py
│   ├── netlist_ir.py
│   ├── schematic_ir.py
│   ├── validate.py
│   ├── symbols.py
│   ├── quantity.py
│   ├── spice_parser.py
│   ├── asc_importer.py
│   ├── layout/
│   │   ├── __init__.py
│   │   ├── graph.py
│   │   ├── place.py
│   │   ├── route.py
│   │   └── metrics.py
│   └── emit/
│       ├── __init__.py
│       └── circuitikz.py
├── tests/
│   ├── test_*.py
│   ├── corpus/               (input files: .sp, .asc, .json)
│   └── golden/               (expected outputs: .tex, .json)
└── examples/                 (created in roadmap §6)
```

## CLI contract (implement incrementally per roadmap)

```
spice2tikz INPUT [-o OUTPUT] [options]

Input format by extension (.sp/.cir/.net → SPICE, .asc → LTspice,
.json → IR) or forced with --from {spice,asc,netlist-ir,schematic-ir}.
Output: circuitikz snippet to stdout by default; -o writes a file.

Options:
  --standalone          wrap output in a compilable standalone document
  --dump-netlist FILE   write Netlist IR JSON
  --dump-layout FILE    write Schematic IR JSON
  --style KEY=VALUE     override style defaults (repeatable)
  --config FILE         TOML config for style defaults
  -q / -v               quiet / verbose (diagnostics to stderr)

Exit codes: 0 ok, 1 input parse error, 2 validation error, 3 internal.
```