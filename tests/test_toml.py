"""Tests for the small TOML reader behind ``--config`` (``spice2tikz._toml``).

Both code paths are exercised explicitly: :func:`spice2tikz._toml.loads`, which
delegates to ``tomllib`` on Python 3.11+, and the subset parser
``_loads_subset`` that keeps Python 3.10 working without a runtime dependency.
Every acceptance test runs against both, so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from spice2tikz import _toml

PARSERS: list[Callable[[str], dict[str, Any]]] = [_toml.loads, _toml._loads_subset]
PARSER_IDS = ["loads", "subset"]


@pytest.fixture(params=PARSERS, ids=PARSER_IDS)
def parse(request: pytest.FixtureRequest) -> Callable[[str], dict[str, Any]]:
    """Each acceptance test runs against both parser implementations."""
    return request.param  # type: ignore[no-any-return]


def test_empty_document(parse: Callable[[str], dict[str, Any]]):
    assert parse("") == {}


def test_table_and_string(parse: Callable[[str], dict[str, Any]]):
    assert parse('[style]\nresistor_variant = "american"\n') == {
        "style": {"resistor_variant": "american"}
    }


def test_booleans_and_numbers(parse: Callable[[str], dict[str, Any]]):
    data = parse("[style]\nsiunitx = true\nlabel_refs = false\nn = 3\nx = 1.5\n")
    assert data["style"] == {
        "siunitx": True,
        "label_refs": False,
        "n": 3,
        "x": 1.5,
    }


def test_inline_array(parse: Callable[[str], dict[str, Any]]):
    assert parse('a = ["one", "two"]\n') == {"a": ["one", "two"]}


def test_multiline_array(parse: Callable[[str], dict[str, Any]]):
    text = 'extra_preamble = [\n  "one",\n  "two",\n]\n'
    assert parse(text) == {"extra_preamble": ["one", "two"]}


def test_comments_are_ignored(parse: Callable[[str], dict[str, Any]]):
    text = "# leading\n[style]  # trailing\nsiunitx = true # yes\n"
    assert parse(text) == {"style": {"siunitx": True}}


def test_a_hash_inside_a_string_is_not_a_comment(
    parse: Callable[[str], dict[str, Any]],
):
    assert parse('a = "x # y"\n') == {"a": "x # y"}


def test_literal_strings_keep_backslashes(parse: Callable[[str], dict[str, Any]]):
    assert parse(r"a = '\usepackage{amsmath}'" + "\n") == {"a": r"\usepackage{amsmath}"}


def test_escapes_in_basic_strings(parse: Callable[[str], dict[str, Any]]):
    assert parse(r'a = "back\\slash"' + "\n") == {"a": "back\\slash"}
    assert parse(r'a = "tab\there"' + "\n") == {"a": "tab\there"}
    assert parse(r'a = "µ"' + "\n") == {"a": "µ"}


def test_dotted_table_headers(parse: Callable[[str], dict[str, Any]]):
    assert parse("[a.b]\nc = 1\n") == {"a": {"b": {"c": 1}}}


def test_blank_lines_between_tables(parse: Callable[[str], dict[str, Any]]):
    assert parse("[a]\nx = 1\n\n[b]\ny = 2\n") == {"a": {"x": 1}, "b": {"y": 2}}


# --- errors -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "[style\n",  # unterminated table header
        "no_equals\n",
        'a = "unterminated\n',
        "a = [1, 2\n",
        "a = @\n",
    ],
)
def test_malformed_documents_raise(parse: Callable[[str], dict[str, Any]], text: str):
    # tomllib raises its own ValueError subclass; the subset parser raises
    # TomlError, which is also one.
    with pytest.raises(ValueError):
        parse(text)


def test_toml_error_is_a_value_error():
    assert issubclass(_toml.TomlError, ValueError)


def test_subset_parser_rejects_a_duplicate_key():
    with pytest.raises(_toml.TomlError, match="duplicate key"):
        _toml._loads_subset("a = 1\na = 2\n")


def test_subset_parser_rejects_an_unterminated_array():
    with pytest.raises(_toml.TomlError, match="unterminated array"):
        _toml._loads_subset("a = [1,\n")


def test_subset_parser_rejects_an_unknown_escape():
    with pytest.raises(_toml.TomlError, match="unknown escape"):
        _toml._loads_subset(r'a = "\q"' + "\n")
