"""CLI tests: load, validate, and emit; exit codes per contract (§1.5, §2.4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from spice2tikz import cli, schematic_ir
from spice2tikz.emit.circuitikz import emit_snippet, emit_standalone

CORPUS = Path(__file__).parent / "corpus"
BROKEN = CORPUS / "broken"
NETLIST = CORPUS / "rc_lowpass.netlist.json"
SCHEMATIC = CORPUS / "rc_lowpass.schematic.json"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run the CLI and return ``(exit code, stdout, stderr)``."""
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_valid_schematic_emits_to_stdout(capsys: pytest.CaptureFixture[str]):
    code, out, err = run(capsys, str(SCHEMATIC))
    assert code == cli.EXIT_OK
    assert out == emit_snippet(schematic_ir.load(SCHEMATIC))
    assert err.splitlines() == [
        f"spice2tikz: {SCHEMATIC}: 0 error(s), 0 warning(s)",
    ]


def test_valid_netlist_has_nowhere_to_go_without_a_layout_engine(
    capsys: pytest.CaptureFixture[str],
):
    code, out, err = run(capsys, str(NETLIST))
    assert code == cli.EXIT_INPUT_ERROR
    assert out == ""
    assert "0 error(s), 0 warning(s)" in err
    assert "layout not yet implemented" in err
    assert "--dump-netlist" in err


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


def test_warnings_alone_still_emit(capsys: pytest.CaptureFixture[str]):
    source = BROKEN / "s10a_junction_too_few.schematic.json"
    code, out, err = run(capsys, str(source))
    assert code == cli.EXIT_OK
    assert out.startswith(r"\begin{circuitikz}")
    assert "warning:" in err


