"""Smoke tests: the package imports and the CLI reports its version."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

import spice2tikz
from spice2tikz import cli


def test_package_exposes_version():
    assert isinstance(spice2tikz.__version__, str)
    assert spice2tikz.__version__.count(".") == 2


def test_main_with_no_arguments_prints_version(capsys: pytest.CaptureFixture[str]):
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"spice2tikz {spice2tikz.__version__}\n"
    assert captured.err == ""


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"spice2tikz {spice2tikz.__version__}\n"


def test_module_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "spice2tikz.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == f"spice2tikz {spice2tikz.__version__}\n"


def test_console_script_runs():
    executable = shutil.which("spice2tikz")
    if executable is None:
        pytest.skip("console script not installed")
    result = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == f"spice2tikz {spice2tikz.__version__}\n"
