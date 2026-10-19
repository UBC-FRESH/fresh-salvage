"""Single-pass planning tests (P8.4).

The pure tests cover the lineage replay (assembly, conservation, caps,
burned-inventory invariance), the decade objective decompositions (including
the principal's constant offset), the decade record slicing, the config
surface, and the dispatcher wiring. The oracle tests run the single-pass and
rolling-horizon engines with ``steps=1`` on identical inputs and compare the
decade-1 record plus the final state: a ws3-free variant monkeypatches the WS3
stage with a synthetic schedule (CI-safe) and a gated variant uses the real
TSA29 bridge, stands, and yields (skips when unavailable).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from fresh_salvage import (
    agent,
    ensemble,
    fire,
    principal,
    rh,
    sensitivity,
    single_pass,
    ws3,
)
from fresh_salvage.cli import app
from fresh_salvage.data import BURNED_GRADE_COLUMNS, GRADE_COLUMNS
from fresh_salvage.models import (
    AgentResult,
    AgentYearVolumes,
    AgeSmashing,
    PrincipalResult,
    RHManifest,
    RHResult,
    RHRunConfig,
    RHStepRecord,
)

runner = CliRunner()

STRATUM = "sbps_pli"
CURVE_ID = 2921000
DTYPE = ("29", "managed", "2901000", STRATUM, str(CURVE_ID))
DTYPE_KEY = json.dumps(list(DTYPE))
BURN_RATE = 1.0 / 100.0
SMASHING = AgeSmashing(enabled=True, width=10, midpoint=5)
TIMING_FIELDS = {
    "ws3_build_seconds",
    "ws3_solve_seconds",
    "principal_solve_seconds",
    "agent_solve_seconds",
    "wall_seconds",
}
# Fields the single-pass record adds beyond the rolling-horizon schema.
BASE_YEAR_FIELDS = {
    "principal_objective_value_base_year",
    "agent_objective_value_base_year",
}


# --- fixtures ---------------------------------------------------------------


def _state(rows: list[tuple[str, str, str, str, int, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=pd.Index(list(rh.COHORT_COLUMNS)))


def _row(age: int, area: float, stratum: str = STRATUM) -> tuple:
    return ("29", "managed", "2901000", stratum, CURVE_ID, age, area)


def _curves(max_age: int = 200) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Two-point yield curve: 0 m3/ha at age 0, 150 m3/ha at ``max_age``."""

    return {
        CURVE_ID: (
            np.array([0.0, float(max_age)]),
            np.array([0.0, 150.0]),
        )
    }


def _age_caps(max_age: int = 200) -> dict[int, int]:
    return rh.curve_age_caps(_curves(max_age), width=10, midpoint=5)


def _schedules(
    rows: int, steps: int, period_length: int, harvest: float = 0.0, salvage: float = 0.0
) -> tuple[list[tuple[float, ...]], list[tuple[float, ...]]]:
    horizon = steps * period_length
    return (
        [tuple([harvest] * horizon) for _ in range(rows)],
        [tuple([salvage] * horizon) for _ in range(rows)],
    )


def _cohort(
    cohort_id: str = f"managed:2901000:{STRATUM}:{CURVE_ID}:65",
    green_volume: float = 10_000.0,
    burned_volume: float = 1_000.0,
    burn_rate: float = 0.01,
    cashflow: float = 10_000.0,
    burned_value: float = 1_000.0,
    **overrides: object,
) -> principal.PrincipalCohort:
    fields: dict[str, object] = {
        "cohort_id": cohort_id,
        "stratum_code": STRATUM,
        "development_type": "SPF_SBPS",
        "area_ha": 100.0,
        "green_volume_m3": green_volume,
        "burned_volume_m3": burned_volume,
        "cashflow": cashflow,
        "burned_value": burned_value,
        "burn_rate": burn_rate,
    }
    fields.update(overrides)
    return principal.PrincipalCohort(**fields)  # type: ignore[arg-type]


def _agent_cohort(
    cohort_id: str,
    standing_volume: float = 10_000.0,
    burn_rate: float = 0.01,
    **overrides: object,
) -> agent.AgentCohort:
    fields: dict[str, object] = {
        "cohort_id": cohort_id,
        "stratum_code": STRATUM,
        "development_type": "SPF_SBPS",
        "area_ha": 100.0,
        "standing_volume_m3": standing_volume,
        "burn_rate": burn_rate,
        "green_price_m3": 60.0,
        "burned_price_m3": 40.0,
    }
    fields.update(overrides)
    return agent.AgentCohort(**fields)  # type: ignore[arg-type]


# --- lineage replay ---------------------------------------------------------


def test_replay_conserves_area_and_regenerates_removed_area() -> None:
    state = _state([_row(65, 100.0), _row(145, 50.0)])
    harvests, salvages = _schedules(2, 2, 10, harvest=0.02)

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=2,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    assert len(replay.snapshots) == 3
    assert len(replay.area_burned_ha) == 2
    for before, after in zip(replay.snapshots[:-1], replay.snapshots[1:], strict=True):
        assert after["area_ha"].sum() == pytest.approx(
            before["area_ha"].sum(), rel=1e-9
        )
    # Independent replay of the first decade through the fire primitives: the
    # survivors age +10 and everything removed regenerates at midpoint 5.
    first_decade = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=harvests[0][:10],
        salvage_schedule=salvages[0][:10],
    )
    live_end = first_decade[-1].live_after
    first = replay.snapshots[1].set_index(["curve_id", "age"])["area_ha"]
    assert first.loc[(CURVE_ID, 75)] == pytest.approx(100.0 * live_end, rel=1e-12)
    assert first.loc[(CURVE_ID, 155)] == pytest.approx(50.0 * live_end, rel=1e-12)
    assert first.loc[(CURVE_ID, 5)] == pytest.approx(
        150.0 * (1.0 - live_end), rel=1e-12
    )
    assert replay.area_burned_ha[0] == pytest.approx(
        150.0 * sum(year.burn_influx for year in first_decade), rel=1e-12
    )


def test_replay_decade_burned_area_sums_influx() -> None:
    state = _state([_row(65, 100.0)])
    harvests, salvages = _schedules(1, 1, 10)

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=1,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    # Independent per-year replay through the fire primitives.
    years = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 10,
        salvage_schedule=(0.0,) * 10,
    )
    assert replay.area_burned_ha[0] == pytest.approx(
        100.0 * sum(year.burn_influx for year in years), rel=1e-12
    )


