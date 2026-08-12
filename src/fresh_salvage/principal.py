"""Principal-side salvage-subsidy offer LP (continuous, HiGHS-only).

Semantic reference
------------------
The predecessor continuous prototype
``masc-yunhao-xu/Gurobi/Principal's model/Principal_LP.py`` (itself the
linear descendant of the binary ``P_RH_Version.py``) maximizes, per stand
``s`` and year ``y``::

    sum_{s,y} principal_cashflow_s * offer_{s,y}
              - loss_of_burned_wood_{s,y} * (1 - cum_offer_{s,y})

with ``cum_offer_{s,y} = sum_{t<=y} offer_{s,t}``,
``principal_cashflow_s = green_vol*green_stumpage + burned_vol*burned_stumpage
- burned_vol*subsidy``, and ``loss_of_burned_wood_{s,y} =
revenue_burned_timber_s * (1 - decay_rate**(y-1))``, subject to
``sum_y offer_{s,y} <= 1`` (offer once) and per-year AAC / green / burned
volume ceilings.

Formulation as implemented
--------------------------
Decision units are the derived WS3 bridge cohorts (one ARE row each:
``(ifm, au_id, stratum_code, curve_id, age)`` with ``area_ha``), not stands.
Years are 1-year timesteps ``1..horizon``. Per cohort ``c`` the pipeline
parses, at the boundary:

- ``standing_volume_m3[c] = area_ha[c] * curve_volume_m3_per_ha(curve, age)``
  with linear interpolation between curve points and constant endpoint
  extension beyond the tabulated age range;
- ``green_volume_m3[c] = standing_volume_m3[c]`` (the live standing volume,
  mirroring the stands-table convention that ``Total_Green_Vol`` is the full
  live volume);
- ``burned_volume_m3[c] = standing_volume_m3[c] * burn_share[dt]`` where
  ``burn_share[dt] = Total_Burned_Vol / Total_Green_Vol`` aggregated over the
  Phase 2a stands of the cohort's development type (stratum
  ``{bec}_{species}`` -> ``{species_group}_{BEC}``);
- ``cashflow[c]`` (stumpage net of subsidy, rates from the configured
  ``economics`` surface, defaulting to the calibrated ``data.py`` constants)
  and ``burned_value[c]`` (burned volume priced at the DT's volume-weighted
  average burned price);
- ``burn_rate[c] = 1 / MFRI[bec_zone]`` from ``fire.py``.

Variables (both continuous in ``[0, 1]``):

- ``offer[c, y]``: fraction of cohort ``c`` offered in year ``y``;
- ``cum_offer[c, y] = sum_{t<=y} offer[c, t]`` (definition rows).

Objective (maximize)::

    sum_{c,y} cashflow[c] * offer[c,y]
              - burn_rate[c] * burned_value[c] * (1 - decay_rate**(y-1))
                * (1 - cum_offer[c,y])

Constraints (every volume row is in m3/yr)::

    offer_once_c:   sum_y offer[c,y] <= 1
    aac_y:          sum_c green_volume_m3[c] * offer[c,y] <= aac_annual_m3
    burned_cap_y:   sum_c burned_volume_m3[c] * offer[c,y]
                    <= burned_limit_annual_m3           (only when configured)

Because one fraction variable scales both volumes, an offer's green:burned
split always equals the cohort's standing fractions, and offer-once is exactly
volume conservation: cumulative offered volume never exceeds standing volume.

Documented deviations from the prototype
----------------------------------------
1. **Units.** The ``Rolling_horizon_structure/P_RH_Version.py`` prototype
   summed *area* (ha) against the volume-denominated AAC. Here every
   capacity row is a volume sum in m3/yr; area appears only inside cohort
   standing-volume derivation.
2. **AAC basis.** The AAC ceiling bounds annual offered *green* (live)
   volume, the conventional annual allowable cut basis. The Gurobi
   ``Principal_LP.py`` applied the same ceiling to green + burned total
   volume; the task specification fixes it to green volume.
3. **Expected burn loss.** The loss term is weighted by the annual burn
   probability ``R[zone] = 1/MFRI`` so it is an *expected* loss; the
   prototype charged the full decayed burned value every year (implicit
   ``R = 1``).
4. **No stand-count cap.** The prototype's ``max_stands_per_year`` /
   ``one_per_year`` debug constraint is meaningless for continuous aggregate
   cohorts and is dropped.
5. **Optional burned cap.** The prototype's separate annual green and burned
   ceilings collapse into the green AAC plus an optional burned-volume cap
   (``burned_limit_annual_m3``, unbounded by default).

The model is a pure LP: no integer variables, no binaries, no thresholding or
rounding of decision outputs, and zero offer rows are emitted explicitly.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import highspy
import numpy as np
import pandas as pd

from fresh_salvage import fire
from fresh_salvage.data import (
    BURNED_GRADE_COLUMNS,
    BURNED_PRICE_DISCOUNT,
    GREEN_PRICES,
    SPECIES_GROUP_MAP,
    UNKNOWN_SPECIES_GROUP,
)
from fresh_salvage.models import (
    ArtifactLayout,
    Economics,
    PrincipalManifest,
    PrincipalOfferRecord,
    PrincipalResult,
    PrincipalRunConfig,
    PrincipalYearVolumes,
    safe_slug,
)
from fresh_salvage.ws3 import normalize_status

DEFAULT_DECAY_RATE = 0.85
DEFAULT_AAC_ANNUAL_M3 = 2_937_509
OFFER_REPORTING_TOLERANCE = 1e-9


class PrincipalError(RuntimeError):
    """Fatal principal-LP failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PrincipalCohort:
    """Trusted LP input record for one bridge cohort (parsed at the boundary).

    All volumes are m3; ``cashflow`` and ``burned_value`` are the totals when
    the whole cohort is offered once. ``burn_rate`` is the annual burn
    probability ``1 / MFRI`` of the cohort's BEC zone.
    """

    cohort_id: str
    stratum_code: str
    development_type: str
    area_ha: float
    green_volume_m3: float
    burned_volume_m3: float
    cashflow: float
    burned_value: float
    burn_rate: float


