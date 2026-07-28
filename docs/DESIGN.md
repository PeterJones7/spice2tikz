<!-- FILE: docs/DESIGN.md -->

# spice2tikz — Design Document

## 1. Background and motivation

People writing papers, theses, and teaching material in LaTeX draw
circuits with **CircuiTikZ**. Doing so by hand is slow and error-prone.
Meanwhile the circuits usually already exist in machine-readable form:
SPICE netlists (simulation) or LTspice `.asc` schematics (capture).
No maintained free tool converts these to CircuiTikZ. The one prior
attempt (`lt2circuiTikZ`) is stale and limited.

**Core insight:** the conversion itself is easy; the hard problem is
that SPICE netlists contain *no geometry*. Producing a readable
schematic from pure connectivity is an automatic-layout problem with
strong domain conventions (signal flow left→right, ground rail at
bottom, orthogonal wires, minimal crossings). This project treats
layout as the central engineering challenge and structures everything
around it.

**Differentiator:** deterministic, reproducible, hand-tweakable,
publication-quality output — in contrast to AI/LLM-based schematic
generators. Same input, same output, forever; outputs diff cleanly in
version control.

## 2. Architecture

```
 SPICE netlist ──parse──► Netlist IR ──layout engine──► Schematic IR ──emit──► circuitikz
 LTspice .asc ──import────────────────────────────────► Schematic IR ──emit──► (later: SVG)
 IR JSON files ──load──► either IR (hand-edit / pipeline re-entry)
```

Two IRs, both JSON-serializable (spec: `docs/SPEC_IR.md`):

- **Netlist IR** — logical: components, pins, nets, hierarchy. No
  coordinates.
- **Schematic IR** — physical: placed components, wires, junctions,
  labels on an integer grid. Fully self-contained (renderable without
  the Netlist IR or tool internals).

The JSON round-trip is a first-class feature, not debugging aid:
users run auto-layout, dump Schematic IR, hand-tweak coordinates, and
re-emit. This "escape hatch" means the layout engine only has to get
*close*, not perfect, for the tool to be useful.

## 3. Strategic sequencing (why the roadmap is ordered as it is)

1. **IRs and the circuitikz emitter first** — the stable core
   everything plugs into; testable immediately with hand-written IR
   files.
2. **LTspice `.asc` importer second** — `.asc` files *contain*
   geometry, so this path yields a genuinely useful tool early
   (v0.1) with no layout engine at all. It also builds a corpus of
   human-made layouts that later serves as ground truth for
   evaluating auto-layout quality.
3. **SPICE parser third** — enables the netlist path end-to-end
   (initially via `--dump-netlist` only).
4. **Layout engine v1 fourth** — series-parallel decomposition plus
   convention heuristics. Generic graph layout (Graphviz, force-
   directed) is known to produce schematic-hostile results; do not
   use it. For the small circuits academics draw (<30 components),
   heuristics beat generality.
5. **Polish, gallery, PyPI release.**
6. Future: SVG emitter, KiCad import, smarter layout (ELK-style
   layered orthogonal), current/voltage annotations.

## 4. Final design decisions (do not relitigate)

| # | Decision | Rationale |
|---|---|---|
| D1 | Python ≥3.10, stdlib-only runtime | Contributor accessibility; pipx-friendly; workload is small |
| D2 | MIT license | Maximum adoption |
| D3 | `argparse` CLI, library-first internals (`cli.py` is a thin shell) | Zero deps; embeddable |
| D4 | Two IRs, JSON, versioned `"version": "1.0"` | See §2; minor versions additive-only |
| D5 | Integer grid, y-up coordinates | No float bugs; matches TikZ; hand-editable |
| D6 | Two component placement modes: `path` (2-terminal, by endpoints) and `node` (multi-terminal, origin+rot+mirror) | Mirrors circuitikz's own idioms; emitter stays simple |
| D7 | Explicit junctions; grounds/supplies as `net_symbol` elements, not components | Dumb emitter; clean netlist↔schematic correspondence |
| D8 | Values as `Quantity {raw, value?, unit?}`; raw text always preserved | Graceful degradation on unparseable SPICE expressions |
| D9 | IDs = SPICE refdes / node names (no synthetic IDs) | Determinism; readable diffs |
| D10 | ngspice dialect first; dialect field in meta for future | Rabbit-hole containment |
| D11 | European resistor/capacitor symbols default; American via style | Configurable; must pick one |
| D12 | LaTeX injection allowed ONLY in `Label.text` (explicit), `extra_preamble`, `circuitikz_options`; all derived text is escaped/formatted by emitter | One clear escaping rule |
| D13 | Emitted `.tex` is a snippet by default; `--standalone` wraps it | Composability with documents and with CI compile tests |
| D14 | Golden-file testing as backbone; CI compiles LaTeX | See §5 |
| D15 | Layout v1 = series-parallel decomposition + heuristics; no external layout libs | See §3 point 4 |
| D16 | Package/CLI/repo name: `spice2tikz` | Discoverable; the `.asc` path is a feature, not the name |

## 5. Testing strategy

- **Unit tests**: parsers, quantity parsing (SPICE suffixes:
  `f p n u µ m k meg g t`, case-insensitive, `meg` vs `m` trap),
  serde round-trips (IR → JSON → IR identical), validators, geometry
  (rotation/mirror math).
- **Golden-file tests**: inputs in `tests/corpus/`, expected outputs
  in `tests/golden/`. A test helper compares generated output to
  golden byte-for-byte. Provide a single regeneration entry point
  (`pytest --update-golden` via a fixture flag) so intentional changes
  are one command, and diffs are reviewed in git.
- **Round-trip tests**: Schematic IR → JSON → load → emit must equal
  direct emit.
- **Compile tests (CI)**: every golden `.tex` is wrapped standalone
  and compiled with `latexmk -pdf` in a TeX Live container job;
  failure = red build. Locally skipped unless `latexmk` is present
  (auto-detect, `pytest.mark.skipif`).
- **Layout metrics tests** (§5 of roadmap): assert crossing count,
  total wire length, and bounding box stay within recorded bounds for
  each corpus circuit — a regression ratchet, not absolute quality.
- **Determinism test**: run pipeline twice on every corpus file,
  assert byte-identical outputs.

## 6. Error-handling philosophy

- Parse errors: report file/line/column, exit 1.
- Unknown/unsupported SPICE cards: **warn to stderr and continue**,
  mapping to `generic` components — a partial schematic beats none.
- Validation errors on IR files: itemized report, exit 2.
- Never emit silently-wrong output: if a component can't be drawn,
  draw a labelled placeholder box and warn.

## 7. Known traps (learn from these, don't rediscover them)

- SPICE `M` suffix means milli, `MEG` means mega. Case-insensitive.
- SPICE node `0` is ground; also accept `gnd` as ground-class hint.
- LTspice `.asc` is y-down; flip on import (D5).
- LTspice component symbols have per-symbol pin-offset quirks; encode
  offsets in a data table, not code logic.
- `_`, `$`, `%`, `#`, `&` in SPICE names must be LaTeX-escaped in all
  derived labels (D12).
- Continuation lines (`+`), inline comments (`;`, `$` in some
  dialects), `*` comment lines, first-line-is-title in SPICE.
- CircuiTikZ path components draw at the *midpoint* of their segment;
  a path component needs sufficient segment length (≥2 grid units) —
  validator should warn.