"""Command-line interface for spice2tikz.

A thin shell around the library internals (design decision D3).  At this
point of the roadmap it loads an IR JSON file, validates it, and reports the
findings; emission, importers, and the layout engine are wired up in later
sections.

Exit codes: 0 ok, 1 input parse error, 2 validation error, 3 internal.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from . import __version__, _serde, netlist_ir, schematic_ir
from ._serde import IRError
from .netlist_ir import NetlistIR
from .schematic_ir import SchematicIR
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
    "spice": "SPICE netlist parsing is not implemented yet (roadmap section 4)",
    "asc": "LTspice .asc import is not implemented yet (roadmap section 3)",
}


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
        "--from",
        dest="from_format",
        choices=FORMATS,
        help="force the input format instead of deducing it from the extension",
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
    text = path.read_text(encoding="utf-8")
    input_format = _resolve_format(path, text, args.from_format)
    if input_format in NOT_YET_IMPLEMENTED:
        _report(f"{PROG}: {NOT_YET_IMPLEMENTED[input_format]}")
        return EXIT_INPUT_ERROR
    if args.verbose:
        _report(f"{PROG}: reading {path} as {input_format}")

    warnings: list[str] = []
    ir: NetlistIR | SchematicIR
    if input_format == "netlist-ir":
        ir = netlist_ir.loads(text, warnings)
    else:
        ir = schematic_ir.loads(text, warnings)
    if not args.quiet:
        for warning in warnings:
            _report(f"warning: {warning}")
    if args.verbose:
        _report(f"{PROG}: {_describe(ir)}")

    findings = validate(ir)
    for finding in findings:
        if args.quiet and finding.severity is not Severity.ERROR:
            continue
        _report(format_finding(finding))
    errors, warning_count = count_by_severity(findings)
    if not args.quiet:
        _report(
            f"{PROG}: {path}: {errors} error(s), "
            f"{warning_count + len(warnings)} warning(s)"
        )
    return EXIT_VALIDATION_ERROR if errors else EXIT_OK


def _resolve_format(path: Path, text: str, forced: str | None) -> str:
    """Return the input format, from ``--from``, the extension, or the file."""
    if forced is not None:
        return forced
    suffix = path.suffix.lower()
    if suffix in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[suffix]
    if suffix == ".json":
        kind = _serde.detect_ir_kind(_serde.loads(text))
        if kind not in IR_FORMATS:
            raise IRError(f"<root>.ir: expected 'netlist' or 'schematic', got {kind!r}")
        return IR_FORMATS[kind]
    raise IRError(
        f"cannot deduce the input format from the extension "
        f"{suffix or '(none)'!r}; use --from {{{','.join(FORMATS)}}}"
    )


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
