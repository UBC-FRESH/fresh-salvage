"""Single-pass 100-year planning baseline: one solve, one replay.

Semantics
---------
:func:`run_single_pass` runs the coupled WS3 -> principal -> agent -> fire
pipeline that :func:`fresh_salvage.rh.run_rh` runs, but solves it ONCE at t=0
over the whole run (``steps * period_length`` years, 10 x 10 = 100 by default)
and never re-plans mid-horizon. It is the baseline rolling-horizon
re-planning is compared against (planning decisions D1-D9 in
``planning/phase8-single-pass-baseline.md``):

1. the boundary parse and the period-0 inventory injection are the same as
   rolling horizon (:func:`fresh_salvage.rh.read_cohort_table`,
   :func:`fresh_salvage.rh.inject_cohort_table`), and the one WS3 solve is the
   same ``horizon``-period problem ``rh-run`` solves at step 1 (D1); only
   periods ``1..steps`` are implemented;
2. :func:`fresh_salvage.rh.schedule_cohort_period_volumes` maps the schedule's
   per-period ``cc`` volume back onto the t=0 cohorts (PR #32 apportionment
   keeps each group's rows summing to its group total) and
   :func:`fresh_salvage.rh.annual_ceiling_matrix` splits each period into
   uniform annual ceilings;
3. the t=0 cohorts carry their decadal growth surface
   (:func:`fresh_salvage.principal.decadal_per_ha_volumes`, D2), the principal
   LP runs over ``steps * period_length`` years with the grade clock
   ``period_length`` (D5) and the configured burned-stock semantics (D4/D6),
   writes the run-level offers, and the agent LP runs against those offers over
   the same horizon;
4. :func:`replay_lineage` walks the agent's annual H/S fractions decade by
   decade through :func:`fresh_salvage.fire.simulate_cohort_years`, aging
   survivors, regenerating everything that leaves the live pool, and keeping
   the replayed burned inventory consistent with the LP (carried by default,
   dropped at each boundary under ``burned_stock_reset``).

Lineage replay and the regeneration convention (R6 reconciled)
--------------------------------------------------------------
Each t=0 cohort is replayed with its agent H/S fractions, which are fractions
of the cohort's t=0 standing volume and therefore of its area (an
age-homogeneous cohort holds one per-ha volume). At each decade boundary the
area that left the live pool that decade (``area * (live_start - live_end)``:
green harvest plus burn influx; salvage only reclassifies burned area)
regenerates as a regen sub-cohort in the same development type at
``age_smashing.midpoint``, exactly as
:func:`fresh_salvage.rh.advance_cohort_table` regenerates each step's removed
area. Regen sub-cohorts age ``+period_length`` per decade (clamped to the
curve age cap), are never harvested (D3 rejects a WS3 harvest of an
in-horizon regenerated stand), and stay exposed to fire; their own burned area
regenerates again at the next boundary. Surviving live t=0 area ages by
``+period_length`` clamped to the cap. Area is conserved to 1e-6 relative at
every boundary, and ``area_burned_ha`` per decade sums ``area * burn_influx``
over t=0 cohorts and regen sub-cohorts. This mirrors RH's regeneration
convention (the replay decides the reported state); the WS3 t=0 solve uses its
own convention (``*ACTION cc Y`` -> clear-cut target age 0), which only matters
after period ``steps``.

Outputs (planning section 7)
----------------------------
``data/<run>-steps.jsonl`` holds ``steps`` :class:`~fresh_salvage.models.RHStepRecord`
lines directly comparable with ``rh-run``: per-decade annual green/burned
harvest arrays sliced from the 100-year agent result, the replay's
``area_burned_ha``, and the principal/agent objectives decomposed per decade —
``*_objective_value`` re-based to the decade start (D7) and
``*_objective_value_base_year`` re-based to the run base year. The WS3 fields
are populated for decade 1 only (D8; the one solve happens at t=0). The
manifest and result are the regular :class:`~fresh_salvage.models.RHManifest` /
:class:`~fresh_salvage.models.RHResult` with ``planning_mode="single_pass"``
and the base-year run totals (summed from the decade records, the same way the
rolling-horizon engine sums its step records). State snapshots
(``derived/rh_state/step_{k:02d}.are``, ``k = 0..steps``) come from the replay;
``final-state.csv`` is the last snapshot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from fresh_salvage import agent, fire, principal, rh, ws3
from fresh_salvage.models import (
    AgentResult,
    ArtifactLayout,
    PrincipalResult,
    RHManifest,
    RHResult,
    RHRunConfig,
    RHStepRecord,
    WS3RunConfig,
    safe_slug,
)

__all__ = ["LineageReplay", "SinglePassError", "replay_lineage", "run_single_pass"]


class SinglePassError(RuntimeError):
    """Fatal single-pass planning failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LineageReplay:
    """Outcome of one decade-block lineage replay over the whole run.

    ``snapshots[k]`` is the cohort table at boundary ``k`` (after ``k`` decades;
    ``snapshots[0]`` is the canonical initial state), ``area_burned_ha[k]`` the
    decade-``k`` burned area summed over t=0 cohorts and regen sub-cohorts,
    ``area_harvested_ha[k]``/``area_salvaged_ha[k]`` the decade's harvested and
    salvaged areas (RH's per-step totals, for comparability), and
    ``decade_wall_seconds[k]`` the wall time the replay spent on that decade —
    the only per-decade work of a single-pass run.
    """

    snapshots: tuple[pd.DataFrame, ...]
    area_burned_ha: tuple[float, ...]
    area_harvested_ha: tuple[float, ...]
    area_salvaged_ha: tuple[float, ...]
    decade_wall_seconds: tuple[float, ...]


