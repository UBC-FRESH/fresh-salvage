"""Rolling-horizon engine invariant tests.

The pure state-transition and boundary-parser tests run without the external
ws3 package. The end-to-end determinism test needs the ws3 package, the
canonical TSA29 bridge, and the Phase 2a stands table; it skips cleanly when
any of those are unavailable (e.g. in CI).
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fresh_salvage import fire, principal, rh
from fresh_salvage.models import RHRunConfig

STRATUM = "sbps_pli"
CURVE_ID = 2921000
BURN_RATE_SBPS = 1.0 / 100.0


def _curves(max_age: int = 200) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Two-point yield curve: 0 m3/ha at age 0, 150 m3/ha at ``max_age``."""

    return {
        CURVE_ID: (
            np.array([0.0, float(max_age)]),
            np.array([0.0, 150.0]),
        )
    }


def _state(rows: list[tuple[str, str, str, str, int, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=pd.Index(list(rh.COHORT_COLUMNS)))


def _row(age: int, area: float, stratum: str = STRATUM) -> tuple:
    return ("29", "managed", "2901000", stratum, CURVE_ID, age, area)


def _advance(
    state: pd.DataFrame,
    harvests: list[tuple[float, ...]],
    salvages: list[tuple[float, ...]] | None = None,
    period_length: int = 10,
    max_age: int = 200,
) -> tuple[pd.DataFrame, dict[str, float]]:
    caps = rh.curve_age_caps(_curves(max_age), width=10, midpoint=5)
    return rh.advance_cohort_table(
        state,
        harvests=harvests,
        salvages=salvages or [tuple([0.0]) * period_length for _ in range(len(state))],
        decay_rate=fire.DEFAULT_BURNED_DECAY_RATE,
        age_caps=caps,
        period_length=period_length,
        regeneration_age=5,
    )


def _live_fraction_end(harvest: tuple[float, ...], period_length: int) -> float:
    """Independent fire-primitive replay of the surviving live fraction."""

    years = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE_SBPS,
        harvest_schedule=harvest,
        salvage_schedule=tuple([0.0]) * period_length,
    )
    return years[-1].live_after


# --- cohort table boundary parsing -----------------------------------------


def test_read_cohort_table_roundtrip_lossless(tmp_path: Path) -> None:
    state = _state([_row(25, 100.0), _row(195, 50.123456789123)])
    are_path = rh.write_cohort_table(state, tmp_path / "state.are")

    restored = rh.read_cohort_table(are_path)

    assert list(restored.columns) == list(rh.COHORT_COLUMNS)
    assert restored["area_ha"].sum() == state["area_ha"].sum()
    assert restored.loc[1, "area_ha"] == 50.123456789123


def test_read_cohort_table_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(rh.RHError) as excinfo:
        rh.read_cohort_table(tmp_path / "missing.are")

    assert excinfo.value.code == "rh_state_missing"


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("*A 29 managed 2901000 sbps_pli 2921000 25", "rh_state_unparseable"),
        ("*A 29 managed 2901000 sbps_pli 2921000 25 not_a_float", "rh_state_unparseable"),
        ("*A 29 managed 2901000 sbps_pli 2921000 -25 10.0", "rh_state_invalid_age"),
        ("*A 29 managed 2901000 sbps_pli 2921000 25 -10.0", "rh_state_negative_area"),
    ],
)
def test_read_cohort_table_malformed_rows_fail_fast(
    tmp_path: Path, line: str, code: str
) -> None:
    are_path = tmp_path / "bad.are"
    are_path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(rh.RHError) as excinfo:
        rh.read_cohort_table(are_path)

    assert excinfo.value.code == code


def test_read_cohort_table_duplicate_keys_fail(tmp_path: Path) -> None:
    line = "*A 29 managed 2901000 sbps_pli 2921000 25 10.0\n"
    are_path = tmp_path / "dup.are"
    are_path.write_text(line + line, encoding="utf-8")

    with pytest.raises(rh.RHError) as excinfo:
        rh.read_cohort_table(are_path)

    assert excinfo.value.code == "rh_state_duplicate_cohort"


