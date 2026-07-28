# CircuiTikZ Notes for spice2tikz

Source:

- CircuiTikZ manual v1.8.5 (2026-02-04)

Purpose:

- Minimal reference for emitter implementation.
- Only records information actually needed by spice2tikz.
- If information is not present here, consult the full manual.

There is the possibility of errors in this document as well as the code that is being tested.

---

# Package basics

Load package:

```latex
\usepackage{circuitikz}
```

CircuiTikZ is built on TikZ.

A minimal resistor example is:

```latex
\tikz \draw (0,0) to[R=$R_1$] (2,0);
```

---

# Component categories

CircuiTikZ has two main component types.

## Path-style components

Used inside:

```latex
to[...]
```

Example:

```latex
\draw (0,0) to[R] (2,0);
```

Typical spice2tikz use:

- resistors
- capacitors
- inductors
- diodes
- sources
- wires (`short`)

## Node-style components

Used inside:

```latex
node[...]
```

Example:

```latex
\node[npn] {};
```

Typical spice2tikz use:

- npn
- pnp
- nmos
- pmos
- op amp
- ground
- vcc
- vee

---

# Labels

## Path labels

Basic label:

```latex
to[R, l=$R_1$]
```

Opposite side:

```latex
to[R, l_=$R_1$]
```

The manual consistently uses:

```latex
l=
l_=
```

for component labels.

## Current labels

```latex
to[R, i=$i_1$]
```

Examples in the manual also use variants such as:

```latex
i>_
i^<
```

to change placement and direction.

## Voltage labels

```latex
to[V, v=$V_1$]
```

---

# Label orientation options

Package options:

```text
straightlabels
rotatelabels
smartlabels
```

Default:

```text
smartlabels
```

---

# Path-style orientation

For path-style components:

Use:

```latex
mirror
invert
```

Examples:

```latex
to[R, mirror]
to[R, invert]
to[R, mirror, invert]
```

Do not use:

```latex
xscale=-1
yscale=-1
```

inside path-style components.

Manual guidance:

> never use xscale=-1 nor yscale=-1

for path-style components.

---

# Node-style orientation

For node-style components:

Use:

```latex
xscale=-1
yscale=-1
rotate=<angle>
```

Example:

```latex
\node[op amp, xscale=-1] {};
```

The manual states that node mirroring uses xscale/yscale.

---

# Common path components

## Resistor

```latex
to[R]
```

Aliases:

```text
R
```

## Capacitor

```latex
to[C]
```

Aliases:

```text
C
```

## Inductor

```latex
to[L]
```

Aliases:

```text
L
```

## Short wire

```latex
to[short]
```

The tutorials routinely use `short` for ordinary wires.

---

# Common node components

## Ground

```latex
node[ground] {}
```

Other ground symbols exist, but `ground` is the standard symbol.

## Positive supply

```latex
node[vcc] {}
```

## Negative supply

```latex
node[vee] {}
```

## NPN transistor

```latex
node[npn] {}
```

Named transistor anchors:

```text
B
C
E
```

## PNP transistor

```latex
node[pnp] {}
```

Named transistor anchors:

```text
B
C
E
```

## NMOS transistor

```latex
node[nmos] {}
```

Named anchors:

```text
G
D
S
```

Additional bulk-related anchors are documented.

## PMOS transistor

```latex
node[pmos] {}
```

Named anchors:

```text
G
D
S
```

Additional bulk-related anchors are documented.

## Operational amplifier

```latex
node[op amp] {}
```

Documented anchors:

```text
+
-
out
up
down
```

The tutorials use:

```latex
anchor=+
```

to place the op-amp on an incoming wire.

---

# Anchors

## Path-style anchors

Standard anchors include:

```text
left
right
center
north
south
east
west
```

Path components may expose additional anchors.

A named path component uses:

```latex
to[R, name=R1]
```

then:

```latex
(R1.left)
(R1.right)
```

etc.

## Node-style anchors

Node components expose:

```text
north
south
east
west
center
```

plus component-specific anchors.

Examples:

```text
npn:
    B
    C
    E

nmos:
    G
    D
    S

op amp:
    +
    -
    out
    up
    down
```

---

# Visual style defaults relevant to spice2tikz

Package defaults include:

```text
europeanvoltages
europeancurrents
americanresistors
cuteinductors
smartlabels
```

The package supports package-level styles:

```text
american
european
```

---

# Scaling rules

Path-style:

```text
mirror
invert
```

for orientation.

Node-style:

```text
xscale
yscale
rotate
```

for orientation.

The manual warns about global negative scaling and some rotation/scaling combinations.

---

# Emitter rules derived directly from the manual

1. Emit bipoles as path-style components using `to[...]`.
2. Emit transistors and op-amps as node-style components.
3. Use `mirror` / `invert` for path-style orientation.
4. Use `xscale=-1` / `yscale=-1` for node-style orientation.
5. Use `l=` and `l_=` for ordinary component labels.
6. Use documented anchors only.
7. Use `short` for ordinary wire segments when a CircuiTikZ path component is required.

---

# Findings added during roadmap 2.3

