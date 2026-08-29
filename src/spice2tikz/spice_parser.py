"""SPICE netlist parsing: ngspice deck text to Netlist IR (roadmap §4.1-4.2).

Parsing runs in two stages, mirroring the roadmap:

*Stage 1* — :func:`assemble_lines` turns raw deck text into logical
:class:`SpiceLine` cards: the title line is split off, ``+`` continuations
are folded into the card above them, ``*`` full-line and ``;``/``$`` inline
comments are removed, blank lines vanish, and ``.end`` terminates the deck.
Every card keeps the 1-based line and column it started at, so diagnostics
can name a position (``docs/DESIGN.md`` §6).

*Stage 2* — :func:`parse_spice` maps those cards onto the dataclasses of
:mod:`spice2tikz.netlist_ir` following the ``docs/SPEC_IR.md`` §1 kind
taxonomy.  It never raises on a card it does not understand: unknown cards
become ``generic`` components with a warning, because a partial schematic
beats none (``docs/DESIGN.md`` §6).  The only unrecoverable input is a
``+`` continuation with nothing to continue, which raises
:class:`~spice2tikz._serde.IRError` (CLI exit code 1).

Case handling
-------------
SPICE is case-insensitive, so matching is done on lowercased text while
``Component.raw`` keeps the card verbatim as SPEC_IR §1 requires.  Node
names, model names, and subcircuit names are lowercased; a refdes keeps the
spelling it was written with (D9), and cross-references to one (a ``H``/``F``
card naming its controlling source) are resolved case-insensitively back to
that spelling.

Net classing
------------
``0``, ``gnd`` and ``gnd!`` are ``ground`` (``docs/DESIGN.md`` §7).  A net is
``supply`` when, within its scope, *all* of the following hold:

1. it is not itself ground-class;
2. exactly one voltage source has a terminal on it whose *other* terminal is
   a ground-class net;
3. that source carries a ``dc`` parameter which parsed to a non-zero number
   and carries no ``ac``/``sin``/``pulse``/``pwl``/``exp`` parameter, so it is
   a pure DC source (a ``DC 0`` source is the classic ammeter idiom, not a
   rail);
4. no *other* voltage source (independent, VCVS, or CCVS) touches the net,
   since a second one would fight over the node voltage.  A current source
   may touch it: injecting current into a rail does not define its voltage,
   and biasing a rail that way is routine.

``supply_voltage`` is then that DC value, negated when the net hangs on the
source's ``n`` terminal (``V1 0 vee 5`` makes ``vee`` a -5 V rail).  Every
other net is ``signal``.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from ._serde import IRError, warn
from .netlist_ir import (
    Component,
    Kind,
    ModelDef,
    Net,
    NetlistIR,
    NetlistMeta,
    Scope,
    SubcktDef,
    generic_pin_names,
    pin_order,
    required_pins,
)
from .quantity import Quantity, parse_quantity

DIALECT: Final = "ngspice"
"""Value written to ``meta.dialect`` (D10: ngspice is the first dialect)."""

GENERATOR: Final = "spice2tikz"
"""Value written to ``meta.generator``; deliberately carries no version, so
that a release never churns golden files (CLAUDE.md working rule 4)."""

INPUT_LABEL: Final = "<input>"
"""Stand-in file name in diagnostics when the caller named no source."""

GROUND_NET_NAMES: Final[frozenset[str]] = frozenset({"0", "gnd", "gnd!"})
"""Node names that are ground by name alone (``docs/DESIGN.md`` §7)."""

VOLT: Final = "V"
AMPERE: Final = "A"
SECOND: Final = "s"
HERTZ: Final = "Hz"
OHM: Final = "ohm"

PASSIVE_CARDS: Final[dict[str, tuple[Kind, str]]] = {
    "R": (Kind.RESISTOR, OHM),
    "C": (Kind.CAPACITOR, "F"),
    "L": (Kind.INDUCTOR, "H"),
}
"""Two-terminal passive card letters, with the unit their value defaults to."""

SOURCE_CARDS: Final[dict[str, tuple[Kind, str]]] = {
    "V": (Kind.VSOURCE, VOLT),
    "I": (Kind.ISOURCE, AMPERE),
}
"""Independent source card letters and the unit of their amplitudes."""

VOLTAGE_CONTROLLED_CARDS: Final[dict[str, tuple[Kind, str | None]]] = {
    "E": (Kind.VCVS, None),  # gain is V/V
    "G": (Kind.VCCS, None),  # transconductance is A/V, not in the unit table
}
"""``E``/``G`` cards: four nodes then a gain."""

CURRENT_CONTROLLED_CARDS: Final[dict[str, tuple[Kind, str | None]]] = {
    "H": (Kind.CCVS, OHM),  # transresistance
    "F": (Kind.CCCS, None),  # gain is A/A
}
"""``H``/``F`` cards: two nodes, a controlling source refdes, then a gain."""

BJT_MODEL_KINDS: Final[dict[str, Kind]] = {
    "npn": Kind.BJT_NPN,
    "pnp": Kind.BJT_PNP,
}
MOS_MODEL_KINDS: Final[dict[str, Kind]] = {
    "nmos": Kind.NMOS,
    "pmos": Kind.PMOS,
}
JFET_MODEL_KINDS: Final[dict[str, Kind]] = {
    "njf": Kind.NJFET,
    "njfet": Kind.NJFET,
    "pjf": Kind.PJFET,
    "pjfet": Kind.PJFET,
}
"""``.model`` type names that decide transistor polarity (SPEC_IR §1: the
polarity is baked into ``kind`` at parse time)."""

VOLTAGE_SOURCE_KINDS: Final[frozenset[Kind]] = frozenset(
    {Kind.VSOURCE, Kind.VCVS, Kind.CCVS}
)
"""Kinds that impose a voltage on a net, for the supply-inference rule.

