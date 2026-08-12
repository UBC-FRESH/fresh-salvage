"""Model record round-trip and layout tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from fresh_salvage import data
from fresh_salvage.models import (
    ARTIFACT_DIRECTORIES,
    ArtifactLayout,
    DevelopmentType,
    Diagnostic,
    Economics,
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
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        harvest_cost_green=45.0,
        harvest_cost_burned=56.0,
        subsidy_total=90.0,
        stumpage_green_total=1500.0,
        stumpage_burned_total=7.5,
        green_prices={"SPF_Sawlog": 127.0},
        burned_prices={"SPF_Sawlog": 82.55},
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


def test_economics_defaults_match_the_calibrated_data_constants() -> None:
    economics = Economics()

    assert economics.green_prices == data.GREEN_PRICES
    assert economics.burned_price_discount == pytest.approx(data.BURNED_PRICE_DISCOUNT)
    assert economics.green_harvest_cost == pytest.approx(data.GREEN_HARVEST_COST)
    assert economics.burned_harvest_cost == pytest.approx(data.BURNED_HARVEST_COST)
    assert economics.green_transport_cost_per_m3 == pytest.approx(
        data.TRANSPORT_COST_PER_M3
    )
    assert economics.burned_transport_cost_per_m3 == pytest.approx(
        data.BURNED_TRANSPORT_COST_PER_M3
    )
    assert economics.green_stumpage_rate == pytest.approx(data.GREEN_STUMPAGE_RATE)
    assert economics.burned_stumpage_rate == pytest.approx(data.BURNED_STUMPAGE_RATE)
    assert economics.subsidy_rate_per_m3 == pytest.approx(data.SUBSIDY_RATE_PER_M3)
    assert economics.burned_prices() == data.BURNED_PRICES


def test_economics_validation_fails_fast() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        Economics(burned_harvest_cost=-1.0)
    with pytest.raises(ValidationError, match=r"burned_price_discount must lie"):
        Economics(burned_price_discount=1.5)
    with pytest.raises(ValidationError, match="canonical grade price keys"):
        Economics(green_prices={"SPF_Sawlog": 127.0})


def test_rh_run_config_assembles_economics_from_flat_fields(tmp_path: Path) -> None:
    from fresh_salvage.models import RHRunConfig

    config = RHRunConfig(
        stands_path=tmp_path / "stands.parquet",
        yields_path=tmp_path / "yields.csv",
        bridge_path=tmp_path / "bridge",
        output_root=tmp_path / "out",
        subsidy_rate_per_m3=12.0,
        burned_harvest_cost=70.0,
        green_transport_cost_per_m3=28.0,
    )

    economics = config.economics()

    assert economics.subsidy_rate_per_m3 == pytest.approx(12.0)
    assert economics.burned_harvest_cost == pytest.approx(70.0)
    assert economics.green_transport_cost_per_m3 == pytest.approx(28.0)
    # Untouched fields keep the calibrated data.py defaults.
    assert economics.green_harvest_cost == pytest.approx(data.GREEN_HARVEST_COST)
    assert economics.burned_stumpage_rate == pytest.approx(data.BURNED_STUMPAGE_RATE)
    assert economics.green_prices == data.GREEN_PRICES


def test_rh_run_config_economic_fields_are_ensemble_axes(tmp_path: Path) -> None:
    """Every flat economic field is a valid ensemble axis (RHRunConfig field)."""

    from fresh_salvage.ensemble import expand_scenarios
    from fresh_salvage.models import EnsembleConfig

    config = EnsembleConfig(
        base={
            "stands_path": str(tmp_path / "stands.parquet"),
            "yields_path": str(tmp_path / "yields.csv"),
            "bridge_path": str(tmp_path / "bridge"),
        },
        axes={"burned_transport_cost_per_m3": [41.0, 50.0]},
        output_root=tmp_path / "ens",
    )

    specs = expand_scenarios(config)

    assert [spec.run_config.burned_transport_cost_per_m3 for spec in specs] == [
        41.0,
        50.0,
    ]
    assert specs[0].run_config.economics().burned_transport_cost_per_m3 == 41.0


def test_scenario_config_economics_section_is_config_visible(tmp_path: Path) -> None:
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        "run_id: econ-run\n"
        "inputs:\n"
        "  wl_vfsl_path: /data/WL_VFSL.csv\n"
        "  output_root: out\n"
        "economics:\n"
        "  subsidy_rate_per_m3: 12.0\n"
        "  green_harvest_cost: 50.0\n",
        encoding="utf-8",
    )

    scenario = ScenarioRunConfig.read(config_path)

    assert scenario.economics.subsidy_rate_per_m3 == pytest.approx(12.0)
    assert scenario.economics.green_harvest_cost == pytest.approx(50.0)
    assert scenario.economics.green_prices == data.GREEN_PRICES
