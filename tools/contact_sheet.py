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

    @property
    def anchor(self) -> str:
        """Return a stable id, also used as the key for review marks."""
        keep = [c if c.isalnum() else "-" for c in self.title.lower()]
        return "".join(keep).strip("-").replace("---", "-").replace("--", "-")


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
  /* Drafting materials: a cool vellum ground, drafting-pen teal, and a plate
     that stays light in BOTH themes -- the figures are transparent PNGs with
     black ink, so a dark plate would erase them. A contact sheet is frames on
     a light table; here that is a constraint as much as a concept. */
  --ground: #f1f4f3;
  --panel: #ffffff;
  --plate: #fbfbfa;
  --ink: #16191c;
  --muted: #5a6570;
  --rule: #d5dbd9;
  --accent: #0b6b63;
  --accent-soft: #e2efec;
  --alert: #a4342b;
  --done: #0b6b63;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #12161a;
    --panel: #1a2027;
    --plate: #f4f5f3;
    --ink: #e6eae8;
    --muted: #93a0a6;
    --rule: #2b343d;
    --accent: #5fbfb2;
    --accent-soft: #17302e;
    --alert: #e2867c;
    --done: #5fbfb2;
  }
}
:root[data-theme="dark"] {
  --ground: #12161a;
  --panel: #1a2027;
  --plate: #f4f5f3;
  --ink: #e6eae8;
  --muted: #93a0a6;
  --rule: #2b343d;
  --accent: #5fbfb2;
  --accent-soft: #17302e;
  --alert: #e2867c;
  --done: #5fbfb2;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  * { transition: none !important; }
}
body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: Archivo, ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.55;
}
.mono {
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}

.shell { display: grid; grid-template-columns: 1fr; gap: 0; }
@media (min-width: 1080px) {
  .shell {
    grid-template-columns: 15rem minmax(0, 1fr);
    max-width: 74rem;
    margin: 0 auto;
    gap: 2.5rem;
    padding: 0 1.5rem;
  }
}

/* --- index rail ------------------------------------------------------- */
.rail { padding: 1.5rem 1.25rem 0; }
@media (min-width: 1080px) {
  .rail { position: sticky; top: 0; align-self: start; height: 100vh;
          overflow-y: auto; padding: 3rem 0 2rem; }
}
.rail h2 {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .14em;
  color: var(--muted); margin: 0 0 .75rem; font-weight: 600;
}
.rail ol { list-style: none; margin: 0 0 1.75rem; padding: 0;
           display: flex; flex-direction: column; gap: .1rem; }
.rail a {
  display: flex; justify-content: space-between; gap: .75rem;
  padding: .3rem .5rem; border-radius: 4px; text-decoration: none;
  color: var(--ink); font-size: .88rem; transition: background .12s ease;
}
.rail a:hover { background: var(--accent-soft); color: var(--accent); }
.rail a:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.rail .count { color: var(--muted); font-size: .78rem;
               font-variant-numeric: tabular-nums; }
.progress {
  border-top: 1px solid var(--rule); padding-top: .9rem;
  font-size: .82rem; color: var(--muted);
}
.progress strong { color: var(--accent); font-variant-numeric: tabular-nums; }
.progress button {
  margin-top: .6rem; font: inherit; font-size: .78rem; cursor: pointer;
  background: none; border: 1px solid var(--rule); color: var(--muted);
  border-radius: 4px; padding: .25rem .6rem;
}
.progress button:hover { border-color: var(--accent); color: var(--accent); }

/* --- main ------------------------------------------------------------- */
main { padding: 1.5rem 1.25rem 6rem; min-width: 0; }
@media (min-width: 1080px) { main { padding: 3rem 0 8rem; } }

.masthead { border-bottom: 2px solid var(--ink); padding-bottom: 1.25rem;
            margin-bottom: 2.5rem; }
