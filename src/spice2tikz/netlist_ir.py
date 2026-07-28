"""Netlist IR: logical circuit connectivity (``docs/SPEC_IR.md`` §1).

The Netlist IR is the output of the SPICE parser and the input of the layout
engine: components with named pins, the nets those pins reference, and
subcircuit definitions.  It carries no geometry at all.

Dataclasses serialise through :meth:`NetlistIR.to_json` in the field order
given by the spec; dictionaries keep insertion order deliberately, so the
same input always produces byte-identical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, cast

from . import _serde
from ._serde import (
    IRError,
    check_header,
    check_keys,
    optional_field,
    require_choice,
    require_field,
    require_list,
    require_mapping,
    require_str,
)
from .quantity import Quantity

IR_KIND: Final = "netlist"


class Kind(str, Enum):
    """Component kinds of the ``docs/SPEC_IR.md`` §1 taxonomy."""

    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    INDUCTOR = "inductor"
    DIODE = "diode"
    VSOURCE = "vsource"
    ISOURCE = "isource"
    BJT_NPN = "bjt_npn"
    BJT_PNP = "bjt_pnp"
    NMOS = "nmos"
    PMOS = "pmos"
    NJFET = "njfet"
    PJFET = "pjfet"
    VCVS = "vcvs"
    VCCS = "vccs"
    CCVS = "ccvs"
    CCCS = "cccs"
    SWITCH = "switch"
    TLINE = "tline"
    SUBCIRCUIT = "subcircuit"
    GENERIC = "generic"

    def __str__(self) -> str:
        return self.value


PIN_NAMES: Final[dict[Kind, tuple[str, ...]]] = {
    Kind.RESISTOR: ("a", "b"),
    Kind.CAPACITOR: ("a", "b"),
    Kind.INDUCTOR: ("a", "b"),
    Kind.DIODE: ("a", "k"),
    Kind.VSOURCE: ("p", "n"),
    Kind.ISOURCE: ("p", "n"),
    Kind.BJT_NPN: ("c", "b", "e"),
    Kind.BJT_PNP: ("c", "b", "e"),
    Kind.NMOS: ("d", "g", "s", "b"),
    Kind.PMOS: ("d", "g", "s", "b"),
    Kind.NJFET: ("d", "g", "s"),
    Kind.PJFET: ("d", "g", "s"),
    Kind.VCVS: ("p", "n", "cp", "cn"),
    Kind.VCCS: ("p", "n", "cp", "cn"),
    Kind.CCVS: ("p", "n"),
    Kind.CCCS: ("p", "n"),
    Kind.SWITCH: ("p", "n", "cp", "cn"),
    Kind.TLINE: ("p1a", "p1b", "p2a", "p2b"),
    Kind.SUBCIRCUIT: (),
    Kind.GENERIC: (),
}
"""Mandatory pin names per kind, in SPICE card order."""

OPTIONAL_PIN_NAMES: Final[dict[Kind, tuple[str, ...]]] = {
    Kind.BJT_NPN: ("s",),
    Kind.BJT_PNP: ("s",),
}
"""Pins a kind may carry but need not (the BJT substrate)."""

DYNAMIC_PIN_KINDS: Final[frozenset[Kind]] = frozenset({Kind.SUBCIRCUIT, Kind.GENERIC})
"""Kinds whose pin names come from elsewhere: subcircuit ports, or ``1``..``n``."""

CONTROL_KINDS: Final[frozenset[Kind]] = frozenset({Kind.CCVS, Kind.CCCS})
"""Kinds that name a controlling voltage source in ``control``."""

NetClass = Literal["signal", "ground", "supply"]
NET_CLASSES: Final[tuple[str, ...]] = ("signal", "ground", "supply")


def required_pins(kind: Kind) -> tuple[str, ...]:
    """Return the pin names *kind* must have."""
    return PIN_NAMES[kind]


def optional_pins(kind: Kind) -> tuple[str, ...]:
    """Return the pin names *kind* may additionally have."""
    return OPTIONAL_PIN_NAMES.get(kind, ())


def pin_order(kind: Kind) -> tuple[str, ...]:
    """Return every pin name *kind* may have, mandatory ones first."""
    return required_pins(kind) + optional_pins(kind)


def generic_pin_names(count: int) -> tuple[str, ...]:
    """Return the pin names of a ``generic`` component with *count* pins."""
    return tuple(str(index) for index in range(1, count + 1))


@dataclass
class NetlistMeta:
    """Provenance of a netlist document; every field is optional."""

    title: str | None = None
    source: str | None = None
    generator: str | None = None
    dialect: str | None = None

    FIELDS: ClassVar[tuple[str, ...]] = ("title", "source", "generator", "dialect")

    def to_json(self) -> dict[str, Any]:
        """Serialise, omitting absent fields."""
        data: dict[str, Any] = {}
        for name in self.FIELDS:
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> NetlistMeta:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, cls.FIELDS, location, warnings)
        values: dict[str, str | None] = {}
        for name in cls.FIELDS:
            raw = optional_field(mapping, name, location)
            values[name] = (
                None if raw is None else require_str(raw, f"{location}.{name}")
            )
        return cls(**values)


@dataclass
class Net:
    """A node of the circuit; the net id in a scope equals its ``name``."""

    name: str
    net_class: NetClass = "signal"
    supply_voltage: Quantity | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialise (``net_class`` is spelled ``class`` in JSON)."""
        data: dict[str, Any] = {"name": self.name, "class": self.net_class}
        if self.supply_voltage is not None:
            data["supply_voltage"] = self.supply_voltage.to_json()
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Net:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("name", "class", "supply_voltage"), location, warnings)
        name = require_str(require_field(mapping, "name", location), f"{location}.name")
        net_class = require_choice(
            require_field(mapping, "class", location), NET_CLASSES, f"{location}.class"
        )
        raw_supply = optional_field(mapping, "supply_voltage", location)
        supply = (
            None
            if raw_supply is None
            else Quantity.from_json(raw_supply, f"{location}.supply_voltage", warnings)
        )
        return cls(
            name=name,
            net_class=cast(NetClass, net_class),
            supply_voltage=supply,
        )


