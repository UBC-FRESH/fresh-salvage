"""Agent-side harvest/salvage LP with annual fire dynamics (continuous, HiGHS).

Semantic reference
------------------
The predecessor continuous prototype
``masc-yunhao-xu/Gurobi/Rolling_horizon_structure/A_RH_Version.py`` (itself
the linear descendant of the binary
``masc-yunhao-xu/Gurobi/Agent's model/A_RH_Version.py``; no separate
``Agent_LP.py`` exists) maximizes, per stand ``s`` and year ``y``::

    sum_{s,y} (green_revenue_s - harvest_cost_s
               + decayed_burned_revenue_{s,y} + subsidy_s - salvage_cost_s)
              * x_{s,y}

subject to ``sum_y x_{s,y} <= 1`` (harvest once), per-year area / green /
burned ceilings, and per-``(development_type, age_class, action)`` pool
limits against the WS3 schedule.

Formulation as implemented
--------------------------
Decision units are the same WS3 bridge cohorts as the principal LP
(``(ifm, au_id, stratum_code, curve_id, age)`` with ``area_ha`` and derived
``standing_volume_m3[c]``). Years are 1-year timesteps ``1..horizon`` and the
ordering within one timestep is harvest -> fire -> salvage -> decay, exactly
as implemented in :mod:`fresh_salvage.fire` (the LP rows below are the same
equations, so the LP and :func:`fire.simulate_cohort_years` share one source
of truth for the dynamics). All variables are continuous fractions of the
cohort's initial standing volume in ``[0, 1]``; there are no integer
variables.

Variables per cohort ``c`` and year ``t`` (``R = burn_rate[c]`` from the
``fire.py`` MFRI table, ``d = decay_rate`` the annual retention of unsalvaged
burned volume):

- ``H[c,t]``: fraction of the cohort's standing volume harvested green;
- ``S[c,t]``: fraction salvaged from the burned inventory;
- ``V[c,t]``: live (standing) fraction at end of year ``t``;
- ``B[c,t]``: burned inventory fraction at end of year ``t``.

Rows (``V[c,0] = 1`` and ``B[c,0] = 0`` are the initial conditions; every
row is a fraction-of-standing-volume balance, volumes enter only through the
objective coefficients, so no area/volume unit mixing is possible)::

    live_t:      V[c,t] = V[c,t-1] - H[c,t] - R * (V[c,t-1] - H[c,t])
    burned_t:    B[c,t] = (B[c,t-1] + R * (V[c,t-1] - H[c,t]) - S[c,t]) * d
    salvage_t:   S[c,t] <= B[c,t-1] + R * (V[c,t-1] - H[c,t])
    sell_once_c: sum_t H[c,t] + sum_t S[c,t] <= 1        (no double selling)

Offered fractions are an *input* (Phase 5 wires the principal coupling):
``H[c,t]`` and ``S[c,t]`` carry upper bounds ``offer[c,t]`` from either the
uniform ``default_offer_fraction`` (1.0 = every cohort fully offered every
year) or a principal offer table (``cohort_id``/``year``/``offer_fraction``).

Objective (maximize agent NPV, ``df_t = 1 / (1 + discount_rate) ** t``)::

    sum_{c,t} df_t * standing_volume_m3[c] * (
        green_margin_m3[c] * H[c,t] + salvage_margin_m3[c] * S[c,t])

with ``green_margin_m3 = green_price - green_harvest_cost -
green_transport_cost - green_stumpage_rate`` and ``salvage_margin_m3 =
burned_price - burned_harvest_cost - burned_transport_cost -
burned_stumpage_rate + subsidy_rate_per_m3`` (prices are the development
type's volume-weighted average grade prices, weighted by the configured
``economics.green_prices`` with burned prices at the configured discount; the
economic surface defaults to the calibrated ``data.py`` constants and the
subsidy accrues per m3 of burned volume actually salvaged).

Documented deviations from the prototype
----------------------------------------
1. **Continuous fractions, not binaries.** The Gurobi prototype harvested
   whole stands (``x[s,y]`` binary, ``<= 20`` stands per year debug cap).
   Here every action is a continuous fraction of an aggregate cohort and the
   debug cap is meaningless, so the model is a pure LP with zero integer
   variables.
2. **Endogenous fire dynamics.** The prototype paid the decayed burned
   revenue of the stand's static ``Total_Burned_Vol`` whenever it harvested.
   Here burned volume is generated year by year at rate ``R = 1/MFRI`` on the
   post-harvest remainder, accumulates into an explicit burned inventory, and
   unsalvaged burned volume decays at ``decay_rate`` (0.85) per year.
3. **Salvage feasibility.** Salvage is limited to the on-hand burned
   inventory ``B[t-1] + BURN_IN[t]``; the prototype had no burned-inventory
   balance at all.
4. **No double selling, kept linear.** A cohort's volume is sold at most
   once across the green and burned channels: cumulative harvested plus
   salvaged fraction never exceeds 1. (With ``V, B >= 0`` the balance rows
   already imply this; the row is kept explicit because it is part of the
   approved formulation.)
5. **NPV discounting.** Cash flows are discounted by ``(1 +
   discount_rate) ** t`` (default 3%); the prototype was undiscounted.
6. **No area/volume-mixed capacity rows.** The rolling-horizon prototype
   summed *area* against volume ceilings and carried WS3-schedule pool
   constraints; here the only limits are the offered fractions and the fire
   dynamics, and every constraint row is a fraction balance.
7. **Subsidy basis.** The subsidy is paid per m3 of burned volume actually
   salvaged; the prototype paid it on the stand's full ``Total_Burned_Vol``
   whenever the stand was harvested.
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
    GRADE_COLUMNS,
    GREEN_PRICES,
    SPECIES_GROUP_MAP,
    UNKNOWN_SPECIES_GROUP,
)
from fresh_salvage.models import (
    AgentDecisionRecord,
    AgentManifest,
    AgentResult,
    AgentRunConfig,
    AgentYearVolumes,
    ArtifactLayout,
    Economics,
    safe_slug,
)
from fresh_salvage.principal import OFFER_REPORTING_TOLERANCE
from fresh_salvage.ws3 import normalize_status

__all__ = [
    "AgentCohort",
    "AgentError",
    "AgentLP",
    "build_agent_lp",
    "load_cohorts",
    "resolve_offers",
    "run_agent",
    "solve_agent",
]


class AgentError(RuntimeError):
    """Fatal agent-LP failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentCohort:
    """Trusted LP input record for one bridge cohort (parsed at the boundary).

    ``standing_volume_m3`` is the initial live volume; ``burn_rate`` is the
    annual burn probability ``1 / MFRI`` of the cohort's BEC zone;
    ``green_price_m3``/``burned_price_m3`` are the development type's
    volume-weighted average prices ($/m3).
    """

    cohort_id: str
    stratum_code: str
    development_type: str
    area_ha: float
    standing_volume_m3: float
    burn_rate: float
    green_price_m3: float
    burned_price_m3: float