@dataclass
class _ReplayLineage:
    """One live lineage mid-replay: a t=0 cohort or a regenerated sub-cohort.

    ``reference_area_ha`` is the lineage's area at its formation (the t=0
    cohort area, or the removed area a regen sub-cohort regenerated from);
    ``live_before``/``burned_before`` are its end-of-previous-decade inventories
    as fractions of that reference area (``1.0``/``0.0`` for a freshly formed
    regen sub-cohort and for a t=0 cohort at t=0). ``state_index`` indexes the
    initial state row whose agent schedule drives the lineage (``None`` for
    regen sub-cohorts, which are never harvested).
    """

    tsa: str
    ifm: str
    au_id: str
    stratum_code: str
    curve_id: int
    age: int
    reference_area_ha: float
    live_before: float
    burned_before: float
    burn_rate: float
    state_index: int | None


def replay_lineage(
    state: pd.DataFrame,
    *,
    harvests: list[tuple[float, ...]],
    salvages: list[tuple[float, ...]],
    steps: int,
    period_length: int,
    decay_rate: float,
    age_caps: dict[int, int],
    regeneration_age: int,
    burn_rate_multiplier: float = 1.0,
    burned_stock_reset: bool = False,
) -> LineageReplay:
    """Replay ``steps * period_length`` years in decade blocks from a t=0 state.

    ``harvests``/``salvages`` carry one tuple of ``steps * period_length``
    annual fractions per state row (agent H/S fractions of the t=0 standing
    volume, the same shape :func:`rh.advance_cohort_table` consumes per step).
    Every decade runs each active lineage through
    :func:`fresh_salvage.fire.simulate_cohort_years` from its carried live and
    burned inventories, so the LP's dynamics and the replay stay one source of
    truth; ``burned_stock_reset`` drops the carried burned inventory at every
    decade start, mirroring the LP's boundary rows (the live trajectory, and so
    the reported state and burned area, are unaffected).

    Raises ``SinglePassError`` on shape mismatches, unknown burn rates, missing
    age caps, or an area-conservation violation; nothing is written to disk.
    """

    if steps <= 0 or period_length <= 0:
        raise SinglePassError(
            "single_pass_invalid_shape",
            f"steps and period_length must be positive, got {steps} and {period_length}",
        )
    if regeneration_age < 0:
        raise SinglePassError(
            "single_pass_invalid_regeneration_age",
            f"regeneration_age cannot be negative, got {regeneration_age}",
        )
    horizon = steps * period_length
    if len(harvests) != len(state) or len(salvages) != len(state):
        raise SinglePassError(
            "single_pass_schedule_shape_mismatch",
            f"harvest/salvage schedules cover {len(harvests)}/{len(salvages)} "
            f"cohorts but the state table holds {len(state)}",
        )
    for index, (harvest, salvage) in enumerate(zip(harvests, salvages, strict=True)):
        if len(harvest) != horizon or len(salvage) != horizon:
            raise SinglePassError(
                "single_pass_schedule_shape_mismatch",
                f"state row {index} carries {len(harvest)}/{len(salvage)} annual "
                f"fractions but the replay covers {horizon} years",
            )

    snapshots = [state]
    area_burned: list[float] = []
    area_harvested: list[float] = []
    area_salvaged: list[float] = []
    decade_wall: list[float] = []
    active = [
        _replay_lineage_from_state_row(row, index, burn_rate_multiplier)
        for index, row in enumerate(state.itertuples(index=False))
    ]
    for decade in range(steps):
        decade_started = time.monotonic()
        year_start = decade * period_length
        year_stop = year_start + period_length
        burned_decade = 0.0
        harvested_decade = 0.0
        salvaged_decade = 0.0
        next_active: list[_ReplayLineage] = []
        for lineage in active:
            cap = age_caps.get(lineage.curve_id)
            if cap is None:
                raise SinglePassError(
                    "single_pass_curve_age_cap_missing",
                    f"cohort {_lineage_id(lineage)!r} curve {lineage.curve_id} has "
                    "no yield-curve age cap",
                )
            harvest, salvage = _decade_schedules(
                lineage, harvests, salvages, year_start, year_stop, period_length
            )
            years = _simulate_decade(lineage, harvest, salvage, decay_rate)
            live_end = years[-1].live_after
            removed = max(0.0, lineage.live_before - live_end)
            burned_decade += (
                lineage.reference_area_ha * sum(year.burn_influx for year in years)
            )
            harvested_decade += lineage.reference_area_ha * sum(harvest)
            salvaged_decade += lineage.reference_area_ha * sum(salvage)
            survivor_area = lineage.reference_area_ha * live_end
            if survivor_area > 0.0:
                next_active.append(
                    replace(
                        lineage,
                        age=min(lineage.age + period_length, cap),
                        live_before=live_end,
                        burned_before=(
                            0.0 if burned_stock_reset else years[-1].burned_after
                        ),
                    )
                )
            regen_area = lineage.reference_area_ha * removed
            if regen_area > 0.0:
                next_active.append(
                    replace(
                        lineage,
                        age=regeneration_age,
                        reference_area_ha=regen_area,
                        live_before=1.0,
                        burned_before=0.0,
                        state_index=None,
                    )
                )
        snapshot = _replay_state_table(next_active)
        rh._require_area_conserved(
            float(snapshots[-1]["area_ha"].sum(skipna=False)),
            float(snapshot["area_ha"].sum(skipna=False)),
            context=f"single-pass replay decade {decade + 1}",
        )
        snapshots.append(snapshot)
        area_burned.append(burned_decade)
        area_harvested.append(harvested_decade)
        area_salvaged.append(salvaged_decade)
        decade_wall.append(time.monotonic() - decade_started)
        active = next_active
    return LineageReplay(
        snapshots=tuple(snapshots),
        area_burned_ha=tuple(area_burned),
        area_harvested_ha=tuple(area_harvested),
        area_salvaged_ha=tuple(area_salvaged),
        decade_wall_seconds=tuple(decade_wall),
    )


