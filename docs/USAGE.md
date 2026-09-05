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

Writing `.tex` needs nothing more. If you want spice2tikz to hand you a **PDF,
PNG or SVG** directly (`-o figure.png`), it also needs a LaTeX toolchain on
your `PATH` — `latexmk` or `pdflatex` — and, for the images, a converter. On
Debian or Ubuntu:

```sh
sudo apt install latexmk texlive-latex-extra texlive-pictures texlive-science poppler-utils
```

`poppler-utils` provides `pdftoppm` and `pdftocairo`, which cover both PNG and
SVG. Ghostscript, ImageMagick, `dvisvgm`, `mutool` and Inkscape are used as
fallbacks if they are what you have.

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
| `-o FILE` | write to a file; the extension picks the format |
| `--from {spice,asc,netlist-ir,schematic-ir}` | force the input format |
| `--standalone` | wrap the output in a compilable document |
| `--dpi N` | raster resolution for PNG (default 150) |
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

**Output format** comes from the extension you give `-o`:

| `-o` | you get |
|---|---|
| `figure.tex` | CircuiTikZ source (a snippet, or a document with `--standalone`) |
| `figure.pdf` | a cropped PDF |
| `figure.png` | a cropped bitmap, at `--dpi` |
| `figure.svg` | a cropped vector image |

`.pdf`, `.png` and `.svg` are *rendered derivatives*: spice2tikz emits the same
CircuiTikZ it always does, compiles it, and converts the result, so they need a
LaTeX toolchain (see §1). `--standalone` is implied for them — a snippet has no
preamble and cannot be compiled on its own. Anything else is refused by name:

```
spice2tikz: cannot tell what to write from the extension '.jpeg';
use one of .tex, .pdf, .png, .svg
```

With no `-o`, the LaTeX goes to stdout, which carries the generated source and
nothing else; every diagnostic goes to stderr. A shell redirect therefore
produces exactly the bytes `-o figure.tex` would write, with LF line endings on
every platform.

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

`--standalone` instead produces a complete document that crops to the drawing.
You rarely need to ask for it, though, because asking for an image implies it:

```sh
spice2tikz amplifier.sp -o amplifier.pdf
spice2tikz amplifier.sp -o amplifier.png --dpi 300
spice2tikz amplifier.sp -o amplifier.svg
```

Each of those emits CircuiTikZ, compiles it with `latexmk` or `pdflatex`, and —
for PNG and SVG — converts the PDF with whichever of `pdftoppm`, `pdftocairo`,
`gs`, `magick`, `dvisvgm`, `mutool` or `inkscape` is installed and able. Every
intermediate lives in a temporary directory that is removed afterwards, so a
failed compile leaves nothing behind but the message. If no suitable tool is
found, spice2tikz says which ones it looked for; write `.tex` and compile it
yourself:

```sh
spice2tikz amplifier.sp --standalone -o amplifier.tex
latexmk -pdf amplifier.tex
```

Rendering is deterministic too: spice2tikz pins `SOURCE_DATE_EPOCH`, so the
same input gives the same PDF bytes rather than a new timestamp every run. That
holds within one toolchain — a PDF records the pdfTeX version that made it, and
the images inherit whatever the local converter does — so the thing worth
committing is still the `.tex`, with images rendered in your build.

Because conversion is deterministic, generated `.tex` files can be committed
and will diff cleanly: the same input always produces the same bytes, with no
timestamps anywhere.

---

## 5. Telling the drawing what the netlist cannot

SPICE has no way to say "draw this one as an op amp" or "leave the value off
that resistor" — and it should not, because those are not facts about the
circuit. So spice2tikz reads them from an inline `;` comment, which every
simulator ignores:

```spice
R1 in out 10k                          ; labels=ref,value
.subckt LM741 PLUS MINUS OUT VCC VEE   ; symbol=opamp
```

The form is `key=value`, separated by spaces, as many as you like. **A key the
tool does not know is not an error** — it is carried through the IR and left
for something else to read — so a deck annotated for a later version still
converts today. Note that a value cannot contain a space: `labels=ref, value`
loses the second half, and says so.

### `labels=` — what text a component shows

