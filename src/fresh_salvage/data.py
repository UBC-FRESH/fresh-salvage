"""Full-TSA data ingestion at the pipeline boundary.

This module ports the predecessor preprocessing in
``masc-yunhao-xu/Gurobi/Rolling_horizon_structure/DP_PA.py`` into a typed,
deterministic pipeline. The predecessor's 11-landscape-unit subset filter is
removed: every stand in the WL_VFSL polygon layer is retained, so ingestion
covers the full TSA29.

Burn severity, grade, and species logic is ported unchanged from DP_PA, with
two validation defects (FS-VAL-01, FS-VAL-02) fixed at the boundary:

Severity ladder (FS-VAL-01)
---------------------------
``SEVERITY_TO_BURNED_FRAC`` converts a stand's burn severity rating into the
fraction of live volume that becomes salvageable. The ladder is a
scenario-visible parameter (:class:`fresh_salvage.models.SeverityMapping` on
``ScenarioRunConfig.severity``) with the ported defaults — Unburned 0.0,
Low 0.30, Moderate 0.60, High 0.85 — echoed into the manifest parameters.
The dataset labels the mid severity tier "Medium" while the mapping table
calls it "Moderate"; ``SEVERITY_ALIASES`` normalizes that label at the
boundary. Unrated (NaN) stands are treated as unburned, and the literal
label ``UNKNOWN_SEVERITY_LABEL`` ("Unknown") maps to
``UNKNOWN_SEVERITY_FRAC`` (0.0) with a warning diagnostic. Any other
non-null rating that matches neither the ladder nor the aliases is a
boundary defect: ingestion halts with ``IngestError``
(``data_severity_unmatched``) listing the offending labels and their counts
— the predecessor's silent ``fillna(0.0)`` is not reproduced.

Coverage scaling (FS-VAL-02)
----------------------------
The severity rating describes a burn-severity survey polygon that generally
covers only part of the VRI polygon it was joined to, so applying the
severity fraction to the stand's entire live volume overstates salvageable
volume. Each rated row is therefore scaled by a coverage factor::

    coverage = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)

where ``SHAPE_Area_1`` is the burn-severity polygon area (m2) and
``FEATURE_AREA_SQM`` is the whole VRI polygon area (m2). UPPER-BOUND CAVEAT:
both columns are whole-polygon attributes of their respective layers, so the
ratio is NOT a true spatial intersection of the two geometries. When the
severity polygon is smaller than the VRI polygon the ratio assumes the
entire severity polygon lies inside this stand; when it is larger the clamp
to 1.0 assumes the stand is fully covered. Salvageable volume on rated
stands is therefore an upper bound. Unrated rows carry no severity polygon
and are unaffected (their fraction is 0). Rated rows with a missing or
non-positive denominator (or a missing/non-positive severity-polygon area)
halt ingestion with ``IngestError`` (``data_coverage_denominator_invalid`` /
``data_coverage_numerator_invalid``).

Burned volume is split across the same species/grade buckets as green volume
and degraded through ``BURNED_GRADE_TRANSITION``.

Two predecessor defects are deliberately not reproduced:

- The predecessor wrote burned peeler destinations under ``B_*_Peeler_Vol``
  while the schema names them ``B_*_Peelers_Vol``, and its
  ``if col_name in df.columns`` guard silently dropped those volumes. Here the
  grade transition is applied with the schema column names, so burned volume
  is conserved: ``Total_Burned_Vol`` equals live volume times the severity
  fraction.
- The predecessor never populated the ``B_Other_Vol`` bucket (same guard), so
  burned volume of Other species was lost entirely. Here Other salvageable
  volume is routed into ``B_Other_Vol``.

``development_type`` derivation
-------------------------------
``development_type`` is the stratum key that later phases use for aggregation
and that the refactor contract's aggregate opportunity
``(development_type, age_class, period, harvest_action)`` builds on. Each
stand is assigned ``{leading_species_group}_{BEC_ZONE_CODE}``, where the
leading species group comes from ``SPECIES_CD_1`` via ``SPECIES_GROUP_MAP``
(for example ``SPF_SBPS``, ``Cedar_IDF``, ``Other_BG``). Stands without a BEC
zone are bucketed as ``{leading_species_group}_UNKNOWN`` and reported as a
warning diagnostic.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from fresh_salvage.models import (
    ArtifactLayout,
    DevelopmentType,
    Diagnostic,
    Economics,
    IngestManifest,
    IngestResult,
    ScenarioRunConfig,
    SeverityMapping,
    Stand,
    safe_slug,
)

# --- Burn severity -> burned volume fraction -----------------------------
# Based on BC Pricing Wildfire Damaged Timber: high severity = more degradation.
# These are the module-level defaults; the scenario-visible parameter surface
# is fresh_salvage.models.SeverityMapping on ScenarioRunConfig.severity.
SEVERITY_TO_BURNED_FRAC = {
    "Unburned": 0.0,
    "Low": 0.30,
    "Moderate": 0.60,
    "High": 0.85,
}
# The WL_VFSL burn-severity layer labels the mid tier "Medium"; the mapping
# table (and the predecessor's intent) call it "Moderate".
SEVERITY_ALIASES = {"Medium": "Moderate"}
# The literal "Unknown" rating is recognized (treated as unburned with a
# warning); every other unmatched non-null rating is fatal.
UNKNOWN_SEVERITY_LABEL = "Unknown"
UNKNOWN_SEVERITY_FRAC = 0.0

# --- Burned-area coverage scaling (FS-VAL-02) ------------------------------
# SHAPE_Area_1 is the whole burn-severity survey polygon area (m2);
# FEATURE_AREA_SQM is the whole VRI polygon area (m2). Their clamped ratio is
# an UPPER-BOUND coverage proxy, not a true spatial intersection (see the
# module docstring). Both are whole-polygon attributes shared across every
# row of their polygon — they must never be summed over rows.
COVERAGE_NUMERATOR_COLUMN = "SHAPE_Area_1"
COVERAGE_DENOMINATOR_COLUMN = "FEATURE_AREA_SQM"

# Grade transition for burned timber in the fresh/prompt-salvage regime
# (year 1-3 after the kill). Red-stage evidence (Plank 1984; Loeffler &
# Anderson 2018): fire-killed stands retain most of their grade in the first
# 1-2 years (sawlog share 85% -> 73%, lumber value -10%), so year-1 sawlog
# retention is ~0.80; checking loss is already priced by
# BURNED_PRICE_DISCOUNT (0.65). The grey-stage (5-10 yr) collapse to pulp is
# handled by the 0.85/yr burned-inventory decay — putting it in the initial
# mix would double-count the time decay.
# Grade hierarchy is Peel > Saw > Pulp and fire can only DEGRADE grade, so
# every row is downgrade-only (each row still sums to 1.0, keeping burned
# volume conserved): the ~20% of burned sawlog volume that does not hold
# sawlog grade drops straight to pulp — fire never upgrades sawlog to peel.
BURNED_GRADE_TRANSITION = {
    "Sawlog": {"Sawlog": 0.80, "Peeler": 0.00, "Pulpwood": 0.20},
    "Peeler": {"Sawlog": 0.35, "Peeler": 0.55, "Pulpwood": 0.10},
    "Pulpwood": {"Sawlog": 0.0, "Peeler": 0.0, "Pulpwood": 1.0},
}

# Species grading splits (green volume shares per market group).
SPECIES_GRADE_SPLIT = {
    "SPF": {"Sawlog": 0.805, "Peeler": 0.092, "Pulpwood": 0.103},
    "Cedar": {"Sawlog": 0.805, "Peeler": 0.092, "Pulpwood": 0.103},
    "Hem-Bal": {"Sawlog": 0.805, "Peeler": 0.092, "Pulpwood": 0.103},
    "Df-Larch": {"Sawlog": 0.805, "Peeler": 0.092, "Pulpwood": 0.103},
    "Other": {"Sawlog": 0.805, "Peeler": 0.092, "Pulpwood": 0.103},
}

# The output schema names the peeler grade columns "Peelers" while the
# transition/split tables use the singular "Peeler".
GRADE_COLUMN_SUFFIX = {
    "Sawlog": "Sawlog",
    "Peeler": "Peelers",
    "Pulpwood": "Pulpwood",
}

# Economic parameters (calibrated; per-parameter rationale and sources are
# documented in planning/economics-calibration.md). These are the module-level
# defaults; the scenario-visible parameter surface is
# fresh_salvage.models.Economics on ScenarioRunConfig.economics (and the flat
# economic fields of RHRunConfig), echoed into the manifest parameters.
SUBSIDY_RATE_PER_M3 = 3.0
GREEN_STUMPAGE_RATE = 15.0
BURNED_STUMPAGE_RATE = 0.25
GREEN_HARVEST_COST = 45.0
# Burned premiums are +25% over green for the mild, recently-killed case
# (prompt year-1-3 salvage): 45 x 1.25 = 56.25 -> 56; 30 x 1.25 = 37.5 -> 38.
# Convention: burned premiums are rounded to the nearest dollar.
BURNED_HARVEST_COST = 56.0
TRANSPORT_COST_PER_M3 = 30.0
BURNED_TRANSPORT_COST_PER_M3 = 38.0

# Green prices ($/m3 FOB mill; BC Interior Log Market Report Q4-2023 anchors,
# peeler = sawlog x 1.15 assumption, pulp at the market pulpwood level).
GREEN_PRICES = {
    "SPF_Sawlog": 127,
    "SPF_Peelers": 146,
    "SPF_Pulpwood": 55,
    "Df-Larch_Sawlog": 103,
    "Df-Larch_Peelers": 118,
    "Df-Larch_Pulpwood": 55,
    "Hem-Bal_Sawlog": 120,
    "Hem-Bal_Peelers": 138,
    "Hem-Bal_Pulpwood": 55,
    "Cedar_Sawlog": 144,
    "Cedar_Peelers": 166,
    "Cedar_Pulpwood": 55,
    "Other": 90,
}

# Burned prices: fire damage reduces value; 35% discount on green prices.
BURNED_PRICE_DISCOUNT = 0.65
BURNED_PRICES = {key: value * BURNED_PRICE_DISCOUNT for key, value in GREEN_PRICES.items()}

# VRI species code -> market species group (port of DP_PA.get_species_group).
SPECIES_GROUP_MAP = {
    "ACB": "SPF",
    "B": "SPF",
    "BA": "SPF",
    "BB": "SPF",
    "BL": "SPF",
    "FD": "SPF",
    "FDI": "SPF",
    "P": "SPF",
    "PA": "SPF",
    "PL": "SPF",
    "PLC": "SPF",
    "PLI": "SPF",
    "PW": "SPF",
    "PY": "SPF",
    "S": "SPF",
    "SB": "SPF",
    "SE": "SPF",
    "SS": "SPF",
    "SW": "SPF",
    "SX": "SPF",
    "SXW": "SPF",
    "CW": "Cedar",
    "H": "Hem-Bal",
    "HM": "Hem-Bal",
    "HW": "Hem-Bal",
    "LA": "Df-Larch",
    "LS": "Df-Larch",
    "LW": "Df-Larch",
    "AC": "Other",
    "ACT": "Other",
    "AT": "Other",
    "DR": "Other",
    "E": "Other",
    "EP": "Other",
    "OA": "Other",
    "W": "Other",
    "XC": "Other",
    "XH": "Other",
}
UNKNOWN_SPECIES_GROUP = "Other"

# The 37 base columns retained from the WL_VFSL layer (port of cols_to_keep).
BASE_COLUMNS = [
    "FEATURE_ID",
    "MAP_ID",
    "POLYGON_ID",
    "POLYGON_AREA",
    "BASAL_AREA",
    "VRI_LIVE_STEMS_PER_HA",
    "PROJ_HEIGHT_1",
    "SPECIES_CD_1",
    "SPECIES_PCT_1",
    "SPECIES_CD_2",
    "SPECIES_PCT_2",
    "SPECIES_CD_3",
    "SPECIES_PCT_3",
    "SPECIES_CD_4",
    "SPECIES_PCT_4",
    "SPECIES_CD_5",
    "SPECIES_PCT_5",
    "SPECIES_CD_6",
    "SPECIES_PCT_6",
    "LIVE_VOL_PER_HA_SPP1_175",
    "LIVE_VOL_PER_HA_SPP2_175",
    "LIVE_VOL_PER_HA_SPP3_175",
    "LIVE_VOL_PER_HA_SPP4_175",
    "LIVE_VOL_PER_HA_SPP5_175",
    "LIVE_VOL_PER_HA_SPP6_175",
    "DEAD_VOL_PER_HA_SPP1_175",
    "DEAD_VOL_PER_HA_SPP2_175",
    "DEAD_VOL_PER_HA_SPP3_175",
    "DEAD_VOL_PER_HA_SPP4_175",
    "DEAD_VOL_PER_HA_SPP5_175",
    "DEAD_VOL_PER_HA_SPP6_175",
    "LIVE_STAND_VOLUME_175",
    "DEAD_STAND_VOLUME_175",
    "BURN_SEVERITY_RATING",
    "MEAN",
    "LANDSCAPE_UNIT_ID",
    "LANDSCAPE_UNIT_NAME",
]

# The two coverage columns are read at the boundary but are not part of the
# output schema: they only shape Total_Burned_Vol via the coverage factor.
INPUT_COLUMNS = BASE_COLUMNS + [
    "BEC_ZONE_CODE",
    COVERAGE_NUMERATOR_COLUMN,
    COVERAGE_DENOMINATOR_COLUMN,
]

# Columns whose null values drop the stand (port of cols_to_check).
NULL_CHECK_COLUMNS = [
    "FEATURE_ID",
    "MAP_ID",
    "POLYGON_ID",
    "POLYGON_AREA",
    "BASAL_AREA",
    "VRI_LIVE_STEMS_PER_HA",
    "PROJ_HEIGHT_1",
    "MEAN",
    "LIVE_VOL_PER_HA_SPP1_175",
]

# Per-species-slot (species code column, live volume column) pairs.
SPECIES_SLOT_COLUMNS = [
    ("SPECIES_CD_1", "LIVE_VOL_PER_HA_SPP1_175"),
    ("SPECIES_CD_2", "LIVE_VOL_PER_HA_SPP2_175"),
    ("SPECIES_CD_3", "LIVE_VOL_PER_HA_SPP3_175"),
    ("SPECIES_CD_4", "LIVE_VOL_PER_HA_SPP4_175"),
    ("SPECIES_CD_5", "LIVE_VOL_PER_HA_SPP5_175"),
    ("SPECIES_CD_6", "LIVE_VOL_PER_HA_SPP6_175"),
]

GRADE_COLUMNS = [
    "SPF_Sawlog_Vol",
    "SPF_Peelers_Vol",
    "SPF_Pulpwood_Vol",
    "Df-Larch_Sawlog_Vol",
    "Df-Larch_Peelers_Vol",
    "Df-Larch_Pulpwood_Vol",
    "Hem-Bal_Sawlog_Vol",
    "Hem-Bal_Peelers_Vol",
    "Hem-Bal_Pulpwood_Vol",
    "Cedar_Sawlog_Vol",
    "Cedar_Peelers_Vol",
    "Cedar_Pulpwood_Vol",
    "Other_Vol",
]

BURNED_GRADE_COLUMNS = [
    "B_SPF_Sawlog_Vol",
    "B_SPF_Peelers_Vol",
    "B_SPF_Pulpwood_Vol",
    "B_Df-Larch_Sawlog_Vol",
    "B_Df-Larch_Peelers_Vol",
    "B_Df-Larch_Pulpwood_Vol",
    "B_Hem-Bal_Sawlog_Vol",
    "B_Hem-Bal_Peelers_Vol",
    "B_Hem-Bal_Pulpwood_Vol",
    "B_Cedar_Sawlog_Vol",
    "B_Cedar_Peelers_Vol",
    "B_Cedar_Pulpwood_Vol",
    "B_Other_Vol",
]

ECONOMIC_OUTPUT_COLUMNS = [
    "Total_Green_Vol",
    "Total_Burned_Vol",
    "Subsidy_Rate",
    "Green_Stumpage_Rate",
    "Burned_Stumpage_Rate",
    "Subsidy_Total",
    "Stumpage_Green_Total",
    "Stumpage_Burned_Total",
    "Harvest_Cost_Green",
    "Harvest_Cost_Burned",
    "green_prices",
    "burned_prices",
]

NEW_OUTPUT_COLUMNS = ["BEC_ZONE_CODE", "development_type"]

# Full output schema: the 75-column Gurobi_test1.csv schema plus the new
# BEC_ZONE_CODE and development_type columns.
OUTPUT_COLUMNS = (
    BASE_COLUMNS
    + GRADE_COLUMNS
    + BURNED_GRADE_COLUMNS
    + ECONOMIC_OUTPUT_COLUMNS
    + NEW_OUTPUT_COLUMNS
)


class IngestError(RuntimeError):
    """Fatal ingestion failure carrying a structured diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def species_group(code: object) -> str:
    """Map a VRI species code to its market species group (Other when unknown)."""

    if pd.isna(code):
        return UNKNOWN_SPECIES_GROUP
    return SPECIES_GROUP_MAP.get(str(code).strip().upper(), UNKNOWN_SPECIES_GROUP)