@dataclass
class Component:
    """One element instance: refdes, kind, and pin-to-net mapping."""

    id: str
    kind: Kind
    pins: dict[str, str] = field(default_factory=dict)
    value: Quantity | None = None
    model: str | None = None
    subckt: str | None = None
    control: str | None = None
    params: dict[str, Quantity] = field(default_factory=dict)
    raw: str = ""

    def __post_init__(self) -> None:
        self.kind = Kind(self.kind)

    def to_json(self) -> dict[str, Any]:
        """Serialise in spec field order, omitting absent optional fields."""
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "pins": dict(self.pins),
        }
        if self.value is not None:
            data["value"] = self.value.to_json()
        if self.model is not None:
            data["model"] = self.model
        if self.subckt is not None:
            data["subckt"] = self.subckt
        if self.control is not None:
            data["control"] = self.control
        if self.params:
            data["params"] = {
                key: quantity.to_json() for key, quantity in self.params.items()
            }
        data["raw"] = self.raw
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Component:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(
            mapping,
            (
                "id",
                "kind",
                "pins",
                "value",
                "model",
                "subckt",
                "control",
                "params",
                "raw",
            ),
            location,
            warnings,
        )
        component_id = require_str(
            require_field(mapping, "id", location), f"{location}.id"
        )
        kind_text = require_str(
            require_field(mapping, "kind", location), f"{location}.kind"
        )
        try:
            kind = Kind(kind_text)
        except ValueError as exc:
            raise IRError(f"{location}.kind: unknown kind {kind_text!r}") from exc
        pins_mapping = require_mapping(
            require_field(mapping, "pins", location), f"{location}.pins"
        )
        pins = {
            pin: require_str(net, f"{location}.pins.{pin}")
            for pin, net in pins_mapping.items()
        }
        raw_value = optional_field(mapping, "value", location)
        value = (
            None
            if raw_value is None
            else Quantity.from_json(raw_value, f"{location}.value", warnings)
        )
        params = _params_from_json(
            optional_field(mapping, "params", location), f"{location}.params", warnings
        )
        return cls(
            id=component_id,
            kind=kind,
            pins=pins,
            value=value,
            model=_optional_str(mapping, "model", location),
            subckt=_optional_str(mapping, "subckt", location),
            control=_optional_str(mapping, "control", location),
            params=params,
            raw=require_str(require_field(mapping, "raw", location), f"{location}.raw"),
        )