def test_replay_regen_subcohorts_age_and_stay_exposed_to_fire() -> None:
    # A fully harvested cohort regenerates its whole area at midpoint 5, which
    # then burns (and regenerates again) in the second decade.
    state = _state([_row(65, 100.0)])
    harvests = [(1.0,) + (0.0,) * 9 + (0.0,) * 10]
    salvages = [(0.0,) * 20]

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=2,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    first = replay.snapshots[1].set_index("age")["area_ha"]
    assert list(first.index) == [5]
    assert first.loc[5] == pytest.approx(100.0, rel=1e-9)
    second = replay.snapshots[2].set_index("age")["area_ha"]
    assert second.loc[15] == pytest.approx(100.0 * 0.99**10, rel=1e-9)
    assert second.loc[5] == pytest.approx(100.0 * (1.0 - 0.99**10), rel=1e-9)
    assert replay.area_burned_ha[1] == pytest.approx(
        100.0 * (1.0 - 0.99**10), rel=1e-9
    )
    assert replay.area_burned_ha[1] > replay.area_burned_ha[0]


def test_replay_ages_clamp_to_curve_cap() -> None:
    state = _state([_row(190, 40.0)])
    harvests, salvages = _schedules(1, 2, 10)

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=2,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),  # cap 195 for the synthetic 200-year curve
        regeneration_age=5,
    )

    ages = sorted(int(age) for age in replay.snapshots[2]["age"])
    # Both the t=0 survivor and the first decadal regen sub-cohort clamp to 195
    # by aging; the second decade adds its own age-5 regen sub-cohort.
    assert ages == [5, 15, 195]
    second_decade_regen = replay.snapshots[2].set_index("age")["area_ha"].loc[15]
    assert second_decade_regen > 0.0


def test_replay_records_harvest_and_salvage_areas() -> None:
    state = _state([_row(65, 100.0)])
    harvests, salvages = _schedules(1, 1, 10, harvest=0.05, salvage=0.001)

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=1,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    assert replay.area_harvested_ha[0] == pytest.approx(100.0 * 0.5, rel=1e-9)
    assert replay.area_salvaged_ha[0] == pytest.approx(100.0 * 0.01, rel=1e-9)


def test_replay_regen_decade_slices_match_carried_state_simulation() -> None:
    # The memoized zero-harvest trajectory (P8.5 perf change) must reproduce,
    # bit for bit, the per-decade simulation the carried-state replay would
    # run for a regen lineage: same floats, no numeric change.
    lineage = single_pass._ReplayLineage(
        tsa="29",
        ifm="managed",
        au_id="2901000",
        stratum_code=STRATUM,
        curve_id=CURVE_ID,
        age=5,
        reference_area_ha=100.0,
        live_before=0.0,  # overwritten below with the carried state
        burned_before=0.0,
        burn_rate=BURN_RATE,
        state_index=None,
        elapsed_years=10,
    )
    fresh = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 20,
        salvage_schedule=(0.0,) * 20,
        decay_rate=0.85,
    )
    carried = fire.simulate_cohort_years(
        initial_live=fresh[9].live_after,
        initial_burned=fresh[9].burned_after,
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 10,
        salvage_schedule=(0.0,) * 10,
        decay_rate=0.85,
    )
    lineage = replace(
        lineage, live_before=fresh[9].live_after, burned_before=fresh[9].burned_after
    )
    slice_ = single_pass._zero_harvest_decade(
        lineage, {}, horizon=20, period_length=10, decay_rate=0.85
    )

    assert [year.live_after for year in slice_] == [
        year.live_after for year in carried
    ]
    assert [year.burn_influx for year in slice_] == [
        year.burn_influx for year in carried
    ]
    assert [year.burned_after for year in slice_] == [
        year.burned_after for year in carried
    ]


def test_replay_burned_stock_reset_leaves_state_and_burned_area_unchanged() -> None:
    # Reset drops the carried burned inventory (LP semantics) and cannot change
    # the live trajectory, so the reported state and areas are identical.
    state = _state([_row(65, 100.0)])
    harvests, salvages = _schedules(1, 2, 10, harvest=0.02, salvage=0.001)
    kwargs = {
        "harvests": harvests,
        "salvages": salvages,
        "steps": 2,
        "period_length": 10,
        "decay_rate": 0.85,
        "age_caps": _age_caps(),
        "regeneration_age": 5,
    }

    carried = single_pass.replay_lineage(state, **kwargs)
    reset = single_pass.replay_lineage(state, burned_stock_reset=True, **kwargs)

    for carried_state, reset_state in zip(
        carried.snapshots, reset.snapshots, strict=True
    ):
        assert carried_state["area_ha"].tolist() == reset_state["area_ha"].tolist()
    assert carried.area_burned_ha == reset.area_burned_ha
    assert carried.area_salvaged_ha == reset.area_salvaged_ha


def test_replay_ignores_lp_boundary_salvage_gate_across_declining_growth() -> None:
    # Regression (review M2): under decadal growth the agent LP re-expresses
    # its carried burned inventory in the next decade's per-ha units
    # (v_k / v_{k+1}, agent._decade_boundary_ratio), so a decade-2 salvage
    # schedule may draw on more burned inventory than the replay's unconverted
    # t=0-fraction carry holds. The replay must not re-adjudicate a schedule
    # the LP proved feasible: it replays the live trajectory and reports the
    # raw salvage sums. Before the fix this schedule raised
    # single_pass_fire_simulation_failed.
    state = _state([_row(65, 100.0)])
    decade1 = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 10,
        salvage_schedule=(0.0,) * 10,
        decay_rate=0.85,
    )
    carried_burned = decade1[-1].burned_after
    influx = fire.burn_influx(decade1[-1].live_after, BURN_RATE)
    # Declining per-ha volume v_0 / v_1 = 2: the LP's decade-2 gate is
    # 2 * carried + influx, the old replay gate only carried + influx.
    salvage_2 = carried_burned + influx + 0.5 * carried_burned
    assert carried_burned + influx < salvage_2 <= 2.0 * carried_burned + influx
    harvests = [(0.0,) * 20]
    salvages = [(0.0,) * 10 + (salvage_2,) + (0.0,) * 9]

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=2,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    assert replay.area_salvaged_ha[0] == 0.0
    assert replay.area_salvaged_ha[1] == pytest.approx(100.0 * salvage_2, rel=1e-12)
    # The live trajectory and the burned-area reporting stay salvage-free:
    # decade 2 matches the independent carried-state simulation of the t=0
    # survivor plus the fresh regen trajectory of the decade-1 removed area.
    regen_area = 100.0 * (1.0 - decade1[-1].live_after)
    decade2 = fire.simulate_cohort_years(
        initial_live=decade1[-1].live_after,
        initial_burned=carried_burned,  # never enters burn_influx/live
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 10,
        salvage_schedule=(0.0,) * 10,
        decay_rate=0.85,
    )
    regen_decade2 = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=(0.0,) * 10,
        salvage_schedule=(0.0,) * 10,
        decay_rate=0.85,
    )
    assert replay.area_burned_ha[1] == pytest.approx(
        100.0 * sum(year.burn_influx for year in decade2)
        + regen_area * sum(year.burn_influx for year in regen_decade2),
        rel=1e-12,
    )


