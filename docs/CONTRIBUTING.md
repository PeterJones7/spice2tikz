<!-- FILE: docs/CONTRIBUTING.md -->

# Contributing to spice2tikz

`spice2tikz` converts SPICE netlists and LTspice `.asc` schematics into
CircuiTikZ. The interesting parts of the codebase are the two JSON
intermediate representations (`docs/SPEC_IR.md`) and the code that produces
them; everything else is plumbing around those.

Read `docs/DESIGN.md` before proposing anything structural — its §4 decision
table (D1–D16) is settled and is not relitigated in issues or pull requests.
`docs/ROADMAP.md` says what is being built and in which order.
`docs/DECISIONS.md` is the append-only log of the small calls made along the
way.

By taking part you agree to `CODE_OF_CONDUCT.md`.

---

## 1. Development setup

Python ≥ 3.10, and nothing else. From a clone:

```sh
python -m pip install -e ".[dev]"
spice2tikz --version
```

The `dev` extra pulls in pytest, ruff and mypy. The package itself has **zero
runtime dependencies** and will keep it that way (design decision D1): the
tool has to be `pipx install`-able and drop cleanly onto a TeX-oriented
machine with one Python and no patience for a dependency tree. **A pull
request that adds anything to `[project.dependencies]` will be rejected.**
Reach for the standard library, or vendor a small piece of code and say so in
`docs/DECISIONS.md`. `src/spice2tikz/_toml.py` is the worked example:
`--config` needs a TOML reader, `tomllib` is 3.11+, so Python 3.10 gets a
small in-tree subset parser rather than a dependency on a back-port.

Dev dependencies are not covered by that rule.

A LaTeX toolchain is optional. Without one, `tests/test_compile.py` skips
itself and `tools/render_goldens.py` reports what is missing and exits 3.
With one — `latexmk` and circuitikz, plus `pdftoppm`, `pdftocairo`, `magick`
or `gs` for images — you can check your work locally instead of waiting for
CI. §3 explains why you want to.

## 2. The four checks

```sh
ruff check .            # lint
ruff format --diff .    # formatting; shows the diff, rewrites nothing
mypy                    # strict, over src/ (files= is set in pyproject.toml)
pytest                  # the suite
```

All four pass before a pull request is ready. CI
(`.github/workflows/ci.yml`) runs the same four on every push and pull
request: ruff and mypy once on Python 3.12, `pytest` on Python 3.10 **and**
3.12 — lint and type results do not vary with the interpreter, test results
do (`docs/DECISIONS.md`, `2026-07-28 | 0.2`). A third job runs
`pytest tests/test_compile.py` inside a `texlive/texlive` container, which
compiles every standalone golden `.tex` with `latexmk -pdf`; a golden that no
longer builds is a red build.

All configuration lives in `pyproject.toml`: ruff at line length 88 with the
docstring (`D`) and annotation (`ANN`) rule sets enabled, mypy in `strict`
mode over `src/`. Type hints everywhere in `src/`; tests are excused a few
docstring rules and nothing else.

## 3. Tests, and the golden-file workflow

This is the section worth reading twice.

### What lives where

- `tests/corpus/` — **inputs**, written by hand and committed: Schematic IR
  and Netlist IR JSON documents, `.sp` netlists, `.asc` schematics.
  `tests/corpus/broken/` holds one deliberately invalid document per IR
  invariant, so `validate.py` is tested against files that fire exactly one
  finding each.
- `tests/golden/` — **expected outputs**, generated, never hand-edited:
  `<name>.tex` (snippet) and `<name>.standalone.tex` for every schematic
  corpus document, plus golden IR JSON for the importer and parser corpora as
  those land (roadmap §3, §4).
- `tools/render_goldens.py` — compiles the standalone goldens and renders
  them to PNG under `build/golden-review/`. Not a test; a reviewing aid.

