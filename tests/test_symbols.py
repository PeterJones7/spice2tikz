"""Unit tests for symbol geometry and the rotation/mirror maths (roadmap §1.3)."""

from __future__ import annotations

import pytest

from spice2tikz._serde import IRError
from spice2tikz.netlist_ir import Kind, required_pins
from spice2tikz.symbols import (
    BUILTIN_SYMBOLS,
    SYMBOL_FOR_KIND,
    PinDef,
    SymbolDef,
    lookup_symbol,
    require_point,
    require_rotation,
    resolve_pins,
    rotated_size,
    transform_offset,
)

NMOS = BUILTIN_SYMBOLS["nmos"]

# Hand-computed pin positions for all eight orientations of the built-in nmos
# symbol, whose unrotated offsets are d(0,2) g(-2,0) s(0,-2) b(0,0) — the
# channel terminals directly above and below the origin, the gate due left, and
# the bulk on the origin, matching the directions of the circuitikz anchors.
# Mirror negates x first, then the rotation turns counterclockwise (y-up):
# 90° maps (x,y)→(-y,x); 180° maps (x,y)→(-x,-y); 270° maps (x,y)→(y,-x).
NMOS_ORIENTATIONS = {
    (0, False): {"d": (0, 2), "g": (-2, 0), "s": (0, -2), "b": (0, 0)},
    (90, False): {"d": (-2, 0), "g": (0, -2), "s": (2, 0), "b": (0, 0)},
    (180, False): {"d": (0, -2), "g": (2, 0), "s": (0, 2), "b": (0, 0)},
    (270, False): {"d": (2, 0), "g": (0, 2), "s": (-2, 0), "b": (0, 0)},
    (0, True): {"d": (0, 2), "g": (2, 0), "s": (0, -2), "b": (0, 0)},
    (90, True): {"d": (-2, 0), "g": (0, 2), "s": (2, 0), "b": (0, 0)},
    (180, True): {"d": (0, -2), "g": (-2, 0), "s": (0, 2), "b": (0, 0)},
    (270, True): {"d": (2, 0), "g": (0, -2), "s": (-2, 0), "b": (0, 0)},
}


@pytest.mark.parametrize(("rot", "mirror"), sorted(NMOS_ORIENTATIONS))
def test_resolve_pins_all_eight_orientations(rot: int, mirror: bool):
    assert resolve_pins(NMOS, (0, 0), rot, mirror) == NMOS_ORIENTATIONS[(rot, mirror)]


@pytest.mark.parametrize(("rot", "mirror"), sorted(NMOS_ORIENTATIONS))
def test_resolve_pins_translates_by_origin(rot: int, mirror: bool):
    expected = {
        name: (x + 10, y + 6)
        for name, (x, y) in NMOS_ORIENTATIONS[(rot, mirror)].items()
    }
    assert resolve_pins(NMOS, (10, 6), rot, mirror) == expected


def test_the_eight_orientations_are_all_distinct():
    resolved = {
        tuple(sorted(resolve_pins(NMOS, (0, 0), rot, mirror).items()))
        for rot, mirror in NMOS_ORIENTATIONS
    }
    assert len(resolved) == 8


def test_resolve_pins_preserves_symbol_pin_order():
    assert list(resolve_pins(NMOS, (0, 0), 90, mirror=True)) == list(NMOS.pins)


def test_mirror_is_applied_before_rotation():
    # The gate sits at (-2, 0): mirroring first then turning 90° puts it at
    # (0, 2), whereas turning first and mirroring afterwards would give
    # (0, -2).  The spec mandates the former order.
    gate = NMOS.pins["g"].offset
    assert transform_offset(gate, 90, mirror=True) == (0, 2)
    turned_x, turned_y = transform_offset(gate, 90, mirror=False)
    mirrored_afterwards = (-turned_x, turned_y)
    assert mirrored_afterwards == (0, -2)
    assert resolve_pins(NMOS, (0, 0), 90, mirror=True)["g"] == (0, 2)


