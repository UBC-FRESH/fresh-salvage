# Phase 6 Validation Report: FS-VAL-01 / FS-VAL-02 Resolution

Status: resolved (both defects fixed, results re-generated)

Date: 2026-08-12

## Scope

Two validation defects in the Phase 1 full-TSA ingestion
(`src/fresh_salvage/data.py`) were confirmed by code inspection and an
empirical audit of the WL_VFSL polygon layer (`WL_VFSL.csv`, 317,750 rows,
source_sha256 `649f8c614963f73dc100d1456f656335290cc6d53e4edac1f96b8e784aeb264e`):

- **FS-VAL-01** — the burn-severity ladder was hardcoded, and any unmatched
  non-null severity label was silently mapped to fraction 0.0 by a terminal
  `fillna` (only the literal label "Unknown" raised a warning).
- **FS-VAL-02** — the severity fraction was applied to the stand's entire
  live volume, although the rating describes a burn-severity survey polygon
  that generally covers only part of the VRI polygon.

## FS-VAL-01: severity ladder audit and fix

### Before-picture (empirical, raw layer)

Tabulation of `BURN_SEVERITY_RATING` against the fraction the pre-fix code
assigned (area_ha is the summed `POLYGON_AREA` of the affected VRI polygons;
each `FEATURE_ID` appears exactly once in the layer):

| Rating (post-alias) | Assigned fraction | Stands | Area (ha) | Note |
| --- | --- | --- | --- | --- |
| (null) | 0.0 (unrated) | 311,655 | 5,012,263.2 | no severity polygon joined |
| Medium | 0.60 via alias | 2,596 | 73,399.7 | Medium -> Moderate confirmed |
| High | 0.85 | 1,315 | 29,130.4 | |
| Unburned | 0.0 | 1,096 | 27,434.1 | rated but unburned |
| Low | 0.30 | 1,076 | 22,250.4 | |
| Unknown | 0.0 (silent) | 12 | 143.6 | only the warning saved these |

Every non-null label other than the four ladder labels, the "Medium" alias,
and "Unknown" would have been zeroed without any record. In this dataset the
only unmatched label is "Unknown" (12 rows; 10 survive the null/zero-live
drops). All 6,095 rated rows carry `FIRE_YEAR = 2025` — the severity layer
covers the 2025 fire season only (see Caveats).

### Fix (FS-VAL-01, RESOLVED)

- The ladder is now a scenario-visible parameter:
  `ScenarioRunConfig.severity` (`SeverityMapping`, in `models.py`) carries
  `severity_to_burned_frac` and `severity_aliases`, defaulting to the ported
  values (Unburned 0.0 / Low 0.30 / Moderate 0.60 / High 0.85, alias
  `Medium -> Moderate`). Fractions are validated to [0, 1], alias targets
  must be ladder labels, and alias sources must not collide with ladder
  labels — invalid ladders fail at config parse time.
- The silent `fillna(0.0)` is replaced by a fatal guard: any unmatched
  non-null rating raises `IngestError` with structured code
  `data_severity_unmatched`, listing the offending labels and their stand
  counts. Unrated (NaN) stands and the recognized "Unknown" label still map
  to 0.0, the latter with the existing `ingest_unknown_severity` warning.
- The effective ladder, aliases, and the unknown-label policy are echoed
  into the manifest `parameters` block.

## FS-VAL-02: coverage-scaled burned volume

### Mechanism and empirical shape

`SHAPE_Area_1` is the whole burn-severity survey polygon area (m2) and
`FEATURE_AREA_SQM` is the whole VRI polygon area (m2) — verified on rated
rows: `SHAPE_Area_1 / (AREA_HA x 1e4)` median 1.0000001 and
`FEATURE_AREA_SQM / (POLYGON_AREA x 1e4)` median 1.0. Both are whole-polygon
attributes shared across the rows of their polygon and must never be summed.

The fix scales each rated row by

```
coverage = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)
salvageable = severity_fraction x coverage x live_volume
```

Rated-row ratio distribution over the raw layer: 2,885 rows (47%) below 1
(median of the sub-1 population ~0.16), 3,210 rows (53%) at or above 1 and
therefore clamped; overall median 1.305. The ratio is **not** a true spatial
intersection: when the severity polygon is smaller than the VRI polygon the
ratio assumes the entire severity polygon lies inside this stand, and when it
is larger the clamp assumes full coverage. Salvageable volume on rated
stands is therefore an UPPER BOUND. This caveat is recorded in the `data.py`
module docstring and in the manifest `parameters.coverage_scaling` block.

Fail-fast guards: rated rows with a missing/non-positive
`FEATURE_AREA_SQM` raise `data_coverage_denominator_invalid`; rated rows
missing a positive `SHAPE_Area_1` raise `data_coverage_numerator_invalid`.
Unrated rows are unaffected (fraction 0; coverage set to an inert 1.0). The
real layer triggers neither guard (no null/non-positive areas on rated rows).

### Before/after (post-drop model input, 246,957 retained stands)

Salvageable (burned) volume per severity class, pre-fix vs post-fix stands
tables (`outputs/full_tsa/data/tsa29-full-stands.parquet`; the pre-fix table
was preserved for the comparison):

