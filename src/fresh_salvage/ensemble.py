"""Scenario-ensemble driver: parallel rolling-horizon runs over a grid.

Thesis-scale sensitivity analysis runs one full rolling-horizon coupled run
per scenario, where a scenario is one point of a cartesian grid over named
``RHRunConfig`` axes (e.g. ``subsidy_rate_per_m3`` for the principal policy,
``burn_rate_multiplier`` for the future fire pattern, ``discount_rate``).
:func:`expand_scenarios` is the pure grid boundary: axis names are explicit
config field names (no positional ambiguity), the driver-owned fields
``run_id``/``output_root`` are reserved, ``bridge_path`` is reserved as an
axis (every scenario is bound to the once-prebuilt shared bridge), and every
grid-shape violation fails fast with a structured :class:`EnsembleError`
code before any work starts.

Isolation model
---------------
Every scenario runs :func:`fresh_salvage.rh.run_rh` in its own
``ProcessPoolExecutor`` worker process with its own output root
(``<ensemble output_root>/<scenario name>/``), so there is no shared mutable
state between scenarios. The one shared input is the WS3 bridge: when the
configured bridge is the canonical Landscape-Unit bridge, the derived no-LU
bridge is rebuilt ONCE by the parent process under the ensemble output root
(:func:`fresh_salvage.ws3.resolved_bridge_path`) before the pool starts, and
every scenario then points at that already-derived bridge, which
``resolved_bridge_path`` returns as-is — the bridge is strictly read-only
during the parallel phase and there is no write contention. The pool uses
the ``spawn`` multiprocessing context: workers start from a clean
interpreter (no inherited model state, no fork-with-threads hazards) and
inherit ``PYTHONPATH``, which the ws3 dependency requires.

Failure semantics
-----------------
A scenario failure never kills the ensemble: the worker converts any
exception into a :class:`ScenarioRecord` with ``status="failed"`` and the
structured error code (``RHError.code`` or the exception type name), the
ensemble completes the remaining scenarios, and the summary manifest reports
the per-scenario outcomes. ``max_workers=1`` runs scenarios sequentially
in-process (the debug and test profile); results are deterministic given
identical inputs: scenarios are expanded in sorted-axis cartesian order and
records are written in that grid order regardless of completion order.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from fresh_salvage import rh, ws3
from fresh_salvage.models import (
    ArtifactLayout,
    EnsembleConfig,
    EnsembleManifest,
    EnsembleResult,
    RHRunConfig,
    ScenarioRecord,
    WS3RunConfig,
    safe_slug,
)

# Driver-owned RHRunConfig fields: the ensemble assigns them per scenario, so
# they are rejected in user-supplied ``base``/``axes`` (fail fast, no silent
# override).
RESERVED_SCENARIO_FIELDS = ("run_id", "output_root")

# Fields the ensemble overrides per scenario after expansion: every scenario
# is bound to the once-prebuilt shared bridge (:func:`_scenario_payload`), so
# a ``bridge_path`` axis would be silently discarded. It stays required in
# ``base`` (the bridge source) but is reserved in ``axes``.
RESERVED_AXIS_FIELDS = ("bridge_path",)

BASELINE_SCENARIO_NAME = "baseline"


class EnsembleError(RuntimeError):
    """Fatal ensemble failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScenarioSpec:
    """One grid point: scenario name, its axis overrides, and its run config."""

    name: str
    overrides: dict[str, object]
    run_config: RHRunConfig


