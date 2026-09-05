"""Turn emitted CircuiTikZ into a PDF, PNG or SVG.

Rendering is deliberately outside the emitter (``emit/circuitikz.py``), which
stays a pure function from Schematic IR to text.  This module only drives
external tools: it compiles a standalone document and, for the raster and
vector image formats, converts the resulting PDF.

Nothing here is a runtime dependency (D1) — it is all :mod:`subprocess` and
:mod:`shutil` — but the *tools* are, so every entry point reports a missing one
by name rather than failing obscurely.

The chain is always the same, which is why there is no separate SVG emitter::

    Schematic IR → CircuiTikZ → PDF → PNG / SVG

The ``.tex`` output is byte-for-byte reproducible everywhere.  The PDF is too
within one toolchain — see :data:`REPRODUCIBLE_ENV` — but its ``/Producer``
string names the pdfTeX version, and the images inherit whatever the local
converter does, so nothing compares them across machines.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

__all__ = [
    "RENDERED_FORMATS",
    "RenderError",
    "find_latex",
    "format_for",
    "missing_tools",
    "render",
]

TEX: Final = "tex"
PDF: Final = "pdf"
PNG: Final = "png"
SVG: Final = "svg"

RENDERED_FORMATS: Final[tuple[str, ...]] = (PDF, PNG, SVG)
"""Formats that need an external toolchain; ``tex`` is written directly."""

OUTPUT_FORMATS: Final[tuple[str, ...]] = (TEX, *RENDERED_FORMATS)

DEFAULT_DPI: Final = 150
TIMEOUT_SECONDS: Final = 300

REPRODUCIBLE_ENV: Final[dict[str, str]] = {
    "SOURCE_DATE_EPOCH": "0",
    "FORCE_SOURCE_DATE": "1",
}
"""Pin the clock pdfTeX stamps into the PDF.

Determinism is a promise this project makes about its own output, and there is
no reason to drop it at the last step: with these set, pdfTeX writes a fixed
``/CreationDate``, ``/ModDate`` and trailer ``/ID``, so the same input renders
to the same bytes.  Only within one toolchain, mind — the ``/Producer`` string
carries the pdfTeX version — but that is enough to diff two runs of a change
and see whether the picture moved.
"""


class RenderError(Exception):
    """A rendering step failed, or the tool it needs is not installed."""


def format_for(target: Path) -> str:
    """Return the output format implied by *target*'s extension.

    The extension is matched case-insensitively, so ``diagram.PNG`` works.
    """
    suffix = target.suffix.lower().lstrip(".")
    if suffix not in OUTPUT_FORMATS:
        raise RenderError(
            f"cannot tell what to write from the extension "
            f"{target.suffix or '(none)'!r}; use one of "
            f"{', '.join('.' + name for name in OUTPUT_FORMATS)}"
        )
    return suffix


# --- finding the tools -------------------------------------------------------


def _available(candidates: tuple[str, ...]) -> list[str]:
    """Return the candidates present on this machine, in preference order."""
    return [command for command in candidates if shutil.which(command)]


def _first_available(candidates: tuple[str, ...]) -> str | None:
    found = _available(candidates)
    return found[0] if found else None


LATEX_COMMANDS: Final[tuple[str, ...]] = ("latexmk", "pdflatex")
PNG_COMMANDS: Final[tuple[str, ...]] = (
    "pdftoppm",
    "pdftocairo",
    "gs",
    "gswin64c",
    "magick",
)
SVG_COMMANDS: Final[tuple[str, ...]] = (
    "pdftocairo",
    "dvisvgm",
    "mutool",
    "inkscape",
)
"""PDF-to-SVG converters, most dependable first.

