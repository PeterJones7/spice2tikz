#!/usr/bin/env python3
"""Compile the standalone golden ``.tex`` files and render them to PNG.

This is a developer aid for the human-review step of roadmap §2.3: goldens are
compared byte-for-byte by ``pytest``, but only a rendered image shows whether a
schematic is actually *right* — components present, orientation and mirroring
correct, wires joining the intended pins, junction dots where expected.

Usage::

    python tools/render_goldens.py                 # render every golden
    python tools/render_goldens.py rc_lowpass ...  # only the named ones
    python tools/render_goldens.py --dpi 300
    python tools/render_goldens.py --outdir /tmp/review --keep-pdf

Requires a LaTeX toolchain (``latexmk`` or ``pdflatex``) with circuitikz, plus a
PDF-to-PNG converter (``pdftoppm``, ``pdftocairo``, or ImageMagick). Missing
tools are reported and exit code 3 is returned rather than failing obscurely.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
DEFAULT_OUTDIR = REPO_ROOT / "build" / "golden-review"
STANDALONE_SUFFIX = ".standalone.tex"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_INPUT = 2
EXIT_NO_TOOLS = 3

TIMEOUT_SECONDS = 120


def find_latex() -> tuple[str, list[str]] | None:
    """Return the LaTeX command and its per-file arguments, or ``None``."""
    if shutil.which("latexmk"):
        return "latexmk", ["-pdf", "-interaction=nonstopmode", "-halt-on-error"]
    if shutil.which("pdflatex"):
        return "pdflatex", ["-interaction=nonstopmode", "-halt-on-error"]
    return None


def find_converter() -> tuple[str, str] | None:
    """Return ``(command, kind)`` for a PDF→PNG converter, or ``None``."""
    for command, kind in (
        ("pdftoppm", "poppler"),
        ("pdftocairo", "poppler"),
        ("magick", "imagemagick"),
        ("convert", "imagemagick"),
    ):
        if shutil.which(command):
            return command, kind
    return None


def run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run *command* in *cwd*, capturing output."""
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def latex_error(log: Path) -> str:
    """Return the first TeX error block from *log*, for a useful one-liner."""
    if not log.exists():
        return "no log file produced"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("!"):
            return " ".join(lines[index : index + 3]).strip()
    return "compilation failed; see the log"


def convert_to_png(
    pdf: Path, png: Path, converter: tuple[str, str], dpi: int, work: Path
) -> str | None:
    """Convert *pdf* to *png*. Return an error message, or ``None`` on success."""
    command, kind = converter
    if kind == "poppler":
        # Both poppler tools append the page number, so render to a prefix and
        # then move the single page into place.
        prefix = work / "page"
        args = [command, "-png", "-r", str(dpi), "-singlefile", str(pdf), str(prefix)]
        result = run(args, work)
        produced = prefix.with_suffix(".png")
        if result.returncode != 0 or not produced.exists():
            return (result.stderr or "conversion failed").strip().splitlines()[0]
        shutil.move(str(produced), str(png))
        return None
    args = [command, "-density", str(dpi), str(pdf), "-quality", "92", str(png)]
    result = run(args, work)
    if result.returncode != 0 or not png.exists():
        return (result.stderr or "conversion failed").strip().splitlines()[0]
    return None


def render(
    tex: Path,
    outdir: Path,
    latex: tuple[str, list[str]],
    converter: tuple[str, str],
    dpi: int,
    keep_pdf: bool,
) -> str | None:
    """Compile and render one golden. Return an error message, or ``None``."""
    name = tex.name[: -len(STANDALONE_SUFFIX)]
    command, args = latex
    with tempfile.TemporaryDirectory(prefix=f"s2t-{name}-") as tmp:
        work = Path(tmp)
        shutil.copy2(tex, work / tex.name)
        result = run([command, *args, tex.name], work)
        # foo.standalone.tex compiles to foo.standalone.pdf / .log
        pdf = (work / tex.name).with_suffix(".pdf")
        if result.returncode != 0 or not pdf.exists():
            return latex_error(pdf.with_suffix(".log"))
        error = convert_to_png(pdf, outdir / f"{name}.png", converter, dpi, work)
        if error is not None:
            return error
        if keep_pdf:
            shutil.copy2(pdf, outdir / f"{name}.pdf")
    return None


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="render_goldens.py",
        description="Compile standalone golden .tex files and render them to PNG.",
    )
    parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="golden names to render (default: all)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="where to write images "
        f"(default: {DEFAULT_OUTDIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--dpi", type=int, default=200, help="raster resolution (default: 200)"
    )
    parser.add_argument(
        "--keep-pdf", action="store_true", help="also copy the compiled PDF"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render the goldens and return a process exit code."""
    args = build_parser().parse_args(argv)

    latex = find_latex()
    converter = find_converter()
    if latex is None or converter is None:
        missing = []
        if latex is None:
            missing.append("a LaTeX toolchain (latexmk or pdflatex)")
        if converter is None:
            missing.append("a PDF→PNG converter (pdftoppm, pdftocairo, or magick)")
        print(f"render_goldens: missing {' and '.join(missing)}", file=sys.stderr)
        print(
            "render_goldens: on Debian/Ubuntu install: latexmk texlive-latex-base "
            "texlive-latex-extra texlive-pictures texlive-science poppler-utils",
            file=sys.stderr,
        )
        return EXIT_NO_TOOLS

    available = sorted(GOLDEN_DIR.glob(f"*{STANDALONE_SUFFIX}"))
    if args.names:
        wanted = set(args.names)
        selected = [t for t in available if t.name[: -len(STANDALONE_SUFFIX)] in wanted]
        unknown = wanted - {t.name[: -len(STANDALONE_SUFFIX)] for t in available}
        for name in sorted(unknown):
            print(f"render_goldens: no such golden: {name}", file=sys.stderr)
        if unknown:
            return EXIT_NO_INPUT
    else:
        selected = available

    if not selected:
        print(
            f"render_goldens: no standalone goldens in {GOLDEN_DIR}; "
            "run: pytest --update-golden",
            file=sys.stderr,
        )
        return EXIT_NO_INPUT

    args.outdir.mkdir(parents=True, exist_ok=True)
    print(f"render_goldens: {latex[0]} + {converter[0]} at {args.dpi} dpi")
    print(f"render_goldens: writing to {args.outdir}")

    failures = 0
    for tex in selected:
        name = tex.name[: -len(STANDALONE_SUFFIX)]
        print(f"  {name:24} ", end="", flush=True)
        error = render(tex, args.outdir, latex, converter, args.dpi, args.keep_pdf)
        if error is None:
            print("ok")
        else:
            failures += 1
            print(f"FAILED: {error}")

    total = len(selected)
    print(f"render_goldens: {total - failures}/{total} rendered")
    if failures:
        return EXIT_FAILED
    print("render_goldens: review the images, then accept the goldens in git")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