@dataclass(frozen=True)
class AgentLP:
    """A built HiGHS model plus the column layout needed to read it back."""

    model: highspy.Highs
    decision_columns: tuple[tuple[int, int], ...]
    horizon: int


def load_cohorts(config: AgentRunConfig) -> list[AgentCohort]:
    """Parse stands, ARE cohorts, and yield curves into trusted LP inputs.

    Boundary parser: raises ``AgentError`` with a structured code on any
    missing input, unmapped stratum, missing yield curve, or malformed ARE
    row; callers never re-validate the returned records.
    """

    stands = _read_table(config.stands_path, "agent_stands_missing")
    price_by_dt = _development_type_economics(
        stands,
        green_prices=config.economics.green_prices,
        burned_price_discount=config.economics.burned_price_discount,
    )
    curves = _yield_curves(Path(config.yields_path))
    return _parse_are_cohorts(Path(config.are_path), price_by_dt, curves)


def resolve_offers(
    cohorts: list[AgentCohort],
    *,
    horizon: int,
    offers_path: Path | None = None,
    default_offer_fraction: float = 1.0,
) -> list[tuple[float, ...]]:
    """Parse the offered-fraction input into a trusted per-cohort matrix.

    Returns one tuple of ``horizon`` fractions per cohort (cohort order
    preserved). With ``offers_path=None`` every cohort-year carries
    ``default_offer_fraction``. Otherwise the principal offer table
    (``cohort_id``/``year``/``offer_fraction``, parquet or csv) is read;
    cohort-years absent from the table are not offered (0.0), and unknown
    cohort ids, duplicate rows, or out-of-range fractions fail fast.
    """

    if horizon <= 0:
        raise AgentError(
            "agent_invalid_horizon", f"horizon must be positive, got {horizon}"
        )
    if not 0.0 <= default_offer_fraction <= 1.0:
        raise AgentError(
            "agent_invalid_default_offer",
            f"default_offer_fraction must lie in [0, 1]: {default_offer_fraction}",
        )
    if offers_path is None:
        return [tuple([default_offer_fraction]) * horizon for _ in cohorts]

    table = _read_table(offers_path, "agent_offers_missing")
    required = {"cohort_id", "year", "offer_fraction"}
    missing = required.difference(table.columns)
    if missing:
        raise AgentError(
            "agent_offers_missing_columns",
            f"offers table {offers_path} is missing required columns: "
            f"{sorted(missing)}",
        )
    if table.duplicated(subset=["cohort_id", "year"]).any():
        raise AgentError(
            "agent_offers_duplicate_rows",
            f"offers table {offers_path} contains duplicate (cohort_id, year) rows",
        )

    fractions: dict[tuple[str, int], float] = {}
    for row in table.itertuples(index=False):
        fraction = float(row.offer_fraction)
        if not 0.0 <= fraction <= 1.0:
            raise AgentError(
                "agent_offers_fraction_out_of_bounds",
                f"offers table {offers_path} has offer_fraction {fraction} for "
                f"cohort {row.cohort_id!r} year {row.year}, outside [0, 1]",
            )
        fractions[(str(row.cohort_id), int(row.year))] = fraction

    known_ids = {cohort.cohort_id for cohort in cohorts}
    unknown_ids = sorted({cohort_id for cohort_id, _year in fractions} - known_ids)
    if unknown_ids:
        raise AgentError(
            "agent_offers_unknown_cohorts",
            f"offers table {offers_path} references {len(unknown_ids)} cohorts "
            f"absent from the ARE inputs (e.g. {unknown_ids[:5]})",
        )
    return [
        tuple(
            fractions.get((cohort.cohort_id, year), 0.0)
            for year in range(1, horizon + 1)
        )
        for cohort in cohorts
    ]