@pytest.mark.parametrize(
    ("offset", "rot", "expected"),
    [
        ((3, 1), 0, (3, 1)),
        ((3, 1), 90, (-1, 3)),
        ((3, 1), 180, (-3, -1)),
        ((3, 1), 270, (1, -3)),
        ((0, 0), 90, (0, 0)),
    ],
)
def test_transform_offset_rotations(
    offset: tuple[int, int], rot: int, expected: tuple[int, int]
):
    assert transform_offset(offset, rot, mirror=False) == expected


def test_four_rotations_return_to_the_start():
    offset = (3, 1)
    turned = offset
    for _ in range(4):
        turned = transform_offset(turned, 90, mirror=False)
    assert turned == offset


def test_double_mirror_is_the_identity():
    assert transform_offset(transform_offset((3, 1), 0, True), 0, True) == (3, 1)


def test_invalid_rotation_is_rejected():
    with pytest.raises(ValueError, match="rotation must be one of"):
        transform_offset((1, 1), 45, mirror=False)
    with pytest.raises(ValueError, match="rotation must be one of"):
        rotated_size((4, 2), 45)


def test_rotated_size_swaps_axes_for_quarter_turns():
    assert rotated_size((4, 2), 0) == (4, 2)
    assert rotated_size((4, 2), 90) == (2, 4)
    assert rotated_size((4, 2), 180) == (4, 2)
    assert rotated_size((4, 2), 270) == (2, 4)


# --- built-in library -------------------------------------------------------


def test_builtins_cover_the_promised_symbols():
    assert set(BUILTIN_SYMBOLS) == {
        "nmos",
        "pmos",
        "npn",
        "pnp",
        "njfet",
        "pjfet",
    }


def test_builtin_pin_names_match_the_kind_taxonomy():
    assert SYMBOL_FOR_KIND == {
        Kind.NMOS: "nmos",
        Kind.PMOS: "pmos",
        Kind.BJT_NPN: "npn",
        Kind.BJT_PNP: "pnp",
        Kind.NJFET: "njfet",
        Kind.PJFET: "pjfet",
    }
    for kind, symbol_name in SYMBOL_FOR_KIND.items():
        symbol = BUILTIN_SYMBOLS[symbol_name]
        assert set(symbol.pins) == set(required_pins(kind))


def test_mos_symbols_have_all_four_terminals():
    for name in ("nmos", "pmos"):
        assert set(BUILTIN_SYMBOLS[name].pins) == {"d", "g", "s", "b"}
        assert BUILTIN_SYMBOLS[name].base == name


def test_bjt_symbols_have_three_terminals():
    for name in ("npn", "pnp"):
        assert set(BUILTIN_SYMBOLS[name].pins) == {"c", "b", "e"}
        assert BUILTIN_SYMBOLS[name].base == name


def test_builtin_pins_lie_inside_the_bounding_box():
    for symbol in BUILTIN_SYMBOLS.values():
        width, height = symbol.size
        for pin in symbol.pins.values():
            assert abs(pin.offset[0]) * 2 <= width
            assert abs(pin.offset[1]) * 2 <= height


def test_lookup_prefers_the_file_library():
    override = SymbolDef(size=(2, 2), pins={"g": PinDef(offset=(-1, 0))})
    assert lookup_symbol("nmos", {"nmos": override}) is override
    assert lookup_symbol("nmos") is BUILTIN_SYMBOLS["nmos"]
    assert lookup_symbol("nmos", {}) is BUILTIN_SYMBOLS["nmos"]
    assert lookup_symbol("nonesuch") is None
    assert lookup_symbol("nonesuch", {}) is None


# --- serialisation ----------------------------------------------------------


