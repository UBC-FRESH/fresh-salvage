"""Principal LP tests on synthetic toy instances with known optima.

Every fixture is a hand-built list of ``PrincipalCohort`` records; no real
TSA data is read. ``_solve`` wraps ``solve_principal`` with a high AAC and no
burned cap unless the test says otherwise.
"""

import hashlib

import highspy
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