def ingest(scenario: ScenarioRunConfig) -> IngestResult:
    """Run the full-TSA ingestion pipeline for a scenario config.

    Raises ``IngestError`` on fatal boundary failures; non-fatal data anomalies
    are reported as ``Diagnostic`` records on the returned result.
    """

    started = time.monotonic()
    diagnostics: list[Diagnostic] = []

    source = Path(scenario.inputs.wl_vfsl_path)
    if not source.is_file():
        raise IngestError(
            code="ingest_source_missing",
            message=f"WL_VFSL source file not found: {source}",
        )

    layout = ArtifactLayout(output_root=Path(scenario.inputs.output_root)).initialize()
    run_slug = safe_slug(scenario.run_id)
    data_path = layout.data_path(f"{run_slug}-stands", ext="parquet")
    csv_path = layout.data_path(f"{run_slug}-stands", ext="csv")
    manifest_path = layout.manifest_path(f"{run_slug}-manifest")

    input_rows, dropped_null_rows, dropped_zero_live_rows, frame = _read_wl_vfsl(source)
    severity = scenario.severity
    severity_fraction = _severity_fraction(
        frame,
        diagnostics,
        severity.severity_to_burned_frac,
        severity.severity_aliases,
    )
    coverage_fraction = _coverage_fraction(frame)
    burned_fraction = severity_fraction * coverage_fraction
    grade_columns = _derive_grade_columns(frame, burned_fraction)
    for column, values in grade_columns.items():
        frame[column] = values

    economics = scenario.economics
    burned_prices = economics.burned_prices()
    frame["Total_Green_Vol"] = frame[GRADE_COLUMNS].sum(axis=1)
    frame["Total_Burned_Vol"] = frame[BURNED_GRADE_COLUMNS].sum(axis=1)
    frame["Subsidy_Rate"] = economics.subsidy_rate_per_m3
    frame["Green_Stumpage_Rate"] = economics.green_stumpage_rate
    frame["Burned_Stumpage_Rate"] = economics.burned_stumpage_rate
    frame["Subsidy_Total"] = frame["Total_Burned_Vol"] * economics.subsidy_rate_per_m3
    frame["Stumpage_Green_Total"] = frame["Total_Green_Vol"] * economics.green_stumpage_rate
    frame["Stumpage_Burned_Total"] = (
        frame["Total_Burned_Vol"] * economics.burned_stumpage_rate
    )
    frame["Harvest_Cost_Green"] = economics.green_harvest_cost
    frame["Harvest_Cost_Burned"] = economics.burned_harvest_cost
    frame["green_prices"] = json.dumps(economics.green_prices, sort_keys=True)
    frame["burned_prices"] = json.dumps(burned_prices, sort_keys=True)

    _attach_zone_and_development_type(frame, diagnostics)
    frame = frame.loc[:, OUTPUT_COLUMNS]

    burned_stands = int((frame["Total_Burned_Vol"] > 0).sum())
    burned_volume = float(frame["Total_Burned_Vol"].sum())
    green_volume = float(frame["Total_Green_Vol"].sum())
    per_bec_zone_counts = _sorted_counts(frame, "BEC_ZONE_CODE")
    per_development_type_counts = _sorted_counts(frame, "development_type")

    frame.to_parquet(data_path, index=False)
    frame.to_csv(csv_path, index=False)

    manifest = IngestManifest(
        run_id=scenario.run_id,
        source_file=source,
        source_sha256=_sha256_file(source),
        completed_at=datetime.now(UTC),
        input_rows=input_rows,
        retained_rows=len(frame),
        dropped_null_rows=dropped_null_rows,
        dropped_zero_live_rows=dropped_zero_live_rows,
        burned_stands=burned_stands,
        burned_volume=burned_volume,
        green_volume=green_volume,
        per_bec_zone_counts=per_bec_zone_counts,
        per_development_type_counts=per_development_type_counts,
        parameters=_parameters(severity, economics),
        diagnostics=diagnostics,
    )
    manifest.write_json(manifest_path)

    return IngestResult(
        run_id=scenario.run_id,
        source_file=source,
        data_path=data_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        total_stands=len(frame),
        dropped_null_rows=dropped_null_rows,
        dropped_zero_live_rows=dropped_zero_live_rows,
        burned_stands=burned_stands,
        burned_volume=burned_volume,
        green_volume=green_volume,
        per_bec_zone_counts=per_bec_zone_counts,
        per_development_type_counts=per_development_type_counts,
        diagnostics=diagnostics,
        duration_seconds=time.monotonic() - started,
    )