The test modules discover the corpus by globbing, so **adding a corpus file
is enough to be covered by everything**. `tests/test_golden.py` picks up
every `tests/corpus/*.schematic.json` and subjects it to validation, snippet
and standalone golden comparison, a determinism check (emit twice, compare
bytes) and a round-trip check (dump to JSON, reload, re-emit, compare).
`tests/test_compile.py` picks up every `tests/golden/**/*.standalone.tex`.

### Regenerating goldens

Goldens are compared **byte for byte**. Every intentional change to the
output is therefore a golden diff, and there is exactly one way to produce
one:

```sh
pytest --update-golden
```

That rewrites the golden files from the current output instead of comparing
against it (the flag is registered in `tests/conftest.py`; files whose
content would not change are left untouched, so the diff *is* the change).
Then, always:

```sh
git diff tests/golden/          # read every line of it
python tools/render_goldens.py  # and look at the pictures
```

### Why the second command is not optional

Determinism is a core promise of this project — same input, byte-identical
output, forever (`CLAUDE.md` working rule 4, `docs/DESIGN.md` §1). Golden
files are how that promise is enforced, and the price of enforcing it is that
*every* output change surfaces as a diff, including the ones you did not
intend.

A `.tex` diff proves the output changed. It does not prove the output is
right. `\draw (4,2) to[R=$R_1$] (4,8);` is entirely plausible text for a
resistor drawn in the wrong place, mirrored the wrong way, or wired to the
wrong pin. Roadmap §2.3 mandates the render step because exactly that class
of bug — built-in transistor pins pointing at no real circuitikz anchor — was
invisible in the golden text and obvious in the rendered image. So for
anything visual, render the goldens and check:

- every expected component is present,
- orientation and mirroring are correct,
- labels are present and readable,
- wires join the pins they are meant to join,
- junction dots appear where they should,
- nothing overlaps, collides, or dangles.

Say in the pull request that you did this. If you have no LaTeX toolchain,
say *that* instead, so the reviewer knows the images still need eyes. Never
accept a golden diff you cannot explain line by line: an unexplained diff is
a bug report about your own change.

`tests/test_docs.py` additionally asserts that the LaTeX example in
`README.md` is still literally the contents of `tests/golden/rc_lowpass.tex`,
so a change that reaches that circuit means updating the README in the same
commit.

### Writing a new test

Prefer a corpus file to a unit test whenever the thing under test is
end-to-end behaviour: it costs one file and earns validation, determinism,
round-trip and compile coverage for free. Keep unit tests for what a corpus
cannot isolate — quantity parsing, LaTeX escaping, rotation maths, one
invariant at a time.

Do not hand-type rotated pin coordinates into a Schematic IR corpus file.
Compute them with `resolve_pins` from `src/spice2tikz/symbols.py` and write
the file with the canonical printer, or `test_corpus_file_is_canonical_json`
will fail on the formatting before validation gets a chance to fail on the
geometry.

## 4. How to add a symbol

The procedure is `docs/EMITTER.md` §4 — follow it there rather than a copy
here. What it touches, from a contributor's point of view:

| what you are adding | where it goes |
|---|---|
| a one-off symbol for one drawing | that document's own `symbols` block; no code change, no pull request needed |
| a multi-terminal built-in | `BUILTIN_SYMBOLS` and `BASE_PIN_ANCHORS` in `src/spice2tikz/symbols.py` |
| a 2-terminal device placed by its endpoints | `BIPOLE_NAMES` in `src/spice2tikz/emit/circuitikz.py` — a path component, not a symbol at all |

In every case that touches code, add a corpus circuit that uses it,
regenerate the goldens, and look at the rendered image (§3).

Two rules here are not negotiable:

