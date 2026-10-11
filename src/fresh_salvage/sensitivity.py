"""One-at-a-time sensitivity analysis over rolling-horizon parameters.

The existing ensemble driver evaluates Cartesian products. This module uses
the same runner one parameter at a time, ensuring that every comparison holds
all other ``RHRunConfig`` fields at their baseline values.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fresh_salvage import ensemble
from fresh_salvage.models import (
    ArtifactLayout,
    RHManifest,
    RHRunConfig,
    SensitivityComparison,
    SensitivityConfig,
    SensitivityFlipPoint,
    SensitivityManifest,
    SensitivityObservation,
    SensitivityResult,
    safe_slug,
)

SENSITIVITY_RESERVED_FIELDS = ("run_id", "output_root", "bridge_path")
SENSITIVITY_METRICS = (
    "total_green_harvest_m3",
    "total_burned_harvest_m3",
    "total_area_burned_ha",
    "total_ws3_objective_value",
    "total_principal_objective_value",
    "total_agent_objective_value",
    "final_cohort_count",
    "wall_seconds",
)

REPORT_METRICS = (
    "total_green_harvest_m3",
    "total_burned_harvest_m3",
    "total_area_burned_ha",
    "total_principal_objective_value",
    "total_agent_objective_value",
)

METRIC_LABELS = {
    "total_green_harvest_m3": "Green harvest (m3)",
    "total_burned_harvest_m3": "Salvage harvest (m3)",
    "total_area_burned_ha": "Area burned (ha)",
    "total_ws3_objective_value": "WS3 objective value",
    "total_principal_objective_value": "Principal objective value",
    "total_agent_objective_value": "Agent objective value",
    "final_cohort_count": "Final cohort count",
    "wall_seconds": "Wall time (s)",
}


class SensitivityError(RuntimeError):
    """Fatal sensitivity configuration or analysis failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SensitivitySpec:
    """One parameter value and its validated rolling-horizon config."""

    parameter: str
    value: object
    run_config: RHRunConfig


def eligible_parameters() -> tuple[str, ...]:
    """Return RH fields suitable for sensitivity axes by default."""

    excluded = set(SENSITIVITY_RESERVED_FIELDS) | {
        "stands_path",
        "yields_path",
        "workers",
        "metadata",
        "age_smashing",
        "objective",
    }
    return tuple(sorted(set(RHRunConfig.model_fields) - excluded))


def expand_one_parameter(config: SensitivityConfig, parameter: str) -> list[SensitivitySpec]:
    """Expand one parameter into isolated, validated scenarios.

    The baseline value is automatically included, even when it is absent from
    the configured values. The bridge remains in ``base`` and is shared by the
    underlying ensemble runner.
    """

    _validate_parameter_name(parameter)
    _validate_base(config)
    if parameter not in config.parameters:
        raise SensitivityError(
            "sensitivity_parameter_missing",
            f"parameter {parameter!r} has no configured values",
        )
    values = list(config.parameters[parameter])
    if not values:
        raise SensitivityError(
            "sensitivity_parameter_empty", f"parameter {parameter!r} has no values"
        )
    baseline = _baseline_config(config)
    baseline_value = getattr(baseline, parameter)
    if not any(value == baseline_value for value in values):
        values.insert(0, baseline_value)

    specs: list[SensitivitySpec] = []
    seen: list[object] = []
    for value in values:
        if any(value == prior for prior in seen):
            continue
        seen.append(value)
        fields = {**config.base, parameter: value}
        try:
            run_config = RHRunConfig(
                run_id=f"{config.sensitivity_id}-{safe_slug(parameter)}-{safe_slug(str(value))}",
                output_root=Path(config.output_root) / safe_slug(parameter) / safe_slug(str(value)),
                **fields,
            )
        except Exception as exc:
            raise SensitivityError(
                "sensitivity_value_invalid",
                f"value {value!r} is invalid for parameter {parameter!r}: {exc}",
            ) from exc
        specs.append(SensitivitySpec(parameter, value, run_config))
    return specs


