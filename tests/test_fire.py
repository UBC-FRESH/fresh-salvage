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
