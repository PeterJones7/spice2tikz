"""CLI tests: validate-and-report on IR files, exit codes per contract (§1.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from spice2tikz import cli

CORPUS = Path(__file__).parent / "corpus"
BROKEN = CORPUS / "broken"
NETLIST = CORPUS / "rc_lowpass.netlist.json"
SCHEMATIC = CORPUS / "rc_lowpass.schematic.json"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run the CLI and return ``(exit code, stdout, stderr)``."""
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_valid_schematic_exits_zero(capsys: pytest.CaptureFixture[str]):
    code, out, err = run(capsys, str(SCHEMATIC))
    assert code == cli.EXIT_OK
    assert out == ""
    assert err.splitlines() == [
        f"spice2tikz: {SCHEMATIC}: 0 error(s), 0 warning(s)",
    ]


def test_valid_netlist_exits_zero(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(NETLIST))
    assert code == cli.EXIT_OK
    assert "0 error(s), 0 warning(s)" in err


def test_diagnostics_go_to_stderr_never_stdout(capsys: pytest.CaptureFixture[str]):
    code, out, err = run(capsys, str(BROKEN / "s9_dangling_wire.schematic.json"))
    assert code == cli.EXIT_VALIDATION_ERROR
    assert out == ""
    assert "dangling wire end" in err


def test_validation_error_exits_two(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(BROKEN / "n1b_pin_name.netlist.json"))
    assert code == cli.EXIT_VALIDATION_ERROR
    assert err.count("error:") == 2
    assert "2 error(s), 0 warning(s)" in err


def test_warnings_alone_exit_zero(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(BROKEN / "n5_no_ground.netlist.json"))
    assert code == cli.EXIT_OK
    assert "warning: circuit.nets: no ground-class net" in err


def test_quiet_suppresses_warnings_and_the_summary(
    capsys: pytest.CaptureFixture[str],
):
    code, _, err = run(capsys, str(BROKEN / "n5_no_ground.netlist.json"), "-q")
    assert code == cli.EXIT_OK
    assert err == ""


def test_quiet_still_reports_errors(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(BROKEN / "s9_dangling_wire.schematic.json"), "-q")
    assert code == cli.EXIT_VALIDATION_ERROR
    assert err.splitlines() == [
        "error: sheets[0].elements[1]: dangling wire end at (6, 0): "
        "no component pin, wire, net symbol, or port there"
    ]


def test_verbose_describes_the_document(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(SCHEMATIC), "-v")
    assert code == cli.EXIT_OK
    assert f"reading {SCHEMATIC} as schematic-ir" in err
    assert "schematic IR: 1 sheet(s), 7 element(s)" in err


def test_verbose_describes_a_netlist(capsys: pytest.CaptureFixture[str]):
    _, _, err = run(capsys, str(NETLIST), "-v")
    assert "netlist IR: 3 component(s), 3 net(s), 0 subcircuit definition(s)" in err


def test_quiet_and_verbose_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main([str(SCHEMATIC), "-q", "-v"])


# --- format detection -------------------------------------------------------


def test_json_format_is_sniffed_from_the_ir_field(
    capsys: pytest.CaptureFixture[str],
):
    _, _, err = run(capsys, str(NETLIST), "-v")
    assert "as netlist-ir" in err


def test_from_flag_forces_the_format(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(SCHEMATIC), "--from", "schematic-ir", "-v")
    assert code == cli.EXIT_OK
    assert "as schematic-ir" in err


def test_from_flag_with_the_wrong_ir_is_an_input_error(
    capsys: pytest.CaptureFixture[str],
):
    code, _, err = run(capsys, str(SCHEMATIC), "--from", "netlist-ir")
    assert code == cli.EXIT_INPUT_ERROR
    assert "expected 'netlist'" in err


def test_unknown_from_value_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        cli.main([str(SCHEMATIC), "--from", "vhdl"])


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("circuit.sp", "roadmap section 4"),
        ("circuit.cir", "roadmap section 4"),
        ("circuit.net", "roadmap section 4"),
        ("circuit.asc", "roadmap section 3"),
    ],
)
def test_not_yet_implemented_formats_say_so(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, name: str, fragment: str
):
    path = tmp_path / name
    path.write_text("* placeholder\n", encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_INPUT_ERROR
    assert fragment in err


def test_unknown_extension_asks_for_from(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    path = tmp_path / "circuit.txt"
    path.write_text("hello\n", encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_INPUT_ERROR
    assert "cannot deduce the input format" in err
    assert "--from" in err


def test_json_with_an_unknown_ir_field(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    path = tmp_path / "thing.json"
    path.write_text('{"ir": "layout", "version": "1.0"}\n', encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_INPUT_ERROR
    assert "expected 'netlist' or 'schematic'" in err


# --- input errors -----------------------------------------------------------


def test_missing_file_is_an_input_error(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, "does/not/exist.json")
    assert code == cli.EXIT_INPUT_ERROR
    assert "cannot read does/not/exist.json" in err


def test_invalid_json_is_an_input_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_INPUT_ERROR
    assert "invalid JSON" in err


def test_unloadable_ir_is_an_input_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    path = tmp_path / "bad.json"
    path.write_text(
        '{"ir": "schematic", "version": "9.0", "sheets": []}', encoding="utf-8"
    )
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_INPUT_ERROR
    assert "unsupported major version" in err


def test_no_arguments_prints_usage(capsys: pytest.CaptureFixture[str]):
    code, out, err = run(capsys)
    assert code == cli.EXIT_INPUT_ERROR
    assert out == ""
    assert "usage: spice2tikz" in err
    assert "no input file given" in err


def test_unknown_fields_are_reported_as_warnings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    data = SCHEMATIC.read_text(encoding="utf-8").replace(
        '"ir": "schematic"', '"ir": "schematic",\n  "extra": 1', 1
    )
    path = tmp_path / "extra.json"
    path.write_text(data, encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == cli.EXIT_OK
    assert "warning: <root>: unknown field 'extra' ignored" in err
    assert "0 error(s), 1 warning(s)" in err


def test_internal_errors_exit_three(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "validate", boom)
    code, _, err = run(capsys, str(SCHEMATIC))
    assert code == cli.EXIT_INTERNAL_ERROR
    assert "internal error: RuntimeError: kaboom" in err
