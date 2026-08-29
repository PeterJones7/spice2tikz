<!-- FILE: docs/USAGE.md -->

# spice2tikz — usage

Everything the command-line tool does, in the order you are likely to need it.
For what the generated LaTeX looks like and how to restyle it, see
`docs/EMITTER.md`; for how automatic placement decides anything, see
`docs/LAYOUT.md`.

---

## 1. Install

Python ≥ 3.10 and nothing else — the package has no runtime dependencies.

It is not on PyPI yet, so install it from the repository:

```sh
python -m pip install git+https://github.com/PeterJones7/spice2tikz
```

or, for an isolated command without touching your Python environment:

```sh
pipx install git+https://github.com/PeterJones7/spice2tikz
```

Once the first release is published, `pip install spice2tikz` and
`pipx install spice2tikz` will work as well.

From a clone, for development:

```sh
git clone https://github.com/PeterJones7/spice2tikz.git
cd spice2tikz
python -m pip install -e ".[dev]"
```

Check it:

```sh
spice2tikz --version
```

The generated LaTeX needs `circuitikz` and, if you keep siunitx value labels,
`siunitx`. Both ship with TeX Live and MiKTeX.

---

## 2. The three workflows

### 2.1 An LTspice schematic → LaTeX

`.asc` files already contain a layout that a person made, so this path
preserves your geometry exactly. It is the highest-fidelity route and needs no
automatic placement at all.

```sh
spice2tikz amplifier.asc > amplifier.tex
```

### 2.2 A SPICE netlist → LaTeX

A netlist has no geometry, so the layout engine invents one.

```sh
spice2tikz amplifier.sp > amplifier.tex
```

Expect to be pleased on small circuits and to want a few nudges on larger ones.
That is what §2.3 is for.

### 2.3 Convert, tweak, re-emit

The Schematic IR is a documented, hand-editable JSON format
(`docs/SPEC_IR.md` §2), and dumping it is a first-class feature rather than a
debugging aid:

```sh
spice2tikz amplifier.sp --dump-layout amplifier.schematic.json
$EDITOR amplifier.schematic.json
spice2tikz amplifier.schematic.json > amplifier.tex
```

Re-emitting an unedited dump reproduces the original output byte for byte, so
every difference in the `.tex` is a difference you made. The validator runs on
the edited file, so mistakes are reported rather than drawn.

---

## 3. Command reference

```
spice2tikz INPUT [-o OUTPUT] [options]
```

| option | effect |
|---|---|
| `-o FILE` | write the LaTeX to a file instead of stdout |
| `--from {spice,asc,netlist-ir,schematic-ir}` | force the input format |
| `--standalone` | wrap the output in a compilable document |
| `--dump-netlist FILE` | also write the Netlist IR as JSON |
| `--dump-layout FILE` | also write the Schematic IR as JSON |
| `--style KEY=VALUE` | override one style default; repeatable |
| `--config FILE` | TOML file whose `[style]` table supplies defaults |
| `-q` | errors only |
| `-v` | progress, a document summary, and layout metrics |
| `--version` | print the version and exit |

**Input format** is deduced from the extension — `.sp`, `.cir`, `.net` are
SPICE; `.asc` is LTspice; `.json` is sniffed from its `ir` field — or forced
with `--from`.

**Output** goes to stdout, which carries the generated LaTeX and nothing else;
every diagnostic goes to stderr. A shell redirect therefore produces exactly
the bytes `-o` would write, with LF line endings on every platform.

**Exit codes**: `0` ok, `1` input parse error, `2` validation error, `3`
internal error. Validation *warnings* are reported and conversion continues;
validation *errors* suppress emission entirely, because a partly-wrong
schematic is worse than a clear failure.

Style options are documented in `docs/EMITTER.md` §3. Briefly:

```sh
spice2tikz circuit.sp --style resistor_variant=american
spice2tikz circuit.sp --style label_refs=false --style siunitx=false
spice2tikz circuit.sp --config house-style.toml
```

```toml
# house-style.toml
[style]
resistor_variant = "american"
inductor_variant = "european"
extra_preamble = ['\usepackage{amsmath}']
```

---

## 4. Using the output

By default you get a **snippet** — a bare `circuitikz` environment — which is
what you want inside a document:

```latex
\documentclass{article}
\usepackage{circuitikz}
\usepackage{siunitx}
\begin{document}
\begin{figure}
  \centering
  \input{amplifier.tex}
  \caption{The amplifier, converted from \texttt{amplifier.sp}.}
\end{figure}
\end{document}
```

