# fresh-salvage Roadmap

This roadmap is the current project plan and issue tracker map. Keep it
synchronized with GitHub issues, planning notes, pull requests, and
`CHANGE_LOG.md`.

## Issue Tracker Map

| Phase | Parent issue | Branch | Status |
| --- | --- | --- | --- |
| P0 Skeleton scaffold | TBD | `feature/p0-skeleton-scaffold` | Complete |
| P1 Data ingestion and typed input records | TBD | `feature/p1-data-ingestion` | Complete |
| P2 Full-TSA WS3 schedule integration | TBD | `feature/p2-ws3-integration` | Complete |
| P3 Principal-side linear HiGHS LP | TBD | `feature/p3-principal-lp` | Complete |
| P4 Agent-side linear HiGHS LP and annual fire simulation | TBD | `feature/p4-agent-fire` | Complete |
| P5 Rolling-horizon coordination loop and ensemble driver | TBD | `feature/p5-rolling-horizon` | Complete |
| P6 Validation, calibration, and documentation | TBD | `main` | Active (P6.2 docs in flight) |

## Phase 0: Skeleton Scaffold

Parent issue: TBD

Branch: `feature/p0-skeleton-scaffold`

Status: complete

Goal: establish `fresh-salvage` as a public package-backed UBC-FRESH
project with strict governance, planning, docs, CI, release-artifact checks, a
minimal importable Python package, and stub CLI commands.