def build_agent_lp(
    cohorts: list[AgentCohort],
    offers: list[tuple[float, ...]],
    *,
    horizon: int,
    decay_rate: float = fire.DEFAULT_BURNED_DECAY_RATE,
    discount_rate: float = 0.03,
    economics: Economics | None = None,
) -> AgentLP:
    """Build the continuous agent harvest/salvage LP as a HiGHS model.

    Column layout is deterministic (cohort-major, then year): all ``H``
    columns, then all ``S``, ``V``, ``B`` columns, each block of size
    ``len(cohorts) * horizon``. ``H[c,t]``/``S[c,t]`` carry the offered
    fractions as upper bounds; ``V``/``B`` are bounded to ``[0, 1]``.
    ``economics`` carries the price/cost/rate surface of the margin
    coefficients (defaults to the calibrated ``data.py`` constants).
    """

    if economics is None:
        economics = Economics()
    if not cohorts:
        raise AgentError("agent_no_cohorts", "at least one cohort is required")
    if horizon <= 0:
        raise AgentError(
            "agent_invalid_horizon", f"horizon must be positive, got {horizon}"
        )
    if len(offers) != len(cohorts):
        raise AgentError(
            "agent_offers_shape_mismatch",
            f"offers cover {len(offers)} cohorts but {len(cohorts)} were parsed",
        )
    for cohort, offered in zip(cohorts, offers, strict=True):
        if len(offered) != horizon:
            raise AgentError(
                "agent_offers_shape_mismatch",
                f"cohort {cohort.cohort_id!r} carries {len(offered)} offer "
                f"fractions for a horizon of {horizon}",
            )
    if not 0.0 <= decay_rate <= 1.0:
        raise AgentError(
            "agent_invalid_decay", f"decay_rate must lie in [0, 1]: {decay_rate}"
        )
    if discount_rate < 0.0:
        raise AgentError(
            "agent_invalid_discount",
            f"discount_rate cannot be negative: {discount_rate}",
        )

    cohort_count = len(cohorts)
    block = cohort_count * horizon
    harvest_offset = 0
    salvage_offset = block
    live_offset = 2 * block
    burned_offset = 3 * block
    total_columns = 4 * block

    lower = np.zeros(total_columns)
    upper = np.ones(total_columns)
    for c_index, offered in enumerate(offers):
        base = c_index * horizon
        upper[harvest_offset + base : harvest_offset + base + horizon] = offered
        upper[salvage_offset + base : salvage_offset + base + horizon] = offered

    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setMaximize()
    model.addVars(total_columns, lower, upper)

    costs = np.zeros(total_columns)
    for c_index, cohort in enumerate(cohorts):
        green_margin = (
            cohort.green_price_m3
            - economics.green_harvest_cost
            - economics.green_transport_cost_per_m3
            - economics.green_stumpage_rate
        )
        salvage_margin = (
            cohort.burned_price_m3
            - economics.burned_harvest_cost
            - economics.burned_transport_cost_per_m3
            - economics.burned_stumpage_rate
            + economics.subsidy_rate_per_m3
        )
        base = c_index * horizon
        for year in range(horizon):
            discount_factor = 1.0 / (1.0 + discount_rate) ** (year + 1)
            costs[harvest_offset + base + year] = (
                discount_factor * cohort.standing_volume_m3 * green_margin
            )
            costs[salvage_offset + base + year] = (
                discount_factor * cohort.standing_volume_m3 * salvage_margin
            )
    model.changeColsCost(
        total_columns,
        np.arange(total_columns, dtype=np.int32),
        costs,
    )

    for c_index, cohort in enumerate(cohorts):
        rate = cohort.burn_rate
        survival = 1.0 - rate
        base = c_index * horizon
        for year in range(horizon):
            harvest_col = harvest_offset + base + year
            salvage_col = salvage_offset + base + year
            live_col = live_offset + base + year
            burned_col = burned_offset + base + year
            if year == 0:
                # Initial conditions V[c,0] = 1 and B[c,0] = 0 fold into the
                # right-hand sides via the fire.py dynamics primitives.
                influx0 = fire.burn_influx(1.0, rate)
                live_rhs = fire.live_volume_after(1.0, 0.0, influx0)
                burned_rhs = fire.burned_volume_after(0.0, influx0, 0.0, decay_rate)
                salvage_rhs = fire.salvageable_volume(0.0, influx0)
                model.addRow(
                    live_rhs,
                    live_rhs,
                    2,
                    np.array([live_col, harvest_col], dtype=np.int32),
                    np.array([1.0, survival]),
                )
                model.addRow(
                    burned_rhs,
                    burned_rhs,
                    3,
                    np.array([burned_col, harvest_col, salvage_col], dtype=np.int32),
                    np.array([1.0, decay_rate * rate, decay_rate]),
                )
                model.addRow(
                    -highspy.kHighsInf,
                    salvage_rhs,
                    2,
                    np.array([salvage_col, harvest_col], dtype=np.int32),
                    np.array([1.0, rate]),
                )
                continue
            previous_live_col = live_offset + base + year - 1
            previous_burned_col = burned_offset + base + year - 1
            # V[c,t] - (1 - R) * V[c,t-1] + (1 - R) * H[c,t] = 0
            model.addRow(
                0.0,
                0.0,
                3,
                np.array([live_col, previous_live_col, harvest_col], dtype=np.int32),
                np.array([1.0, -survival, survival]),
            )
            # B[c,t] - d*B[c,t-1] - d*R*V[c,t-1] + d*R*H[c,t] + d*S[c,t] = 0
            model.addRow(
                0.0,
                0.0,
                5,
                np.array(
                    [
                        burned_col,
                        previous_burned_col,
                        previous_live_col,
                        harvest_col,
                        salvage_col,
                    ],
                    dtype=np.int32,
                ),
                np.array(
                    [1.0, -decay_rate, -decay_rate * rate, decay_rate * rate, decay_rate]
                ),
            )
            # S[c,t] - B[c,t-1] - R*V[c,t-1] + R*H[c,t] <= 0
            model.addRow(
                -highspy.kHighsInf,
                0.0,
                4,
                np.array(
                    [salvage_col, previous_burned_col, previous_live_col, harvest_col],
                    dtype=np.int32,
                ),
                np.array([1.0, -1.0, -rate, rate]),
            )
        # sell_once[c]: sum_t H[c,t] + sum_t S[c,t] <= 1
        indices = np.concatenate(
            [
                np.arange(harvest_offset + base, harvest_offset + base + horizon),
                np.arange(salvage_offset + base, salvage_offset + base + horizon),
            ]
        ).astype(np.int32)
        model.addRow(
            -highspy.kHighsInf, 1.0, 2 * horizon, indices, np.ones(2 * horizon)
        )

    decision_columns = tuple(
        (c_index, year) for c_index in range(cohort_count) for year in range(horizon)
    )
    return AgentLP(model=model, decision_columns=decision_columns, horizon=horizon)


