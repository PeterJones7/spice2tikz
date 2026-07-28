"""SPICE value parsing: scale suffixes, units, and the :class:`Quantity` type.

A SPICE number is an optional sign, a decimal mantissa, then *either* an
exponent (``1e3``) *or* one scale suffix (``1k``), optionally followed by
unit text that SPICE itself ignores (``10kohm``, ``1uF``).  Parsing is
case-insensitive, and the classic trap applies: ``m`` is *milli* while
``meg`` is *mega* (``docs/DESIGN.md`` §7).  Unparseable text — expressions,
parameter references, multi-token values such as ``AC 1`` — degrades to a
:class:`Quantity` carrying only ``raw`` (design decision D8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from ._serde import IRError, check_keys, require_mapping, require_number, require_str

SCALE_EXPONENTS: Final[dict[str, int]] = {
    "f": -15,
    "p": -12,
    "n": -9,
    "u": -6,
    "µ": -6,  # MICRO SIGN
    "μ": -6,  # GREEK SMALL LETTER MU
    "m": -3,
    "k": 3,
    "meg": 6,
    "g": 9,
    "t": 12,
}
"""SPICE scale suffixes as powers of ten, keyed by lowercase suffix."""

UNIT_ALIASES: Final[dict[str, str]] = {
    "ohm": "ohm",
    "ohms": "ohm",
    "ω": "ohm",  # GREEK SMALL LETTER OMEGA (lowercase of Ω and Ω)
    "f": "F",
    "farad": "F",
    "farads": "F",
    "h": "H",
    "henry": "H",
    "henries": "H",
    "henrys": "H",
    "v": "V",
    "volt": "V",
    "volts": "V",
    "a": "A",
    "amp": "A",
    "amps": "A",
    "ampere": "A",
    "amperes": "A",
    "s": "s",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "hz": "Hz",
    "hertz": "Hz",
    "w": "W",
    "watt": "W",
    "watts": "W",
}
"""Recognised unit spellings mapped to their canonical SI symbol."""

_SUFFIXES_LONGEST_FIRST: Final[tuple[str, ...]] = tuple(
    sorted(SCALE_EXPONENTS, key=len, reverse=True)
)
_MANTISSA_RE: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_EXPONENT_RE: Final = re.compile(r"[eE][+-]?\d+")


@dataclass(frozen=True)
class Quantity:
    """A SPICE value: the verbatim text plus, when parseable, number and unit.

    ``unit`` is the canonical SI symbol (``ohm``, ``F``, ``H``, ``V``, …) and
    may come from the text itself or from the caller's context, so it can be
    present only when ``value`` is.
    """

    raw: str
    value: float | None = None
    unit: str | None = None

    @property
    def parsed(self) -> bool:
        """Return ``True`` when the raw text yielded a numeric value."""
        return self.value is not None

    def to_json(self) -> dict[str, Any]:
        """Serialise to the JSON form of ``docs/SPEC_IR.md`` §1."""
        data: dict[str, Any] = {"raw": self.raw}
        if self.value is not None:
            data["value"] = self.value
        if self.unit is not None:
            data["unit"] = self.unit
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Quantity:
        """Load from the JSON form of ``docs/SPEC_IR.md`` §1."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("raw", "value", "unit"), location, warnings)
        if "raw" not in mapping:
            raise IRError(f"{location}: missing required field 'raw'")
        raw = require_str(mapping["raw"], f"{location}.raw")
        value: float | None = None
        if mapping.get("value") is not None:
            value = require_number(mapping["value"], f"{location}.value")
        unit: str | None = None
        if mapping.get("unit") is not None:
            unit = require_str(mapping["unit"], f"{location}.unit")
        return cls(raw=raw, value=value, unit=unit)


def canonical_unit(text: str) -> str | None:
    """Return the canonical SI symbol for *text*, or ``None`` if unrecognised."""
    return UNIT_ALIASES.get(text.strip().lower())


def parse_quantity(text: str, default_unit: str | None = None) -> Quantity:
    """Parse SPICE value *text* into a :class:`Quantity`.

    *default_unit* is the canonical unit implied by the context (a resistor's
    value is in ohms); a unit spelled out in the text wins over it.  Text
    that is not a plain SPICE number is returned verbatim, with neither
    ``value`` nor ``unit`` set.
    """
    body = text.strip()
    match = _MANTISSA_RE.match(body)
    if match is None:
        return Quantity(raw=text)
    mantissa = match.group(0)
    rest = body[match.end() :]

    exponent = 0
    exp_match = _EXPONENT_RE.match(rest)
    if exp_match is not None:
        mantissa += exp_match.group(0)
        rest = rest[exp_match.end() :]
    else:
        lowered = rest.lower()
        for suffix in _SUFFIXES_LONGEST_FIRST:
            if lowered.startswith(suffix):
                exponent = SCALE_EXPONENTS[suffix]
                rest = rest[len(suffix) :]
                break

    if rest and not rest.isalpha():
        # Trailing junk (expressions, embedded spaces): not a plain number.
        return Quantity(raw=text)

    unit = default_unit
    if rest:
        # Unrecognised unit text is ignored, as SPICE itself ignores it.
        unit = canonical_unit(rest) or default_unit

    # The mantissa already carries its own exponent when one was present, and
    # an exponent excludes a scale suffix, so the two never combine.
    numeric = mantissa if exponent == 0 else f"{mantissa}e{exponent}"
    try:
        value = float(numeric)
    except (ValueError, OverflowError):  # pragma: no cover - guarded by the regex
        return Quantity(raw=text)
    return Quantity(raw=text, value=value, unit=unit)