def analyze_sensitivity(
    observations: list[SensitivityObservation],
) -> list[SensitivityComparison]:
    """Compare successful observations against each parameter's baseline."""

    comparisons: list[SensitivityComparison] = []
    parameters = sorted({observation.parameter for observation in observations})
    for parameter in parameters:
        group = [
            observation
            for observation in observations
            if observation.parameter == parameter and observation.status != "failed"
        ]
        baseline = next((item for item in group if item.baseline), None)
        if baseline is None:
            continue
        for metric in SENSITIVITY_METRICS:
            pairs = [
                (observation.value, observation.metrics.get(metric))
                for observation in group
                if isinstance(observation.metrics.get(metric), (int, float))
                and math.isfinite(float(observation.metrics[metric]))
            ]
            if not pairs:
                continue
            values = [float(value) for _value, value in pairs]
            baseline_value = baseline.metrics.get(metric)
            if not isinstance(baseline_value, (int, float)):
                baseline_number = None
                absolute_change = None
                relative_change = None
            else:
                baseline_number = float(baseline_value)
                absolute_change = max(values) - baseline_number
                relative_change = (
                    absolute_change / baseline_number if baseline_number != 0.0 else None
                )
            ordered = sorted(
                ((float(value), metric_value) for value, metric_value in pairs),
                key=lambda item: item[0],
            )
            deltas = [right - left for (_x1, left), (_x2, right) in zip(ordered, ordered[1:])]
            increasing = all(delta >= 0.0 for delta in deltas)
            decreasing = all(delta <= 0.0 for delta in deltas)
            monotonic = increasing or decreasing
            direction = (
                "flat"
                if all(delta == 0.0 for delta in deltas)
                else (
                    "increasing" if increasing else "decreasing" if decreasing else "non-monotonic"
                )
            )
            comparisons.append(
                SensitivityComparison(
                    parameter=parameter,
                    metric=metric,
                    baseline_value=baseline_number,
                    minimum=min(values),
                    maximum=max(values),
                    absolute_change=absolute_change,
                    relative_change=relative_change,
                    range_value=max(values) - min(values),
                    monotonic=monotonic,
                    direction=direction,
                )
            )
    return comparisons


def run_parameter_sensitivity(
    config: SensitivityConfig, parameter: str, *, verbose: bool = False
) -> SensitivityResult:
    """Run and analyze one parameter-at-a-time sensitivity sweep."""

    started = time.monotonic()
    specs = expand_one_parameter(config, parameter)
    axis_config = ensemble.EnsembleConfig(
        ensemble_id=f"{config.sensitivity_id}-{safe_slug(parameter)}",
        base=config.base,
        axes={parameter: [spec.value for spec in specs]},
        max_workers=config.max_workers,
        output_root=Path(config.output_root) / safe_slug(parameter),
        metadata=config.metadata,
    )
    ensemble_result = ensemble.run_ensemble(axis_config, verbose=verbose)
    baseline_value = getattr(_baseline_config(config), parameter)
    observations = [
        _observation(parameter, record, baseline_value) for record in ensemble_result.scenarios
    ]
    result = _make_result(config.sensitivity_id, observations, time.monotonic() - started)
    _write_artifacts(result, config)
    return result


def run_all_sensitivities(config: SensitivityConfig, *, verbose: bool = False) -> SensitivityResult:
    """Run every configured parameter independently and collect all results."""

    started = time.monotonic()
    if not config.parameters:
        raise SensitivityError("sensitivity_no_parameters", "at least one parameter is required")
    observations: list[SensitivityObservation] = []
    for parameter in sorted(config.parameters):
        specs = expand_one_parameter(config, parameter)
        axis_config = ensemble.EnsembleConfig(
            ensemble_id=f"{config.sensitivity_id}-{safe_slug(parameter)}",
            base=config.base,
            axes={parameter: [spec.value for spec in specs]},
            max_workers=config.max_workers,
            output_root=Path(config.output_root) / safe_slug(parameter),
            metadata=config.metadata,
        )
        outcome = ensemble.run_ensemble(axis_config, verbose=verbose)
        baseline_value = getattr(_baseline_config(config), parameter)
        observations.extend(
            _observation(parameter, record, baseline_value) for record in outcome.scenarios
        )
    flip_points = _run_flip_point_searches(config, verbose=verbose)
    result = _make_result(
        config.sensitivity_id,
        observations,
        time.monotonic() - started,
        flip_points=flip_points,
    )
    _write_artifacts(result, config)
    return result


def _validate_base(config: SensitivityConfig) -> None:
    try:
        RHRunConfig(
            run_id=config.sensitivity_id,
            output_root=config.output_root,
            **config.base,
        )
    except Exception as exc:
        raise SensitivityError("sensitivity_base_invalid", str(exc)) from exc


