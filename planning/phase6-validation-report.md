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

### Subsidy flip-point sweep — SUPERSEDED by the economic recalibration

The 20-scenario pre-recalibration sweep (`outputs/ensemble_flip_sweep_fixed/`,
subsidy 0.0–9.5 $/m3 in 0.5 steps, per-scenario WS3 `workers: 1`,
`max_workers: 64`; 20/20 optimal, wall 153.6 s) found the subsidy had **no
behavioral effect**: salvage volume was subsidy-invariant at 1,338,477.16 m3
across the whole window, because the placeholder economics gave a salvage
margin of `burned_price − 35 − 5 + subsidy ≈ +90 + subsidy` $/m3 — positive
at any non-negative subsidy. That margin (≈ +$93/m3 for standing fire-killed
wood, unsubsidized) is not credible for the BC Interior, and it triggered
the economic recalibration below.

## Economic recalibration (2026-08-12, supersedes the flat-subsidy finding; superseded in turn by the prompt-salvage adjustment below)

All economic parameters were recalibrated against BC anchors and threaded
through a config-visible `Economics` surface (ingestion scenario section,
principal/agent config sections, flat RHRunConfig fields as ensemble axes).
The per-parameter old/new values, rationale, and sources are in
`planning/economics-calibration.md`; the headline changes are green prices
to the Q4-2023 BC Interior log market level (SPF 127/146/55), harvest costs
45 (green) / 61 (burned), NEW transport costs 30 (green) / 41 (burned),
stumpage 15 (green) / 0.25 (burned, BC tabular fire-damaged floor).

Margin decomposition (pinned by tests):

- SPF sawlog basis: green = 127 − 45 − 30 − 15 = **+37.00 $/m3**;
  salvage = 82.55 − 61 − 41 − 0.25 = **−19.70 $/m3** at subsidy 0.
- Model basis (the agent LP prices cohorts at the development type's
  volume-weighted grade mix; fire degradation pushes ~62% of burned volume
  to pulpwood at 35.75 $/m3): salvage margins at subsidy 0 are **−45.1 to
  −48.1 $/m3 on every development type that carries burned volume** (SPF DTs
  ≈ −47.9 to −48.0) — the unsubsidized salvage benefit is negative for 100%
  of the burned volume. Green margins stay positive everywhere (+5.9 to
  +41.1 $/m3; SPF DTs ≈ +29 to +31).

### Re-run evidence

- Ingestion: 246,957 stands; green 19,773,448.62 m3 and burned 79,087.38 m3
  row-identical to the FS-VAL-02 table; the new manifest echoes the full
  economic parameter set (source sha256 unchanged).
- WS3 smoke regression: objective **24,328,759.75 m3**, bit-identical (WS3
  consumes the bridge, not the economics).
- 100-year RH dev run (`outputs/rh_100yr_calib`, default subsidy 3.0,
  workers 64): status optimal, wall 157.0 s; decadal green harvest
  4.850/4.569/4.238/3.979/3.717/3.479/3.217/2.982/2.726/2.494 M m3
  (total 36,250,960.9 m3); **decadal burned salvage 0.00 m3 in every
  decade** (pre-calibration dev2: 1,354,180.4 m3). The ~201 kha/decade of
  new fire still burns; it is simply no longer economic to recover at a
  3 $/m3 subsidy, and the forgone salvage reclassifies to green
  (+1,895.9 m3 vs dev2).

### Post-calibration flip curve

Prescribed sweep (`outputs/ensemble_flip_calib`, 20/20 optimal, wall
143.9 s): `subsidy_rate_per_m3` in {0, 5, 10, 12, 14, 15, 16, 18, 20, 25} x
`burn_rate_multiplier` in {0.0, 1.0}. Salvage is **exactly 0.00 m3 at every
swept level on both fire axes** — the directive's primary requirement
(salvage strongly reduced at subsidy 0) holds maximally, but no flip occurs
inside 0–25 because the grade-mixed salvage breakeven sits higher. The
subsidy survives there only as a pure transfer: principal step-1 objective
falls ~33.4k $ per $/m3 (both fire axes; the offered burned stock is
behaviorally invariant) while the agent step-1 objective is flat
(130,041,680.42 at burn x1.0) until the flip, then rises with the subsidy
paid on the newly salvaged volume (130.38M at 50, 131.20M at 55).

Supplementary extended sweeps (same base config; marked supplementary, not
part of the prescribed grid) locate the transition:

| Subsidy ($/m3) | Burn x1.0 total salvage (m3) | Step-1 salvage (m3) |
| --- | --- | --- |
| 0–45 (all levels) | 0.00 | 0.00 |
| 47.5 | 0.00 | 0.00 |
| 47.9 | 1,049,997.63 | 138,095.91 |
| 48.0 | 1,338,477.16 | 181,238.68 |
| 48.1–55 | 1,338,477.16 (flat, maximal) | 181,238.68 (flat) |
| any, burn x0.0 | 0.00 (no fire) | 0.00 |

**Identified flip point: subsidy ≈ 47.9–48.0 $/m3** — exactly the
volume-weighted SPF salvage breakeven predicted by the calibration
(burned price 54.29 − costs 102.25 = −47.96 $/m3 margin at subsidy 0). The
response is a sharp step, not a ramp: at 47.9 the lower-breakeven SPF DTs
(ESSF/IDF/SBPS) switch on; by 48.0 the salvage program reaches the maximal
physical level (bit-identical to the pre-calibration flat 1,338,477.16 m3).
Green harvest is identical (36,279,196.26 m3, burn x1.0) at every subsidy —
salvage never displaces green harvest; it monetizes the fire influx on the
offered-but-unharvested slack that would otherwise decay. FESBC-benchmark
subsidies (14–15 $/m3) remain well below the flip: under this cost stack,
benchmark-level support does not make pulp-degraded salvage volume-positive.