@dataclass(frozen=True)
class PrincipalLP:
    """A built HiGHS model plus the column layout needed to read it back."""

    model: highspy.Highs
    offer_columns: tuple[tuple[int, int], ...]
    horizon: int


def load_cohorts(config: PrincipalRunConfig) -> list[PrincipalCohort]:
    """Parse stands, ARE cohorts, and yield curves into trusted LP inputs.

    Boundary parser: raises ``PrincipalError`` with a structured code on any
    missing input, unmapped stratum, missing yield curve, or malformed ARE
    row; callers never re-validate the returned records.
    """

    stands = _read_table(config.stands_path, "principal_stands_missing")
    price_by_dt = _development_type_economics(
        stands,
        green_prices=config.economics.green_prices,
        burned_price_discount=config.economics.burned_price_discount,
    )
    curves = _yield_curves(Path(config.yields_path))
    return _parse_are_cohorts(
        Path(config.are_path), price_by_dt, curves, economics=config.economics
    )


def build_principal_lp(
    cohorts: list[PrincipalCohort],
    *,
    horizon: int,
    aac_annual_m3: float = DEFAULT_AAC_ANNUAL_M3,
    decay_rate: float = DEFAULT_DECAY_RATE,
    burned_limit_annual_m3: float | None = None,
    cohort_ceilings_m3: list[float] | None = None,
) -> PrincipalLP:
    """Build the continuous principal offer LP as a HiGHS model.

    Column layout: all ``offer[c, y]`` columns first (cohort-major, then
    year), followed by all ``cum_offer[c, y]`` columns in the same order, so
    column indices are deterministic functions of cohort index and year.

    ``cohort_ceilings_m3`` (optional) carries one annual green-volume ceiling
    (m3/yr) per cohort — the rolling-horizon engine passes the WS3 period-1
    decadal harvest split uniformly over the implemented years. Each ceiling
    adds ``horizon`` rows ``green_volume_m3[c] * offer[c,y] <= ceiling[c]``
    that augment (never replace) the global AAC row.
    """

    if not cohorts:
        raise PrincipalError("principal_no_cohorts", "at least one cohort is required")
    if horizon <= 0:
        raise PrincipalError(
            "principal_invalid_horizon", f"horizon must be positive, got {horizon}"
        )
    if aac_annual_m3 < 0:
        raise PrincipalError(
            "principal_invalid_aac", f"aac_annual_m3 cannot be negative: {aac_annual_m3}"
        )
    if not 0.0 <= decay_rate <= 1.0:
        raise PrincipalError(
            "principal_invalid_decay", f"decay_rate must lie in [0, 1]: {decay_rate}"
        )
    if burned_limit_annual_m3 is not None and burned_limit_annual_m3 < 0:
        raise PrincipalError(
            "principal_invalid_burned_limit",
            f"burned_limit_annual_m3 cannot be negative: {burned_limit_annual_m3}",
        )
    if cohort_ceilings_m3 is not None:
        if len(cohort_ceilings_m3) != len(cohorts):
            raise PrincipalError(
                "principal_invalid_cohort_ceilings",
                f"cohort_ceilings_m3 covers {len(cohort_ceilings_m3)} cohorts but "
                f"{len(cohorts)} were parsed",
            )
        for ceiling in cohort_ceilings_m3:
            if ceiling < 0:
                raise PrincipalError(
                    "principal_invalid_cohort_ceilings",
                    f"cohort ceiling cannot be negative: {ceiling}",
                )

    cohort_count = len(cohorts)
    years = range(horizon)
    offer_count = cohort_count * horizon
    total_columns = 2 * offer_count

    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setMaximize()
    model.addVars(total_columns, np.zeros(total_columns), np.ones(total_columns))

    # Objective coefficients: cashflow on offer columns, plus the expected
    # decayed-burn loss on the cumulative-offer columns (the loss term is
    # -loss * (1 - cum_offer) = -loss + loss * cum_offer). The constant
    # -sum_y loss[c,y] per cohort is tracked as the HiGHS objective offset so
    # reported objective values match the formula above exactly.
    costs = np.zeros(total_columns)
    offset = 0.0
    for c_index, cohort in enumerate(cohorts):
        base = c_index * horizon
        costs[base : base + horizon] = cohort.cashflow
        for year, loss in _expected_burn_losses(cohort, horizon, decay_rate):
            costs[offer_count + base + year] = loss
            offset -= loss
    model.changeColsCost(
        total_columns,
        np.arange(total_columns, dtype=np.int32),
        costs,
    )
    model.changeObjectiveOffset(offset)

    # cum_offer[c,y] - sum_{t<=y} offer[c,t] = 0
    for c_index in range(cohort_count):
        base = c_index * horizon
        for year in years:
            indices = np.arange(base, base + year + 1, dtype=np.int32)
            indices = np.append(indices, offer_count + base + year)
            values = np.append(np.full(year + 1, -1.0), 1.0)
            model.addRow(0.0, 0.0, len(indices), indices, values)

    # offer_once[c]: sum_y offer[c,y] <= 1
    for c_index in range(cohort_count):
        base = c_index * horizon
        indices = np.arange(base, base + horizon, dtype=np.int32)
        model.addRow(
            -highspy.kHighsInf, 1.0, horizon, indices, np.ones(horizon)
        )

    # aac[y]: sum_c green_volume_m3[c] * offer[c,y] <= aac_annual_m3
    offer_columns = tuple(
        (c_index, year) for c_index in range(cohort_count) for year in years
    )
    green_volumes = np.array([cohort.green_volume_m3 for cohort in cohorts])
    burned_volumes = np.array([cohort.burned_volume_m3 for cohort in cohorts])
    column_grid = np.arange(offer_count, dtype=np.int32).reshape(cohort_count, horizon)
    for year in years:
        model.addRow(
            -highspy.kHighsInf,
            aac_annual_m3,
            cohort_count,
            column_grid[:, year],
            green_volumes,
        )
        if burned_limit_annual_m3 is None:
            continue
        model.addRow(
            -highspy.kHighsInf,
            burned_limit_annual_m3,
            cohort_count,
            column_grid[:, year],
            burned_volumes,
        )

    # ws3_ceiling[c,y]: green_volume_m3[c] * offer[c,y] <= cohort_ceilings_m3[c]
    if cohort_ceilings_m3 is not None:
        for c_index, ceiling in enumerate(cohort_ceilings_m3):
            coefficient = np.array([green_volumes[c_index]])
            for year in years:
                model.addRow(
                    -highspy.kHighsInf,
                    float(ceiling),
                    1,
                    np.array([column_grid[c_index, year]], dtype=np.int32),
                    coefficient,
                )

    return PrincipalLP(model=model, offer_columns=offer_columns, horizon=horizon)


