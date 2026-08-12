"""CLI smoke tests."""

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from fresh_salvage import __version__, data
from fresh_salvage.cli import app

runner = CliRunner()

STUB_COMMANDS = ("export",)


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "salvage-subsidy" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"fresh-salvage {__version__}" in result.stdout


def test_cli_ws3_run_help() -> None:
    result = runner.invoke(app, ["ws3-run", "--help"])

    assert result.exit_code == 0
    assert "full-TSA WS3 schedule" in result.stdout


@pytest.mark.parametrize("command", STUB_COMMANDS)
def test_cli_stub_command_fails_with_json_diagnostic(command: str) -> None:
    result = runner.invoke(app, [command, "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == command
    assert payload["diagnostic"]
    assert "not implemented yet" in payload["diagnostic"]


def test_cli_stub_command_fails_fast_without_json() -> None:
    result = runner.invoke(app, ["export"])

    assert result.exit_code == 1
    assert "not implemented yet" in result.stdout


def test_cli_solve_principal_stub_is_retired() -> None:
    result = runner.invoke(app, ["solve-principal"])

    # Typer/click exit code 2 = no such command; the working command is
    # principal-run.
    assert result.exit_code == 2


def test_cli_solve_agent_stub_is_retired() -> None:
    result = runner.invoke(app, ["solve-agent"])

    # Typer/click exit code 2 = no such command; the working command is
    # agent-run.
    assert result.exit_code == 2


def test_cli_ws3_run_missing_config_fails_with_json_diagnostic(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["ws3-run", str(tmp_path / "missing.yaml"), "--json"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "ws3-run"
    assert payload["diagnostic"]


def test_cli_rh_run_missing_config_fails_with_json_diagnostic(tmp_path: Path) -> None:
    result = runner.invoke(app, ["rh-run", str(tmp_path / "missing.yaml"), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "rh-run"
    assert payload["diagnostic"]


def _write_synthetic_scenario(tmp_path: Path) -> tuple[Path, int]:
    """Write a tiny synthetic WL_VFSL CSV and scenario YAML; return paths."""

    rows = [
        {
            **{column: None for column in data.INPUT_COLUMNS},
            "FEATURE_ID": str(index),
            "MAP_ID": f"M{index}",
            "POLYGON_ID": f"P{index}",
            "POLYGON_AREA": "5.0",
            "BASAL_AREA": "10.0",
            "VRI_LIVE_STEMS_PER_HA": "300",
            "PROJ_HEIGHT_1": "15.0",
            "SPECIES_CD_1": "FD",
            "SPECIES_PCT_1": "100.0",
            "LIVE_VOL_PER_HA_SPP1_175": "50.0",
            "DEAD_VOL_PER_HA_SPP1_175": "0.0",
            "LIVE_STAND_VOLUME_175": "50.0",
            "DEAD_STAND_VOLUME_175": "0.0",
            "BURN_SEVERITY_RATING": "High",
            "MEAN": "8.0",
            "LANDSCAPE_UNIT_ID": "9999",
            "LANDSCAPE_UNIT_NAME": "Outside canonical set",
            "BEC_ZONE_CODE": "SBPS",
            # Rated rows need valid coverage areas (FS-VAL-02 fail-fast);
            # SHAPE_Area_1 == FEATURE_AREA_SQM means full coverage.
            "SHAPE_Area_1": "50000.0",
            "FEATURE_AREA_SQM": "50000.0",
        }
        for index in range(1, 6)
    ]
    csv_path = tmp_path / "wl_vfsl.csv"
    pd.DataFrame(rows, columns=data.INPUT_COLUMNS).to_csv(csv_path, index=False)

    yaml_path = tmp_path / "scenario.yaml"
    yaml_path.write_text(
        "run_id: cli-smoke\n"
        f"inputs:\n"
        f"  wl_vfsl_path: {csv_path}\n"
        f"  output_root: {tmp_path / 'out'}\n"
        f"fire: {{}}\n",
        encoding="utf-8",
    )
    return yaml_path, len(rows)


def test_cli_ingest_smoke_json(tmp_path: Path) -> None:
    yaml_path, row_count = _write_synthetic_scenario(tmp_path)

    result = runner.invoke(app, ["ingest", str(yaml_path), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == "ingest"
    assert payload["run_id"] == "cli-smoke"
    assert payload["total_stands"] == row_count
    assert payload["per_bec_zone_counts"] == {"SBPS": row_count}
    assert Path(payload["artifacts"]["data"]).is_file()
    assert Path(payload["artifacts"]["csv"]).is_file()
    assert Path(payload["artifacts"]["manifest"]).is_file()


def test_cli_ingest_smoke_rich(tmp_path: Path) -> None:
    yaml_path, row_count = _write_synthetic_scenario(tmp_path)

    result = runner.invoke(app, ["ingest", str(yaml_path)])

    assert result.exit_code == 0, result.stdout
    assert "Ingest complete: cli-smoke" in result.stdout
    assert f"Total stands: {row_count}" in result.stdout


def test_cli_ingest_missing_scenario_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["ingest", str(tmp_path / "missing.yaml"), "--json"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "ingest"
    assert payload["severity"] == "error"
    assert payload["code"] == "ingest_failed"