Current sources are deliberately absent: they inject current rather than
setting a node's voltage, so one hanging off a rail does not stop that rail
being a rail.
"""

SIGNAL_PARAM_PREFIXES: Final[tuple[str, ...]] = (
    "ac_",
    "sin_",
    "pulse_",
    "pwl_",
    "exp_",
)
"""Parameter-name prefixes that mark a source as carrying a signal."""

BEHAVIOURAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"poly", "value", "table", "laplace", "freq"}
)
"""Controlled-source forms this parser does not model; they map to generic."""

IGNORED_DOT_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        ".ac",
        ".control",
        ".csparam",
        ".data",
        ".dc",
        ".disto",
        ".else",
        ".elseif",
        ".end",
        ".endc",
        ".enddata",
        ".endif",
        ".endl",
        ".four",
        ".fourier",
        ".func",
        ".global",
        ".ic",
        ".if",
        ".inc",
        ".include",
        ".lib",
        ".meas",
        ".measure",
        ".noise",
        ".nodeset",
        ".op",
        ".option",
        ".options",
        ".param",
        ".plot",
        ".print",
        ".probe",
        ".pz",
        ".save",
        ".sens",
        ".step",
        ".temp",
        ".tf",
        ".tran",
        ".width",
    }
)
"""Dot commands that carry no structure this IR models.

They are analysis directives, simulator settings, or file inclusion: expected
in a real deck, not anomalies, so they are dropped without a warning.  An
unrecognised dot command *does* warn, since that is a genuine surprise.
"""

PARAM_UNITS: Final[dict[str, str]] = {
    "z0": OHM,
    "td": SECOND,
    "freq": HERTZ,
    "f": HERTZ,
}
"""Default units for the few ``k=v`` parameters whose unit is unambiguous."""

CONTINUATION: Final = "+"
COMMENT: Final = "*"

_TOKEN_RE: Final = re.compile(r"\{[^{}]*\}|'[^']*'|\"[^\"]*\"|[(),=]|[^\s(),=]+")
"""Card tokenizer: braces/quotes stay whole, ``( ) , =`` are their own tokens."""

_PUNCTUATION: Final[frozenset[str]] = frozenset({"(", ")", ","})


@dataclass(frozen=True)
class _TransientForm:
    """Positional argument names and units of one transient source form."""

    names: tuple[str, ...]
    units: tuple[str | None, ...]


TRANSIENT_FORMS: Final[dict[str, _TransientForm]] = {
    "sin": _TransientForm(
        names=(
            "sin_offset",
            "sin_amplitude",
            "sin_freq",
            "sin_delay",
            "sin_theta",
            "sin_phase",
        ),
        units=(None, None, HERTZ, SECOND, None, None),
    ),
    "pulse": _TransientForm(
        names=(
            "pulse_initial",
            "pulse_pulsed",
            "pulse_delay",
            "pulse_rise",
            "pulse_fall",
            "pulse_width",
            "pulse_period",
        ),
        units=(None, None, SECOND, SECOND, SECOND, SECOND, SECOND),
    ),
    "exp": _TransientForm(
        names=(
            "exp_initial",
            "exp_pulsed",
            "exp_rise_delay",
            "exp_rise_tau",
            "exp_fall_delay",
            "exp_fall_tau",
        ),
        units=(None, None, SECOND, SECOND, SECOND, SECOND),
    ),
}
"""Transient source specifications, other than ``PWL`` whose arity is open.

A ``None`` unit means "the amplitude unit of the source" (volts or amperes)
for the level arguments, and "dimensionless" for phase and damping factor;
:meth:`_Parser._transient_params` resolves the difference by position.
"""

TRANSIENT_LEVEL_ARGS: Final[frozenset[str]] = frozenset(
    {
        "sin_offset",
        "sin_amplitude",
        "pulse_initial",
        "pulse_pulsed",
        "exp_initial",
        "exp_pulsed",
    }
)
"""Transient arguments measured in the source's own unit (volts or amperes)."""

SINE_ALIASES: Final[frozenset[str]] = frozenset({"sin", "sine"})


@dataclass(frozen=True)
class SpiceLine:
    """One logical SPICE card: continuations joined, comments removed.

    ``text`` keeps the original spelling (SPEC_IR §1 wants the card verbatim
    in ``Component.raw``); ``number`` and ``column`` are the 1-based position
    of the card's first character in the source file.  The deck's title line
    is returned as the first :class:`SpiceLine` with ``is_title`` set, since
    it is text rather than a card.
    """

    text: str
    number: int
    column: int = 1
    is_title: bool = False

    @property
    def head(self) -> str:
        """Return the lowercased first token, or ``""`` for an empty line."""
        parts = self.text.split(maxsplit=1)
        return parts[0].lower() if parts else ""


