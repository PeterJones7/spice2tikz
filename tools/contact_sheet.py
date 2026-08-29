#!/usr/bin/env python3
"""Render everything this tool can draw, onto one page, for a human to check.

Golden-file tests prove the output has not *changed*. Compiling proves it is
valid LaTeX. Neither says it is *right*: a symbol drawn with its leads doubling
back across the body passes both, and did, twice, until somebody looked at a
picture. This builds the picture — one self-contained HTML page holding every
rendered output, so a full visual review is one scroll rather than thirty file
opens.

It renders three things:

* **reference sheets** generated here and not part of the corpus: every
  built-in symbol in all eight orientations, every path-component kind, and
  every net symbol. These exercise emitter paths the circuit corpus never
  reaches — several bipole names were best-effort guesses (`docs/DECISIONS.md`,
  §2.1/2.3) and nothing else draws them;
* **every standalone golden** under ``tests/golden/`` — the hand-written
  schematics, the LTspice imports, and the automatic layouts;
* the review checklist, inline, so the criteria are in front of the reviewer.

Usage::

    python tools/contact_sheet.py                    # build/contact-sheet.html
    python tools/contact_sheet.py -o sheet.html
    python tools/contact_sheet.py --dpi 200

Requires a LaTeX toolchain with circuitikz and a PDF-to-PNG converter, the same
as ``tools/render_goldens.py``.
"""

from __future__ import annotations

import argparse
import base64
import html
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from spice2tikz.emit.circuitikz import emit_standalone  # noqa: E402
from spice2tikz.netlist_ir import Kind  # noqa: E402
from spice2tikz.schematic_ir import (  # noqa: E402
    Grid,
    Label,
    LabelSpec,
    NetSymbol,
    NodeComponent,
    PathComponent,
    SchematicIR,
    SchematicMeta,
    Sheet,
    Wire,
)
from spice2tikz.symbols import BUILTIN_SYMBOLS, resolve_pins  # noqa: E402

GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
STANDALONE_SUFFIX = ".standalone.tex"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "contact-sheet.html"
TIMEOUT_SECONDS = 180

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_TOOLS = 3

ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)


@dataclass
class Figure:
    """One rendered picture and what a reviewer should know about it."""

    name: str
    caption: str
    png: bytes | None = None
    error: str | None = None


@dataclass
class Group:
    """A titled run of figures."""

    title: str
    blurb: str
    look_for: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


# --- reference sheets --------------------------------------------------------


def _document(elements: list[object], title: str) -> SchematicIR:
    return SchematicIR(
        meta=SchematicMeta(title=title, generator="contact_sheet", grid=Grid()),
        sheets=[Sheet(name="main", elements=list(elements))],  # type: ignore[arg-type]
    )


def symbol_matrix() -> dict[str, SchematicIR]:
    """Return one sheet per built-in symbol, showing all eight orientations.

    This is the sheet that would have caught the p-type geometry bug: a PMOS
    drawn beside an NMOS, both with their terminals wired outward, makes a lead
    that doubles back impossible to miss.
    """
    sheets: dict[str, SchematicIR] = {}
    for name, symbol in BUILTIN_SYMBOLS.items():
        elements: list[object] = []
        for index, mirror in enumerate((False, True)):
            for column, rot in enumerate(ROTATIONS):
                at = (column * 14, -index * 12)
                pins = resolve_pins(symbol, at, rot, mirror)
                elements.append(
                    NodeComponent(
                        ref=f"{name[0].upper()}{column + 1 + index * 4}",
                        kind=Kind.GENERIC,
                        symbol=name,
                        at=at,
                        rot=rot,  # type: ignore[arg-type]
                        mirror=mirror,
                        pins=pins,
                        label=LabelSpec(text="-"),
                    )
                )
                # Wire every terminal outward, so a lead that doubles back over
                # the body shows up against a straight reference line.
                for pin, point in pins.items():
                    delta = (point[0] - at[0], point[1] - at[1])
                    if delta == (0, 0):
                        continue
                    far = (point[0] + delta[0], point[1] + delta[1])
                    elements.append(
                        Wire(net=f"{name}{column}{index}{pin}", points=[point, far])
                    )
                    elements.append(
                        Label(at=far, text=f"\\tiny {pin.upper()}", anchor="center")
                    )
                elements.append(
                    Label(
                        at=(at[0], at[1] - 6),
                        text=f"\\tiny {rot}$^\\circ$"
                        + (", mirrored" if mirror else ""),
                        anchor="center",
                    )
                )
        sheets[f"symbols-{name}"] = _document(elements, name)
    return sheets


