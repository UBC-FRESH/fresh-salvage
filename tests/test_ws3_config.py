"""WS3RunConfig round-trip, defaults, and pure WS3 solve helpers.

These tests never run a WS3 solve (too slow for unit tests); the integration
path is covered by ``run_smoke_test``. The pure helpers in
``fresh_salvage.ws3`` are importable without the external ws3 package because
that dependency is loaded lazily inside the solve functions.
"""

from pathlib import Path

import pandas as pd
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


def _require_femic_bridge_writer() -> None:
    """Skip the current test when femic's stage-2 writer is not importable."""

    try:
        ws3._load_femic_bridge_writer()
    except ws3.WS3Error as exc:
        pytest.skip(f"femic bridge writer unavailable: {exc}")


def _write_synthetic_stage1(tmp_path: Path) -> Path:
    """Write a minimal femic stage-1 Woodstock package and return its directory.

    The three area fragments share one ``(tsa, ifm, au_id)`` key but carry two
    landscape units and raw ages 23/25/27, so the rebuilt bridge must smash all
    of them to class midpoint 25 and sum them into a single 35 ha ARE row.
    """

    stage1 = tmp_path / "woodstock"
    stage1.mkdir()
    (stage1 / "woodstock_yields.csv").write_text(
        "tsa,au_id,stratum_code,si_level,ifm,curve_id,age,volume\n"
        "29,2901000,sbps_pli,high,managed,2921000,0,0.0\n"
        "29,2901000,sbps_pli,high,managed,2921000,10,50.0\n",
        encoding="utf-8",
    )
    (stage1 / "woodstock_areas.csv").write_text(
        "stand_id,tsa,au_id,ifm,age,area_ha,landscape_unit_id\n"
        "1,29,2901000,managed,23,10.0,1375\n"
        "2,29,2901000,managed,27,20.0,1376\n"
        "3,29,2901000,managed,25,5.0,1375\n",
        encoding="utf-8",
    )
    (stage1 / "woodstock_actions.csv").write_text(
        "tsa,au_id,action_id,from_ifm,to_ifm,min_age,max_age,managed_curve_id\n"
        "29,2901000,cc,managed,managed,0,1000,2921000\n",
        encoding="utf-8",
    )
    (stage1 / "woodstock_transitions.csv").write_text(
        "tsa,au_id,action_id,from_ifm,to_ifm,next_au_id\n"
        "29,2901000,cc,managed,managed,2901000\n",
        encoding="utf-8",
    )
    return stage1


def _write_canonical_lu_bridge(stage1: Path) -> Path:
    """Write a canonical bridge stub whose LAN still carries the LU theme."""

    bridge = stage1 / "ws3_bridge"
    bridge.mkdir()
    (bridge / "femic_tsa_ws3.lan").write_text(
        "*THEME Timber Supply Area (TSA)\n"
        "29\n"
        "*THEME Managed state\n"
        "managed\n"
        "*THEME Analysis Unit (AU)\n"
        "2901000\n"
        "*THEME Stratum code\n"
        "sbps_pli\n"
        "*THEME Yield curve ID\n"
        "2921000\n"
        "*THEME Landscape Unit\n"
        "1375\n"
        "1376\n"
        "*AGGREGATE masc_lu_subset\n"
        "1375\n",
        encoding="utf-8",
    )
    return bridge


def _are_data_lines(bridge: Path) -> list[list[str]]:
    """Return the whitespace-split ARE data rows of a rebuilt bridge."""

    are_text = (bridge / "femic_tsa_ws3.are").read_text(encoding="utf-8")
    return [line.split() for line in are_text.splitlines() if line.startswith("*A")]


