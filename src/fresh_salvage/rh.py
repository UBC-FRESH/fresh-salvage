"""Rolling-horizon coupling engine: WS3 schedule -> principal -> agent -> state.

Horizon semantics (rolling-horizon convention)
----------------------------------------------
Each step ``k`` SOLVES a full ``horizon``-period WS3 schedule (default 15
periods of ``period_length`` years) and full 1-year-timestep principal/agent
LPs over the ``period_length``-year step window, but only the FIRST WS3 period
(the current decade at the default ``period_length=10``) is implemented. The
principal/agent LPs therefore cover exactly the implemented window
(``horizon = period_length`` on their side), and
:func:`fresh_salvage.fire.simulate_cohort_years` replays exactly those
implemented years with the agent's H/S fractions. ``period_length=1`` is the
degenerate alternative: one implemented year per step, no decadal-to-annual
split (the annual ceiling equals the WS3 period-1 volume directly).

Forest-state feedback into WS3
------------------------------
The bridge files are static; only the inventory changes between steps. One
``ws3.forest.ForestModel`` instance is loaded once (bridge reuse plus config
caching), and each step overwrites the period-0 inventory in place —
``DevelopmentType._areas[0] = defaultdict(float, {age: area_ha})`` keyed by
the cohort table — then calls ``ForestModel.initialize_areas(reset_areas=True)``
and rebuilds the problem with ``ws3._build_problem``. This is the same
injection pattern as :func:`fresh_salvage.ws3.smash_initial_inventory_ages`;
it is safe because ``ForestModel.add_problem``/``ForestModel.reset`` only ever
copy period-0 areas forward (periods 1..N are reset, period 0 is the
caller-owned initial inventory). A full model rebuild per step is therefore
unnecessary; the per-step cost is problem build + solve (~11 s at 15 periods).

Cohort state and transition rules
---------------------------------
The shared state is the cohort table — one row per
``(tsa, ifm, au_id, stratum_code, curve_id, age)`` with ``area_ha`` — parsed
from and written to Woodstock ARE sections (8-token ``*A`` rows). The initial
state is the derived no-LU bridge ARE section. Per step, the fire simulation
partitions each cohort's area into four exhaustive fractions (they sum to one
by construction, so area is conserved to 1e-6 and verified):

- surviving live area stays in the cohort at ``age + period_length``, clamped
  to the cohort's curve age cap (absorbing oldest class; ages stay on the
  smashing midpoint lattice 5/15/25/...);
- harvested area (agent ``sum_t H[c,t]``) regenerates at the smashing
  midpoint age (5 with the default 10/5 smashing);
- salvaged area (agent ``sum_t S[c,t]``) regenerates at the midpoint age;
- burned-but-unsalvaged area (``1 - live_end - H - S``) regenerates at the
  midpoint age at the step boundary.

Burned-stock boundary (documented deviation)
--------------------------------------------
Unsalvaged burned area resets to regeneration at the step boundary; no
burned-volume inventory is carried into the next step's WS3/principal/agent
inputs. This TRUNCATES the multi-year salvage window the agent LP allows
within a step (``S[t] <= B[t-1] + BURN_IN[t]``, 0.85 annual retention). The
predecessor ``Rolling_horizon_structure`` scripts carry no burned stock
across steps either: ``RH.py`` only removes harvested stands from the state
and ``RH2.py`` walks one STATIC pre-solved WS3 schedule, decrementing pool
areas and scaling future rows' ``salvage_volume_per_area`` by
``1 - salvage_decay`` — there is no re-solve and no burned-inventory state
variable. With no predecessor mechanism to mirror, the reset-to-regen mapping
is the implemented choice, with rationale: (1) the WS3 ``.are`` inventory
carries live-stand area only, so a burned-volume carry-over has no
representation on the WS3 side; (2) the 0.85 decay leaves under 20% of
unsalvaged burned volume after 10 years, so the truncated tail is small;
(3) within-step salvage is fully modeled by the agent LP.

Other documented deviations from the predecessor
------------------------------------------------
- No area/volume unit mixing: every capacity row in the coupled LPs is
  volume-denominated (m3). The predecessor ``P_RH_Version.py``/
  ``A_RH_Version.py`` summed AREA against the volume-denominated AAC; here the
  WS3-derived per-cohort ceilings are m3/yr of green volume.
- The principal LP keeps the global AAC ceiling and ADDS the WS3 period-1
  decadal harvest as per-cohort annual green-volume ceilings
  (``decadal_m3 / period_length`` per year; the split conserves volume to
  1e-6 by construction).

Boundary parsing reuses the principal/agent ARE cohort parsers and the
femic stage-1 yields/stands economics aggregations, so cohort identity
(``ifm:au_id:stratum_code:curve_id:age``) and volume derivation stay exactly
aligned across the three models.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from fresh_salvage import agent, fire, principal, ws3
from fresh_salvage.models import (
    ArtifactLayout,
    RHManifest,
    RHResult,
    RHRunConfig,
    RHStepRecord,
    WS3RunConfig,
    safe_slug,
)

AREA_CONSERVATION_REL_TOLERANCE = ws3.AREA_CONSERVATION_REL_TOLERANCE
FRACTION_SNAP_TOLERANCE = 1e-9

STATE_KEY_COLUMNS = ("tsa", "ifm", "au_id", "stratum_code", "curve_id", "age")
COHORT_COLUMNS = (*STATE_KEY_COLUMNS, "area_ha")
ARE_ROW_TOKEN_COUNT = 8


class RHError(RuntimeError):
    """Fatal rolling-horizon failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_cohort_table(are_path: Path) -> pd.DataFrame:
    """Parse a Woodstock ARE section into the trusted cohort state table.

    Boundary parser: every ``*A`` row must carry exactly 8 tokens
    (``*A tsa ifm au_id stratum_code curve_id age area_ha``) with numeric
    curve/age/area, non-negative area and age, and a unique state key. Any
    violation raises ``RHError`` with a structured code; callers never
    re-validate the returned frame.
    """

    are_path = Path(are_path)
    if not are_path.is_file():
        raise RHError("rh_state_missing", f"cohort state ARE section not found: {are_path}")
    rows: list[tuple[str, str, str, str, int, int, float]] = []
    for line_number, line in enumerate(are_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip().startswith("*A"):
            continue
        tokens = line.split()
        if len(tokens) != ARE_ROW_TOKEN_COUNT:
            raise RHError(
                "rh_state_unparseable",
                f"{are_path} line {line_number} is not an {ARE_ROW_TOKEN_COUNT}-token "
                f"ARE data row: {line!r}",
            )
        _, tsa, ifm, au_id, stratum_code, curve_text, age_text, area_text = tokens
        try:
            curve_id = int(curve_text)
            age = int(age_text)
            area_ha = float(area_text)
        except ValueError as exc:
            raise RHError(
                "rh_state_unparseable",
                f"{are_path} line {line_number} has non-numeric curve/age/area: {line!r}",
            ) from exc
        if age < 0:
            raise RHError(
                "rh_state_invalid_age",
                f"{are_path} line {line_number} has negative age {age}: {line!r}",
            )
        if area_ha < 0:
            raise RHError(
                "rh_state_negative_area",
                f"{are_path} line {line_number} has negative area {area_ha}: {line!r}",
            )
        rows.append((tsa, ifm, au_id, stratum_code, curve_id, age, area_ha))
    if not rows:
        raise RHError("rh_state_empty", f"ARE section {are_path} contains no data rows")
    state = pd.DataFrame(rows, columns=pd.Index(list(COHORT_COLUMNS)))
    if state.duplicated(subset=list(STATE_KEY_COLUMNS)).any():
        raise RHError(
            "rh_state_duplicate_cohort",
            f"ARE section {are_path} contains duplicate "
            f"{'/'.join(STATE_KEY_COLUMNS)} keys",
        )
    return state


def write_cohort_table(state: pd.DataFrame, are_path: Path) -> Path:
    """Write the cohort state table as a Woodstock ARE section (lossless).

    ``repr`` formatting round-trips the float area exactly, so the written
    artifact is bit-identical state: re-parsing it conserves area perfectly.
    """

    are_path = Path(are_path)
    are_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"*A {row.tsa} {row.ifm} {row.au_id} {row.stratum_code} "
        f"{row.curve_id} {row.age} {row.area_ha!r}"
        for row in state.itertuples(index=False)
    ]
    are_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return are_path