def stands_from_frame(frame: pd.DataFrame) -> list[Stand]:
    """Parse an ingested stand frame into typed ``Stand`` records.

    Boundary parser for downstream phases; not called during ingestion.
    """

    green_prices = _parse_price_dict(frame["green_prices"].iloc[0])
    burned_prices = _parse_price_dict(frame["burned_prices"].iloc[0])
    return [
        Stand(
            feature_id=str(row["FEATURE_ID"]),
            polygon_id=str(row["POLYGON_ID"]),
            map_id=str(row["MAP_ID"]),
            polygon_area=float(row["POLYGON_AREA"]),
            bec_zone=str(row["BEC_ZONE_CODE"]),
            development_type=str(row["development_type"]),
            landscape_unit_id=_clean_string(row["LANDSCAPE_UNIT_ID"]),
            burn_severity_rating=_clean_string(row["BURN_SEVERITY_RATING"]),
            total_green_vol=float(row["Total_Green_Vol"]),
            total_burned_vol=float(row["Total_Burned_Vol"]),
            subsidy_rate=float(row["Subsidy_Rate"]),
            green_stumpage_rate=float(row["Green_Stumpage_Rate"]),
            burned_stumpage_rate=float(row["Burned_Stumpage_Rate"]),
            harvest_cost_green=float(row["Harvest_Cost_Green"]),
            harvest_cost_burned=float(row["Harvest_Cost_Burned"]),
            subsidy_total=float(row["Subsidy_Total"]),
            stumpage_green_total=float(row["Stumpage_Green_Total"]),
            stumpage_burned_total=float(row["Stumpage_Burned_Total"]),
            green_prices=green_prices,
            burned_prices=burned_prices,
        )
        for row in frame.to_dict("records")
    ]