PATH_KINDS_TO_SHOW: tuple[Kind, ...] = (
    Kind.RESISTOR,
    Kind.CAPACITOR,
    Kind.INDUCTOR,
    Kind.DIODE,
    Kind.VSOURCE,
    Kind.ISOURCE,
    Kind.VCVS,
    Kind.VCCS,
    Kind.CCVS,
    Kind.CCCS,
    Kind.SWITCH,
    Kind.TLINE,
    Kind.GENERIC,
)
"""Every kind the emitter has a bipole name for.

Several were best-effort guesses that no corpus circuit draws
(``docs/DECISIONS.md``, §2.1 and §2.3), which is exactly why they belong on a
sheet somebody looks at.
"""


def bipole_sheet() -> SchematicIR:
    """Return a sheet with every path-component kind, horizontal and vertical."""
    elements: list[object] = []
    for index, kind in enumerate(PATH_KINDS_TO_SHOW):
        column, row = index % 5, index // 5
        left = (column * 16, -row * 8)
        right = (left[0] + 8, left[1])
        elements.append(
            PathComponent(
                ref=f"X{index + 1}",
                kind=kind,
                a=left,
                b=right,
                label=LabelSpec(text=f"\\tiny\\texttt{{{kind.value}}}"),
                value_label=LabelSpec(text="\\tiny 1\\,k"),
            )
        )
    return _document(elements, "path components")


def net_symbol_sheet() -> SchematicIR:
    """Return a sheet with every net-symbol variant and rotation."""
    elements: list[object] = []
    variants = ("ground", "sground", "vcc", "vee", "tap")
    for row, variant in enumerate(variants):
        for column, rot in enumerate(ROTATIONS):
            at = (column * 12, -row * 8)
            elements.append(
                NetSymbol(
                    net=variant,
                    variant=variant,  # type: ignore[arg-type]
                    at=at,
                    rot=rot,  # type: ignore[arg-type]
                    text=variant if variant == "tap" else None,
                )
            )
            elements.append(
                Label(
                    at=(at[0], at[1] - 4),
                    text=f"\\tiny {variant} {rot}$^\\circ$",
                    anchor="center",
                )
            )
    return _document(elements, "net symbols")


# --- rendering ---------------------------------------------------------------


def find_latex() -> list[str] | None:
    """Return the LaTeX command, or ``None``."""
    if shutil.which("latexmk"):
        return ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error"]
    if shutil.which("pdflatex"):
        return ["pdflatex", "-interaction=nonstopmode", "-halt-on-error"]
    return None


def find_converter() -> str | None:
    """Return a PDF-to-PNG converter command, or ``None``."""
    for command in ("pdftoppm", "pdftocairo", "gs", "gswin64c", "magick"):
        if shutil.which(command):
            return command
    return None


