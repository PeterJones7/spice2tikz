<!-- docs/CONTRIBUTING.md §7 says what a pull request should carry. -->

## What and why

<!-- What changed, and what problem it solves. Name the roadmap subsection if
     there is one (docs/ROADMAP.md), e.g. "roadmap §3.2". -->

## Checks

<!-- All four are required; see docs/CONTRIBUTING.md §2. -->

- [ ] `ruff check .`
- [ ] `ruff format --diff .`
- [ ] `mypy`
- [ ] `pytest`

## Goldens

<!-- Delete this section if no file under tests/golden/ changed. -->

- [ ] The golden diff was regenerated with `pytest --update-golden`, not edited
      by hand.
- [ ] I read the whole diff and can explain every line of it.
- [ ] I ran `python tools/render_goldens.py` and **looked at** the affected
      images — components present, orientation and mirroring right, labels
      readable, wires on the intended pins, junction dots where expected, no
      overlaps. *(If you have no LaTeX toolchain, say so here instead so a
      reviewer knows the images still need eyes.)*

## Decisions

<!-- Any ambiguity resolved along the way goes in docs/DECISIONS.md as one
     line, newest last (CLAUDE.md working rule 8). Anything relying on the
     CircuiTikZ manual also goes in docs/CIRCUITIKZ_NOTES.md. -->

- [ ] `docs/DECISIONS.md` updated, or nothing needed deciding.
- [ ] No new entry in `[project.dependencies]` — the runtime stays
      stdlib-only (D1).