def development_types_from_frame(frame: pd.DataFrame) -> list[DevelopmentType]:
    """Aggregate an ingested stand frame into ``DevelopmentType`` records."""

    records: list[DevelopmentType] = []
    for key, group in frame.groupby("development_type", sort=True):
        species_group, bec_zone = key.split("_", 1)
        records.append(
            DevelopmentType(
                development_type=key,
                bec_zone=bec_zone,
                species_group=species_group,
                stand_count=int(len(group)),
                area_ha=float(pd.to_numeric(group["POLYGON_AREA"], errors="coerce").sum()),
                total_green_vol=float(group["Total_Green_Vol"].sum()),
                total_burned_vol=float(group["Total_Burned_Vol"].sum()),
            )
        )
    return records


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a raw input file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_wl_vfsl(path: Path) -> tuple[int, int, int, pd.DataFrame]:
    """Read the WL_VFSL layer, drop null and zero-live-volume stands.

    Returns ``(input_rows, dropped_null_rows, dropped_zero_live_rows, frame)``.
    """

    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    missing = [column for column in INPUT_COLUMNS if column not in raw.columns]
    if missing:
        raise IngestError(
            code="ingest_missing_columns",
            message=(
                "WL_VFSL is missing required columns: "
                + ", ".join(sorted(missing))
            ),
        )
    frame = raw.loc[:, INPUT_COLUMNS].replace(r"^\s*$", pd.NA, regex=True)
    input_rows = len(frame)

    null_mask = frame[NULL_CHECK_COLUMNS].isna().any(axis=1)
    dropped_null_rows = int(null_mask.sum())
    frame = frame.loc[~null_mask]

    live_spp1 = pd.to_numeric(frame["LIVE_VOL_PER_HA_SPP1_175"], errors="coerce").fillna(
        0.0
    )
    zero_mask = live_spp1 == 0.0
    dropped_zero_live_rows = int(zero_mask.sum())
    frame = frame.loc[~zero_mask]

    return input_rows, dropped_null_rows, dropped_zero_live_rows, frame


