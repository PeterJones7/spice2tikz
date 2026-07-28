"""Command-line interface for spice2tikz.

Thin shell around the library internals (design decision D3). At this
stage of the roadmap the CLI only reports its version; the conversion
options documented in ``CLAUDE.md`` are wired up in later sections.

Exit codes: 0 ok, 1 input parse error, 2 validation error, 3 internal.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from collections.abc import Sequence

from . import __version__

PROG = "spice2tikz"


def build_parser() -> ArgumentParser:
    """Build the argument parser for the ``spice2tikz`` command."""
    parser = ArgumentParser(
        prog=PROG,
        description="Convert SPICE netlists and LTspice schematics to CircuiTikZ.",
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
    parser.parse_args(argv)
    print(f"{PROG} {__version__}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