def cohort_record_id(
    ifm: str, au_id: str, stratum_code: str, curve_id: int, age: int
) -> str:
    """Return the principal/agent cohort id of one state row."""

    return f"{ifm}:{au_id}:{stratum_code}:{curve_id}:{age}"


def curve_age_caps(
    curves: dict[int, tuple[object, object]], *, width: int, midpoint: int
) -> dict[int, int]:
    """Return the absorbing age cap of each yield curve on the midpoint lattice.

    The cap is the largest age-smashing midpoint not beyond the curve's
    tabulated maximum age: ``midpoint_age(max_age)`` when it does not exceed
    ``max_age``, else one class lower. Cohorts already older than the cap are
    absorbed into the cap class (area sums); because both WS3 and the
    principal/agent parsers extend yield curves constantly beyond the last
    tabulated age, the absorption is volume-neutral up to curve flatness.
    """

    caps: dict[int, int] = {}
    for curve_id, (ages, _volumes) in curves.items():
        max_age = int(ages[-1])
        cap = ws3.midpoint_age(max_age, width, midpoint)
        if cap > max_age:
            cap -= width
        if cap < midpoint:
            raise RHError(
                "rh_curve_age_cap_invalid",
                f"curve {curve_id} max tabulated age {max_age} cannot host the "
                f"regeneration midpoint age {midpoint}",
            )
        caps[int(curve_id)] = cap
    return caps