def _severity_fraction(
    frame: pd.DataFrame,
    diagnostics: list[Diagnostic],
    severity_to_burned_frac: dict[str, float],
    severity_aliases: dict[str, str],
) -> pd.Series:
    """Map burn severity ratings to burned volume fractions.

    Unrated (NaN) stands and the recognized ``UNKNOWN_SEVERITY_LABEL`` rating
    map to ``UNKNOWN_SEVERITY_FRAC``. Any other non-null rating that matches
    neither the ladder nor the aliases is fatal (``data_severity_unmatched``):
    a silently zeroed severity would understate salvageable volume without
    leaving a trace.
    """

    rating = frame["BURN_SEVERITY_RATING"]
    severity = rating.replace(severity_aliases)
    fraction = severity.map(severity_to_burned_frac)
    explicit_unknown = severity == UNKNOWN_SEVERITY_LABEL
    unmatched = severity.notna() & fraction.isna() & ~explicit_unknown
    if unmatched.any():
        counts = {
            str(label): int(count)
            for label, count in sorted(severity[unmatched].value_counts().items())
        }
        raise IngestError(
            code="data_severity_unmatched",
            message=(
                "WL_VFSL contains burn severity ratings that match neither the "
                f"severity ladder nor the aliases nor "
                f"'{UNKNOWN_SEVERITY_LABEL}' (label: stand count): {counts}. "
                "Extend the scenario severity ladder or aliases instead of "
                "silently treating these stands as unburned."
            ),
        )

    aliased_count = int(rating.isin(severity_aliases).sum())
    if aliased_count:
        alias_summary = ", ".join(
            f"'{source}'->'{target}'"
            for source, target in sorted(severity_aliases.items())
        )
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="ingest_severity_alias",
                message=(
                    f"{aliased_count} stands were normalized through severity "
                    f"aliases ({alias_summary})"
                ),
            )
        )
    if explicit_unknown.any():
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="ingest_unknown_severity",
                message=(
                    f"{int(explicit_unknown.sum())} stands rated "
                    f"'{UNKNOWN_SEVERITY_LABEL}' are treated as unburned"
                ),
            )
        )
    return fraction.fillna(UNKNOWN_SEVERITY_FRAC)