def test_read_cohort_table_empty_fails(tmp_path: Path) -> None:
    are_path = tmp_path / "empty.are"
    are_path.write_text("# no data rows here\n", encoding="utf-8")

    with pytest.raises(rh.RHError) as excinfo:
        rh.read_cohort_table(are_path)

    assert excinfo.value.code == "rh_state_empty"


# --- curve age caps ---------------------------------------------------------


def test_curve_age_caps_snap_to_midpoint_lattice() -> None:
    caps = rh.curve_age_caps(_curves(max_age=200), width=10, midpoint=5)

    # midpoint_age(200) = 205 overshoots the tabulated max, so the cap is one
    # class lower and stays on the 5/15/25/... lattice.
    assert caps[CURVE_ID] == 195
    assert caps[CURVE_ID] % 10 == 5


def test_curve_age_caps_reject_degenerate_curves() -> None:
    curves = {CURVE_ID: (np.array([0.0, 3.0]), np.array([0.0, 1.0]))}

    with pytest.raises(rh.RHError) as excinfo:
        rh.curve_age_caps(curves, width=10, midpoint=5)

    assert excinfo.value.code == "rh_curve_age_cap_invalid"


# --- decadal -> annual split ------------------------------------------------


def test_annual_ceiling_split_conserves_volume() -> None:
    decadal = 5_130_563.706928495
    annual = rh.annual_ceiling(decadal, 10)

    assert annual == decadal / 10
    assert sum([annual] * 10) == pytest.approx(decadal, rel=1e-6)


def test_annual_ceiling_rejects_invalid_inputs() -> None:
    with pytest.raises(rh.RHError):
        rh.annual_ceiling(100.0, 0)
    with pytest.raises(rh.RHError):
        rh.annual_ceiling(-1.0, 10)


# --- state advance invariants ------------------------------------------------


def test_advance_ages_survivors_by_period_and_clamps_at_cap() -> None:
    state = _state([_row(25, 100.0), _row(190, 40.0), _row(195, 60.0)])

    new_state, _totals = _advance(state, [tuple([0.0]) * 10] * 3)

    ages = {
        int(row.age): float(row.area_ha) for row in new_state.itertuples(index=False)
    }
    live_25 = _live_fraction_end(tuple([0.0]) * 10, 10)
    # 25 -> 35; 190 and 195 both clamp into the absorbing cap class 195.
    assert ages[35] == pytest.approx(100.0 * live_25, rel=1e-9)
    assert ages[195] == pytest.approx(100.0 * live_25, rel=1e-9)
    # Burned-but-unsalvaged area regenerates at age 5 (no harvest here).
    assert ages[5] == pytest.approx(200.0 * (1.0 - live_25), rel=1e-9)
    assert all(age % 10 == 5 for age in ages)


def test_advance_conserves_area_to_1e6() -> None:
    state = _state([_row(25, 100.0), _row(85, 250.0)])
    harvests = [tuple([0.05]) * 10, tuple([0.0]) * 10]

    new_state, _totals = _advance(state, harvests)

    assert new_state["area_ha"].sum() == pytest.approx(
        state["area_ha"].sum(), rel=1e-6
    )


def test_advance_regen_at_age_5_for_harvested_and_unsalvaged_burned() -> None:
    state = _state([_row(65, 100.0)])
    harvest = tuple([0.05]) * 10  # 50% harvested over the decade

    new_state, totals = _advance(state, [harvest])

    live_end = _live_fraction_end(harvest, 10)
    regen = new_state[new_state["age"] == 5]
    assert len(regen) == 1
    # Regen = harvested + unsalvaged burned = 1 - live_end (no salvage).
    assert float(regen["area_ha"].iloc[0]) == pytest.approx(
        100.0 * (1.0 - live_end), rel=1e-9
    )
    assert totals["area_harvested_ha"] == pytest.approx(50.0, rel=1e-9)
    assert totals["area_burned_ha"] > 0.0