def _run_flip_point_searches(
    config: SensitivityConfig, *, verbose: bool
) -> list[SensitivityFlipPoint]:
    """Locate the subsidy flip separately for every non-subsidy setting."""

    search = config.flip_point_search
    if search is None:
        return []
    flip_points: list[SensitivityFlipPoint] = []
    for parameter in sorted(config.parameters):
        if parameter == search.axis:
            continue
        baseline_value = getattr(_baseline_config(config), parameter)
        for spec in expand_one_parameter(config, parameter):
            try:
                outcome = ensemble.run_ensemble(
                    ensemble.EnsembleConfig(
                        ensemble_id=(
                            f"{config.sensitivity_id}-flip-{safe_slug(parameter)}-"
                            f"{safe_slug(str(spec.value))}"
                        ),
                        base={**config.base, parameter: spec.value},
                        binary_search=search,
                        max_workers=1,
                        output_root=(
                            Path(config.output_root)
                            / "flip-points"
                            / safe_slug(parameter)
                            / safe_slug(str(spec.value))
                        ),
                        metadata=config.metadata,
                    ),
                    verbose=verbose,
                )
            except ensemble.EnsembleError as exc:
                if exc.code not in {
                    "ensemble_binary_lower_active",
                    "ensemble_binary_upper_inactive",
                }:
                    raise
                flip_points.append(
                    SensitivityFlipPoint(
                        parameter=parameter,
                        value=spec.value,
                        baseline=spec.value == baseline_value,
                        status="not_bracketed",
                        error_code=exc.code,
                    )
                )
                continue
            result = outcome.binary_search_result
            if result is None:  # pragma: no cover - run_ensemble guarantees this
                raise SensitivityError("sensitivity_flip_missing", "flip-point result is missing")
            flip_points.append(
                SensitivityFlipPoint(
                    parameter=parameter,
                    value=spec.value,
                    baseline=spec.value == baseline_value,
                    status=outcome.status,
                    lower=result.lower,
                    upper=result.upper,
                    bracket_width=result.bracket_width,
                    midpoint_probes=result.midpoint_probes,
                    converged=result.converged,
                    manifest_path=outcome.manifest_path,
                )
            )
    return flip_points


def _baseline_config(config: SensitivityConfig) -> RHRunConfig:
    """Validate the base fields using the driver-owned run fields."""

    return RHRunConfig(
        run_id=config.sensitivity_id,
        output_root=config.output_root,
        **config.base,
    )


def _validate_parameter_name(parameter: str) -> None:
    if parameter in SENSITIVITY_RESERVED_FIELDS:
        raise SensitivityError(
            "sensitivity_parameter_reserved", f"parameter {parameter!r} is reserved"
        )
    if parameter not in RHRunConfig.model_fields:
        raise SensitivityError(
            "sensitivity_parameter_unknown",
            f"parameter {parameter!r} is not an RHRunConfig field",
        )


def _observation(parameter: str, record: object, baseline_value: object) -> SensitivityObservation:
    value = record.overrides.get(parameter, baseline_value)
    metrics: dict[str, float | int | None] = {}
    if record.manifest_path is not None and record.status != "failed":
        manifest = RHManifest.read_json(record.manifest_path)
        metrics = _manifest_metrics(manifest)
    return SensitivityObservation(
        parameter=parameter,
        value=value,
        scenario_name=record.name,
        status=record.status,
        baseline=value == baseline_value,
        metrics=metrics,
        manifest_path=record.manifest_path,
        error_code=record.error_code,
    )


def _manifest_metrics(manifest: RHManifest) -> dict[str, float | int | None]:
    """Flatten a rolling-horizon manifest into comparable scalar metrics."""

    return {
        "total_green_harvest_m3": sum(
            sum(record.annual_green_harvest_m3) for record in manifest.step_records
        ),
        "total_burned_harvest_m3": sum(
            sum(record.annual_burned_harvest_m3) for record in manifest.step_records
        ),
        "total_area_burned_ha": manifest.total_area_burned_ha,
        "total_ws3_objective_value": sum(
            record.ws3_objective_value for record in manifest.step_records
        ),
        "total_principal_objective_value": sum(
            record.principal_objective_value for record in manifest.step_records
        ),
        "total_agent_objective_value": sum(
            record.agent_objective_value for record in manifest.step_records
        ),
        "final_cohort_count": manifest.cohorts,
        "wall_seconds": manifest.wall_seconds,
    }