def annual_ceiling(decadal_volume_m3: float, period_length: int) -> float:
    """Split one decadal volume uniformly into annual ceilings (m3/yr).

    The split is exactly conservative: ``period_length`` annual ceilings sum
    back to the decadal volume up to float dust (tested to 1e-6).
    """

    if period_length <= 0:
        raise RHError(
            "rh_invalid_period_length",
            f"period_length must be positive: {period_length}",
        )
    if decadal_volume_m3 < 0:
        raise RHError(
            "rh_invalid_ceiling", f"decadal ceiling volume cannot be negative: {decadal_volume_m3}"
        )
    return float(decadal_volume_m3) / period_length


def advance_cohort_table(
    state: pd.DataFrame,
    *,
    harvests: list[tuple[float, ...]],
    salvages: list[tuple[float, ...]],
    decay_rate: float,
    age_caps: dict[int, int],
    period_length: int,
    regeneration_age: int,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Advance the cohort table over one implemented step via fire dynamics.

    ``harvests``/``salvages`` carry one fraction tuple of length
    ``period_length`` per state row (agent H/S fractions of initial standing
    volume; area fractions equal volume fractions inside an age-homogeneous
    cohort). Returns ``(new_state, totals)`` where the new table is
    re-aggregated over the state key and ``totals`` reports the step's
    burned/harvested/salvaged areas (ha). Area is conserved to 1e-6 (relative)
    or the step fails fast.
    """

    if period_length <= 0:
        raise RHError(
            "rh_invalid_period_length",
            f"period_length must be positive: {period_length}",
        )
    if len(harvests) != len(state) or len(salvages) != len(state):
        raise RHError(
            "rh_schedule_shape_mismatch",
            f"harvest/salvage schedules cover {len(harvests)}/{len(salvages)} cohorts "
            f"but the state table holds {len(state)}",
        )
    rows: list[tuple[str, str, str, str, int, int, float]] = []
    totals = {"area_burned_ha": 0.0, "area_harvested_ha": 0.0, "area_salvaged_ha": 0.0}
    for index, row in enumerate(state.itertuples(index=False)):
        cohort_id = cohort_record_id(
            row.ifm, row.au_id, row.stratum_code, int(row.curve_id), int(row.age)
        )
        harvest_schedule = tuple(float(value) for value in harvests[index])
        salvage_schedule = tuple(float(value) for value in salvages[index])
        if len(harvest_schedule) != period_length or len(salvage_schedule) != period_length:
            raise RHError(
                "rh_schedule_shape_mismatch",
                f"cohort {cohort_id!r} schedules must cover the {period_length} "
                f"implemented years: {len(harvest_schedule)}/{len(salvage_schedule)}",
            )
        try:
            burn_rate = fire.annual_burn_rate_for_stratum(row.stratum_code)
        except fire.UnknownBurnRateError as exc:
            raise RHError(
                "rh_burn_rate_unknown",
                f"cohort {cohort_id!r} has no MFRI fire-rate entry: {exc}",
            ) from exc
        cap = age_caps.get(int(row.curve_id))
        if cap is None:
            raise RHError(
                "rh_curve_age_cap_missing",
                f"cohort {cohort_id!r} curve {row.curve_id} has no yield-curve age cap",
            )
        try:
            years = fire.simulate_cohort_years(
                initial_live=1.0,
                burn_rate=burn_rate,
                harvest_schedule=harvest_schedule,
                salvage_schedule=salvage_schedule,
                decay_rate=decay_rate,
            )
        except fire.FireDynamicsError as exc:
            raise RHError(
                "rh_fire_simulation_failed",
                f"cohort {cohort_id!r} fire replay failed: {exc}",
            ) from exc
        live_end = years[-1].live_after
        harvested = sum(harvest_schedule)
        salvaged = sum(salvage_schedule)
        unsalvaged_burned = 1.0 - live_end - harvested - salvaged
        if abs(unsalvaged_burned) <= FRACTION_SNAP_TOLERANCE:
            unsalvaged_burned = 0.0
        elif unsalvaged_burned < 0:
            raise RHError(
                "rh_fraction_balance_failed",
                f"cohort {cohort_id!r} fractions over-allocate the cohort: "
                f"live_end {live_end} + harvested {harvested} + salvaged {salvaged} "
                f"exceeds 1 by {-unsalvaged_burned}",
            )
        area_ha = float(row.area_ha)
        surviving_area = area_ha * live_end
        removed_area = area_ha * (harvested + salvaged + unsalvaged_burned)
        if surviving_area > 0.0:
            rows.append(
                (
                    row.tsa,
                    row.ifm,
                    row.au_id,
                    row.stratum_code,
                    int(row.curve_id),
                    min(int(row.age) + period_length, cap),
                    surviving_area,
                )
            )
        if removed_area > 0.0:
            rows.append(
                (
                    row.tsa,
                    row.ifm,
                    row.au_id,
                    row.stratum_code,
                    int(row.curve_id),
                    regeneration_age,
                    removed_area,
                )
            )
        totals["area_burned_ha"] += area_ha * sum(year.burn_influx for year in years)
        totals["area_harvested_ha"] += area_ha * harvested
        totals["area_salvaged_ha"] += area_ha * salvaged
    if not rows:
        raise RHError(
            "rh_state_empty_after_advance",
            "the state update left no positive-area cohorts",
        )
    new_state = (
        pd.DataFrame(rows, columns=pd.Index(list(COHORT_COLUMNS)))
        .groupby(list(STATE_KEY_COLUMNS), as_index=False, sort=True)["area_ha"]
        .sum()
        .loc[:, list(COHORT_COLUMNS)]
    )
    _require_area_conserved(
        # skipna=False: a corrupt (NaN) area must reach the gate, not be
        # silently skipped by the reduction.
        float(state["area_ha"].sum(skipna=False)),
        float(new_state["area_ha"].sum(skipna=False)),
        context="state advance",
    )
    return new_state, totals


def inject_cohort_table(model: object, state: pd.DataFrame) -> dict[str, object]:
    """Overwrite a ForestModel's initial inventory from the cohort table.

    Every model development type receives a fresh ``defaultdict(float)`` of
    ``{age: area_ha}`` (empty when the state has no rows for it), then
    ``initialize_areas(reset_areas=True)`` re-derives period-1 areas. State
    keys absent from the model fail fast; the injected area is reconciled
    against the state table to 1e-6 (relative).
    """

    grouped: dict[tuple[str, ...], dict[int, float]] = {}
    for row in state.itertuples(index=False):
        key = (row.tsa, row.ifm, row.au_id, row.stratum_code, str(row.curve_id))
        ages = grouped.setdefault(key, {})
        ages[int(row.age)] = ages.get(int(row.age), 0.0) + float(row.area_ha)
    unknown = sorted(key for key in grouped if key not in model.dtypes)
    if unknown:
        raise RHError(
            "rh_state_dtype_unknown",
            f"cohort state references {len(unknown)} development types absent from "
            f"the WS3 model (e.g. {unknown[:3]})",
        )
    for dtype_key, development_type in model.dtypes.items():
        development_type._areas[0] = defaultdict(float, grouped.get(dtype_key, {}))
    model.initialize_areas(reset_areas=True)
    injected_area_ha = sum(
        area
        for development_type in model.dtypes.values()
        for area in development_type._areas[0].values()
    )
    _require_area_conserved(
        float(state["area_ha"].sum(skipna=False)),
        injected_area_ha,
        context="inventory injection",
    )
    return {"dtypes_with_area": len(grouped), "area_ha": injected_area_ha}


def run_rh(config: RHRunConfig, verbose: bool = False) -> RHResult:
    """Run the rolling-horizon coupled loop and write artifacts + manifest.

    Raises ``RHError`` on fatal boundary, solver, or state failures; a step
    failure wraps the underlying structured error with the step context.
    Completed per-step JSONL records are flushed to ``steps_path`` as they
    finish, so a failed run still leaves its partial trajectory on disk.
    """

    started_at = datetime.now(UTC)
    wall_started = time.monotonic()
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    run_slug = safe_slug(config.run_id)
    steps_path = layout.data_path(f"{run_slug}-steps", ext="jsonl")
    final_state_path = layout.data_path(f"{run_slug}-final-state", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-rh-manifest")
    state_dir = Path(config.output_root) / "derived" / "rh_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    ws3_config = WS3RunConfig(
        run_id=config.run_id,
        bridge_path=config.bridge_path,
        base_year=config.base_year,
        horizon=config.horizon,
        period_length=config.period_length,
        max_age=config.max_age,
        workers=config.workers,
        age_smashing=config.age_smashing,
        objective=config.objective,
        aac_annual_m3=config.aac_annual_m3,
        output_root=config.output_root,
    )
    bridge = ws3.resolved_bridge_path(ws3_config)
    ws3_config = ws3_config.model_copy(update={"bridge_path": bridge})

    # Boundary parse, once per run: stands economics and yield curves are
    # static inputs (config caching); only the cohort ARE section changes.
    stands = principal._read_table(config.stands_path, "rh_stands_missing")
    principal_economics = principal._development_type_economics(stands)
    agent_economics = agent._development_type_economics(stands)
    curves = principal._yield_curves(Path(config.yields_path))
    age_caps = curve_age_caps(
        curves, width=config.age_smashing.width, midpoint=config.age_smashing.midpoint
    )

    state = read_cohort_table(Path(bridge) / f"{ws3.BRIDGE_FILE_PREFIX}.are")
    model = ws3.load_full_model(ws3_config, verbose=verbose)

    step_records: list[RHStepRecord] = []
    with steps_path.open("w", encoding="utf-8") as steps_file:
        for step in range(1, config.steps + 1):
            record, state = _run_step(
                step,
                config=config,
                model=model,
                ws3_config=ws3_config,
                state=state,
                state_dir=state_dir,
                layout=layout,
                principal_economics=principal_economics,
                agent_economics=agent_economics,
                curves=curves,
                age_caps=age_caps,
                verbose=verbose,
            )
            step_records.append(record)
            steps_file.write(record.model_dump_json() + "\n")
            steps_file.flush()

    state.to_csv(final_state_path, index=False)
    wall_seconds = time.monotonic() - wall_started
    final_age_distribution = {
        str(int(age)): float(area)
        for age, area in state.groupby("age")["area_ha"].sum().items()
    }
    # Derive the run status from the recorded per-step WS3 solve statuses
    # instead of asserting a constant: a step that solved non-optimally
    # without raising would surface here as "degraded".
    status = (
        "optimal"
        if all(record.ws3_status == "optimal" for record in step_records)
        else "degraded"
    )
    manifest = RHManifest(
        run_id=config.run_id,
        stands_path=config.stands_path,
        yields_path=config.yields_path,
        bridge_path=bridge,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        status=status,
        steps=config.steps,
        horizon=config.horizon,
        period_length=config.period_length,
        cohorts=len(state),
        total_green_harvest_m3=sum(
            sum(record.annual_green_harvest_m3) for record in step_records
        ),
        total_burned_harvest_m3=sum(
            sum(record.annual_burned_harvest_m3) for record in step_records
        ),
        total_area_burned_ha=sum(record.area_burned_ha for record in step_records),
        wall_seconds=wall_seconds,
        source_sha256=_source_checksums(config, bridge),
        step_records=step_records,
        config=config.model_dump(mode="json"),
    )
    manifest.write_json(manifest_path)

    return RHResult(
        run_id=config.run_id,
        status=status,
        steps=config.steps,
        horizon=config.horizon,
        period_length=config.period_length,
        cohorts=len(state),
        step_records=step_records,
        decadal_green_harvest_m3=[
            sum(record.annual_green_harvest_m3) for record in step_records
        ],
        decadal_burned_harvest_m3=[
            sum(record.annual_burned_harvest_m3) for record in step_records
        ],
        decadal_area_burned_ha=[record.area_burned_ha for record in step_records],
        final_age_distribution_ha=final_age_distribution,
        wall_seconds=wall_seconds,
        steps_path=steps_path,
        final_state_path=final_state_path,
        manifest_path=manifest_path,
    )


def _run_step(
    step: int,
    *,
    config: RHRunConfig,
    model: object,
    ws3_config: WS3RunConfig,
    state: pd.DataFrame,
    state_dir: Path,
    layout: ArtifactLayout,
    principal_economics: dict[str, dict[str, float]],
    agent_economics: dict[str, dict[str, float]],
    curves: dict[int, tuple[object, object]],
    age_caps: dict[int, int],
    verbose: bool,
) -> tuple[RHStepRecord, pd.DataFrame]:
    """Execute one rolling-horizon step and return its record + next state."""

    step_started = time.monotonic()
    start_year = config.base_year + (step - 1) * config.period_length
    step_slug = safe_slug(f"{config.run_id}-step-{step:02d}")
    try:
        # Canonicalize the in-memory state through its artifact: the file the
        # principal/agent parse is bit-identical to the injected inventory.
        are_path = write_cohort_table(state, state_dir / f"step_{step - 1:02d}.are")
        state = read_cohort_table(are_path)
        inject_cohort_table(model, state)

        build_started = time.monotonic()
        problem = ws3._build_problem(model, ws3_config, verbose=verbose)
        ws3_build_seconds = time.monotonic() - build_started
        solve_started = time.monotonic()
        try:
            problem.solve(verbose=verbose)
        except Exception as exc:
            raise ws3.WS3Error(
                "ws3_solve_failed",
                f"step {step} HiGHS solve raised {type(exc).__name__}: {exc}",
            ) from exc
        ws3_solve_seconds = time.monotonic() - solve_started
        ws3_status = ws3.normalize_status(problem.status())
        if ws3_status != "optimal":
            raise ws3.WS3Error(
                "ws3_solve_not_optimal",
                f"step {step} WS3 solve did not reach optimal status: {ws3_status}",
            )
        schedule, ws3_objective = ws3._compile_schedule(model, problem, ws3_config)
        schedule_path = layout.data_path(f"{step_slug}-schedule", ext="csv")
        schedule.to_csv(schedule_path, index=False)
        decadal_volumes = _period1_cohort_volumes(
            schedule, ws3_config.objective.action_code, state
        )

        # Principal LP: global AAC plus the WS3-derived per-cohort annual
        # green-volume ceilings (decadal volume split over the step window).
        cohorts_p = principal._parse_are_cohorts(are_path, principal_economics, curves)
        ceilings = [
            annual_ceiling(
                decadal_volumes.get(cohort.cohort_id, 0.0), config.period_length
            )
            for cohort in cohorts_p
        ]
        principal_result = principal.solve_principal(
            cohorts_p,
            horizon=config.period_length,
            aac_annual_m3=config.aac_annual_m3,
            decay_rate=config.decay_rate,
            burned_limit_annual_m3=config.burned_limit_annual_m3,
            cohort_ceilings_m3=ceilings,
            run_id=f"{step_slug}-principal",
        )
        offers_path = layout.data_path(f"{step_slug}-offers", ext="parquet")
        pd.DataFrame([offer.model_dump() for offer in principal_result.offers]).to_parquet(
            offers_path, index=False
        )

        # Agent LP against the principal offers, then fire replay over the
        # implemented years advances the state.
        cohorts_a = agent._parse_are_cohorts(are_path, agent_economics, curves)
        offers = agent.resolve_offers(
            cohorts_a, horizon=config.period_length, offers_path=offers_path
        )
        agent_result = agent.solve_agent(
            cohorts_a,
            offers,
            horizon=config.period_length,
            decay_rate=config.decay_rate,
            discount_rate=config.discount_rate,
            run_id=f"{step_slug}-agent",
        )
        decisions_path = layout.data_path(f"{step_slug}-decisions", ext="parquet")
        pd.DataFrame(
            [decision.model_dump() for decision in agent_result.decisions]
        ).to_parquet(decisions_path, index=False)
        harvests, salvages = _fraction_schedules(state, agent_result, config.period_length)
        new_state, totals = advance_cohort_table(
            state,
            harvests=harvests,
            salvages=salvages,
            decay_rate=config.decay_rate,
            age_caps=age_caps,
            period_length=config.period_length,
            regeneration_age=config.age_smashing.midpoint,
        )
    except RHError:
        raise
    except (
        ws3.WS3Error,
        principal.PrincipalError,
        agent.AgentError,
        fire.FireDynamicsError,
    ) as exc:
        raise RHError(
            "rh_step_failed",
            f"rolling-horizon step {step} failed: "
            f"[{getattr(exc, 'code', type(exc).__name__)}] {exc}",
        ) from exc

    record = RHStepRecord(
        step=step,
        start_year=start_year,
        ws3_status=ws3_status,
        ws3_objective_value=ws3_objective,
        ws3_build_seconds=ws3_build_seconds,
        ws3_solve_seconds=ws3_solve_seconds,
        principal_objective_value=principal_result.objective_value,
        principal_solve_seconds=principal_result.solve_seconds,
        agent_objective_value=agent_result.objective_value,
        agent_solve_seconds=agent_result.solve_seconds,
        annual_green_harvest_m3=[
            volumes.harvest_volume_m3 for volumes in agent_result.per_year_volumes
        ],
        annual_burned_harvest_m3=[
            volumes.salvage_volume_m3 for volumes in agent_result.per_year_volumes
        ],
        area_burned_ha=totals["area_burned_ha"],
        wall_seconds=time.monotonic() - step_started,
    )
    return record, new_state


def _period1_cohort_volumes(
    schedule: pd.DataFrame, action_code: str, state: pd.DataFrame
) -> dict[str, float]:
    """Sum WS3 period-1 harvest volume (m3) per cohort id over the schedule.

    Only rows of the configured harvest action with positive area count.
    A positive-volume harvest row whose cohort is absent from the state table
    is a coupling anomaly and fails fast.
    """

    known_ids = {
        cohort_record_id(
            row.ifm, row.au_id, row.stratum_code, int(row.curve_id), int(row.age)
        )
        for row in state.itertuples(index=False)
    }
    harvest_rows = schedule[
        (schedule["period"] == 1)
        & (schedule["harvest_action"] == action_code)
        & (schedule["area_ha"] > 0.0)
    ]
    volumes: dict[str, float] = {}
    for row in harvest_rows.itertuples(index=False):
        key = json.loads(row.dtype_key)
        if len(key) != 5:
            raise RHError(
                "rh_schedule_dtype_malformed",
                f"WS3 schedule dtype key is not a 5-element list: {row.dtype_key!r}",
            )
        _tsa, ifm, au_id, stratum_code, curve_id = (str(part) for part in key)
        cohort_id = cohort_record_id(
            ifm, au_id, stratum_code, int(curve_id), int(row.age_class)
        )
        if cohort_id not in known_ids:
            raise RHError(
                "rh_schedule_unknown_cohort",
                f"WS3 period-1 harvest references cohort {cohort_id!r}, which is "
                "absent from the injected state table",
            )
        volumes[cohort_id] = volumes.get(cohort_id, 0.0) + float(row.volume_m3)
    return volumes


def _fraction_schedules(
    state: pd.DataFrame, result: object, period_length: int
) -> tuple[list[tuple[float, ...]], list[tuple[float, ...]]]:
    """Align agent H/S fractions to state row order as per-year schedules.

    Decisions are grouped per cohort in emission order, so each cohort's
    years must be non-decreasing; an out-of-order emission would silently
    misalign the schedule tuples with the implemented years and fails fast.
    """

    harvest_by_id: dict[str, list[float]] = {}
    salvage_by_id: dict[str, list[float]] = {}
    last_year_by_id: dict[str, int] = {}
    for decision in result.decisions:
        last_year = last_year_by_id.get(decision.cohort_id)
        if last_year is not None and decision.year < last_year:
            raise RHError(
                "rh_decision_order_invalid",
                f"agent decisions for cohort {decision.cohort_id!r} are not "
                f"year-ascending (year {decision.year} follows year {last_year}); "
                "fraction schedules would misalign the implemented years",
            )
        last_year_by_id[decision.cohort_id] = decision.year
        harvest_by_id.setdefault(decision.cohort_id, []).append(decision.harvest_fraction)
        salvage_by_id.setdefault(decision.cohort_id, []).append(decision.salvage_fraction)
    harvests: list[tuple[float, ...]] = []
    salvages: list[tuple[float, ...]] = []
    for row in state.itertuples(index=False):
        cohort_id = cohort_record_id(
            row.ifm, row.au_id, row.stratum_code, int(row.curve_id), int(row.age)
        )
        harvest = harvest_by_id.get(cohort_id)
        salvage = salvage_by_id.get(cohort_id)
        if harvest is None or salvage is None:
            raise RHError(
                "rh_decisions_incomplete",
                f"agent decisions do not cover cohort {cohort_id!r}",
            )
        harvests.append(tuple(harvest))
        salvages.append(tuple(salvage))
    return harvests, salvages


def _require_area_conserved(before_ha: float, after_ha: float, *, context: str) -> None:
    """Fail fast when area is not conserved to the relative tolerance.

    Non-finite totals never conserve: pandas reductions skip NaN by default,
    so a corrupt (NaN/inf) area could otherwise pass ``isclose`` unnoticed.
    """

    if (
        math.isfinite(before_ha)
        and math.isfinite(after_ha)
        and math.isclose(after_ha, before_ha, rel_tol=AREA_CONSERVATION_REL_TOLERANCE)
    ):
        return
    raise RHError(
        "rh_area_conservation_failed",
        f"{context} conserves {after_ha:.6f} ha but started from {before_ha:.6f} ha "
        f"(delta {after_ha - before_ha:+.6f} ha, rel tolerance "
        f"{AREA_CONSERVATION_REL_TOLERANCE})",
    )


def _source_checksums(config: RHRunConfig, bridge: Path) -> dict[str, str]:
    """Return SHA-256 digests of the run's input tables and bridge files."""

    checksums = {
        "stands": _sha256_file(config.stands_path),
        "yields": _sha256_file(config.yields_path),
    }
    for name, digest in ws3._file_checksums(Path(bridge)).items():
        checksums[f"bridge/{name}"] = digest
    return checksums


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "ARE_ROW_TOKEN_COUNT",
    "AREA_CONSERVATION_REL_TOLERANCE",
    "COHORT_COLUMNS",
    "FRACTION_SNAP_TOLERANCE",
    "RHError",
    "STATE_KEY_COLUMNS",
    "advance_cohort_table",
    "annual_ceiling",
    "cohort_record_id",
    "curve_age_caps",
    "inject_cohort_table",
    "read_cohort_table",
    "run_rh",
    "write_cohort_table",
]