def test_advance_no_double_count_of_regen_area() -> None:
    # Two source cohorts in the same stratum both feed regen: the aggregated
    # age-5 area must equal the independently recomputed removed area, once.
    state = _state([_row(65, 100.0), _row(75, 200.0)])
    harvest = tuple([0.05]) * 10

    new_state, _totals = _advance(state, [harvest, harvest])

    live_end = _live_fraction_end(harvest, 10)
    expected_regen = (100.0 + 200.0) * (1.0 - live_end)
    regen_rows = new_state[new_state["age"] == 5]
    assert len(regen_rows) == 1
    assert float(regen_rows["area_ha"].iloc[0]) == pytest.approx(
        expected_regen, rel=1e-9
    )
    # And the whole table still conserves area exactly once.
    assert new_state["area_ha"].sum() == pytest.approx(300.0, rel=1e-6)


def test_advance_salvaged_area_regenerates() -> None:
    state = _state([_row(65, 100.0)])
    harvest = tuple([0.0]) * 10
    salvage = tuple([0.002]) * 10  # within the burn-influx ceiling of ~0.01/yr

    new_state, totals = _advance(state, [harvest], salvages=[salvage])

    years = fire.simulate_cohort_years(
        initial_live=1.0,
        burn_rate=BURN_RATE_SBPS,
        harvest_schedule=harvest,
        salvage_schedule=salvage,
    )
    expected_regen = 100.0 * (1.0 - years[-1].live_after)
    regen = float(new_state.loc[new_state["age"] == 5, "area_ha"].iloc[0])
    assert regen == pytest.approx(expected_regen, rel=1e-9)
    assert totals["area_salvaged_ha"] == pytest.approx(2.0, rel=1e-9)


def test_advance_schedule_shape_mismatch_fails() -> None:
    state = _state([_row(25, 100.0)])

    with pytest.raises(rh.RHError) as excinfo:
        _advance(state, [tuple([0.0]) * 10, tuple([0.0]) * 10])
    assert excinfo.value.code == "rh_schedule_shape_mismatch"

    with pytest.raises(rh.RHError) as excinfo:
        _advance(state, [tuple([0.0]) * 5])
    assert excinfo.value.code == "rh_schedule_shape_mismatch"


def test_advance_unknown_stratum_fails_fast() -> None:
    state = _state([_row(25, 100.0, stratum="xxx_zz")])

    with pytest.raises(rh.RHError) as excinfo:
        _advance(state, [tuple([0.0]) * 10])

    assert excinfo.value.code == "rh_burn_rate_unknown"


def test_advance_missing_curve_cap_fails_fast() -> None:
    state = _state([_row(25, 100.0)])
    bad_state = state.copy()
    bad_state["curve_id"] = 9999999

    with pytest.raises(rh.RHError) as excinfo:
        _advance(bad_state, [tuple([0.0]) * 10])

    assert excinfo.value.code == "rh_curve_age_cap_missing"


def test_advance_fraction_over_allocation_fails_fast() -> None:
    # Harvest 11%/yr: the live balance turns negative well before year 10
    # (fire also burns the remainder), so the fire replay must fail fast.
    state = _state([_row(25, 100.0)])

    with pytest.raises(rh.RHError) as excinfo:
        _advance(state, [tuple([0.11]) * 10])

    assert excinfo.value.code == "rh_fire_simulation_failed"


# --- WS3 ceiling flow-through into the principal LP --------------------------


def _cohort(cohort_id: str, green_volume: float) -> principal.PrincipalCohort:
    return principal.PrincipalCohort(
        cohort_id=cohort_id,
        stratum_code=STRATUM,
        development_type="SPF_SBPS",
        area_ha=100.0,
        green_volume_m3=green_volume,
        burned_volume_m3=0.0,
        cashflow=green_volume * 10.0,
        burned_value=0.0,
        burn_rate=0.0,
    )


def test_ws3_ceiling_flows_through_principal_lp() -> None:
    # Positive cashflow makes the LP offer as much as allowed; the per-cohort
    # annual ceiling (WS3 decadal/10) must bind below the global AAC.
    cohorts = [_cohort("managed:2901000:sbps_pli:2921000:65", 10_000.0)]
    decadal = 4_000.0
    annual = rh.annual_ceiling(decadal, 10)

    result = principal.solve_principal(
        cohorts,
        horizon=10,
        aac_annual_m3=1_000_000.0,
        cohort_ceilings_m3=[annual],
    )

    for volumes in result.per_year_volumes:
        assert volumes.green_volume_m3 <= annual + 1e-6
    offered_total = sum(v.green_volume_m3 for v in result.per_year_volumes)
    # Positive cashflow maxes out the ceiling every year: decadal total
    # equals the WS3 decadal volume the ceiling was split from.
    assert offered_total == pytest.approx(decadal, rel=1e-6)