def test_replay_accepts_agent_lp_boundary_salvage_under_declining_growth() -> None:
    # The LP-driven variant of the M2 regression: an agent LP with declining
    # per-ha volume (v_0 / v_1 = 2) and a salvage window capped at the end of
    # decade 1 salvages at its boundary-rescaled gate in decade 2's first
    # year — more than the replay's unconverted carry holds. The replay must
    # complete on the LP's own optimal schedule (the pre-fix replay raised
    # single_pass_fire_simulation_failed on it).
    state = _state([_row(65, 100.0)])
    cohort = _agent_cohort(
        "managed:2901000:sbps_pli:2921000:65",
        standing_volume=20_000.0,  # v_0 = 200 m3/ha
        burn_rate=BURN_RATE,
        green_price_m3=10.0,  # negative green margin: no green harvest
        burned_price_m3=150.0,  # positive salvage margin
        decadal_volume_scale=(200.0, 100.0),
    )
    offers = [(1.0,) * 9 + (0.0,) + (1.0,) * 10]
    result = agent.solve_agent(
        [cohort],
        offers,
        horizon=20,
        decay_rate=0.85,
        discount_rate=0.03,
        period_length=10,
        grade_clock_period=10,
    )
    harvests, salvages = rh._fraction_schedules(state, result)
    salvage_year_11 = salvages[0][10]
    assert salvage_year_11 > 0.0

    decade1 = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE,
        harvest_schedule=harvests[0][:10],
        salvage_schedule=salvages[0][:10],
        decay_rate=0.85,
    )
    old_gate = decade1[-1].burned_after + fire.burn_influx(
        decade1[-1].live_after, BURN_RATE
    )
    assert salvage_year_11 > old_gate  # exactly what the pre-fix replay rejected

    replay = single_pass.replay_lineage(
        state,
        harvests=harvests,
        salvages=salvages,
        steps=2,
        period_length=10,
        decay_rate=0.85,
        age_caps=_age_caps(),
        regeneration_age=5,
    )

    assert replay.area_salvaged_ha[1] == pytest.approx(
        100.0 * sum(salvages[0][10:20]), rel=1e-12
    )


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"steps": 0, "period_length": 10}, "single_pass_invalid_shape"),
        ({"steps": 1, "period_length": 0}, "single_pass_invalid_shape"),
    ],
)
def test_replay_rejects_non_positive_shapes(kwargs: dict, code: str) -> None:
    state = _state([_row(65, 100.0)])
    harvests, salvages = _schedules(1, 1, 10)

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass.replay_lineage(
            state,
            harvests=harvests,
            salvages=salvages,
            decay_rate=0.85,
            age_caps=_age_caps(),
            regeneration_age=5,
            **kwargs,
        )

    assert excinfo.value.code == code


def test_replay_rejects_schedule_shape_mismatch() -> None:
    state = _state([_row(65, 100.0)])

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass.replay_lineage(
            state,
            harvests=[(0.0,) * 9],
            salvages=[(0.0,) * 10],
            steps=1,
            period_length=10,
            decay_rate=0.85,
            age_caps=_age_caps(),
            regeneration_age=5,
        )

    assert excinfo.value.code == "single_pass_schedule_shape_mismatch"


def test_replay_unknown_burn_rate_fails_fast() -> None:
    state = _state([_row(65, 100.0, stratum="zzz_zz")])
    harvests, salvages = _schedules(1, 1, 10)

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass.replay_lineage(
            state,
            harvests=harvests,
            salvages=salvages,
            steps=1,
            period_length=10,
            decay_rate=0.85,
            age_caps=_age_caps(),
            regeneration_age=5,
        )

    assert excinfo.value.code == "single_pass_burn_rate_unknown"


def test_replay_missing_age_cap_fails_fast() -> None:
    state = _state([_row(65, 100.0)])
    harvests, salvages = _schedules(1, 1, 10)

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass.replay_lineage(
            state,
            harvests=harvests,
            salvages=salvages,
            steps=1,
            period_length=10,
            decay_rate=0.85,
            age_caps={CURVE_ID + 1: 195},
            regeneration_age=5,
        )

    assert excinfo.value.code == "single_pass_curve_age_cap_missing"


# --- objective decompositions ----------------------------------------------


def _offset_cohort(decay: float = 0.9) -> principal.PrincipalCohort:
    """Cohort with a burned seed and no configured economics (static path)."""

    return _cohort(
        cohort_id="only",
        green_volume=100.0,
        burned_volume=10.0,
        cashflow=20.0,
        burned_value=10.0,
        burn_rate=0.1,
    )


def test_principal_decade_decomposition_carries_the_offset() -> None:
    # A zero ceiling blocks every offer, so the objective is exactly the constant
    # -sum_y loss[c,y] offset; each decade must carry only its own years' loss.
    cohort = _offset_cohort()
    result = principal.solve_principal(
        [cohort],
        horizon=4,
        aac_annual_m3=0.0,
        decay_rate=0.9,
        cohort_ceilings_m3=[0.0],
        period_length=2,
    )

    losses = [0.1 * 10.0 * (1.0 - 0.9**year) for year in range(4)]
    assert result.decadal_objective_values == pytest.approx(
        [-(losses[0] + losses[1]), -(losses[2] + losses[3])]
    )
    assert sum(result.decadal_objective_values) == pytest.approx(
        result.objective_value, rel=1e-9
    )


def test_principal_decade_decomposition_attributes_offer_columns() -> None:
    # Ceilings allow one offer in year 3 only: cashflow lands in decade 2 and
    # the cumulative-offer loss term follows the offered year.
    cohort = _offset_cohort()
    result = principal.solve_principal(
        [cohort],
        horizon=4,
        aac_annual_m3=1_000_000.0,
        decay_rate=0.9,
        cohort_ceilings_m3=[(0.0, 0.0, 0.0, 100_000.0)],
        period_length=2,
    )

    losses = [0.1 * 10.0 * (1.0 - 0.9**year) for year in range(4)]
    assert result.decadal_objective_values == pytest.approx(
        [-(losses[0] + losses[1]), cohort.cashflow - losses[2]]
    )
    assert sum(result.decadal_objective_values) == pytest.approx(
        result.objective_value, rel=1e-9
    )