| written | drawn |
|---|---|
| `labels=ref` | `R1` |
| `labels=value` | `10k` |
| `labels=ref,value` | both |
| `labels=none` | neither |
| *(nothing)* | the default: both, for anything that has a value |

```spice
R1 in out 10k ; labels=value    * just the value, for a figure that names it
C1 out 0 100n ; labels=none     * a decoupling cap nobody needs to read
Q1 c b e bc547 ; labels=value   * the part number, not "Q1"
```

A device's "value" is its value if it has one, and otherwise its model or
subcircuit name — which is what a reader would call it. A transistor shows no
value unless asked, exactly as before.

### Sources show what they do

Nothing to write for this one: a source that is a *stimulus* gets a symbol
that says so, from its own SPICE specification.

| card | symbol |
|---|---|
| `V1 in 0 DC 5` | the usual `+`/`-` circle |
| `V1 in 0 SIN(0 1 1k)` | a sine wave |
| `V1 in 0 AC 1` | a sine wave — that is what an AC analysis drives with |
| `V1 in 0 PULSE(0 5 0 1n 1n 1u 2u)` | a square wave |
| `V1 in 0 DC 5 AC 1` | the usual circle: a bias with a small signal on it is a supply, not a stimulus |
| `I1 in 0 SIN(0 1m 1k)` | a sine wave |

Three gaps, all circuitikz's: there is no square *current* source, and no shape
for an exponential or piecewise-linear source. Those keep the plain symbol —
their waveform is three numbers and belongs in the caption. See
`tests/corpus/spice/source_types.sp` for one of each.

### `symbol=` — draw a subcircuit as a real symbol

Put it on the `.subckt` card, and every instance is drawn that way:

```spice
.subckt LM741 PLUS MINUS OUT VCC VEE ; symbol=opamp
.ends
X1 0 inv out vcc vee LM741
```

`opamp` is the one symbol implemented. **Nothing is guessed from the name** —
`LM741` is an op amp and `LM317` is a regulator, and no list of prefixes stays
right — so a subcircuit without the metadata is drawn as a labelled box, as it
always was.

The ports map onto the symbol **by position**:

| port | terminal |
|---|---|
| 1 | non-inverting input (`+`) |
| 2 | inverting input (`-`) |
| 3 | output |
| 4 | positive supply |
| 5 | negative supply |

so what the ports are *called* does not matter; `PLUS`, `IN+` and `VP` are all
just "port 1". Three ports is an ideal op amp with no supply terminals, and
those are then not drawn. The port names are never printed beside the symbol —
the triangle carries its own `+` and `-` markings — but they are kept in the
IR, so a dumped layout still says which net reached which port.

An instance may override its definition:

```spice
X2 0 inv out vcc vee LM741 ; symbol=none    * this one as a box
```

If a request cannot be honoured — an unknown symbol name, or a port count that
does not fit — spice2tikz says so and falls back to the box.

---

## 6. A worked example, with a tweak

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
spice2tikz amp.schematic.json -o amp.pdf
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

## 7. When something goes wrong

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

**A `labels=` or `symbol=` request did nothing.** Look at the warnings: a
misspelling (`labels=refs`), a value with a space in it (`labels=ref, value`),
an unknown symbol, or a subcircuit whose port count the symbol cannot take are
all reported, and the defaults are used instead. A key the tool has never
heard of is silent by design — that is what makes the mechanism extensible.

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

**"no LaTeX toolchain found"**, or **"no PDF-to-PNG converter found"** — you
asked for `-o` with a `.pdf`, `.png` or `.svg` extension on a machine without
the tools to produce one. The message lists what was looked for; see §1 for
what to install. Writing `.tex` never needs them.

**"dvisvgm failed: ... Ghostscript version 10.02.1 is not supported"**, or a
similar complaint from a converter that *is* installed. Being installed is not
the same as being able: dvisvgm reads PDF only with Ghostscript older than
10.01 or mutool alongside it, and ImageMagick's default policy refuses PDF
outright. spice2tikz tries every installed converter in turn and only gives up
when they all fail, so the fix is to install one that works — `poppler-utils`
is the reliable choice for both PNG and SVG.