@dataclass
class Scope:
    """A flat namespace of components and the nets they connect."""

    components: list[Component] = field(default_factory=list)
    nets: dict[str, Net] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialise components then nets."""
        return {
            "components": [component.to_json() for component in self.components],
            "nets": {net_id: net.to_json() for net_id, net in self.nets.items()},
        }

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> Scope:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("components", "nets"), location, warnings)
        components, nets = _scope_body_from_json(mapping, location, warnings)
        return cls(components=components, nets=nets)


@dataclass
class SubcktDef(Scope):
    """A ``.subckt`` definition: a scope plus its port order and parameters."""

    ports: list[str] = field(default_factory=list)
    params: dict[str, Quantity] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialise ports and params before the scope body."""
        data: dict[str, Any] = {"ports": list(self.ports)}
        if self.params:
            data["params"] = {
                key: quantity.to_json() for key, quantity in self.params.items()
            }
        data.update(Scope.to_json(self))
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> SubcktDef:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(
            mapping, ("ports", "params", "components", "nets"), location, warnings
        )
        ports_list = require_list(
            require_field(mapping, "ports", location), f"{location}.ports"
        )
        ports = [
            require_str(port, f"{location}.ports[{index}]")
            for index, port in enumerate(ports_list)
        ]
        params = _params_from_json(
            optional_field(mapping, "params", location), f"{location}.params", warnings
        )
        components, nets = _scope_body_from_json(mapping, location, warnings)
        return cls(components=components, nets=nets, ports=ports, params=params)


