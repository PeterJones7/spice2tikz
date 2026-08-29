"""Shared pytest configuration: the golden-file regeneration flag.

Golden-file tests compare generated output byte-for-byte against files under
``tests/golden/``. ``pytest --update-golden`` rewrites those files from the
current output instead of comparing, so an intentional change is one command
and the diff is reviewed in git (``docs/DESIGN.md`` §5).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--update-golden``."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden files from the current output instead of comparing",
    )


@pytest.fixture(scope="session")
def update_golden(request: pytest.FixtureRequest) -> bool:
    """Whether golden files should be rewritten rather than compared."""
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture
def golden(update_golden: bool) -> Callable[[str, str], None]:
    """Return a ``check(name, text)`` helper for golden comparison.

    With ``--update-golden`` the golden file is written (only when its content
    would change, so unrelated files keep their timestamps); otherwise *text* is
    compared byte-for-byte against it.
    """

    def check(name: str, text: str) -> None:
        path = GOLDEN / name
        if update_golden:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                # newline="\n" so that regenerating on Windows does not write
                # CRLF: goldens are compared byte-for-byte elsewhere (the CLI
                # writes LF on every platform) and a CRLF golden would make the
                # same content differ between contributors.
                path.write_text(text, encoding="utf-8", newline="\n")
            return
        if not path.exists():
            pytest.fail(f"missing golden file {path}; run: pytest --update-golden")
        expected = path.read_text(encoding="utf-8")
        assert text == expected, (
            f"output differs from golden {path}; "
            f"if the change is intended run: pytest --update-golden"
        )

    return check
