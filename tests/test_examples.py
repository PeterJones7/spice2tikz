"""The committed example gallery must stay true (roadmap §6.1).

`examples/` holds generated `.tex` and `.png` files that the README links to,
so a reader on the web sees what the tool actually produces. Generated files
that drift from the generator are worse than no examples at all, so the `.tex`
is regenerated here and compared. The `.png` files cannot be rebuilt without a
LaTeX toolchain, so they are only checked for existence — `examples/build.sh`
rebuilds them, and `make -C examples check` reports drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from spice2tikz import asc_importer, spice_parser
from spice2tikz.emit.circuitikz import emit_snippet
from spice2tikz.layout import layout
from spice2tikz.schematic_ir import SchematicIR

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
CORPUS = REPO_ROOT / "tests" / "corpus"
BUILD_SCRIPT = EXAMPLES / "build.sh"

_ENTRY = re.compile(r"^(\w+)\s+(\S+\.(?:sp|asc))$", re.MULTILINE)


def listed_examples() -> list[tuple[str, str]]:
    """Return the ``(name, source)`` pairs ``build.sh`` declares."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    body = text.split('examples="', 1)[1].split('"', 1)[0]
    return _ENTRY.findall(body)


NAMES = listed_examples()
IDS = [name for name, _ in NAMES]


def convert(source: str) -> SchematicIR:
    """Run the same pipeline the CLI runs for *source*."""
    path = CORPUS / source
    if path.suffix == ".asc":
        return asc_importer.load_asc(path)
    return layout(spice_parser.load_spice(path))


def test_build_script_lists_examples():
    assert len(NAMES) >= 6, "the roadmap wants a gallery, not a token example"


def test_the_gallery_covers_both_input_paths():
    sources = [source for _, source in NAMES]
    assert any(source.endswith(".sp") for source in sources)
    assert any(source.endswith(".asc") for source in sources)


@pytest.mark.parametrize(("name", "source"), NAMES, ids=IDS)
def test_the_source_circuit_exists(name: str, source: str):
    assert (CORPUS / source).is_file(), f"{name} names a missing corpus file"


@pytest.mark.parametrize(("name", "source"), NAMES, ids=IDS)
def test_the_committed_tex_is_current(name: str, source: str):
    target = EXAMPLES / f"{name}.tex"
    assert target.is_file(), f"missing {target}; run examples/build.sh"
    assert target.read_text(encoding="utf-8") == emit_snippet(convert(source)), (
        f"{target.name} is out of date; run: bash examples/build.sh"
    )


@pytest.mark.parametrize("name", IDS)
def test_the_committed_image_exists(name: str):
    image = EXAMPLES / f"{name}.png"
    assert image.is_file(), f"missing {image}; run examples/build.sh"
    assert image.stat().st_size > 0


def test_every_example_image_is_used_by_the_readme():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    unused = [name for name in IDS if f"examples/{name}.png" not in readme]
    assert not unused, f"generated but never shown: {unused}"


def test_the_makefile_defers_to_the_build_script():
    makefile = (EXAMPLES / "Makefile").read_text(encoding="utf-8")
    assert "build.sh" in makefile