.eyebrow {
  font-size: .7rem; text-transform: uppercase; letter-spacing: .16em;
  color: var(--accent); margin: 0 0 .5rem; font-weight: 600;
}
.masthead h1 {
  font-size: clamp(1.7rem, 4vw, 2.4rem); line-height: 1.1; margin: 0 0 .7rem;
  font-weight: 700; letter-spacing: -0.02em; text-wrap: balance;
}
.masthead p { margin: 0; color: var(--muted); max-width: 62ch; }
.tally { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-top: 1.25rem; }
.tally div { display: flex; flex-direction: column; }
.tally .n {
  font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums;
  line-height: 1;
}
.tally .k {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .12em;
  color: var(--muted); margin-top: .3rem;
}
.tally .bad .n { color: var(--alert); }

section { margin-bottom: 3.5rem; scroll-margin-top: 1.5rem; }
section > h2 {
  font-size: 1.15rem; font-weight: 700; margin: 0 0 .4rem;
  letter-spacing: -0.01em; text-wrap: balance;
}
section > .blurb { margin: 0 0 1.25rem; color: var(--muted); max-width: 68ch; }

.note {
  border-left: 3px solid var(--accent); background: var(--panel);
  padding: .9rem 1.1rem; margin-bottom: 1.75rem; border-radius: 0 6px 6px 0;
}
.note h3 {
  margin: 0 0 .5rem; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .14em; color: var(--accent); font-weight: 600;
}
.note ul { margin: 0; padding-left: 1.1rem; display: flex;
           flex-direction: column; gap: .3rem; }
.note li { font-size: .92rem; }

/* --- a frame ---------------------------------------------------------- */
figure {
  margin: 0 0 1.5rem; background: var(--panel);
  border: 1px solid var(--rule); border-radius: 6px; overflow: hidden;
}
figure.reviewed { border-color: var(--done); }
figcaption {
  display: flex; align-items: center; gap: .85rem; flex-wrap: wrap;
  padding: .6rem .9rem; border-bottom: 1px solid var(--rule);
}
.idx {
  font-size: .72rem; color: var(--muted); font-variant-numeric: tabular-nums;
  border: 1px solid var(--rule); border-radius: 3px; padding: .05rem .4rem;
}
figure.reviewed .idx { border-color: var(--done); color: var(--done); }
.title { font-weight: 600; font-size: .95rem; }
.path { color: var(--muted); font-size: .78rem; margin-left: auto; }
.check {
  display: inline-flex; align-items: center; gap: .4rem; cursor: pointer;
  font-size: .76rem; color: var(--muted); user-select: none;
  text-transform: uppercase; letter-spacing: .08em;
}
.check input { accent-color: var(--accent); width: 1rem; height: 1rem;
               cursor: pointer; }
.check input:focus-visible { outline: 2px solid var(--accent);
                             outline-offset: 2px; }
