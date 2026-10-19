"""Principal LP tests on synthetic toy instances with known optima.

Every fixture is a hand-built list of ``PrincipalCohort`` records; no real
TSA data is read. ``_solve`` wraps ``solve_principal`` with a high AAC and no
burned cap unless the test says otherwise.
"""

import dataclasses
import hashlib

import highspy
import numpy as np
import pandas as pd
import pytest

from fresh_salvage import data, principal
from fresh_salvage.principal import PrincipalCohort

TOLERANCE = 1e-6


def _cohort(
    cohort_id: str,
    *,
    green_volume_m3: float,
    burned_volume_m3: float = 0.0,
    cashflow: float,
    burned_value: float = 0.0,
    burn_rate: float = 0.0,
) -> PrincipalCohort:
    return PrincipalCohort(
        cohort_id=cohort_id,
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=green_volume_m3,
        burned_volume_m3=burned_volume_m3,
        cashflow=cashflow,
        burned_value=burned_value,
        burn_rate=burn_rate,
    )


def _solve(cohorts, *, horizon=3, aac=1e12, decay_rate=0.85, burned_limit=None):
    return principal.solve_principal(
        cohorts,
        horizon=horizon,
        aac_annual_m3=aac,
        decay_rate=decay_rate,
        burned_limit_annual_m3=burned_limit,
        run_id="toy",
    )