def solve_principal(
    cohorts: list[PrincipalCohort],
    *,
    horizon: int,
    aac_annual_m3: float = DEFAULT_AAC_ANNUAL_M3,
    decay_rate: float = DEFAULT_DECAY_RATE,
    burned_limit_annual_m3: float | None = None,
    cohort_ceilings_m3: list[float] | None = None,
    run_id: str = "principal-solve",
) -> PrincipalResult:
    """Build and solve the principal LP; return the typed result.

    Raises ``PrincipalError`` when the solve does not reach optimal status.
    Offer rows are emitted for every (cohort, year) pair, zeros included.
    """

    built = build_principal_lp(
        cohorts,
        horizon=horizon,
        aac_annual_m3=aac_annual_m3,
        decay_rate=decay_rate,
        burned_limit_annual_m3=burned_limit_annual_m3,
        cohort_ceilings_m3=cohort_ceilings_m3,
    )
    solve_started = time.monotonic()
    built.model.run()
    solve_seconds = time.monotonic() - solve_started

    status = normalize_status(built.model.getModelStatus())
    if status != "optimal":
        raise PrincipalError(
            "principal_solve_not_optimal",
            f"principal LP did not reach optimal status: {status}",
        )

    solution = built.model.getSolution().col_value
    objective_value = float(built.model.getInfo().objective_function_value)

    offers: list[PrincipalOfferRecord] = []
    green_offered = np.zeros(horizon)
    burned_offered = np.zeros(horizon)
    offered_cohort_years = 0
    for column, (c_index, year) in enumerate(built.offer_columns):
        fraction = _parse_offer_fraction(float(solution[column]), column)
        if fraction > OFFER_REPORTING_TOLERANCE:
            offered_cohort_years += 1
        cohort = cohorts[c_index]
        green_offered[year] += fraction * cohort.green_volume_m3
        burned_offered[year] += fraction * cohort.burned_volume_m3
        offers.append(
            PrincipalOfferRecord(
                cohort_id=cohort.cohort_id,
                year=year + 1,
                offer_fraction=fraction,
            )
        )

    return PrincipalResult(
        run_id=run_id,
        status=status,
        horizon=horizon,
        cohorts=len(cohorts),
        objective_value=objective_value,
        offers=offers,
        per_year_volumes=[
            PrincipalYearVolumes(
                year=year + 1,
                green_volume_m3=float(green_offered[year]),
                burned_volume_m3=float(burned_offered[year]),
            )
            for year in range(horizon)
        ],
        offered_cohort_years=offered_cohort_years,
        lp_rows=built.model.getNumRow(),
        lp_columns=built.model.getNumCol(),
        solve_seconds=solve_seconds,
    )