def test_principal_decade_decomposition_rejects_partial_decades() -> None:
    cohort = _offset_cohort()

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.solve_principal(
            [cohort],
            horizon=5,
            aac_annual_m3=0.0,
            cohort_ceilings_m3=[0.0],
            period_length=2,
        )

    assert excinfo.value.code == "principal_invalid_period_length"


def test_agent_decade_decomposition_rebases_to_each_decade_start() -> None:
    cohort = _agent_cohort("only", standing_volume=1_000.0, burn_rate=0.02)
    built = agent.build_agent_lp(
        [cohort],
        [(1.0,) * 4],
        horizon=4,
        discount_rate=0.03,
        period_length=2,
    )
    built.model.run()
    solution = built.model.getSolution().col_value

    values = agent.decadal_objective_values(
        built, solution, period_length=2, discount_rate=0.03
    )

    # Independent expectation: per-decade raw cost*x sums, re-based by
    # (1+r) ** (L * k).
    costs = built.objective_coefficients
    block = len(built.decision_columns)
    expected = []
    for decade in range(2):
        raw = 0.0
        for column, (_c_index, year) in enumerate(built.decision_columns):
            if year // 2 != decade:
                continue
            raw += float(costs[column]) * float(solution[column])
            raw += float(costs[block + column]) * float(solution[block + column])
        expected.append(raw * 1.03 ** (2 * decade))
    assert values == pytest.approx(expected)
    assert sum(values) == pytest.approx(
        float(built.model.getInfo().objective_function_value), rel=1e-9
    )


def test_agent_decade_decomposition_rejects_partial_decades() -> None:
    cohort = _agent_cohort("only")

    with pytest.raises(agent.AgentError) as excinfo:
        agent.solve_agent(
            [cohort], [(1.0,) * 5], horizon=5, period_length=2
        )

    assert excinfo.value.code == "agent_invalid_period_length"


# --- decade record slicing --------------------------------------------------


def _fake_principal_result(steps: int, decade_values: list[float]) -> PrincipalResult:
    return PrincipalResult(
        run_id="fake-principal",
        status="optimal",
        horizon=steps * 10,
        cohorts=1,
        objective_value=sum(decade_values),
        decadal_objective_values=decade_values,
        offered_cohort_years=0,
        solve_seconds=1.0,
    )


def _fake_agent_result(steps: int, decade_values: list[float]) -> AgentResult:
    return AgentResult(
        run_id="fake-agent",
        status="optimal",
        horizon=steps * 10,
        cohorts=1,
        objective_value=sum(decade_values),
        per_year_volumes=[
            AgentYearVolumes(
                year=year + 1,
                harvest_volume_m3=float(year),
                salvage_volume_m3=float(year) / 10.0,
                burn_influx_m3=0.0,
                live_volume_m3=0.0,
                burned_volume_m3=0.0,
            )
            for year in range(steps * 10)
        ],
        decadal_objective_values=decade_values,
        active_cohort_years=0,
        solve_seconds=2.0,
    )


def _fake_replay(steps: int, state: pd.DataFrame) -> single_pass.LineageReplay:
    return single_pass.LineageReplay(
        snapshots=tuple(state.copy() for _ in range(steps + 1)),
        area_burned_ha=tuple(float(decade + 1) for decade in range(steps)),
        area_harvested_ha=(0.0,) * steps,
        area_salvaged_ha=(0.0,) * steps,
        decade_wall_seconds=tuple(0.5 for _ in range(steps)),
    )


def _config(tmp_path: Path, **overrides: object) -> RHRunConfig:
    fields: dict[str, object] = {
        "run_id": "sp-test",
        "stands_path": tmp_path / "stands.parquet",
        "yields_path": tmp_path / "yields.csv",
        "bridge_path": tmp_path / "bridge",
        "base_year": 2025,
        "horizon": 15,
        "period_length": 10,
        "steps": 2,
        "workers": 1,
        "output_root": tmp_path / "out",
        "planning_mode": "single_pass",
    }
    fields.update(overrides)
    return RHRunConfig(**fields)  # type: ignore[arg-type]