def _make_result(
    sensitivity_id: str,
    observations: list[SensitivityObservation],
    wall_seconds: float,
    *,
    flip_points: list[SensitivityFlipPoint] | None = None,
) -> SensitivityResult:
    succeeded = sum(observation.status != "failed" for observation in observations)
    failed = len(observations) - succeeded
    return SensitivityResult(
        sensitivity_id=sensitivity_id,
        status="ok" if failed == 0 else ("failed" if succeeded == 0 else "partial"),
        parameter_count=len({observation.parameter for observation in observations}),
        scenario_count=len(observations),
        succeeded=succeeded,
        failed=failed,
        wall_seconds=wall_seconds,
        observations=observations,
        comparisons=analyze_sensitivity(observations),
        flip_points=flip_points or [],
    )


def _write_artifacts(result: SensitivityResult, config: SensitivityConfig) -> None:
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    slug = safe_slug(config.sensitivity_id)
    observations_path = layout.data_path(f"{slug}-observations", ext="jsonl")
    comparisons_path = layout.data_path(f"{slug}-comparisons", ext="csv")
    flip_points_path = layout.data_path(f"{slug}-flip-points", ext="csv")
    report_path = layout.report_path(f"{slug}-results")
    manifest_path = layout.manifest_path(f"{slug}-sensitivity-manifest")
    observations_path.write_text(
        "".join(item.model_dump_json() + "\n" for item in result.observations),
        encoding="utf-8",
    )
    pd.DataFrame([item.model_dump(mode="json") for item in result.comparisons]).to_csv(
        comparisons_path, index=False
    )
    if result.flip_points:
        pd.DataFrame([item.model_dump(mode="json") for item in result.flip_points]).to_csv(
            flip_points_path, index=False
        )
    report_path.write_text(_render_results_report(result, config), encoding="utf-8")
    manifest = SensitivityManifest(
        sensitivity_id=result.sensitivity_id,
        status=result.status,
        parameter_count=result.parameter_count,
        scenario_count=result.scenario_count,
        succeeded=result.succeeded,
        failed=result.failed,
        wall_seconds=result.wall_seconds,
        observations_path=observations_path,
        comparisons_path=comparisons_path,
        flip_points_path=flip_points_path if result.flip_points else None,
        report_path=report_path,
        config=config.model_dump(mode="json"),
    )
    manifest.write_json(manifest_path)
    result.observations_path = observations_path
    result.comparisons_path = comparisons_path
    result.flip_points_path = flip_points_path if result.flip_points else None
    result.report_path = report_path
    result.manifest_path = manifest_path