def _coverage_fraction(frame: pd.DataFrame) -> pd.Series:
    """Return the upper-bound burned-area coverage of each row.

    ``coverage = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)`` for rated rows
    (see the module docstring for the upper-bound caveat). Unrated rows carry
    no severity polygon; their coverage is 1.0, which is inert because their
    severity fraction is 0. Rated rows with a missing/non-positive
    denominator or a missing/non-positive severity-polygon area are fatal:
    the coverage of a rated stand must never be silently invented.
    """

    rated = frame["BURN_SEVERITY_RATING"].notna()
    if not rated.any():
        return pd.Series(1.0, index=frame.index, dtype=float)

    numerator = pd.to_numeric(frame[COVERAGE_NUMERATOR_COLUMN], errors="coerce")
    denominator = pd.to_numeric(frame[COVERAGE_DENOMINATOR_COLUMN], errors="coerce")

    invalid_denominator = rated & (denominator.isna() | (denominator <= 0))
    if invalid_denominator.any():
        raise IngestError(
            code="data_coverage_denominator_invalid",
            message=(
                f"{int(invalid_denominator.sum())} rated stands lack a positive "
                f"{COVERAGE_DENOMINATOR_COLUMN}; coverage scaling requires a "
                "positive whole-polygon area for every rated stand."
            ),
        )
    invalid_numerator = rated & (numerator.isna() | (numerator <= 0))
    if invalid_numerator.any():
        raise IngestError(
            code="data_coverage_numerator_invalid",
            message=(
                f"{int(invalid_numerator.sum())} rated stands lack a positive "
                f"{COVERAGE_NUMERATOR_COLUMN}; a burn severity rating without "
                "a positive severity-polygon area is a boundary defect."
            ),
        )

    coverage = (numerator / denominator).clip(upper=1.0)
    return coverage.where(rated, 1.0)