figure.reviewed .check { color: var(--done); }
.plate {
  background: var(--plate); padding: 1.5rem 1.25rem;
  display: flex; justify-content: center; overflow-x: auto;
}
.plate img { max-width: 100%; height: auto; display: block; }
.failed {
  padding: 1.1rem; color: var(--alert); font-size: .84rem;
  border-left: 3px solid var(--alert);
}
footer {
  border-top: 1px solid var(--rule); padding-top: 1.25rem;
  color: var(--muted); font-size: .85rem; max-width: 68ch;
}
footer code { font-size: .95em; }
"""

PAGE_SCRIPT = """
(function () {
  var KEY = "s2t-review-v1";
  var seen = {};
  try { seen = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; }
  catch (e) { seen = {}; }

  var boxes = Array.prototype.slice.call(
    document.querySelectorAll("input[data-figure]")
  );
  var readout = document.getElementById("done-count");
  var total = boxes.length;

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(seen)); }
    catch (e) { /* private browsing: marks just do not persist */ }
  }
  function paint() {
    var done = 0;
    boxes.forEach(function (box) {
      var on = !!seen[box.dataset.figure];
      box.checked = on;
      box.closest("figure").classList.toggle("reviewed", on);
      if (on) { done += 1; }
    });
    if (readout) { readout.textContent = done + " / " + total; }
  }
  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      seen[box.dataset.figure] = box.checked;
      save();
      paint();
    });
  });
  var reset = document.getElementById("reset-review");
  if (reset) {
    reset.addEventListener("click", function () {
      seen = {};
      save();
      paint();
    });
  }
  paint();
})();
"""


def build_page(groups: list[Group], dpi: int) -> str:
    """Return the whole contact sheet as one self-contained HTML page."""
    total = sum(len(group.figures) for group in groups)
    failed = sum(
        1 for group in groups for figure in group.figures if figure.png is None
    )
    parts: list[str] = [
        # Without this the page is decoded as Latin-1 by anything that does
        # not send a charset header, and every dash in the copy becomes
        # mojibake.
        '<meta charset="utf-8">',
        "<title>spice2tikz Review Sheet</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=Archivo:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500"
        '&display=swap">',
        f"<style>{PAGE_CSS}</style>",
        '<div class="shell">',
        '<nav class="rail" aria-label="Sections">',
        "<h2>Sections</h2><ol>",
    ]
    for group in groups:
        parts.append(
            f'<li><a href="#{group.anchor}">{html.escape(group.title)}'
            f'<span class="count mono">{len(group.figures)}</span></a></li>'
        )
    parts.append("</ol>")
    parts.append(
        '<div class="progress">Reviewed <strong class="mono" '
        'id="done-count">0 / 0</strong>'
        '<br><button type="button" id="reset-review">Clear marks</button></div>'
    )
    parts.append("</nav><main>")

    parts.append('<div class="masthead">')
    parts.append('<p class="eyebrow mono">Manual visual review</p>')
    parts.append("<h1>Every figure spice2tikz can draw</h1>")
    parts.append(
        "<p>Golden tests prove the output has not changed; compiling proves it "
        "is valid LaTeX. Neither says it is <em>right</em> — a symbol whose "
        "leads double back across its own body passes both. This is the part "
        "that needs eyes.</p>"
    )
    parts.append('<div class="tally">')
    parts.append(
        f'<div><span class="n mono">{total}</span><span class="k">figures</span></div>'
    )
    parts.append(
        f'<div><span class="n mono">{len(groups)}</span>'
        '<span class="k">sections</span></div>'
    )
    parts.append(
        f'<div><span class="n mono">{dpi}</span><span class="k">dpi</span></div>'
    )
    if failed:
        parts.append(
            f'<div class="bad"><span class="n mono">{failed}</span>'
            '<span class="k">failed to render</span></div>'
        )
    parts.append("</div></div>")

    number = 0
    for group in groups:
        parts.append(f'<section id="{group.anchor}">')
        parts.append(f"<h2>{html.escape(group.title)}</h2>")
        parts.append(f'<p class="blurb">{html.escape(group.blurb)}</p>')
        if group.look_for:
            parts.append('<div class="note"><h3>What to look for</h3><ul>')
            for item in group.look_for:
                parts.append(f"<li>{html.escape(item)}</li>")
            parts.append("</ul></div>")
        for figure in group.figures:
            number += 1
            key = f"{group.anchor}/{figure.name}"
            parts.append("<figure>")
            parts.append(
                "<figcaption>"
                f'<span class="idx mono">{number:02d}</span>'
                f'<span class="title">{html.escape(figure.name)}</span>'
                f'<span class="path mono">{html.escape(figure.caption)}</span>'
                f'<label class="check mono"><input type="checkbox" '
                f'data-figure="{html.escape(key)}"> reviewed</label>'
                "</figcaption>"
            )
            if figure.png is not None:
                parts.append(
                    f'<div class="plate"><img src="{embed(figure.png)}" '
                    f'alt="Rendered schematic: {html.escape(figure.name)}"></div>'
                )
            else:
                parts.append(
                    '<div class="failed mono">Did not render — '
                    f"{html.escape(figure.error or 'unknown error')}</div>"
                )
            parts.append("</figure>")
        parts.append("</section>")

    parts.append(
        "<footer>Built by <code>tools/contact_sheet.py</code>. Rebuild it after "
        "any change to the emitter, the symbol library, the importer or the "
        "layout engine, and look at it before accepting a golden diff. Marks are "
        "kept in this browser only.</footer>"
    )
    parts.append("</main></div>")
    parts.append(f"<script>{PAGE_SCRIPT}</script>")
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
