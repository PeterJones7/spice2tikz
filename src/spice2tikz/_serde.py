"""Shared JSON helpers for the two intermediate representations.

Both IRs are UTF-8 JSON with a deliberate field order (see
``docs/SPEC_IR.md``): optional fields are *omitted* rather than serialised
as ``null``, and every file self-identifies with ``ir`` and ``version``
keys.  This module holds the primitives shared by
:mod:`spice2tikz.netlist_ir` and :mod:`spice2tikz.schematic_ir` and
deliberately depends on nothing else in the package.

Loader functions take an optional ``warnings`` list: human-readable
messages (unknown fields, newer minor versions) are appended to it when
one is supplied, so that the caller decides how to report them.  Anything
that makes a file unloadable raises :class:`IRError` instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Final

IR_VERSION: Final = "1.0"
"""IR version implemented by this package (``major.minor``)."""

INDENT: Final = 2
"""Spaces per nesting level in canonical IR JSON."""

LINE_BUDGET: Final = 88
"""Column limit an array must fit within to be written on one line."""


class IRError(ValueError):
    """Raised when JSON input cannot be loaded as an IR document.

    Maps to CLI exit code 1 (input parse error).
    """


def dumps(data: Mapping[str, Any]) -> str:
    """Serialise *data* as canonical IR JSON text, newline-terminated.

    The canonical form (``docs/SPEC_IR.md`` §0) is two-space indented, keeps
    the field order it is given, escapes nothing that UTF-8 can carry, and
    puts one field per line so that diffs stay line-oriented.  Arrays of
    scalars — and arrays of arrays of scalars — are written on one line when
    they fit within :data:`LINE_BUDGET` columns, which keeps coordinate pairs
    (``[0, 4]``) and short point lists (``[[0, 0], [6, 0]]``) readable for
    hand editing.  The result is a pure function of the data: the same
    content always produces byte-identical text.
    """
    return _render(data, 0, 0) + "\n"


def _render(value: Any, indent: int, column: int) -> str:  # noqa: ANN401
    """Render *value*, whose first character sits at *column*.

    *indent* is the indentation of the line the value starts on, used to line
    up its closing bracket.
    """
    if isinstance(value, dict):
        return _render_object(value, indent)
    if isinstance(value, list):
        return _render_array(value, indent, column)
    return _scalar(value)


def _render_object(data: Mapping[str, Any], indent: int) -> str:
    if not data:
        return "{}"
    pad = " " * (indent + INDENT)
    lines = []
    for key, item in data.items():
        key_text = _scalar(str(key))
        prefix = f"{pad}{key_text}: "
        lines.append(prefix + _render(item, indent + INDENT, len(prefix)))
    return "{\n" + ",\n".join(lines) + "\n" + " " * indent + "}"


def _render_array(items: list[Any], indent: int, column: int) -> str:
    if not items:
        return "[]"
    inline = _inline_array(items)
    if inline is not None and column + len(inline) <= LINE_BUDGET:
        return inline
    pad = " " * (indent + INDENT)
    body = ",\n".join(pad + _render(item, indent + INDENT, len(pad)) for item in items)
    return "[\n" + body + "\n" + " " * indent + "]"


def _inline_array(items: list[Any]) -> str | None:
    """Return the one-line form of *items*, or ``None`` if it must be expanded.

    Only two shapes are inlined: an array of scalars, and an array whose
    elements are themselves arrays of scalars (a list of points).
    """
    parts: list[str] = []
    for item in items:
        if _is_scalar(item):
            parts.append(_scalar(item))
        elif isinstance(item, list) and all(_is_scalar(sub) for sub in item):
            parts.append("[" + ", ".join(_scalar(sub) for sub in item) + "]")
        else:
            return None
    return "[" + ", ".join(parts) + "]"


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text: str) -> dict[str, Any]:
    """Parse *text* as a JSON object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IRError(f"invalid JSON: {exc}") from exc
    return require_mapping(data, "<root>")


def warn(warnings: list[str] | None, message: str) -> None:
    """Append *message* to *warnings* when a sink was supplied."""
    if warnings is not None:
        warnings.append(message)


