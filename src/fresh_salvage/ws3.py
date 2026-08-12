"""Full-TSA WS3 schedule compilation and HiGHS solve at the pipeline boundary.

Ports the predecessor integration in
``masc-yunhao-xu/Gurobi/Rolling_horizon_structure/ws3_masc_integration.py`` into
the typed fresh-salvage pipeline. The predecessor's 11-landscape-unit binary
membership rewrite, landscape-unit validation, and subset extraction are
removed. The model is compiled from a Landscape-Unit-free bridge rebuilt from
the femic stage-1 Woodstock CSVs: ``landscape_unit_id`` is dropped at the
source, fragment ages are smashed to deterministic 10-year class midpoints
before grouping, and femic's own stage-2 writer
(``femic.ws3_bridge.build_ws3_sections_from_femic_woodstock``) aggregates area
over ``(TSA, managed, AU, stratum, curve, age)`` so every development type key
is unique and the compiled schedule covers every development type and period.
The rebuild fails fast on unparseable inventory ages and reconciles the
written ARE section against the staged area total, because femic's writer
silently drops area rows whose ``(TSA, managed, AU)`` key has no yield curve.

The solve follows the TSA29 conventions:

- initial-inventory ages are smashed into deterministic 10-year classes
  (``age // width * width + midpoint``);
- the ``cc`` clear-cut action is constrained to operable ages [60, 300] and
  removed for the never-merchantable yield curves — fewer treatment age
  options in each dynamic-programming state tree means fewer branches, fewer
  Model-I paths, and a substantially smaller LP column count;
- an annual-allowable-cut ceiling bounds each period at
  ``aac_annual_m3 * period_length`` through a WS3 ``cgen_data`` general
  constraint;
- the objective maximizes utilized harvest volume (``totvol * utilization``)
  under an even-flow constraint with the configured tolerance.

The ws3 and femic packages are imported lazily so the pure helpers (age
smashing, AAC ceiling, status normalization) stay unit-testable without those
dependencies.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pandas as pd

from fresh_salvage.models import (
    ArtifactLayout,
    Diagnostic,
    WS3Manifest,
    WS3Result,
    WS3RunConfig,
    safe_slug,
)

WS3_DEVELOPMENT_TYPE_INDEX = 3
DEFAULT_AGE_SMASHING_WIDTH = 10
DEFAULT_AGE_SMASHING_MIDPOINT = 5
CC_MIN_HARVEST_AGE = 60
CC_MAX_HARVEST_AGE = 300
DEFAULT_AAC_ANNUAL_M3 = 2_937_509
DEFAULT_UTILIZATION = 0.85
DEFAULT_EVEN_FLOW_TOLERANCE = 0.1
AREA_CONSERVATION_REL_TOLERANCE = 1e-6

# Yield curves whose volume never reaches the merchantability rule; they lose
# ``cc`` operability entirely regardless of the configured harvest age range.
NEVER_MERCHANTABLE_CURVES = frozenset(
    {"2921000", "2921004", "2921014", "2901001", "2901014"}
)

BRIDGE_FILE_PREFIX = "femic_tsa_ws3"
BRIDGE_FILE_SUFFIXES = ("act", "are", "lan", "trn", "yld")
CANONICAL_TSA29_BRIDGE = Path(
    "/srv/shared-data/gep/jupyterhub07-projects/davis/femic/external/"
    "femic-tsa29-instance/output/woodstock_tsa29_validated/ws3_bridge"
)
DERIVED_BRIDGE_DIRNAME = "ws3_bridge_no_lu"
DROPPED_THEME_DESCRIPTION = "Landscape Unit"

FEMIC_SRC_ROOT = Path("/srv/shared-data/gep/jupyterhub07-projects/davis/femic/src")
STAGE1_AREAS_FILENAME = "woodstock_areas.csv"
STAGE1_FILE_NAMES = (
    "woodstock_yields.csv",
    STAGE1_AREAS_FILENAME,
    "woodstock_actions.csv",
    "woodstock_transitions.csv",
)
STAGE1_DERIVED_DIRNAME = "woodstock_no_lu_smashed"
LANDSCAPE_UNIT_COLUMN = "landscape_unit_id"

SCHEDULE_RAW_COLUMNS = ("dtype_key", "age_class", "area_ha", "harvest_action", "period", "etype")
SCHEDULE_COLUMNS = (
    "period",
    "year",
    "dtype_key",
    "stratum",
    "age_class",
    "area_ha",
    "harvest_action",
    "volume_m3",
    "etype",
)


class WS3Error(RuntimeError):
    """Fatal WS3 run failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_status(status: object) -> str:
    """Normalize a HiGHS status across the pre/post 1.0 Python APIs."""

    name = getattr(status, "name", "")
    text = str(status).lower()
    if (
        name.lower() == "koptimal"
        or text in {"optimal", "status_optimal", "1"}
        or text.endswith(".optimal")
        or "koptimal" in text
    ):
        return "optimal"
    return name or str(status)