def assemble_lines(text: str, warnings: list[str] | None = None) -> list[SpiceLine]:
    """Assemble deck *text* into logical cards (roadmap §4.1, stage 1).

    Raises :class:`~spice2tikz._serde.IRError` when a ``+`` continuation has
    no card to continue; every other anomaly is appended to *warnings*.
    """
    return _assemble(text, None, warnings)


def parse_spice(
    text: str,
    *,
    source: str | None = None,
    warnings: list[str] | None = None,
) -> NetlistIR:
    """Parse deck *text* into a :class:`~spice2tikz.netlist_ir.NetlistIR`.

    *source* names the deck for diagnostics and provenance; when it is given,
    ``meta.source`` and ``meta.generator`` are both recorded.  Survivable
    problems are appended to *warnings*.
    """
    return _Parser(source, warnings).parse(text)


def load_spice(path: Path, warnings: list[str] | None = None) -> NetlistIR:
    """Read the deck at *path* and parse it.

    Bytes are decoded tolerantly: UTF-8 (with an optional BOM), falling back
    to latin-1, which cannot fail — a deck that is nearly all ASCII should
    never be rejected over one stray byte in a comment.  ``meta.source`` is
    the file *name*, not its path, so golden files stay machine-independent.
    """
    return parse_spice(_decode(path.read_bytes()), source=path.name, warnings=warnings)


# --- stage 1: line assembly -------------------------------------------------


def _decode(data: bytes) -> str:
    """Decode deck bytes as UTF-8 (BOM tolerated) or, failing that, latin-1."""
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8) :]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _location(source: str | None, number: int, column: int) -> str:
    """Render a ``file:line:column`` prefix for a diagnostic."""
    return f"{source or INPUT_LABEL}:{number}:{column}"


def _strip_inline_comment(text: str) -> str:
    """Remove a ``;`` or ``$`` inline comment from *text*.

    ``;`` starts a comment anywhere.  ``$`` only does so at the start of the
    card or after whitespace, so that a parameter reference such as ``$vdd``
    embedded in a token survives; ngspice applies the same restriction.
    """
    for index, char in enumerate(text):
        if char == ";":
            return text[:index]
        if char == "$" and (index == 0 or text[index - 1].isspace()):
            return text[:index]
    return text


def _title_of(line: str) -> str:
    """Return the deck title carried by the first physical *line*.

    A leading ``*`` is dropped: decks conventionally write the title as what
    looks like a comment, and SPEC_IR §5's own example (``* RC low-pass``)
    expects the title ``RC low-pass``.  Nothing else is stripped — a title is
    free text, so a ``;`` in it is part of the title.
    """
    stripped = line.strip()
    if stripped.startswith(COMMENT):
        stripped = stripped[1:].strip()
    return stripped


def _is_title_card(line: str) -> bool:
    """Return ``True`` when *line* is an explicit ``.title`` card."""
    stripped = line.strip().lower()
    return stripped == ".title" or stripped.startswith(".title ")


def _assemble(
    text: str,
    source: str | None,
    warnings: list[str] | None,
) -> list[SpiceLine]:
    """Implement :func:`assemble_lines` with a source name for diagnostics."""
    # A UTF-8 BOM survives `str.decode("utf-8")` as U+FEFF; CRLF and lone CR
    # both become LF so that line numbering matches what an editor shows.
    body = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    physical = body.split("\n")
    lines: list[SpiceLine] = []
    ignored_after_end = 0
    end_at: int | None = None

    for index, raw in enumerate(physical):
        number = index + 1
        if end_at is not None:
            if raw.strip():
                ignored_after_end += 1
            continue
        if index == 0 and not _is_title_card(raw):
            # The first line of a deck is always the title, even when it
            # looks like a card — unless the deck opens with `.title`, which
            # is handled as an ordinary dot command in stage 2.
            lines.append(
                SpiceLine(
                    text=_title_of(raw),
                    number=number,
                    column=_column_of(raw),
                    is_title=True,
                )
            )
            continue
        content = _strip_inline_comment(raw.strip())
        if content.startswith(COMMENT):
            continue
        content = content.strip()
        if not content:
            continue
        if content.startswith(CONTINUATION):
            _continue_line(lines, content, source, number, _column_of(raw))
            continue
        card = SpiceLine(text=content, number=number, column=_column_of(raw))
        if card.head == ".end":
            end_at = number
            continue
        lines.append(card)

    if end_at is not None and ignored_after_end:
        warn(
            warnings,
            f"{_location(source, end_at, 1)}: {ignored_after_end} line(s) after "
            "'.end' ignored",
        )
    return lines


def _column_of(raw: str) -> int:
    """Return the 1-based column of the first non-blank character of *raw*."""
    return len(raw) - len(raw.lstrip()) + 1


def _continue_line(
    lines: list[SpiceLine],
    content: str,
    source: str | None,
    number: int,
    column: int,
) -> None:
    """Fold a ``+`` continuation into the card above it.

    Full-line comments and blank lines between a card and its continuation
    are transparent, because they were dropped before this point.
    """
    if not lines or lines[-1].is_title:
        # Nothing to continue: the deck is malformed in a way that changes
        # what the circuit *is*, so this is the one fatal parse error.
        raise IRError(
            f"{_location(source, number, column)}: continuation line has no "
            "card to continue"
        )
    previous = lines[-1]
    tail = content[1:].strip()
    joined = previous.text if not tail else f"{previous.text} {tail}"
    lines[-1] = replace(previous, text=joined)


