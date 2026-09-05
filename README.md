# spice2tikz

[![CI](https://github.com/PeterJones7/spice2tikz/actions/workflows/ci.yml/badge.svg)](https://github.com/PeterJones7/spice2tikz/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
<!-- Add these back with the first PyPI release; until the package is published
     they render as a red "package or version not found":
[![PyPI](https://img.shields.io/pypi/v/spice2tikz.svg)](https://pypi.org/project/spice2tikz/)
[![Downloads](https://img.shields.io/pypi/dm/spice2tikz.svg)](https://pypi.org/project/spice2tikz/)
-->

**Turn a SPICE netlist or an LTspice schematic into a CircuiTikZ figure, from
the command line.** The circuit is already in a file; you should not have to
redraw it by hand for a paper.

<table>
<tr>
<td width="46%" valign="middle">

<pre>$ cat rc_lowpass.sp
* RC low-pass
V1 in 0 AC 1
R1 in out 10k
C1 out 0 100n
.end

$ spice2tikz rc_lowpass.sp &gt; rc.tex</pre>

</td>
<td width="54%" valign="middle" align="center">
<img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/rc_lowpass.png" width="360"
     alt="The same RC low-pass drawn as a schematic: a voltage source on the left, a 10 kilohm resistor across the top, a 100 nanofarad capacitor on the right, and a ground symbol on the bottom rail.">
</td>
</tr>
</table>

There are no coordinates in that netlist and none were written by hand. The
tool works out the placement, the wiring and the junction dots, and emits LaTeX
you can `\input`.

## Why

Papers, theses and lecture notes draw circuits with
[CircuiTikZ](https://ctan.org/pkg/circuitikz), and doing it by hand is slow and
easy to get subtly wrong. Meanwhile the circuit usually already exists,
machine-readable, as a netlist you simulated or a schematic you drew. Nothing
maintained converts one to the other.

Three properties are worth caring about:

- **Deterministic.** The same input always produces byte-identical output — no
  timestamps, no dictionary-order surprises. Generated `.tex` belongs in
  version control, where it diffs cleanly.
- **Never silently wrong.** A component it cannot draw becomes a labelled box
  and a warning, not a guess. A schematic that fails validation is not emitted
  at all, because a subtly wrong circuit diagram is worse than no diagram.
  Every generated file in this repository is compiled by CI against TeX Live.
- **Overridable.** The intermediate format is documented, hand-editable JSON,
  so automatic layout only has to get *close*.

## Install

Not on PyPI yet — install from the repository:

```sh
pipx install git+https://github.com/PeterJones7/spice2tikz
```

or into your current environment:

```sh
python -m pip install git+https://github.com/PeterJones7/spice2tikz
```

Python ≥ 3.10, and nothing else — the package has **no runtime dependencies**.
The LaTeX it generates needs the `circuitikz` and `siunitx` packages, both of
which ship with TeX Live and MiKTeX.

To have spice2tikz hand you a **PDF, PNG or SVG** instead of LaTeX, it also
needs a toolchain to compile with. On Debian or Ubuntu:

```sh
sudo apt install latexmk texlive-latex-extra texlive-pictures texlive-science poppler-utils
```

## Usage

```sh
spice2tikz amplifier.sp > amplifier.tex   # netlist, laid out automatically
spice2tikz amplifier.asc > amplifier.tex  # LTspice, your geometry preserved
spice2tikz amplifier.sp -o amp.pdf        # or .png, or .svg, or .tex
```

The input format comes from the extension — `.sp`, `.cir` and `.net` are SPICE,
`.asc` is LTspice, `.json` is either intermediate format — or force it with
`--from`. The **output** format comes from the extension too: `.tex` is the
CircuiTikZ source, and `.pdf`, `.png` and `.svg` are rendered from exactly that
source by a LaTeX run, so the picture you check is the picture your document
gets. `--dpi` sets the PNG resolution; `--standalone` wraps the `.tex` in a
compilable document and is implied by the rendered formats.

Drop the result into a document:

```latex
\documentclass{article}
\usepackage{circuitikz}
\usepackage{siunitx}
\begin{document}
\begin{figure}
  \centering
  \input{amplifier.tex}
  \caption{Common-source amplifier, converted from \texttt{amplifier.sp}.}
\end{figure}
\end{document}
```

`-v` reports what the layout engine decided, which is the quickest way to tell
a good result from one worth nudging:

```console
$ spice2tikz common_source_amp.sp -v -o amp.tex
spice2tikz: reading common_source_amp.sp as spice
spice2tikz: netlist IR: 4 component(s), 4 net(s), 0 subcircuit definition(s)
spice2tikz: schematic IR: 1 sheet(s), 16 element(s)
spice2tikz: layout: 4 component(s), 5 wire(s), 0 crossing(s), wire length 39, bbox area 192, alignment 0.91
spice2tikz: common_source_amp.sp: 0 error(s), 0 warning(s)
spice2tikz: wrote amp.tex
```

Zero crossings and nearly everything aligned: nothing to fix here.

Diagnostics go to stderr and the LaTeX goes to stdout, so a redirect produces
exactly the bytes `-o` would write, with LF endings on every platform. Exit
codes are `0` ok, `1` input parse error, `2` validation error, `3` internal.

### Restyling

European resistors, `\SI{}{}` value labels and derived `$R_1$` refdes labels
are the defaults. Override them per run, or from a TOML file:

```sh
spice2tikz circuit.sp --style resistor_variant=american --style siunitx=false
spice2tikz circuit.sp --config house-style.toml
```

```toml
# house-style.toml
[style]
resistor_variant = "american"
inductor_variant = "european"
extra_preamble = ['\usepackage{amsmath}']
```

Every option is listed in [`docs/USAGE.md`](docs/USAGE.md); every style key, and
how to add a symbol, in [`docs/EMITTER.md`](docs/EMITTER.md).

## When the layout needs a nudge

A netlist contains no geometry, so on the SPICE path the tool has to invent
some. It is good on small circuits and more opinionated than you may like on
large ones — so what it produces is **a file you can edit**, not a black box:

```sh
spice2tikz amplifier.sp --dump-layout amplifier.schematic.json
$EDITOR amplifier.schematic.json
spice2tikz amplifier.schematic.json > amplifier.tex
```

The dump is integer coordinates on a grid, y-up, one JSON object per component
and wire ([`docs/SPEC_IR.md`](docs/SPEC_IR.md) §2). Moving a resistor is editing
two numbers:

```json
{ "type": "component", "mode": "path", "ref": "RD", "kind": "resistor",
  "a": [16, 12], "b": [16, 8],
  "label": { "text": "$R_D$", "side": "left" },
  "value_label": { "text": "\\SI{4.7}{\\kilo\\ohm}" } }
```

Re-emitting an unedited dump reproduces the original output byte for byte, so
every difference in the `.tex` is one you made — and the validator runs on your
edited file, so a mistake is reported with a coordinate instead of being
quietly drawn. The same escape hatch works from LTspice: `.asc` → JSON → edit →
`.tex`.

## Gallery

Each image is generated by [`examples/build.sh`](examples/build.sh), which runs
the tool over circuits from the test corpus and compiles the result. The `.tex`
files sit beside them in [`examples/`](examples/), so you can read exactly what
came out; CI regenerates them and fails on any drift.

| Source | Result |
|---|---|
| [`voltage_divider.sp`](tests/corpus/spice/voltage_divider.sp) | <img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/voltage_divider.png" width="190" alt="Voltage divider: a 5 V source and two 10 kilohm resistors in series, output tapped between them."> |
| [`rlc_series.sp`](tests/corpus/spice/rlc_series.sp) | <img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/rlc_series.png" width="250" alt="Series RLC circuit: source, 100 ohm resistor, 10 millihenry inductor and 100 nanofarad capacitor around one loop."> |
| [`bridge_rectifier.sp`](tests/corpus/spice/bridge_rectifier.sp) | <img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/bridge_rectifier.png" width="250" alt="Full-wave bridge rectifier: four diodes around a source, feeding a 1 kilohm load."> |
| [`common_source_amp.sp`](tests/corpus/spice/common_source_amp.sp) | <img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/common_source_amp.png" width="250" alt="NMOS common-source amplifier: a 4.7 kilohm drain resistor from the 5 V rail, signal source on the gate, source grounded."> |
| [`bjt_amp.sp`](tests/corpus/spice/bjt_amp.sp) — two stages | <img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/bjt_amp.png" width="440" alt="Two-stage BJT amplifier: biasing divider, input coupling capacitor, an NPN stage with a bypassed emitter resistor, and a second stage."> |

### The input decides the layout

Same tool, both columns. On the left a netlist, which carries no geometry, so
placement is invented. On the right an LTspice schematic, whose geometry a
person already chose, reproduced as drawn.

<table>
<tr>
<th width="50%">netlist — placement invented</th>
<th width="50%">LTspice — geometry preserved</th>
</tr>
<tr>
<td align="center" valign="top">
<img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/rc_lowpass.png" width="290"
     alt="RC low-pass laid out automatically from a netlist: source, resistor and capacitor around one loop above a ground rail.">
<br><em><code>rc_lowpass.sp</code></em>
</td>
<td align="center" valign="top">
<img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/ltspice_rc_lowpass.png" width="230"
     alt="The same RC low-pass reproduced from an LTspice schematic, in nearly the same arrangement.">
<br><em><code>rc_lowpass.asc</code> — near enough identical</em>
</td>
</tr>
<tr>
<td align="center" valign="top">
<img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/cmos_inverter.png" width="330"
     alt="CMOS inverter laid out automatically from a netlist: the two transistors sit beside a shared output column with wiring detouring around them, alongside a bias current source and load capacitor.">
<br><em><code>cmos_inverter.sp</code> — correct, and wordier than a person would draw</em>
</td>
<td align="center" valign="top">
<img src="https://raw.githubusercontent.com/PeterJones7/spice2tikz/main/examples/ltspice_cmos_inverter.png" width="200"
     alt="A CMOS inverter from an LTspice schematic: PMOS above NMOS in a totem pole, gates joined on the left, output taken from between them.">
<br><em><code>cmos_inverter.asc</code> — the conventional totem pole</em>
</td>
</tr>
</table>

On the simple circuit you would struggle to say which is which. On the inverter
the difference is obvious: the engine has no notion of stacking two devices in
one column, so it sets them side by side and routes around. (The two inverters
are not quite the same circuit — the netlist also carries a bias current source
and a load capacitor, which the LTspice sheet does not.)

That is the honest state of automatic layout: correct everywhere, idiomatic on
simple circuits, and visibly worse than a person on stacked devices.
[`docs/LAYOUT.md`](docs/LAYOUT.md) §4 measures it on the circuits that exist in
both forms and §5 lists exactly what the engine is bad at. **If your circuit
already exists as a `.asc`, use that** — the LTspice path needs no layout engine
at all.

## What it does, and what it will not

| | |
|---|---|
| **SPICE netlists** | `.sp` / `.cir` / `.net`, ngspice dialect: `R C L D V I Q M J E G H F S T X`, `.model`, nested `.subckt`, and source specifications (`DC`, `AC`, `SIN`, `PULSE`, `PWL`, `EXP`) |
| **LTspice schematics** | `.asc`, including UTF-16 files, with wires, flags, I/O pins, all eight symbol orientations, and per-symbol pin offsets taken from the shipped `.asy` files |
| **Automatic layout** | signal flow left to right, ground at the bottom, supplies at the top, orthogonal wires, junction dots, devices turned by convention |
| **Output** | a CircuiTikZ snippet to `\input`, a standalone document that crops to the drawing, or a PDF, PNG or SVG rendered from it |
| **Escape hatch** | both intermediate formats dump to documented, hand-editable JSON and load straight back |

Deliberately **not** included:

- **No simulation.** Use ngspice; this draws circuits, it does not solve them.
- **No AI or heuristic redrawing of images.** The input must already be
  machine-readable — determinism is the whole point.
- **No GUI**, and no netlist editing: conversion is one-way.
- **No hierarchy expansion.** A subcircuit instance is one labelled box; its
  contents are not drawn on their own sheet.
- **No current or voltage annotations** yet ([roadmap](docs/ROADMAP.md) §7.3).
- **One dialect.** ngspice, on purpose;
  [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) §5 says how to add another.

## How it works

```
 SPICE netlist ──parse──► Netlist IR ──layout──► Schematic IR ──emit──► CircuiTikZ
 LTspice .asc ──import──────────────────────────► Schematic IR ──emit──► CircuiTikZ
 either IR JSON ──load──► hand-edit, and re-enter the pipeline at that stage
```

Two JSON intermediate representations do the work. The **Netlist IR** is
logical — components, pins, nets, hierarchy, no coordinates. The **Schematic
IR** is physical — placed components, wires, junctions and labels on an integer
grid, self-contained enough to render without any tool-internal state. Both are
specified in [`docs/SPEC_IR.md`](docs/SPEC_IR.md) and checked against thirteen
invariants before anything is emitted.

Splitting them is what makes the hard part tractable. Converting a circuit is
easy; *placing* it is the real problem, and isolating that behind a file format
means a bad layout can be fixed without touching the tool.

Here is the whole of the RC low-pass above, as emitted:

```latex
\begin{circuitikz}[scale=0.5]
  \ctikzset{european resistors}
  \ctikzset{cute inductors}
  \draw (0,6) to[american voltage source, l=$V_1$] (0,0);
  \draw (0,6) to[R=$R_1$, a=\SI{10}{\kilo\ohm}] (8,6);
  \draw (8,6) to[C=$C_1$, a=\SI{100}{\nano\farad}] (8,0);
  \draw (0,0) -- (8,0);
  \node[above] at (0,6) {in};
  \node[above] at (8,6) {out};
  \draw (4,0) node[ground]{};
  \draw (4,0) node[circ]{};
\end{circuitikz}
```

Readable, editable, and diffable — which is the point.

## Development

```sh
git clone https://github.com/PeterJones7/spice2tikz.git
cd spice2tikz
python -m pip install -e ".[dev]"
```

```sh
ruff check .            # lint
ruff format --diff .    # formatting
mypy                    # type-check src/ in strict mode
pytest                  # the test suite
pytest --update-golden  # regenerate tests/golden/ after an intended change
```

Golden-file tests are the backbone: inputs live in `tests/corpus/`, expected
output in `tests/golden/`, compared byte for byte. `tests/test_compile.py`
compiles every standalone golden with `latexmk`, skipping itself when no LaTeX
toolchain is installed and always running in CI inside a TeX Live container.

A golden diff proves the output *changed*, not that it is *right*, so after
regenerating goldens, render them and look:

```sh
python tools/render_goldens.py
```

## Documentation

| | |
|---|---|
| [`docs/USAGE.md`](docs/USAGE.md) | every workflow and option, with a worked tweak |
| [`docs/EMITTER.md`](docs/EMITTER.md) | emission rules, style options, how to add a symbol |
| [`docs/LAYOUT.md`](docs/LAYOUT.md) | how placement works, measured against human layouts, and what it is bad at |
| [`docs/SPEC_IR.md`](docs/SPEC_IR.md) | the two intermediate representations |
| [`docs/DESIGN.md`](docs/DESIGN.md) | motivation, architecture, the settled design decisions |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | dev setup, the golden workflow, how to extend it |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | what is built, and what is planned |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | append-only log of the small calls made along the way |

## Contributing

Bug reports are most useful with the input file attached — the issue form asks
for it, because nearly every bug here is a circuit the tool has not seen.
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) covers dev setup, the golden-file
workflow, and how to add a symbol, a SPICE dialect or a whole importer. By
taking part you agree to [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Status

**Alpha.** The whole pipeline works and is tested end to end; the CLI and the
JSON formats may still change between minor releases while the major version is
`0`. See [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