def test_zero_ceiling_blocks_offers() -> None:
    cohorts = [_cohort("managed:2901000:sbps_pli:2921000:65", 10_000.0)]

    result = principal.solve_principal(
        cohorts, horizon=10, aac_annual_m3=1_000_000.0, cohort_ceilings_m3=[0.0]
    )

    assert sum(v.green_volume_m3 for v in result.per_year_volumes) == 0.0


def test_principal_rejects_malformed_ceilings() -> None:
    cohorts = [_cohort("managed:2901000:sbps_pli:2921000:65", 10_000.0)]

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp(cohorts, horizon=10, cohort_ceilings_m3=[1.0, 2.0])
    assert excinfo.value.code == "principal_invalid_cohort_ceilings"

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp(cohorts, horizon=10, cohort_ceilings_m3=[-1.0])
    assert excinfo.value.code == "principal_invalid_cohort_ceilings"


# --- rh config ---------------------------------------------------------------


def test_rh_config_yaml_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "rh.yaml"
    config_path.write_text(
        "run_id: rh-test\n"
        f"stands_path: {tmp_path / 'stands.parquet'}\n"
        f"yields_path: {tmp_path / 'yields.csv'}\n"
        f"bridge_path: {tmp_path / 'bridge'}\n"
        "base_year: 2025\n"
        "horizon: 15\n"
        "period_length: 10\n"
        "steps: 10\n"
        "workers: 64\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    config = RHRunConfig.read(config_path)

    assert config.run_id == "rh-test"
    assert config.horizon == 15
    assert config.period_length == 10
    assert config.steps == 10
    assert config.decay_rate == 0.85
    assert config.discount_rate == 0.03
    assert config.objective.utilization == 0.85
    assert config.burned_limit_annual_m3 is None


def test_rh_config_rejects_non_positive_steps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        RHRunConfig(
            stands_path=tmp_path / "s.parquet",
            yields_path=tmp_path / "y.csv",
            bridge_path=tmp_path / "b",
            steps=0,
            output_root=tmp_path / "out",
        )


# --- 2-step end-to-end determinism (opt-in integration) ----------------------

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
    from fresh_salvage import ws3 as fs_ws3

    for path in (STANDS_PATH, STAGE1_YIELDS_PATH, fs_ws3.CANONICAL_TSA29_BRIDGE):
        if not path.exists():
            pytest.skip(f"local TSA29 input unavailable: {path}")


def _comparable_step_fields(record: dict) -> dict:
    """Strip wall-clock fields; the LP/schedule numbers must match exactly."""

    timing_fields = {
        "ws3_build_seconds",
        "ws3_solve_seconds",
        "principal_solve_seconds",
        "agent_solve_seconds",
        "wall_seconds",
    }
    return {key: value for key, value in record.items() if key not in timing_fields}


def test_two_step_end_to_end_determinism(tmp_path: Path) -> None:
    _require_integration_inputs()
    from fresh_salvage import ws3 as fs_ws3

    def _config(run_id: str, output_root: Path) -> RHRunConfig:
        return RHRunConfig(
            run_id=run_id,
            stands_path=STANDS_PATH,
            yields_path=STAGE1_YIELDS_PATH,
            bridge_path=fs_ws3.CANONICAL_TSA29_BRIDGE,
            base_year=2025,
            horizon=3,
            period_length=10,
            steps=2,
            workers=1,  # serial tree generation keeps LP assembly order fixed
            output_root=output_root,
        )

    first = rh.run_rh(_config("rh-e2e-a", tmp_path / "run-a"))
    second = rh.run_rh(_config("rh-e2e-b", tmp_path / "run-b"))

    assert first.status == "optimal"
    assert len(first.step_records) == 2
    assert [
        _comparable_step_fields(record.model_dump()) for record in first.step_records
    ] == [
        _comparable_step_fields(record.model_dump()) for record in second.step_records
    ]
    # Steps JSONL round-trips and matches the in-memory trajectory.
    lines = first.steps_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["step"] == 1
