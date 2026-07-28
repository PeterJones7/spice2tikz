"""Tests for the canonical IR JSON printer (``docs/SPEC_IR.md`` §0)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from spice2tikz._serde import LINE_BUDGET, dumps, loads


def test_objects_put_one_field_per_line():
    assert dumps({"a": 1, "b": "two"}) == '{\n  "a": 1,\n  "b": "two"\n}\n'


def test_field_order_is_preserved_not_sorted():
    text = dumps({"z": 1, "a": 2, "m": 3})
    assert [line.strip().split(":")[0] for line in text.splitlines()[1:-1]] == [
        '"z"',
        '"a"',
        '"m"',
    ]


def test_coordinate_pairs_stay_inline():
    assert '"a": [0, 4]' in dumps({"a": [0, 4]})


def test_point_lists_stay_inline():
    assert '"points": [[0, 0], [6, 0], [6, 4]]' in dumps(
        {"points": [[0, 0], [6, 0], [6, 4]]}
    )


def test_scalar_arrays_of_any_type_stay_inline():
    assert '"x": ["a", "b"]' in dumps({"x": ["a", "b"]})
    assert '"x": [true, false, null]' in dumps({"x": [True, False, None]})
    assert '"x": [0.5, 1e-07]' in dumps({"x": [0.5, 1e-07]})


def test_long_arrays_are_expanded_one_element_per_line():
    points = [[index, index * 1000] for index in range(12)]
    text = dumps({"points": points})
    assert '"points": [\n' in text
    assert "    [0, 0],\n" in text
    assert all(len(line) <= LINE_BUDGET for line in text.splitlines())


def test_the_budget_counts_the_key_and_the_indentation():
    # This array is exactly 80 characters inline, so whether it fits depends
    # on how much of the line the key and indentation already use.
    values = list(range(10, 30))
    assert len(str(values).replace("'", '"')) == 80
    fits = dumps({"k": values})
    assert '"k": [10, 11' in fits
    assert max(len(line) for line in fits.splitlines()) == LINE_BUDGET - 1

    too_long = dumps({"kkk": values})
    assert '"kkk": [\n' in too_long
    assert all(len(line) <= LINE_BUDGET for line in too_long.splitlines())


def test_arrays_of_objects_are_always_expanded():
    text = dumps({"elements": [{"type": "wire"}]})
    assert text == ('{\n  "elements": [\n    {\n      "type": "wire"\n    }\n  ]\n}\n')


def test_mixed_arrays_are_expanded():
    text = dumps({"x": [[0, 0], {"a": 1}]})
    assert '"x": [\n' in text


def test_deeply_nested_arrays_are_expanded():
    text = dumps({"x": [[[1, 2]]]})
    assert '"x": [\n' in text


def test_empty_containers_are_written_inline():
    assert dumps({"a": {}, "b": []}) == '{\n  "a": {},\n  "b": []\n}\n'
    assert dumps({}) == "{}\n"


def test_output_is_newline_terminated_without_trailing_whitespace():
    text = dumps({"points": [[0, 0], [6, 0]], "meta": {"grid": {"pitch": 0.5}}})
    assert text.endswith("}\n")
    assert not any(line != line.rstrip() for line in text.splitlines())


def test_non_ascii_is_written_verbatim():
    assert '"unit": "10kΩ"' in dumps({"unit": "10kΩ"})


def test_strings_are_escaped_as_json_requires():
    assert dumps({"tex": '\\SI{1}{\\ohm}\n"q"'}) == (
        '{\n  "tex": "\\\\SI{1}{\\\\ohm}\\n\\"q\\""\n}\n'
    )


def test_floats_keep_their_shortest_repr():
    assert '"v": 1e-07' in dumps({"v": 1e-07})
    assert '"v": 10000.0' in dumps({"v": 10000.0})
    assert '"v": 0.5' in dumps({"v": 0.5})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"a": [0, 4], "b": [[0, 0], [1, 1]]},
        {"deep": {"deeper": {"list": [[i, i] for i in range(30)]}}},
        {"mixed": [1, "two", None, True, 0.5, [], {}]},
        {"unicode": "µΩ", "escapes": '\t\\"'},
    ],
)
def test_output_is_valid_json_with_the_same_content(data: dict[str, Any]):
    assert json.loads(dumps(data)) == data
    assert loads(dumps(data)) == data


def test_rendering_is_deterministic():
    data = {"points": [[0, 0], [6, 0]], "nested": {"a": [1, 2, 3]}}
    assert dumps(data) == dumps(data)