- [x] P0.1 Governance and planning scaffold
  - [x] Add public governance files (`README.md`, `LICENSE`, `CITATION.cff`,
        `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `AGENTS.md`).
  - [x] Add roadmap, changelog, and release notes.
  - [x] Add the refactor contract in `planning/phase0-refactor-contract.md`.
  - [x] Document strict issue and roadmap workflow in `AGENTS.md`.
- [x] P0.2 Python package and CLI skeleton
  - [x] Add package metadata and dependency extras.
  - [x] Add minimal package module and Typer CLI with stub commands.
  - [x] Add module stubs for later phases.
  - [x] Add focused package and CLI tests.
- [x] P0.3 Docs, CI, Pages, and release-artifact scaffold
  - [x] Add Sphinx configuration and docs pages.
  - [x] Add CI workflow for Python 3.11 and 3.12.
  - [x] Add docs Pages workflow.
  - [x] Add release artifact workflow with testpypi/pypi dispatch target.
- [x] P0.4 Phase closeout and verification
  - [x] Run local acceptance commands.
  - [x] Commit and push to `main`.

Evidence: `65f1238` (initial scaffold), `a44ce82` (rename to
`fresh-salvage`).

## Phase 1: Data Ingestion And Typed Input Records

Parent issue: TBD

Branch: `feature/p1-data-ingestion`

Status: complete

Goal: ingest the predecessor data sources at the pipeline boundary and parse
them into typed Pydantic records that the LP and simulation phases can trust
without re-validation.

- [x] P1.1 Define input record models.
- [x] P1.2 Implement boundary parsers for predecessor CSV inputs.
- [x] P1.3 Wire the `ingest` CLI command to the ingestion API.
- [x] P1.4 Add synthetic public-safe fixtures and tests.
- [x] P1.5 Docs, roadmap, changelog, and verification closeout.

Evidence: `752075c`. The full-TSA ingestion retains 246,957 stands across
all 12 BEC zones (no landscape-unit subset filter); manifests record the
source SHA-256 and the effective severity/economic parameters.

## Phase 2: Full-TSA WS3 Schedule Integration

Parent issue: TBD

Branch: `feature/p2-ws3-integration`

Status: complete

Goal: replace the predecessor 11-landscape-unit subset with the full TSA29 WS3
bridge, compiling schedule records for every development type and period.

- [x] P2.1 Compile normalized WS3 schedule records from the bridge files.
- [x] P2.2 Provide deterministic provenance for bridge inputs.
- [x] P2.3 Wire the `ws3-run` CLI command to the WS3 API.
- [x] P2.4 Add tests and docs.
- [x] P2.5 Verification and closeout.

Evidence: `11c1f1d` (WS3 pipeline), `08d5014` (no-LU bridge rebuild via the
femic writer: LU theme dropped at source, 10-year midpoint age smashing,
femic stage-2 writer aggregation; ARE section aggregated to 1,608
midpoint-smashed lines),
`01cf464` (area-conservation fail-fast gate and strict age parsing).
External dependency: femic writer fix `d057aea`.

P2.5 measured answer (the 10-period gate question): the 30-period
production horizon solves end-to-end in 1,407.8 s (compile ~600 s + solve
~774 s). Policy: 15-period horizon for development, 20-period for
production; 30p validated. The 3-period smoke profile remains the fast
deterministic regression gate.

## Phase 3: Principal-Side Linear HiGHS LP

Parent issue: TBD

Branch: `feature/p3-principal-lp`

Status: complete

Goal: re-implement the principal-side salvage-subsidy model as a continuous
linear HiGHS LP that emits offer fractions per aggregate cohort.

- [x] P3.1 Define principal LP formulation from the refactor contract.
- [x] P3.2 Implement the HiGHS driver.
- [x] P3.3 Wire the `principal-run` CLI command.
- [x] P3.4 Add synthetic tests and parity checks where possible.
- [x] P3.5 Verification and closeout.

Evidence: `b573f59` (principal LP plus the MFRI fire-rate table),
`07da484` (review follow-ups: structured error contracts, CLI stub
retirement, area guard). Continuous offer fractions in `[0, 1]`, green AAC
ceiling 2,937,509 m3/yr, MFRI-weighted expected burned loss.

## Phase 4: Agent-Side Linear HiGHS LP And Annual Fire Simulation

Parent issue: TBD

Branch: `feature/p4-agent-fire`

Status: complete

Goal: re-implement the agent-side harvest decision as a continuous linear
HiGHS LP bounded by the principal offer, and add an annual fire simulation
that generates salvage supply using development-type burn rates (`1/MFRI`).

- [x] P4.1 Define agent LP formulation from the refactor contract.
- [x] P4.2 Implement the HiGHS driver.
- [x] P4.3 Implement the annual fire simulation.
- [x] P4.4 Wire the `agent-run` CLI command.
- [x] P4.5 Add synthetic tests and docs.
- [x] P4.6 Verification and closeout.

Evidence: `bde6b96`. The agent LP rows implement the `fire.py` dynamics
directly (harvest -> fire -> salvage -> decay; live/burned balances;
salvage feasibility; no double selling), so the LP and the simulation share
one source of truth.

## Phase 5: Rolling-Horizon Coordination Loop And Ensemble Driver

Parent issue: TBD

Branch: `feature/p5-rolling-horizon`

Status: complete

Goal: coordinate principal and agent models in a rolling-horizon loop with
state updates across periods, and scale to scenario ensembles.

- [x] P5.1 Define rolling-horizon state and iteration semantics.
- [x] P5.2 Implement the coordination loop.
- [x] P5.3 Wire the `rh-run` CLI command and add the ensemble driver.
- [x] P5.4 Add synthetic integration tests.
- [x] P5.5 Verification and closeout.

Evidence: `91b4aad` (rolling-horizon engine, 100-year end-to-end run),
`74bb3b3` (review fixes), `01b4f65` (ensemble driver with spawn process
pool and budget measurement), `d81e42e` (review fixes). Measured
performance: ~150 s per 100-year scenario; 4.41x speedup on the 4-scenario
smoke ensemble; ~1,000 scenarios in ~40 minutes at `max_workers: 64` /
per-scenario `workers: 1`.

## Phase 6: Validation, Calibration, And Documentation

Parent issue: TBD

Branch: `main`

Status: active (P6.1 complete; P6.2 documentation pass in flight)

Goal: validate the linear pipeline, fix what the audit finds, calibrate the
economic surface against BC anchors, and ship the documentation suite.

- [x] P6.1 Validation and economic calibration.
  - [x] FS-VAL-01/02 severity audit and fixes (`82688b6`), validation
        report update (`ca65048`): parameterized severity ladder with fatal
        unmatched-label guard; coverage-scaled burned volume (upper-bound
        caveat documented); salvageable stock corrected to 79,087.38 m3.
  - [x] Economic recalibration (`bb4707e`, evidence `0e94ff4`): BC-anchored
        prices/costs/stumpage, config-visible `Economics` surface.
  - [x] Prompt-salvage adjustment (`e828dc6`): grade mix retargeted to the
        year 1-3 regime. Grade-transition monotonicity erratum (2026-08-13):
        the Saw row's 0.10 Saw->Peel share was a physically impossible
        upgrade, moved to pulp (Saw 0.80 / Peel 0.00 / Pulp 0.20);
        unsubsidized salvage margin ≈ -21 $/m3; flip ≈ 23.9-24.1 $/m3;
        FESBC benchmark 14-15 $/m3 sits well below the turn-on. Full
        record: `planning/phase6-validation-report.md` and
        `planning/economics-calibration.md`.
- [ ] P6.2 Documentation suite and release notes (README, Sphinx guides,
      `CHANGE_LOG.md`, this roadmap, `RELEASE_NOTES.md`, version 0.1.0a1).

## Current Next Steps

Phases 0-5 and P6.1 are complete on `main`; P6.2 (this documentation pass)
closes Phase 6. Beyond that:

- **Full-BC scale-up.** Port the pipeline from the single-TSA (TSA29) study
  area to additional TSAs toward a full-BC salvage-subsidy analysis, in
  coordination with the UBC FRESH Lab program.
- **Production hardening.** Stable public APIs, the reserved `export`
  command, expanded integration coverage, and release packaging for a
  non-alpha version line.