@dataclass
class ModelDef:
    """A ``.model`` card: device type, parameters, and the original text."""

    type: str
    params: dict[str, Quantity] = field(default_factory=dict)
    raw: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialise type, params, raw."""
        return {
            "type": self.type,
            "params": {key: value.to_json() for key, value in self.params.items()},
            "raw": self.raw,
        }

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        location: str,
        warnings: list[str] | None = None,
    ) -> ModelDef:
        """Load from JSON."""
        mapping = require_mapping(data, location)
        check_keys(mapping, ("type", "params", "raw"), location, warnings)
        return cls(
            type=require_str(
                require_field(mapping, "type", location), f"{location}.type"
            ),
            params=_params_from_json(
                optional_field(mapping, "params", location),
                f"{location}.params",
                warnings,
            ),
            raw=require_str(require_field(mapping, "raw", location), f"{location}.raw"),
        )


@dataclass
class NetlistIR:
    """A complete Netlist IR document."""

    meta: NetlistMeta = field(default_factory=NetlistMeta)
    circuit: Scope = field(default_factory=Scope)
    subcircuits: dict[str, SubcktDef] = field(default_factory=dict)
    models: dict[str, ModelDef] = field(default_factory=dict)

    IR: ClassVar[str] = IR_KIND
    VERSION: ClassVar[str] = "1.0"

    def to_json(self) -> dict[str, Any]:
        """Serialise the whole document in spec field order."""
        data: dict[str, Any] = {
            "ir": self.IR,
            "version": self.VERSION,
            "meta": self.meta.to_json(),
            "circuit": self.circuit.to_json(),
            "subcircuits": {
                name: definition.to_json()
                for name, definition in self.subcircuits.items()
            },
        }
        if self.models:
            data["models"] = {
                name: model.to_json() for name, model in self.models.items()
            }
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # noqa: ANN401
        warnings: list[str] | None = None,
    ) -> NetlistIR:
        """Load a whole document from JSON."""
        mapping = require_mapping(data, "<root>")
        check_header(mapping, IR_KIND, warnings)
        check_keys(
            mapping,
            ("ir", "version", "meta", "circuit", "subcircuits", "models"),
            "<root>",
            warnings,
        )
        raw_meta = optional_field(mapping, "meta", "<root>")
        meta = (
            NetlistMeta()
            if raw_meta is None
            else NetlistMeta.from_json(raw_meta, "meta", warnings)
        )
        circuit = Scope.from_json(
            require_field(mapping, "circuit", "<root>"), "circuit", warnings
        )
        subcircuits: dict[str, SubcktDef] = {}
        raw_subcircuits = optional_field(mapping, "subcircuits", "<root>")
        if raw_subcircuits is not None:
            for name, definition in require_mapping(
                raw_subcircuits, "subcircuits"
            ).items():
                subcircuits[name] = SubcktDef.from_json(
                    definition, f"subcircuits.{name}", warnings
                )
        models: dict[str, ModelDef] = {}
        raw_models = optional_field(mapping, "models", "<root>")
        if raw_models is not None:
            for name, model in require_mapping(raw_models, "models").items():
                models[name] = ModelDef.from_json(model, f"models.{name}", warnings)
        return cls(meta=meta, circuit=circuit, subcircuits=subcircuits, models=models)

    def scopes(self) -> list[tuple[str, Scope]]:
        """Return ``(location, scope)`` pairs: the circuit then each subcircuit."""
        scopes: list[tuple[str, Scope]] = [("circuit", self.circuit)]
        scopes.extend(
            (f"subcircuits.{name}", definition)
            for name, definition in self.subcircuits.items()
        )
        return scopes


def dumps(ir: NetlistIR) -> str:
    """Serialise *ir* as canonical JSON text."""
    return _serde.dumps(ir.to_json())


def loads(text: str, warnings: list[str] | None = None) -> NetlistIR:
    """Load a Netlist IR document from JSON *text*."""
    return NetlistIR.from_json(_serde.loads(text), warnings)


def load(path: Path, warnings: list[str] | None = None) -> NetlistIR:
    """Load a Netlist IR document from *path*."""
    return loads(path.read_text(encoding="utf-8"), warnings)


def dump(ir: NetlistIR, path: Path) -> None:
    """Write *ir* to *path* as canonical JSON text."""
    path.write_text(dumps(ir), encoding="utf-8")


def _scope_body_from_json(
    mapping: dict[str, Any],
    location: str,
    warnings: list[str] | None,
) -> tuple[list[Component], dict[str, Net]]:
    components_list = require_list(
        require_field(mapping, "components", location), f"{location}.components"
    )
    components = [
        Component.from_json(item, f"{location}.components[{index}]", warnings)
        for index, item in enumerate(components_list)
    ]
    nets: dict[str, Net] = {}
    nets_mapping = require_mapping(
        require_field(mapping, "nets", location), f"{location}.nets"
    )
    for net_id, net_data in nets_mapping.items():
        nets[net_id] = Net.from_json(net_data, f"{location}.nets[{net_id!r}]", warnings)
    return components, nets


def _params_from_json(
    data: Any,  # noqa: ANN401
    location: str,
    warnings: list[str] | None,
) -> dict[str, Quantity]:
    if data is None:
        return {}
    mapping = require_mapping(data, location)
    return {
        key: Quantity.from_json(value, f"{location}.{key}", warnings)
        for key, value in mapping.items()
    }


def _optional_str(mapping: dict[str, Any], key: str, location: str) -> str | None:
    raw = optional_field(mapping, key, location)
    return None if raw is None else require_str(raw, f"{location}.{key}")
