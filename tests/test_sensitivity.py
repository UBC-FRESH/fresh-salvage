"""One-at-a-time sensitivity expansion and comparison tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from fresh_salvage import sensitivity
from fresh_salvage.models import (
    BinarySearchResult,
    SensitivityConfig,
    SensitivityFlipPoint,
    SensitivityObservation,
)


def _config(tmp_path: Path, parameters: dict[str, list[object]]) -> SensitivityConfig:
    return SensitivityConfig(
        sensitivity_id="test-sensitivity",
        base={
            "stands_path": str(tmp_path / "stands.parquet"),
            "yields_path": str(tmp_path / "yields.csv"),
            "bridge_path": str(tmp_path / "bridge"),
            "horizon": 3,
            "period_length": 1,
            "steps": 1,
            "workers": 1,
        },
        parameters=parameters,
        output_root=tmp_path / "sensitivity",
    )


def test_expand_one_parameter_adds_baseline_and_only_changes_one_field(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, {"decay_rate": [0.5, 0.9]})

    specs = sensitivity.expand_one_parameter(config, "decay_rate")

    assert [spec.value for spec in specs] == [0.85, 0.5, 0.9]
    assert [spec.run_config.decay_rate for spec in specs] == [0.85, 0.5, 0.9]
    assert all(spec.run_config.steps == 1 for spec in specs)


def test_expand_one_parameter_rejects_unknown_and_reserved_fields(tmp_path: Path) -> None:
    config = _config(tmp_path, {"not_a_field": [1.0]})

    with pytest.raises(sensitivity.SensitivityError) as unknown:
        sensitivity.expand_one_parameter(config, "not_a_field")
    assert unknown.value.code == "sensitivity_parameter_unknown"

    reserved = _config(tmp_path, {"output_root": ["other"]})
    with pytest.raises(sensitivity.SensitivityError) as reserved_error:
        sensitivity.expand_one_parameter(reserved, "output_root")
    assert reserved_error.value.code == "sensitivity_parameter_reserved"


def test_analyze_sensitivity_reports_baseline_range_and_direction() -> None:
    observations = [
        SensitivityObservation(
            parameter="subsidy_rate_per_m3",
            value=0.0,
            scenario_name="zero",
            status="optimal",
            baseline=True,
            metrics={"total_burned_harvest_m3": 10.0},
        ),
        SensitivityObservation(
            parameter="subsidy_rate_per_m3",
            value=3.0,
            scenario_name="three",
            status="optimal",
            metrics={"total_burned_harvest_m3": 25.0},
        ),
        SensitivityObservation(
            parameter="subsidy_rate_per_m3",
            value=6.0,
            scenario_name="six",
            status="optimal",
            metrics={"total_burned_harvest_m3": 40.0},
        ),
    ]

    comparisons = sensitivity.analyze_sensitivity(observations)
    comparison = next(item for item in comparisons if item.metric == "total_burned_harvest_m3")

    assert comparison.baseline_value == 10.0
    assert comparison.minimum == 10.0
    assert comparison.maximum == 40.0
    assert comparison.range_value == 30.0
    assert comparison.monotonic is True
    assert comparison.direction == "increasing"


def test_flip_point_search_requires_subsidy_axis(tmp_path: Path) -> None:
    payload = _config(tmp_path, {"decay_rate": [0.5]}).model_dump()
    payload["flip_point_search"] = {
        "axis": "decay_rate",
        "upper": 30.0,
        "iterations": 8,
    }
    with pytest.raises(ValueError, match="flip_point_search.axis"):
        SensitivityConfig(**payload)


def test_flip_point_searches_each_non_subsidy_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        {"subsidy_rate_per_m3": [0.0], "decay_rate": [0.5]},
    )
    payload = config.model_dump()
    payload["flip_point_search"] = {
        "axis": "subsidy_rate_per_m3",
        "upper": 30.0,
        "iterations": 8,
    }
    config = SensitivityConfig(**payload)
    calls = []

    def fake_run_ensemble(config, *, verbose):
        calls.append(config)
        return SimpleNamespace(
            status="ok",
            manifest_path=tmp_path / "flip-manifest.json",
            binary_search_result=BinarySearchResult(
                lower=23.75,
                upper=24.0625,
                bracket_width=0.3125,
                midpoint_probes=5,
                converged=True,
            ),
        )

    monkeypatch.setattr(sensitivity.ensemble, "run_ensemble", fake_run_ensemble)

    flip_points = sensitivity._run_flip_point_searches(config, verbose=False)

    assert [item.value for item in flip_points] == [0.85, 0.5]
    assert all(item.parameter == "decay_rate" for item in flip_points)
    assert all(item.converged for item in flip_points)
    assert all(call.max_workers == 1 for call in calls)


def test_flip_point_search_records_an_unbracketed_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, {"burn_rate_multiplier": [0.0]})
    payload = config.model_dump()
    payload["flip_point_search"] = {
        "axis": "subsidy_rate_per_m3",
        "upper": 30.0,
        "iterations": 8,
    }
    config = SensitivityConfig(**payload)

    def fake_run_ensemble(config, *, verbose):
        raise sensitivity.ensemble.EnsembleError(
            "ensemble_binary_upper_inactive", "no burned salvage"
        )

    monkeypatch.setattr(sensitivity.ensemble, "run_ensemble", fake_run_ensemble)

    flip_points = sensitivity._run_flip_point_searches(config, verbose=False)

    assert len(flip_points) == 2
    assert all(item.status == "not_bracketed" for item in flip_points)
    assert all(item.error_code == "ensemble_binary_upper_inactive" for item in flip_points)


def test_results_report_identifies_settings_scenarios_and_changes(tmp_path: Path) -> None:
    config = _config(tmp_path, {"subsidy_rate_per_m3": [0.0, 6.0]})
    result = sensitivity._make_result(
        "test-sensitivity",
        [
            SensitivityObservation(
                parameter="subsidy_rate_per_m3",
                value=3.0,
                scenario_name="baseline",
                status="optimal",
                baseline=True,
                metrics={
                    "total_green_harvest_m3": 100.0,
                    "total_burned_harvest_m3": 10.0,
                    "total_area_burned_ha": 5.0,
                    "total_principal_objective_value": 50.0,
                    "total_agent_objective_value": 40.0,
                },
            ),
            SensitivityObservation(
                parameter="subsidy_rate_per_m3",
                value=6.0,
                scenario_name="six",
                status="optimal",
                metrics={
                    "total_green_harvest_m3": 100.0,
                    "total_burned_harvest_m3": 25.0,
                    "total_area_burned_ha": 5.0,
                    "total_principal_objective_value": 60.0,
                    "total_agent_objective_value": 55.0,
                },
            ),
        ],
        1.0,
        flip_points=[
            SensitivityFlipPoint(
                parameter="decay_rate",
                value=0.5,
                status="ok",
                lower=23.75,
                upper=24.0625,
                bracket_width=0.3125,
                midpoint_probes=5,
                converged=True,
            )
        ],
    )

    sensitivity._write_artifacts(result, config)

    assert result.report_path is not None
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Effective Baseline Settings" in report
    assert "## Scenarios Examined" in report
    assert "## Results: `subsidy_rate_per_m3`" in report
    assert "### Change From Baseline" in report
    assert "## Subsidy Flip Points" in report
    assert "23.75" in report
    assert "+15" in report
    assert result.flip_points_path is not None
