"""MFRI fire-rate table tests (pure functions, synthetic identifiers only)."""

import pytest

from fresh_salvage import fire


def test_mfri_table_matches_specification() -> None:
    assert fire.MFRI_YEARS_BY_ZONE == {
        "SBPS": 100,
        "IDF": 200,
        "MS": 150,
        "ESSF": 200,
        "ICH": 250,
        "SBS": 125,
    }


def test_annual_burn_rate_is_inverse_mfri() -> None:
    for zone, mfri in fire.MFRI_YEARS_BY_ZONE.items():
        assert fire.annual_burn_rate(zone) == pytest.approx(1.0 / mfri)
        assert fire.ANNUAL_BURN_RATE_BY_ZONE[zone] == pytest.approx(1.0 / mfri)


def test_annual_burn_rate_is_case_insensitive() -> None:
    assert fire.annual_burn_rate("sbps") == pytest.approx(0.01)
    assert fire.annual_burn_rate(" Idf ") == pytest.approx(0.005)


def test_annual_burn_rate_fails_fast_on_unknown_zone() -> None:
    with pytest.raises(fire.UnknownBurnRateError, match="no MFRI entry"):
        fire.annual_burn_rate("CWH")
    with pytest.raises(fire.UnknownBurnRateError, match="non-empty"):
        fire.annual_burn_rate("   ")


def test_bec_zone_from_stratum_extracts_prefix() -> None:
    assert fire.bec_zone_from_stratum("sbps_pli") == "SBPS"
    assert fire.bec_zone_from_stratum("ESSF_BL") == "ESSF"


def test_bec_zone_from_stratum_fails_fast_on_malformed_code() -> None:
    with pytest.raises(fire.UnknownBurnRateError, match="bec_zone"):
        fire.bec_zone_from_stratum("sbps")
    with pytest.raises(fire.UnknownBurnRateError, match="bec_zone"):
        fire.bec_zone_from_stratum("_pli")


def test_annual_burn_rate_for_stratum_chains_zone_lookup() -> None:
    assert fire.annual_burn_rate_for_stratum("sbps_pli") == pytest.approx(1.0 / 100)
    assert fire.annual_burn_rate_for_stratum("ich_cw") == pytest.approx(1.0 / 250)


def test_annual_burn_rate_for_stratum_fails_fast_on_unmapped_zone() -> None:
    with pytest.raises(fire.UnknownBurnRateError, match="no MFRI entry"):
        fire.annual_burn_rate_for_stratum("cwh_fd")


def test_burn_influx_is_rate_times_post_harvest_remainder() -> None:
    assert fire.burn_influx(80.0, 0.25) == pytest.approx(20.0)
    assert fire.burn_influx(0.0, 0.25) == 0.0
    with pytest.raises(fire.FireDynamicsError, match="negative"):
        fire.burn_influx(-1.0, 0.25)
    with pytest.raises(fire.FireDynamicsError, match="burn_rate"):
        fire.burn_influx(80.0, 1.5)


def test_salvageable_volume_is_carry_in_plus_influx() -> None:
    assert fire.salvageable_volume(12.0, 8.0) == pytest.approx(20.0)
    with pytest.raises(fire.FireDynamicsError, match="negative"):
        fire.salvageable_volume(-1.0, 0.0)


def test_live_volume_after_subtracts_harvest_and_influx() -> None:
    assert fire.live_volume_after(100.0, 40.0, 15.0) == pytest.approx(45.0)
    with pytest.raises(fire.FireDynamicsError, match="negative"):
        fire.live_volume_after(-1.0, 0.0, 0.0)


def test_burned_volume_after_decays_unsalvaged_volume_by_point_eight_five() -> None:
    # (10 carried in + 5 influx - 3 salvaged) retained at 0.85.
    assert fire.burned_volume_after(10.0, 5.0, 3.0, 0.85) == pytest.approx(10.2)
    assert fire.burned_volume_after(10.0, 5.0, 15.0, 0.85) == 0.0
    with pytest.raises(fire.FireDynamicsError, match="decay_rate"):
        fire.burned_volume_after(0.0, 0.0, 0.0, 1.5)


def test_simulate_applies_harvest_before_fire_ordering() -> None:
    states = fire.simulate_cohort_years(
        initial_live=100.0,
        burn_rate=0.25,
        harvest_schedule=[40.0],
        salvage_schedule=[0.0],
    )

    year_one = states[0]
    assert year_one.exposed == pytest.approx(60.0)
    assert year_one.burn_influx == pytest.approx(15.0)  # 0.25 * (100 - 40)
    assert year_one.live_after == pytest.approx(45.0)
    assert year_one.salvageable == pytest.approx(15.0)
    assert year_one.burned_after == pytest.approx(0.85 * 15.0)


def test_simulate_conserves_volume_over_the_horizon() -> None:
    states = fire.simulate_cohort_years(
        initial_live=100.0,
        burn_rate=0.3,
        harvest_schedule=[10.0, 5.0, 0.0],
        salvage_schedule=[2.0, 1.0, 1.0],
        decay_rate=0.85,
    )

    accounted = (
        states[-1].live_after
        + states[-1].burned_after
        + sum(state.harvested for state in states)
        + sum(state.salvaged for state in states)
        + sum(state.decayed for state in states)
    )
    assert accounted == pytest.approx(100.0, abs=1e-9)
    for state in states:
        assert state.live_after == pytest.approx(
            state.live_before - state.harvested - state.burn_influx
        )
        assert state.burned_after == pytest.approx(
            (state.burned_before + state.burn_influx - state.salvaged) * 0.85
        )
        assert state.decayed == pytest.approx(
            (state.burned_before + state.burn_influx - state.salvaged) * 0.15
        )


def test_simulate_fails_fast_on_infeasible_schedules() -> None:
    with pytest.raises(fire.FireDynamicsError, match="equal length"):
        fire.simulate_cohort_years(
            initial_live=100.0,
            burn_rate=0.1,
            harvest_schedule=[1.0, 1.0],
            salvage_schedule=[1.0],
        )
    with pytest.raises(fire.FireDynamicsError, match="exceeds the standing"):
        fire.simulate_cohort_years(
            initial_live=100.0,
            burn_rate=0.1,
            harvest_schedule=[101.0],
            salvage_schedule=[0.0],
        )
    with pytest.raises(fire.FireDynamicsError, match="available burned inventory"):
        fire.simulate_cohort_years(
            initial_live=100.0,
            burn_rate=0.1,
            harvest_schedule=[0.0],
            salvage_schedule=[50.0],  # only 10 burned in year 1
        )
    with pytest.raises(fire.FireDynamicsError, match="initial live"):
        fire.simulate_cohort_years(
            initial_live=-1.0,
            burn_rate=0.1,
            harvest_schedule=[],
            salvage_schedule=[],
        )
