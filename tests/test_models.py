"""Model record round-trip and layout tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from fresh_salvage.models import (
    ARTIFACT_DIRECTORIES,
    ArtifactLayout,
    DevelopmentType,
    Diagnostic,
    ScenarioInputs,
    ScenarioRunConfig,
    Stand,
)


def test_scenario_config_json_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "scenario.json"
    scenario = ScenarioRunConfig(
        run_id="roundtrip-run",
        inputs=ScenarioInputs(
            wl_vfsl_path=Path("/data/WL_VFSL.csv"),
            output_root=tmp_path / "out",
        ),
        metadata={"note": "roundtrip"},
    )

    written = scenario.write_json(config_path)
    restored = ScenarioRunConfig.read(written)

    assert written == config_path
    assert restored == scenario
    assert restored.inputs.wl_vfsl_path == Path("/data/WL_VFSL.csv")


def test_scenario_config_yaml_read(tmp_path: Path) -> None:
    yaml_path = tmp_path / "scenario.yaml"
    yaml_path.write_text(
        "run_id: yaml-run\n"
        "inputs:\n"
        "  wl_vfsl_path: /data/WL_VFSL.csv\n"
        "  output_root: out\n",
        encoding="utf-8",
    )

    scenario = ScenarioRunConfig.read(yaml_path)

    assert scenario.run_id == "yaml-run"
    assert scenario.inputs.wl_vfsl_path == Path("/data/WL_VFSL.csv")
    assert scenario.inputs.output_root == Path("out")


def test_scenario_config_rejects_empty_run_id(tmp_path: Path) -> None:
    config_path = tmp_path / "scenario.json"
    config_path.write_text(
        '{"run_id": " ", "inputs": '
        '{"wl_vfsl_path": "/data/WL_VFSL.csv", "output_root": "out"}}',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        ScenarioRunConfig.read(config_path)


def test_artifact_layout_properties(tmp_path: Path) -> None:
    layout = ArtifactLayout(output_root=tmp_path / "root")

    assert ARTIFACT_DIRECTORIES == ("data", "manifests", "logs")
    assert layout.data_dir == tmp_path / "root" / "data"
    assert layout.manifests_dir == tmp_path / "root" / "manifests"
    assert layout.logs_dir == tmp_path / "root" / "logs"

    initialized = layout.initialize()
    assert initialized.data_dir.is_dir()
    assert initialized.manifests_dir.is_dir()
    assert initialized.logs_dir.is_dir()


def test_stand_record() -> None:
    stand = Stand(
        feature_id="1",
        polygon_id="2",
        map_id="3",
        polygon_area=10.0,
        bec_zone="SBPS",
        development_type="SPF_SBPS",
        total_green_vol=100.0,
        total_burned_vol=30.0,
        subsidy_rate=3.0,
        green_stumpage_rate=30.0,
        burned_stumpage_rate=5.0,
        harvest_cost_green=30.0,
        harvest_cost_burned=35.0,
        subsidy_total=90.0,
        stumpage_green_total=3000.0,
        stumpage_burned_total=150.0,
        green_prices={"SPF_Sawlog": 200.0},
        burned_prices={"SPF_Sawlog": 130.0},
    )

    assert stand.development_type == "SPF_SBPS"
    assert stand.total_burned_vol == 30.0


def test_development_type_record() -> None:
    development_type = DevelopmentType(
        development_type="SPF_SBPS",
        bec_zone="SBPS",
        species_group="SPF",
        stand_count=5,
        area_ha=50.0,
        total_green_vol=500.0,
        total_burned_vol=0.0,
    )

    assert development_type.stand_count == 5


def test_diagnostic_record() -> None:
    diagnostic = Diagnostic(severity="warning", code="test", message="hello")

    assert diagnostic.severity == "warning"
    assert diagnostic.model_dump() == {
        "severity": "warning",
        "code": "test",
        "message": "hello",
        "context": {},
    }
