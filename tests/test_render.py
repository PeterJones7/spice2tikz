"""Rendered output: ``-o`` chooses the format from the extension.

The chain is always Schematic IR → CircuiTikZ → PDF → PNG/SVG, so these tests
split three ways:

* format selection and error reporting, which need no tools at all;
* real compilation to PDF and PNG, skipped without a LaTeX toolchain;
* the SVG path, exercised through a stub converter placed on ``PATH`` so that
  the arguments and file handling are covered on a machine with no real
  PDF-to-SVG tool installed.

``.tex`` is byte-for-byte reproducible everywhere and the PDF is reproducible
within one toolchain, so two runs on this machine are compared. Beyond that the
checks are that the right file appears and starts with the right magic: the
``/Producer`` string names the pdfTeX version, and the images inherit whatever
the local converter does.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from spice2tikz import cli, render
from spice2tikz.render import RenderError, format_for

CORPUS = Path(__file__).parent / "corpus" / "spice"
DECK = CORPUS / "rc_lowpass.sp"

requires_latexmk = pytest.mark.skipif(
    render.find_latex() is None,
    reason="no LaTeX toolchain; rendering is checked in CI",
)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run the CLI and return ``(exit code, stdout, stderr)``."""
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- choosing the format -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("x.tex", "tex"),
        ("x.pdf", "pdf"),
        ("x.png", "png"),
        ("x.svg", "svg"),
        ("x.TEX", "tex"),
        ("x.PNG", "png"),
        ("x.SvG", "svg"),
        ("a.b.pdf", "pdf"),
    ],
)
def test_format_comes_from_the_extension(name: str, expected: str):
    assert format_for(Path(name)) == expected


@pytest.mark.parametrize("name", ["x.jpeg", "x.eps", "x", "x."])
def test_an_unknown_extension_is_refused(name: str):
    with pytest.raises(RenderError, match="cannot tell what to write"):
        format_for(Path(name))


def test_the_refusal_lists_what_is_supported():
    with pytest.raises(RenderError) as caught:
        format_for(Path("x.jpeg"))
    for known in (".tex", ".pdf", ".png", ".svg"):
        assert known in str(caught.value)


# --- .tex needs no toolchain -------------------------------------------------


def test_tex_is_written_directly_with_lf_newlines(tmp_path: Path):
    target = tmp_path / "out.tex"
    render.render("a\nb\n", target, "tex")
    assert target.read_bytes() == b"a\nb\n"