def _derive_grade_columns(frame: pd.DataFrame, burned_fraction: pd.Series) -> dict[str, np.ndarray]:
    """Return the green/burned grade volume columns (vectorized DP_PA port)."""

    columns: dict[str, np.ndarray] = {}
    for column in GRADE_COLUMNS + BURNED_GRADE_COLUMNS:
        columns[column] = np.zeros(len(frame), dtype=float)

    burned_frac = burned_fraction.to_numpy(dtype=float)
    for sp_col, live_col in SPECIES_SLOT_COLUMNS:
        if sp_col not in frame.columns or frame[sp_col].isna().all():
            continue
        species_group = (
            frame[sp_col].map(SPECIES_GROUP_MAP).fillna(UNKNOWN_SPECIES_GROUP)
        )
        live = pd.to_numeric(frame[live_col], errors="coerce").fillna(0.0).to_numpy(
            dtype=float
        )
        salvageable = live * burned_frac
        for group, splits in SPECIES_GRADE_SPLIT.items():
            mask = species_group.to_numpy() == group
            if group == UNKNOWN_SPECIES_GROUP:
                columns["Other_Vol"][mask] += live[mask]
                # The schema exposes a single burned bucket for Other species;
                # route the whole salvageable volume there so no volume is lost.
                columns["B_Other_Vol"][mask] += salvageable[mask]
                continue
            for grade, frac in splits.items():
                columns[f"{group}_{GRADE_COLUMN_SUFFIX[grade]}_Vol"][mask] += (
                    live[mask] * frac
                )
            for grade_in, transitions in BURNED_GRADE_TRANSITION.items():
                vol_in = salvageable[mask] * splits[grade_in]
                for grade_out, frac in transitions.items():
                    columns[f"B_{group}_{GRADE_COLUMN_SUFFIX[grade_out]}_Vol"][
                        mask
                    ] += vol_in * frac
    return columns