def solve_agent(
    cohorts: list[AgentCohort],
    offers: list[tuple[float, ...]],
    *,
    horizon: int,
    decay_rate: float = fire.DEFAULT_BURNED_DECAY_RATE,
    discount_rate: float = 0.03,
    economics: Economics | None = None,
    run_id: str = "agent-solve",
) -> AgentResult:
    """Build and solve the agent LP; return the typed result.

    Raises ``AgentError`` when the solve does not reach optimal status.
    Decision rows are emitted for every (cohort, year) pair, zeros included.
    """

    built = build_agent_lp(
        cohorts,
        offers,
        horizon=horizon,
        decay_rate=decay_rate,
        discount_rate=discount_rate,
        economics=economics,
    )
    solve_started = time.monotonic()
    built.model.run()
    solve_seconds = time.monotonic() - solve_started

    status = normalize_status(built.model.getModelStatus())
    if status != "optimal":
        raise AgentError(
            "agent_solve_not_optimal",
            f"agent LP did not reach optimal status: {status}",
        )

    solution = built.model.getSolution().col_value
    objective_value = float(built.model.getInfo().objective_function_value)

    cohort_count = len(cohorts)
    block = cohort_count * horizon
    decisions: list[AgentDecisionRecord] = []
    harvest_m3 = np.zeros(horizon)
    salvage_m3 = np.zeros(horizon)
    influx_m3 = np.zeros(horizon)
    live_end_m3 = np.zeros(horizon)
    burned_end_m3 = np.zeros(horizon)
    active_cohort_years = 0
    for column, (c_index, year) in enumerate(built.decision_columns):
        cohort = cohorts[c_index]
        harvest = _parse_action_fraction(float(solution[column]), column)
        salvage = _parse_action_fraction(
            float(solution[block + column]), block + column
        )
        live = _parse_action_fraction(
            float(solution[2 * block + column]), 2 * block + column
        )
        burned = _parse_action_fraction(
            float(solution[3 * block + column]), 3 * block + column
        )
        if harvest > OFFER_REPORTING_TOLERANCE or salvage > OFFER_REPORTING_TOLERANCE:
            active_cohort_years += 1
        live_before = (
            _parse_action_fraction(
                float(solution[2 * block + column - 1]), 2 * block + column - 1
            )
            if year > 0
            else 1.0
        )
        # Reporting-only recomputation: the LP rows enforce the dynamics, and
        # parsed fractions are dust-snapped to [0, 1], so the exposed volume
        # can only undershoot zero by solver dust.
        influx = fire.burn_influx(max(0.0, live_before - harvest), cohort.burn_rate)
        volume = cohort.standing_volume_m3
        harvest_m3[year] += harvest * volume
        salvage_m3[year] += salvage * volume
        influx_m3[year] += influx * volume
        live_end_m3[year] += live * volume
        burned_end_m3[year] += burned * volume
        decisions.append(
            AgentDecisionRecord(
                cohort_id=cohort.cohort_id,
                year=year + 1,
                harvest_fraction=harvest,
                salvage_fraction=salvage,
                harvest_volume_m3=harvest * volume,
                salvage_volume_m3=salvage * volume,
            )
        )

    return AgentResult(
        run_id=run_id,
        status=status,
        horizon=horizon,
        cohorts=cohort_count,
        objective_value=objective_value,
        decisions=decisions,
        per_year_volumes=[
            AgentYearVolumes(
                year=year + 1,
                harvest_volume_m3=float(harvest_m3[year]),
                salvage_volume_m3=float(salvage_m3[year]),
                burn_influx_m3=float(influx_m3[year]),
                live_volume_m3=float(live_end_m3[year]),
                burned_volume_m3=float(burned_end_m3[year]),
            )
            for year in range(horizon)
        ],
        active_cohort_years=active_cohort_years,
        lp_rows=built.model.getNumRow(),
        lp_columns=built.model.getNumCol(),
        solve_seconds=solve_seconds,
    )