def test_quiet_suppresses_warnings_and_the_summary(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    dump = tmp_path / "n.json"
    code, out, err = run(
        capsys,
        str(BROKEN / "n5_no_ground.netlist.json"),
        "-q",
        "--dump-netlist",
        str(dump),
    )
    assert code == cli.EXIT_OK
    assert out == ""
    assert err == ""
    assert dump.exists()


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


# --- emission (§2.4) --------------------------------------------------------


def test_stdout_is_byte_identical_to_the_emitter(
    capsys: pytest.CaptureFixture[str],
):
    _, out, _ = run(capsys, str(SCHEMATIC))
    assert out == emit_snippet(schematic_ir.load(SCHEMATIC))


def test_standalone_wraps_the_snippet(capsys: pytest.CaptureFixture[str]):
    code, out, _ = run(capsys, str(SCHEMATIC), "--standalone")
    assert code == cli.EXIT_OK
    assert out == emit_standalone(schematic_ir.load(SCHEMATIC))
    assert out.startswith(r"\documentclass")


def test_output_file_receives_the_snippet_with_lf_newlines(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    target = tmp_path / "out.tex"
    code, out, _ = run(capsys, str(SCHEMATIC), "-o", str(target))
    assert code == cli.EXIT_OK
    assert out == ""
    expected = emit_snippet(schematic_ir.load(SCHEMATIC))
    assert target.read_bytes() == expected.encode("utf-8")
    assert b"\r\n" not in target.read_bytes()


def test_emission_is_suppressed_by_validation_errors(
    capsys: pytest.CaptureFixture[str],
):
    code, out, err = run(capsys, str(BROKEN / "s9_dangling_wire.schematic.json"))
    assert code == cli.EXIT_VALIDATION_ERROR
    assert out == ""
    assert "dangling wire end" in err


def test_running_twice_produces_identical_bytes(
    capsys: pytest.CaptureFixture[str],
):
    _, first, _ = run(capsys, str(SCHEMATIC), "--standalone")
    _, second, _ = run(capsys, str(SCHEMATIC), "--standalone")
    assert first == second


def test_dump_layout_writes_canonical_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    target = tmp_path / "layout.json"
    code, out, _ = run(capsys, str(SCHEMATIC), "--dump-layout", str(target))
    assert code == cli.EXIT_OK
    assert out != ""  # the dump is an extra output, not a replacement
    assert target.read_bytes() == SCHEMATIC.read_bytes()


def test_dump_netlist_on_a_schematic_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, _, err = run(
        capsys, str(SCHEMATIC), "--dump-netlist", str(tmp_path / "n.json")
    )
    assert code == cli.EXIT_INPUT_ERROR
    assert "no Netlist IR stage" in err


def test_dump_layout_on_a_netlist_needs_the_layout_engine(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, _, err = run(capsys, str(NETLIST), "--dump-layout", str(tmp_path / "s.json"))
    assert code == cli.EXIT_INPUT_ERROR
    assert "layout not yet implemented" in err


def test_dump_netlist_round_trips_the_input(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    target = tmp_path / "n.json"
    code, out, _ = run(capsys, str(NETLIST), "--dump-netlist", str(target))
    assert code == cli.EXIT_OK
    assert out == ""
    assert target.read_bytes() == NETLIST.read_bytes()


# --- style overrides (§2.4) -------------------------------------------------


def test_style_flag_overrides_a_variant(capsys: pytest.CaptureFixture[str]):
    code, out, _ = run(capsys, str(SCHEMATIC), "--style", "resistor_variant=american")
    assert code == cli.EXIT_OK
    assert r"\ctikzset{american resistors}" in out


def test_style_flag_accepts_booleans(capsys: pytest.CaptureFixture[str]):
    code, out, _ = run(capsys, str(SCHEMATIC), "--style", "label_refs=false")
    assert code == cli.EXIT_OK
    assert "$R_1$" not in out


def test_style_flag_is_repeatable(capsys: pytest.CaptureFixture[str]):
    code, out, _ = run(
        capsys,
        str(SCHEMATIC),
        "--standalone",
        "--style",
        r"extra_preamble=\usepackage{amsmath}",
        "--style",
        r"extra_preamble=\usetikzlibrary{arrows}",
    )
    assert code == cli.EXIT_OK
    assert r"\usepackage{amsmath}" in out
    assert r"\usetikzlibrary{arrows}" in out


def test_unknown_style_key_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(SCHEMATIC), "--style", "colour=red")
    assert code == cli.EXIT_INPUT_ERROR
    assert "unknown key 'colour'" in err


def test_style_without_equals_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(SCHEMATIC), "--style", "siunitx")
    assert code == cli.EXIT_INPUT_ERROR
    assert "expected KEY=VALUE" in err


def test_bad_style_value_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    code, _, err = run(capsys, str(SCHEMATIC), "--style", "resistor_variant=swiss")
    assert code == cli.EXIT_INPUT_ERROR
    assert "must be one of" in err


def test_config_file_supplies_style_defaults(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    config = tmp_path / "s2t.toml"
    config.write_text(
        "[style]\n"
        'resistor_variant = "american"\n'
        'inductor_variant = "american"\n'
        "label_refs = false\n"
        r"extra_preamble = ['\usepackage{amsmath}']"
        "\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, str(SCHEMATIC), "--standalone", "--config", str(config))
    assert code == cli.EXIT_OK
    assert r"\ctikzset{american resistors}" in out
    assert r"\ctikzset{american inductors}" in out
    assert r"\usepackage{amsmath}" in out
    assert "$R_1$" not in out


def test_style_flag_beats_the_config_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    config = tmp_path / "s2t.toml"
    config.write_text('[style]\nresistor_variant = "american"\n', encoding="utf-8")
    code, out, _ = run(
        capsys,
        str(SCHEMATIC),
        "--config",
        str(config),
        "--style",
        "resistor_variant=european",
    )
    assert code == cli.EXIT_OK
    assert r"\ctikzset{european resistors}" in out


def test_unknown_config_key_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    config = tmp_path / "s2t.toml"
    config.write_text('[style]\nfont = "serif"\n', encoding="utf-8")
    code, _, err = run(capsys, str(SCHEMATIC), "--config", str(config))
    assert code == cli.EXIT_INPUT_ERROR
    assert "unknown style key 'font'" in err


def test_missing_config_file_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, _, err = run(capsys, str(SCHEMATIC), "--config", str(tmp_path / "none.toml"))
    assert code == cli.EXIT_INPUT_ERROR
    assert "--config" in err


def test_malformed_config_file_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    config = tmp_path / "s2t.toml"
    config.write_text("[style\nresistor_variant\n", encoding="utf-8")
    code, _, err = run(capsys, str(SCHEMATIC), "--config", str(config))
    assert code == cli.EXIT_INPUT_ERROR
    assert "--config" in err


# --- the roadmap §2.4 headline: a real shell redirection --------------------


def test_shell_redirection_produces_the_golden_tex(tmp_path: Path):
    """``spice2tikz x.schematic.json > x.tex`` end to end, in a real process."""
    target = tmp_path / "rc_lowpass.tex"
    with target.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "spice2tikz.cli", str(SCHEMATIC)],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert result.returncode == cli.EXIT_OK
    golden = Path(__file__).parent / "golden" / "rc_lowpass.tex"
    assert target.read_bytes() == golden.read_bytes()


def test_shell_redirection_is_standalone_compilable_text(tmp_path: Path):
    target = tmp_path / "rc_lowpass.standalone.tex"
    with target.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "spice2tikz.cli", str(SCHEMATIC), "--standalone"],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert result.returncode == cli.EXIT_OK
    golden = Path(__file__).parent / "golden" / "rc_lowpass.standalone.tex"
    assert target.read_bytes() == golden.read_bytes()
