"""Golden-file tests for the circuitikz emitter (roadmap §2.3).

Every ``tests/corpus/*.schematic.json`` file is emitted in both snippet and
standalone mode and compared byte-for-byte against ``tests/golden/``. The same
corpus doubles as the validation, determinism, and round-trip corpus, so a
hand-written circuit that is geometrically wrong fails here rather than only
looking wrong in a rendered PDF.

Regenerate with ``pytest --update-golden`` and review the diff in git.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from spice2tikz import schematic_ir
from spice2tikz.emit.circuitikz import emit_snippet, emit_standalone
from spice2tikz.schematic_ir import NodeComponent, SchematicIR
from spice2tikz.validate import Severity, format_finding, validate

CORPUS = Path(__file__).parent / "corpus"

SUFFIX = ".schematic.json"
CORPUS_FILES = sorted(CORPUS.glob(f"*{SUFFIX}"))
CORPUS_NAMES = [path.name[: -len(SUFFIX)] for path in CORPUS_FILES]

REQUIRED_CIRCUITS = frozenset(
    {
        "rc_lowpass",
        "voltage_divider",
        "rlc_series",
        "bridge_rectifier",
        "common_source_amp",
        "opamp_placeholder",
    }
)
"""The circuits the roadmap names for §2.3; extras are welcome."""


def load(name: str) -> SchematicIR:
    """Load the corpus document *name*."""
    return schematic_ir.load(CORPUS / f"{name}{SUFFIX}")


def node_components(ir: SchematicIR) -> list[NodeComponent]:
    """Return every node component of every sheet of *ir*."""
    return [
        element
        for sheet in ir.sheets
        for element in sheet.elements
        if isinstance(element, NodeComponent)
    ]


# --- corpus coverage (the roadmap's own requirements) ------------------------


def test_corpus_contains_the_required_circuits():
    assert set(CORPUS_NAMES) >= REQUIRED_CIRCUITS


def test_corpus_has_at_least_six_documents():
    assert len(CORPUS_NAMES) >= 6


def test_corpus_exercises_all_four_rotations():
    rotations = {
        component.rot
        for name in CORPUS_NAMES
        for component in node_components(load(name))
    }
    assert rotations == {0, 90, 180, 270}


def test_corpus_exercises_mirroring():
    assert any(
        component.mirror
        for name in CORPUS_NAMES
        for component in node_components(load(name))
    )


# --- the corpus itself must be well-formed ----------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_document_validates_without_findings(name: str):
    findings = validate(load(name))
    assert not findings, "\n".join(format_finding(finding) for finding in findings)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_document_has_no_errors(name: str):
    # Redundant with the above while the corpus is clean, but this is the
    # invariant that actually matters: warnings may be tolerated one day.
    findings = validate(load(name))
    assert not [f for f in findings if f.severity is Severity.ERROR]


@pytest.mark.parametrize("path", CORPUS_FILES, ids=CORPUS_NAMES)
def test_corpus_file_is_canonical_json(path: Path):
    text = path.read_text(encoding="utf-8")
    assert schematic_ir.dumps(schematic_ir.loads(text)) == text


# --- golden output ----------------------------------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_snippet_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"{name}.tex", emit_snippet(load(name)))


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_standalone_matches_golden(name: str, golden: Callable[[str, str], None]):
    golden(f"{name}.standalone.tex", emit_standalone(load(name)))


# --- determinism and round-tripping (DESIGN §5) -----------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_emission_is_deterministic(name: str):
    first, second = load(name), load(name)
    assert emit_snippet(first) == emit_snippet(second)
    assert emit_standalone(first) == emit_standalone(second)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_emitting_the_same_document_twice_is_byte_equal(name: str):
    ir = load(name)
    assert emit_snippet(ir) == emit_snippet(ir)
    assert emit_standalone(ir) == emit_standalone(ir)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_json_round_trip_does_not_change_emission(name: str):
    ir = load(name)
    reloaded = schematic_ir.loads(schematic_ir.dumps(ir))
    assert emit_snippet(reloaded) == emit_snippet(ir)
    assert emit_standalone(reloaded) == emit_standalone(ir)


# --- shape of the goldens ---------------------------------------------------


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_standalone_wraps_the_snippet_verbatim(name: str):
    ir = load(name)
    snippet = emit_snippet(ir).rstrip("\n")
    assert snippet in emit_standalone(ir)


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_golden_files_end_with_exactly_one_newline(name: str):
    for suffix in (".tex", ".standalone.tex"):
        path = Path(__file__).parent / "golden" / f"{name}{suffix}"
        if not path.exists():  # first --update-golden run
            continue
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