def test_build_smashed_no_lu_bridge_aggregates_duplicate_keys(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    dest = tmp_path / "out" / ws3.DERIVED_BRIDGE_DIRNAME

    built = ws3.build_smashed_no_lu_bridge(stage1, dest)

    assert built == dest
    rows = _are_data_lines(dest)
    assert rows == [["*A", "29", "managed", "2901000", "sbps_pli", "2921000", "25", "35.000000"]]


def test_build_smashed_no_lu_bridge_smashes_every_age(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    (stage1 / "woodstock_areas.csv").write_text(
        "stand_id,tsa,au_id,ifm,age,area_ha,landscape_unit_id\n"
        "1,29,2901000,managed,23,10.0,1375\n"
        "2,29,2901000,managed,51,20.0,1376\n"
        "3,29,2901000,managed,109,5.0,1375\n",
        encoding="utf-8",
    )
    dest = tmp_path / "out" / ws3.DERIVED_BRIDGE_DIRNAME

    ws3.build_smashed_no_lu_bridge(stage1, dest)

    ages = [int(row[-2]) for row in _are_data_lines(dest)]
    assert ages == [25, 55, 105]
    assert all(age % 10 == 5 for age in ages)


def test_build_smashed_no_lu_bridge_writes_five_themes(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    dest = tmp_path / "out" / ws3.DERIVED_BRIDGE_DIRNAME

    ws3.build_smashed_no_lu_bridge(stage1, dest)

    lan_text = (dest / "femic_tsa_ws3.lan").read_text(encoding="utf-8")
    assert "*THEME Landscape Unit" not in lan_text
    assert len(ws3._split_theme_blocks(lan_text)) == 5
    staged = (dest.parent / ws3.STAGE1_DERIVED_DIRNAME / "woodstock_areas.csv").read_text(
        encoding="utf-8"
    )
    assert "landscape_unit_id" not in staged.splitlines()[0]


def test_build_smashed_no_lu_bridge_fails_on_incomplete_stage1(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = tmp_path / "woodstock"
    stage1.mkdir()

    with pytest.raises(ws3.WS3Error) as excinfo:
        ws3.build_smashed_no_lu_bridge(stage1, tmp_path / "out")

    assert excinfo.value.code == "ws3_stage1_incomplete"


def test_build_smashed_no_lu_bridge_conserves_staged_area(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    dest = tmp_path / "out" / ws3.DERIVED_BRIDGE_DIRNAME

    ws3.build_smashed_no_lu_bridge(stage1, dest)

    staged_area_ha = float(
        pd.read_csv(dest.parent / ws3.STAGE1_DERIVED_DIRNAME / "woodstock_areas.csv")[
            "area_ha"
        ].sum()
    )
    written_area_ha = sum(float(row[-1]) for row in _are_data_lines(dest))
    assert written_area_ha == pytest.approx(
        staged_area_ha, rel=ws3.AREA_CONSERVATION_REL_TOLERANCE
    )


def test_build_smashed_no_lu_bridge_fails_when_writer_drops_area(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    # au_id 2999999 has no yield curve, so femic's writer silently dropna-discards
    # the row: written ARE conserves 35.0 ha of the staged 42.5 ha.
    (stage1 / "woodstock_areas.csv").write_text(
        "stand_id,tsa,au_id,ifm,age,area_ha,landscape_unit_id\n"
        "1,29,2901000,managed,23,10.0,1375\n"
        "2,29,2901000,managed,27,20.0,1376\n"
        "3,29,2901000,managed,25,5.0,1375\n"
        "4,29,2999999,managed,25,7.5,1375\n",
        encoding="utf-8",
    )

    with pytest.raises(ws3.WS3Error) as excinfo:
        ws3.build_smashed_no_lu_bridge(stage1, tmp_path / "out")

    assert excinfo.value.code == "area_conservation_failed"
    message = str(excinfo.value)
    assert "35.000000" in message
    assert "42.500000" in message
    assert "-7.500000" in message


def test_build_smashed_no_lu_bridge_fails_on_invalid_age(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    (stage1 / "woodstock_areas.csv").write_text(
        "stand_id,tsa,au_id,ifm,age,area_ha,landscape_unit_id\n"
        "1,29,2901000,managed,twenty-three,10.0,1375\n"
        "2,29,2901000,managed,27,20.0,1376\n",
        encoding="utf-8",
    )

    with pytest.raises(ws3.WS3Error) as excinfo:
        ws3.build_smashed_no_lu_bridge(stage1, tmp_path / "out")

    assert excinfo.value.code == "invalid_age_values"
    assert "1 rows" in str(excinfo.value)
    assert "twenty-three" in str(excinfo.value)


def test_verify_smashed_bridge_missing_are_raises_ws3_error(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "femic_tsa_ws3.lan").write_text("*THEME Managed state\nmanaged\n", encoding="utf-8")

    with pytest.raises(ws3.WS3Error) as excinfo:
        ws3._verify_smashed_bridge(bridge)

    assert excinfo.value.code == "ws3_bridge_are_missing"


def test_verify_smashed_bridge_malformed_are_raises_ws3_error(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "femic_tsa_ws3.lan").write_text("*THEME Managed state\nmanaged\n", encoding="utf-8")
    (bridge / "femic_tsa_ws3.are").write_text(
        "*A 29 managed 2901000 sbps_pli 2921000 notanage 10.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ws3.WS3Error) as excinfo:
        ws3._verify_smashed_bridge(bridge)

    assert excinfo.value.code == "ws3_bridge_are_unparseable"


def test_resolved_bridge_path_rebuilds_lu_free_bridge(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    canonical = _write_canonical_lu_bridge(stage1)
    config = WS3RunConfig(
        run_id="resolve-test",
        bridge_path=canonical,
        base_year=2025,
        horizon=3,
        output_root=tmp_path / "out",
    )

    resolved = ws3.resolved_bridge_path(config)

    assert resolved == ws3.derived_bridge_path(tmp_path / "out")
    lan_text = (resolved / "femic_tsa_ws3.lan").read_text(encoding="utf-8")
    assert "*THEME Landscape Unit" not in lan_text
    assert all(int(row[-2]) % 10 == 5 for row in _are_data_lines(resolved))


def test_resolved_bridge_path_passes_through_derived_bridge(tmp_path: Path) -> None:
    _require_femic_bridge_writer()
    stage1 = _write_synthetic_stage1(tmp_path)
    canonical = _write_canonical_lu_bridge(stage1)
    config = WS3RunConfig(
        bridge_path=canonical,
        base_year=2025,
        horizon=3,
        output_root=tmp_path / "out",
    )
    derived = ws3.resolved_bridge_path(config)

    resolved = ws3.resolved_bridge_path(config.model_copy(update={"bridge_path": derived}))

    assert resolved == derived


class _StubDevelopmentType:
    """Minimal ws3 DevelopmentType stand-in with the enforced attributes."""

    def __init__(self, curve_id: str) -> None:
        self.key = ("29", "managed", "2901000", "sbps_pli", curve_id)
        self.oper_expr = {"cc": ["_age >= 0 and _age <= 1000"]}


class _StubModel:
    """Minimal ws3 ForestModel stand-in tracking action compilation."""

    def __init__(self, development_types: list[_StubDevelopmentType]) -> None:
        self.dtypes = {index: dt for index, dt in enumerate(development_types)}
        self.compile_calls = 0

    def compile_actions(self) -> None:
        self.compile_calls += 1


def test_enforce_harvest_age_range_applies_60_to_300_bounds() -> None:
    merchantable = _StubDevelopmentType("2901000")
    never_merchantable = _StubDevelopmentType("2921000")
    model = _StubModel([merchantable, never_merchantable])

    summary = ws3.enforce_harvest_age_range(model)

    assert merchantable.oper_expr["cc"] == ["_age >= 60 and _age <= 300"]
    assert "cc" not in never_merchantable.oper_expr
    assert summary["min_harvest_age"] == 60
    assert summary["max_harvest_age"] == 300
    assert summary["oper_expr_popped"] == 1
    assert summary["oper_expr_rewritten"] == 1
    assert model.compile_calls == 1


def test_enforce_harvest_age_range_skips_types_without_the_action() -> None:
    no_cc = _StubDevelopmentType("2901000")
    no_cc.oper_expr = {}
    model = _StubModel([no_cc])

    summary = ws3.enforce_harvest_age_range(model)

    assert "cc" not in no_cc.oper_expr
    assert summary["oper_expr_popped"] == 0
    assert summary["oper_expr_rewritten"] == 0
    assert model.compile_calls == 1


def test_problem_lp_dimensions_counts_rows_and_columns() -> None:
    class _StubProblem:
        _constraints = {"a": 1, "b": 2}
        _vars = {"x": 1, "y": 2, "z": 3}

    assert ws3.problem_lp_dimensions(_StubProblem()) == {
        "lp_rows": 2,
        "lp_columns": 3,
    }
