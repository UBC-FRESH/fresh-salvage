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
   survivors and regenerating everything that leaves the live pool. The
   replay tracks the live trajectory only: salvage is reported straight from
   the LP schedule (``area_salvaged_ha``) and its feasibility is enforced by
   the agent LP's own rows, because the LP re-expresses its carried burned
   stock in each decade's per-ha volume units (``v_k / v_{k+1}``,
   :func:`fresh_salvage.agent._decade_boundary_ratio`) while the replay has
   no burned-unit machinery to match that gate.

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
curve age cap) and stay exposed to fire; their own burned area regenerates
again at the next boundary. Surviving live t=0 area ages by
``+period_length`` clamped to the cap. Area is conserved to 1e-6 relative at
every boundary, and ``area_burned_ha`` per decade sums ``area * burn_influx``
over t=0 cohorts and regen sub-cohorts. This mirrors RH's regeneration
convention (the replay decides the reported state); the WS3 t=0 solve uses its
own convention (``*ACTION cc Y`` -> clear-cut target age 0), which only matters
after period ``steps``.

Regenerated-cohort harvests (extends D3)
----------------------------------------
The D3 contract excluded regenerated stands from the LP cohort set, and the
strict :func:`fresh_salvage.rh.schedule_cohort_period_volumes` still does.
Single pass now uses :func:`fresh_salvage.rh.schedule_period_volumes`, which
also returns one explicit :class:`~fresh_salvage.rh.RegeneratedCohortSpec` per
WS3 ``(dtype_key, period, age)`` regeneration-lattice group. Each spec carries
the group's area and raw scheduled volume (the same pre-utilization ``totvol``
basis as the initial-lineage ceilings), its birth period
``b = period - age / L``, and an availability year ``(period - 1) * L``. The
LP cohort uses that scheduled volume as its standing basis, no burned volume
and no burn rate (it represents the scheduled green harvest only), and a
constant decadal per-ha scale; its per-year ceiling matrix is zero outside the
scheduled period, so the principal offer and the agent H/S variables are zero
before and after it. The cohort cannot shift the regen harvest outside the
WS3 schedule.

