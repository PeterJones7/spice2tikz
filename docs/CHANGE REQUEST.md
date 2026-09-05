Below a series of change requests / bugs to fix.

Do each in turn. Update documentation and push changes after each  request is implemented.

Once addressed they can be removed from this document (with evidence left in changelog.md)

---

All seven requests in this file have been implemented and removed; the
evidence is in `CHANGELOG.md` under Unreleased, and the reasoning for each
call in `docs/DECISIONS.md`. Two points where the request was not followed
exactly, both recorded there:

1. **`sqI` does not exist in circuitikz.** Square-wave *voltage* sources are
   drawn as asked; there is no square current source, and a document using one
   does not compile. Pulsed current sources keep the plain symbol.
2. **The junction-dot suggestion in request 2 was not implemented.** The extra
   dot was a symptom of the overlapping leads and went away with them, and
   `validate.py` counts every node pin — an importer that counted differently
   would emit documents the validator then warned about.

Add new requests below.