def run_principal(config: PrincipalRunConfig) -> PrincipalResult:
    """Run the principal LP from a config and write artifacts + manifest.

    Mirrors ``ws3.run_ws3``: parses inputs at the boundary, solves, writes the
    offer table (parquet + csv) and a provenance manifest under
    ``config.output_root``, and returns the typed result. Raises
    ``PrincipalError`` on fatal boundary or solver failures.
    """

    started_at = datetime.now(UTC)
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    run_slug = safe_slug(config.run_id)
    data_path = layout.data_path(f"{run_slug}-offers", ext="parquet")
    csv_path = layout.data_path(f"{run_slug}-offers", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-principal-manifest")

    cohorts = load_cohorts(config)
    result = solve_principal(
        cohorts,
        horizon=config.horizon,
        aac_annual_m3=config.aac_annual_m3,
        decay_rate=config.decay_rate,
        burned_limit_annual_m3=config.burned_limit_annual_m3,
        run_id=config.run_id,
    )

    offers_frame = pd.DataFrame([offer.model_dump() for offer in result.offers])
    offers_frame.to_parquet(data_path, index=False)
    offers_frame.to_csv(csv_path, index=False)

    manifest = PrincipalManifest(
        run_id=config.run_id,
        stands_path=config.stands_path,
        are_path=config.are_path,
        yields_path=config.yields_path,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        status=result.status,
        horizon=config.horizon,
        cohorts=result.cohorts,
        objective_value=result.objective_value,
        offered_cohort_years=result.offered_cohort_years,
        lp_rows=result.lp_rows,
        lp_columns=result.lp_columns,
        solve_seconds=result.solve_seconds,
        source_sha256=_source_checksums(config),
        config=config.model_dump(mode="json"),
        diagnostics=result.diagnostics,
    )
    manifest.write_json(manifest_path)

    result.data_path = data_path
    result.csv_path = csv_path
    result.manifest_path = manifest_path
    return result


def _parse_offer_fraction(value: float, column: int) -> float:
    """Parse a solved offer fraction, snapping solver dust inside tolerance.

    HiGHS returns primal values within its primal feasibility tolerance, so a
    fraction may land a few ulps outside ``[0, 1]``; values beyond the
    reporting tolerance are a genuine solver anomaly and fail fast.
    """

    if -OFFER_REPORTING_TOLERANCE <= value <= 1.0 + OFFER_REPORTING_TOLERANCE:
        return min(1.0, max(0.0, value))
    raise PrincipalError(
        "principal_fraction_out_of_bounds",
        f"offer fraction in column {column} is {value}, outside [0, 1] beyond "
        f"tolerance {OFFER_REPORTING_TOLERANCE}",
    )


def _expected_burn_losses(
    cohort: PrincipalCohort, horizon: int, decay_rate: float
) -> list[tuple[int, float]]:
    """Return ``(year_index, expected_loss)`` pairs for one cohort.

    The expected loss charged when the cohort is still unoffered in year
    ``y`` (1-based) is ``burn_rate * burned_value * (1 - decay_rate**(y-1))``.
    """

    if cohort.burn_rate == 0.0 or cohort.burned_value == 0.0:
        return []
    return [
        (year, cohort.burn_rate * cohort.burned_value * (1.0 - decay_rate**year))
        for year in range(horizon)
    ]


def _read_table(path: Path, code: str) -> pd.DataFrame:
    """Read a parquet or csv table, failing fast when absent or unsupported."""

    path = Path(path)
    if not path.is_file():
        raise PrincipalError(code, f"input table not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise PrincipalError(
        "principal_table_unsupported_suffix",
        f"input table {path} has unsupported suffix {path.suffix!r}; "
        "expected .parquet or .csv",
    )


def _development_type_economics(
    frame: pd.DataFrame,
    *,
    green_prices: dict[str, float] | None = None,
    burned_price_discount: float | None = None,
) -> dict[str, dict[str, float]]:
    """Aggregate the stands table into per-development-type burned shares.

    Returns ``{development_type: {"burn_share": ..., "burned_price": ...}}``
    where ``burn_share`` is Total_Burned_Vol over Total_Green_Vol and
    ``burned_price`` is the volume-weighted average burned price ($/m3).
    ``green_prices``/``burned_price_discount`` are the configured price
    surface (defaults: the calibrated ``data.py`` constants); burned prices
    are derived as green x discount, matching ``data.BURNED_PRICES``.
    """

    if green_prices is None:
        green_prices = GREEN_PRICES
    if burned_price_discount is None:
        burned_price_discount = BURNED_PRICE_DISCOUNT
    burned_prices = {
        key: value * burned_price_discount for key, value in green_prices.items()
    }

    required = {
        "development_type",
        "Total_Green_Vol",
        "Total_Burned_Vol",
        *BURNED_GRADE_COLUMNS,
    }
    missing = required.difference(frame.columns)
    if missing:
        raise PrincipalError(
            "principal_stands_missing_columns",
            f"stands table is missing required columns: {sorted(missing)}",
        )

    economics: dict[str, dict[str, float]] = {}
    for development_type, group in frame.groupby("development_type", sort=True):
        green = float(group["Total_Green_Vol"].sum())
        if green <= 0.0:
            continue
        burned = float(group["Total_Burned_Vol"].sum())
        burned_value = 0.0
        for column in BURNED_GRADE_COLUMNS:
            price_key = column[len("B_") : -len("_Vol")]
            burned_value += float(group[column].sum()) * burned_prices[price_key]
        economics[str(development_type)] = {
            "burn_share": burned / green,
            "burned_price": burned_value / burned if burned > 0.0 else 0.0,
        }
    return economics


def _yield_curves(yields_path: Path) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Parse the femic stage-1 yields table into per-curve point arrays."""

    table = _read_table(yields_path, "principal_yields_missing")
    required = {"curve_id", "age", "volume"}
    missing = required.difference(table.columns)
    if missing:
        raise PrincipalError(
            "principal_yields_missing_columns",
            f"yields table is missing required columns: {sorted(missing)}",
        )
    curves: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for curve_id, group in table.groupby("curve_id", sort=True):
        ordered = group.sort_values("age")
        curves[int(curve_id)] = (
            ordered["age"].to_numpy(dtype=float),
            ordered["volume"].to_numpy(dtype=float),
        )
    return curves


def _curve_volume_m3_per_ha(
    curves: dict[int, tuple[np.ndarray, np.ndarray]], curve_id: int, age: int
) -> float:
    """Interpolate a yield curve at ``age`` (constant beyond the endpoints)."""

    if curve_id not in curves:
        raise PrincipalError(
            "principal_yield_curve_missing",
            f"no yield curve for curve_id {curve_id} (cohort age {age})",
        )
    ages, volumes = curves[curve_id]
    return float(np.interp(float(age), ages, volumes))


def _development_type_from_stratum(stratum_code: str) -> str:
    """Map a bridge stratum code to its stands-table development type.

    ``stratum_code`` is ``{bec_zone}_{leading_species}`` (e.g. ``sbps_pli``)
    while the stands table keys development types as
    ``{species_group}_{BEC_ZONE}`` (e.g. ``SPF_SBPS``); the leading species
    code translates through ``SPECIES_GROUP_MAP``.
    """

    zone = fire.bec_zone_from_stratum(stratum_code)
    species_code = str(stratum_code).strip().split("_", 1)[1]
    species_group = SPECIES_GROUP_MAP.get(species_code.upper(), UNKNOWN_SPECIES_GROUP)
    return f"{species_group}_{zone}"


def _parse_are_cohorts(
    are_path: Path,
    price_by_dt: dict[str, dict[str, float]],
    curves: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    economics: Economics | None = None,
    burn_rate_multiplier: float = 1.0,
) -> list[PrincipalCohort]:
    """Parse ARE data rows into cohort LP inputs (file order preserved).

    ``economics`` carries the configured price/cost/rate surface charged in
    the principal cashflow (green/burned stumpage net of the per-m3 salvage
    subsidy; defaults to the calibrated ``data.py`` constants) and
    ``burn_rate_multiplier`` scales every cohort's MFRI-derived annual burn
    rate; a scaled rate above 1.0 (a burn probability, not a rate ratio)
    fails fast at the boundary.
    """

    if economics is None:
        economics = Economics()
    if not are_path.is_file():
        raise PrincipalError(
            "principal_are_missing", f"ARE section not found: {are_path}"
        )

    cohorts: list[PrincipalCohort] = []
    for line_number, line in enumerate(
        are_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip().startswith("*A"):
            continue
        tokens = line.split()
        if len(tokens) != 8:
            raise PrincipalError(
                "principal_are_unparseable",
                f"{are_path} line {line_number} is not an 8-token ARE data row: {line!r}",
            )
        _, _tsa, ifm, au_id, stratum_code, curve_text, age_text, area_text = tokens
        try:
            curve_id = int(curve_text)
            age = int(age_text)
            area_ha = float(area_text)
        except ValueError as exc:
            raise PrincipalError(
                "principal_are_unparseable",
                f"{are_path} line {line_number} has non-numeric curve/age/area: {line!r}",
            ) from exc

        try:
            development_type = _development_type_from_stratum(stratum_code)
        except fire.UnknownBurnRateError as exc:
            raise PrincipalError(
                "principal_stratum_malformed",
                f"{are_path} line {line_number} carries a malformed stratum code: {exc}",
            ) from exc
        if development_type not in price_by_dt:
            raise PrincipalError(
                "principal_stratum_unmapped",
                f"stratum {stratum_code!r} maps to development type "
                f"{development_type!r}, which has no stands-table economics",
            )
        try:
            burn_rate = fire.annual_burn_rate_for_stratum(stratum_code)
        except fire.UnknownBurnRateError as exc:
            raise PrincipalError(
                "principal_burn_rate_unknown",
                f"stratum {stratum_code!r} on {are_path} line {line_number} has no "
                f"MFRI fire-rate entry: {exc}",
            ) from exc
        burn_rate *= burn_rate_multiplier
        if burn_rate > 1.0:
            raise PrincipalError(
                "principal_burn_rate_invalid",
                f"stratum {stratum_code!r} annual burn rate {burn_rate} "
                f"(multiplier {burn_rate_multiplier}) exceeds 1.0",
            )
        standing_volume_m3 = area_ha * _curve_volume_m3_per_ha(curves, curve_id, age)
        burn_share = price_by_dt[development_type]["burn_share"]
        burned_volume_m3 = standing_volume_m3 * burn_share
        cohorts.append(
            PrincipalCohort(
                cohort_id=f"{ifm}:{au_id}:{stratum_code}:{curve_id}:{age}",
                stratum_code=stratum_code,
                development_type=development_type,
                area_ha=area_ha,
                green_volume_m3=standing_volume_m3,
                burned_volume_m3=burned_volume_m3,
                cashflow=(
                    standing_volume_m3 * economics.green_stumpage_rate
                    + burned_volume_m3 * economics.burned_stumpage_rate
                    - burned_volume_m3 * economics.subsidy_rate_per_m3
                ),
                burned_value=burned_volume_m3 * price_by_dt[development_type]["burned_price"],
                burn_rate=burn_rate,
            )
        )
    if not cohorts:
        raise PrincipalError(
            "principal_are_empty", f"ARE section {are_path} contains no data rows"
        )
    return cohorts


def _source_checksums(config: PrincipalRunConfig) -> dict[str, str]:
    """Return SHA-256 digests of the run's input files for provenance."""

    return {
        "stands": _sha256_file(config.stands_path),
        "are": _sha256_file(config.are_path),
        "yields": _sha256_file(config.yields_path),
    }


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "DEFAULT_AAC_ANNUAL_M3",
    "DEFAULT_DECAY_RATE",
    "OFFER_REPORTING_TOLERANCE",
    "PrincipalCohort",
    "PrincipalError",
    "PrincipalLP",
    "build_principal_lp",
    "load_cohorts",
    "run_principal",
    "solve_principal",
]