The replay links the cohort to the existing lineage instead of adding area at
birth: in the scheduled decade its agent H fractions (times its scheduled
area) are allocated across every active descendant of the same dtype and
origin birth period in proportion to live area, and applied as a harvest event
on those lineages. The event removes area from the live pool and regenerates
it at the boundary, so the parent area is never double-counted and area
conservation is unchanged. A claim larger than the matching live pool fails
fast with ``single_pass_regen_area_unavailable`` instead of being capped or
dropped.

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

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from fresh_salvage import agent, fire, principal, rh, ws3
from fresh_salvage.models import (
    AgentResult,
    ArtifactLayout,
    Diagnostic,
    Economics,
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
    regen sub-cohorts, which carry no t=0 schedule). ``elapsed_years`` counts the
    years since formation at the start of the current decade; it indexes the
    memoized zero-harvest trajectory of a regen sub-cohort.

    ``origin_dtype``/``birth_period`` identify the regeneration pool a lineage
    descends from: the initial-lineage dtype and the **last clear-cut period**
    that formed it. ``birth_period`` survives fire-driven re-regeneration
    (the stand is still the same cohort since that clear-cut) but resets to
    the current period when an explicit regen event harvests the lineage (a
    new stand). An explicit regenerated cohort therefore reclaims its
    scheduled area from the whole descendant pool of its own clear-cut
    without double-counting area. ``harvested`` marks a lineage that an
    explicit regen event has already touched; its carried state no longer
    matches the fresh zero-harvest memo, so it is simulated directly.

    ``burned_before`` is carried and updated for parity with the LP's boundary
    semantics but never enters a reported output (salvage is not replayed; see
    :func:`replay_lineage`).
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
    elapsed_years: int = 0
    origin_dtype: tuple[str, str, str, str, int] | None = None
    birth_period: int | None = None
    harvested: bool = False


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
    regenerated_cohorts: Sequence[rh.RegeneratedCohortSpec] = (),
    regenerated_harvests: Mapping[str, tuple[float, ...]] | None = None,
) -> LineageReplay:
    """Replay ``steps * period_length`` years in decade blocks from a t=0 state.

    ``harvests``/``salvages`` carry one tuple of ``steps * period_length``
    annual fractions per state row (agent H/S fractions of the t=0 standing
    volume, the same shape :func:`rh.advance_cohort_table` consumes per step).
    Every decade runs each active t=0 lineage through
    :func:`fresh_salvage.fire.simulate_cohort_years` from its carried live
    inventory and harvest schedule. **Salvage is not replayed.** The agent LP
    owns salvage feasibility — under decadal growth it carries the burned
    inventory in the next decade's per-ha volume units
    (:func:`fresh_salvage.agent._decade_boundary_ratio`, ``v_k / v_{k+1}``) —
    and every replayed output is salvage-independent: the live balance and
    ``burn_influx`` take no salvage input
    (:func:`fresh_salvage.fire.simulate_cohort_years`), and
    ``area_salvaged_ha`` is summed from the raw LP schedule below. Feeding
    the LP's raw salvage fractions into the simulation against the replay's
    t=0-fraction burned carry could reject a schedule the LP proved feasible,
    so the replay leaves salvage to the LP and reports it directly. Regen
    sub-cohorts carry no t=0 harvest schedule, so one memoized fresh
    zero-harvest trajectory per burn rate supplies their decade slices with
    identical floats (:func:`_zero_harvest_decade`). ``burned_stock_reset``
    still zeroes the carried burned inventory at every decade start; that carry
    never enters a reported output either way.

    ``regenerated_cohorts``/``regenerated_harvests`` represent WS3 harvests of
    stands that regenerated inside the implemented horizon. Each spec is a
    claim on the regen pool created by removals in its birth period: in the
    spec's scheduled decade, the agent H fractions (of the spec's scheduled
    area) are applied as a harvest event to every active descendant lineage
    with the same dtype and birth (last clear-cut) period, allocated by live
    area. This is the "event on the existing lineage" linkage: no regen area
    is added at birth, so the parent area is never double-counted. The event
    removes area from the live pool and regenerates it at the same boundary,
    exactly like a t=0 harvest. A spec whose claim exceeds the matching live
    pool fails fast with ``single_pass_regen_area_unavailable`` rather than
    being dropped or capped.

    Raises ``SinglePassError`` on shape mismatches, unknown burn rates, missing
    age caps, a harvest that overruns the replayed live volume, an unsatisfied
    regenerated-cohort claim, or an area-conservation violation; nothing is
    written to disk.
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
    regen_specs = tuple(regenerated_cohorts)
    regen_schedules = dict(regenerated_harvests or {})
    spec_ids = {spec.cohort_id for spec in regen_specs}
    unknown_schedules = sorted(set(regen_schedules) - spec_ids)
    if unknown_schedules:
        raise SinglePassError(
            "single_pass_regen_schedule_unknown",
            f"regenerated harvest schedules reference cohorts with no spec: "
            f"{unknown_schedules[:5]}",
        )
    seen_claims: set[tuple[tuple[str, str, str, str, int], int, int]] = set()
    for spec in regen_specs:
        if spec.period < 1 or spec.period > steps:
            raise SinglePassError(
                "single_pass_regen_period_out_of_range",
                f"regenerated cohort {spec.cohort_id!r} schedules period "
                f"{spec.period} outside the implemented 1..{steps}",
            )
        if spec.birth_period < 1 or spec.birth_period >= spec.period:
            raise SinglePassError(
                "single_pass_regen_birth_invalid",
                f"regenerated cohort {spec.cohort_id!r} has birth period "
                f"{spec.birth_period} for scheduled period {spec.period}",
            )
        if not math.isfinite(spec.area_ha) or spec.area_ha <= 0.0:
            raise SinglePassError(
                "single_pass_regen_area_invalid",
                f"regenerated cohort {spec.cohort_id!r} carries invalid area "
                f"{spec.area_ha!r}",
            )
        if not math.isfinite(spec.volume_m3) or spec.volume_m3 < 0.0:
            raise SinglePassError(
                "single_pass_regen_volume_invalid",
                f"regenerated cohort {spec.cohort_id!r} carries invalid volume "
                f"{spec.volume_m3!r}",
            )
        schedule = regen_schedules.get(spec.cohort_id)
        if schedule is None:
            raise SinglePassError(
                "single_pass_regen_schedule_missing",
                f"regenerated cohort {spec.cohort_id!r} has no agent harvest "
                "schedule",
            )
        if len(schedule) != horizon:
            raise SinglePassError(
                "single_pass_regen_schedule_shape_mismatch",
                f"regenerated cohort {spec.cohort_id!r} carries {len(schedule)} "
                f"annual fractions but the replay covers {horizon} years",
            )
        claim = (
            (spec.tsa, spec.ifm, spec.au_id, spec.stratum_code, spec.curve_id),
            spec.birth_period,
            spec.period,
        )
        if claim in seen_claims:
            raise SinglePassError(
                "single_pass_regen_ambiguous_cohort",
                f"more than one regenerated cohort claims the same dtype/birth "
                f"period/scheduled period: {claim}",
            )
        seen_claims.add(claim)
    specs_by_period: dict[int, list[rh.RegeneratedCohortSpec]] = {}
    for spec in regen_specs:
        specs_by_period.setdefault(spec.period, []).append(spec)

    snapshots = [state]
    area_burned: list[float] = []
    area_harvested: list[float] = []
    area_salvaged: list[float] = []
    decade_wall: list[float] = []
    active = [
        _replay_lineage_from_state_row(row, index, burn_rate_multiplier)
        for index, row in enumerate(state.itertuples(index=False))
    ]
    # One fresh zero-harvest trajectory per distinct burn rate serves every
    # regen sub-cohort without an explicit event (it carries no t=0 schedule),
    # so its state is a pure continuation of the fresh formation state.
    zero_harvest: dict[float, tuple[fire.FireYearState, ...]] = {}
    for decade in range(steps):
        decade_started = time.monotonic()
        year_start = decade * period_length
        year_stop = year_start + period_length
        burned_decade = 0.0
        harvested_decade = 0.0
        salvaged_decade = 0.0
        regen_events = _regenerated_decade_events(
            active,
            specs_by_period.get(decade + 1, ()),
            regen_schedules,
            year_start=year_start,
            year_stop=year_stop,
        )
        next_active: list[_ReplayLineage] = []
        for index, lineage in enumerate(active):
            cap = age_caps.get(lineage.curve_id)
            if cap is None:
                raise SinglePassError(
                    "single_pass_curve_age_cap_missing",
                    f"cohort {_lineage_id(lineage)!r} curve {lineage.curve_id} has "
                    "no yield-curve age cap",
                )
            event = regen_events.get(index)
            if lineage.state_index is None:
                if event is None and not lineage.harvested:
                    years: tuple[fire.FireYearState, ...] | list[fire.FireYearState] = (
                        _zero_harvest_decade(
                            lineage,
                            zero_harvest,
                            horizon=horizon,
                            period_length=period_length,
                            decay_rate=decay_rate,
                        )
                    )
                    # A regen sub-cohort with no event carries no harvest.
                    harvest = salvage = ()
                else:
                    harvest = event if event is not None else (0.0,) * period_length
                    salvage = ()
                    years = _simulate_decade(lineage, harvest, decay_rate)
            else:
                harvest, salvage = _decade_schedules(
                    lineage, harvests, salvages, year_start, year_stop, period_length
                )
                years = _simulate_decade(lineage, harvest, decay_rate)
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
                        # Inert burned carry: salvage is not replayed (the LP
                        # owns it); the reset still zeroes it for LP parity.
                        burned_before=(
                            0.0 if burned_stock_reset else years[-1].burned_after
                        ),
                        elapsed_years=lineage.elapsed_years + period_length,
                        harvested=lineage.harvested or event is not None,
                    )
                )
            regen_area = lineage.reference_area_ha * removed
            if regen_area > 0.0:
                origin_dtype = lineage.origin_dtype
                if origin_dtype is None:
                    origin_dtype = (
                        lineage.tsa,
                        lineage.ifm,
                        lineage.au_id,
                        lineage.stratum_code,
                        lineage.curve_id,
                    )
                # A t=0 clear-cut or an explicit regen harvest starts a new
                # stand (birth = this period). Fire-only removal of a regen
                # lineage keeps the stand's last clear-cut period.
                if lineage.state_index is not None or event is not None:
                    birth_period = decade + 1
                else:
                    birth_period = lineage.birth_period
                next_active.append(
                    replace(
                        lineage,
                        age=regeneration_age,
                        reference_area_ha=regen_area,
                        live_before=1.0,
                        burned_before=0.0,
                        state_index=None,
                        elapsed_years=0,
                        origin_dtype=origin_dtype,
                        birth_period=birth_period,
                        harvested=False,
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
    dispatcher routes by it) and ``horizon >= steps``.

    Failure contract: the input loaders that run before the staged section
    (economics assembly, stands/yields reads, bridge resolution,
    ``ws3.load_full_model``) raise their own module errors unwrapped
    (:class:`fresh_salvage.rh.RHError`, :class:`fresh_salvage.ws3.WS3Error`,
    :class:`fresh_salvage.principal.PrincipalError`,
    :class:`fresh_salvage.agent.AgentError`). Everything from the boundary
    write onward — the single WS3 solve, the schedule mapping, both LPs, and
    the lineage replay — reports as ``SinglePassError``: layer failures wrap
    into ``single_pass_failed`` with the inner code quoted, and the run-level
    guards raise their own ``single_pass_*`` codes. Decade records are flushed
    to ``steps_path`` as they are emitted, so a failed run still leaves its
    partial trajectory on disk.
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
        mapping = rh.schedule_period_volumes(
            schedule,
            ws3_config.objective.action_code,
            state,
            implemented_periods=config.steps,
            period_length=config.period_length,
            age_smashing=config.age_smashing,
        )
        period_volumes = dict(mapping.initial)
        for spec in mapping.regenerated:
            period_volumes[spec.cohort_id] = spec.period_volumes
        ceilings = rh.annual_ceiling_matrix(period_volumes, config.period_length)
        curve_ages = _cohort_curve_ages(state)
        regenerated = mapping.regenerated
        regenerated_p = _regenerated_principal_cohorts(
            regenerated,
            economics=economics,
            price_by_dt=principal_economics,
            curves=curves,
            horizon=horizon,
            period_length=config.period_length,
        )
        regenerated_a = _regenerated_agent_cohorts(
            regenerated,
            price_by_dt=agent_economics,
            curves=curves,
            horizon=horizon,
            period_length=config.period_length,
        )

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
        ] + regenerated_p
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
        ] + regenerated_a
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
        harvests, salvages = rh._fraction_schedules(state, agent_result)
        regenerated_harvests = _regenerated_harvest_schedules(
            regenerated, agent_result, horizon
        )
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
            regenerated_cohorts=regenerated,
            regenerated_harvests=regenerated_harvests,
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
    diagnostics = _regenerated_diagnostics(regenerated)
    with steps_path.open("w", encoding="utf-8") as steps_file:
        for record in step_records:
            steps_file.write(record.model_dump_json() + "\n")
            steps_file.flush()

    # The run status is the single t=0 WS3 solve's status (the only solve
    # status the decade records carry; every later record leaves it unset,
    # and a non-optimal solve already raised above).
    status = ws3_status
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
        diagnostics=diagnostics,
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
        diagnostics=diagnostics,
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


def _regenerated_principal_cohorts(
    specs: Sequence[rh.RegeneratedCohortSpec],
    *,
    economics: Economics,
    price_by_dt: dict[str, dict[str, object]],
    curves: dict[int, tuple[object, object]],
    horizon: int,
    period_length: int,
) -> list[principal.PrincipalCohort]:
    """Build principal LP cohorts for the explicit regenerated harvests.

    Each cohort carries the WS3 row group's area and raw scheduled volume as
    its standing basis, a union-bound green stumpage cashflow, and **no burned
    volume or burn rate**: it represents the scheduled green harvest only.
    ``decadal_volume_scale`` is the row's scheduled per-ha volume in every
    decade. That keeps the LP's year-0 and later-decade coefficients identical
    to the scheduled volume and avoids a fictitious growth/decline boundary;
    the per-year ceiling matrix is zero outside the scheduled period, so the
    cohort cannot be offered before or after it.
    """

    cohorts: list[principal.PrincipalCohort] = []
    for spec in specs:
        development_type = _regenerated_development_type(
            spec, price_by_dt=price_by_dt, curves=curves
        )
        _require_regenerated_amounts(spec)
        per_ha = spec.volume_m3 / spec.area_ha
        scale = (per_ha,) * math.ceil(horizon / period_length)
        cohorts.append(
            principal.PrincipalCohort(
                cohort_id=spec.cohort_id,
                stratum_code=spec.stratum_code,
                development_type=development_type,
                area_ha=spec.area_ha,
                green_volume_m3=spec.volume_m3,
                burned_volume_m3=0.0,
                cashflow=spec.volume_m3 * economics.green_stumpage_rate,
                burned_value=0.0,
                burn_rate=0.0,
                burned_grade_mix=None,
                burned_grade_prices=None,
                green_stumpage_rate=economics.green_stumpage_rate,
                burned_stumpage_rate=economics.burned_stumpage_rate,
                subsidy_rate_per_m3=economics.subsidy_rate_per_m3,
                decadal_volume_scale=scale,
            )
        )
    return cohorts


def _regenerated_agent_cohorts(
    specs: Sequence[rh.RegeneratedCohortSpec],
    *,
    price_by_dt: dict[str, dict[str, object]],
    curves: dict[int, tuple[object, object]],
    horizon: int,
    period_length: int,
) -> list[agent.AgentCohort]:
    """Build agent LP cohorts for the explicit regenerated harvests.

    Mirrors :func:`_regenerated_principal_cohorts`: the scheduled row volume is
    the standing basis, the burn rate is zero (no burned inventory or salvage
    on the scheduled green block), and the constant decadal scale keeps the
    LP's per-year volume equal to the scheduled volume. Zero ceilings outside
    the scheduled period bound H/S to zero there.
    """

    cohorts: list[agent.AgentCohort] = []
    for spec in specs:
        development_type = _regenerated_development_type(
            spec, price_by_dt=price_by_dt, curves=curves
        )
        _require_regenerated_amounts(spec)
        per_ha = spec.volume_m3 / spec.area_ha
        scale = (per_ha,) * math.ceil(horizon / period_length)
        cohorts.append(
            agent.AgentCohort(
                cohort_id=spec.cohort_id,
                stratum_code=spec.stratum_code,
                development_type=development_type,
                area_ha=spec.area_ha,
                standing_volume_m3=spec.volume_m3,
                burn_rate=0.0,
                green_price_m3=float(price_by_dt[development_type]["green_price"]),
                burned_price_m3=float(price_by_dt[development_type]["burned_price"]),
                burned_grade_mix=None,
                burned_grade_prices=None,
                decadal_volume_scale=scale,
            )
        )
    return cohorts


def _regenerated_development_type(
    spec: rh.RegeneratedCohortSpec,
    *,
    price_by_dt: dict[str, dict[str, object]],
    curves: dict[int, tuple[object, object]],
) -> str:
    """Map a regen spec to its stands-table development type, failing fast."""

    if spec.curve_id not in curves:
        raise SinglePassError(
            "single_pass_regen_curve_missing",
            f"regenerated cohort {spec.cohort_id!r} references yield curve "
            f"{spec.curve_id}, which is absent from the yields table",
        )
    try:
        development_type = principal._development_type_from_stratum(
            spec.stratum_code
        )
    except fire.UnknownBurnRateError as exc:
        raise SinglePassError(
            "single_pass_regen_stratum_invalid",
            f"regenerated cohort {spec.cohort_id!r} carries malformed stratum "
            f"{spec.stratum_code!r}: {exc}",
        ) from exc
    if development_type not in price_by_dt:
        raise SinglePassError(
            "single_pass_regen_stratum_unmapped",
            f"regenerated cohort {spec.cohort_id!r} maps to development type "
            f"{development_type!r}, which has no stands-table economics",
        )
    return development_type


def _require_regenerated_amounts(spec: rh.RegeneratedCohortSpec) -> None:
    """Fail fast on a regen spec without a usable area/volume basis."""

    if not math.isfinite(spec.area_ha) or spec.area_ha <= 0.0:
        raise SinglePassError(
            "single_pass_regen_area_invalid",
            f"regenerated cohort {spec.cohort_id!r} carries invalid area "
            f"{spec.area_ha!r}",
        )
    if not math.isfinite(spec.volume_m3) or spec.volume_m3 < 0.0:
        raise SinglePassError(
            "single_pass_regen_volume_invalid",
            f"regenerated cohort {spec.cohort_id!r} carries invalid volume "
            f"{spec.volume_m3!r}",
        )


def _regenerated_harvest_schedules(
    specs: Sequence[rh.RegeneratedCohortSpec],
    result: AgentResult,
    horizon: int,
) -> dict[str, tuple[float, ...]]:
    """Align the agent H fractions of every regenerated cohort by year."""

    harvest_by_id: dict[str, list[float]] = {}
    last_year_by_id: dict[str, int] = {}
    for decision in result.decisions:
        last_year = last_year_by_id.get(decision.cohort_id)
        if last_year is not None and decision.year < last_year:
            raise SinglePassError(
                "single_pass_regen_decision_order_invalid",
                f"agent decisions for regenerated cohort "
                f"{decision.cohort_id!r} are not year-ascending (year "
                f"{decision.year} follows year {last_year})",
            )
        last_year_by_id[decision.cohort_id] = decision.year
        harvest_by_id.setdefault(decision.cohort_id, []).append(
            decision.harvest_fraction
        )
    schedules: dict[str, tuple[float, ...]] = {}
    for spec in specs:
        values = harvest_by_id.get(spec.cohort_id)
        if values is None:
            raise SinglePassError(
                "single_pass_regen_schedule_missing",
                f"agent decisions do not cover regenerated cohort "
                f"{spec.cohort_id!r}",
            )
        if len(values) != horizon:
            raise SinglePassError(
                "single_pass_regen_schedule_shape_mismatch",
                f"agent decisions for regenerated cohort {spec.cohort_id!r} "
                f"cover {len(values)} years but the run horizon is {horizon}",
            )
        schedules[spec.cohort_id] = tuple(values)
    return schedules


def _regenerated_diagnostics(
    specs: Sequence[rh.RegeneratedCohortSpec],
) -> list[Diagnostic]:
    """Return the manifest diagnostic recording the explicit regen cohorts."""

    if not specs:
        return []
    return [
        Diagnostic(
            severity="info",
            code="single_pass_regenerated_cohorts",
            message=(
                f"{len(specs)} explicit regenerated cohort(s) constructed from "
                "WS3 regeneration-lattice harvest rows; replayed as harvest "
                "events on the matching regen lineage pool"
            ),
            context={
                "cohorts": len(specs),
                "cohort_ids": [spec.cohort_id for spec in specs],
                "area_ha": sum(spec.area_ha for spec in specs),
                "scheduled_volume_m3": sum(spec.volume_m3 for spec in specs),
                "periods": sorted({spec.period for spec in specs}),
                "birth_periods": sorted({spec.birth_period for spec in specs}),
                "volume_basis": (
                    "WS3 schedule raw totvol (pre-utilization), same basis as "
                    "the initial-lineage ceilings"
                ),
                "availability": (
                    "scheduled period only; the annual ceiling matrix is zero "
                    "outside it"
                ),
            },
        )
    ]


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


def _regenerated_decade_events(
    active: list[_ReplayLineage],
    specs: Sequence[rh.RegeneratedCohortSpec],
    schedules: Mapping[str, tuple[float, ...]],
    *,
    year_start: int,
    year_stop: int,
) -> dict[int, tuple[float, ...]]:
    """Return the per-lineage harvest fractions of this decade's regen claims.

    A spec's agent H fractions are fractions of its scheduled cohort area. Its
    claim is ``sum(H) * area_ha`` hectares, allocated across every active
    descendant lineage with the same dtype and birth (last clear-cut) period
    in proportion to live area. Each lineage receives annual harvest fractions
    of its own reference area, preserving the LP's within-decade timing. A
    claim larger than the matching live pool fails fast rather than being
    capped.
    """

    events: dict[int, tuple[float, ...]] = {}
    for spec in specs:
        schedule = schedules[spec.cohort_id]
        decade_fractions = schedule[year_start:year_stop]
        total_fraction = sum(decade_fractions)
        if total_fraction <= 0.0:
            continue
        if total_fraction > 1.0 + 1e-9:
            raise SinglePassError(
                "single_pass_regen_fraction_invalid",
                f"regenerated cohort {spec.cohort_id!r} harvests "
                f"{total_fraction} of its area in period {spec.period}; the "
                "sell-once bound allows at most 1.0",
            )
        target_area_ha = total_fraction * spec.area_ha
        if target_area_ha <= 0.0:
            continue
        origin_dtype = (
            spec.tsa,
            spec.ifm,
            spec.au_id,
            spec.stratum_code,
            spec.curve_id,
        )
        matches = [
            (index, lineage)
            for index, lineage in enumerate(active)
            if lineage.state_index is None
            and lineage.origin_dtype == origin_dtype
            and lineage.birth_period == spec.birth_period
        ]
        available_area_ha = sum(
            lineage.reference_area_ha * lineage.live_before
            for _index, lineage in matches
        )
        tolerance = max(
            1e-9, abs(available_area_ha) * rh.AREA_CONSERVATION_REL_TOLERANCE
        )
        if target_area_ha > available_area_ha + tolerance:
            raise SinglePassError(
                "single_pass_regen_area_unavailable",
                f"regenerated cohort {spec.cohort_id!r} schedules "
                f"{target_area_ha:.6f} ha at period {spec.period} but the "
                f"matching lineage pool (dtype {origin_dtype}, birth period "
                f"{spec.birth_period}) holds only {available_area_ha:.6f} ha",
            )
        if available_area_ha <= 0.0:
            continue
        for index, lineage in matches:
            live_area_ha = lineage.reference_area_ha * lineage.live_before
            allocated_area_ha = target_area_ha * live_area_ha / available_area_ha
            if allocated_area_ha <= 0.0:
                continue
            if lineage.reference_area_ha <= 0.0:
                raise SinglePassError(
                    "single_pass_regen_lineage_area_invalid",
                    f"regenerated cohort {spec.cohort_id!r} matched lineage "
                    f"{_lineage_id(lineage)!r} with non-positive reference area "
                    f"{lineage.reference_area_ha}",
                )
            annual = tuple(
                allocated_area_ha
                * (fraction / total_fraction)
                / lineage.reference_area_ha
                for fraction in decade_fractions
            )
            previous = events.get(index)
            events[index] = (
                annual
                if previous is None
                else tuple(
                    left + right
                    for left, right in zip(previous, annual, strict=True)
                )
            )
    return events


def _decade_schedules(
    lineage: _ReplayLineage,
    harvests: list[tuple[float, ...]],
    salvages: list[tuple[float, ...]],
    year_start: int,
    year_stop: int,
    period_length: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return one t=0 lineage's harvest/salvage slice of the current decade.

    Only t=0 cohorts carry a t=0 schedule; regen sub-cohorts use the regen
    event path or the memoized zero-harvest trajectory. Only the harvest slice
    drives the replay simulation (salvage is not replayed); the salvage slice
    is reporting-only (``area_salvaged_ha``).
    """

    return (
        harvests[lineage.state_index][year_start:year_stop],
        salvages[lineage.state_index][year_start:year_stop],
    )