def run_single_pass(config: RHRunConfig, verbose: bool = False) -> RHResult:
    """Run the single-pass coupled pipeline and write artifacts + manifest.

    Requires a validated ``planning_mode="single_pass"`` config (the CLI's
    ``single-pass-run`` forces it; the :func:`fresh_salvage.rh.run_planning`
    dispatcher routes by it) and ``horizon >= steps``. Raises
    ``SinglePassError`` on fatal boundary, solver, replay, or state failures;
    decade records are flushed to ``steps_path`` as they are emitted, so a
    failed run still leaves its partial trajectory on disk.
    """

    if config.planning_mode != "single_pass":
        raise SinglePassError(
            "single_pass_planning_mode_required",
            f"run_single_pass requires planning_mode='single_pass', got "
            f"{config.planning_mode!r}; use rh.run_planning to dispatch by mode",
        )
    if config.horizon < config.steps:
        raise SinglePassError(
            "single_pass_horizon_too_short",
            f"planning_mode='single_pass' requires horizon >= steps: one WS3 solve "
            f"must cover every implemented period (horizon={config.horizon}, "
            f"steps={config.steps})",
        )

    started_at = datetime.now(UTC)
    wall_started = time.monotonic()
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    run_slug = safe_slug(config.run_id)
    steps_path = layout.data_path(f"{run_slug}-steps", ext="jsonl")
    final_state_path = layout.data_path(f"{run_slug}-final-state", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-rh-manifest")
    schedule_path = layout.data_path(f"{run_slug}-schedule", ext="csv")
    offers_path = layout.data_path(f"{run_slug}-offers", ext="parquet")
    decisions_path = layout.data_path(f"{run_slug}-decisions", ext="parquet")
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

    # Boundary parse, once per run: the same static inputs and cohort ARE
    # section the rolling-horizon engine reads (see rh.run_rh).
    economics = config.economics()
    stands = principal._read_table(config.stands_path, "rh_stands_missing")
    principal_economics = principal._development_type_economics(
        stands,
        green_prices=economics.green_prices,
        burned_price_discount=economics.burned_price_discount,
    )
    agent_economics = agent._development_type_economics(
        stands,
        green_prices=economics.green_prices,
        burned_price_discount=economics.burned_price_discount,
    )
    curves = principal._yield_curves(Path(config.yields_path))
    age_caps = rh.curve_age_caps(
        curves, width=config.age_smashing.width, midpoint=config.age_smashing.midpoint
    )

    state = rh.read_cohort_table(Path(bridge) / f"{ws3.BRIDGE_FILE_PREFIX}.are")
    model = ws3.load_full_model(ws3_config, verbose=verbose)
    horizon = config.steps * config.period_length

    try:
        # Canonicalize the in-memory state through its boundary artifact, then
        # run the single t=0 WS3 solve (D1: the same problem RH solves at
        # step 1) and map its implemented periods onto the t=0 cohorts.
        are_path = rh.write_cohort_table(state, state_dir / "step_00.are")
        state = rh.read_cohort_table(are_path)
        rh.inject_cohort_table(model, state)

        ws3_build_started = time.monotonic()
        problem = ws3._build_problem(model, ws3_config, verbose=verbose)
        ws3_build_seconds = time.monotonic() - ws3_build_started
        ws3_solve_started = time.monotonic()
        try:
            problem.solve(verbose=verbose)
        except Exception as exc:
            raise ws3.WS3Error(
                "ws3_solve_failed", f"single-pass HiGHS solve raised {type(exc).__name__}: {exc}"
            ) from exc
        ws3_solve_seconds = time.monotonic() - ws3_solve_started
        ws3_status = ws3.normalize_status(problem.status())
        if ws3_status != "optimal":
            raise ws3.WS3Error(
                "ws3_solve_not_optimal",
                f"single-pass WS3 solve did not reach optimal status: {ws3_status}",
            )
        schedule, ws3_objective = ws3._compile_schedule(model, problem, ws3_config)
        schedule.to_csv(schedule_path, index=False)
        period_volumes = rh.schedule_cohort_period_volumes(
            schedule,
            ws3_config.objective.action_code,
            state,
            implemented_periods=config.steps,
            period_length=config.period_length,
            age_smashing=config.age_smashing,
        )
        ceilings = rh.annual_ceiling_matrix(period_volumes, config.period_length)
        curve_ages = _cohort_curve_ages(state)

        # Principal LP over the whole run: per-year WS3 ceilings, decadal
        # growth surface, grade clock period_length (D5), burned-stock
        # semantics per config (D4/D6).
        cohorts_p = principal._parse_are_cohorts(
            are_path,
            principal_economics,
            curves,
            economics=economics,
            burn_rate_multiplier=config.burn_rate_multiplier,
        )
        _require_cohort_alignment(state, cohorts_p)
        scales = principal.decadal_per_ha_volumes(
            curves, curve_ages, horizon=horizon, period_length=config.period_length
        )
        cohorts_p = [
            replace(cohort, decadal_volume_scale=scale)
            for cohort, scale in zip(cohorts_p, scales, strict=True)
        ]
        principal_result = principal.solve_principal(
            cohorts_p,
            horizon=horizon,
            aac_annual_m3=config.aac_annual_m3,
            decay_rate=config.decay_rate,
            burned_limit_annual_m3=config.burned_limit_annual_m3,
            cohort_ceilings_m3=[ceilings[cohort.cohort_id] for cohort in cohorts_p],
            period_length=config.period_length,
            grade_clock_period=config.period_length,
            burned_stock_reset=config.burned_stock_reset,
            run_id=f"{run_slug}-principal",
        )
        pd.DataFrame(
            [offer.model_dump() for offer in principal_result.offers]
        ).to_parquet(offers_path, index=False)

        # Agent LP against the run-level offers over the same horizon.
        cohorts_a = agent._parse_are_cohorts(
            are_path,
            agent_economics,
            curves,
            burn_rate_multiplier=config.burn_rate_multiplier,
        )
        _require_cohort_alignment(state, cohorts_a)
        cohorts_a = [
            replace(cohort, decadal_volume_scale=scale)
            for cohort, scale in zip(cohorts_a, scales, strict=True)
        ]
        offers = agent.resolve_offers(
            cohorts_a, horizon=horizon, offers_path=offers_path
        )
        agent_result = agent.solve_agent(
            cohorts_a,
            offers,
            horizon=horizon,
            decay_rate=config.decay_rate,
            discount_rate=config.discount_rate,
            economics=economics,
            period_length=config.period_length,
            grade_clock_period=config.period_length,
            burned_stock_reset=config.burned_stock_reset,
            run_id=f"{run_slug}-agent",
        )
        pd.DataFrame(
            [decision.model_dump() for decision in agent_result.decisions]
        ).to_parquet(decisions_path, index=False)

        # 100-year lineage replay in decade blocks; every boundary snapshot is
        # written as an ARE section (step_00 came from the boundary parse).
        harvests, salvages = rh._fraction_schedules(state, agent_result, horizon)
        replay = replay_lineage(
            state,
            harvests=harvests,
            salvages=salvages,
            steps=config.steps,
            period_length=config.period_length,
            decay_rate=config.decay_rate,
            age_caps=age_caps,
            regeneration_age=config.age_smashing.midpoint,
            burn_rate_multiplier=config.burn_rate_multiplier,
            burned_stock_reset=config.burned_stock_reset,
        )
        for boundary, snapshot in enumerate(replay.snapshots[1:], start=1):
            rh.write_cohort_table(snapshot, state_dir / f"step_{boundary:02d}.are")
    except SinglePassError:
        raise
    except (
        rh.RHError,
        ws3.WS3Error,
        principal.PrincipalError,
        agent.AgentError,
        fire.FireDynamicsError,
    ) as exc:
        raise SinglePassError(
            "single_pass_failed",
            f"single-pass run failed: "
            f"[{getattr(exc, 'code', type(exc).__name__)}] {exc}",
        ) from exc

    final_state = replay.snapshots[-1]
    final_state.to_csv(final_state_path, index=False)
    wall_seconds = time.monotonic() - wall_started
    final_age_distribution = {
        str(int(age)): float(area)
        for age, area in final_state.groupby("age")["area_ha"].sum().items()
    }

    step_records = _decade_records(
        config,
        principal_result=principal_result,
        agent_result=agent_result,
        replay=replay,
        ws3_status=ws3_status,
        ws3_objective=ws3_objective,
        ws3_build_seconds=ws3_build_seconds,
        ws3_solve_seconds=ws3_solve_seconds,
    )
    with steps_path.open("w", encoding="utf-8") as steps_file:
        for record in step_records:
            steps_file.write(record.model_dump_json() + "\n")
            steps_file.flush()

    status = (
        "optimal"
        if all(
            record.ws3_status is None or record.ws3_status == "optimal"
            for record in step_records
        )
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
        planning_mode=config.planning_mode,
        steps=config.steps,
        horizon=config.horizon,
        period_length=config.period_length,
        cohorts=len(final_state),
        total_green_harvest_m3=sum(
            sum(record.annual_green_harvest_m3) for record in step_records
        ),
        total_burned_harvest_m3=sum(
            sum(record.annual_burned_harvest_m3) for record in step_records
        ),
        total_area_burned_ha=sum(record.area_burned_ha for record in step_records),
        total_principal_objective=sum(
            record.principal_objective_value for record in step_records
        ),
        total_agent_objective_base_year=sum(
            record.agent_objective_value_base_year
            for record in step_records
            if record.agent_objective_value_base_year is not None
        ),
        wall_seconds=wall_seconds,
        source_sha256=rh._source_checksums(config, bridge),
        step_records=step_records,
        config=config.model_dump(mode="json"),
    )
    manifest.write_json(manifest_path)

    return RHResult(
        run_id=config.run_id,
        status=status,
        planning_mode=config.planning_mode,
        steps=config.steps,
        horizon=config.horizon,
        period_length=config.period_length,
        cohorts=len(final_state),
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


def _decade_records(
    config: RHRunConfig,
    *,
    principal_result: PrincipalResult,
    agent_result: AgentResult,
    replay: LineageReplay,
    ws3_status: str,
    ws3_objective: float,
    ws3_build_seconds: float,
    ws3_solve_seconds: float,
) -> list[RHStepRecord]:
    """Split the one 100-year solve into one record per implemented decade.

    Decade ``k`` (0-based) takes its annual arrays from the agent result's
    ``k * period_length``-th slice, its ``area_burned_ha`` from the replay, and
    its objectives from the LP decompositions (D7). Every one-time cost (the
    WS3 solve, both LP solves) is attributed to decade 1; ``wall_seconds`` is
    each decade's replay time. The WS3 fields of decades after the first stay
    unset (D8), so their ``None``s never mix into a summed WS3 total.
    """

    period_length = config.period_length
    discount_factor = 1.0 + config.discount_rate
    if len(principal_result.decadal_objective_values) != config.steps:
        raise SinglePassError(
            "single_pass_objective_decomposition_missing",
            f"the principal result carries "
            f"{len(principal_result.decadal_objective_values)} decade values for "
            f"{config.steps} steps; the single-pass solve must be given "
            "period_length",
        )
    if len(agent_result.decadal_objective_values) != config.steps:
        raise SinglePassError(
            "single_pass_objective_decomposition_missing",
            f"the agent result carries {len(agent_result.decadal_objective_values)} "
            f"decade values for {config.steps} steps; the single-pass solve must be "
            "given period_length",
        )
    records: list[RHStepRecord] = []
    for decade in range(config.steps):
        year_start = decade * period_length
        annual_volumes = agent_result.per_year_volumes[
            year_start : year_start + period_length
        ]
        agent_decade_value = agent_result.decadal_objective_values[decade]
        records.append(
            RHStepRecord(
                step=decade + 1,
                start_year=config.base_year + year_start,
                ws3_status=ws3_status if decade == 0 else None,
                ws3_objective_value=ws3_objective if decade == 0 else None,
                ws3_build_seconds=ws3_build_seconds if decade == 0 else 0.0,
                ws3_solve_seconds=ws3_solve_seconds if decade == 0 else 0.0,
                principal_objective_value=principal_result.decadal_objective_values[
                    decade
                ],
                principal_objective_value_base_year=(
                    principal_result.decadal_objective_values[decade]
                ),
                principal_solve_seconds=(
                    principal_result.solve_seconds if decade == 0 else 0.0
                ),
                agent_objective_value=agent_decade_value,
                agent_objective_value_base_year=(
                    agent_decade_value * discount_factor ** (-period_length * decade)
                ),
                agent_solve_seconds=agent_result.solve_seconds if decade == 0 else 0.0,
                annual_green_harvest_m3=[
                    volumes.harvest_volume_m3 for volumes in annual_volumes
                ],
                annual_burned_harvest_m3=[
                    volumes.salvage_volume_m3 for volumes in annual_volumes
                ],
                area_burned_ha=replay.area_burned_ha[decade],
                wall_seconds=replay.decade_wall_seconds[decade],
            )
        )
    return records


def _cohort_curve_ages(state: pd.DataFrame) -> list[tuple[int, int]]:
    """Return each state row's ``(curve_id, age)`` in row order.

    The decadal growth surface is built per state row; the LP cohort parsers
    preserve ARE file order, and ``rh.write_cohort_table`` wrote exactly the
    state rows, so the two sequences align (checked by
    :func:`_require_cohort_alignment`).
    """

    return [
        (int(row.curve_id), int(row.age)) for row in state.itertuples(index=False)
    ]


def _require_cohort_alignment(
    state: pd.DataFrame, cohorts: list[object]
) -> None:
    """Fail fast unless parsed LP cohorts align 1:1 with the state rows.

    ``principal._parse_are_cohorts``/``agent._parse_are_cohorts`` return the ARE
    file's rows in order; the decadal growth surface is positional, so any
    misalignment would silently misprice cohorts.
    """

    if len(cohorts) != len(state):
        raise SinglePassError(
            "single_pass_cohort_alignment",
            f"parsed {len(cohorts)} LP cohorts from {len(state)} state rows",
        )
    for row, cohort in zip(state.itertuples(index=False), cohorts, strict=True):
        expected = rh.cohort_record_id(
            row.ifm, row.au_id, row.stratum_code, int(row.curve_id), int(row.age)
        )
        if cohort.cohort_id != expected:
            raise SinglePassError(
                "single_pass_cohort_alignment",
                f"LP cohort {cohort.cohort_id!r} does not align with state row "
                f"{expected!r}; the decadal growth surface is positional",
            )


def _replay_lineage_from_state_row(
    row: object, state_index: int, burn_rate_multiplier: float
) -> _ReplayLineage:
    """Parse one t=0 state row into its replay lineage (burn rate included)."""

    cohort_id = rh.cohort_record_id(
        row.ifm, row.au_id, row.stratum_code, int(row.curve_id), int(row.age)
    )
    try:
        burn_rate = fire.annual_burn_rate_for_stratum(row.stratum_code)
    except fire.UnknownBurnRateError as exc:
        raise SinglePassError(
            "single_pass_burn_rate_unknown",
            f"cohort {cohort_id!r} has no MFRI fire-rate entry: {exc}",
        ) from exc
    burn_rate *= burn_rate_multiplier
    if burn_rate > 1.0:
        raise SinglePassError(
            "single_pass_burn_rate_invalid",
            f"cohort {cohort_id!r} annual burn rate {burn_rate} (multiplier "
            f"{burn_rate_multiplier}) exceeds 1.0",
        )
    return _ReplayLineage(
        tsa=row.tsa,
        ifm=row.ifm,
        au_id=row.au_id,
        stratum_code=row.stratum_code,
        curve_id=int(row.curve_id),
        age=int(row.age),
        reference_area_ha=float(row.area_ha),
        live_before=1.0,
        burned_before=0.0,
        burn_rate=burn_rate,
        state_index=state_index,
    )


def _decade_schedules(
    lineage: _ReplayLineage,
    harvests: list[tuple[float, ...]],
    salvages: list[tuple[float, ...]],
    year_start: int,
    year_stop: int,
    period_length: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return one lineage's harvest/salvage slice of the current decade.

    Regen sub-cohorts are never harvested (D3), so they carry a zero schedule.
    """

    if lineage.state_index is None:
        return ((0.0,) * period_length, (0.0,) * period_length)
    return (
        harvests[lineage.state_index][year_start:year_stop],
        salvages[lineage.state_index][year_start:year_stop],
    )


def _simulate_decade(
    lineage: _ReplayLineage,
    harvest: tuple[float, ...],
    salvage: tuple[float, ...],
    decay_rate: float,
) -> list[fire.FireYearState]:
    """Run one decade of the replayed fire dynamics for one lineage."""

    try:
        return fire.simulate_cohort_years(
            initial_live=lineage.live_before,
            initial_burned=lineage.burned_before,
            burn_rate=lineage.burn_rate,
            harvest_schedule=harvest,
            salvage_schedule=salvage,
            decay_rate=decay_rate,
        )
    except fire.FireDynamicsError as exc:
        raise SinglePassError(
            "single_pass_fire_simulation_failed",
            f"cohort {_lineage_id(lineage)!r} fire replay failed: {exc}",
        ) from exc


def _replay_state_table(lineages: list[_ReplayLineage]) -> pd.DataFrame:
    """Aggregate the surviving lineages into the trusted cohort state table.

    A lineage's live area at a boundary is ``reference_area_ha * live_before``
    (its full formation area for a fresh regen sub-cohort, its decaying live
    share for anything older).
    """

    if not lineages:
        raise SinglePassError(
            "single_pass_state_empty_after_decade",
            "the replay left no positive-area cohorts",
        )
    rows = [
        (
            lineage.tsa,
            lineage.ifm,
            lineage.au_id,
            lineage.stratum_code,
            lineage.curve_id,
            lineage.age,
            lineage.reference_area_ha * lineage.live_before,
        )
        for lineage in lineages
    ]
    return (
        pd.DataFrame(rows, columns=pd.Index(list(rh.COHORT_COLUMNS)))
        .groupby(list(rh.STATE_KEY_COLUMNS), as_index=False, sort=True)["area_ha"]
        .sum()
        .loc[:, list(rh.COHORT_COLUMNS)]
    )


def _lineage_id(lineage: object) -> str:
    """Return a lineage's ``ifm:au_id:stratum:curve:age`` identity string."""

    return rh.cohort_record_id(
        lineage.ifm, lineage.au_id, lineage.stratum_code, lineage.curve_id, lineage.age
    )
