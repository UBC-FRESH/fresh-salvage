"""Annual burn-rate table and fire-dynamics helpers (mean fire return interval).

Phase 3 consumes the burn rates in the principal LP objective (expected
burned wood loss); Phase 4 consumes both the rates and the annual dynamics
below in the agent LP and in any standalone fire simulation, so the LP rows
and the simulation share one source of truth for the dynamics.

The annual burn probability of a development type is ``1 / MFRI`` where MFRI
is the mean fire return interval (years) of its BEC zone.

Annual dynamics (per 1-year timestep ``t``, per cohort)
-------------------------------------------------------
Ordering within one timestep is harvest -> fire -> salvage -> decay:

- exposed-to-burn volume ``V_rem[t] = V[t-1] - H[t]`` (harvest first: volume
  harvested in year ``t`` is no longer exposed to that year's fire);
- burn influx ``BURN_IN[t] = R * V_rem[t]`` with ``R = 1 / MFRI[zone]``;
- live balance ``V[t] = V[t-1] - H[t] - BURN_IN[t]``;
- salvage feasibility ``S[t] <= B[t-1] + BURN_IN[t]`` (only the burned
  inventory on hand after this year's fire can be salvaged);
- burned inventory ``B[t] = (B[t-1] + BURN_IN[t] - S[t]) * decay_rate`` with
  ``decay_rate = 0.85`` (unsalvaged burned volume retains 85% per year).

The primitive helpers operate on any consistent unit (fractions of standing
volume inside the LP, m3 in a simulation); ``simulate_cohort_years`` drives a
full horizon and fails fast on infeasible harvest/salvage schedules. All
helpers are pure functions: no I/O, no hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass

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

# Annual retention fraction of unsalvaged burned volume (decay 0.15/yr).
DEFAULT_BURNED_DECAY_RATE = 0.85

# Float dust tolerated when checking harvest/salvage schedule feasibility.
SCHEDULE_TOLERANCE = 1e-9


class UnknownBurnRateError(ValueError):
    """Raised when a BEC zone or stratum has no MFRI table entry."""


class FireDynamicsError(ValueError):
    """Raised when a fire-simulation input or schedule is infeasible."""


@dataclass(frozen=True)
class FireYearState:
    """Trusted state snapshot of one cohort after one annual timestep.

    All volumes share the caller's unit (m3 or fractions of the initial
    standing volume). ``live_after``/``burned_after`` are the end-of-year
    inventories that feed the next year's ``live_before``/``burned_before``.
    """

    year: int
    live_before: float
    harvested: float
    exposed: float
    burn_influx: float
    burned_before: float
    salvageable: float
    salvaged: float
    decayed: float
    live_after: float
    burned_after: float


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


def burn_influx(remaining_live: float, burn_rate: float) -> float:
    """Return ``BURN_IN[t] = burn_rate * V_rem[t]`` (fire after harvest).

    ``remaining_live`` is the live volume still standing after this year's
    harvest, ``V_rem[t] = V[t-1] - H[t]``.
    """

    _require_fraction(burn_rate, "burn_rate")
    if remaining_live < 0.0:
        raise FireDynamicsError(
            f"exposed live volume cannot be negative: {remaining_live}"
        )
    return burn_rate * remaining_live


def salvageable_volume(burned_before: float, influx: float) -> float:
    """Return the salvage ceiling ``B[t-1] + BURN_IN[t]`` for one year.

    Salvage in year ``t`` may draw on the decayed burned inventory carried
    in plus this year's burn influx, nothing more.
    """

    if burned_before < 0.0:
        raise FireDynamicsError(
            f"burned inventory cannot be negative: {burned_before}"
        )
    if influx < 0.0:
        raise FireDynamicsError(f"burn influx cannot be negative: {influx}")
    return burned_before + influx


def live_volume_after(live_before: float, harvested: float, influx: float) -> float:
    """Return the live balance ``V[t] = V[t-1] - H[t] - BURN_IN[t]``."""

    if live_before < 0.0:
        raise FireDynamicsError(f"live volume cannot be negative: {live_before}")
    if harvested < 0.0:
        raise FireDynamicsError(f"harvested volume cannot be negative: {harvested}")
    if influx < 0.0:
        raise FireDynamicsError(f"burn influx cannot be negative: {influx}")
    return live_before - harvested - influx


def burned_volume_after(
    burned_before: float,
    influx: float,
    salvaged: float,
    decay_rate: float,
) -> float:
    """Return ``B[t] = (B[t-1] + BURN_IN[t] - S[t]) * decay_rate``.

    ``decay_rate`` is the annual retention fraction of unsalvaged burned
    volume (0.85 default: 15% of the on-hand burned volume decays away each
    year).
    """

    _require_fraction(decay_rate, "decay_rate")
    if salvaged < 0.0:
        raise FireDynamicsError(f"salvaged volume cannot be negative: {salvaged}")
    unsalvaged = salvageable_volume(burned_before, influx) - salvaged
    return unsalvaged * decay_rate


def simulate_cohort_years(
    *,
    initial_live: float,
    burn_rate: float,
    harvest_schedule: list[float] | tuple[float, ...],
    salvage_schedule: list[float] | tuple[float, ...],
    decay_rate: float = DEFAULT_BURNED_DECAY_RATE,
    initial_burned: float = 0.0,
) -> list[FireYearState]:
    """Simulate the annual harvest -> fire -> salvage -> decay ordering.

    Pure driver over the primitive helpers above: given per-year harvest and
    salvage schedules (same length, same unit as ``initial_live``), return
    one :class:`FireYearState` per year. Raises ``FireDynamicsError`` when a
    schedule harvests more than the standing live volume or salvages more
    than the on-hand burned inventory (beyond float dust).
    """

    _require_fraction(burn_rate, "burn_rate")
    _require_fraction(decay_rate, "decay_rate")
    if initial_live < 0.0:
        raise FireDynamicsError(f"initial live volume cannot be negative: {initial_live}")
    if initial_burned < 0.0:
        raise FireDynamicsError(
            f"initial burned inventory cannot be negative: {initial_burned}"
        )
    if len(harvest_schedule) != len(salvage_schedule):
        raise FireDynamicsError(
            "harvest and salvage schedules must have equal length: "
            f"{len(harvest_schedule)} != {len(salvage_schedule)}"
        )

    states: list[FireYearState] = []
    live = float(initial_live)
    burned = float(initial_burned)
    for year_index, (harvested, salvaged) in enumerate(
        zip(harvest_schedule, salvage_schedule, strict=True), start=1
    ):
        if harvested < -SCHEDULE_TOLERANCE:
            raise FireDynamicsError(f"year {year_index}: negative harvest {harvested}")
        if salvaged < -SCHEDULE_TOLERANCE:
            raise FireDynamicsError(f"year {year_index}: negative salvage {salvaged}")
        harvested = max(0.0, harvested)
        salvaged = max(0.0, salvaged)
        if harvested > live + SCHEDULE_TOLERANCE:
            raise FireDynamicsError(
                f"year {year_index}: harvest {harvested} exceeds the standing "
                f"live volume {live}"
            )
        influx = burn_influx(live - harvested, burn_rate)
        salvageable = salvageable_volume(burned, influx)
        if salvaged > salvageable + SCHEDULE_TOLERANCE:
            raise FireDynamicsError(
                f"year {year_index}: salvage {salvaged} exceeds the available "
                f"burned inventory {salvageable}"
            )
        next_live = live_volume_after(live, harvested, influx)
        next_burned = burned_volume_after(burned, influx, salvaged, decay_rate)
        states.append(
            FireYearState(
                year=year_index,
                live_before=live,
                harvested=harvested,
                exposed=live - harvested,
                burn_influx=influx,
                burned_before=burned,
                salvageable=salvageable,
                salvaged=salvaged,
                decayed=(salvageable - salvaged) * (1.0 - decay_rate),
                live_after=next_live,
                burned_after=next_burned,
            )
        )
        live = next_live
        burned = next_burned
    return states


def _require_fraction(value: float, name: str) -> None:
    """Fail fast when a rate/retention parameter lies outside ``[0, 1]``."""

    if not 0.0 <= value <= 1.0:
        raise FireDynamicsError(f"{name} must lie in [0, 1]: {value}")


__all__ = [
    "ANNUAL_BURN_RATE_BY_ZONE",
    "DEFAULT_BURNED_DECAY_RATE",
    "MFRI_YEARS_BY_ZONE",
    "SCHEDULE_TOLERANCE",
    "FireDynamicsError",
    "FireYearState",
    "UnknownBurnRateError",
    "annual_burn_rate",
    "annual_burn_rate_for_stratum",
    "bec_zone_from_stratum",
    "burn_influx",
    "burned_volume_after",
    "live_volume_after",
    "salvageable_volume",
    "simulate_cohort_years",
]
