# Phase 0 Refactor Contract: Gurobi Binary Models to Linear HiGHS Pipeline

Status: adopted (Phase 0)

Date: 2026-08-12

## Purpose

This note is the working contract for the clean-reboot re-implementation of the
TSA29 principal-agent salvage-subsidy model. It records what the predecessor
repository did, what the linear pipeline will do, and the boundaries that later
phases must respect. It is the authoritative scope reference for phases 1-6 in
`ROADMAP.md`.

## Predecessor Models (Gurobi, in `masc-yunhao-xu`)

| Predecessor file | Location | Role | Formulation |
| --- | --- | --- | --- |
| `P_RH_Version.py` | `Gurobi/Principal's model/` and `Gurobi/Rolling_horizon_structure/` | Principal-side model: forest-management / salvage-subsidy offer | Gurobi LP with continuous and binary decision variables over stands and years; revenue components for green and burned timber, stumpage, subsidy, decay of burned wood |
| `A_RH_Version.py` | `Gurobi/Agent's model/` and `Gurobi/Rolling_horizon_structure/` | Agent-side model: harvesting operations profit | Gurobi LP over stands and years; green/burned revenue, harvest costs, stumpage, subsidy; bounded by principal offer |
| `Version 2.py` | `Gurobi/Principal's model/` | Binary stand-level principal prototype | Binary harvest decisions per stand/year; hard-coded price schedules; study-area AAC constants |
| `Version3.3.py` | `Gurobi/Agent's model/` | Binary stand-level agent prototype | Binary purchase decisions per stand/year; reads `Principal_Offer.csv`; price schedules with burned prices at 65% of green |

The predecessor README records the study context and known limitations:
missing subsidies, operational costs, AAC, green/burned harvest volume
constraints, and burned-wood decay rate; the solver note recommends HiGHS as an
alternative because Gurobi variable-count limits constrain the stand-level
binary setup; and the data sources are VRI
(`VEG_COMP_LYR_R1_POLY_2024`), fire severity
(`WHSE_FOREST_VEGETATION.VEG_BURN_SEVERITY_SP`), TSA boundary
(`WHSE_ADMIN_BOUNDARIES.FADM_TSA`), landscape units
(`WHSE_LAND_USE_PLANNING.RMP_LANDSCAPE_UNIT_SVW`), timber prices
(`1mi_mar_26` Interior timber market report), and stumpage
(`may2026_interior_all_non-appraised`).

## Refactor Decisions

### 1. Linear HiGHS LPs, not binary Gurobi models

The predecessor binary stand-level models become continuous linear programs
solved with HiGHS through `highspy`. Decision variables are aggregate
fractions:

- Principal emits raw `offer_fraction` in `[0, 1]` per aggregate opportunity
  `(development_type, age_class, period, harvest_action)`.
- Agent emits raw `purchase_fraction` in `[0, 1]` bounded by that offer.

No binary variables, no thresholding, and no rounding of decision outputs.
Zero decisions are emitted explicitly.

### 2. Full TSA, not the 11-LU subset

The predecessor subset extraction (landscape units
`1376, 1378, 1382, 1383, 1384, 1387, 1389, 1390, 1391, 1393, 1404`) is
dropped. The linear pipeline compiles the full-TSA29 WS3 bridge into
normalized schedule records with required columns `development_type`,
`age_class`, `period`, `harvest_action`, `available_area`,
`green_volume_per_area`, and `salvage_volume_per_area`, plus optional
economics columns. Aggregation is aspatial: no `FEATURE_ID`, stand identity,
rasterization, or spatial join.

### 3. Annual fire simulation with DT-wise burn rate 1/MFRI

Salvage supply becomes a modelled state, not a static input. An annual fire
simulation burns development-type (DT) area with a per-DT burn rate of
`1/MFRI` (mean fire return interval), converting standing inventory into
salvage supply for the pipeline periods. Fire dynamics stay outside the LPs:
the LPs consume the burned inventory produced by the simulation.

### 4. Data sources

The pipeline ingests the predecessor data sources at the boundary (VRI, fire
severity, TSA boundary, landscape units, timber prices, stumpage) plus the
full-TSA WS3 bridge. Predecessor data files are NOT vendored in this
repository; ingestion reads external paths and records provenance.

## Boundaries

- May: continuous linear HiGHS LPs, full-TSA WS3 schedule compilation,
  annual fire simulation with `1/MFRI` DT-wise burn rate, rolling-horizon
  state carry-forward with optional salvage decay, deterministic JSON/tabular
  exports.
- Must not: use Gurobi, use binary decision variables, round or threshold
  decision outputs, restrict to the predecessor 11-LU subset, vendor
  predecessor data files, or claim production results without approved
  external configuration.

## Phase Mapping

| Roadmap phase | Contract section |
| --- | --- |
| P1 Data ingestion | 4 |
| P2 WS3 schedule integration | 2 |
| P3 Principal LP | 1 |
| P4 Agent LP + fire simulation | 1, 3 |
| P5 Rolling horizon | 1 |
| P6 Validation | all |