def test_cli_writes_tex_unchanged(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    """The default path must be exactly as it was before rendering existed."""
    target = tmp_path / "out.tex"
    code, _, _ = run(capsys, str(DECK), "-o", str(target), "-q")
    assert code == cli.EXIT_OK
    _, expected, _ = run(capsys, str(DECK), "-q")
    assert target.read_text(encoding="utf-8") == expected


def test_cli_reports_an_unknown_extension(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, out, err = run(capsys, str(DECK), "-o", str(tmp_path / "x.jpeg"), "-q")
    assert code == cli.EXIT_INPUT_ERROR
    assert out == ""
    assert "cannot tell what to write" in err


def test_a_validation_error_still_wins_over_rendering(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    """Exit 2 and no file: a broken schematic is never rendered."""
    broken = (
        Path(__file__).parent / "corpus" / "broken" / "s9_dangling_wire.schematic.json"
    )
    target = tmp_path / "out.pdf"
    code, _, _ = run(capsys, str(broken), "-o", str(target), "-q")
    assert code == cli.EXIT_VALIDATION_ERROR
    assert not target.exists()


# --- real compilation --------------------------------------------------------


@requires_latexmk
def test_pdf_is_produced(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    target = tmp_path / "out.pdf"
    code, out, _ = run(capsys, str(DECK), "-o", str(target), "-q")
    assert code == cli.EXIT_OK
    assert out == ""
    assert target.read_bytes().startswith(b"%PDF")


@requires_latexmk
def test_png_is_produced(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    target = tmp_path / "out.png"
    code, _, _ = run(capsys, str(DECK), "-o", str(target), "-q")
    assert code == cli.EXIT_OK
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@requires_latexmk
def test_a_rendered_format_does_not_need_the_standalone_flag(tmp_path: Path):
    """A snippet has no preamble; rendering must wrap it whether asked or not."""
    target = tmp_path / "out.pdf"
    assert cli.main([str(DECK), "-o", str(target), "-q"]) == cli.EXIT_OK
    assert target.exists()


@requires_latexmk
def test_dpi_changes_the_image(tmp_path: Path):
    small, large = tmp_path / "s.png", tmp_path / "l.png"
    assert cli.main([str(DECK), "-o", str(small), "--dpi", "50", "-q"]) == cli.EXIT_OK
    assert cli.main([str(DECK), "-o", str(large), "--dpi", "200", "-q"]) == cli.EXIT_OK
    assert large.stat().st_size > small.stat().st_size


@requires_latexmk
def test_the_same_input_renders_to_the_same_pdf(tmp_path: Path):
    """Determinism is promised for the .tex; the PDF should not give it up.

    pdfTeX stamps a creation date and a trailer ID from the clock unless
    SOURCE_DATE_EPOCH pins it, which would make every render differ.
    """
    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"
    assert cli.main([str(DECK), "-o", str(first), "-q"]) == cli.EXIT_OK
    assert cli.main([str(DECK), "-o", str(second), "-q"]) == cli.EXIT_OK
    assert first.read_bytes() == second.read_bytes()


@requires_latexmk
def test_nothing_is_left_behind(tmp_path: Path):
    """Every intermediate lives in a temporary directory that is removed."""
    target = tmp_path / "out.pdf"
    assert cli.main([str(DECK), "-o", str(target), "-q"]) == cli.EXIT_OK
    assert [path.name for path in tmp_path.iterdir()] == ["out.pdf"]


@requires_latexmk
def test_a_document_that_will_not_compile_reports_the_tex_error(tmp_path: Path):
    with pytest.raises(RenderError) as caught:
        render.render(
            "\\documentclass{standalone}\\begin{document}"
            "\\thiscommanddoesnotexist\\end{document}\n",
            tmp_path / "out.pdf",
            "pdf",
        )
    assert "undefined control sequence" in str(caught.value).lower()
    assert not (tmp_path / "out.pdf").exists()


# --- the SVG path, through a stub --------------------------------------------


STUB = """#!{python}
import sys
from pathlib import Path

# Mimic pdftocairo -svg IN OUT: the output path is the last argument.
Path(sys.argv[-1]).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
"""


@pytest.fixture
def stub_pdftocairo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a fake ``pdftocairo`` first on PATH.

    The real tool is not installed everywhere — notably not alongside the
    TeX Live used in CI — but the argument order and the move into place are
    ours to get right, so they are worth covering.
    """
    binary = tmp_path / "bin"
    binary.mkdir()
    script = binary / "pdftocairo"
    script.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    if os.name == "nt":  # a .bat wrapper is what shutil.which finds on Windows
        (binary / "pdftocairo.bat").write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return binary


@requires_latexmk
def test_svg_is_produced_through_the_converter(stub_pdftocairo: Path, tmp_path: Path):
    target = tmp_path / "out.svg"
    assert cli.main([str(DECK), "-o", str(target), "-q"]) == cli.EXIT_OK
    assert target.read_text(encoding="utf-8").startswith("<svg")


def test_a_missing_converter_is_reported_by_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda command: None)
    with pytest.raises(RenderError) as caught:
        render._to_svg(Path("in.pdf"), Path("out.svg"), Path())
    message = str(caught.value)
    assert "no PDF-to-SVG converter found" in message
    for command in render.SVG_COMMANDS:
        assert command in message


def test_a_converter_that_fails_is_passed_over(monkeypatch: pytest.MonkeyPatch):
    """Installed is not the same as able: dvisvgm needs an older Ghostscript."""
    tried: list[str] = []

    def fake_run(args: list[str], cwd: Path) -> object:
        tried.append(args[0])

        class Result:
            returncode = 1
            stdout = ""
            stderr = f"{args[0]}: nope"

        return Result()

    monkeypatch.setattr(shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(render, "_run", fake_run)
    with pytest.raises(RenderError) as caught:
        render._to_svg(Path("in.pdf"), Path("out.svg"), Path())
    assert tried == list(render.SVG_COMMANDS), "every candidate should be tried"
    assert str(caught.value).count("nope") == len(render.SVG_COMMANDS)