Verified against the manual (v1.8.5) and by compiling/rendering with
circuitikz 1.4.6. Each of these corrected a wrong assumption in the emitter.

## Package defaults: resistors are AMERICAN by default

Manual §"Loading the package with no options is equivalent to":

```text
nofetsolderdot, europeancurrents, europeanvoltages, americanports,
americanresistors, cuteinductors, europeangfsurgearrester, nosiunitx,
noarrowmos, smartlabels, nocompatibility, centertransistorstext
```

So `americanresistors` (zig-zag) is the **default**, not European.

Style names (note the space):

```latex
\ctikzset{american resistors}
\ctikzset{european resistors}
```

Consequence for spice2tikz: SPEC_IR defaults `resistor_variant` to
`european` (D11), so the emitter must state the variant **explicitly and
always** — leaving it implicit renders the wrong symbol.

## There is no American/European capacitor

CircuiTikZ has **no** `american capacitors` / `european capacitors` key.
The capacitor family is a set of distinct bipoles:

```text
capacitor        C     plain (two straight plates)
curved capacitor cC    "Curved (polarized) capacitor"
ecapacitor       eC    electrolytic (aliases: elko)
polar capacitor  pC
variable capacitor vC
```

Only geometry keys exist: `capacitors/width` (default 0.2),
`capacitors/height` (default 0.6).

Consequence: SPEC_IR's `capacitor_variant` has no faithful translation.
`cC` is *polarized*, so using it for "american" would change the meaning of
the component, not its style. The emitter therefore always emits `C`.

## Labels vs annotations, and the `_` / `^` suffixes

Manual §5.1.1:

> When drawing a component left-to-right, the label `l` is by default above
> the component, and the annotation `a` is by default below it. The position
> of annotations and labels can be adjusted adding the characters `_` or `^`
> to the key.

The manual's own flip idiom pairs the two:

```latex
\draw (0,0) to[R,  l=$R_1$, a=1<\kilo\ohm>]  (2,0);   % label natural side
\draw (0,0) to[R, l_=$R_1$, a^=1<\kilo\ohm>] (2,0);   % both flipped
```

Rendering confirms the asymmetry: `a` and `a_` both land on the side
*opposite* the natural label side; only `a^` crosses to the natural side.
So the non-colliding pairings are `l`+`a` and `l_`+`a^`.

## The `to[<bipole>=text]` shorthand means different things

Manual §5.1.1:

> For passive components, you can use `component type =text` as a shortcut
> for `component type, l=text`. [...] Notice though that in active component
> (sources of either voltage or current) the shortcut will set the voltage
> (v) or current (i) property.

Rendering confirms `to[vsource=$V_1$]` draws a voltage *arrow* labelled
`$V_1$`, not a component name. Use explicit `l=` for every source.

## Transistor anchors (manual §4.15.9)

```text
nmos, pmos, nfet, nigfete/d, pfet, pigfete/d:  base gate source drain
                                               (abbrev. B G S D), plus bulk
njfet, pjfet:                                  gate source drain (G S D)
npn, pnp, nigbt, pigbt:                        base emitter collector (B E C)
```

The bulk *terminal* is only drawn when the `bulk` key is given to the node
(`node[nmos, bulk]{}`); the `bulk` anchor exists either way.

## Node shapes do not scale with the environment `scale`

Measured anchor offsets for `node[nmos] at (0,0)` (no scaling):

```text
D      ( 0.000,  0.770) cm      north  (-0.490,  0.770)
G      (-0.980,  0.000) cm      south  (-0.490, -0.770)
S      ( 0.000, -0.770) cm      east   ( 0.000,  0.000)
bulk   ( 0.000,  0.000) cm      west   (-0.980,  0.000)
```

Two things follow:

- The node origin is on the **right edge** of the MOS body (`east` == origin);
  the body extends to the left only.
- Terminals sit at absolute distances (0.77 cm, 0.98 cm) that do not move
  with `scale=`, so at the default 0.5 cm grid pitch they land on
  non-integer grid coordinates (1.54 and 1.96 units).

Consequence: an integer-grid IR can never declare pin positions that
coincide exactly with the rendered terminals. The emitter names the node and
draws a short orthogonal lead from each documented anchor to the declared
pin, e.g.

```latex
\node[nmos] (s2t1) at (10,6) {};
\draw (s2t1.D) |- (10,8);
\draw (s2t1.G) -| (8,6);
```

Anchors are resolved after the node's own `rotate`/`xscale`, so leads follow
the component through all four rotations and the mirror.

## standalone cropping

Use:

```latex
\documentclass[border=2pt]{standalone}
\usepackage{circuitikz}
```

Adding standalone's `tikz` class option **defeats the cropping** when
circuitikz is loaded separately: the result is a full letter page rather
than a tight bounding box.

## Bipole names checked to compile

```text
R  C  L  D  vsource  isource  cvsource  cisource  switch  tline  generic  cC
```

`generic` is a real bipole (a plain rectangle) and is the placeholder for
kinds with no dedicated symbol. Names that do **not** exist, despite looking
plausible: `vcontrolledsource`, `cccs`, `twoport` as a `style@to@style`
alias (`twoport` does exist as a bipole), `american capacitors`.