def run_agent(config: AgentRunConfig) -> AgentResult:
    """Run the agent LP from a config and write artifacts + manifest.

    Mirrors ``principal.run_principal``: parses inputs at the boundary,
    solves, writes the decision table (parquet + csv) and a provenance
    manifest under ``config.output_root``, and returns the typed result.
    Raises ``AgentError`` on fatal boundary or solver failures.
    """

    started_at = datetime.now(UTC)
    layout = ArtifactLayout(output_root=Path(config.output_root)).initialize()
    run_slug = safe_slug(config.run_id)
    data_path = layout.data_path(f"{run_slug}-decisions", ext="parquet")
    csv_path = layout.data_path(f"{run_slug}-decisions", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-agent-manifest")

    cohorts = load_cohorts(config)
    offers = resolve_offers(
        cohorts,
        horizon=config.horizon,
        offers_path=config.offers_path,
        default_offer_fraction=config.default_offer_fraction,
    )
    result = solve_agent(
        cohorts,
        offers,
        horizon=config.horizon,
        decay_rate=config.decay_rate,
        discount_rate=config.discount_rate,
        economics=config.economics,
        run_id=config.run_id,
    )

    decisions_frame = pd.DataFrame(
        [decision.model_dump() for decision in result.decisions]
    )
    decisions_frame.to_parquet(data_path, index=False)
    decisions_frame.to_csv(csv_path, index=False)

    manifest = AgentManifest(
        run_id=config.run_id,
        stands_path=config.stands_path,
        are_path=config.are_path,
        yields_path=config.yields_path,
        offers_path=config.offers_path,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        status=result.status,
        horizon=config.horizon,
        cohorts=result.cohorts,
        objective_value=result.objective_value,
        active_cohort_years=result.active_cohort_years,
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


def _parse_action_fraction(value: float, column: int) -> float:
    """Parse a solved fraction, snapping solver dust inside tolerance.

    HiGHS returns primal values within its primal feasibility tolerance, so a
    fraction may land a few ulps outside ``[0, 1]``; values beyond the
    reporting tolerance are a genuine solver anomaly and fail fast.
    """

    if -OFFER_REPORTING_TOLERANCE <= value <= 1.0 + OFFER_REPORTING_TOLERANCE:
        return min(1.0, max(0.0, value))
    raise AgentError(
        "agent_fraction_out_of_bounds",
        f"action fraction in column {column} is {value}, outside [0, 1] beyond "
        f"tolerance {OFFER_REPORTING_TOLERANCE}",
    )


def _read_table(path: Path, code: str) -> pd.DataFrame:
    """Read a parquet or csv table, failing fast when absent or unsupported."""

    path = Path(path)
    if not path.is_file():
        raise AgentError(code, f"input table not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise AgentError(
        "agent_table_unsupported_suffix",
        f"input table {path} has unsupported suffix {path.suffix!r}; "
        "expected .parquet or .csv",
    )


def _development_type_economics(
    frame: pd.DataFrame,
    *,
    green_prices: dict[str, float] | None = None,
    burned_price_discount: float | None = None,
) -> dict[str, dict[str, float]]:
    """Aggregate the stands table into per-development-type prices.

    Returns ``{development_type: {"green_price": ..., "burned_price": ...}}``
    with volume-weighted average grade prices ($/m3) over the green and
    burned grade columns. ``green_prices``/``burned_price_discount`` are the
    configured price surface (defaults: the calibrated ``data.py``
    constants); burned prices are derived as green x discount, matching
    ``data.BURNED_PRICES``.
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
        *GRADE_COLUMNS,
        *BURNED_GRADE_COLUMNS,
    }
    missing = required.difference(frame.columns)
    if missing:
        raise AgentError(
            "agent_stands_missing_columns",
            f"stands table is missing required columns: {sorted(missing)}",
        )

    economics: dict[str, dict[str, float]] = {}
    for development_type, group in frame.groupby("development_type", sort=True):
        green = float(group["Total_Green_Vol"].sum())
        if green <= 0.0:
            continue
        green_value = 0.0
        for column in GRADE_COLUMNS:
            price_key = column[: -len("_Vol")]
            green_value += float(group[column].sum()) * green_prices[price_key]
        burned = float(group["Total_Burned_Vol"].sum())
        burned_value = 0.0
        for column in BURNED_GRADE_COLUMNS:
            price_key = column[len("B_") : -len("_Vol")]
            burned_value += float(group[column].sum()) * burned_prices[price_key]
        economics[str(development_type)] = {
            "green_price": green_value / green,
            "burned_price": burned_value / burned if burned > 0.0 else 0.0,
        }
    return economics


def _yield_curves(yields_path: Path) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Parse the femic stage-1 yields table into per-curve point arrays."""

    table = _read_table(yields_path, "agent_yields_missing")
    required = {"curve_id", "age", "volume"}
    missing = required.difference(table.columns)
    if missing:
        raise AgentError(
            "agent_yields_missing_columns",
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
        raise AgentError(
            "agent_yield_curve_missing",
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
    burn_rate_multiplier: float = 1.0,
) -> list[AgentCohort]:
    """Parse ARE data rows into cohort LP inputs (file order preserved).

    ``burn_rate_multiplier`` scales every cohort's MFRI-derived annual burn
    rate; a scaled rate above 1.0 (a burn probability, not a rate ratio)
    fails fast at the boundary.
    """

    if not are_path.is_file():
        raise AgentError("agent_are_missing", f"ARE section not found: {are_path}")

    cohorts: list[AgentCohort] = []
    for line_number, line in enumerate(
        are_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip().startswith("*A"):
            continue
        tokens = line.split()
        if len(tokens) != 8:
            raise AgentError(
                "agent_are_unparseable",
                f"{are_path} line {line_number} is not an 8-token ARE data row: "
                f"{line!r}",
            )
        _, _tsa, ifm, au_id, stratum_code, curve_text, age_text, area_text = tokens
        try:
            curve_id = int(curve_text)
            age = int(age_text)
            area_ha = float(area_text)
        except ValueError as exc:
            raise AgentError(
                "agent_are_unparseable",
                f"{are_path} line {line_number} has non-numeric curve/age/area: "
                f"{line!r}",
            ) from exc

        try:
            development_type = _development_type_from_stratum(stratum_code)
        except fire.UnknownBurnRateError as exc:
            raise AgentError(
                "agent_stratum_malformed",
                f"{are_path} line {line_number} carries a malformed stratum code: "
                f"{exc}",
            ) from exc
        if development_type not in price_by_dt:
            raise AgentError(
                "agent_stratum_unmapped",
                f"stratum {stratum_code!r} maps to development type "
                f"{development_type!r}, which has no stands-table economics",
            )
        try:
            burn_rate = fire.annual_burn_rate_for_stratum(stratum_code)
        except fire.UnknownBurnRateError as exc:
            raise AgentError(
                "agent_burn_rate_unknown",
                f"stratum {stratum_code!r} on {are_path} line {line_number} has "
                f"no MFRI fire-rate entry: {exc}",
            ) from exc
        burn_rate *= burn_rate_multiplier
        if burn_rate > 1.0:
            raise AgentError(
                "agent_burn_rate_invalid",
                f"stratum {stratum_code!r} annual burn rate {burn_rate} "
                f"(multiplier {burn_rate_multiplier}) exceeds 1.0",
            )
        standing_volume_m3 = area_ha * _curve_volume_m3_per_ha(curves, curve_id, age)
        cohorts.append(
            AgentCohort(
                cohort_id=f"{ifm}:{au_id}:{stratum_code}:{curve_id}:{age}",
                stratum_code=stratum_code,
                development_type=development_type,
                area_ha=area_ha,
                standing_volume_m3=standing_volume_m3,
                burn_rate=burn_rate,
                green_price_m3=price_by_dt[development_type]["green_price"],
                burned_price_m3=price_by_dt[development_type]["burned_price"],
            )
        )
    if not cohorts:
        raise AgentError(
            "agent_are_empty", f"ARE section {are_path} contains no data rows"
        )
    return cohorts


def _source_checksums(config: AgentRunConfig) -> dict[str, str]:
    """Return SHA-256 digests of the run's input files for provenance."""

    checksums = {
        "stands": _sha256_file(config.stands_path),
        "are": _sha256_file(config.are_path),
        "yields": _sha256_file(config.yields_path),
    }
    if config.offers_path is not None:
        checksums["offers"] = _sha256_file(config.offers_path)
    return checksums


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