| Severity | Stands | Burned before | Burned after | Ratio |
| --- | --- | --- | --- | --- |
| High | 1,019 | 43,141.16 | 30,597.32 | 0.709 |
| Moderate | 1,944 | 60,848.18 | 42,243.57 | 0.694 |
| Low | 808 | 15,596.39 | 6,246.49 | 0.400 |
| Unburned | 830 | 0.00 | 0.00 | — |
| Unknown | 10 | 0.00 | 0.00 | — |
| (unrated) | 242,346 | 0.00 | 0.00 | — |
| **Total** | 246,957 | **119,585.72** | **79,087.38** | **0.661** |

Total salvageable volume dropped 34% (−40,498.34 m3). Green volume is
unchanged (19,773,448.62 m3, row-identical); the 77-column output schema is
unchanged; 1,917 rated rows changed value. The post-fix run matches an
independent from-raw-CSV recomputation to the cent (79,087.38). Ingestion
wall time 33.9 s. The new manifest records the source SHA-256 (the pre-fix
manifest predates that field).

Honest magnitude note: the expected "~3x drop" does not hold for this layer.
53% of rated rows clamp to coverage 1 because their severity polygon is at
least as large as the VRI polygon; the volume-weighted correction is 0.66.

## Re-generated results

### WS3 smoke (regression)

`ws3-run --smoke` post-fix: status optimal, 3 periods, objective
24,328,759.75 m3, solve 0.077 s — identical to the pre-fix smoke objective
(WS3 consumes the bridge, not the stands table; regression only).

### 100-year rolling-horizon dev run (clean pair: workers 64 both sides)

| Run | Stands table | Green harvest (m3) | Burned salvage (m3) | Wall (s) |
| --- | --- | --- | --- | --- |
| `outputs/rh_100yr_dev` | pre-fix | 36,248,318.9 | 1,354,926.6 | 150.4 |
| `outputs/rh_100yr_dev2` | post-fix | 36,249,065.0 | 1,354,180.4 | 158.9 |

Delta: −746.2 m3 burned salvage (−0.055%), reclassified to green
(+746.2 m3). The RH impact is small because decadal burned flows are
dominated by *new* MFRI fire influx (~2.01 Mha burned over 100 years), not
by the ~152 kha initially rated 2025 stock; FS-VAL-02 corrects the initial
salvageable stock, which is a minor share of the 100-year salvage total.

### Subsidy flip-point sweep (20 scenarios, post-fix)

`outputs/ensemble_flip_sweep_fixed/`: `subsidy_rate_per_m3` swept
0.0–9.5 $/m3 in 0.5 steps (20 scenarios, per-scenario WS3 `workers: 1`,
`max_workers: 64`); 20/20 optimal, wall 153.6 s.

| Subsidy ($/m3) | Total burned salvage (m3) | Step-1 burned (m3) |
| --- | --- | --- |
| 0.0–9.5 (all 20 levels) | 1,338,477.16 (flat) | 181,238.68 (flat) |

**The pre-fix flip point between 3 and 4 $/m3 does not reproduce.** Salvage
volume is subsidy-invariant across the entire swept window (zero adjacent
delta at all 19 steps). This is structural, not a data accident: the agent
salvage margin is `burned_price − 35 − 5 + subsidy ≈ +90 + subsidy` $/m3
(burned prices are 65% of green), so salvage is maximal at any non-negative
subsidy; the principal's per-cohort cashflow is dominated by green stumpage
(30 $/m3 on the whole standing volume versus a ≤2.8% burned share), so a
≤9.5 $/m3 subsidy never flips an offer. The subsidy survives only as a pure
objective transfer: principal step-1 objective falls ~33.4k $ per $/m3 of
subsidy while the agent step-1 objective rises ~163.0k $ per $/m3 (the
discounted salvaged volume); WS3 objectives are exactly invariant. The old
3→4 $/m3 flip should be regarded as an artifact of the predecessor's binary
threshold structure, not a property of the linear pipeline.

Determinism caveat discovered during this validation: WS3 step objectives
depend slightly on the per-scenario `workers` count (workers 64 vs 1 shift
the step-1 objective by +0.014%), so cross-run comparisons must hold
`workers` fixed. The dev pair above (both workers 64) is the clean
before/after comparison; the sweep is internally consistent (all workers 1).

## Caveats

- **2025-only severity scope.** Every rated row in the layer carries
  `FIRE_YEAR = 2025`. Burns from earlier seasons carry no severity rating
  and therefore no initial salvageable volume; only the 2025 stock seeds
  the year-0 burned inventory.
- **Upper-bound coverage.** The FS-VAL-02 coverage factor is a whole-polygon
  area ratio, not a spatial intersection; rated-stand salvageable volume is
  an upper bound (see the mechanism section and the manifest parameter
  block).
- **Unknown ratings.** 12 raw (10 retained) stands rated "Unknown" are
  treated as unburned under an explicit warning; any other unmatched label
  is now fatal.

## Verification

- `python -m ruff check .` — clean.
- `python -m pytest` — 187 passed (with `PYTHONPATH` pointing at the ws3
  repo); 185 passed + 2 pre-existing ws3-availability skips without it.
  New tests: ladder scenario override + manifest echo, fatal unmatched
  label (labels and counts in the error), coverage scaling 0.3 -> volume
  x0.3, coverage clamp at 1, fatal missing/non-positive denominator, fatal
  missing severity-polygon area, unrated rows unaffected.
- Pipeline re-runs: ingestion 33.9 s; ws3 smoke regression identical; RH
  dev pair and the 20-scenario sweep as tabulated above.