def midpoint_age(
    age: int,
    width: int = DEFAULT_AGE_SMASHING_WIDTH,
    midpoint: int = DEFAULT_AGE_SMASHING_MIDPOINT,
) -> int:
    """Map an inventory age to its deterministic age-class midpoint."""

    if width <= 0 or not 0 <= midpoint < width:
        raise ValueError("age-smashing width must be positive and midpoint inside the class")
    if age < 0:
        raise ValueError("inventory ages cannot be negative")
    return (age // width) * width + midpoint


def smash_initial_inventory_ages(
    model: object,
    width: int = DEFAULT_AGE_SMASHING_WIDTH,
    midpoint: int = DEFAULT_AGE_SMASHING_MIDPOINT,
) -> dict[str, object]:
    """Compress actual WS3 period-0 inventory maps and return provenance.

    Only period-0 inventory is touched; period-1 areas are re-derived by the
    model's own ``initialize_areas``.
    """

    changed = 0
    area_ha = 0.0
    for development_type in model.dtypes.values():
        initial = development_type._areas[0]
        if not initial:
            continue
        compressed = initial.copy()
        compressed.clear()
        for age, area in initial.items():
            target = midpoint_age(int(age), width, midpoint)
            compressed[target] = compressed.get(target, 0.0) + area
            area_ha += float(area)
            changed += target != age
        development_type._areas[0] = compressed
    model.initialize_areas(reset_areas=True)
    return {
        "width": width,
        "midpoint": midpoint,
        "changed_age_classes": changed,
        "area_ha": area_ha,
    }


def enforce_harvest_age_range(
    model: object,
    action_code: str = "cc",
    min_age: int = CC_MIN_HARVEST_AGE,
    max_age: int = CC_MAX_HARVEST_AGE,
) -> dict[str, object]:
    """Constrain one harvest action to operable ages ``[min_age, max_age]``.

    Never-merchantable yield curves lose operability entirely
    (``oper_expr`` popped, not emptied); every other curve is restricted to
    ``min_age <= age <= max_age`` and the model recompiles its actions.
    """

    oper_expr_popped = 0
    oper_expr_rewritten = 0
    for development_type in model.dtypes.values():
        if action_code not in development_type.oper_expr:
            continue
        curve_id = development_type.key[4]
        if curve_id in NEVER_MERCHANTABLE_CURVES:
            development_type.oper_expr.pop(action_code, None)
            oper_expr_popped += 1
        else:
            development_type.oper_expr[action_code] = [
                f"_age >= {min_age} and _age <= {max_age}"
            ]
            oper_expr_rewritten += 1
    model.compile_actions()
    return {
        "min_harvest_age": min_age,
        "max_harvest_age": max_age,
        "never_merchantable_curves": sorted(NEVER_MERCHANTABLE_CURVES),
        "oper_expr_popped": oper_expr_popped,
        "oper_expr_rewritten": oper_expr_rewritten,
    }


def derived_bridge_path(output_root: Path) -> Path:
    """Return the deterministic derived-bridge workspace under an output root."""

    return Path(output_root) / "derived" / DERIVED_BRIDGE_DIRNAME


def _split_theme_blocks(lan_text: str) -> list[tuple[str, list[str]]]:
    """Split a LAN text into ``(description, body_lines)`` theme blocks."""

    blocks: list[tuple[str, list[str]]] = []
    description: str | None = None
    body: list[str] = []
    for line in lan_text.splitlines():
        if line.startswith("*THEME"):
            if description is not None:
                blocks.append((description, body))
            description = line[len("*THEME") :].strip()
            body = []
        elif description is not None:
            body.append(line)
    if description is not None:
        blocks.append((description, body))
    return blocks


def _load_femic_bridge_writer() -> object:
    """Import femic's stage-2 Woodstock section writer, lazily extending sys.path.

    The femic repository uses a src layout and is not a fresh-salvage install
    dependency; when a plain import fails, the ``FEMIC_SRC`` environment
    variable (falling back to :data:`FEMIC_SRC_ROOT`) is inserted into
    ``sys.path`` before retrying. Raises ``WS3Error`` when femic's writer
    cannot be imported at all.
    """

    try:
        from femic.ws3_bridge import build_ws3_sections_from_femic_woodstock

        return build_ws3_sections_from_femic_woodstock
    except ImportError:
        pass
    femic_src = Path(os.environ.get("FEMIC_SRC", str(FEMIC_SRC_ROOT)))
    if femic_src.is_dir() and str(femic_src) not in sys.path:
        sys.path.insert(0, str(femic_src))
    try:
        from femic.ws3_bridge import build_ws3_sections_from_femic_woodstock

        return build_ws3_sections_from_femic_woodstock
    except ImportError as exc:
        raise WS3Error(
            "femic_import_failed",
            "femic's WS3 bridge writer is required to rebuild the no-LU bridge; "
            "add the femic src directory to PYTHONPATH (e.g. "
            f"PYTHONPATH={FEMIC_SRC_ROOT}): {exc}",
        ) from exc


def build_smashed_no_lu_bridge(
    stage1_dir: Path,
    dest_dir: Path,
    width: int = DEFAULT_AGE_SMASHING_WIDTH,
    midpoint: int = DEFAULT_AGE_SMASHING_MIDPOINT,
) -> Path:
    """Materialize the natively aggregated Landscape-Unit-free WS3 bridge.

    Reads the femic stage-1 Woodstock CSVs, drops ``landscape_unit_id``, and
    smashes every fragment age to its deterministic age-class midpoint
    (``age // width * width + midpoint``, identical to :func:`midpoint_age`)
    *before* any aggregation. All section text is written by femic's own
    stage-2 writer, so area is summed over the true
    ``(tsa, ifm, au_id, stratum_code, curve_id, age)`` key at the source and
    the emitted bridge carries exactly five themes. The staged CSVs are kept
    beside ``dest_dir`` for provenance. Raises ``WS3Error`` when the stage-1
    package is incomplete, when any area-row age fails to parse, when the
    written bridge violates the smash contract, or when the written ARE
    section does not conserve the staged area total.
    """

    source = Path(stage1_dir)
    missing_files = [name for name in STAGE1_FILE_NAMES if not (source / name).is_file()]
    if missing_files:
        raise WS3Error(
            "ws3_stage1_incomplete",
            f"femic stage-1 Woodstock directory {source} is missing files: "
            + ", ".join(missing_files),
        )
    writer = _load_femic_bridge_writer()

    dest = Path(dest_dir)
    staging = dest.parent / STAGE1_DERIVED_DIRNAME
    staging.mkdir(parents=True, exist_ok=True)

    areas = pd.read_csv(source / STAGE1_AREAS_FILENAME)
    if LANDSCAPE_UNIT_COLUMN in areas.columns:
        areas = areas.drop(columns=[LANDSCAPE_UNIT_COLUMN])
    parsed_ages = pd.to_numeric(areas["age"], errors="coerce")
    invalid_age_mask = parsed_ages.isna()
    if invalid_age_mask.any():
        examples = areas.loc[invalid_age_mask, "age"].astype(str).head(5).tolist()
        raise WS3Error(
            "invalid_age_values",
            f"femic stage-1 areas table {source / STAGE1_AREAS_FILENAME} contains "
            f"{int(invalid_age_mask.sum())} rows with unparseable age values "
            f"(e.g. {examples}); refusing to silently bucket them into age class "
            f"{midpoint}",
        )
    areas["age"] = parsed_ages.astype(int).map(
        partial(midpoint_age, width=width, midpoint=midpoint)
    )
    areas.to_csv(staging / STAGE1_AREAS_FILENAME, index=False)
    for name in STAGE1_FILE_NAMES:
        if name == STAGE1_AREAS_FILENAME:
            continue
        shutil.copy2(source / name, staging / name)

    # Mirror femic's writer-side normalization so the conservation gate
    # reconciles exactly what the writer received against what it wrote.
    staged_area_ha = float(
        pd.to_numeric(areas["area_ha"], errors="coerce").fillna(0.0).sum()
    )
    result = writer(
        woodstock_dir=staging,
        output_dir=dest,
        model_name=BRIDGE_FILE_PREFIX,
    )
    written = Path(result.output_dir)
    _verify_smashed_bridge(written, width, midpoint)
    _verify_area_conservation(written, staged_area_ha)
    return written


def _parse_are_rows(are_path: Path) -> list[tuple[str, int, float]]:
    """Parse ARE data rows into trusted ``(raw_line, age, area_ha)`` triples.

    Boundary parse: an unreadable or malformed ARE section fails with a
    structured ``WS3Error`` code instead of leaking ``FileNotFoundError`` or
    ``ValueError`` to callers.
    """

    try:
        are_text = are_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WS3Error(
            "ws3_bridge_are_missing",
            f"rebuilt bridge is missing its ARE section: {are_path}",
        ) from exc
    rows: list[tuple[str, int, float]] = []
    for line in are_text.splitlines():
        if not line.strip().startswith("*A"):
            continue
        tokens = line.split()
        try:
            rows.append((line, int(tokens[-2]), float(tokens[-1])))
        except (IndexError, ValueError) as exc:
            raise WS3Error(
                "ws3_bridge_are_unparseable",
                f"rebuilt bridge ARE section {are_path} contains a malformed "
                f"data row: {line!r}",
            ) from exc
    return rows


def _verify_smashed_bridge(
    bridge: Path,
    width: int = DEFAULT_AGE_SMASHING_WIDTH,
    midpoint: int = DEFAULT_AGE_SMASHING_MIDPOINT,
) -> None:
    """Fail fast when a written bridge violates the no-LU smash contract."""

    if _lan_contains_landscape_unit_theme(bridge):
        raise WS3Error(
            "ws3_bridge_lu_theme_unexpected",
            f"rebuilt bridge {bridge} still carries the "
            f"{DROPPED_THEME_DESCRIPTION!r} theme",
        )
    are_path = Path(bridge) / f"{BRIDGE_FILE_PREFIX}.are"
    offenders = [
        line
        for line, age, _area_ha in _parse_are_rows(are_path)
        if age % width != midpoint % width
    ]
    if offenders:
        raise WS3Error(
            "ws3_bridge_age_unsmashed",
            f"rebuilt bridge {are_path} contains {len(offenders)} ARE rows whose "
            f"age is not a {width}-year class midpoint (e.g. {offenders[0]!r})",
        )


def _verify_area_conservation(bridge: Path, expected_area_ha: float) -> None:
    """Fail fast when the written ARE section does not conserve staged area.

    femic's ``_write_are`` merges the staged areas against the yield-curve map
    and silently ``dropna``-discards rows whose ``(tsa, ifm, au_id)`` key has
    no curve, so the written ARE total must be reconciled against the staged
    (post-smash, LU-dropped) areas-table total before the bridge is trusted.
    """

    are_path = Path(bridge) / f"{BRIDGE_FILE_PREFIX}.are"
    written_area_ha = sum(area_ha for _, _, area_ha in _parse_are_rows(are_path))
    if math.isclose(
        written_area_ha, expected_area_ha, rel_tol=AREA_CONSERVATION_REL_TOLERANCE
    ):
        return
    delta = written_area_ha - expected_area_ha
    raise WS3Error(
        "area_conservation_failed",
        f"rebuilt bridge {are_path} conserves {written_area_ha:.6f} ha but the "
        f"staged areas table holds {expected_area_ha:.6f} ha "
        f"(delta {delta:+.6f} ha, rel tolerance {AREA_CONSERVATION_REL_TOLERANCE}); "
        "femic's writer silently drops area rows without a matching yield curve",
    )


def _lan_contains_landscape_unit_theme(bridge: Path) -> bool:
    """Return whether a bridge LAN still carries the Landscape Unit theme."""

    lan_path = Path(bridge) / f"{BRIDGE_FILE_PREFIX}.lan"
    if not lan_path.is_file():
        return False
    return any(
        description.lower() == DROPPED_THEME_DESCRIPTION.lower()
        for description, _ in _split_theme_blocks(lan_path.read_text(encoding="utf-8"))
    )


def resolved_bridge_path(config: WS3RunConfig) -> Path:
    """Return the bridge a run loads, rebuilding the no-LU bridge when needed.

    When ``config.bridge_path`` is the canonical Landscape-Unit bridge, the
    Landscape-Unit-free age-smashed bridge is rebuilt from the sibling femic
    stage-1 Woodstock CSVs (which live in the bridge's parent directory) into
    ``config.output_root`` and returned; an already rebuilt bridge is used
    as-is.
    """

    canonical = Path(config.bridge_path)
    if canonical.is_dir() and _lan_contains_landscape_unit_theme(canonical):
        return build_smashed_no_lu_bridge(
            canonical.parent, derived_bridge_path(Path(config.output_root))
        )
    return canonical


def aac_ceiling_constraints(
    periods: list[int],
    aac_annual_m3: float,
    period_length: int,
) -> dict[str, dict[int, float]]:
    """Build per-period lower/upper bounds for the WS3 ``cgen_data`` AAC row."""

    if aac_annual_m3 < 0:
        raise ValueError("aac_annual_m3 cannot be negative")
    if period_length <= 0:
        raise ValueError("period_length must be positive")
    ceiling = float(aac_annual_m3) * period_length
    return {
        "lb": {period: 0.0 for period in periods},
        "ub": {period: ceiling for period in periods},
    }


def even_flow_constraints(
    periods: list[int], tolerance: float
) -> tuple[dict[int, float], int]:
    """Build the WS3 ``cflw_e`` epsilon mapping and its reference period."""

    if not periods or not 0 <= tolerance < 1:
        raise ValueError("even-flow requires periods and tolerance in [0, 1)")
    return ({period: tolerance for period in periods}, periods[0])


def smoke_config(bridge_path: Path, output_root: Path) -> WS3RunConfig:
    """Return the deterministic 3-period smoke profile (workers 2)."""

    return WS3RunConfig(
        run_id="tsa29-ws3-smoke",
        bridge_path=bridge_path,
        base_year=2025,
        horizon=3,
        workers=2,
        output_root=output_root,
    )


def load_full_model(config: WS3RunConfig, verbose: bool = False) -> object:
    """Compile the full TSA29 WS3 model from the derived LU-free bridge.

    The configured ``bridge_path`` is resolved through
    :func:`resolved_bridge_path`: a canonical 6-theme bridge is first
    transformed into the derived Landscape-Unit-free bridge under
    ``output_root``. Raises ``WS3Error`` with a structured code on boundary
    failures; the caller converts non-fatal anomalies into ``Diagnostic``
    records.
    """

    bridge = resolved_bridge_path(config)
    if not bridge.is_dir():
        raise WS3Error("ws3_bridge_missing", f"WS3 bridge directory does not exist: {bridge}")
    missing_files = [
        f"{BRIDGE_FILE_PREFIX}.{suffix}"
        for suffix in BRIDGE_FILE_SUFFIXES
        if not (bridge / f"{BRIDGE_FILE_PREFIX}.{suffix}").is_file()
    ]
    if missing_files:
        raise WS3Error(
            "ws3_bridge_incomplete",
            "WS3 bridge is missing files: " + ", ".join(missing_files),
        )

    try:
        from ws3.forest import ForestModel
    except ImportError as exc:
        raise WS3Error(
            "ws3_import_failed",
            "the ws3 package is required for a WS3 solve; add the ws3 repository to "
            "PYTHONPATH (e.g. PYTHONPATH=/srv/shared-data/gep/jupyterhub07-projects/davis/ws3)",
        ) from exc

    action_code = config.objective.action_code
    _validate_action_code(bridge, action_code)

    model = ForestModel(
        model_name=BRIDGE_FILE_PREFIX,
        model_path=str(bridge),
        base_year=config.base_year,
        horizon=config.horizon,
        period_length=config.period_length,
        max_age=config.max_age,
    )
    model.import_landscape_section(filename_suffix="lan")
    for suffix, importer in (
        ("are", model.import_areas_section),
        ("yld", model.import_yields_section),
        ("act", model.import_actions_section),
        ("trn", model.import_transitions_section),
    ):
        # FEMIC's bridge emits biological years already; a period multiplier
        # here would turn 553 into 5530 and, after smashing, into 5535.
        importer(filename_suffix=suffix, convert_periods_to_years=1)
    model.initialize_areas()
    _validate_inventory_age_domain(model)

    if config.age_smashing.enabled:
        smash_initial_inventory_ages(
            model, config.age_smashing.width, config.age_smashing.midpoint
        )

    if action_code not in model.actions:
        raise WS3Error(
            "ws3_action_missing",
            f"configured action code {action_code!r} was not imported into the WS3 model",
        )
    # ``*ACTION cc Y`` declares a target-age action, not a harvest action. Set
    # the flag before action compilation so objective and general-capacity
    # coefficients can recognize cc as harvest, while retaining bridge order.
    model.actions[action_code].is_harvest = 1
    # ``null`` is a synthetic WS3 action; register it after imported actions so
    # the bridge's ``cc`` remains intact, before compiling transitions.
    model.add_null_action()
    model.reset_actions()
    model.compile_actions()
    enforce_harvest_age_range(model, action_code=action_code)
    return model


def run_ws3(config: WS3RunConfig, verbose: bool = False) -> WS3Result:
    """Run one full-TSA WS3 solve and write schedule + manifest artifacts.

    Raises ``WS3Error`` on fatal boundary or solver failures; non-fatal data
    anomalies are reported as ``Diagnostic`` records on the returned result.
    """

    diagnostics: list[Diagnostic] = []
    bridge = resolved_bridge_path(config)
    run_config = config.model_copy(update={"bridge_path": bridge})
    layout = ArtifactLayout(output_root=Path(run_config.output_root)).initialize()
    run_slug = safe_slug(run_config.run_id)
    data_path = layout.data_path(f"{run_slug}-schedule", ext="parquet")
    csv_path = layout.data_path(f"{run_slug}-schedule", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-ws3-manifest")

    model = load_full_model(run_config, verbose=verbose)
    problem = _build_problem(model, run_config, verbose=verbose)
    lp_dimensions = problem_lp_dimensions(problem)
    solve_started = time.monotonic()
    try:
        problem.solve(verbose=verbose)
    except Exception as exc:
        raise WS3Error(
            "ws3_solve_failed",
            f"HiGHS solve raised {type(exc).__name__}: {exc}",
        ) from exc
    solve_seconds = time.monotonic() - solve_started
    status = normalize_status(problem.status())
    if status != "optimal":
        raise WS3Error(
            "ws3_solve_not_optimal",
            f"full TSA29 WS3 solve did not reach optimal status: {status}",
        )

    schedule, objective_value = _compile_schedule(model, problem, run_config)
    schedule.to_parquet(data_path, index=False)
    schedule.to_csv(csv_path, index=False)

    per_period_volumes_m3 = {
        str(period): float(volume)
        for period, volume in schedule.groupby("period")["volume_m3"].sum().items()
    }
    per_period_area_ha = {
        str(period): float(area)
        for period, area in schedule.groupby("period")["area_ha"].sum().items()
    }

    manifest = WS3Manifest(
        run_id=run_config.run_id,
        bridge_path=bridge,
        completed_at=datetime.now(UTC),
        status=status,
        periods=run_config.horizon,
        objective_value=objective_value,
        schedule_rows=len(schedule),
        lp_rows=lp_dimensions["lp_rows"],
        lp_columns=lp_dimensions["lp_columns"],
        solve_seconds=solve_seconds,
        bridge_checksums=_file_checksums(bridge),
        config=run_config.model_dump(mode="json"),
        diagnostics=diagnostics,
    )
    manifest.write_json(manifest_path)

    return WS3Result(
        run_id=run_config.run_id,
        status=status,
        periods=run_config.horizon,
        period_length=run_config.period_length,
        objective_value=objective_value,
        schedule_row_counts={"total": len(schedule)},
        lp_rows=lp_dimensions["lp_rows"],
        lp_columns=lp_dimensions["lp_columns"],
        per_period_volumes_m3=per_period_volumes_m3,
        per_period_area_ha=per_period_area_ha,
        solve_seconds=solve_seconds,
        data_path=data_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        diagnostics=diagnostics,
    )


def problem_lp_dimensions(problem: object) -> dict[str, int]:
    """Return the built Model-I LP row and column counts of a WS3 problem.

    Columns are the decision variables (one per state-tree path); rows are the
    objective and constraint rows of the built problem.
    """

    return {
        "lp_rows": len(problem._constraints),
        "lp_columns": len(problem._vars),
    }


def run_smoke_test(verbose: bool = True) -> WS3Result:
    """Deterministic 3-period full-TSA smoke solve on the derived LU-free bridge."""

    output_root = Path("outputs/ws3_smoke")
    smoke = smoke_config(CANONICAL_TSA29_BRIDGE, output_root)
    bridge = resolved_bridge_path(smoke)
    return run_ws3(smoke_config(bridge, output_root), verbose=verbose)


def _validate_action_code(bridge: Path, action_code: str) -> None:
    """Fail fast when the configured action is absent from the bridge."""

    action_file = bridge / f"{BRIDGE_FILE_PREFIX}.act"
    action_codes = {
        line.split()[1]
        for line in action_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("*ACTION ") and len(line.split()) > 1
    }
    if action_code not in action_codes:
        raise WS3Error(
            "ws3_action_absent",
            f"configured action code {action_code!r} is absent from {action_file}: "
            f"{sorted(action_codes)}",
        )


def _validate_inventory_age_domain(model: object) -> dict[str, object]:
    """Fail fast when bridge ages exceed the configured model age domain.

    WS3 does not silently clamp ages above ``max_age``; after each growth step
    such classes remain above the ceiling and a null action has no feasible
    child. The bridge is imported with a period multiplier of one, so ARE ages
    are biological years and the check below must pass for any valid bridge.
    """

    if not model.dtypes:
        return {"max_age": model.max_age, "initial_max_age": 0}
    invalid: list[tuple[str, int]] = []
    for dtype_key, development_type in model.dtypes.items():
        for age in development_type._areas[0]:
            parsed_age = int(age)
            if parsed_age < 0 or parsed_age > model.max_age:
                invalid.append((str(dtype_key), parsed_age))
    if invalid:
        examples = ", ".join(f"{dtype}={age}" for dtype, age in invalid[:5])
        raise WS3Error(
            "ws3_invalid_inventory_age",
            f"WS3 initial inventory contains ages outside [0, {model.max_age}]: "
            f"{examples}; ARE age must be biological years (import with period "
            "multiplier 1)",
        )
    initial_max = max(
        (
            int(age)
            for development_type in model.dtypes.values()
            for age in development_type._areas[0]
        ),
        default=0,
    )
    required = initial_max + model.horizon * model.period_length
    if required > model.max_age:
        raise WS3Error(
            "ws3_age_domain_exceeded",
            f"initial inventory age plus configured horizon exceeds hard max_age="
            f"{model.max_age}: {initial_max} + {model.horizon} * "
            f"{model.period_length} = {required}",
        )
    return {"max_age": model.max_age, "initial_max_age": initial_max, "required": required}


def _harvest_objective_coefficient(
    model: object, path: tuple[object, ...], action_code: str, utilization: float
) -> float:
    """Objective row: utilized harvest volume along one path."""

    expr = f"totvol * {utilization}"
    return sum(
        model.compile_product(
            period,
            expr,
            node.data()["acode"],
            [node.data()["dtk"]],
            node.data()["age"],
            coeff=False,
        )
        for period, node in enumerate(path, 1)
        if model.is_harvest(node.data()["acode"])
    )


def _action_flow_coefficient(
    model: object, path: tuple[object, ...], action_code: str, utilization: float
) -> dict[int, float]:
    """Even-flow row: per-period utilized volume of the configured action."""

    expr = f"totvol * {utilization}"
    values: dict[int, float] = {}
    for period, node in enumerate(path, 1):
        data = node.data()
        if data["acode"] == action_code:
            values[period] = model.compile_product(
                period,
                expr,
                data["acode"],
                [data["dtk"]],
                data["age"],
                coeff=False,
            )
    return values


def _action_volume_coefficient(
    model: object, path: tuple[object, ...], action_code: str
) -> dict[int, float]:
    """AAC row: per-period harvested totvol of the configured action."""

    values: dict[int, float] = {}
    for period, node in enumerate(path, 1):
        data = node.data()
        if data["acode"] == action_code and model.is_harvest(data["acode"]):
            values[period] = model.compile_product(
                period,
                "totvol",
                data["acode"],
                [data["dtk"]],
                data["age"],
                coeff=False,
            )
    return values


def _build_problem(model: object, config: WS3RunConfig, verbose: bool = False) -> object:
    """Build the WS3 maximization problem with even-flow and AAC constraints."""

    try:
        from ws3 import opt
    except ImportError as exc:
        raise WS3Error(
            "ws3_import_failed",
            "the ws3 package is required for a WS3 solve; add the ws3 repository to "
            "PYTHONPATH (e.g. PYTHONPATH=/srv/shared-data/gep/jupyterhub07-projects/davis/ws3)",
        ) from exc

    action_code = config.objective.action_code
    utilization = config.objective.utilization
    periods = list(range(1, config.horizon + 1))
    tolerance = config.objective.even_flow_tolerance

    coeffs = {
        "z": partial(
            _harvest_objective_coefficient, action_code=action_code, utilization=utilization
        ),
        "even_flow_volume": partial(
            _action_flow_coefficient, action_code=action_code, utilization=utilization
        ),
        "cgen_aac": partial(_action_volume_coefficient, action_code=action_code),
    }
    flow_constraints = {
        "even_flow_volume": even_flow_constraints(periods, tolerance)
    }
    general_constraints = {
        "cgen_aac": aac_ceiling_constraints(
            periods, config.aac_annual_m3, config.period_length
        )
    }

    build_started = time.monotonic()
    try:
        problem = model.add_problem(
            "tsa29_full_max_volume",
            coeffs,
            cflw_e=flow_constraints,
            cgen_data=general_constraints,
            solver=opt.SOLVER_HIGHS,
            sense=opt.SENSE_MAXIMIZE,
            acodes=["null", action_code],
            mask=None,
            workers=config.workers,
            verbose=verbose,
        )
    except Exception as exc:
        raise WS3Error(
            "ws3_problem_build_failed",
            f"WS3 problem build failed after {time.monotonic() - build_started:.1f}s: {exc}",
        ) from exc
    return problem


def _compile_schedule(
    model: object, problem: object, config: WS3RunConfig
) -> tuple[pd.DataFrame, float]:
    """Compile the optimal schedule, attach volumes, and return sorted records.

    Returns ``(schedule, objective_value)`` where ``objective_value`` is the
    modeled objective: ``utilization`` times the sum of harvested ``totvol``.
    """

    raw_schedule = model.compile_schedule(problem)
    # Apply the schedule to populate yield components for compile_product.
    model.apply_schedule(raw_schedule, compile_t_ycomps=True, compile_c_ycomps=True)
    schedule = pd.DataFrame(raw_schedule, columns=SCHEDULE_RAW_COLUMNS)
    volumes = [
        float(
            model.compile_product(
                int(period),
                "totvol",
                action,
                [dtype_key],
                int(age_class),
                coeff=False,
            )
        )
        for dtype_key, age_class, action, period in zip(
            schedule["dtype_key"],
            schedule["age_class"],
            schedule["harvest_action"],
            schedule["period"],
        )
    ]
    schedule["volume_m3"] = volumes
    schedule["stratum"] = schedule["dtype_key"].map(_stratum_from_dtype_key)
    schedule["dtype_key"] = schedule["dtype_key"].map(_dtype_key_to_json)
    schedule["year"] = config.base_year + (schedule["period"] - 1) * config.period_length
    schedule = (
        schedule.loc[:, SCHEDULE_COLUMNS]
        .sort_values(["period", "dtype_key", "age_class", "harvest_action"])
        .reset_index(drop=True)
    )
    objective_value = float(schedule["volume_m3"].sum()) * config.objective.utilization
    return schedule, objective_value


def _stratum_from_dtype_key(dtype_key: object) -> str:
    """Extract the stratum (development type) theme from a WS3 dtype key."""

    if isinstance(dtype_key, tuple) and len(dtype_key) > WS3_DEVELOPMENT_TYPE_INDEX:
        return str(dtype_key[WS3_DEVELOPMENT_TYPE_INDEX])
    return str(dtype_key)


def _dtype_key_to_json(dtype_key: object) -> str:
    """Serialize a WS3 dtype key tuple as a deterministic JSON list."""

    if isinstance(dtype_key, tuple):
        return json.dumps(list(dtype_key))
    return str(dtype_key)


def _file_checksums(directory: Path) -> dict[str, str]:
    """Return SHA-256 digests of every bridge file in the directory."""

    files = sorted(directory.glob(f"{BRIDGE_FILE_PREFIX}.*"))
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


__all__ = [
    "AREA_CONSERVATION_REL_TOLERANCE",
    "BRIDGE_FILE_PREFIX",
    "BRIDGE_FILE_SUFFIXES",
    "CANONICAL_TSA29_BRIDGE",
    "CC_MAX_HARVEST_AGE",
    "CC_MIN_HARVEST_AGE",
    "DEFAULT_AAC_ANNUAL_M3",
    "DEFAULT_AGE_SMASHING_MIDPOINT",
    "DEFAULT_AGE_SMASHING_WIDTH",
    "DEFAULT_EVEN_FLOW_TOLERANCE",
    "DEFAULT_UTILIZATION",
    "DERIVED_BRIDGE_DIRNAME",
    "FEMIC_SRC_ROOT",
    "NEVER_MERCHANTABLE_CURVES",
    "SCHEDULE_COLUMNS",
    "STAGE1_DERIVED_DIRNAME",
    "STAGE1_FILE_NAMES",
    "WS3Error",
    "aac_ceiling_constraints",
    "build_smashed_no_lu_bridge",
    "derived_bridge_path",
    "enforce_harvest_age_range",
    "even_flow_constraints",
    "load_full_model",
    "midpoint_age",
    "normalize_status",
    "problem_lp_dimensions",
    "resolved_bridge_path",
    "run_smoke_test",
    "run_ws3",
    "smash_initial_inventory_ages",
    "smoke_config",
]
