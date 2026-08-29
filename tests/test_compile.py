"""Compile every standalone golden with a real LaTeX toolchain (roadmap §2.5).

Golden-file tests prove the emitter's output has not *changed*; only a compiler
proves it is *valid*.  A ``.tex`` that renders a beautiful diff but does not
build is worse than useless, so CI runs this in a TeX Live container
(``.github/workflows/ci.yml``, job ``compile``).

Locally the whole module is skipped when no LaTeX toolchain is installed, per
``docs/DESIGN.md`` §5 — contributors should not need TeX Live to run the suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"
STANDALONE_SUFFIX = ".standalone.tex"
TIMEOUT_SECONDS = 180

STANDALONE_GOLDENS = sorted(GOLDEN.glob(f"**/*{STANDALONE_SUFFIX}"))
GOLDEN_IDS = [
    path.relative_to(GOLDEN).as_posix()[: -len(STANDALONE_SUFFIX)]
    for path in STANDALONE_GOLDENS
]


def find_latexmk() -> str | None:
    """Return the path to ``latexmk``, or ``None`` when it is not installed."""
    return shutil.which("latexmk")


requires_latexmk = pytest.mark.skipif(
    find_latexmk() is None,
    reason="latexmk not installed; TeX compilation is checked in CI",
)


def first_tex_error(log: Path) -> str:
    """Return the first TeX error block from *log*, to make a failure readable."""
    if not log.exists():
        return "no log file was produced"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("!"):
            return "\n".join(lines[index : index + 8])
    return "compilation failed with no '!' line in the log"


def compile_tex(source: Path, work: Path) -> tuple[int, Path, Path]:
    """Copy *source* into *work* and run ``latexmk -pdf``; return code and paths."""
    target = work / source.name
    shutil.copy2(source, target)
    environment = dict(os.environ)
    # A per-run cache directory keeps parallel invocations from racing, and
    # keeps the test from writing into the user's home directory.
    environment["TEXMFVAR"] = str(work / "texmf-var")
    result = subprocess.run(
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            source.name,
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=environment,
        check=False,
    )
    return result.returncode, target.with_suffix(".pdf"), target.with_suffix(".log")


def test_there_are_standalone_goldens_to_compile():
    """A silent zero-golden run would make the whole job vacuous."""
    assert STANDALONE_GOLDENS, "no *.standalone.tex under tests/golden/"


@requires_latexmk
@pytest.mark.parametrize("source", STANDALONE_GOLDENS, ids=GOLDEN_IDS)
def test_standalone_golden_compiles(source: Path, tmp_path: Path):
    code, pdf, log = compile_tex(source, tmp_path)
    assert code == 0, f"{source.name} failed to compile:\n{first_tex_error(log)}"
    assert pdf.exists(), f"{source.name}: latexmk reported success but wrote no PDF"
    assert pdf.stat().st_size > 0


@requires_latexmk
def test_a_deliberately_broken_document_fails(tmp_path: Path):
    """Guard against the compile check silently passing everything."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    broken = source_dir / "broken.standalone.tex"
    broken.write_text(
        "\\documentclass[border=2pt]{standalone}\n"
        "\\usepackage{circuitikz}\n"
        "\\begin{document}\n"
        "\\begin{circuitikz}\n"
        "\\draw (0,0) to[thisbipoledoesnotexist] (2,0);\n"
        "\\end{circuitikz}\n"
        "\\end{document}\n",
        encoding="utf-8",
        newline="\n",
    )
    code, _, _ = compile_tex(broken, tmp_path)
    assert code != 0