def expand_scenarios(config: EnsembleConfig) -> list[ScenarioSpec]:
    """Expand the ensemble grid into validated per-scenario run configs.

    Pure boundary function (no I/O): the cartesian product is taken over
    axes sorted by name, so expansion order — and every downstream artifact
    ordering — is deterministic. Each scenario config is the ``base`` field
    values plus that grid point's overrides, validated by ``RHRunConfig``
    itself, with the driver-owned ``run_id``/``output_root`` assigned here.
    An empty ``axes`` mapping yields the single ``baseline`` scenario.
    """

    field_names = set(RHRunConfig.model_fields)
    for key in sorted(config.base):
        _require_scenario_field(key, field_names, origin="base")
    axis_names = sorted(config.axes)
    for axis in axis_names:
        _require_scenario_field(
            axis,
            field_names,
            origin="axes",
            reserved=RESERVED_SCENARIO_FIELDS + RESERVED_AXIS_FIELDS,
        )
        if not config.axes[axis]:
            raise EnsembleError(
                "ensemble_axis_empty",
                f"axis {axis!r} carries no values; every axis needs at least one",
            )

    combos = itertools.product(*(config.axes[axis] for axis in axis_names))
    specs: list[ScenarioSpec] = []
    seen_names: set[str] = set()
    for values in combos:
        overrides = dict(zip(axis_names, values, strict=True))
        name = _scenario_name(overrides)
        if name in seen_names:
            raise EnsembleError(
                "ensemble_duplicate_scenario",
                f"scenario name {name!r} is produced more than once; check for "
                "duplicate axis values",
            )
        seen_names.add(name)
        fields = {**config.base, **overrides}
        try:
            run_config = RHRunConfig(
                run_id=f"{config.ensemble_id}-{name}",
                output_root=Path(config.output_root) / name,
                **fields,
            )
        except ValidationError as exc:
            raise EnsembleError(
                "ensemble_scenario_invalid",
                f"scenario {name!r} does not form a valid RHRunConfig: {exc}",
            ) from exc
        specs.append(
            ScenarioSpec(name=name, overrides=overrides, run_config=run_config)
        )
    return specs


def run_ensemble(config: EnsembleConfig, verbose: bool = False) -> EnsembleResult:
    """Run every scenario of the grid and write JSONL + summary manifest.

    Raises ``EnsembleError`` on fatal grid, input, or bridge failures before
    any scenario starts; per-scenario failures are captured as records, never
    raised. The scenarios JSONL and the summary manifest are written in
    deterministic grid order once all scenarios have completed (per-scenario
    evidence — RH manifests and step JSONL — is flushed incrementally inside
    each scenario's own output root by ``run_rh`` itself).
    """

    started_at = datetime.now(UTC)
    wall_started = time.monotonic()
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    ensemble_slug = safe_slug(config.ensemble_id)
    scenarios_path = layout.data_path(f"{ensemble_slug}-scenarios", ext="jsonl")
    manifest_path = layout.manifest_path(f"{ensemble_slug}-ensemble-manifest")

    specs = expand_scenarios(config)
    # Digest the grid config and input tables BEFORE the bridge prebuild so a
    # missing input fails fast (ensemble_input_missing) without paying for a
    # bridge rebuild.
    source_checksums = _provenance_checksums(config, specs)
    bridge = _prebuild_bridge(config, specs[0])
    source_checksums.update(_bridge_checksums(bridge))
    payloads = [_scenario_payload(config, spec, bridge) for spec in specs]

    if config.max_workers == 1:
        # Sequential in-process profile (debug/test): same worker function,
        # same failure-capture semantics, no subprocess boundary.
        records = [
            ScenarioRecord.model_validate(_run_scenario_worker(payload, verbose=verbose))
            for payload in payloads
        ]
    else:
        records = _run_parallel(
            specs, payloads, max_workers=config.max_workers, verbose=verbose
        )
    succeeded = sum(record.status != "failed" for record in records)
    failed = len(records) - succeeded
    status = "ok" if failed == 0 else ("failed" if succeeded == 0 else "partial")
    wall_seconds = time.monotonic() - wall_started

    scenarios_path.write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    manifest = EnsembleManifest(
        ensemble_id=config.ensemble_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        status=status,
        scenario_count=len(records),
        succeeded=succeeded,
        failed=failed,
        max_workers=config.max_workers,
        wall_seconds=wall_seconds,
        source_sha256=source_checksums,
        scenarios=records,
        config=config.model_dump(mode="json"),
    )
    manifest.write_json(manifest_path)

    return EnsembleResult(
        ensemble_id=config.ensemble_id,
        status=status,
        scenario_count=len(records),
        succeeded=succeeded,
        failed=failed,
        max_workers=config.max_workers,
        wall_seconds=wall_seconds,
        scenarios=records,
        scenarios_path=scenarios_path,
        manifest_path=manifest_path,
    )


