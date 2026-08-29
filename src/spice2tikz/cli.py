r"""Command-line interface for spice2tikz.

A thin shell around the library internals (design decision D3).  The pipeline it
drives is the one in ``docs/DESIGN.md`` §2::

    SPICE ──parse──► Netlist IR ──layout──► Schematic IR ──emit──► circuitikz
    .asc  ──import──────────────────────► Schematic IR ──emit──► circuitikz
    IR JSON ──load──► either IR (hand-edit / pipeline re-entry)

Stages that a later roadmap section still owns report "not implemented yet"
rather than failing obscurely.

stdout carries the generated LaTeX and nothing else; every diagnostic goes to
stderr.  Output is written through ``sys.stdout.buffer`` so that the bytes are
identical on every platform — text-mode stdout would translate ``\n`` to
``\r\n`` on Windows and break the determinism promise (CLAUDE.md rule 4).

Exit codes: 0 ok, 1 input parse error, 2 validation error, 3 internal.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from . import __version__, _serde, _toml, netlist_ir, schematic_ir, spice_parser
from ._serde import IRError
from .emit.circuitikz import emit
from .netlist_ir import NetlistIR
from .schematic_ir import (
    COMPONENT_VARIANTS,
    INDUCTOR_VARIANTS,
    SchematicIR,
    StyleDefaults,
)
from .validate import Severity, count_by_severity, format_finding, validate

PROG = "spice2tikz"

EXIT_OK: Final = 0
EXIT_INPUT_ERROR: Final = 1
EXIT_VALIDATION_ERROR: Final = 2
EXIT_INTERNAL_ERROR: Final = 3

FORMATS: Final[tuple[str, ...]] = ("spice", "asc", "netlist-ir", "schematic-ir")
EXTENSION_FORMATS: Final[dict[str, str]] = {
    ".sp": "spice",
    ".cir": "spice",
    ".net": "spice",
    ".asc": "asc",
}
IR_FORMATS: Final[dict[str, str]] = {
    "netlist": "netlist-ir",
    "schematic": "schematic-ir",
}
NOT_YET_IMPLEMENTED: Final[dict[str, str]] = {
    "asc": "LTspice .asc import is not implemented yet (roadmap section 3)",
}
NO_LAYOUT_ENGINE: Final = (
    "layout not yet implemented (roadmap section 5); use --dump-netlist to write "
    "the Netlist IR instead"
)

BOOL_WORDS: Final[dict[str, bool]] = {
    "true": True,
    "yes": True,
    "on": True,
    "1": True,
    "false": False,
    "no": False,
    "off": False,
    "0": False,
}
STYLE_CHOICES: Final[dict[str, tuple[str, ...]]] = {
    "resistor_variant": COMPONENT_VARIANTS,
    "inductor_variant": INDUCTOR_VARIANTS,
}
STYLE_FLAGS: Final[tuple[str, ...]] = ("siunitx", "label_refs")
STYLE_KEYS: Final[tuple[str, ...]] = (
    *STYLE_CHOICES,
    *STYLE_FLAGS,
    "extra_preamble",
)


class UsageError(Exception):
    """A command-line or config-file mistake; reported as an input error."""


def build_parser() -> ArgumentParser:
    """Build the argument parser for the ``spice2tikz`` command."""
    parser = ArgumentParser(
        prog=PROG,
        description="Convert SPICE netlists and LTspice schematics to CircuiTikZ.",
    )
    parser.add_argument(
        "input",
        metavar="INPUT",
        nargs="?",
        help="circuit description to read (.sp/.cir/.net, .asc, or IR .json)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write the generated LaTeX to FILE instead of stdout",
    )
    parser.add_argument(
        "--from",
        dest="from_format",
        choices=FORMATS,
        help="force the input format instead of deducing it from the extension",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="wrap the output in a compilable standalone document",
    )
    parser.add_argument(
        "--dump-netlist",
        metavar="FILE",
        help="also write the Netlist IR as JSON",
    )
    parser.add_argument(
        "--dump-layout",
        metavar="FILE",
        help="also write the Schematic IR as JSON (the hand-tweak escape hatch)",
    )
    parser.add_argument(
        "--style",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        dest="style",
        help=("override a style default; repeatable. Keys: " + ", ".join(STYLE_KEYS)),
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="TOML file whose [style] table supplies style defaults",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="report errors only",
    )
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="report progress as well as findings",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="show the program version and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input is None:
        parser.print_usage(sys.stderr)
        _report(f"{PROG}: no input file given (use --help for usage)")
        return EXIT_INPUT_ERROR
    try:
        return _run(args)
    except UsageError as error:
        _report(f"{PROG}: {error}")
        return EXIT_INPUT_ERROR
    except IRError as error:
        _report(f"{PROG}: {args.input}: {error}")
        return EXIT_INPUT_ERROR
    except OSError as error:
        _report(f"{PROG}: cannot read {args.input}: {error.strerror or error}")
        return EXIT_INPUT_ERROR
    except Exception as error:  # unexpected: report instead of a traceback
        _report(f"{PROG}: internal error: {type(error).__name__}: {error}")
        return EXIT_INTERNAL_ERROR


def _run(args: Namespace) -> int:
    path = Path(args.input)
    input_format = _resolve_format(path, args.from_format)
    if input_format in NOT_YET_IMPLEMENTED:
        _report(f"{PROG}: {NOT_YET_IMPLEMENTED[input_format]}")
        return EXIT_INPUT_ERROR
    if args.verbose:
        _report(f"{PROG}: reading {path} as {input_format}")

    overrides = _style_overrides(args)

    warnings: list[str] = []
    netlist: NetlistIR | None = None
    schematic: SchematicIR | None = None
    if input_format == "spice":
        netlist = spice_parser.load_spice(path, warnings)
    elif input_format == "netlist-ir":
        netlist = netlist_ir.loads(path.read_text(encoding="utf-8"), warnings)
    else:
        schematic = schematic_ir.loads(path.read_text(encoding="utf-8"), warnings)

    if not args.quiet:
        for warning in warnings:
            _report(f"warning: {warning}")

    if args.dump_netlist and netlist is None:
        raise UsageError("--dump-netlist: this input has no Netlist IR stage")
    if args.dump_layout and schematic is None and netlist is not None:
        raise UsageError(f"--dump-layout: {NO_LAYOUT_ENGINE}")

    if netlist is not None:
        # Dumps are written before validation on purpose: when a document *is*
        # broken, the dump is exactly what the user wants to open and fix.
        if args.dump_netlist:
            _write_text(Path(args.dump_netlist), netlist_ir.dumps(netlist))
            if args.verbose:
                _report(f"{PROG}: wrote {args.dump_netlist}")
        if args.verbose:
            _report(f"{PROG}: {_describe(netlist)}")
        errors, warning_count = _validate_and_report(netlist, args)
        if not args.quiet:
            _report(_summary(path, errors, warning_count + len(warnings)))
        if errors:
            return EXIT_VALIDATION_ERROR
        # The layout engine (roadmap §5) is what would turn this into a
        # Schematic IR; until it exists a requested dump is the whole job.
        if args.dump_netlist:
            return EXIT_OK
        _report(f"{PROG}: {NO_LAYOUT_ENGINE}")
        return EXIT_INPUT_ERROR

    # Exactly one of the two is set by the loader above; this narrows the type.
    assert schematic is not None
    if overrides is not None:
        schematic.style = _merge_style(schematic.effective_style(), overrides)
    if args.dump_layout:
        _write_text(Path(args.dump_layout), schematic_ir.dumps(schematic))
        if args.verbose:
            _report(f"{PROG}: wrote {args.dump_layout}")
    if args.verbose:
        _report(f"{PROG}: {_describe(schematic)}")

    errors, warning_count = _validate_and_report(schematic, args)
    if not args.quiet:
        _report(_summary(path, errors, warning_count + len(warnings)))
    if errors:
        # DESIGN §6: never emit silently-wrong output.
        return EXIT_VALIDATION_ERROR

    output = emit(schematic, standalone=args.standalone)
    if args.output:
        _write_text(Path(args.output), output)
        if args.verbose:
            _report(f"{PROG}: wrote {args.output}")
    else:
        _write_stdout(output)
    return EXIT_OK


def _validate_and_report(
    ir: NetlistIR | SchematicIR, args: Namespace
) -> tuple[int, int]:
    """Validate *ir*, print its findings, and return ``(errors, warnings)``."""
    findings = validate(ir)
    for finding in findings:
        if args.quiet and finding.severity is not Severity.ERROR:
            continue
        _report(format_finding(finding))
    return count_by_severity(findings)


def _summary(path: Path, errors: int, warnings: int) -> str:
    return f"{PROG}: {path}: {errors} error(s), {warnings} warning(s)"


def _resolve_format(path: Path, forced: str | None) -> str:
    """Return the input format, from ``--from``, the extension, or the file."""
    if forced is not None:
        return forced
    suffix = path.suffix.lower()
    if suffix in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[suffix]
    if suffix == ".json":
        kind = _serde.detect_ir_kind(_serde.loads(path.read_text(encoding="utf-8")))
        if kind not in IR_FORMATS:
            raise IRError(f"<root>.ir: expected 'netlist' or 'schematic', got {kind!r}")
        return IR_FORMATS[kind]
    raise IRError(
        f"cannot deduce the input format from the extension "
        f"{suffix or '(none)'!r}; use --from {{{','.join(FORMATS)}}}"
    )


# --- style overrides (--config, then --style) --------------------------------


def _style_overrides(args: Namespace) -> dict[str, Any] | None:
    """Collect style overrides from ``--config`` and ``--style``, in that order."""
    overrides: dict[str, Any] = {}
    if args.config:
        overrides.update(_config_style(Path(args.config)))
    for item in args.style:
        key, value = _style_assignment(item)
        if key == "extra_preamble":
            # Repeatable: each --style extra_preamble=... adds one line, on top
            # of whatever the config file already listed.
            existing = list(overrides.get("extra_preamble", []))
            existing.extend(value)
            overrides["extra_preamble"] = existing
        else:
            overrides[key] = value
    return overrides or None


def _config_style(path: Path) -> dict[str, Any]:
    """Read the ``[style]`` table of a TOML config file."""
    try:
        data = _toml.loads(path.read_text(encoding="utf-8"))
    except _toml.TomlError as error:
        raise UsageError(f"--config {path}: {error}") from error
    except OSError as error:
        raise UsageError(f"--config {path}: {error.strerror or error}") from error
    table = data.get("style", data)
    if not isinstance(table, dict):
        raise UsageError(f"--config {path}: [style] must be a table")
    overrides: dict[str, Any] = {}
    for key, value in table.items():
        if key not in STYLE_KEYS:
            raise UsageError(
                f"--config {path}: unknown style key {key!r} "
                f"(known: {', '.join(STYLE_KEYS)})"
            )
        overrides[key] = _coerce_style_value(key, value, f"--config {path}")
    return overrides


def _style_assignment(item: str) -> tuple[str, Any]:
    """Split one ``--style KEY=VALUE`` argument and coerce its value."""
    key, separator, raw = item.partition("=")
    key = key.strip()
    if not separator:
        raise UsageError(f"--style {item!r}: expected KEY=VALUE")
    if key not in STYLE_KEYS:
        raise UsageError(
            f"--style {item!r}: unknown key {key!r} (known: {', '.join(STYLE_KEYS)})"
        )
    return key, _coerce_style_value(key, raw.strip(), f"--style {item!r}")


def _coerce_style_value(key: str, value: Any, where: str) -> Any:  # noqa: ANN401
    """Validate and convert one style value, from either source."""
    if key in STYLE_CHOICES:
        choices = STYLE_CHOICES[key]
        if not isinstance(value, str) or value not in choices:
            raise UsageError(
                f"{where}: {key} must be one of {{{','.join(choices)}}}, got {value!r}"
            )
        return value
    if key in STYLE_FLAGS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in BOOL_WORDS:
            return BOOL_WORDS[value.lower()]
        raise UsageError(f"{where}: {key} must be a boolean, got {value!r}")
    # extra_preamble: a list in a config file, one line per --style flag.
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        return list(value)
    raise UsageError(f"{where}: extra_preamble must be a list of strings")


def _merge_style(style: StyleDefaults, overrides: dict[str, Any]) -> StyleDefaults:
    """Return *style* with *overrides* applied; ``extra_preamble`` appends."""
    merged = StyleDefaults(
        resistor_variant=style.resistor_variant,
        inductor_variant=style.inductor_variant,
        siunitx=style.siunitx,
        label_refs=style.label_refs,
        extra_preamble=list(style.extra_preamble),
    )
    for key, value in overrides.items():
        if key == "extra_preamble":
            merged.extra_preamble.extend(value)
        else:
            setattr(merged, key, value)
    return merged


# --- output ------------------------------------------------------------------


def _write_text(path: Path, text: str) -> None:
    """Write *text* with LF newlines, whatever the platform (determinism)."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_stdout(text: str) -> None:
    """Write *text* to stdout as UTF-8 bytes, bypassing newline translation."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pragma: no cover - only when stdout is not a real stream
        sys.stdout.write(text)
        return
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _describe(ir: NetlistIR | SchematicIR) -> str:
    """Return a one-line summary of a loaded document."""
    if isinstance(ir, NetlistIR):
        return (
            f"netlist IR: {len(ir.circuit.components)} component(s), "
            f"{len(ir.circuit.nets)} net(s), "
            f"{len(ir.subcircuits)} subcircuit definition(s)"
        )
    elements = sum(len(sheet.elements) for sheet in ir.sheets)
    return f"schematic IR: {len(ir.sheets)} sheet(s), {elements} element(s)"


def _report(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