def test_symbol_json_round_trip():
    symbol = SymbolDef(
        size=(4, 4),
        pins={"a": PinDef(offset=(-2, 0), label="A"), "b": PinDef(offset=(2, 0))},
        base="mything",
    )
    data = symbol.to_json()
    assert list(data) == ["base", "size", "pins"]
    assert data["pins"]["b"] == {"offset": [2, 0]}
    assert SymbolDef.from_json(data, "symbols.mything") == symbol


def test_symbol_json_omits_absent_base():
    symbol = SymbolDef(size=(2, 2), pins={})
    assert symbol.to_json() == {"size": [2, 2], "pins": {}}


def test_builtin_symbols_round_trip():
    for name, symbol in BUILTIN_SYMBOLS.items():
        assert SymbolDef.from_json(symbol.to_json(), f"symbols.{name}") == symbol


def test_require_point_normalises_whole_floats():
    assert require_point([4.0, -2], "p") == (4, -2)


def test_require_point_keeps_fractional_values_for_the_validator():
    assert require_point([1.5, 2], "p") == (1.5, 2)


@pytest.mark.parametrize("value", [[1], [1, 2, 3], "1,2", [1, "2"], [1, True], 4])
def test_require_point_rejects_bad_input(value: object):
    with pytest.raises(IRError):
        require_point(value, "p")


@pytest.mark.parametrize("value", [0, 90, 180, 270, 90.0])
def test_require_rotation_accepts_quarter_turns(value: object):
    assert require_rotation(value, "rot") in (0, 90, 180, 270)


@pytest.mark.parametrize("value", [45, -90, 360, 1.5, "90", True])
def test_require_rotation_rejects_anything_else(value: object):
    with pytest.raises(IRError):
        require_rotation(value, "rot")


# --- polarity and the real circuitikz geometry ------------------------------

CHANNEL_TOP = {
    # circuitikz draws every p-type device the other way up from its n-type
    # counterpart, because that is how each is conventionally drawn: a PMOS
    # source sits above its drain, a PNP emitter above its collector.  These
    # are the anchors that appear at the TOP of each shape, read off a
    # compiled rendering, not guessed.
    "nmos": "d",
    "pmos": "s",
    "npn": "c",
    "pnp": "e",
    "njfet": "d",
    "pjfet": "s",
}


@pytest.mark.parametrize(("name", "top"), sorted(CHANNEL_TOP.items()))
def test_channel_terminals_follow_the_circuitikz_shape(name: str, top: str):
    """The declared offsets must match where circuitikz really puts the anchors.

    The emitter draws a lead from each anchor to the pin position declared
    here, so an offset on the wrong side of the body produces a lead that
    doubles back across the symbol — which is exactly what happened while
    ``pmos`` reused the ``nmos`` offsets.
    """
    symbol = BUILTIN_SYMBOLS[name]
    bottom = {"d": "s", "s": "d", "c": "e", "e": "c"}[top]
    assert symbol.pins[top].offset[1] > 0, f"{name}: {top} should be drawn on top"
    assert symbol.pins[bottom].offset[1] < 0, f"{name}: {bottom} should be below"
    assert symbol.pins["g" if "g" in symbol.pins else "b"].offset[0] < 0


@pytest.mark.parametrize(
    ("n_type", "p_type"), [("nmos", "pmos"), ("npn", "pnp"), ("njfet", "pjfet")]
)
def test_a_p_type_is_the_n_type_turned_over(n_type: str, p_type: str):
    """The two polarities carry the same terminals, the opposite way up."""
    n_pins = BUILTIN_SYMBOLS[n_type].pins
    p_pins = BUILTIN_SYMBOLS[p_type].pins
    assert set(n_pins) == set(p_pins)
    channel = [pin for pin in n_pins if n_pins[pin].offset[1] != 0]
    assert len(channel) == 2
    for pin in channel:
        assert n_pins[pin].offset[1] == -p_pins[pin].offset[1], pin
    control = [pin for pin in n_pins if n_pins[pin].offset[0] < 0]
    for pin in control:
        assert n_pins[pin].offset == p_pins[pin].offset, pin