- **The CircuiTikZ manual is the authority.** Do not guess shape names,
  anchor names, or option names. `docs/CIRCUITIKZ_NOTES.md` is the distilled
  reference; when it does not cover what you need, consult
  `docs/circuitikz_manual.MD` — and **add what you learned back to
  `CIRCUITIKZ_NOTES.md` in the same pull request**. That file is the
  project's memory of the manual; anything the code relies on that is missing
  from it gets re-guessed by the next person (`CLAUDE.md`, "CircuiTikZ
  Reference Policy").
- **Record CircuiTikZ-specific assumptions** as a line in
  `docs/DECISIONS.md`. The `2.3` entries are what a good one looks like: what
  was assumed, what the manual actually says, and what happened when it was
  rendered.

Tests you should expect to touch: `tests/test_symbols.py` (geometry,
rotation, mirroring), `tests/test_emit_circuitikz.py` (emission and labels),
and `test_golden.py` / `test_compile.py` by way of the corpus file.

## 5. How to add a SPICE dialect

The SPICE parser is roadmap §4 and lives in
`src/spice2tikz/spice_parser.py`. **ngspice is the first dialect and
currently the only one** (D10); the Netlist IR carries `meta.dialect`
(`docs/SPEC_IR.md` §1) precisely so that a second one can exist without a
format change. Further dialect quirks — LTspice netlists, PSpice — are
roadmap §7.5, that is, deliberately deferred rather than forgotten.

A dialect, when the time comes, is three things:

1. **A hook in `spice_parser.py`** selected by `meta.dialect`, defaulting to
   ngspice and differing from it only where the dialect genuinely differs.
   Line assembly (title line, `+` continuations, `*` and `;` comments, case
   normalisation, `.end`) is stage 1; element and dot cards are stage 2.
   Nearly all dialect divergence belongs in one of those two places rather
   than sprinkled through the parser.
2. **A corpus file per quirk** under `tests/corpus/`, named for the quirk,
   with its golden Netlist IR JSON in `tests/golden/`. One file exercising
   eight quirks tells you something broke; eight files tell you which.
3. **Unit tests** for the cards whose parsing actually differs.

One rule is inherited from `docs/DESIGN.md` §6 and applies to every dialect:
**an unknown card is not an error.** Map it to a `generic` component, warn on
stderr, and keep going — a partial schematic beats none, and the warning is
what tells the user which part to distrust. Parse errors proper report file,
line and column and exit 1.

The traps in `docs/DESIGN.md` §7 (`M` is milli while `MEG` is mega, node `0`
is ground, continuations, inline comments) are handled for ngspice. Check
each one against your dialect rather than assuming it carries over.

## 6. How to add an importer

An importer's entire contract is: **produce a valid Schematic IR**
(`docs/SPEC_IR.md` §2). Nothing downstream knows or cares where the document
came from — that is the point of the IR, and it is why the LTspice `.asc`
importer (roadmap §3) can be a useful release with no layout engine behind
it. KiCad is roadmap §7.2.

A complete importer is four things:

1. **A module** under `src/spice2tikz/`, plus extension autodetection and a
   `--from` value in `cli.py` (the CLI contract is in `CLAUDE.md`). `cli.py`
   stays a thin shell (D3): the importer is a library function that takes a
   path and returns an IR object.
2. **A corpus** of inputs under `tests/corpus/` covering the source format's
   real variety, not its happy path — encodings (LTspice `.asc` files are
   UTF-16 LE in the wild; detect the BOM), coordinate conventions (`.asc` is
   y-down, the IR is y-up per D5), and the per-symbol offset quirks that
   capture formats accumulate. Put those quirks in a data table, not in
   control flow.
3. **Goldens**: the Schematic IR JSON *and* the emitted `.tex` for every
   corpus input. The IR golden localises a regression to the importer; the
   `.tex` golden proves the document survives emission and compilation.
4. **The two properties every path through this tool has.** The produced
   document passes `spice2tikz.validate.validate` with zero findings of
   severity `ERROR`, and importing the same file twice produces
   byte-identical output. Both are one parametrized test each over the corpus
   glob; copy them from `tests/test_golden.py`.

Anything the source format expresses that the IR cannot is a spec question,
not an importer question. Propose the IR change first — minor versions are
additive-only (D4) — rather than smuggling the concept in as a convention
inside `meta`.

## 7. Commits, pull requests, and the decision log

One commit per roadmap subsection at minimum, with a
`feat`/`fix`/`test`/`docs`/`chore` prefix and the subsection number as the
scope (`CLAUDE.md` working rule 2):

```
feat(1.2): schematic IR dataclasses + serde
fix(2.3): emit american source symbols so polarity is visible
docs(6.2): contributing guide, issue templates, code of conduct
```

Work outside the roadmap uses the same prefixes with a plain scope
(`fix(cli): …`) or none. Keep the subject imperative and under about 72
characters; the reasoning goes in the body, where it survives.

A pull request says what changed and why, names the roadmap section if there
is one, lists the checks that were run, and states whether rendered goldens
were inspected. Small and reviewable beats complete.

When something is ambiguous, do not open an issue and wait. Make the smallest
reasonable decision consistent with `docs/DESIGN.md`, record it as **one
line** in `docs/DECISIONS.md` —

```
- 2026-08-29 | 4.2 | decision — rationale
```

— and carry on (`CLAUDE.md` working rule 8). The log is append-only, one line
per decision, newest last. When a later decision overturns an earlier one,
add a line that says so (`**Corrects 2.1**: …`) instead of editing the
original: the file's value is that it shows what was believed at the time and
what changed the belief.

Architectural decisions are different. `docs/DESIGN.md` §4 is final, and a
pull request that changes one of D1–D16 is a design conversation first — it
opens with what breaks, not with the patch.

## 8. Reporting bugs and asking for features

Use the issue forms: [bug report][bug] or [feature request][feature].

The single most useful thing you can attach to a bug report is **the input
file**. `spice2tikz` is a translator; without the `.sp`, `.asc` or `.json`
that went in, a report describes a rendering nobody else can reproduce, and
the error philosophy the tool is built on (`docs/DESIGN.md` §6 — warn,
degrade, never emit silently-wrong output) can only be judged against a
specific input. Attach the file, or paste it if it is short. Reducing it to
the smallest version that still misbehaves is a kindness, but a large real
file beats no file at all.

After that, in order of usefulness: the exact command you ran, what happened,
what you expected, the generated output if there was any,
`spice2tikz --version`, your Python version and OS, and — when the complaint
is about a *rendered* schematic rather than the generated text — your
circuitikz and TeX Live versions, because the same `.tex` does genuinely
render differently across circuitikz releases.

Before filing a feature request, check `docs/ROADMAP.md`: §7 is work that is
deliberately deferred rather than overlooked, and `README.md` lists the
explicit non-features (no simulation, no netlist editing, no AI redrawing of
images, no GUI).

[bug]: https://github.com/PeterJones7/spice2tikz/issues/new?template=bug_report.yml
[feature]: https://github.com/PeterJones7/spice2tikz/issues/new?template=feature_request.yml

---

## 9. Looking at the output

Two symbol bugs reached rendered figures because nothing in the suite checks
*appearance*: goldens prove the output has not changed, `test_compile.py`
proves it is valid LaTeX, and `test_end_to_end.py` proves the connectivity is
right — but a symbol whose leads double back across its own body satisfies all
three.

`tests/test_anchor_geometry.py` now covers the specific failure those bugs
shared, by asking a real compiler where circuitikz places each anchor. For
everything else, look:

```sh
python tools/contact_sheet.py      # build/contact-sheet.html
```

That renders every golden plus reference sheets for each built-in symbol in all
eight orientations, every path-component kind, and every net-symbol variant —
the last of which cover emitter paths no corpus circuit reaches. Each section
carries its own checklist, and the page remembers which figures you have
marked.

Rebuild it after any change to the emitter, the symbol library, the importer or
the layout engine, and look at it before accepting a golden diff. A golden diff
tells you the output changed; only the picture tells you which way.