def require_mapping(value: Any, location: str) -> dict[str, Any]:  # noqa: ANN401
    """Return *value* as a JSON object."""
    if not isinstance(value, dict):
        raise IRError(f"{location}: expected an object, got {_typename(value)}")
    return value


def require_list(value: Any, location: str) -> list[Any]:  # noqa: ANN401
    """Return *value* as a JSON array."""
    if not isinstance(value, list):
        raise IRError(f"{location}: expected an array, got {_typename(value)}")
    return value


def require_str(value: Any, location: str) -> str:  # noqa: ANN401
    """Return *value* as a string."""
    if not isinstance(value, str):
        raise IRError(f"{location}: expected a string, got {_typename(value)}")
    return value


def require_bool(value: Any, location: str) -> bool:  # noqa: ANN401
    """Return *value* as a boolean."""
    if not isinstance(value, bool):
        raise IRError(f"{location}: expected true or false, got {_typename(value)}")
    return value


def require_number(value: Any, location: str) -> float:  # noqa: ANN401
    """Return *value* as a number (booleans are rejected)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IRError(f"{location}: expected a number, got {_typename(value)}")
    return float(value)


def require_choice(value: Any, choices: tuple[str, ...], location: str) -> str:  # noqa: ANN401
    """Return *value* as one of *choices*."""
    text = require_str(value, location)
    if text not in choices:
        allowed = ", ".join(repr(choice) for choice in choices)
        raise IRError(f"{location}: expected one of {allowed}, got {text!r}")
    return text


def require_field(data: Mapping[str, Any], key: str, location: str) -> Any:  # noqa: ANN401
    """Return the mandatory field *key* of *data*."""
    if key not in data:
        raise IRError(f"{location}: missing required field {key!r}")
    return data[key]


def optional_field(data: Mapping[str, Any], key: str, location: str) -> Any | None:  # noqa: ANN401
    """Return the optional field *key* of *data*, or ``None`` when absent.

    Explicit ``null`` is an error: optional fields are omitted (spec §0).
    """
    if key not in data:
        return None
    if data[key] is None:
        raise IRError(f"{location}: field {key!r} must be omitted rather than null")
    return data[key]


def check_keys(
    data: Mapping[str, Any],
    known: Iterable[str],
    location: str,
    warnings: list[str] | None,
) -> None:
    """Warn about fields of *data* that this version does not know."""
    allowed = set(known)
    for key in data:
        if key not in allowed:
            warn(warnings, f"{location}: unknown field {key!r} ignored")


def check_header(
    data: Mapping[str, Any],
    expected_ir: str,
    warnings: list[str] | None,
) -> None:
    """Check the ``ir`` and ``version`` fields of an IR document.

    Rejects a different document kind or an unknown major version; warns
    when the file was written by a newer minor version of the spec.
    """
    kind = require_str(require_field(data, "ir", "<root>"), "<root>.ir")
    if kind != expected_ir:
        raise IRError(f"<root>.ir: expected {expected_ir!r}, got {kind!r}")
    version = require_str(require_field(data, "version", "<root>"), "<root>.version")
    major, minor = _parse_version(version)
    expected_major, expected_minor = _parse_version(IR_VERSION)
    if major != expected_major:
        raise IRError(
            f"<root>.version: unsupported major version {version!r} "
            f"(this build implements {IR_VERSION})"
        )
    if minor > expected_minor:
        warn(
            warnings,
            f"<root>.version: file uses IR {version} but this build implements "
            f"{IR_VERSION}; unknown additions are ignored",
        )


def detect_ir_kind(data: Mapping[str, Any]) -> str:
    """Return the ``ir`` field of a loaded document (``netlist``/``schematic``)."""
    return require_str(require_field(data, "ir", "<root>"), "<root>.ir")


def _parse_version(version: str) -> tuple[int, int]:
    parts = version.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise IRError(f"<root>.version: expected 'major.minor', got {version!r}")
    return int(parts[0]), int(parts[1])


def _typename(value: Any) -> str:  # noqa: ANN401
    if value is None:
        return "null"
    return {bool: "boolean", int: "number", float: "number", str: "string"}.get(
        type(value), type(value).__name__
    )
