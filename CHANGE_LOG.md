# Change Log

This is the project narrative for `fresh-salvage`. Newest entries first
(reverse-chronological). Commit references are short hashes on `main`.

## 2026-08-12 — Phase 6: validation, economic recalibration

- Fixed FS-VAL-01/02 (`82688b6`): the burn-severity ladder became a
  scenario-visible parameter with a fatal guard on unmatched non-null
  labels (`data_severity_unmatched`), and severity fractions are now
  coverage-scaled by `min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)` — an
  upper-bound proxy, not a spatial intersection. Total salvageable volume
  on the real layer corrected 119,585.72 -> 79,087.38 m3 (-34%); green
  volume unchanged.
- Updated the validation report with the FS-VAL resolution and corrected
  numbers (`ca65048`); the full audit trail is in
  `planning/phase6-validation-report.md`.
- Economic recalibration (`bb4707e`): all parameters recalibrated against
  BC anchors (Q4-2023 BC Interior log market levels, ILCR-style logging
  costs, new haul costs, South Central stumpage, BC tabular fire-damaged
  floor 0.25 $/m3) and threaded through a config-visible `Economics`
  surface (ingestion scenario, principal/agent config sections, flat
  `RHRunConfig` fields as ensemble axes). Rationale and provenance per
  parameter: `planning/economics-calibration.md`.
- Validation report updated with the recalibration evidence (`0e94ff4`);
  the pre-calibration flat-subsidy finding (salvage subsidy-invariant
  because the placeholder margin was ~+93 $/m3 unsubsidized) is superseded.
- Prompt-salvage adjustment (`e828dc6`): burned grade mix retargeted to
  the year 1-3 prompt-salvage regime (sawlog retention 0.80; the grey-stage
  pulp collapse stays in the 0.85/yr decay where it belongs — the first
  recalibration double-counted it) and the burned cost premium set to +25%
  (harvest 56, transport 38 $/m3). Unsubsidized salvage margin now
  ≈ -15 $/m3 (SPF transition-mix basis; DT band ≈ -10 to -36 $/m3); the
  coupled-system flip sits at ≈ 19.2 $/m3 (turn-on 19.1, saturated by
  19.4), just above the FESBC 14-15 $/m3 benchmark; the default 3 $/m3
  policy produces zero salvage, as calibrated.

## 2026-08-12 — Phase 5: rolling-horizon engine and ensemble driver

- Rolling-horizon coupling engine (`91b4aad`): `rh.py` plus the `rh-run`
  CLI. Each step re-solves a 15-period WS3 schedule from the current cohort
  state (period-0 inventory injection, no full model rebuild), splits the
  period-1 decadal harvest into 10 annual per-cohort green-volume ceilings,
  solves the principal and agent LPs over the implemented decade, replays
  the years with fire dynamics, and transitions the cohort table with
  area conservation verified to 1e-6. 10 steps = 100 implemented years;
  ≈ 150 s per scenario on a 64-core host.
- Review follow-ups (`74bb3b3`): decision-order guard, fail-path gate
  tests, nits.
- Ensemble driver (`01b4f65`): `ensemble.py` plus the `ensemble-run` CLI.
  Cartesian scenario grids over named `RHRunConfig` axes (subsidy rate,
  burn-rate multiplier, any flat economic field); spawn-based process pool;
  the no-LU bridge is built once and shared read-only; a failed scenario is
  recorded and never aborts the ensemble. 4-scenario smoke grid: 4.41x
  speedup; ~1,000 scenarios ≈ 40 min at `max_workers: 64` / `workers: 1`.
- Review follow-ups (`d81e42e`): reserved `bridge_path` axis, verbose
  forwarding, worker-crash test, checksum-first bridge reuse, shared
  subsidy constant, public file checksums.

## 2026-08-12 — Phase 4: agent LP and fire dynamics

- Agent-side harvest/salvage LP (`bde6b96`): continuous fractions of cohort
  standing volume in `[0, 1]`, 1-year timesteps, NPV objective with 3%
  discounting. The LP rows implement the fire dynamics of `fire.py`
  directly (harvest -> fire -> salvage -> decay; `V`/`B` balances; salvage
  feasibility against the on-hand burned inventory; no double selling), so
  the LP and the simulation share one source of truth.

## 2026-08-12 — Phase 3: principal LP

- Principal-side offer LP (`b573f59`): continuous offer fractions per
  cohort-year under the green-volume AAC ceiling (2,937,509 m3/yr),
  expected burned-wood loss weighted by the MFRI burn rate, optional
  burned-volume cap. Added the MFRI fire-rate table (`fire.py`).
- Review follow-ups (`07da484`): structured error contracts, CLI stub
  retirement, area guard, test-gap closures.

## 2026-08-12 — Phase 2: ingestion and WS3 full-TSA pipeline

- Full-TSA data ingestion (`752075c`): pydantic v2 records
  (`ScenarioRunConfig`, `ArtifactLayout`, `Stand`, `DevelopmentType`,
  manifests), a faithful port of the predecessor preprocessing with the
  11-landscape-unit subset filter removed (246,957 stands retained, all 12
  BEC zones), the `development_type` stratum key, and the wired `ingest`
  CLI with `--json`.
- WS3 full-TSA solve pipeline (`11c1f1d`): `WS3RunConfig` and records,
  `ws3.py` port for the entire bridge with age smashing,
  minimum-harvest-age enforcement, the AAC ceiling, even-flow constraints,
  HiGHS solve, and the wired `ws3-run` CLI with `--smoke` and `--json`.
- Bridge rebuild via the femic writer (`08d5014`): the landscape-unit theme
  is dropped at the source from the femic stage-1 Woodstock CSVs, fragment
  ages are smashed to 10-year class midpoints, and femic's own stage-2
  writer aggregates area over the unique cohort keys — the ARE section
  contracts from 44,998 raw rows to 1,608 aggregated, area-conserving
  lines. Depends on the femic writer fix (`femic` commit `d057aea`).
- Review-hardening gates (`01cf464`): area-conservation fail-fast gate on
  the written ARE section and strict age parsing on the bridge rebuild.
- P2.5 verification closed: the 30-period production horizon solves
  end-to-end in 1,407.8 s (compile ≈ 600 s + solve ≈ 774 s); policy set to
  15-period dev / 20-period production horizons, with 30p validated.

## 2026-08-12 — Phase 1: scaffold and rename

- Initial scaffold (`65f1238`): freshforge-style skeleton — governance
  docs, roadmap, changelog, release notes, planning refactor contract,
  package skeleton with Typer CLI stubs, Sphinx docs, and CI/docs/release
  workflows.
- Repo and package rename (`a44ce82`): `masc-yunhao-xu-linear` ->
  `fresh-salvage` (GitHub repository, local directory, import name
  `fresh_salvage`, console script `fresh-salvage`).
