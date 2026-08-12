"""Annual burn-rate table keyed by BEC zone (mean fire return interval).

Phase 3 consumes these rates in the principal LP objective (expected burned
wood loss); Phase 4 extends this module with the annual fire simulation that
converts standing inventory into salvage supply at the same DT-wise rate.

The annual burn probability of a development type is ``1 / MFRI`` where MFRI
is the mean fire return interval (years) of its BEC zone. All helpers are
pure functions over the exported constants: no I/O, no hidden state.
"""

from __future__ import annotations

# Mean fire return interval (years) per BEC zone.
MFRI_YEARS_BY_ZONE: dict[str, int] = {
    "SBPS": 100,
    "IDF": 200,
    "MS": 150,
    "ESSF": 200,
    "ICH": 250,
    "SBS": 125,
}

# Annual burn probability per BEC zone: R[zone] = 1 / MFRI[zone].
ANNUAL_BURN_RATE_BY_ZONE: dict[str, float] = {
    zone: 1.0 / mfri for zone, mfri in MFRI_YEARS_BY_ZONE.items()
}


class UnknownBurnRateError(ValueError):
    """Raised when a BEC zone or stratum has no MFRI table entry."""


def annual_burn_rate(bec_zone: str) -> float:
    """Return the annual burn probability ``1 / MFRI`` of a BEC zone.

    Zone matching is case-insensitive. Raises ``UnknownBurnRateError`` for
    blank or unmapped zones: an unknown fire regime must halt the pipeline,
    never silently default to a neighbouring zone's rate.
    """

    zone = str(bec_zone).strip().upper()
    if not zone:
        raise UnknownBurnRateError("BEC zone must be a non-empty string")
    if zone not in ANNUAL_BURN_RATE_BY_ZONE:
        known = ", ".join(sorted(ANNUAL_BURN_RATE_BY_ZONE))
        raise UnknownBurnRateError(
            f"no MFRI entry for BEC zone {zone!r}; mapped zones: {known}"
        )
    return ANNUAL_BURN_RATE_BY_ZONE[zone]


def bec_zone_from_stratum(stratum_code: str) -> str:
    """Extract the BEC zone prefix of a WS3 bridge stratum code.

    Bridge stratum codes are ``{bec_zone}_{leading_species}`` in lowercase
    (e.g. ``sbps_pli`` -> ``SBPS``, ``idf_fd`` -> ``IDF``). Raises
    ``UnknownBurnRateError`` for malformed codes.
    """

    text = str(stratum_code).strip()
    prefix, separator, _species = text.partition("_")
    if not separator or not prefix:
        raise UnknownBurnRateError(
            f"stratum code {stratum_code!r} does not follow "
            "'{bec_zone}_{leading_species}'"
        )
    return prefix.upper()


def annual_burn_rate_for_stratum(stratum_code: str) -> float:
    """Return the annual burn probability of a WS3 bridge stratum code.

    Chains :func:`bec_zone_from_stratum` and :func:`annual_burn_rate`, so a
    stratum whose BEC zone has no MFRI entry fails fast with a descriptive
    error.
    """

    return annual_burn_rate(bec_zone_from_stratum(stratum_code))


__all__ = [
    "ANNUAL_BURN_RATE_BY_ZONE",
    "MFRI_YEARS_BY_ZONE",
    "UnknownBurnRateError",
    "annual_burn_rate",
    "annual_burn_rate_for_stratum",
    "bec_zone_from_stratum",
]