def test_decade_records_slice_annual_arrays_and_rebase_objectives(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = _state([_row(65, 100.0)])

    records = single_pass._decade_records(
        config,
        principal_result=_fake_principal_result(2, [-100.0, -60.0]),
        agent_result=_fake_agent_result(2, [200.0, 100.0]),
        replay=_fake_replay(2, state),
        ws3_status="optimal",
        ws3_objective=7.5,
        ws3_build_seconds=1.0,
        ws3_solve_seconds=2.0,
    )

    assert [record.step for record in records] == [1, 2]
    assert [record.start_year for record in records] == [2025, 2035]
    assert records[0].annual_green_harvest_m3 == [float(year) for year in range(10)]
    assert records[1].annual_green_harvest_m3 == [
        float(year) for year in range(10, 20)
    ]
    assert records[0].annual_burned_harvest_m3 == [
        float(year) / 10.0 for year in range(10)
    ]
    assert [record.area_burned_ha for record in records] == [1.0, 2.0]
    # D8: the single WS3 solve is attributed to decade 1 only.
    assert records[0].ws3_status == "optimal"
    assert records[0].ws3_objective_value == 7.5
    assert records[0].ws3_solve_seconds == 2.0
    assert records[1].ws3_status is None
    assert records[1].ws3_objective_value is None
    assert records[1].ws3_solve_seconds == 0.0
    # D7: decade-start basis in *_objective_value, base-year basis next to it.
    assert [record.principal_objective_value for record in records] == [-100.0, -60.0]
    assert [record.agent_objective_value for record in records] == [200.0, 100.0]
    assert records[0].agent_objective_value_base_year == 200.0
    assert records[1].agent_objective_value_base_year == pytest.approx(
        100.0 * 1.03**-10
    )
    assert [record.principal_objective_value_base_year for record in records] == [
        -100.0,
        -60.0,
    ]
    # Every record round-trips through its JSONL serialization.
    assert [
        RHStepRecord.model_validate_json(record.model_dump_json()) for record in records
    ] == records


def test_decade_records_require_the_decade_decomposition(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = _state([_row(65, 100.0)])

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass._decade_records(
            config,
            principal_result=_fake_principal_result(2, []),
            agent_result=_fake_agent_result(2, [200.0, 100.0]),
            replay=_fake_replay(2, state),
            ws3_status="optimal",
            ws3_objective=7.5,
            ws3_build_seconds=1.0,
            ws3_solve_seconds=2.0,
        )

    assert excinfo.value.code == "single_pass_objective_decomposition_missing"


# --- config surface ---------------------------------------------------------


def test_planning_mode_defaults_are_backward_compatible(tmp_path: Path) -> None:
    config = _config(tmp_path, planning_mode="rolling_horizon", steps=10, horizon=10)

    assert config.planning_mode == "rolling_horizon"
    assert config.burned_stock_reset is False


def test_single_pass_config_yaml_without_new_fields_parses(tmp_path: Path) -> None:
    config_path = tmp_path / "rh.yaml"
    config_path.write_text(
        "run_id: sp-yaml\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "horizon: 15\n"
        "steps: 10\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    config = RHRunConfig.read(config_path)

    assert config.planning_mode == "rolling_horizon"
    assert config.burned_stock_reset is False


def test_single_pass_config_requires_horizon_to_cover_steps(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="horizon >= steps"):
        _config(tmp_path, horizon=2, steps=3)


@pytest.mark.parametrize(
    "age_smashing",
    [
        AgeSmashing(enabled=False, width=10, midpoint=5),
        AgeSmashing(enabled=True, width=4, midpoint=2),  # 10 % 4 != 0
        AgeSmashing(enabled=True, width=10, midpoint=0),  # regen residue
    ],
)
def test_single_pass_config_requires_disjoint_age_lattices(
    tmp_path: Path, age_smashing: AgeSmashing
) -> None:
    with pytest.raises(ValidationError, match="age lattices"):
        _config(tmp_path, age_smashing=age_smashing)


def test_single_pass_config_requires_period_length_multiple_of_width(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="age lattices"):
        _config(tmp_path, period_length=1)


def test_burned_stock_reset_only_applies_to_single_pass(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="only applies to planning_mode"):
        _config(
            tmp_path, planning_mode="rolling_horizon", burned_stock_reset=True
        )


def test_single_pass_config_accepts_burned_stock_reset(tmp_path: Path) -> None:
    config = _config(tmp_path, burned_stock_reset=True)

    assert config.burned_stock_reset is True
    assert config.planning_mode == "single_pass"


def test_old_rh_manifest_parses_without_planning_mode(tmp_path: Path) -> None:
    manifest = RHManifest(
        run_id="legacy",
        stands_path=tmp_path / "stands.parquet",
        yields_path=tmp_path / "yields.csv",
        bridge_path=tmp_path / "bridge",
        status="optimal",
        steps=1,
        horizon=15,
        period_length=10,
        cohorts=1,
        total_green_harvest_m3=0.0,
        total_burned_harvest_m3=0.0,
        total_area_burned_ha=0.0,
        wall_seconds=0.0,
        step_records=[
            RHStepRecord(
                step=1,
                start_year=2025,
                ws3_status="optimal",
                ws3_objective_value=1.0,
                ws3_build_seconds=0.0,
                ws3_solve_seconds=0.0,
                principal_objective_value=1.0,
                agent_objective_value=1.0,
                area_burned_ha=0.0,
                wall_seconds=0.0,
            )
        ],
    ).model_dump(mode="json")
    for key in ("planning_mode", "total_principal_objective", "total_agent_objective_base_year"):
        manifest.pop(key)
    payload = dict(manifest)
    payload["step_records"] = [
        {
            key: value
            for key, value in manifest["step_records"][0].items()
            if key not in BASE_YEAR_FIELDS
        }
    ]
    path = tmp_path / "legacy-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = RHManifest.read_json(path)

    assert restored.planning_mode == "rolling_horizon"
    assert restored.total_principal_objective is None
    assert restored.total_agent_objective_base_year is None
    assert restored.step_records[0].ws3_status == "optimal"


def test_single_pass_record_parses_with_absent_ws3_fields() -> None:
    record = RHStepRecord.model_validate_json(
        json.dumps(
            {
                "step": 7,
                "start_year": 2085,
                "principal_objective_value": -1.0,
                "agent_objective_value": 2.0,
                "area_burned_ha": 3.0,
                "wall_seconds": 0.1,
            }
        )
    )

    assert record.ws3_status is None
    assert record.ws3_objective_value is None
    assert record.ws3_build_seconds == 0.0


def test_sensitivity_metrics_skip_absent_ws3_objectives() -> None:
    manifest = RHManifest(
        run_id="sp-metrics",
        stands_path=Path("stands.parquet"),
        yields_path=Path("yields.csv"),
        bridge_path=Path("bridge"),
        status="optimal",
        planning_mode="single_pass",
        steps=2,
        horizon=15,
        period_length=10,
        cohorts=1,
        total_green_harvest_m3=0.0,
        total_burned_harvest_m3=0.0,
        total_area_burned_ha=0.0,
        wall_seconds=0.0,
        step_records=[
            RHStepRecord(
                step=1,
                start_year=2025,
                ws3_status="optimal",
                ws3_objective_value=5.0,
                ws3_build_seconds=0.0,
                ws3_solve_seconds=0.0,
                principal_objective_value=1.0,
                agent_objective_value=2.0,
                area_burned_ha=0.0,
                wall_seconds=0.0,
            ),
            RHStepRecord(
                step=2,
                start_year=2035,
                principal_objective_value=3.0,
                agent_objective_value=4.0,
                area_burned_ha=0.0,
                wall_seconds=0.0,
            ),
        ],
    )

    metrics = sensitivity._manifest_metrics(manifest)

    assert metrics["total_ws3_objective_value"] == 5.0
    assert metrics["total_principal_objective_value"] == 4.0
    assert metrics["total_agent_objective_value"] == 6.0


# --- dispatcher and CLI -----------------------------------------------------


def _stub_result(run_id: str, planning_mode: str, burned: float = 0.0) -> RHResult:
    return RHResult(
        run_id=run_id,
        status="optimal",
        planning_mode=planning_mode,
        steps=1,
        horizon=3,
        period_length=10,
        cohorts=1,
        decadal_burned_harvest_m3=[burned],
        wall_seconds=0.1,
    )


@pytest.mark.parametrize(
    ("mode", "attribute"),
    [("rolling_horizon", "run_rh"), ("single_pass", "run_single_pass")],
)
def test_run_planning_dispatches_by_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, attribute: str
) -> None:
    calls: list[RHRunConfig] = []

    def fake_run_rh(config: RHRunConfig, verbose: bool = False) -> RHResult:
        calls.append(config)
        return _stub_result(config.run_id, "rolling_horizon")

    def fake_run_single_pass(config: RHRunConfig, verbose: bool = False) -> RHResult:
        calls.append(config)
        return _stub_result(config.run_id, "single_pass")

    monkeypatch.setattr(rh, "run_rh", fake_run_rh)
    monkeypatch.setattr(single_pass, "run_single_pass", fake_run_single_pass)
    config = _config(
        tmp_path, planning_mode=mode, steps=1, horizon=3, burned_stock_reset=False
    )

    result = rh.run_planning(config)

    assert len(calls) == 1
    assert result.planning_mode == mode
    assert (attribute == "run_rh") == (mode == "rolling_horizon")


def test_run_rh_rejects_single_pass_config(tmp_path: Path) -> None:
    config = _config(tmp_path, steps=1, horizon=3)

    with pytest.raises(rh.RHError) as excinfo:
        rh.run_rh(config)

    assert excinfo.value.code == "rh_planning_mode_invalid"


def test_run_single_pass_rejects_rolling_horizon_config(tmp_path: Path) -> None:
    config = _config(tmp_path, planning_mode="rolling_horizon", steps=1, horizon=3)

    with pytest.raises(single_pass.SinglePassError) as excinfo:
        single_pass.run_single_pass(config)

    assert excinfo.value.code == "single_pass_planning_mode_required"


def test_ensemble_dispatches_planning_mode_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_run_rh(config: RHRunConfig, verbose: bool = False) -> RHResult:
        seen.append("rolling_horizon")
        return _stub_result(config.run_id, "rolling_horizon", burned=1.0)

    def fake_run_single_pass(config: RHRunConfig, verbose: bool = False) -> RHResult:
        seen.append("single_pass")
        return _stub_result(config.run_id, "single_pass", burned=2.0)

    monkeypatch.setattr(rh, "run_rh", fake_run_rh)
    monkeypatch.setattr(single_pass, "run_single_pass", fake_run_single_pass)
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    for name in ("stands.parquet", "yields.csv"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    config = ensemble.EnsembleConfig(
        ensemble_id="mode-axis",
        base={
            "stands_path": str(tmp_path / "stands.parquet"),
            "yields_path": str(tmp_path / "yields.csv"),
            "bridge_path": str(bridge),
            "horizon": 3,
            "steps": 1,
            "workers": 1,
        },
        axes={"planning_mode": ["rolling_horizon", "single_pass"]},
        max_workers=1,
        output_root=tmp_path / "ensemble",
    )

    result = ensemble.run_ensemble(config)

    assert result.status == "ok"
    assert seen == ["rolling_horizon", "single_pass"]
    assert [record.name for record in result.scenarios] == [
        "planning_mode-rolling_horizon",
        "planning_mode-single_pass",
    ]
    assert [record.metric_value for record in result.scenarios] == [1.0, 2.0]


def test_cli_single_pass_run_help() -> None:
    result = runner.invoke(app, ["single-pass-run", "--help"])

    assert result.exit_code == 0
    assert "single-pass" in result.stdout


def test_cli_single_pass_run_missing_config_fails_with_json_diagnostic(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app, ["single-pass-run", str(tmp_path / "missing.yaml"), "--json"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "single-pass-run"
    assert payload["diagnostic"]


def test_cli_single_pass_run_forces_the_planning_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[RHRunConfig] = []
    monkeypatch.setattr(
        single_pass,
        "run_single_pass",
        lambda config, verbose=False: (
            captured.append(config) or _stub_result(config.run_id, "single_pass")
        ),
    )
    config_path = tmp_path / "single-pass.yaml"
    config_path.write_text(
        "run_id: cli-single-pass\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "horizon: 3\n"
        "steps: 1\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["single-pass-run", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert "Single-pass run complete: cli-single-pass" in result.stdout
    assert captured[0].planning_mode == "single_pass"


def test_cli_single_pass_run_rejects_invalid_single_pass_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        single_pass,
        "run_single_pass",
        lambda config, verbose=False: pytest.fail("run must not start"),
    )
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(
        "run_id: cli-invalid\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "horizon: 1\n"
        "steps: 5\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["single-pass-run", str(config_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "horizon >= steps" in payload["diagnostic"]


def test_cli_rh_run_dispatches_single_pass_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # M1 (review): `rh-run` shares `rh.run_planning`, so a config alone
    # switches modes. A `planning_mode: single_pass` config must run the
    # single-pass path, with the config passed through unchanged (no forced
    # mode; that stays `single-pass-run`'s job).
    captured: list[RHRunConfig] = []
    monkeypatch.setattr(
        single_pass,
        "run_single_pass",
        lambda config, verbose=False: (
            captured.append(config) or _stub_result(config.run_id, "single_pass")
        ),
    )
    config_path = tmp_path / "rh-single-pass.yaml"
    config_path.write_text(
        "run_id: cli-rh-single-pass\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "horizon: 3\n"
        "steps: 1\n"
        "planning_mode: single_pass\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["rh-run", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert captured[0].planning_mode == "single_pass"
    assert "Single-pass run complete" in result.stdout


def test_cli_rh_run_rolling_horizon_config_uses_run_rh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rolling-horizon config keeps its pre-Phase 8 behaviour: `rh-run`
    # routes through the shared dispatcher to `rh.run_rh` unchanged.
    captured: list[RHRunConfig] = []
    monkeypatch.setattr(
        rh,
        "run_rh",
        lambda config, verbose=False: (
            captured.append(config) or _stub_result(config.run_id, "rolling_horizon")
        ),
    )
    config_path = tmp_path / "rh.yaml"
    config_path.write_text(
        "run_id: cli-rh\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "horizon: 3\n"
        "steps: 1\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["rh-run", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert captured[0].planning_mode == "rolling_horizon"
    assert "Rolling-horizon run complete" in result.stdout


# --- oracle: single pass with steps=1 reproduces the RH step-1 record -------


def _comparable_record(record: RHStepRecord) -> dict:
    """Record fields both engines must agree on (timings/bases stripped)."""

    return {
        key: value
        for key, value in record.model_dump().items()
        if key not in TIMING_FIELDS and key not in BASE_YEAR_FIELDS
    }


def _assert_matching_step1(
    single_pass_record: RHStepRecord, rh_record: RHStepRecord
) -> None:
    """The decade-1 record must reproduce the RH step-1 record.

    The LPs are bit-identical (the P8.2/P8.3 equivalence suite), so every array
    field matches exactly; the objective fields are the single-pass decade
    decompositions, which sum the same terms in a different order than HiGHS's
    own dot product and therefore agree to float-summation dust.
    """

    objective_fields = {
        "principal_objective_value",
        "agent_objective_value",
    }
    single_pass_fields = _comparable_record(single_pass_record)
    rh_fields = _comparable_record(rh_record)
    for field in sorted(objective_fields):
        assert single_pass_fields.pop(field) == pytest.approx(
            rh_fields.pop(field), rel=1e-12
        )
    assert single_pass_fields == rh_fields


def _compare_final_states(single_pass_csv: Path, rh_csv: Path) -> None:
    """Both final states must hold the same cohorts and areas (to 1e-9 rel)."""

    single_pass_state = pd.read_csv(single_pass_csv)
    rh_state = pd.read_csv(rh_csv)
    keys = list(rh.STATE_KEY_COLUMNS)
    left = single_pass_state.sort_values(keys).reset_index(drop=True)
    right = rh_state.sort_values(keys).reset_index(drop=True)
    assert left[keys].equals(right[keys])
    assert left["area_ha"].to_numpy() == pytest.approx(
        right["area_ha"].to_numpy(), rel=1e-9
    )


class _StubDevelopmentType:
    def __init__(self) -> None:
        self._areas: list[dict[int, float]] = [{}]


class _StubForestModel:
    """Minimal ForestModel stand-in for ``rh.inject_cohort_table``."""

    def __init__(self, dtype_keys: list[tuple[str, ...]]) -> None:
        self.dtypes = {key: _StubDevelopmentType() for key in dtype_keys}
        self.injected: dict[tuple[str, ...], dict[int, float]] = {}

    def initialize_areas(self, reset_areas: bool = False) -> None:
        self.injected = {
            key: dict(development_type._areas[0])
            for key, development_type in self.dtypes.items()
        }


class _StubProblem:
    def solve(self, verbose: bool = False) -> None:
        return None

    def status(self) -> str:
        return "optimal"


@pytest.fixture
def synthetic_ws3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Write synthetic boundary inputs and monkeypatch the WS3 stage.

    The fake WS3 stage solves nothing: ``_compile_schedule`` emits one
    period-1 ``cc`` row per injected cohort at its injected age, so the
    ceiling path (and everything downstream of it) runs for real.
    """

    stands_rows = [
        {
            "development_type": "SPF_SBPS",
            "Total_Green_Vol": 1_000.0,
            "Total_Burned_Vol": 100.0,
            **{column: 0.0 for column in GRADE_COLUMNS},
            **{column: 0.0 for column in BURNED_GRADE_COLUMNS},
            "SPF_Peelers_Vol": 400.0,
            "SPF_Sawlog_Vol": 500.0,
            "SPF_Pulpwood_Vol": 100.0,
            "B_SPF_Peelers_Vol": 40.0,
            "B_SPF_Sawlog_Vol": 50.0,
            "B_SPF_Pulpwood_Vol": 10.0,
        }
    ]
    stands_path = tmp_path / "stands.parquet"
    pd.DataFrame(stands_rows).to_parquet(stands_path, index=False)
    yields_path = tmp_path / "yields.csv"
    pd.DataFrame(
        {
            "curve_id": [CURVE_ID, CURVE_ID],
            "age": [0, 200],
            "volume": [0.0, 150.0],
        }
    ).to_csv(yields_path, index=False)
    state = _state([_row(65, 100.0), _row(145, 50.0)])
    bridge = tmp_path / "bridge"
    rh.write_cohort_table(state, bridge / f"{ws3.BRIDGE_FILE_PREFIX}.are")

    dtype_keys = [
        ("29", "managed", "2901000", STRATUM, str(CURVE_ID)),
    ]
    model = _StubForestModel(dtype_keys)

    def fake_load_full_model(config: object, verbose: bool = False) -> object:
        return model

    def fake_build_problem(model: object, config: object, verbose: bool = False) -> object:
        return _StubProblem()

    def fake_compile_schedule(
        model: object, problem: object, config: object
    ) -> tuple[pd.DataFrame, float]:
        rows = []
        for dtype_key, ages in sorted(model.injected.items()):
            for age, area in sorted(ages.items()):
                if area <= 0.0:
                    continue
                rows.append(
                    {
                        "period": 1,
                        "year": 2025,
                        "dtype_key": json.dumps(list(dtype_key)),
                        "stratum": dtype_key[3],
                        "age_class": int(age),
                        "area_ha": area,
                        "harvest_action": "cc",
                        "volume_m3": area * 10.0,
                        "etype": "a",
                    }
                )
        schedule = pd.DataFrame(rows).loc[:, list(ws3.SCHEDULE_COLUMNS)]
        return schedule, float(schedule["volume_m3"].sum()) * 0.85

    monkeypatch.setattr(ws3, "load_full_model", fake_load_full_model)
    monkeypatch.setattr(ws3, "_build_problem", fake_build_problem)
    monkeypatch.setattr(ws3, "_compile_schedule", fake_compile_schedule)
    return {"stands": stands_path, "yields": yields_path, "bridge": bridge}


def _oracle_config(
    inputs: dict[str, Path], tmp_path: Path, name: str, **overrides: object
) -> RHRunConfig:
    fields: dict[str, object] = {
        "run_id": f"oracle-{name}",
        "stands_path": inputs["stands"],
        "yields_path": inputs["yields"],
        "bridge_path": inputs["bridge"],
        "base_year": 2025,
        "horizon": 3,
        "period_length": 10,
        "steps": 1,
        "workers": 1,
        "output_root": tmp_path / name,
    }
    fields.update(overrides)
    return RHRunConfig(**fields)  # type: ignore[arg-type]


def test_oracle_single_pass_steps1_reproduces_rh_step1(
    tmp_path: Path, synthetic_ws3: dict[str, Path]
) -> None:
    rh_result = rh.run_rh(_oracle_config(synthetic_ws3, tmp_path, "rh"))
    single_pass_result = single_pass.run_single_pass(
        _oracle_config(
            synthetic_ws3, tmp_path, "sp", planning_mode="single_pass"
        )
    )

    assert rh_result.status == "optimal"
    assert single_pass_result.status == "optimal"
    assert len(rh_result.step_records) == len(single_pass_result.step_records) == 1
    rh_record = rh_result.step_records[0]
    single_pass_record = single_pass_result.step_records[0]
    _assert_matching_step1(single_pass_record, rh_record)
    # Decade 0 is the base year, so both bases coincide exactly.
    assert (
        single_pass_record.principal_objective_value_base_year
        == single_pass_record.principal_objective_value
    )
    assert (
        single_pass_record.agent_objective_value_base_year
        == single_pass_record.agent_objective_value
    )
    assert rh_record.principal_objective_value_base_year is None
    assert rh_record.agent_objective_value_base_year is None
    # Same next state (replay formula differs in float association only).
    _compare_final_states(
        single_pass_result.final_state_path, rh_result.final_state_path
    )
    # The run totals re-based to the base year are the same LP objectives.
    manifest = RHManifest.read_json(single_pass_result.manifest_path)
    assert manifest.planning_mode == "single_pass"
    assert manifest.total_principal_objective == pytest.approx(
        single_pass_record.principal_objective_value, rel=1e-15
    )
    assert manifest.total_agent_objective_base_year == pytest.approx(
        single_pass_record.agent_objective_value_base_year, rel=1e-15
    )
    assert manifest.total_green_harvest_m3 == pytest.approx(
        sum(single_pass_record.annual_green_harvest_m3)
    )


def test_single_pass_multi_decade_records_and_snapshots(
    tmp_path: Path, synthetic_ws3: dict[str, Path]
) -> None:
    # steps=2 exercises the decade blocking: decade 2 carries no WS3 fields and
    # a zero ceiling matrix, so it replays as pure fire.
    result = single_pass.run_single_pass(
        _config(
            tmp_path,
            run_id="sp-two-decades",
            stands_path=synthetic_ws3["stands"],
            yields_path=synthetic_ws3["yields"],
            bridge_path=synthetic_ws3["bridge"],
            horizon=3,
            steps=2,
            workers=1,
            output_root=tmp_path / "two-decades",
        )
    )

    assert [record.step for record in result.step_records] == [1, 2]
    assert result.step_records[1].ws3_objective_value is None
    assert result.step_records[1].annual_green_harvest_m3 == [0.0] * 10
    state_dir = tmp_path / "two-decades" / "derived" / "rh_state"
    assert (state_dir / "step_00.are").is_file()
    assert (state_dir / "step_01.are").is_file()
    assert (state_dir / "step_02.are").is_file()
    final_state = pd.read_csv(result.final_state_path)
    assert final_state["area_ha"].sum() == pytest.approx(150.0, rel=1e-9)
    # The final ARE snapshot and the CSV hold the same state.
    assert rh.read_cohort_table(
        state_dir / "step_02.are"
    )["area_ha"].to_numpy() == pytest.approx(final_state["area_ha"].to_numpy())


# --- real-data oracle (opt-in integration) ---------------------------------

STANDS_PATH = Path("outputs/full_tsa/data/tsa29-full-stands.parquet")
STAGE1_YIELDS_PATH = Path(
    "/srv/shared-data/gep/jupyterhub07-projects/davis/femic/external/"
    "femic-tsa29-instance/output/woodstock_tsa29_validated/woodstock_yields.csv"
)


def _require_integration_inputs() -> None:
    """Skip when the ws3 package or the local TSA29 inputs are unavailable."""

    try:
        import ws3 as _ws3  # noqa: F401
    except ImportError:
        pytest.skip("ws3 package unavailable (set PYTHONPATH to the ws3 repo)")
    for path in (STANDS_PATH, STAGE1_YIELDS_PATH, ws3.CANONICAL_TSA29_BRIDGE):
        if not path.exists():
            pytest.skip(f"local TSA29 input unavailable: {path}")


def test_real_data_oracle_single_pass_steps1_matches_rh_step1(tmp_path: Path) -> None:
    _require_integration_inputs()

    def _config(run_id: str, output_root: Path, **overrides: object) -> RHRunConfig:
        fields: dict[str, object] = {
            "run_id": run_id,
            "stands_path": STANDS_PATH,
            "yields_path": STAGE1_YIELDS_PATH,
            "bridge_path": ws3.CANONICAL_TSA29_BRIDGE,
            "base_year": 2025,
            "horizon": 3,
            "period_length": 10,
            "steps": 1,
            "workers": 1,
            "output_root": output_root,
        }
        fields.update(overrides)
        return RHRunConfig(**fields)  # type: ignore[arg-type]

    rh_result = rh.run_rh(_config("real-oracle-rh", tmp_path / "rh"))
    single_pass_result = single_pass.run_single_pass(
        _config("real-oracle-sp", tmp_path / "sp", planning_mode="single_pass")
    )

    assert len(rh_result.step_records) == len(single_pass_result.step_records) == 1
    _assert_matching_step1(
        single_pass_result.step_records[0], rh_result.step_records[0]
    )
    _compare_final_states(
        single_pass_result.final_state_path, rh_result.final_state_path
    )


def test_real_data_single_pass_steps2_replay_smoke(tmp_path: Path) -> None:
    """Gated smoke: decade 2+ of the true single-pass replay on TSA29 inputs.

    One real WS3 solve over the canonical bridge, then the true 20-year
    principal/agent pair and the two-decade lineage replay. This is the
    cheapest end-to-end path through the decade-2 replay (which the steps=1
    oracle never reaches); it asserts the run structure, the absence of the
    pre-fix boundary salvage failure, area conservation against the bridge
    inventory, and the last snapshot's agreement with the final state CSV.
    """

    _require_integration_inputs()

    config = RHRunConfig(
        run_id="real-smoke-sp-steps2",
        stands_path=STANDS_PATH,
        yields_path=STAGE1_YIELDS_PATH,
        bridge_path=ws3.CANONICAL_TSA29_BRIDGE,
        base_year=2025,
        horizon=3,
        period_length=10,
        steps=2,
        workers=1,
        planning_mode="single_pass",
        output_root=tmp_path / "sp2",
    )

    result = single_pass.run_single_pass(config)

    assert result.status == "optimal"
    assert [record.step for record in result.step_records] == [1, 2]
    assert result.step_records[0].ws3_status == "optimal"
    assert result.step_records[1].ws3_status is None
    assert len(result.step_records[1].annual_green_harvest_m3) == 10
    state_dir = tmp_path / "sp2" / "derived" / "rh_state"
    assert (state_dir / "step_01.are").is_file()
    assert (state_dir / "step_02.are").is_file()
    # The step_00 snapshot is the canonicalized t=0 state the run injected;
    # the replay conserves area across both decades against it.
    initial_snapshot = rh.read_cohort_table(state_dir / "step_00.are")
    final_state = pd.read_csv(result.final_state_path)
    assert final_state["area_ha"].sum() == pytest.approx(
        initial_snapshot["area_ha"].sum(), rel=1e-6
    )
    assert rh.read_cohort_table(state_dir / "step_02.are")[
        "area_ha"
    ].to_numpy() == pytest.approx(final_state["area_ha"].to_numpy(), rel=1e-12)