def _attach_zone_and_development_type(
    frame: pd.DataFrame, diagnostics: list[Diagnostic]
) -> None:
    """Attach the normalized BEC zone and the ``development_type`` stratum key."""

    missing_bec = int(frame["BEC_ZONE_CODE"].isna().sum())
    if missing_bec:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="ingest_missing_bec_zone",
                message=(
                    f"{missing_bec} stands lack a BEC zone and are bucketed as "
                    "'UNKNOWN'"
                ),
            )
        )
    frame["BEC_ZONE_CODE"] = frame["BEC_ZONE_CODE"].fillna("UNKNOWN")
    leading_group = (
        frame["SPECIES_CD_1"].map(SPECIES_GROUP_MAP).fillna(UNKNOWN_SPECIES_GROUP)
    )
    frame["development_type"] = leading_group + "_" + frame["BEC_ZONE_CODE"]


def _sorted_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    """Return deterministic value counts (sorted by key)."""

    return {str(key): int(count) for key, count in sorted(frame[column].value_counts().items())}


def _parameters(severity: SeverityMapping, economics: Economics) -> dict[str, object]:
    """Return the parameter surface recorded in the run manifest."""

    return {
        "severity_to_burned_frac": dict(severity.severity_to_burned_frac),
        "severity_aliases": dict(severity.severity_aliases),
        "unknown_severity_label": UNKNOWN_SEVERITY_LABEL,
        "unknown_severity_frac": UNKNOWN_SEVERITY_FRAC,
        "coverage_scaling": {
            "numerator_column": COVERAGE_NUMERATOR_COLUMN,
            "denominator_column": COVERAGE_DENOMINATOR_COLUMN,
            "formula": "coverage = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)",
            "caveat": (
                "Upper bound: both columns are whole-polygon areas of their "
                "respective layers, so the ratio is not a true spatial "
                "intersection of the severity fragment with the VRI polygon."
            ),
        },
        "burned_grade_transition": BURNED_GRADE_TRANSITION,
        "species_grade_split": SPECIES_GRADE_SPLIT,
        "subsidy_rate_per_m3": economics.subsidy_rate_per_m3,
        "green_stumpage_rate": economics.green_stumpage_rate,
        "burned_stumpage_rate": economics.burned_stumpage_rate,
        "green_harvest_cost": economics.green_harvest_cost,
        "burned_harvest_cost": economics.burned_harvest_cost,
        "green_transport_cost_per_m3": economics.green_transport_cost_per_m3,
        "burned_transport_cost_per_m3": economics.burned_transport_cost_per_m3,
        "burned_price_discount": economics.burned_price_discount,
        "green_prices": dict(economics.green_prices),
        "burned_prices": economics.burned_prices(),
    }


def _parse_price_dict(value: object) -> dict[str, float]:
    """Parse a stored price JSON column into a float dict."""

    if isinstance(value, str):
        return {str(key): float(item) for key, item in json.loads(value).items()}
    return {str(key): float(item) for key, item in dict(value).items()}


def _clean_string(value: object) -> str | None:
    """Return a trimmed string or None for missing values."""

    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "BASE_COLUMNS",
    "BURNED_GRADE_COLUMNS",
    "BURNED_GRADE_TRANSITION",
    "BURNED_HARVEST_COST",
    "BURNED_PRICE_DISCOUNT",
    "BURNED_PRICES",
    "BURNED_STUMPAGE_RATE",
    "BURNED_TRANSPORT_COST_PER_M3",
    "COVERAGE_DENOMINATOR_COLUMN",
    "COVERAGE_NUMERATOR_COLUMN",
    "ECONOMIC_OUTPUT_COLUMNS",
    "GRADE_COLUMNS",
    "GRADE_COLUMN_SUFFIX",
    "GREEN_HARVEST_COST",
    "GREEN_PRICES",
    "GREEN_STUMPAGE_RATE",
    "INPUT_COLUMNS",
    "IngestError",
    "NEW_OUTPUT_COLUMNS",
    "NULL_CHECK_COLUMNS",
    "OUTPUT_COLUMNS",
    "SEVERITY_ALIASES",
    "SEVERITY_TO_BURNED_FRAC",
    "SPECIES_GRADE_SPLIT",
    "SPECIES_GROUP_MAP",
    "SPECIES_SLOT_COLUMNS",
    "SUBSIDY_RATE_PER_M3",
    "TRANSPORT_COST_PER_M3",
    "UNKNOWN_SEVERITY_FRAC",
    "UNKNOWN_SEVERITY_LABEL",
    "UNKNOWN_SPECIES_GROUP",
    "development_types_from_frame",
    "ingest",
    "species_group",
    "stands_from_frame",
]
