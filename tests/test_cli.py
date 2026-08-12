"""CLI smoke tests."""

from typer.testing import CliRunner

from masc_yunhao_xu_linear import __version__
from masc_yunhao_xu_linear.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "salvage-subsidy" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"masc-yunhao-xu-linear {__version__}" in result.stdout


def test_cli_stub_command_fails_fast() -> None:
    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 1
    assert "not implemented yet" in result.stdout


def test_cli_stub_command_json_output() -> None:
    import json

    result = runner.invoke(app, ["ingest", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "ingest"
