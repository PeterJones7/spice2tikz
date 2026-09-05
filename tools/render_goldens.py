#!/usr/bin/env python3
"""Compile the standalone golden ``.tex`` files and render them to PNG.

This is a developer aid for the human-review step of roadmap 2.3: goldens are
compared byte-for-byte by ``pytest``, but only a rendered image shows whether a
schematic is actually *right* — components present, orientation and mirroring
correct, wires joining the intended pins, junction dots where expected.

Usage::

    python tools/render_goldens.py                 # render every golden
    python tools/render_goldens.py rc_lowpass ...  # only the named ones
    python tools/render_goldens.py --dpi 300
    python tools/render_goldens.py --outdir /tmp/review --keep-pdf

The pipeline itself is :mod:`spice2tikz.render`, the same one behind
``spice2tikz -o figure.png``, so this tool cannot drift from what users get.
It needs what that needs: a LaTeX toolchain with circuitikz, and a PDF-to-PNG
converter. Missing tools are reported and exit code 3 is returned rather than
failing obscurely.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from spice2tikz.render import RenderError, missing_tools  # noqa: E402
from spice2tikz.render import render as render_to  # noqa: E402

GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
DEFAULT_OUTDIR = REPO_ROOT / "build" / "golden-review"
STANDALONE_SUFFIX = ".standalone.tex"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_INPUT = 2
EXIT_NO_TOOLS = 3


def golden_name(tex: Path) -> str:
    """Return a golden's name, keeping any subdirectory (``asc/rc_lowpass``)."""
    relative = tex.relative_to(GOLDEN_DIR)
    return relative.as_posix()[: -len(STANDALONE_SUFFIX)]


def render(tex: Path, outdir: Path, dpi: int, keep_pdf: bool) -> str | None:
    """Render one golden to PNG. Return an error message, or ``None``."""
    name = golden_name(tex)
    source = tex.read_text(encoding="utf-8")
    png = outdir / f"{name}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    try:
        render_to(source, png, "png", dpi=dpi)
        if keep_pdf:
            # A second compile, but --keep-pdf is opt-in and this keeps the
            # tool to one call per output rather than a pipeline of its own.
            render_to(source, outdir / f"{name}.pdf", "pdf")
    except RenderError as error:
        return str(error)
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
        "--keep-pdf", action="store_true", help="also write the compiled PDF"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render the goldens and return a process exit code."""
    args = build_parser().parse_args(argv)

    missing = missing_tools("png")
    if missing:
        print(f"render_goldens: missing {' and '.join(missing)}", file=sys.stderr)
        print(
            "render_goldens: on Debian/Ubuntu install: latexmk texlive-latex-base "
            "texlive-latex-extra texlive-pictures texlive-science poppler-utils",
            file=sys.stderr,
        )
        return EXIT_NO_TOOLS

    # ** so that goldens grouped in subdirectories (asc/, layout/) are found too.
    available = sorted(GOLDEN_DIR.glob(f"**/*{STANDALONE_SUFFIX}"))
    if args.names:
        wanted = set(args.names)
        selected = [t for t in available if golden_name(t) in wanted]
        unknown = wanted - {golden_name(t) for t in available}
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
    print(f"render_goldens: writing to {args.outdir} at {args.dpi} dpi")

    failures = 0
    for tex in selected:
        name = golden_name(tex)
        print(f"  {name:24} ", end="", flush=True)
        error = render(tex, args.outdir, args.dpi, args.keep_pdf)
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
