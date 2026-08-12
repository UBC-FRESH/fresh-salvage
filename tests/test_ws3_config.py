"""WS3RunConfig round-trip, defaults, and pure WS3 solve helpers.

These tests never run a WS3 solve (too slow for unit tests); the integration
path is covered by ``run_smoke_test``. The pure helpers in
``fresh_salvage.ws3`` are importable without the external ws3 package because
that dependency is loaded lazily inside the solve functions.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from fresh_salvage import ws3
from fresh_salvage.models import WS3Result, WS3RunConfig


def _sample_config(tmp_path: Path) -> WS3RunConfig:
    return WS3RunConfig(
        run_id="roundtrip-ws3",
        bridge_path=Path("/data/ws3_bridge"),
        base_year=2025,
        horizon=30,
        output_root=tmp_path / "out",
    )


def test_ws3_config_json_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "ws3.json"
    config = _sample_config(tmp_path)

    written = config.write_json(config_path)
    restored = WS3RunConfig.read(written)

    assert written == config_path
    assert restored == config
    assert restored.bridge_path == Path("/data/ws3_bridge")
    assert restored.output_root == tmp_path / "out"


def test_ws3_config_yaml_read(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ws3.yaml"
    yaml_path.write_text(
        "run_id: yaml-ws3\n"
        "bridge_path: /data/ws3_bridge\n"
        "base_year: 2025\n"
        "horizon: 10\n"
        "output_root: out\n",
        encoding="utf-8",
    )

    config = WS3RunConfig.read(yaml_path)

    assert config.run_id == "yaml-ws3"
    assert config.horizon == 10
    assert config.period_length == 10


def test_ws3_config_defaults(tmp_path: Path) -> None:
    config = _sample_config(tmp_path)

    assert config.period_length == 10
    assert config.max_age == 999
    assert config.workers == 64
    assert config.aac_annual_m3 == 2_937_509
    assert config.age_smashing.enabled is True
    assert config.age_smashing.width == 10
    assert config.age_smashing.midpoint == 5
    assert config.objective.action_code == "cc"
    assert config.objective.utilization == 0.85
    assert config.objective.even_flow_tolerance == 0.1


def test_ws3_config_rejects_invalid_age_smashing(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WS3RunConfig(
            bridge_path=Path("/data/ws3_bridge"),
            base_year=2025,
            horizon=3,
            output_root=tmp_path / "out",
            age_smashing={"enabled": True, "width": 10, "midpoint": 10},
        )


def test_ws3_config_has_no_landscape_units_field(tmp_path: Path) -> None:
    _ = _sample_config(tmp_path)

    assert "landscape_units" not in WS3RunConfig.model_fields


def test_smoke_config_is_deterministic(tmp_path: Path) -> None:
    bridge = Path("/data/ws3_bridge")
    output_root = tmp_path / "out"

    first = ws3.smoke_config(bridge, output_root)
    second = ws3.smoke_config(bridge, output_root)

    assert first == second
    assert first.horizon == 3
    assert first.workers == 2
    assert first.run_id == "tsa29-ws3-smoke"


def test_midpoint_age_maps_classes() -> None:
    assert ws3.midpoint_age(0) == 5
    assert ws3.midpoint_age(4) == 5
    assert ws3.midpoint_age(9) == 5
    assert ws3.midpoint_age(10) == 15
    assert ws3.midpoint_age(553) == 555


def test_midpoint_age_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        ws3.midpoint_age(10, width=0)
    with pytest.raises(ValueError):
        ws3.midpoint_age(10, width=10, midpoint=10)
    with pytest.raises(ValueError):
        ws3.midpoint_age(-1)


def test_aac_ceiling_constraints_period_scaling() -> None:
    constraints = ws3.aac_ceiling_constraints([1, 2, 3], 2_937_509, 10)

    assert constraints["lb"] == {1: 0.0, 2: 0.0, 3: 0.0}
    assert constraints["ub"] == {1: 29_375_090.0, 2: 29_375_090.0, 3: 29_375_090.0}


def test_aac_ceiling_constraints_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        ws3.aac_ceiling_constraints([1], -1.0, 10)
    with pytest.raises(ValueError):
        ws3.aac_ceiling_constraints([1], 1.0, 0)


def test_even_flow_constraints_uses_reference_period() -> None:
    epsilon_map, reference = ws3.even_flow_constraints([1, 2, 3], 0.1)

    assert epsilon_map == {1: 0.1, 2: 0.1, 3: 0.1}
    assert reference == 1


def test_even_flow_constraints_rejects_invalid_tolerance() -> None:
    with pytest.raises(ValueError):
        ws3.even_flow_constraints([1], 1.0)
    with pytest.raises(ValueError):
        ws3.even_flow_constraints([], 0.1)


def test_normalize_status_recognizes_optimal() -> None:
    assert ws3.normalize_status("optimal") == "optimal"
    assert ws3.normalize_status("status_optimal") == "optimal"
    assert ws3.normalize_status("HighsModelStatus.kOptimal") == "optimal"
    assert ws3.normalize_status("infeasible") == "infeasible"


def test_ws3_result_summary_is_deterministic(tmp_path: Path) -> None:
    result = WS3Result(
        run_id="test",
        status="optimal",
        periods=3,
        period_length=10,
        objective_value=1000.0,
        schedule_row_counts={"total": 5},
        per_period_volumes_m3={"1": 500.0, "2": 300.0},
        per_period_area_ha={"1": 100.0, "2": 60.0},
        solve_seconds=1.25,
        data_path=tmp_path / "data.parquet",
        csv_path=tmp_path / "data.csv",
        manifest_path=tmp_path / "manifest.json",
    )

    assert result.summary() == result.summary()
    assert result.summary()["status"] == "optimal"
    assert result.summary()["objective_value"] == 1000.0
    assert result.summary()["solve_seconds"] == 1.25
