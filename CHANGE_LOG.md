# Change Log

Newest entries last. Keep this file synchronized with roadmap phase/task
completion and GitHub issue comments.

## 2026-08-12

- Created the Phase 0 bootstrap lane for `masc-yunhao-xu-linear` on
  `feature/p0-skeleton-scaffold`.
- Began scaffolding the clean-reboot linear implementation of the TSA29
  principal-agent salvage-subsidy pipeline as a package-backed UBC-FRESH
  project with pure-HiGHS linear programming as the solver direction.
- Added strict FRESH governance docs, roadmap, changelog, release notes,
  planning refactor contract, contribution and public-repo hygiene files, a
  minimal `masc_yunhao_xu_linear` package with a Typer CLI and stub commands,
  module stubs for later phases, Sphinx RTD-theme docs, and
  CI/docs/release-artifact workflows.
- Recorded the refactor contract in `planning/phase0-refactor-contract.md`,
  covering the migration from the predecessor Gurobi models
  (`P_RH_Version.py`, `A_RH_Version.py`, binary `Version 2.py` and
  `Version3.3.py`) to continuous linear HiGHS LPs, the drop of the 11-LU
  subset in favour of the full TSA, the annual fire simulation with DT-wise
  burn rate `1/MFRI`, and the predecessor data sources.
- Verified Phase 0 locally with editable install, `ruff check`, `pytest`, and
  `masc-yunhao-xu-linear --help`.

## 2026-08-12

- Implemented Phase 2 (full-TSA data ingestion) on `main`:
  - Added pydantic v2 records in `models.py` mirroring the figrecover house
    style: `ScenarioRunConfig` (with `read()`/`write_json()` for JSON/YAML),
    `ArtifactLayout` with `data`/`manifests`/`logs` directories, `Stand` and
    `DevelopmentType` records, `Diagnostic`, `IngestManifest`, and
    `IngestResult`.
  - Implemented `data.ingest()` as a faithful port of the predecessor
    `DP_PA.py` preprocessing (severity-to-burned fractions, burned grade
    transitions, species grading splits, green/burned prices, subsidy and
    stumpage rates) with the 11-landscape-unit subset filter removed, so the
    pipeline covers the full TSA29.
  - Reproduced the 75-column `Gurobi_test1.csv` schema and added
    `BEC_ZONE_CODE` and the `development_type` stratum key
    (`{leading_species_group}_{BEC_ZONE_CODE}`).
  - Wired the `ingest` CLI command to the ingestion API with deterministic
    JSON and Rich summary output; other commands remain stubs.
  - Added the local example scenario `examples/scenario_tsa29.yaml`, synthetic
    model/data/CLI tests, and a run-manifest evidence artifact.
  - Design notes: the dataset labels the mid burn-severity tier "Medium"
    (predecessor mapping "Moderate"); `data.py` normalizes `Medium` ->
    `Moderate` at the boundary instead of silently treating it as unburned.
    All 12 BEC zones present in the layer are retained (no zone filtering).

## 2026-08-12

- Repo renamed `masc-yunhao-xu-linear` -> `fresh-salvage`: GitHub repository,
  local directory, Python package import name (`masc_yunhao_xu_linear` ->
  `fresh_salvage`), and console script (`masc-yunhao-xu-linear` ->
  `fresh-salvage`).

## 2026-08-12

- Implemented Phase 2 (full-TSA WS3 schedule integration):
  - Added `WS3RunConfig` (run id, bridge path, base year, horizon, period
    length, max age, worker count, AAC) with no landscape-unit filter,
    `WS3Objective` (clear-cut action, utilization, even-flow tolerance),
    `AgeSmashing` (10-year width, midpoint 5), `WS3Manifest` (bridge
    checksums and config snapshot), and `WS3Result` (per-period volumes and
    areas, solve seconds, artifact paths).
  - Implemented `ws3.py` as a full port of the predecessor
    `ws3_masc_integration.py` for the entire 44,998-row ARE bridge (2,405
    development types): age smashing, minimum-harvest-age enforcement,
    AAC ceiling constraints, even-flow constraints, HiGHS solve, and
    deterministic schedule compilation.
  - Wired the real `ws3-run` CLI command with `--smoke` and `--json` modes;
    `solve-principal`, `solve-agent`, `rh-run`, and `export` remain stubs.
  - Ingest manifests now record `source_sha256` of the source WL_VFSL file.
  - Added `examples/ws3_tsa29.yaml` and unit tests for configs and pure
    helpers; installed ws3 runtime dependencies (`dill`, `scipy`,
    `rasterio`, `fiona`) in the local venv.
  - Verified: 53 tests pass, `ruff check` clean, 3-period full-TSA smoke run
    solves to optimal (0.7 s, 3,500 schedule rows). The 10-period gate run
    exceeded 10 minutes and is awaiting a decision; Phase 2 closeout is open.