`--standalone` instead produces a complete document that crops to the drawing,
which is what you want for a standalone PDF or PNG:

```sh
spice2tikz amplifier.sp --standalone -o amplifier.tex
latexmk -pdf amplifier.tex
```

Because conversion is deterministic, generated `.tex` files can be committed
and will diff cleanly: the same input always produces the same bytes, with no
timestamps anywhere.

---

## 5. A worked example, with a tweak

Start from a netlist:

```spice
* Common-source amplifier
VDD vdd 0 DC 5
V1 in 0 DC 0 AC 1 SIN(0 10m 1k)
RD vdd out 4.7k
M1 out in 0 0 NMOSMOD L=1u W=10u
.model NMOSMOD NMOS (VTO=0.7 KP=100u)
.tran 10u 5m
.end
```

Convert it and look at what the engine decided:

```sh
$ spice2tikz common_source_amp.sp -v -o amp.tex
spice2tikz: reading common_source_amp.sp as spice
spice2tikz: netlist IR: 4 component(s), 4 net(s), 0 subcircuit definition(s)
spice2tikz: schematic IR: 1 sheet(s), 16 element(s)
spice2tikz: layout: 4 component(s), 5 wire(s), 0 crossing(s), wire length 39, bbox area 192, alignment 0.91
spice2tikz: common_source_amp.sp: 0 error(s), 0 warning(s)
```

Zero crossings and every component aligned: nothing to fix. Now suppose you
want the drain resistor labelled `$R_D$` rather than the upright `RD` the
refdes derives, and the supply source moved out of the way. Dump the layout:

```sh
spice2tikz common_source_amp.sp --dump-layout amp.schematic.json
```

Find the component in the JSON:

```json
{ "type": "component", "mode": "path", "ref": "RD", "kind": "resistor",
  "a": [16, 12], "b": [16, 8],
  "value_label": { "text": "\\SI{4.7}{\\kilo\\ohm}" } }
```

and add an explicit label, which is emitted verbatim:

```json
{ "type": "component", "mode": "path", "ref": "RD", "kind": "resistor",
  "a": [16, 12], "b": [16, 8],
  "label": { "text": "$R_D$", "side": "left" },
  "value_label": { "text": "\\SI{4.7}{\\kilo\\ohm}" } }
```

Re-emit:

```sh
spice2tikz amp.schematic.json --standalone -o amp.tex
latexmk -pdf amp.tex
```

The only change in the output is the one you made. To move something, edit its
coordinates: a path component has `a` and `b`; a node component has `at` plus a
`pins` map that must stay consistent with its symbol geometry — the validator
will tell you, with the position it expected, if it does not.

Useful edits, in rough order of how often they are wanted:

| want | edit |
|---|---|
| a different label | add `"label": {"text": "..."}` — raw LaTeX, verbatim |
| no label at all | `"label": {"text": "-"}` |
| label on the other side | `"label": {"side": "left"}` (or `right`/`above`/`below`) |
| move a two-terminal part | its `a` and `b` |
| move a device | its `at` **and** every entry in `pins` |
| a longer wire | add points to the `points` list |
| a colour or line style | `"style": {"color": "red", "circuitikz_options": "thick"}` |

---

## 6. When something goes wrong

**"cannot deduce the input format"** — the extension is not one the tool knows.
Pass `--from spice`, `--from asc`, `--from netlist-ir` or
`--from schematic-ir`.

**Warnings about unknown cards.** Unrecognised SPICE cards become `generic`
components drawn as labelled boxes, and the tool says so. A partial schematic
beats none; if the card matters, it needs a kind in `docs/SPEC_IR.md` §1.

**"body terminal … is not tied to the channel"** — a MOS bulk or bipolar
substrate is on its own net. It is declared but not drawn, because a wire out
of the middle of a device crosses both its other terminals. Wire it by hand in
the dumped layout if the connection matters to the reader.

**Validation errors, exit 2.** The itemised report names the element and the
coordinate. In a hand-edited file this is nearly always a node component whose
`pins` no longer match its `at`, or a wire whose end no longer lands on
anything.

**The layout is ugly.** Read `docs/LAYOUT.md` §5 for what the engine is known
to be bad at, then use the dump-and-edit workflow. If a whole *class* of
circuit comes out wrong, that is a bug worth reporting with the netlist
attached — see `docs/CONTRIBUTING.md`.

**The LaTeX does not compile.** The generated snippet needs `circuitikz`;
value labels need `siunitx`. If a golden-quality file still fails, that is a
bug: every file this tool ships is compiled in CI against TeX Live.
