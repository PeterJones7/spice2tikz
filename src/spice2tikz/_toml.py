"""A very small TOML reader for ``--config`` files.

``tomllib`` only entered the standard library in Python 3.11, and the project
supports 3.10 with **zero runtime dependencies** (D1), so a third-party
back-port is not an option.  This module reads the deliberately narrow subset
of TOML a style config needs — tables, strings, booleans, numbers, and arrays —
and delegates to ``tomllib`` when the interpreter provides it, so that on 3.11+
users get the real parser and its error messages.

Deliberately unsupported (and reported as an error rather than mis-parsed):
inline tables, arrays of tables, multi-line strings, dates and times.
"""

from __future__ import annotations

import importlib
import re
from types import ModuleType
from typing import Any, Final, cast

__all__ = ["TomlError", "loads"]


class TomlError(ValueError):
    """Raised when a config file is not readable as the supported subset."""


def _stdlib_toml() -> ModuleType | None:
    """Return the standard library's ``tomllib`` (Python 3.11+), or ``None``.

    Imported dynamically rather than with a ``try: import tomllib``: the type
    checker is pinned to 3.10, where the module does not exist, so a static
    import would need an ignore comment that is wrong on every other version.
    """
    try:
        return importlib.import_module("tomllib")
    except ModuleNotFoundError:  # pragma: no cover - only on Python 3.10
        return None


_TOMLLIB: Final = _stdlib_toml()


_TABLE_RE: Final = re.compile(r"^\[([^\[\]]+)\]$")
_KEY_RE: Final = re.compile(r"^([A-Za-z0-9_.\-]+|\"[^\"]*\"|'[^']*')\s*=\s*(.*)$")
_INT_RE: Final = re.compile(r"^[+-]?(?:0|[1-9](?:_?\d)*)$")
_FLOAT_RE: Final = re.compile(
    r"^[+-]?(?:0|[1-9](?:_?\d)*)(?:\.\d(?:_?\d)*)?(?:[eE][+-]?\d+)?$"
)
_ESCAPES: Final[dict[str, str]] = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
}


def loads(text: str) -> dict[str, Any]:
    """Parse TOML *text* into nested dictionaries.

    Uses ``tomllib`` when available; otherwise falls back to the subset parser
    below.  Raises :class:`TomlError` on anything it cannot read.
    """
    if _TOMLLIB is not None:
        try:
            # TOMLDecodeError derives from ValueError, so no dynamic attribute
            # lookup is needed to catch it.
            return cast("dict[str, Any]", _TOMLLIB.loads(text))
        except ValueError as error:
            raise TomlError(str(error)) from error
    return _loads_subset(text)


def _loads_subset(text: str) -> dict[str, Any]:
    """Parse the supported TOML subset without ``tomllib`` (Python 3.10)."""
    root: dict[str, Any] = {}
    table = root
    for number, raw_line in _logical_lines(text):
        line = raw_line.strip()
        header = _TABLE_RE.match(line)
        if header is not None:
            table = _descend(root, header.group(1), number)
            continue
        pair = _KEY_RE.match(line)
        if pair is None:
            raise TomlError(f"line {number}: expected 'key = value', got {line!r}")
        key, value_text = pair.groups()
        key = _unquote_key(key)
        if key in table:
            raise TomlError(f"line {number}: duplicate key {key!r}")
        table[key] = _value(value_text.strip(), number)
    return root


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Return ``(line number, text)`` pairs, joining bracketed continuations.

    An array may span lines, so a line whose brackets are unbalanced absorbs the
    lines that follow it until they balance.  Comments are stripped first.
    """
    lines: list[tuple[int, str]] = []
    pending: str | None = None
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line and pending is None:
            continue
        if pending is None:
            pending, start = line, number
        else:
            pending = f"{pending} {line}"
        if pending.count("[") == pending.count("]"):
            lines.append((start, pending))
            pending = None
    if pending is not None:
        raise TomlError(f"line {start}: unterminated array")
    return lines


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment, respecting quoted strings."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "#":
            return line[:index]
    return line


def _descend(root: dict[str, Any], header: str, number: int) -> dict[str, Any]:
    """Return (creating if needed) the table named by a dotted *header*."""
    table = root
    for part in header.split("."):
        key = _unquote_key(part.strip())
        if not key:
            raise TomlError(f"line {number}: empty table name in [{header}]")
        child = table.setdefault(key, {})
        if not isinstance(child, dict):
            raise TomlError(f"line {number}: [{header}] redefines a value")
        table = child
    return table


def _unquote_key(key: str) -> str:
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return key[1:-1]
    return key


def _value(text: str, number: int) -> Any:  # noqa: ANN401 - TOML values are dynamic
    """Convert one TOML value literal to a Python object."""
    if not text:
        raise TomlError(f"line {number}: missing value")
    if text.startswith("["):
        return _array(text, number)
    if text[0] == '"':
        return _basic_string(text, number)
    if text[0] == "'":
        return _literal_string(text, number)
    if text in ("true", "false"):
        return text == "true"
    if _INT_RE.match(text):
        return int(text.replace("_", ""))
    if _FLOAT_RE.match(text):
        return float(text.replace("_", ""))
    raise TomlError(f"line {number}: unsupported value {text!r}")


def _array(text: str, number: int) -> list[Any]:
    if not text.endswith("]"):
        raise TomlError(f"line {number}: unterminated array")
    items = _split_items(text[1:-1], number)
    return [_value(item, number) for item in items]


def _split_items(body: str, number: int) -> list[str]:
    """Split an array body on top-level commas, ignoring commas in strings."""
    items: list[str] = []
    current = ""
    quote: str | None = None
    escaped = False
    depth = 0
    for char in body:
        if quote is not None:
            current += char
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current += char
        elif char == "[":
            depth += 1
            current += char
        elif char == "]":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    if quote is not None:
        raise TomlError(f"line {number}: unterminated string in array")
    tail = current.strip()
    if tail:
        items.append(tail)
    return items


def _basic_string(text: str, number: int) -> str:
    if len(text) < 2 or not text.endswith('"'):
        raise TomlError(f"line {number}: unterminated string {text!r}")
    body = text[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise TomlError(f"line {number}: trailing backslash in {text!r}")
        escape = body[index]
        if escape in _ESCAPES:
            out.append(_ESCAPES[escape])
            index += 1
        elif escape in "uU":
            width = 4 if escape == "u" else 8
            digits = body[index + 1 : index + 1 + width]
            if len(digits) != width:
                raise TomlError(f"line {number}: truncated \\{escape} escape")
            out.append(chr(int(digits, 16)))
            index += 1 + width
        else:
            raise TomlError(f"line {number}: unknown escape '\\{escape}'")
    return "".join(out)


def _literal_string(text: str, number: int) -> str:
    if len(text) < 2 or not text.endswith("'"):
        raise TomlError(f"line {number}: unterminated string {text!r}")
    return text[1:-1]