poppler's ``pdftocairo`` needs nothing else; ``dvisvgm`` is more likely to
be installed (it ships with TeX Live) but cannot read PDF without a
Ghostscript older than 10.01 or mutool alongside it."""


def find_latex() -> list[str] | None:
    """Return the LaTeX command and its arguments, or ``None`` if absent."""
    command = _first_available(LATEX_COMMANDS)
    if command == "latexmk":
        return [command, "-pdf", "-interaction=nonstopmode", "-halt-on-error"]
    if command == "pdflatex":
        return [command, "-interaction=nonstopmode", "-halt-on-error"]
    return None


def missing_tools(output_format: str) -> list[str]:
    """Return a description of each class of tool *output_format* needs and lacks.

    A preflight for callers that would rather say what is missing once, up
    front, than fail on the first of fifty files — ``tools/render_goldens.py``
    and ``tools/contact_sheet.py`` both do. It shares this module's candidate
    lists, so a tool added here is looked for there too.
    """
    if output_format not in RENDERED_FORMATS:
        return []
    missing = []
    if find_latex() is None:
        missing.append(f"a LaTeX toolchain ({' or '.join(LATEX_COMMANDS)})")
    needed = {PNG: PNG_COMMANDS, SVG: SVG_COMMANDS}.get(output_format)
    if needed is not None and not _available(needed):
        missing.append(
            f"a PDF-to-{output_format.upper()} converter ({', '.join(needed)})"
        )
    return missing


def _convert(
    what: str,
    candidates: tuple[str, ...],
    attempt: Callable[[str], tuple[list[str], Path]],
    target: Path,
    work: Path,
) -> None:
    """Try each installed converter in turn until one produces *target*.

    Being installed is not the same as being able to do the job: dvisvgm needs
    a Ghostscript older than 10.01 to read PDF, and ImageMagick's default
    policy refuses PDF outright.  Both are common, and both report the reason
    perfectly well — so a converter that fails is passed over rather than being
    the end of it, and only when every candidate has failed does the error
    carry what each of them said.
    """
    found = _available(candidates)
    if not found:
        raise RenderError(f"no {what} found; install one of: {', '.join(candidates)}")
    failures: list[str] = []
    for command in found:
        args, produced = attempt(command)
        result = _run(args, work)
        if result.returncode == 0 and produced.exists():
            shutil.move(str(produced), str(target))
            return
        failures.append(_tool_failure(command, result))
    raise RenderError("; ".join(failures))


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, **REPRODUCIBLE_ENV},
        )
    except subprocess.TimeoutExpired as error:  # pragma: no cover - needs a hang
        raise RenderError(f"{args[0]} timed out after {TIMEOUT_SECONDS}s") from error
    except OSError as error:  # pragma: no cover - race with shutil.which
        raise RenderError(f"could not run {args[0]}: {error}") from error


# --- the steps ---------------------------------------------------------------


def _first_tex_error(log: Path) -> str:
    """Return the first TeX error from *log*, so a failure says what broke."""
    if not log.exists():
        return "no log file was produced"
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("!"):
            return line.lstrip("! ").strip()
    return "compilation failed; see the LaTeX log"


def _compile(source: str, work: Path) -> Path:
    """Compile standalone LaTeX *source* in *work* and return the PDF path."""
    latex = find_latex()
    if latex is None:
        raise RenderError(
            "no LaTeX toolchain found; install one of: "
            f"{', '.join(LATEX_COMMANDS)} (TeX Live or MiKTeX), "
            "or write .tex and compile it yourself"
        )
    tex = work / "figure.tex"
    tex.write_text(source, encoding="utf-8", newline="\n")
    result = _run([*latex, tex.name], work)
    pdf = work / "figure.pdf"
    if result.returncode != 0 or not pdf.exists():
        raise RenderError(_first_tex_error(work / "figure.log"))
    return pdf


def _to_png(pdf: Path, target: Path, work: Path, dpi: int) -> None:
    def attempt(command: str) -> tuple[list[str], Path]:
        if command in ("pdftoppm", "pdftocairo"):
            # Both poppler tools append the page number, so render to a prefix.
            prefix = work / "page"
            return (
                [command, "-png", "-r", str(dpi), "-singlefile", str(pdf), str(prefix)],
                prefix.with_suffix(".png"),
            )
        produced = work / "page.png"
        if command in ("gs", "gswin64c"):
            return (
                [
                    command,
                    "-q",
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-dSAFER",
                    "-sDEVICE=pngalpha",
                    f"-r{dpi}",
                    f"-sOutputFile={produced}",
                    str(pdf),
                ],
                produced,
            )
        return ([command, "-density", str(dpi), str(pdf), str(produced)], produced)

    _convert("PDF-to-PNG converter", PNG_COMMANDS, attempt, target, work)


def _to_svg(pdf: Path, target: Path, work: Path) -> None:
    def attempt(command: str) -> tuple[list[str], Path]:
        produced = work / "page.svg"
        if command == "pdftocairo":
            return ([command, "-svg", str(pdf), str(produced)], produced)
        if command == "dvisvgm":
            # Ships with TeX Live, so it is usually present when latexmk is,
            # but its PDF support needs Ghostscript older than 10.01 or mutool;
            # _convert() moves on when it says so.
            return (
                [command, "--pdf", "--output=" + str(produced), str(pdf)],
                produced,
            )
        if command == "inkscape":
            return (
                [
                    command,
                    str(pdf),
                    "--export-type=svg",
                    "--export-filename=" + str(produced),
                ],
                produced,
            )
        # mutool numbers its output after the page.
        return (
            [command, "convert", "-o", str(produced), str(pdf), "1"],
            work / "page1.svg",
        )

    _convert("PDF-to-SVG converter", SVG_COMMANDS, attempt, target, work)


def _tool_failure(command: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return f"{command} failed: {detail[0] if detail else 'no output'}"


def render(
    source: str, target: Path, output_format: str, *, dpi: int = DEFAULT_DPI
) -> None:
    """Write *source* to *target* in *output_format*.

    *source* must be a **standalone** document for every format but ``tex``: a
    bare snippet has no preamble and cannot be compiled on its own.

    Every intermediate file lives in a temporary directory that is removed
    whether or not the run succeeds, so a failed compile leaves nothing behind
    but the message.
    """
    if output_format == TEX:
        target.write_text(source, encoding="utf-8", newline="\n")
        return
    if output_format not in RENDERED_FORMATS:
        raise RenderError(f"unknown output format {output_format!r}")

    with tempfile.TemporaryDirectory(prefix="spice2tikz-") as tmp:
        work = Path(tmp)
        pdf = _compile(source, work)
        if output_format == PDF:
            shutil.move(str(pdf), str(target))
        elif output_format == PNG:
            _to_png(pdf, target, work, dpi)
        else:
            _to_svg(pdf, target, work)