def _require_scenario_field(
    key: str,
    field_names: set[str],
    *,
    origin: str,
    reserved: tuple[str, ...] = RESERVED_SCENARIO_FIELDS,
) -> None:
    """Fail fast when a base/axes key is reserved or not an RHRunConfig field."""

    if key in reserved:
        raise EnsembleError(
            "ensemble_field_reserved",
            f"{origin} key {key!r} is driver-owned; the ensemble assigns or "
            f"overrides {', '.join(reserved)} per scenario",
        )
    if key not in field_names:
        raise EnsembleError(
            "ensemble_axis_unknown",
            f"{origin} key {key!r} is not an RHRunConfig field; valid axes: "
            f"{sorted(field_names - set(reserved))}",
        )


def _scenario_name(overrides: dict[str, object]) -> str:
    """Return the explicit ``axis-value`` scenario name (slugged)."""

    if not overrides:
        return BASELINE_SCENARIO_NAME
    return safe_slug(
        "__".join(
            f"{axis}-{_axis_value_label(value)}"
            for axis, value in sorted(overrides.items())
        )
    )


def _axis_value_label(value: object) -> str:
    """Render an axis value as a compact, deterministic name fragment."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, sort_keys=True)


def _prebuild_bridge(config: EnsembleConfig, first: ScenarioSpec) -> Path:
    """Resolve the shared WS3 bridge once, before the worker pool starts.

    A canonical Landscape-Unit bridge is rebuilt into the no-LU derived
    bridge under the ensemble output root; an already-derived bridge is
    returned as-is. Either way the parallel phase treats the bridge as
    read-only shared input.
    """

    probe = WS3RunConfig(
        run_id=config.ensemble_id,
        bridge_path=first.run_config.bridge_path,
        base_year=first.run_config.base_year,
        horizon=first.run_config.horizon,
        period_length=first.run_config.period_length,
        max_age=first.run_config.max_age,
        workers=first.run_config.workers,
        age_smashing=first.run_config.age_smashing,
        objective=first.run_config.objective,
        aac_annual_m3=first.run_config.aac_annual_m3,
        output_root=config.output_root,
    )
    try:
        return ws3.resolved_bridge_path(probe)
    except ws3.WS3Error as exc:
        raise EnsembleError(
            "ensemble_bridge_failed",
            f"shared WS3 bridge resolution failed: [{exc.code}] {exc}",
        ) from exc


def _scenario_payload(
    config: EnsembleConfig, spec: ScenarioSpec, bridge: Path
) -> dict[str, object]:
    """Build the picklable worker payload with the prebuilt bridge bound."""

    run_config = spec.run_config.model_copy(update={"bridge_path": bridge})
    return {
        "name": spec.name,
        "overrides": spec.overrides,
        "run_config": run_config.model_dump(mode="json"),
    }


def _run_parallel(
    specs: list[ScenarioSpec],
    payloads: list[dict[str, object]],
    *,
    max_workers: int,
    verbose: bool,
) -> list[ScenarioRecord]:
    """Run scenario workers on a spawn-context process pool.

    Records are returned in grid (spec) order, not completion order, so the
    JSONL and manifest are deterministic for identical inputs. A worker that
    dies without returning (pickle failure, hard crash) is captured as an
    ``ensemble_worker_crashed`` record — the ensemble still completes.
    """

    context = multiprocessing.get_context("spawn")
    records: dict[str, ScenarioRecord] = {}
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=context) as pool:
        futures = {
            pool.submit(_run_scenario_worker, payload, verbose=verbose): spec
            for spec, payload in zip(specs, payloads, strict=True)
        }
        for future in as_completed(futures):
            spec = futures[future]
            try:
                record = ScenarioRecord.model_validate(future.result())
            except Exception as exc:
                record = ScenarioRecord(
                    name=spec.name,
                    run_id=spec.run_config.run_id,
                    overrides=spec.overrides,
                    status="failed",
                    error_code="ensemble_worker_crashed",
                    error_message=f"{type(exc).__name__}: {exc}",
                    wall_seconds=0.0,
                    output_root=spec.run_config.output_root,
                )
            records[record.name] = record
    return [records[spec.name] for spec in specs]


def _run_scenario_worker(
    payload: dict[str, object], verbose: bool = False
) -> dict[str, object]:
    """Run one scenario's rolling-horizon run; capture any failure as a record.

    Module-level and pickle-friendly (spawn workers import it fresh). The
    worker owns its scenario output root exclusively; the shared bridge is
    read-only.
    """

    started = time.monotonic()
    run_config = RHRunConfig.model_validate(payload["run_config"])
    name = str(payload["name"])
    overrides = payload["overrides"]
    record_fields = {
        "name": name,
        "run_id": run_config.run_id,
        "overrides": overrides,
        "output_root": run_config.output_root,
    }
    try:
        result = rh.run_rh(run_config, verbose=verbose)
    except Exception as exc:
        return ScenarioRecord(
            **record_fields,
            status="failed",
            error_code=str(getattr(exc, "code", type(exc).__name__)),
            error_message=str(exc),
            wall_seconds=time.monotonic() - started,
        ).model_dump(mode="json")
    return ScenarioRecord(
        **record_fields,
        status=result.status,
        wall_seconds=result.wall_seconds,
        manifest_path=result.manifest_path,
        steps_path=result.steps_path,
    ).model_dump(mode="json")


def _provenance_checksums(
    config: EnsembleConfig, specs: list[ScenarioSpec]
) -> dict[str, str]:
    """Return SHA-256 provenance for the grid config and input tables.

    Runs before the bridge prebuild so a missing input fails fast without
    paying for a rebuild. ``stands``/``yields`` are single keys when every
    scenario shares one path; scenarios overriding them produce one
    ``label:path`` key each.
    """

    grid_digest = hashlib.sha256(
        json.dumps(config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
        return {
            "grid_config": grid_digest,
            **_input_checksums(
                "stands", [spec.run_config.stands_path for spec in specs]
            ),
            **_input_checksums(
                "yields", [spec.run_config.yields_path for spec in specs]
            ),
        }
    except OSError as exc:
        raise EnsembleError(
            "ensemble_input_missing",
            f"ensemble input provenance failed: {exc}",
        ) from exc


def _bridge_checksums(bridge: Path) -> dict[str, str]:
    """Return ``bridge/<file>`` SHA-256 digests of the prebuilt bridge."""

    try:
        return {
            f"bridge/{name}": digest
            for name, digest in ws3.file_checksums(Path(bridge)).items()
        }
    except OSError as exc:
        raise EnsembleError(
            "ensemble_input_missing",
            f"ensemble input provenance failed: {exc}",
        ) from exc


def _input_checksums(label: str, paths: list[Path]) -> dict[str, str]:
    """Digest the distinct input paths of one kind across the grid."""

    unique = sorted({str(path) for path in paths})
    if len(unique) == 1:
        return {label: _sha256_file(unique[0])}
    return {f"{label}:{path}": _sha256_file(path) for path in unique}


def _sha256_file(path: Path | str) -> str:
    """Return the SHA-256 hex digest of a file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "BASELINE_SCENARIO_NAME",
    "RESERVED_AXIS_FIELDS",
    "RESERVED_SCENARIO_FIELDS",
    "EnsembleError",
    "ScenarioSpec",
    "expand_scenarios",
    "run_ensemble",
]