Determinism caveat (unchanged): WS3 step objectives shift slightly with
per-scenario `workers` (the ensemble used workers 1; the workers-64 dev run
green total 36,250,960.9 vs the ensemble's 36,279,196.26 reflects this), so
cross-run comparisons must hold `workers` fixed. All sweep rows are
internally consistent (workers 1).

### Before/after summary

| Run | Subsidy ($/m3) | Burned salvage (m3) |
| --- | --- | --- |
| Pre-calibration (`ensemble_flip_sweep_fixed`) | 0.0–9.5 | 1,338,477.16 (flat, subsidy-invariant) |
| Post-calibration (`ensemble_flip_calib`) | 0–25 | 0.00 (flat) |
| Post-calibration (supplementary ext.) | 47.9 | 1,049,997.63 |
| Post-calibration (supplementary ext.) | ≥48.0 | 1,338,477.16 (flat, maximal) |

The subsidy is now a real behavioral instrument with a sharp flip at
≈ 48 $/m3, and the default 3 $/m3 policy produces zero salvage — the
calibrated interior cost structure makes fire-killed, pulp-degraded wood a
net recovery cost absent substantial support.

## Prompt-salvage adjustment (2026-08-12, supersedes the ≈48 $/m3 flip)

User verdict on the ≈48 $/m3 flip: DISTRACTINGLY LARGE — at that gap the
subsidy cost of a flip is obviously several times the benefit NPV, so the
minimum-subsidy question is no longer genuinely open. Cause: the first
recalibration's `BURNED_GRADE_TRANSITION` sent 55% of burned saw volume to
pulpwood at year 0 — a grey-stage (5–10 yr post-fire) outcome — while the
0.85/yr burned-inventory decay already removes grey-stage volume, charging
the time decay twice. The adjustment retargets the economics to the
FRESH/PROMPT-SALVAGE regime (year 1–3), the regime a subsidy program
actually operates in (`planning/economics-calibration.md`):

- `BURNED_GRADE_TRANSITION`: year-1 sawlog retention 0.40 -> 0.80 for every
  species group (Plank 1984; Loeffler & Anderson 2018 red-stage evidence:
  sawlog share 85% -> 73% over years 1–2, lumber value −10%; checking loss
  is already in the 0.65 price discount). Sawlog -> {Saw 0.80, Peel 0.10,
  Pulp 0.10}; Peeler -> {Peel 0.55, Saw 0.35, Pulp 0.10}; Pulpwood stays
  Pulpwood 1.0. The grey-stage collapse stays in the decay term.
- Burned cost premium +35% -> +25% (mild, recently-killed case):
  `BURNED_HARVEST_COST` 61 -> 56, `BURNED_TRANSPORT_COST_PER_M3` 41 -> 38.
- Green prices/costs/stumpage unchanged.

### Post-adjustment margins

Stands-table development-type (DT) economics after re-ingestion (the
agent LP's volume-weighted cohort prices; burned costs now 56 + 38 + 0.25
= 94.25 $/m3):

| Development type | Burned price ($/m3) | Margin @ subsidy 0 | Breakeven subsidy |
| --- | --- | --- | --- |
| Cedar_ESSF | 78.71 | −15.54 | 15.54 |
| Cedar_ICH | 77.80 | −16.45 | 16.45 |
| SPF_ESSF | 75.16 | −19.09 | 19.09 |
| SPF_MS | 75.15 | −19.10 | 19.10 |
| SPF_IDF | 74.88 | −19.37 | 19.37 |
| SPF_SBPS | 74.85 | −19.40 | 19.40 |
| Hem-Bal_ICH | 74.66 | −19.59 | 19.59 |
| SPF_ICH | 74.01 | −20.24 | 20.24 |
| Other_MS | 67.35 | −26.90 | 26.90 |
| Other_ICH | 66.52 | −27.73 | 27.73 |
| Other_SBPS | 64.94 | −29.31 | 29.31 |
| Other_IDF | 63.93 | −30.32 | 30.32 |

The unsubsidized burned-wood marginal benefit is a MODERATE negative band
(−15.5 to −30.3 $/m3; SPF ≈ −19.1): neither trivially small nor
distractingly large. Note the heterogeneity is DT-level, not pure-species:
secondary species slots dilute the leading-species price (Cedar DTs price
below the pure-cedar 84.33 $/m3 mix; Other DTs price above the pure-Other
58.50 $/m3 basket).

### Re-run evidence (post-adjustment)

- Ingestion: re-run (34.7 s) because the stands table's `B_*_Vol` grade
  columns embed the transition; 246,957 stands, source sha256 unchanged
  (`649f8c61...`). Verified UNAFFECTED, row-identical: `green_prices`,
  `burned_prices`, `Total_Green_Vol` (19,773,448.62 m3),
  `Total_Burned_Vol` (79,087.38 m3). Changed as intended: the `B_*` grade
  split (SPF burned sawlog share 0.322 -> 0.6762; SPF DT burned price
  54.29 -> 75.15 $/m3). The manifest echoes the new transition and costs.
- WS3 smoke regression: objective **24,328,759.75 m3**, bit-identical (WS3
  consumes the bridge, not the stands table).
- 100-year RH dev run (`outputs/rh_100yr_calib`, default subsidy 3.0,
  workers 64, 154.9 s, optimal): decadal green 4.849/4.569/4.238/3.979/
  3.717/3.479/3.217/2.982/2.726/2.494 M m3 (total 36,249,151.9 m3);
  **decadal burned salvage 0.00 m3 in every decade** (total 0.00) — the
  expectation at subsidy 3.0, since the smallest DT margin (Cedar_ESSF
  −15.54 + 3.0) stays negative. ~201 kha/decade still burns.
  Pre-adjustment calib run: green 36,250,960.9, salvage 0.00 — the
  −1,809.0 m3 green delta (−0.005%) is the principal's offer LP repricing
  `burned_value` off the new grade mix, inside the known WS3
  workers-sensitivity band (see the determinism caveat).

### Post-adjustment flip curve

Prescribed sweep (`outputs/ensemble_flip_calib`, 26/26 optimal, wall
151.8 s): `subsidy_rate_per_m3` in {0, 5, 8, 10, 12, 14, 15, 16, 18, 20,
22, 25, 30} x `burn_rate_multiplier` in {0.0, 1.0}, per-scenario WS3
workers 1, max_workers 64. Total burned salvage (m3, 100 implemented
years):

| Subsidy ($/m3) | Burn x0.0 | Burn x1.0 | Step-1 salvage, burn x1.0 |
| --- | --- | --- | --- |
| 0 | 0.00 | 0.00 | 0.00 |
| 5 | 0.00 | 0.00 | 0.00 |
| 8 | 0.00 | 0.00 | 0.00 |
| 10 | 0.00 | 0.00 | 0.00 |
| 12 | 0.00 | 0.00 | 0.00 |
| 14 | 0.00 | 0.00 | 0.00 |
| 15 | 0.00 | 0.00 | 0.00 |
| 16 | 0.00 | 0.00 | 0.00 |
| 18 | 0.00 | 0.00 | 0.00 |
| 20 | 0.00 | 1,338,477.16 | 181,238.68 |
| 22 | 0.00 | 1,338,477.16 | 181,238.68 |
| 25 | 0.00 | 1,338,477.16 | 181,238.68 |
| 30 | 0.00 | 1,338,477.16 | 181,238.68 |

Supplementary fine probe (same base config; marked supplementary, not part
of the prescribed grid; `outputs/ensemble_flip_calib_fine`, 5/5 optimal)
resolves the turn-on between 18 and 20 (burn x1.0):

| Subsidy ($/m3) | Total salvage (m3) | Step-1 salvage (m3) | DTs over breakeven |
| --- | --- | --- | --- |
| 19.0 | 0.00 | 0.00 | none |
| 19.1 | 61,549.50 | 15,907.90 | SPF_ESSF (19.088) |
| 19.2 | 288,479.53 | 43,142.77 | + SPF_MS (19.104) |
| 19.3 | 288,479.53 | 43,142.77 | (plateau) |
| 19.4 | 1,338,477.16 | 181,238.68 | + SPF_IDF (19.368), SPF_SBPS (19.397) |

**Turn-on ≈ 19.1 $/m3; ramp across 19.1–19.4; saturation by 19.4** at
1,338,477.16 m3 — bit-identical to the pre-calibration physical maximum
(the same fire influx on the same offered slack). The flip moved from
≈ 48 to ≈ 19.1–19.4 $/m3, exactly the volume-weighted SPF breakevens the
adjusted calibration predicts (SPF DT margin −19.1 $/m3 at subsidy 0).

Why the ramp is narrow in the coupled run: every ARE cohort in the WS3
bridge maps to one of the four SPF development types (SBPS/IDF/MS/ESSF,
breakevens 19.09–19.40), so only the SPF cluster is behaviorally active.
The wider species-level heterogeneity in the price surface (Cedar ≈ 15.5,
Hem-Bal ≈ 19.6, Other ≈ 27–30) does not bind because no bridge stratum
maps to those DTs. The FESBC $14–15/m3 benchmark now sits just BELOW the
turn-on: benchmark-level support closes ~75–80% of the margin gap but does
not flip the program — the minimum-subsidy question is genuinely open.

Objective responses (burn x1.0): pre-flip the principal step-1 objective
falls ~33.4k $ per $/m3 of subsidy (a pure transfer on the offered burned
stock; the offered stock is behaviorally invariant) while the agent step-1
objective is flat at 130,041,680.42 (bit-identical to the pre-adjustment
sweeps — green economics unchanged); post-flip the agent objective rises
with the subsidy paid on newly salvaged volume (130.15M at 20, 130.48M at
22, 130.97M at 25, 131.78M at 30).

Green invariance: green harvest is identical at every subsidy —
36,279,196.26 m3 at burn x1.0 (max pairwise spread 7.5e-9, solver
precision) and 48,622,159.93 m3 at burn x0.0; salvage never displaces
green harvest. Burn x0.0 salvage is 0.00 at every subsidy (no fire).

### Before/after summary (adjusted)

| Calibration state | Subsidy ($/m3) | Burned salvage (m3) |
| --- | --- | --- |
| Pre-calibration (placeholder economics) | 0.0–9.5 | 1,338,477.16 (flat, subsidy-invariant) |
| First recalibration (grey-stage mix, +35% premium) | 0–47.9 | 0.00; step to maximal at ≈48.0 |
| **Prompt-salvage adjustment (this round)** | 0–19.0 | 0.00 |
| | 19.1 | 61,549.50 |
| | 19.2–19.3 | 288,479.53 |
| | ≥19.4 | 1,338,477.16 (flat, maximal) |

The subsidy remains a real behavioral instrument, now with the flip in the
high teens (≈19.1–19.4 $/m3) instead of ≈48, and the default 3 $/m3 policy
still produces zero salvage.

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
- `python -m pytest` — 198 passed (with `PYTHONPATH` pointing at the ws3
  repo). FS-VAL tests: ladder scenario override + manifest echo, fatal
  unmatched label (labels and counts in the error), coverage scaling
  0.3 -> volume x0.3, coverage clamp at 1, fatal missing/non-positive
  denominator, fatal missing severity-polygon area, unrated rows unaffected.
  Recalibration tests: pinned economic constants (burned costs 56/38),
  margin decomposition (green +37 on the SPF sawlog basis; salvage margins
  at subsidy 0 of −11.70 sawlog basis, −15.145 transition mix, −19.10
  development-type mix), prompt-salvage grade-transition math
  (200 x 0.30 x (0.805 x 0.80 + 0.092 x 0.35) = 40.572), agent behavior at
  subsidy 0 (no salvage) vs 25 (full influx salvage), economics scenario
  override + manifest echo, `Economics` validation fail-fast, RHRunConfig
  flat-field assembly and ensemble-axis acceptance.
- Pipeline re-runs (post-adjustment): ingestion 34.7 s (price columns and
  green/burned totals row-identical, `B_*` grade mix updated, new manifest
  echo); ws3 smoke regression bit-identical (24,328,759.75 m3); RH dev run
  `outputs/rh_100yr_calib` (optimal, 154.9 s, zero salvage at the default
  subsidy); prescribed 26-scenario flip sweep
  (`outputs/ensemble_flip_calib`, 26/26 optimal, 151.8 s) plus the
  supplementary 5-scenario fine probe (`outputs/ensemble_flip_calib_fine`,
  5/5 optimal) resolving the turn-on at 19.1–19.4 $/m3.