# --- tokenizing -------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Split card *text* into tokens, keeping ``( ) , =`` separate."""
    return _TOKEN_RE.findall(text)


def _split_arguments(tokens: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """Split *tokens* into positional words and ``key=value`` pairs.

    Because ``=`` is its own token, ``k=v``, ``k =v``, ``k= v`` and ``k = v``
    all parse identically.  Parentheses and commas are pure punctuation here
    and are dropped; keys are lowercased for the ``ParamMap``.
    """
    positional: list[str] = []
    keywords: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _PUNCTUATION or token == "=":
            index += 1
            continue
        if index + 1 < len(tokens) and tokens[index + 1] == "=":
            value = tokens[index + 2] if index + 2 < len(tokens) else ""
            keywords[token.lower()] = value
            index += 3
            continue
        positional.append(token)
        index += 1
    return positional, keywords


def _tail(text: str, skip: int) -> str:
    """Return *text* with its first *skip* whitespace-separated words removed.

    Used to recover a source specification (``AC 1``) verbatim from the card,
    which SPEC_IR §5 requires in ``value.raw``.
    """
    remainder = text
    for _ in range(skip):
        stripped = remainder.lstrip()
        space = re.search(r"\s", stripped)
        if space is None:
            return ""
        remainder = stripped[space.start() :]
    return remainder.strip()


def _is_number(token: str) -> bool:
    """Return ``True`` when *token* is a plain SPICE number."""
    return parse_quantity(token).value is not None


def _negated(quantity: Quantity) -> Quantity:
    """Return *quantity* with its sign flipped, ``raw`` text included."""
    raw = quantity.raw.strip()
    raw = raw[1:] if raw.startswith("-") else f"-{raw}"
    value = None if quantity.value is None else -quantity.value
    return Quantity(raw=raw, value=value, unit=quantity.unit)


def _quantities(keywords: dict[str, str]) -> dict[str, Quantity]:
    """Parse a ``key=value`` mapping into a ``ParamMap``."""
    return {
        key: parse_quantity(value, PARAM_UNITS.get(key))
        for key, value in keywords.items()
    }


# --- stage 2: cards to Netlist IR -------------------------------------------


class _Parser:
    """Builds a Netlist IR from assembled cards; one instance per deck."""

    def __init__(self, source: str | None, warnings: list[str] | None) -> None:
        self.source = source
        self.warnings = warnings
        self.ir = NetlistIR()
        self.title: str | None = None
        self.models: dict[str, ModelDef] = {}
        self.subckt_ports: dict[str, list[str]] = {}
        self.stack: list[SubcktDef] = []
        self.pending_controls: list[tuple[Scope, Component, str, SpiceLine]] = []

    # -- driver ------------------------------------------------------------

    def parse(self, text: str) -> NetlistIR:
        """Run both passes over *text* and return the finished document."""
        lines = _strip_control_blocks(_assemble(text, self.source, self.warnings))
        self._collect_definitions(lines)
        self.ir.models = self.models
        for line in lines:
            self._card(line)
        if self.stack:
            self._warn_at(lines[-1], "unterminated '.subckt' closed at end of deck")
            self.stack.clear()
        self._resolve_controls()
        for _, scope in self.ir.scopes():
            _classify_nets(scope)
        self.ir.meta = self._meta()
        return self.ir

    def _meta(self) -> NetlistMeta:
        """Build ``meta``: what the deck said, plus provenance when known.

        ``source``/``generator`` are recorded together and only when the
        caller named a source: they describe the *run*, not the deck, and a
        netlist parsed from an anonymous string has no provenance to record.
        """
        meta = NetlistMeta(title=self.title, dialect=DIALECT)
        if self.source is not None:
            meta.source = self.source
            meta.generator = GENERATOR
        return meta

    # -- diagnostics -------------------------------------------------------

    def _warn_at(self, line: SpiceLine, message: str) -> None:
        """Append a warning naming *line*'s position."""
        warn(
            self.warnings,
            f"{_location(self.source, line.number, line.column)}: {message}",
        )

    # -- pass 1: definitions -----------------------------------------------

    def _collect_definitions(self, lines: Sequence[SpiceLine]) -> None:
        """Collect ``.model`` and ``.subckt`` headers before building cards.

        A card may reference a model or subcircuit defined further down the
        deck, and transistor polarity has to be known when the card is built
        (SPEC_IR §1), so definitions are gathered in a first pass.
        """
        for line in lines:
            if line.is_title:
                continue
            head = line.head
            if head == ".model":
                self._model(line)
            elif head == ".subckt":
                name, ports, _ = _subckt_header(line)
                if name is not None:
                    self.subckt_ports[name] = ports

    def _model(self, line: SpiceLine) -> None:
        """Record one ``.model`` card."""
        positional, keywords = _split_arguments(_tokenize(line.text))
        if len(positional) < 3:  # ".model name type"
            self._warn_at(line, "'.model' needs a name and a type; card ignored")
            return
        name = positional[1].lower()
        if name in self.models:
            self._warn_at(line, f"'.model {name}' redefined; the later card wins")
        self.models[name] = ModelDef(
            type=positional[2].lower(),
            params=_quantities(keywords),
            raw=line.text,
        )

    # -- pass 2: cards -----------------------------------------------------

    @property
    def scope(self) -> Scope:
        """Return the scope cards currently land in."""
        return self.stack[-1] if self.stack else self.ir.circuit

    def _card(self, line: SpiceLine) -> None:
        """Dispatch one assembled card."""
        if line.is_title:
            self.title = line.text or None
            return
        head = line.head
        if head.startswith("."):
            self._dot_command(line, head)
            return
        self._element(line)

    def _dot_command(self, line: SpiceLine, head: str) -> None:
        """Handle a dot command, or warn that it is unknown."""
        if head == ".model":
            return  # already collected in pass 1
        if head == ".title":
            self.title = _tail(line.text, 1) or None
            return
        if head == ".subckt":
            self._begin_subckt(line)
            return
        if head in (".ends", ".endsub"):
            self._end_subckt(line)
            return
        if head in IGNORED_DOT_COMMANDS:
            return
        self._warn_at(line, f"unknown dot command {head!r} ignored")

    # -- subcircuits -------------------------------------------------------

    def _begin_subckt(self, line: SpiceLine) -> None:
        """Open a ``.subckt`` scope, registering its definition."""
        name, ports, params = _subckt_header(line)
        definition = SubcktDef(ports=ports, params=_quantities(params))
        if name is None:
            self._warn_at(line, "'.subckt' without a name; its body is discarded")
        else:
            if name in self.ir.subcircuits:
                self._warn_at(
                    line, f"'.subckt {name}' redefined; the later definition wins"
                )
            # SPEC_IR §1 keeps `subcircuits` flat, so a nested definition is
            # hoisted here rather than nested inside its parent.
            self.ir.subcircuits[name] = definition
        for port in ports:
            _register_net(definition, port)
        self.stack.append(definition)

    def _end_subckt(self, line: SpiceLine) -> None:
        """Close the innermost ``.subckt`` scope."""
        if not self.stack:
            self._warn_at(line, "'.ends' outside a '.subckt'; ignored")
            return
        self.stack.pop()

    # -- element cards -----------------------------------------------------

    def _element(self, line: SpiceLine) -> None:
        """Dispatch an element card on its leading letter."""
        tokens = _tokenize(line.text)
        letter = tokens[0][0].upper()
        if letter in PASSIVE_CARDS:
            self._passive(line, tokens, letter)
        elif letter == "D":
            self._diode(line, tokens)
        elif letter in SOURCE_CARDS:
            self._source(line, tokens, letter)
        elif letter == "Q":
            self._bjt(line, tokens)
        elif letter == "M":
            self._mos(line, tokens)
        elif letter == "J":
            self._jfet(line, tokens)
        elif letter in VOLTAGE_CONTROLLED_CARDS:
            self._voltage_controlled(line, tokens, letter)
        elif letter in CURRENT_CONTROLLED_CARDS:
            self._current_controlled(line, tokens, letter)
        elif letter == "S":
            self._switch(line, tokens)
        elif letter == "T":
            self._tline(line, tokens)
        elif letter == "X":
            self._instance(line, tokens)
        else:
            self._generic(line, tokens, f"unknown card letter {letter!r}")

    def _passive(self, line: SpiceLine, tokens: list[str], letter: str) -> None:
        """Parse an ``R``/``C``/``L`` card."""
        kind, unit = PASSIVE_CARDS[letter]
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 2, kind)
        if nodes is None:
            return
        value: Quantity | None = None
        if len(positional) > 3:
            value = parse_quantity(positional[3], unit)
        elif letter.lower() in keywords:
            # `R1 a b R=10k` is the parameterised spelling of the same value.
            value = parse_quantity(keywords.pop(letter.lower()), unit)
        self._add(line, positional[0], kind, nodes, value=value, keywords=keywords)

    def _diode(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse a ``D`` card: anode, cathode, model, optional area."""
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 2, Kind.DIODE)
        if nodes is None:
            return
        model = positional[3].lower() if len(positional) > 3 else None
        if len(positional) > 4:
            keywords.setdefault("area", positional[4])
        self._add(
            line, positional[0], Kind.DIODE, nodes, model=model, keywords=keywords
        )

    def _source(self, line: SpiceLine, tokens: list[str], letter: str) -> None:
        """Parse a ``V``/``I`` card, including its source specification."""
        kind, unit = SOURCE_CARDS[letter]
        positional, _ = _split_arguments(tokens[:3])
        nodes = self._nodes(line, positional, 2, kind)
        if nodes is None:
            return
        spec = _tail(line.text, 3)
        value = parse_quantity(spec, unit) if spec else None
        params = self._source_params(_tokenize(spec), unit)
        self._add(line, positional[0], kind, nodes, value=value, params=params)

    def _bjt(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse a ``Q`` card, resolving npn/pnp from its ``.model``."""
        positional, keywords = _split_arguments(tokens)
        rest = positional[4:]
        # `Q1 c b e mod` and `Q1 c b e s mod` differ only in whether a fourth
        # node precedes the model name.  A known model name settles it; short
        # of that, a name-like token in the model slot means four nodes.
        four_terminal = (
            len(rest) >= 2
            and rest[0].lower() not in self.models
            and not _is_number(rest[1])
        )
        node_count = 4 if four_terminal else 3
        model_index = node_count + 1
        nodes = self._nodes(line, positional, node_count, Kind.BJT_NPN)
        if nodes is None:
            return
        model = (
            positional[model_index].lower() if len(positional) > model_index else None
        )
        kind = self._polarity(line, model, BJT_MODEL_KINDS, Kind.BJT_NPN, "bjt")
        if len(positional) > model_index + 1:
            keywords.setdefault("area", positional[model_index + 1])
        self._add(line, positional[0], kind, nodes, model=model, keywords=keywords)

    def _mos(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse an ``M`` card, resolving nmos/pmos from its ``.model``."""
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 4, Kind.NMOS)
        if nodes is None:
            return
        model = positional[5].lower() if len(positional) > 5 else None
        kind = self._polarity(line, model, MOS_MODEL_KINDS, Kind.NMOS, "mos")
        self._add(line, positional[0], kind, nodes, model=model, keywords=keywords)

    def _jfet(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse a ``J`` card, resolving njfet/pjfet from its ``.model``."""
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 3, Kind.NJFET)
        if nodes is None:
            return
        model = positional[4].lower() if len(positional) > 4 else None
        kind = self._polarity(line, model, JFET_MODEL_KINDS, Kind.NJFET, "jfet")
        if len(positional) > 5:
            keywords.setdefault("area", positional[5])
        self._add(line, positional[0], kind, nodes, model=model, keywords=keywords)

    def _polarity(
        self,
        line: SpiceLine,
        model: str | None,
        table: dict[str, Kind],
        fallback: Kind,
        family: str,
    ) -> Kind:
        """Resolve a transistor's kind from its model type, or warn and guess."""
        definition = self.models.get(model or "")
        if definition is not None:
            kind = table.get(definition.type)
            if kind is not None:
                return kind
            self._warn_at(
                line,
                f"model {model!r} has type {definition.type!r}, which is not a "
                f"{family} type; assuming {fallback}",
            )
            return fallback
        if model is None:
            self._warn_at(line, f"card names no model; assuming {fallback}")
        else:
            self._warn_at(
                line, f"no '.model {model}' in this deck; assuming {fallback}"
            )
        return fallback

    def _voltage_controlled(
        self, line: SpiceLine, tokens: list[str], letter: str
    ) -> None:
        """Parse an ``E``/``G`` card: two output nodes, two control nodes, gain."""
        kind, unit = VOLTAGE_CONTROLLED_CARDS[letter]
        positional, keywords = _split_arguments(tokens)
        if len(positional) < 5 or positional[3].lower() in BEHAVIOURAL_KEYWORDS:
            self._generic(
                line,
                tokens,
                f"card {positional[0]!r} uses a behavioural or POLY form that "
                "this parser does not model",
            )
            return
        nodes = self._nodes(line, positional, 4, kind)
        if nodes is None:
            return
        value = parse_quantity(positional[5], unit) if len(positional) > 5 else None
        self._add(line, positional[0], kind, nodes, value=value, keywords=keywords)

    def _current_controlled(
        self, line: SpiceLine, tokens: list[str], letter: str
    ) -> None:
        """Parse an ``H``/``F`` card: two nodes, a controlling source, a gain."""
        kind, unit = CURRENT_CONTROLLED_CARDS[letter]
        positional, keywords = _split_arguments(tokens)
        if len(positional) < 4 or positional[3].lower() in BEHAVIOURAL_KEYWORDS:
            self._generic(
                line,
                tokens,
                f"card {positional[0]!r} names no controlling source, or uses a "
                "POLY form that this parser does not model",
            )
            return
        nodes = self._nodes(line, positional, 2, kind)
        if nodes is None:
            return
        value = parse_quantity(positional[4], unit) if len(positional) > 4 else None
        component = self._add(
            line,
            positional[0],
            kind,
            nodes,
            value=value,
            keywords=keywords,
            control=positional[3],
        )
        self.pending_controls.append((self.scope, component, positional[3], line))

    def _switch(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse an ``S`` card: two nodes, two control nodes, a model."""
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 4, Kind.SWITCH)
        if nodes is None:
            return
        model = positional[5].lower() if len(positional) > 5 else None
        self._add(
            line, positional[0], Kind.SWITCH, nodes, model=model, keywords=keywords
        )

    def _tline(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse a ``T`` card: two port pairs plus ``Z0``/``TD`` parameters."""
        positional, keywords = _split_arguments(tokens)
        nodes = self._nodes(line, positional, 4, Kind.TLINE)
        if nodes is None:
            return
        self._add(line, positional[0], Kind.TLINE, nodes, keywords=keywords)

    def _instance(self, line: SpiceLine, tokens: list[str]) -> None:
        """Parse an ``X`` card: nodes, then the subcircuit name, then params."""
        positional, keywords = _split_arguments(tokens)
        words = [word for word in positional if word.lower() != "params:"]
        if len(words) < 2:
            self._generic(line, tokens, "'X' card names no subcircuit")
            return
        subckt = words[-1].lower()
        nodes = words[1:-1]
        ports = self.subckt_ports.get(subckt)
        if ports is None:
            self._warn_at(
                line,
                f"no '.subckt {subckt}' in this deck; pins fall back to generic names",
            )
            names: tuple[str, ...] = generic_pin_names(len(nodes))
        elif len(ports) != len(nodes):
            self._warn_at(
                line,
                f"'.subckt {subckt}' has {len(ports)} port(s) but the instance "
                f"connects {len(nodes)}; pins fall back to generic names",
            )
            names = generic_pin_names(len(nodes))
        else:
            names = tuple(ports)
        component = Component(
            id=words[0],
            kind=Kind.SUBCIRCUIT,
            pins=self._connect(names, nodes),
            subckt=subckt,
            params=_quantities(keywords),
            raw=line.text,
        )
        self.scope.components.append(component)

    def _generic(self, line: SpiceLine, tokens: list[str], message: str) -> None:
        """Map an unmodelled card to a ``generic`` component and warn.

        Every positional word after the refdes is treated as a node, except a
        trailing word that names a known ``.model`` (kept as ``model``) or
        parses as a plain SPICE number (kept as ``value``).  DESIGN §6: a
        partial schematic beats none.
        """
        self._warn_at(line, f"{message}; mapped to a generic component")
        positional, keywords = _split_arguments(tokens)
        nodes = positional[1:]
        model: str | None = None
        value: Quantity | None = None
        if nodes and nodes[-1].lower() in self.models:
            model = nodes.pop().lower()
        elif nodes and _is_number(nodes[-1]):
            value = parse_quantity(nodes.pop())
        component = Component(
            id=positional[0],
            kind=Kind.GENERIC,
            pins=self._connect(generic_pin_names(len(nodes)), nodes),
            value=value,
            model=model,
            params=_quantities(keywords),
            raw=line.text,
        )
        self.scope.components.append(component)

    # -- component construction --------------------------------------------

    def _nodes(
        self,
        line: SpiceLine,
        positional: Sequence[str],
        count: int,
        kind: Kind,
    ) -> list[str] | None:
        """Return the *count* node words of a card, or ``None`` after a warning.

        A card too short to wire up cannot become the component it claims to
        be, so it degrades to ``generic`` instead of producing a component
        that violates SPEC_IR §4 invariant 1.
        """
        if len(positional) < count + 1:
            self._generic(
                line,
                _tokenize(line.text),
                f"card needs {count} node(s) for kind {kind}",
            )
            return None
        return list(positional[1 : count + 1])

    def _connect(self, pins: Sequence[str], nodes: Sequence[str]) -> dict[str, str]:
        """Map *pins* onto *nodes*, registering each net in the current scope."""
        scope = self.scope
        return {
            pin: _register_net(scope, node)
            for pin, node in zip(pins, nodes, strict=False)
        }

    def _add(
        self,
        line: SpiceLine,
        refdes: str,
        kind: Kind,
        nodes: Sequence[str],
        *,
        value: Quantity | None = None,
        model: str | None = None,
        control: str | None = None,
        params: dict[str, Quantity] | None = None,
        keywords: dict[str, str] | None = None,
    ) -> Component:
        """Build a component from resolved parts and append it to the scope."""
        merged = dict(params or {})
        merged.update(_quantities(keywords or {}))
        pins = pin_order(kind) if len(nodes) > len(required_pins(kind)) else None
        names = pins or required_pins(kind)
        component = Component(
            id=refdes,
            kind=kind,
            pins=self._connect(names, nodes),
            value=value,
            model=model,
            control=control,
            params=merged,
            raw=line.text,
        )
        self.scope.components.append(component)
        return component

    def _source_params(self, tokens: list[str], unit: str) -> dict[str, Quantity]:
        """Parse a ``V``/``I`` source specification into named parameters."""
        params: dict[str, Quantity] = {}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            lowered = token.lower()
            if token in _PUNCTUATION or token == "=":
                index += 1
                continue
            if lowered in ("dc", "ac"):
                index = self._dc_or_ac(tokens, index, lowered, unit, params)
                continue
            if lowered in SINE_ALIASES or lowered in TRANSIENT_FORMS:
                form = TRANSIENT_FORMS["sin" if lowered in SINE_ALIASES else lowered]
                args, index = _take_arguments(tokens, index + 1, len(form.names))
                params.update(_transient_params(form, args, unit))
                continue
            if lowered == "pwl":
                args, index = _take_arguments(tokens, index + 1, None)
                params.update(_pwl_params(args, unit))
                continue
            if _is_number(token) and "dc" not in params:
                # A bare number is the DC value: `V1 in 0 5`.
                params["dc"] = parse_quantity(token, unit)
                index += 1
                continue
            index += 1
        return params

    def _dc_or_ac(
        self,
        tokens: list[str],
        index: int,
        keyword: str,
        unit: str,
        params: dict[str, Quantity],
    ) -> int:
        """Consume a ``DC <v>`` or ``AC <mag> [phase]`` clause; return the cursor."""
        index += 1
        if index < len(tokens) and tokens[index] == "=":
            index += 1
        if index >= len(tokens) or not _is_number(tokens[index]):
            return index
        params[keyword] = parse_quantity(tokens[index], unit)
        index += 1
        if keyword == "ac" and index < len(tokens) and _is_number(tokens[index]):
            params["ac_phase"] = parse_quantity(tokens[index])
            index += 1
        return index

    # -- cross-references and net classing ---------------------------------

    def _resolve_controls(self) -> None:
        """Point every ``control`` at the actual refdes of its source.

        SPICE matches refdes case-insensitively, but SPEC_IR §4 invariant 3
        compares the strings, so ``H1 a b vs 50`` must resolve to the ``VS``
        the deck defines.
        """
        for scope, component, name, line in self.pending_controls:
            table = {other.id.casefold(): other.id for other in scope.components}
            resolved = table.get(name.casefold())
            if resolved is None:
                self._warn_at(
                    line,
                    f"controlling source {name!r} is not defined in this scope",
                )
                continue
            component.control = resolved


def _strip_control_blocks(lines: list[SpiceLine]) -> list[SpiceLine]:
    """Drop ``.control`` … ``.endc`` blocks.

    Their contents are ngspice *command scripts*, not netlist cards, so
    letting them through would turn every command into a generic component.
    """
    kept: list[SpiceLine] = []
    inside = False
    for line in lines:
        head = line.head
        if head == ".control":
            inside = True
            continue
        if head == ".endc":
            inside = False
            continue
        if not inside:
            kept.append(line)
    return kept


def _subckt_header(line: SpiceLine) -> tuple[str | None, list[str], dict[str, str]]:
    """Split a ``.subckt`` card into its name, port list, and parameters."""
    positional, keywords = _split_arguments(_tokenize(line.text))
    words = [word for word in positional if word.lower() != "params:"]
    if len(words) < 2:
        return None, [], keywords
    return words[1].lower(), [word.lower() for word in words[2:]], keywords


def _take_arguments(
    tokens: Sequence[str], index: int, limit: int | None
) -> tuple[list[str], int]:
    """Collect the arguments of a transient form, returning them and the cursor.

    ``SIN(0 1 1k)`` and ``SIN 0 1 1k`` are both legal ngspice; the first is
    delimited by parentheses, the second by running out of numbers.
    """
    args: list[str] = []
    if index < len(tokens) and tokens[index] == "(":
        index += 1
        while index < len(tokens) and tokens[index] != ")":
            if tokens[index] != ",":
                args.append(tokens[index])
            index += 1
        return args, min(index + 1, len(tokens))
    while index < len(tokens) and (limit is None or len(args) < limit):
        if not _is_number(tokens[index]):
            break
        args.append(tokens[index])
        index += 1
    return args, index


def _transient_params(
    form: _TransientForm, args: Sequence[str], unit: str
) -> dict[str, Quantity]:
    """Name the positional arguments of a transient form."""
    params: dict[str, Quantity] = {}
    for name, declared, text in zip(form.names, form.units, args, strict=False):
        resolved = unit if name in TRANSIENT_LEVEL_ARGS else declared
        params[name] = parse_quantity(text, resolved)
    return params


def _pwl_params(args: Sequence[str], unit: str) -> dict[str, Quantity]:
    """Name the open-ended ``PWL`` time/value pairs."""
    params: dict[str, Quantity] = {}
    for index, text in enumerate(args):
        point = index // 2 + 1
        if index % 2 == 0:
            params[f"pwl_t{point}"] = parse_quantity(text, SECOND)
        else:
            params[f"pwl_v{point}"] = parse_quantity(text, unit)
    return params


def _register_net(scope: Scope, name: str) -> str:
    """Return the net id of node *name* in *scope*, creating it if new.

    Node names are lowercased: ngspice matches them case-insensitively, so
    ``OUT`` and ``out`` must be one net, and one spelling has to win.
    """
    net_id = name.lower()
    if net_id not in scope.nets:
        scope.nets[net_id] = Net(name=net_id)
    return net_id


def _classify_nets(scope: Scope) -> None:
    """Assign every net of *scope* its ``ground``/``supply``/``signal`` class."""
    for net in scope.nets.values():
        if net.name in GROUND_NET_NAMES:
            net.net_class = "ground"
    for net_id, net in scope.nets.items():
        if net.net_class == "ground":
            continue
        supply = _supply_voltage(scope, net_id)
        if supply is not None:
            net.net_class = "supply"
            net.supply_voltage = supply


def _supply_voltage(scope: Scope, net_id: str) -> Quantity | None:
    """Return the rail voltage of *net_id*, or ``None`` when it is not a rail.

    The rule is stated in full in the module docstring; it is deliberately
    strict, because mislabelling a signal net as a supply would move it to
    the top of the drawing in the layout engine.
    """
    rails: list[Quantity] = []
    others = 0
    for component in scope.components:
        if component.kind not in VOLTAGE_SOURCE_KINDS:
            continue
        if net_id not in component.pins.values():
            continue
        rail = _rail_voltage(scope, component, net_id)
        if rail is None:
            others += 1
        else:
            rails.append(rail)
    if others or len(rails) != 1:
        return None
    return rails[0]


def _rail_voltage(scope: Scope, component: Component, net_id: str) -> Quantity | None:
    """Return the DC voltage *component* impresses on *net_id*, if it is a rail."""
    if component.kind is not Kind.VSOURCE:
        return None
    positive = component.pins.get("p")
    negative = component.pins.get("n")
    if positive == net_id:
        other = negative
        invert = False
    elif negative == net_id:
        other = positive
        invert = True
    else:  # pragma: no cover - the caller already matched a pin
        return None
    if other is None or other not in scope.nets:
        return None
    if scope.nets[other].net_class != "ground":
        return None
    if any(
        key == "ac" or key.startswith(SIGNAL_PARAM_PREFIXES) for key in component.params
    ):
        return None
    direct = component.params.get("dc")
    if direct is None or direct.value is None or direct.value == 0.0:
        # A `DC 0` source is the ammeter idiom, not a power rail.
        return None
    return _negated(direct) if invert else direct
