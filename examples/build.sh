#!/usr/bin/env bash
# Build the example gallery (roadmap §6.1).
#
# Runs spice2tikz over a hand-picked set of corpus circuits and, if a LaTeX
# toolchain is available, compiles each one and renders it to PNG.  The
# generated .tex and .png files are committed, so the README gallery works for
# anyone reading the repository on the web.
#
#   ./build.sh          # regenerate everything
#   make -C examples    # the same, via the Makefile
#
# Requires: the package importable (pip install -e .), and for images a LaTeX
# toolchain (latexmk or pdflatex) with circuitikz plus a PDF-to-PNG converter
# (pdftoppm, pdftocairo, magick, or gs).

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
ltspice_rc_lowpass      asc/rc_lowpass.asc
ltspice_cmos_inverter   asc/cmos_inverter.asc
"

find_latex() {
  if command -v latexmk >/dev/null 2>&1; then
    echo "latexmk -pdf -interaction=nonstopmode -halt-on-error"
  elif command -v pdflatex >/dev/null 2>&1; then
    echo "pdflatex -interaction=nonstopmode -halt-on-error"
  fi
}

render_png() {
  # render_png <pdf> <png>
  local pdf="$1" png="$2"
  if command -v pdftoppm >/dev/null 2>&1; then
    pdftoppm -png -r "$dpi" -singlefile "$pdf" "${png%.png}"
  elif command -v pdftocairo >/dev/null 2>&1; then
    pdftocairo -png -r "$dpi" -singlefile "$pdf" "${png%.png}"
  elif command -v gs >/dev/null 2>&1; then
    gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pngalpha -r"$dpi" \
       -sOutputFile="$png" "$pdf"
  elif command -v magick >/dev/null 2>&1; then
    magick -density "$dpi" "$pdf" "$png"
  else
    return 1
  fi
}

latex="$(find_latex)"
if [ -z "$latex" ]; then
  echo "build.sh: no LaTeX toolchain found; writing .tex only" >&2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "$examples" | while read -r name source; do
  [ -n "$name" ] || continue
  echo "  $name"
  "$python" -m spice2tikz.cli "$corpus/$source" -q -o "$here/$name.tex"
  "$python" -m spice2tikz.cli "$corpus/$source" -q --standalone \
      -o "$work/$name.tex"
  if [ -n "$latex" ]; then
    (cd "$work" && $latex "$name.tex" >/dev/null 2>&1) || {
      echo "    FAILED to compile" >&2
      continue
    }
    render_png "$work/$name.pdf" "$here/$name.png" >/dev/null 2>&1 || {
      echo "    no PDF-to-PNG converter; skipping the image" >&2
    }
  fi
done

echo "build.sh: wrote $here"
