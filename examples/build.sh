#!/usr/bin/env bash
# Build the example gallery (roadmap §6.1).
#
# Runs spice2tikz over a hand-picked set of corpus circuits, writing the
# CircuiTikZ snippet and, if a LaTeX toolchain is available, the rendered
# image.  Both are committed, so the README gallery works for anyone reading
# the repository on the web.
#
#   ./build.sh          # regenerate everything
#   make -C examples    # the same, via the Makefile
#
# The rendering is spice2tikz's own (`-o name.png`), not a pipeline of this
# script's: the gallery should show exactly what a user gets.  Requires the
# package importable (pip install -e .), and for images a LaTeX toolchain with
# circuitikz plus a PDF-to-PNG converter.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
corpus="$root/tests/corpus"
dpi="${DPI:-140}"

python="${PYTHON:-python3}"
command -v "$python" >/dev/null 2>&1 || python=python

# Each line is: <name> <source file, relative to tests/corpus>
examples="
rc_lowpass              spice/rc_lowpass.sp
voltage_divider         spice/voltage_divider.sp
rlc_series              spice/rlc_series.sp
bridge_rectifier        spice/bridge_rectifier.sp
common_source_amp       spice/common_source_amp.sp
bjt_amp                 spice/bjt_amp.sp
cmos_inverter           spice/cmos_inverter.sp
opamp_inverting         spice/opamp_inverting.sp
ltspice_rc_lowpass      asc/rc_lowpass.asc
ltspice_cmos_inverter   asc/cmos_inverter.asc
"

# Ask the package what it is missing, rather than looking for tools again here.
missing="$("$python" -c 'import sys; sys.path.insert(0, "'"$root"'/src")
from spice2tikz.render import missing_tools
print(" and ".join(missing_tools("png")))')"
if [ -n "$missing" ]; then
  echo "build.sh: no $missing; writing .tex only" >&2
fi

echo "$examples" | while read -r name source; do
  [ -n "$name" ] || continue
  echo "  $name"
  "$python" -m spice2tikz.cli "$corpus/$source" -q -o "$here/$name.tex"
  if [ -z "$missing" ]; then
    "$python" -m spice2tikz.cli "$corpus/$source" -q --dpi "$dpi" \
        -o "$here/$name.png" || echo "    FAILED to render" >&2
  fi
done

echo "build.sh: wrote $here"