def to_png(pdf: Path, png: Path, converter: str, dpi: int, work: Path) -> str | None:
    """Convert *pdf* to *png*; return an error message or ``None``."""
    if converter in ("pdftoppm", "pdftocairo"):
        prefix = png.with_suffix("")
        args = [converter, "-png", "-r", str(dpi), "-singlefile", str(pdf), str(prefix)]
    elif converter in ("gs", "gswin64c"):
        args = [
            converter,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=pngalpha",
            f"-r{dpi}",
            f"-sOutputFile={png}",
            str(pdf),
        ]
    else:
        args = [converter, "-density", str(dpi), str(pdf), str(png)]
    result = subprocess.run(
        args,
        cwd=work,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0 or not png.exists():
        return (result.stderr or "conversion failed").strip().splitlines()[:1][0]
    return None


def render_tex(
    source: str, latex: list[str], converter: str, dpi: int
) -> tuple[bytes | None, str | None]:
    """Compile standalone LaTeX *source* and return its PNG bytes."""
    with tempfile.TemporaryDirectory(prefix="s2t-sheet-") as tmp:
        work = Path(tmp)
        tex = work / "figure.tex"
        tex.write_text(source, encoding="utf-8", newline="\n")
        result = subprocess.run(
            [*latex, tex.name],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        pdf = work / "figure.pdf"
        if result.returncode != 0 or not pdf.exists():
            log = work / "figure.log"
            message = "compilation failed"
            if log.exists():
                text = log.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if line.startswith("!"):
                        message = line
                        break
            return None, message
        png = work / "figure.png"
        error = to_png(pdf, png, converter, dpi, work)
        if error is not None:
            return None, error
        return png.read_bytes(), None


def golden_groups() -> list[tuple[str, str, list[str], list[Path]]]:
    """Return the golden files, grouped by which half of the pipeline made them."""
    everything = sorted(GOLDEN_DIR.glob(f"**/*{STANDALONE_SUFFIX}"))
    hand = [p for p in everything if p.parent == GOLDEN_DIR]
    asc = [p for p in everything if p.parent.name == "asc"]
    auto = [p for p in everything if p.parent.name == "layout"]
    return [
        (
            "Hand-written Schematic IR",
            "Corpus files written by hand to exercise the emitter. What they "
            "draw is what the IR says, with no importer or layout engine "
            "involved — so anything wrong here is the emitter's fault.",
            [
                "every component present, and the right symbol for its kind",
                "orientation and mirroring correct",
                "leads leaving each device terminal without crossing the body",
                "labels readable and not overlapping anything",
                "wires meeting the intended pins; junction dots where nets meet",
            ],
            hand,
        ),
        (
            "Imported from LTspice (.asc)",
            "Geometry a person chose in LTspice, reproduced. These should look "
            "like the original schematic; the layout engine is not involved.",
            [
                "the arrangement matches what LTspice would show",
                "symbol orientations match the source file's R0/R90/M0/… codes",
                "component values and names carried across",
                "no stray or dangling leads from the pin-offset correction",
            ],
            asc,
        ),
        (
            "Laid out automatically from SPICE",
            "No geometry in the input: placement, routing and junctions are all "
            "invented. Correctness is checked by the readback test; this is "
            "about whether it is *legible*.",
            [
                "signal reads left to right; ground at the bottom, supplies at top",
                "no wire drawn along or through a component body",
                "labels clear of symbols and of each other",
                "crossings and detours reasonable for the circuit",
                "devices turned the conventional way for their polarity",
            ],
            auto,
        ),
    ]


# --- the page ----------------------------------------------------------------


def embed(png: bytes) -> str:
    """Return a ``data:`` URI for *png*."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


PAGE_CSS = """
:root {
  --bg: #f6f5f3; --panel: #ffffff; --ink: #1a1a1a; --muted: #5f5c58;
  --line: #d9d5d0; --accent: #7a3b2e; --ok: #2f6b3f; --bad: #a8322a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14161a; --panel: #1c1f25; --ink: #e8e6e3; --muted: #a09b94;
    --line: #333840; --accent: #d99a7a; --ok: #7fc48f; --bad: #e08a80;
  }
}
:root[data-theme="dark"] {
  --bg: #14161a; --panel: #1c1f25; --ink: #e8e6e3; --muted: #a09b94;
  --line: #333840; --accent: #d99a7a; --ok: #7fc48f; --bad: #e08a80;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
header h1 { font-size: 1.9rem; margin: 0 0 .35rem; letter-spacing: -0.01em; }
header p { color: var(--muted); margin: 0 0 .5rem; max-width: 62ch; }
.meta { font-size: .85rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.toc { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1.5rem 0 0; padding: 0;
       list-style: none; }
.toc a {
  display: inline-block; padding: .3rem .7rem; border: 1px solid var(--line);
  border-radius: 999px; text-decoration: none; color: var(--ink);
  background: var(--panel); font-size: .85rem;
}
.toc a:hover { border-color: var(--accent); color: var(--accent); }
section { margin: 3rem 0 0; }
h2 {
  font-size: 1.25rem; margin: 0 0 .4rem; padding-bottom: .4rem;
  border-bottom: 2px solid var(--accent);
}
.blurb { color: var(--muted); margin: 0 0 1rem; max-width: 72ch; }
.checklist {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: .85rem 1rem .85rem 1rem; margin: 0 0 1.5rem;
}
.checklist h3 {
  margin: 0 0 .5rem; font-size: .8rem; text-transform: uppercase;
  letter-spacing: .07em; color: var(--muted);
}
.checklist ul { margin: 0; padding-left: 1.1rem; }
.checklist li { margin: .2rem 0; font-size: .92rem; }
figure {
  margin: 0 0 1.75rem; background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; overflow: hidden;
}
figcaption {
  display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline;
  padding: .7rem 1rem; border-bottom: 1px solid var(--line);
}
figcaption .name { font-weight: 650; }
figcaption .note {
  color: var(--muted); font-size: .85rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.plate {
  padding: 1.25rem; background: #ffffff; display: flex; justify-content: center;
  overflow-x: auto;
}
.plate img { max-width: 100%; height: auto; display: block; }
.fail { padding: 1rem; color: var(--bad); font-family: ui-monospace, monospace;
        font-size: .85rem; }
footer { margin-top: 4rem; padding-top: 1.25rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .87rem; }
"""


def build_page(groups: list[Group], dpi: int) -> str:
    """Return the whole contact sheet as one self-contained HTML page."""
    total = sum(len(group.figures) for group in groups)
    failed = sum(
        1 for group in groups for figure in group.figures if figure.png is None
    )
    parts: list[str] = [
        "<title>spice2tikz visual review</title>",
        f"<style>{PAGE_CSS}</style>",
        '<div class="wrap">',
        "<header>",
        "<h1>spice2tikz — visual review sheet</h1>",
        "<p>Every picture this tool can produce, on one page. Golden tests prove "
        "the output has not changed and compilation proves it is valid LaTeX; "
        "neither says it is right. This is the part that needs eyes.</p>",
        f'<p class="meta">{total} figures at {dpi} dpi'
        + (f" &middot; <strong>{failed} failed to render</strong>" if failed else "")
        + "</p>",
        "<ul class='toc'>",
    ]
    for group in groups:
        anchor = group.title.lower().replace(" ", "-").replace("(", "").replace(")", "")
        parts.append(
            f'<li><a href="#{anchor}">{html.escape(group.title)} '
            f"({len(group.figures)})</a></li>"
        )
    parts.append("</ul></header>")

    for group in groups:
        anchor = group.title.lower().replace(" ", "-").replace("(", "").replace(")", "")
        parts.append(f'<section id="{anchor}">')
        parts.append(f"<h2>{html.escape(group.title)}</h2>")
        parts.append(f'<p class="blurb">{html.escape(group.blurb)}</p>')
        if group.look_for:
            parts.append('<div class="checklist"><h3>What to look for</h3><ul>')
            for item in group.look_for:
                parts.append(f"<li>{html.escape(item)}</li>")
            parts.append("</ul></div>")
        for figure in group.figures:
            parts.append("<figure>")
            parts.append(
                '<figcaption><span class="name">'
                f"{html.escape(figure.name)}</span>"
                f'<span class="note">{html.escape(figure.caption)}</span>'
                "</figcaption>"
            )
            if figure.png is not None:
                parts.append(
                    f'<div class="plate"><img src="{embed(figure.png)}" '
                    f'alt="Rendered schematic: {html.escape(figure.name)}"></div>'
                )
            else:
                parts.append(
                    f'<div class="fail">FAILED TO RENDER — '
                    f"{html.escape(figure.error or 'unknown error')}</div>"
                )
            parts.append("</figure>")
        parts.append("</section>")

    parts.append(
        "<footer>Generated by <code>tools/contact_sheet.py</code>. "
        "Regenerate after any change that touches the emitter, the symbol "
        "library, the importer or the layout engine, and look before accepting "
        "a golden diff.</footer></div>"
    )
    return "\n".join(parts)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="contact_sheet.py",
        description="Render every figure onto one HTML page for visual review.",
    )
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=150)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build the contact sheet and return a process exit code."""
    args = build_parser().parse_args(argv)
    latex, converter = find_latex(), find_converter()
    if latex is None or converter is None:
        print(
            "contact_sheet: needs a LaTeX toolchain and a PDF-to-PNG converter",
            file=sys.stderr,
        )
        return EXIT_NO_TOOLS
    print(f"contact_sheet: {latex[0]} + {converter} at {args.dpi} dpi")

    groups: list[Group] = []

    reference = Group(
        title="Symbol reference",
        blurb=(
            "Generated here, not from the corpus: every built-in symbol in all "
            "eight orientations with each terminal wired straight outward, then "
            "every path-component kind and every net symbol. These cover "
            "emitter paths no circuit in the corpus reaches."
        ),
        look_for=[
            "each lead leaves its terminal and goes outward — never back across "
            "the body of the device",
            "p-type devices inverted relative to n-type (PMOS source up, PNP "
            "emitter up)",
            "terminal letters land on the right ends of the symbol",
            "all eight orientations distinct, and mirroring flipping left-right",
            "every bipole drawing a real symbol, not a fallback box",
        ],
    )
    sheets = symbol_matrix()
    sheets["bipoles"] = bipole_sheet()
    sheets["net-symbols"] = net_symbol_sheet()
    for name, document in sheets.items():
        print(f"  {name:34}", end="", flush=True)
        png, error = render_tex(emit_standalone(document), latex, converter, args.dpi)
        print("ok" if error is None else f"FAILED: {error}")
        reference.figures.append(
            Figure(name=name, caption="generated reference sheet", png=png, error=error)
        )
    groups.append(reference)

    for title, blurb, look_for, paths in golden_groups():
        group = Group(title=title, blurb=blurb, look_for=look_for)
        for path in paths:
            name = path.relative_to(GOLDEN_DIR).as_posix()[: -len(STANDALONE_SUFFIX)]
            print(f"  {name:34}", end="", flush=True)
            png, error = render_tex(
                path.read_text(encoding="utf-8"), latex, converter, args.dpi
            )
            print("ok" if error is None else f"FAILED: {error}")
            group.figures.append(
                Figure(
                    name=name,
                    caption=str(path.relative_to(REPO_ROOT)),
                    png=png,
                    error=error,
                )
            )
        groups.append(group)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_page(groups, args.dpi), encoding="utf-8", newline="\n")
    failures = sum(1 for g in groups for f in g.figures if f.png is None)
    size = args.output.stat().st_size / 1e6
    print(f"contact_sheet: wrote {args.output} ({size:.1f} MB)")
    if failures:
        print(f"contact_sheet: {failures} figure(s) failed to render", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