def _zero_harvest_decade(
    lineage: _ReplayLineage,
    cache: dict[float, tuple[fire.FireYearState, ...]],
    *,
    horizon: int,
    period_length: int,
    decay_rate: float,
) -> tuple[fire.FireYearState, ...]:
    """Return a regen lineage's decade slice of its memoized trajectory.

    A regenerated lineage without an explicit regen event and without a prior
    harvest carries no harvest or salvage, so from its fresh formation state
    (``live=1``, ``burned=0``) its inventory follows one recurrence that
    depends only on the burn rate. A single fresh ``horizon``-year simulation
    per distinct burn rate therefore serves every such regen sub-cohort, and
    the lineage's decade slice is read at its ``elapsed_years`` offset. The
    trajectory applies the same year-by-year float operations
    ``simulate_cohort_years`` would run on the carried state, so the replay
    stays bit-identical to the per-decade simulation (the ``burned_before``
    carry is unused for these lineages: they salvage nothing, and
    ``burn_influx`` depends only on live volume).
    """

    trajectory = cache.get(lineage.burn_rate)
    if trajectory is None:
        no_schedule = (0.0,) * horizon
        trajectory = tuple(
            fire.simulate_cohort_years(
                initial_live=1.0,
                initial_burned=0.0,
                burn_rate=lineage.burn_rate,
                harvest_schedule=no_schedule,
                salvage_schedule=no_schedule,
                decay_rate=decay_rate,
            )
        )
        cache[lineage.burn_rate] = trajectory
    return trajectory[lineage.elapsed_years : lineage.elapsed_years + period_length]


def _simulate_decade(
    lineage: _ReplayLineage,
    harvest: tuple[float, ...],
    decay_rate: float,
) -> list[fire.FireYearState]:
    """Run one decade of the replayed live dynamics for one lineage.

    Salvage is deliberately NOT passed to the fire simulation. The agent LP
    owns salvage feasibility, and it carries the burned inventory in each
    decade's per-ha volume units (``v_k / v_{k+1}``); the lineage's
    t=0-fraction burned carry is not in those units, so replaying the raw LP
    salvage fractions against it could reject a schedule the LP proved
    feasible. Nothing reported depends on salvage: the live balance and
    ``burn_influx`` are salvage-free in
    :func:`fresh_salvage.fire.simulate_cohort_years`, and
    :func:`replay_lineage` sums ``area_salvaged_ha`` from the raw schedule.
    The burned carry keeps evolving for parity with the LP's boundary
    semantics but never enters a reported value.
    """

    try:
        return fire.simulate_cohort_years(
            initial_live=lineage.live_before,
            initial_burned=lineage.burned_before,
            burn_rate=lineage.burn_rate,
            harvest_schedule=harvest,
            salvage_schedule=(0.0,) * len(harvest),
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
