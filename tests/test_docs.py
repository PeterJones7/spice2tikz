"""Documentation tests: the worked examples in the docs must stay true.

`CLAUDE.md` working rule 7 makes docs deliverables and requires `README.md` to
be accurate at every push. The cheapest way to keep it that way is to assert
that the LaTeX the README claims the tool produces is the LaTeX the golden
files actually contain.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
GOLDEN = REPO_ROOT / "tests" / "golden"
DOCS = REPO_ROOT / "docs"

_LATEX_BLOCK = re.compile(r"```latex\n(.*?)```", re.DOTALL)


def readme_latex_blocks() -> list[str]:
    """Return the contents of every ```latex fenced block in the README."""
    return _LATEX_BLOCK.findall(README.read_text(encoding="utf-8"))


def test_readme_shows_a_latex_example():
    assert readme_latex_blocks(), "the README no longer shows any generated LaTeX"


def test_readme_example_matches_the_golden():
    golden = (GOLDEN / "rc_lowpass.tex").read_text(encoding="utf-8")
    assert golden in readme_latex_blocks(), (
        "README.md's example output no longer matches tests/golden/rc_lowpass.tex; "
        "update the README block after regenerating the goldens"
    )


def test_readme_example_image_exists():
    text = README.read_text(encoding="utf-8")
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
        target = match.group(1)
        if target.startswith("http"):
            continue
        assert (REPO_ROOT / target).is_file(), f"README image {target} is missing"


@pytest.mark.parametrize(
    "name",
    ["DESIGN.md", "SPEC_IR.md", "ROADMAP.md", "DECISIONS.md", "EMITTER.md"],
)
def test_documented_files_exist(name: str):
    assert (DOCS / name).is_file()


def test_readme_links_to_local_docs_that_exist():
    text = README.read_text(encoding="utf-8")
    for match in re.finditer(r"`(docs/[A-Za-z_]+\.md)`", text):
        assert (REPO_ROOT / match.group(1)).is_file(), f"{match.group(1)} is missing"


# --- repository hygiene -----------------------------------------------------


TEXT_SUFFIXES = frozenset(
    {".py", ".json", ".tex", ".md", ".toml", ".yml", ".yaml", ".sp", ".asc", ".cfg"}
)
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        ".ruff_cache",
        "build",
        "dist",
    }
)
# The circuitikz manual is a verbatim PDF extraction, kept exactly as received.
EXEMPT = frozenset({Path("docs/circuitikz_manual.MD")})


def text_files() -> list[Path]:
    """Return every repository text file whose line endings we control."""
    found = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.relative_to(REPO_ROOT) in EXEMPT:
            continue
        found.append(path)
    return found


def test_no_text_file_uses_crlf():
    """Byte-for-byte golden comparison only works if line endings are stable."""
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in text_files()
        if b"\r\n" in path.read_bytes()
    ]
    assert not offenders, f"CRLF line endings in: {offenders}"


def test_every_golden_ends_with_exactly_one_newline():
    offenders = []
    for path in (REPO_ROOT / "tests" / "golden").rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not data.endswith(b"\n") or data.endswith(b"\n\n"):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, f"goldens with odd trailing newlines: {offenders}"