def _render_results_report(result: SensitivityResult, config: SensitivityConfig) -> str:
    """Render the run evidence as a readable one-at-a-time results document."""

    baseline = _baseline_config(config).model_dump(mode="json")
    slug = safe_slug(result.sensitivity_id)
    lines = [
        f"# Sensitivity Analysis Results: {result.sensitivity_id}",
        "",
        "## Analysis Summary",
        "",
        "This is a one-at-a-time analysis: each scenario changes one listed "
        "parameter while every other setting remains at the effective baseline.",
        "",
        f"- Status: `{result.status}`",
        f"- Parameters examined: {result.parameter_count}",
        f"- Scenarios examined: {result.scenario_count}",
        f"- Successful scenarios: {result.succeeded}",
        f"- Failed scenarios: {result.failed}",
        f"- Analysis wall time: {_format_number(result.wall_seconds)} s",
        "",
        "## Effective Baseline Settings",
        "",
        "| Setting | Value |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{name}` | `{_markdown_value(value)}` |" for name, value in sorted(baseline.items())
    )
    lines.extend(
        [
            "",
            "## Scenarios Examined",
            "",
            "| Parameter | Tested value | Baseline | Status | Error |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for observation in sorted(
        result.observations, key=lambda item: (item.parameter, str(item.value))
    ):
        lines.append(
            "| `{parameter}` | {value} | {baseline} | `{status}` | {error} |".format(
                parameter=observation.parameter,
                value=_markdown_value(observation.value),
                baseline="yes" if observation.baseline else "",
                status=observation.status,
                error=f"`{observation.error_code}`" if observation.error_code else "",
            )
        )

    for parameter in sorted({item.parameter for item in result.observations}):
        observations = sorted(
            (item for item in result.observations if item.parameter == parameter),
            key=lambda item: str(item.value),
        )
        baseline_observation = next((item for item in observations if item.baseline), None)
        lines.extend(
            [
                "",
                f"## Results: `{parameter}`",
                "",
                "### Outcome By Setting",
                "",
                "| Setting | Status | "
                + " | ".join(METRIC_LABELS[metric] for metric in REPORT_METRICS)
                + " |",
                "| ---: | --- | " + " | ".join("---:" for _ in REPORT_METRICS) + " |",
            ]
        )
        for observation in observations:
            values = " | ".join(
                _format_metric(observation.metrics.get(metric)) for metric in REPORT_METRICS
            )
            lines.append(
                f"| {_markdown_value(observation.value)} | `{observation.status}` | {values} |"
            )

        if baseline_observation is not None:
            lines.extend(
                [
                    "",
                    "### Change From Baseline",
                    "",
                    "Positive values mean the outcome is greater than at the baseline setting.",
                    "",
                    "| Setting | "
                    + " | ".join(f"Delta {METRIC_LABELS[metric]}" for metric in REPORT_METRICS)
                    + " |",
                    "| ---: | " + " | ".join("---:" for _ in REPORT_METRICS) + " |",
                ]
            )
            for observation in observations:
                changes = " | ".join(
                    _format_change(
                        observation.metrics.get(metric), baseline_observation.metrics.get(metric)
                    )
                    for metric in REPORT_METRICS
                )
                lines.append(f"| {_markdown_value(observation.value)} | {changes} |")

        comparisons = [item for item in result.comparisons if item.parameter == parameter]
        if comparisons:
            lines.extend(
                [
                    "",
                    "### Response Summary",
                    "",
                    "| Outcome | Baseline | Minimum | Maximum | Range | Direction |",
                    "| --- | ---: | ---: | ---: | ---: | --- |",
                ]
            )
            for comparison in comparisons:
                baseline_value = _format_metric(comparison.baseline_value)
                minimum = _format_metric(comparison.minimum)
                maximum = _format_metric(comparison.maximum)
                range_value = _format_metric(comparison.range_value)
                lines.append(
                    f"| {METRIC_LABELS[comparison.metric]} | {baseline_value} | {minimum} | "
                    f"{maximum} | {range_value} | {comparison.direction} |"
                )

    if result.flip_points:
        lines.extend(
            [
                "",
                "## Subsidy Flip Points",
                "",
                "Each row is an independent adaptive search for the lowest subsidy "
                "with positive burned salvage while the listed setting is held fixed.",
                "",
                "| Parameter | Setting | Baseline | Status | Inactive lower ($/m3) | "
                "Active upper ($/m3) | Width ($/m3) | Probes | Converged |",
                "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for flip_point in sorted(
            result.flip_points, key=lambda item: (item.parameter, str(item.value))
        ):
            lines.append(
                "| `{parameter}` | {value} | {baseline} | `{status}` | {lower} | {upper} | "
                "{width} | {probes} | {converged} |".format(
                    parameter=flip_point.parameter,
                    value=_markdown_value(flip_point.value),
                    baseline="yes" if flip_point.baseline else "",
                    status=flip_point.status,
                    lower=_format_metric(flip_point.lower),
                    upper=_format_metric(flip_point.upper),
                    width=_format_metric(flip_point.bracket_width),
                    probes=_format_metric(flip_point.midpoint_probes),
                    converged=(
                        "yes"
                        if flip_point.converged
                        else ("no" if flip_point.converged is False else "not available")
                    ),
                )
            )

    lines.extend(
        [
            "",
            "## Evidence Files",
            "",
            f"- Scenario-level observations: `data/{slug}-observations.jsonl`",
            f"- Machine-readable response summaries: `data/{slug}-comparisons.csv`",
            *(
                [f"- Machine-readable flip points: `data/{slug}-flip-points.csv`"]
                if result.flip_points
                else []
            ),
            f"- Run provenance: `manifests/{slug}-sensitivity-manifest.json`",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_value(value: object) -> str:
    """Format a config or axis value safely for a Markdown table."""

    return str(value).replace("|", "\\|").replace("`", "'")


def _format_metric(value: object) -> str:
    """Format a numeric metric or render an unavailable outcome explicitly."""

    return _format_number(value) if isinstance(value, (int, float)) else "not available"


def _format_change(value: object, baseline: object) -> str:
    """Format an observation's signed difference from its parameter baseline."""

    if not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)):
        return "not available"
    return f"{value - baseline:+,.6g}"


def _format_number(value: object) -> str:
    """Format a numeric value compactly without hiding its sign."""

    return f"{float(value):,.6g}"


__all__ = [
    "SENSITIVITY_METRICS",
    "SENSITIVITY_RESERVED_FIELDS",
    "SensitivityError",
    "SensitivitySpec",
    "analyze_sensitivity",
    "eligible_parameters",
    "expand_one_parameter",
    "run_all_sensitivities",
    "run_parameter_sensitivity",
]
