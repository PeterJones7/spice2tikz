#!/usr/bin/env python3
"""Compare automatic layouts against the human ones (roadmap §5.6).

Some circuits exist in both corpora: as an LTspice `.asc` file, which carries a
layout a person made, and as a `.sp` netlist, which carries none.  Running both
through the same metrics says how far the layout engine is from a human answer
on the same circuit.

This **reports**; it does not judge.  The engine is not expected to match a
hand layout, and the numbers are noisy on circuits this small — they exist so
that the gap can be watched over releases, and as the ground truth a future
layout v2 (roadmap §7.2) would be evaluated against.

Usage::

    python tools/cross_validate.py
    python tools/cross_validate.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from spice2tikz import asc_importer, spice_parser  # noqa: E402
from spice2tikz.layout import layout, measure  # noqa: E402

ASC_CORPUS = REPO_ROOT / "tests" / "corpus" / "asc"
SPICE_CORPUS = REPO_ROOT / "tests" / "corpus" / "spice"

COLUMNS = ("crossings", "wire_length", "bbox_area", "alignment")


def shared_circuits() -> list[str]:
    """Return the circuits that exist in both corpora, sorted."""
    asc = {path.stem for path in ASC_CORPUS.glob("*.asc")}
    spice = {path.stem for path in SPICE_CORPUS.glob("*.sp")}
    return sorted(asc & spice)


def compare(name: str) -> dict[str, dict[str, float]]:
    """Return the human and automatic metrics for one circuit."""
    human = measure(asc_importer.load_asc(ASC_CORPUS / f"{name}.asc"))
    auto = measure(layout(spice_parser.load_spice(SPICE_CORPUS / f"{name}.sp")))
    return {"human": human.to_json(), "auto": auto.to_json()}


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="cross_validate.py",
        description="Compare automatic layouts with the human .asc layouts.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON, not a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print the comparison and return a process exit code."""
    args = build_parser().parse_args(argv)
    names = shared_circuits()
    if not names:
        print("cross_validate: no circuit exists in both corpora", file=sys.stderr)
        return 1
    results = {name: compare(name) for name in names}
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    print(f"{'circuit':22} {'metric':12} {'human':>10} {'auto':>10} {'ratio':>8}")
    print("-" * 66)
    for name, pair in results.items():
        for column in COLUMNS:
            human, auto = pair["human"][column], pair["auto"][column]
            ratio = "n/a" if not human else f"{auto / human:.2f}"
            print(f"{name:22} {column:12} {human:>10} {auto:>10} {ratio:>8}")
        print()
    print("Reported, not asserted: the engine is not expected to match a person.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