def test_annual_burned_value_uses_initial_mix_then_transition() -> None:
    cohort = PrincipalCohort(
        cohort_id="graded",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=100.0,
        cashflow=0.0,
        burned_value=7_600.0,
        burn_rate=0.1,
        burned_grade_mix=(0.2, 0.6, 0.2, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
    )

    values = principal._annual_burned_values(cohort, horizon=2)
    assert values[0] == pytest.approx(7_600.0)
    expected_mix_year_2 = data.annual_grade_mix(
        dict(zip(data.GRADE_ORDER, cohort.burned_grade_mix, strict=True)), horizon=2
    )[1]
    expected_value_year_2 = 100.0 * data.weighted_grade_price(
        expected_mix_year_2,
        dict(zip(data.GRADE_ORDER, cohort.burned_grade_prices, strict=True)),
    )
    assert values[1] == pytest.approx(expected_value_year_2)


def test_offer_coefficients_precompute_fire_and_decay_by_offer_year() -> None:
    cohort = PrincipalCohort(
        cohort_id="fire-decay",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=10.0,
        cashflow=1_472.5,
        burned_value=1_000.0,
        burn_rate=0.1,
        burned_grade_mix=(1.0, 0.0, 0.0, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
    )

    coefficients = principal._principal_offer_coefficients(cohort, 3, 0.85)

    assert coefficients.annual_burned_volume_m3 == pytest.approx((10.0, 17.0, 22.1))
    assert coefficients.annual_burned_price_m3 == pytest.approx((100.0, 87.0, 77.05))
    assert coefficients.annual_offer == pytest.approx((1472.5, 1303.25, 1154.225))
    assert coefficients.annual_burn_loss == pytest.approx(
        (0.0, 22.185, 47.25283875)
    )


def test_principal_lp_uses_annual_coefficients_without_new_columns() -> None:
    cohort = PrincipalCohort(
        cohort_id="wired-fire-decay",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=10.0,
        cashflow=1_472.5,
        burned_value=1_000.0,
        burn_rate=0.1,
        burned_grade_mix=(1.0, 0.0, 0.0, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
    )

    built = principal.build_principal_lp([cohort], horizon=3, decay_rate=0.85)
    costs = built.model.getLp().col_cost_

    assert list(costs[:3]) == pytest.approx((1472.5, 1303.25, 1154.225))
    assert list(costs[3:]) == pytest.approx((0.0, 22.185, 47.25283875))
    assert built.model.getNumCol() == 2 * 3


def test_fire_free_unit_decay_preserves_legacy_offer_coefficients() -> None:
    cohort = PrincipalCohort(
        cohort_id="legacy-settings",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=10.0,
        cashflow=1_472.5,
        burned_value=1_000.0,
        burn_rate=0.0,
        burned_grade_mix=(1.0, 0.0, 0.0, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
    )

    coefficients = principal._principal_offer_coefficients(cohort, 3, 1.0)

    assert coefficients.annual_burned_volume_m3 == pytest.approx((10.0, 10.0, 10.0))
    assert coefficients.annual_offer == pytest.approx((1472.5, 1472.5, 1472.5))
    assert coefficients.annual_burn_loss == pytest.approx((0.0, 0.0, 0.0))


def test_unit_decay_still_accumulates_new_fire_in_offer_coefficients() -> None:
    cohort = PrincipalCohort(
        cohort_id="unit-decay-fire",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=10.0,
        cashflow=1_472.5,
        burned_value=1_000.0,
        burn_rate=0.1,
        burned_grade_mix=(1.0, 0.0, 0.0, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
    )

    coefficients = principal._principal_offer_coefficients(cohort, 3, 1.0)

    assert coefficients.annual_burned_volume_m3 == pytest.approx((10.0, 20.0, 29.0))
    assert coefficients.annual_burn_loss == pytest.approx((0.0, 0.0, 0.0))


def _fractions(result):
    return {
        (offer.cohort_id, offer.year): offer.offer_fraction for offer in result.offers
    }


def test_offer_once_binds_with_positive_cashflow_and_no_loss() -> None:
    cohorts = [_cohort("c1", green_volume_m3=100.0, cashflow=500.0)]

    result = _solve(cohorts, horizon=3)
    fractions = _fractions(result)

    assert result.status == "optimal"
    # Cashflow is identical across years, so any split of the single allowed
    # offer is optimal; the offer-once row must bind exactly (cumulative = 1).
    assert sum(fractions[("c1", year)] for year in (1, 2, 3)) == pytest.approx(1.0)
    assert result.objective_value == pytest.approx(500.0)


def test_aac_ceiling_binds_and_prefers_higher_cashflow_density() -> None:
    cohorts = [
        _cohort("rich", green_volume_m3=100.0, cashflow=300.0),
        _cohort("poor", green_volume_m3=100.0, cashflow=100.0),
    ]

    result = _solve(cohorts, horizon=1, aac=100.0)
    fractions = _fractions(result)

    # The 100 m3 ceiling goes entirely to the better cashflow density.
    assert fractions[("rich", 1)] == pytest.approx(1.0)
    assert fractions[("poor", 1)] == pytest.approx(0.0, abs=TOLERANCE)
    assert result.per_year_volumes[0].green_volume_m3 == pytest.approx(100.0)
    assert result.objective_value == pytest.approx(300.0)


def test_zero_burn_rate_yields_zero_loss_term() -> None:
    cohorts = [
        _cohort(
            "c1",
            green_volume_m3=100.0,
            burned_volume_m3=40.0,
            cashflow=700.0,
            burned_value=2_000.0,
            burn_rate=0.0,
        )
    ]

    result = _solve(cohorts, horizon=4)

    # With R = 0 the expected loss is identically zero: the objective is pure
    # cashflow and offering is never delayed by the loss term.
    assert result.objective_value == pytest.approx(700.0)
    assert sum(
        offer.offer_fraction for offer in result.offers
    ) == pytest.approx(1.0)


def test_positive_burn_rate_prices_unoffered_decay() -> None:
    cohorts = [
        _cohort(
            "c1",
            green_volume_m3=100.0,
            burned_volume_m3=40.0,
            cashflow=700.0,
            burned_value=2_000.0,
            burn_rate=0.01,
        )
    ]

    result = _solve(cohorts, horizon=4, decay_rate=0.85)
    fractions = _fractions(result)

    # The year-1 loss is identically zero (1 - 0.85**0), so years 1 and 2 are
    # tied; the optimum offers the whole cohort by year 2 and strictly avoids
    # later years, keeping cum_offer = 1 before any positive loss accrues.
    assert fractions[("c1", 1)] + fractions[("c1", 2)] == pytest.approx(1.0)
    assert fractions[("c1", 3)] == pytest.approx(0.0, abs=TOLERANCE)
    assert fractions[("c1", 4)] == pytest.approx(0.0, abs=TOLERANCE)
    assert result.objective_value == pytest.approx(700.0)


def test_unprofitable_cohort_is_never_offered_and_loss_is_charged() -> None:
    cohorts = [
        _cohort(
            "negative",
            green_volume_m3=100.0,
            burned_volume_m3=40.0,
            cashflow=-50.0,
            burned_value=2_000.0,
            burn_rate=0.01,
        )
    ]

    result = _solve(cohorts, horizon=3, decay_rate=0.85)

    fractions = _fractions(result)
    assert all(
        fractions[("negative", year)] == pytest.approx(0.0, abs=TOLERANCE)
        for year in (1, 2, 3)
    )
    # Objective = -sum_y R * burned_value * (1 - 0.85**(y-1)), y = 1..3.
    expected_loss = -sum(0.01 * 2_000.0 * (1.0 - 0.85 ** (year - 1)) for year in (1, 2, 3))
    assert result.objective_value == pytest.approx(expected_loss)


def test_lp_has_no_integer_variables() -> None:
    cohorts = [
        _cohort("c1", green_volume_m3=100.0, cashflow=500.0),
        _cohort("c2", green_volume_m3=200.0, cashflow=900.0),
    ]

    built = principal.build_principal_lp(cohorts, horizon=3)
    integrality = list(built.model.getLp().integrality_)

    # A pure LP carries either no integrality vector at all or only
    # continuous entries.
    assert all(
        entry == highspy.HighsIntegrality.kContinuous for entry in integrality
    )
    assert built.model.getNumCol() == 2 * len(cohorts) * 3


def test_solve_is_deterministic_across_runs() -> None:
    cohorts = [
        _cohort(
            "c1",
            green_volume_m3=100.0,
            burned_volume_m3=40.0,
            cashflow=700.0,
            burned_value=2_000.0,
            burn_rate=0.01,
        ),
        _cohort("c2", green_volume_m3=250.0, cashflow=100.0),
    ]

    first = _solve(cohorts, horizon=5, aac=180.0)
    second = _solve(cohorts, horizon=5, aac=180.0)

    assert first.objective_value == second.objective_value
    assert _fractions(first) == _fractions(second)


def test_offered_volume_never_exceeds_standing_volume() -> None:
    cohorts = [
        _cohort(
            "c1",
            green_volume_m3=120.0,
            burned_volume_m3=30.0,
            cashflow=800.0,
            burned_value=500.0,
            burn_rate=0.01,
        ),
        _cohort("c2", green_volume_m3=200.0, cashflow=100.0),
    ]

    result = _solve(cohorts, horizon=6, aac=90.0)
    fractions = _fractions(result)

    for cohort in cohorts:
        cumulative = sum(
            fractions[(cohort.cohort_id, year)] for year in range(1, 7)
        )
        assert cumulative <= 1.0 + TOLERANCE
    for volumes in result.per_year_volumes:
        assert volumes.green_volume_m3 <= 90.0 + TOLERANCE


def test_burned_limit_binds_annual_burned_volume() -> None:
    cohorts = [
        _cohort(
            "burned-heavy",
            green_volume_m3=50.0,
            burned_volume_m3=100.0,
            cashflow=900.0,
        ),
        _cohort("green-only", green_volume_m3=100.0, cashflow=200.0),
    ]

    result = _solve(cohorts, horizon=1, aac=1e12, burned_limit=50.0)
    fractions = _fractions(result)

    # The burned cap halves the burned-heavy offer; the green-only cohort is
    # unaffected.
    assert fractions[("burned-heavy", 1)] == pytest.approx(0.5)
    assert fractions[("green-only", 1)] == pytest.approx(1.0)
    assert result.per_year_volumes[0].burned_volume_m3 == pytest.approx(50.0)


def test_offer_table_emits_every_cohort_year_including_zeros() -> None:
    cohorts = [
        _cohort("c1", green_volume_m3=100.0, cashflow=-1.0),
        _cohort("c2", green_volume_m3=100.0, cashflow=-1.0),
    ]

    result = _solve(cohorts, horizon=4)

    assert len(result.offers) == 2 * 4
    assert result.offered_cohort_years == 0
    assert all(offer.offer_fraction == 0.0 for offer in result.offers)


def test_build_fails_fast_on_empty_cohorts() -> None:
    with pytest.raises(principal.PrincipalError, match="at least one cohort"):
        principal.build_principal_lp([], horizon=3)


def test_build_fails_fast_on_invalid_parameters() -> None:
    cohorts = [_cohort("c1", green_volume_m3=100.0, cashflow=1.0)]
    with pytest.raises(principal.PrincipalError, match="horizon"):
        principal.build_principal_lp(cohorts, horizon=0)
    with pytest.raises(principal.PrincipalError, match="aac"):
        principal.build_principal_lp(cohorts, horizon=1, aac_annual_m3=-1.0)
    with pytest.raises(principal.PrincipalError, match="decay"):
        principal.build_principal_lp(cohorts, horizon=1, decay_rate=1.5)
    with pytest.raises(principal.PrincipalError, match="burned_limit"):
        principal.build_principal_lp(cohorts, horizon=1, burned_limit_annual_m3=-1.0)


def test_expected_burn_losses_are_empty_without_fire_risk() -> None:
    no_risk = _cohort("a", green_volume_m3=1.0, cashflow=1.0)
    no_value = _cohort("b", green_volume_m3=1.0, cashflow=1.0, burn_rate=0.01)

    assert principal._expected_burn_losses(no_risk, 5, 0.85) == []
    assert principal._expected_burn_losses(no_value, 5, 0.85) == []


def test_load_cohorts_fails_fast_on_missing_inputs(tmp_path) -> None:
    from fresh_salvage.models import PrincipalRunConfig

    config = PrincipalRunConfig(
        stands_path=tmp_path / "missing.parquet",
        are_path=tmp_path / "missing.are",
        yields_path=tmp_path / "missing.csv",
        output_root=tmp_path / "out",
    )

    with pytest.raises(principal.PrincipalError, match="not found"):
        principal.load_cohorts(config)


def test_stratum_maps_to_stands_development_type() -> None:
    assert principal._development_type_from_stratum("sbps_pli") == "SPF_SBPS"
    assert principal._development_type_from_stratum("idf_fd") == "SPF_IDF"
    assert principal._development_type_from_stratum("essf_bl") == "SPF_ESSF"


def test_parse_offer_fraction_snaps_within_tolerance() -> None:
    assert principal._parse_offer_fraction(1.0 + 1e-12, 0) == 1.0
    assert principal._parse_offer_fraction(-1e-12, 0) == 0.0
    assert principal._parse_offer_fraction(0.25, 3) == 0.25


def test_parse_offer_fraction_fails_fast_beyond_tolerance() -> None:
    with pytest.raises(principal.PrincipalError) as excinfo:
        principal._parse_offer_fraction(1.0 + 1e-6, 7)
    assert excinfo.value.code == "principal_fraction_out_of_bounds"
    with pytest.raises(principal.PrincipalError) as excinfo:
        principal._parse_offer_fraction(-1e-6, 2)
    assert excinfo.value.code == "principal_fraction_out_of_bounds"


def _write_toy_run_config(tmp_path):
    """Write a tiny synthetic stands/ARE/yields triple and its config."""

    from fresh_salvage.models import PrincipalRunConfig

    stands_path = tmp_path / "stands.parquet"
    pd.DataFrame(
        [
            {
                "development_type": "SPF_SBPS",
                "Total_Green_Vol": 1_000.0,
                "Total_Burned_Vol": 200.0,
                **{column: 0.0 for column in data.BURNED_GRADE_COLUMNS},
            }
        ]
    ).to_parquet(stands_path, index=False)

    are_path = tmp_path / "toy.are"
    are_path.write_text(
        "toy ARE section\n*A 29 1 7 sbps_pli 101 45 120.5\n",
        encoding="utf-8",
    )

    yields_path = tmp_path / "yields.csv"
    pd.DataFrame(
        [
            {"curve_id": 101, "age": 0, "volume": 0.0},
            {"curve_id": 101, "age": 50, "volume": 250.0},
            {"curve_id": 101, "age": 100, "volume": 400.0},
        ]
    ).to_csv(yields_path, index=False)

    return PrincipalRunConfig(
        run_id="toy-e2e",
        stands_path=stands_path,
        are_path=are_path,
        yields_path=yields_path,
        horizon=3,
        output_root=tmp_path / "out",
    )


def test_run_principal_end_to_end_writes_artifacts_and_manifest(tmp_path) -> None:
    from fresh_salvage.models import PrincipalManifest

    config = _write_toy_run_config(tmp_path)

    result = principal.run_principal(config)

    assert result.status == "optimal"
    assert result.cohorts == 1
    assert result.data_path.is_file()
    assert result.csv_path.is_file()
    assert result.manifest_path.is_file()

    offers = pd.read_parquet(result.data_path)
    assert len(offers) == 3  # one cohort x three years, zeros included

    manifest = PrincipalManifest.read_json(result.manifest_path)
    expected_checksums = {
        "stands": hashlib.sha256(config.stands_path.read_bytes()).hexdigest(),
        "are": hashlib.sha256(config.are_path.read_bytes()).hexdigest(),
        "yields": hashlib.sha256(config.yields_path.read_bytes()).hexdigest(),
    }
    assert manifest.source_sha256 == expected_checksums
    assert manifest.status == "optimal"
    assert manifest.run_id == "toy-e2e"


# --- per-cohort per-year WS3 ceilings (Phase 8 P8.1) --------------------------


def _economic_cohort(cohort_id: str, green_volume_m3: float) -> PrincipalCohort:
    """Cohort with the full economic surface (fire, decay, grade-mix paths)."""

    burned_volume_m3 = 0.1 * green_volume_m3
    return PrincipalCohort(
        cohort_id=cohort_id,
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=10.0,
        green_volume_m3=green_volume_m3,
        burned_volume_m3=burned_volume_m3,
        cashflow=green_volume_m3 * 4.0,
        burned_value=burned_volume_m3 * 30.0,
        burn_rate=0.01,
        burned_grade_mix=(0.1, 0.7, 0.2, 0.0),
        burned_grade_prices=(60.0, 45.0, 20.0, 0.0),
        green_stumpage_rate=4.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
    )


def _lp_arrays(built: principal.PrincipalLP) -> dict[str, list[float]]:
    """Every array that defines a built HiGHS LP, for exact comparison."""

    lp = built.model.getLp()
    return {
        "col_cost": list(lp.col_cost_),
        "col_lower": list(lp.col_lower_),
        "col_upper": list(lp.col_upper_),
        "row_lower": list(lp.row_lower_),
        "row_upper": list(lp.row_upper_),
        "a_start": list(lp.a_matrix_.start_),
        "a_index": list(lp.a_matrix_.index_),
        "a_value": list(lp.a_matrix_.value_),
        "offset": [lp.offset_],
    }


def test_scalar_and_broadcast_ceilings_build_identical_lps() -> None:
    # RH equivalence: the rolling-horizon engine passes one scalar per
    # cohort; its per-year broadcast must build the bit-identical LP.
    horizon = 10
    cohorts = [_economic_cohort("a", 12_000.0), _economic_cohort("b", 7_500.0)]
    scalar_ceilings = [400.0 / 3.0, 0.0]

    scalar_lp = principal.build_principal_lp(
        cohorts, horizon=horizon, aac_annual_m3=5_000.0, cohort_ceilings_m3=scalar_ceilings
    )
    broadcast_lp = principal.build_principal_lp(
        cohorts,
        horizon=horizon,
        aac_annual_m3=5_000.0,
        cohort_ceilings_m3=[(ceiling,) * horizon for ceiling in scalar_ceilings],
    )

    assert _lp_arrays(scalar_lp) == _lp_arrays(broadcast_lp)
    assert scalar_lp.offer_columns == broadcast_lp.offer_columns


def test_per_year_ceilings_set_one_row_bound_per_cohort_year() -> None:
    horizon = 4
    cohorts = [_economic_cohort("a", 1_000.0), _economic_cohort("b", 2_000.0)]
    per_year = [(10.0, 20.0, 30.0, 40.0), (0.0, 5.0, 0.0, 5.0)]

    built = principal.build_principal_lp(
        cohorts, horizon=horizon, aac_annual_m3=1e9, cohort_ceilings_m3=per_year
    )

    # The ceiling rows are the last cohorts x horizon rows, cohort-major.
    lp = built.model.getLp()
    ceiling_bounds = list(lp.row_upper_)[-len(cohorts) * horizon :]
    assert ceiling_bounds == [bound for row in per_year for bound in row]


def test_per_year_ceilings_bind_each_year_in_the_solution() -> None:
    horizon = 4
    cohorts = [_cohort("a", green_volume_m3=1_000.0, cashflow=100.0)]
    per_year = [(10.0, 0.0, 30.0, 40.0)]

    result = principal.solve_principal(
        cohorts, horizon=horizon, aac_annual_m3=1e9, cohort_ceilings_m3=per_year
    )

    offered = [volumes.green_volume_m3 for volumes in result.per_year_volumes]
    assert offered == pytest.approx([10.0, 0.0, 30.0, 40.0], abs=TOLERANCE)


def test_scalar_and_sequence_ceilings_may_mix_across_cohorts() -> None:
    horizon = 3
    cohorts = [
        _cohort("a", green_volume_m3=100.0, cashflow=10.0),
        _cohort("b", green_volume_m3=100.0, cashflow=10.0),
    ]

    result = principal.solve_principal(
        cohorts, horizon=horizon, aac_annual_m3=1e9, cohort_ceilings_m3=[5.0, (1.0, 2.0, 3.0)]
    )

    offered = [volumes.green_volume_m3 for volumes in result.per_year_volumes]
    assert offered == pytest.approx([6.0, 7.0, 8.0], abs=TOLERANCE)


@pytest.mark.parametrize(
    "ceilings",
    [
        [(1.0, 2.0)],  # too short for horizon 3
        [(1.0, 2.0, 3.0, 4.0)],  # too long
        [(1.0, -2.0, 3.0)],  # negative year
        [(1.0, float("nan"), 3.0)],  # NaN year
        [float("nan")],  # NaN scalar
    ],
)
def test_malformed_per_year_ceilings_fail_fast(ceilings) -> None:
    cohorts = [_cohort("a", green_volume_m3=100.0, cashflow=10.0)]

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp(cohorts, horizon=3, cohort_ceilings_m3=ceilings)

    assert excinfo.value.code == "principal_invalid_cohort_ceilings"


# --- decadal growth, grade clock, burned-stock reset (Phase 8 P8.2/P8.3) -----


def _growth_cohort(
    cohort_id: str,
    *,
    per_ha: tuple[float, ...] = (100.0, 200.0),
    burn_rate: float = 0.1,
    burned_fraction: float = 0.1,
) -> PrincipalCohort:
    """Cohort with the full economic surface and a decadal growth surface."""

    area_ha = 10.0
    green_volume_m3 = area_ha * per_ha[0]
    burned_volume_m3 = burned_fraction * green_volume_m3
    return PrincipalCohort(
        cohort_id=cohort_id,
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=area_ha,
        green_volume_m3=green_volume_m3,
        burned_volume_m3=burned_volume_m3,
        cashflow=4.0 * green_volume_m3,
        # The "Other" grade never transitions, so the burned value stays a
        # clean 20 $/m3 every year: expected losses read directly off the
        # simulated burned volumes.
        burned_value=20.0 * burned_volume_m3,
        burn_rate=burn_rate,
        burned_grade_mix=(0.0, 0.0, 0.0, 1.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
        green_stumpage_rate=15.0,
        burned_stumpage_rate=0.25,
        subsidy_rate_per_m3=3.0,
        decadal_volume_scale=per_ha,
    )


def _row_entries(built: principal.PrincipalLP, row: int) -> dict[int, float]:
    """Return the nonzero ``{column: coefficient}`` entries of one LP row."""

    lp = built.model.getLp()
    start = lp.a_matrix_.start_[row]
    end = lp.a_matrix_.start_[row + 1]
    return {
        int(column): float(value)
        for column, value in zip(
            lp.a_matrix_.index_[start:end], lp.a_matrix_.value_[start:end], strict=True
        )
    }


def test_decadal_per_ha_volumes_interpolate_and_extend_unclamped() -> None:
    # The curve peaks at age 50 and declines afterwards; evaluating at
    # unclamped ages beyond the tabulated maximum 100 extends the last value
    # (90.0) instead of clamping to the RH curve age cap 95 (91.0).
    curves = {7: (np.array([0.0, 50.0, 100.0]), np.array([0.0, 100.0, 90.0]))}

    volumes = principal.decadal_per_ha_volumes(
        curves, [(7, 85)], horizon=30, period_length=10
    )

    assert len(volumes) == 1
    assert list(volumes[0]) == pytest.approx([93.0, 91.0, 90.0])


def test_decadal_per_ha_volumes_cover_every_decade_and_fail_fast() -> None:
    curves = {7: (np.array([0.0, 50.0]), np.array([0.0, 100.0]))}

    volumes = principal.decadal_per_ha_volumes(
        curves, [(7, 5)], horizon=25, period_length=10
    )

    assert len(volumes[0]) == 3  # ceil(25 / 10)

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.decadal_per_ha_volumes(
            curves, [(99, 5)], horizon=10, period_length=10
        )
    assert excinfo.value.code == "principal_yield_curve_missing"
    with pytest.raises(principal.PrincipalError, match="horizon"):
        principal.decadal_per_ha_volumes(curves, [(7, 5)], horizon=0, period_length=10)
    with pytest.raises(principal.PrincipalError, match="period_length"):
        principal.decadal_per_ha_volumes(curves, [(7, 5)], horizon=10, period_length=0)


def test_growth_scales_principal_annual_coefficients() -> None:
    cohort = _growth_cohort("growth")

    coefficients = principal._principal_offer_coefficients(
        cohort, 4, 0.85, period_length=2
    )

    # Decades are years 0-1 (v = 100 m3/ha) and 2-3 (v = 200 m3/ha). Burn
    # influx in decade k is R * live_area * v_k; burned stock carried across
    # the boundary stays in m3.
    assert coefficients.annual_burned_volume_m3 == pytest.approx(
        (100.0, 170.0, 221.0, 325.55)
    )
    assert coefficients.annual_offer == pytest.approx(
        (14725.0, 13032.5, 23692.25, 20974.7375)
    )
    assert coefficients.annual_burn_loss == pytest.approx(
        (0.0, 51.0, 122.655, 251.2432125)
    )


def test_burned_stock_reset_reseeds_and_restarts_the_decay_exponent() -> None:
    cohort = _growth_cohort("reset")

    coefficients = principal._principal_offer_coefficients(
        cohort, 4, 0.85, period_length=2, burned_stock_reset=True
    )

    # Decade 2 restarts from burned_volume_m3 * v_1 / v_0 = 200 m3, and the
    # annual_burn_loss exponent restarts at 0 (no loss in the decade's first
    # year) while the burn influx keeps flowing.
    assert coefficients.annual_burned_volume_m3 == pytest.approx(
        (100.0, 170.0, 200.0, 307.7)
    )
    assert coefficients.annual_burn_loss == pytest.approx((0.0, 51.0, 0.0, 92.31))
    assert coefficients.annual_offer == pytest.approx(
        (14725.0, 13032.5, 23750.0, 21023.825)
    )


def test_grade_clock_indexes_the_mix_at_year_modulo_period() -> None:
    cohort = PrincipalCohort(
        cohort_id="clock",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=1.0,
        green_volume_m3=100.0,
        burned_volume_m3=100.0,
        cashflow=0.0,
        burned_value=7_600.0,
        burn_rate=0.0,
        burned_grade_mix=(0.2, 0.6, 0.2, 0.0),
        burned_grade_prices=(100.0, 80.0, 40.0, 20.0),
    )

    clocked = principal._annual_burned_values(cohort, 4, grade_clock_period=2)
    unclocked = principal._annual_burned_values(cohort, 4)

    assert clocked[0] == pytest.approx(unclocked[0])
    assert clocked[1] == pytest.approx(unclocked[1])
    assert clocked[2] == pytest.approx(unclocked[0])
    assert clocked[3] == pytest.approx(unclocked[1])
    assert abs(unclocked[2] - unclocked[0]) > 1.0  # the clock really wraps


def test_growth_scales_the_legacy_simple_branch_cashflow() -> None:
    cohort = PrincipalCohort(
        cohort_id="simple-growth",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=10.0,
        green_volume_m3=1000.0,
        burned_volume_m3=100.0,
        cashflow=500.0,
        burned_value=1000.0,
        burn_rate=0.0,
        decadal_volume_scale=(100.0, 200.0),
    )

    coefficients = principal._principal_offer_coefficients(
        cohort, 4, 0.85, period_length=2
    )

    # Year 0 is the parsed t=0 volume; later decades double the per-ha
    # volume, so the cashflow and the static burned volume scale by 2.
    assert coefficients.annual_offer == pytest.approx((500.0, 500.0, 1000.0, 1000.0))
    assert coefficients.annual_burned_volume_m3 == pytest.approx(
        (100.0, 100.0, 200.0, 200.0)
    )


def test_growth_scales_aac_burned_cap_and_ceiling_row_coefficients() -> None:
    cohort = _growth_cohort("rows", burn_rate=0.0)
    horizon = 4

    built = principal.build_principal_lp(
        [cohort],
        horizon=horizon,
        aac_annual_m3=1e9,
        burned_limit_annual_m3=1e9,
        cohort_ceilings_m3=[(0.0,) * horizon],
        period_length=2,
    )

    # Row order: horizon cum_offer rows, one offer_once row, then the AAC and
    # burned-cap rows interleaved per year, then the horizon ceiling rows.
    aac_rows = [_row_entries(built, horizon + 1 + 2 * year) for year in range(horizon)]
    burned_rows = [
        _row_entries(built, horizon + 2 + 2 * year) for year in range(horizon)
    ]
    ceiling_rows = [
        _row_entries(built, 5 + 2 * horizon + year) for year in range(horizon)
    ]

    assert aac_rows[0] == {0: 1000.0}
    assert aac_rows[2] == {2: 2000.0}
    assert burned_rows[0] == {0: 100.0}
    assert burned_rows[2] == {2: 200.0}
    assert ceiling_rows[0] == {0: 1000.0}
    assert ceiling_rows[3] == {3: 2000.0}


def test_growth_solve_offers_the_grown_volume_once() -> None:
    cohort = PrincipalCohort(
        cohort_id="solve-growth",
        stratum_code="sbps_pli",
        development_type="SPF_SBPS",
        area_ha=10.0,
        green_volume_m3=1000.0,
        burned_volume_m3=0.0,
        cashflow=1000.0,
        burned_value=0.0,
        burn_rate=0.0,
        decadal_volume_scale=(100.0, 200.0),
    )

    static_cohort = dataclasses.replace(cohort, decadal_volume_scale=None)
    grown = principal.solve_principal(
        [cohort], horizon=4, aac_annual_m3=1e12, period_length=2
    )
    static = principal.solve_principal(
        [static_cohort], horizon=4, aac_annual_m3=1e12
    )

    # A decade-2 offer is worth its grown volume (2 x 1000 m3), so the LP
    # takes the whole single offer there instead of at the t=0 volume.
    assert grown.objective_value == pytest.approx(2_000.0)
    assert static.objective_value == pytest.approx(1_000.0)
    assert sum(
        volumes.green_volume_m3 for volumes in grown.per_year_volumes
    ) == pytest.approx(2_000.0)
    assert sum(
        volumes.green_volume_m3 for volumes in static.per_year_volumes
    ) == pytest.approx(1_000.0)


def test_growth_parameter_validation_fails_fast() -> None:
    cohort = _growth_cohort("growth")

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp([cohort], horizon=4)
    assert excinfo.value.code == "principal_invalid_decadal_volume_scale"

    for scale in [(100.0,), (100.0, -1.0), (100.0, float("nan"))]:
        malformed = dataclasses.replace(cohort, decadal_volume_scale=scale)
        with pytest.raises(principal.PrincipalError) as excinfo:
            principal.build_principal_lp([malformed], horizon=4, period_length=2)
        assert excinfo.value.code == "principal_invalid_decadal_volume_scale"

    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp([cohort], horizon=4, period_length=0)
    assert excinfo.value.code == "principal_invalid_period_length"
    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp([cohort], horizon=4, grade_clock_period=0)
    assert excinfo.value.code == "principal_invalid_grade_clock"
    with pytest.raises(principal.PrincipalError) as excinfo:
        principal.build_principal_lp([cohort], horizon=4, burned_stock_reset=True)
    assert excinfo.value.code == "principal_invalid_burned_stock_reset"
