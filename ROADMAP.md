# masc-yunhao-xu-linear Roadmap

This roadmap is the current project plan and issue tracker map. Keep it
synchronized with GitHub issues, planning notes, pull requests, and
`CHANGE_LOG.md`.

## Issue Tracker Map

| Phase | Parent issue | Branch | Status |
| --- | --- | --- | --- |
| P0 Skeleton scaffold | TBD | `feature/p0-skeleton-scaffold` | Complete |
| P1 Data ingestion and typed input records | TBD | `feature/p1-data-ingestion` | Planned |
| P2 Full-TSA WS3 schedule integration | TBD | `feature/p2-ws3-integration` | Planned |
| P3 Principal-side linear HiGHS LP | TBD | `feature/p3-principal-lp` | Planned |
| P4 Agent-side linear HiGHS LP and annual fire simulation | TBD | `feature/p4-agent-fire` | Planned |
| P5 Rolling-horizon coordination loop | TBD | `feature/p5-rolling-horizon` | Planned |
| P6 Validation against predecessor Gurobi results | TBD | `feature/p6-validation` | Planned |

## Phase 0: Skeleton Scaffold

Parent issue: TBD

Branch: `feature/p0-skeleton-scaffold`

Status: complete

Goal: establish `masc-yunhao-xu-linear` as a public package-backed UBC-FRESH
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

Phase 0 local verification passed with:

- `python -m pip install -e .[dev]`
- `python -m ruff check .`
- `python -m pytest`
- `masc-yunhao-xu-linear --help`

## Phase 1: Data Ingestion And Typed Input Records

Parent issue: TBD

Branch: `feature/p1-data-ingestion`

Status: planned

Goal: ingest the predecessor data sources at the pipeline boundary and parse
them into typed Pydantic records that the LP and simulation phases can trust
without re-validation.

- [ ] P1.1 Define input record models.
- [ ] P1.2 Implement boundary parsers for predecessor CSV inputs.
- [ ] P1.3 Wire the `ingest` CLI command to the ingestion API.
- [ ] P1.4 Add synthetic public-safe fixtures and tests.
- [ ] P1.5 Docs, roadmap, changelog, and verification closeout.

Acceptance boundary:

- May parse and validate predecessor-format inputs into typed records.
- Must not vendor predecessor data files.
- Must not implement LP or simulation logic.

## Phase 2: Full-TSA WS3 Schedule Integration

Parent issue: TBD

Branch: `feature/p2-ws3-integration`

Status: planned

Goal: replace the predecessor 11-landscape-unit subset with the full TSA29 WS3
bridge, compiling schedule records for every development type and period.

- [ ] P2.1 Compile normalized WS3 schedule records from the bridge files.
- [ ] P2.2 Provide deterministic provenance for bridge inputs.
- [ ] P2.3 Wire the `ws3-run` CLI command to the WS3 API.
- [ ] P2.4 Add tests and docs.
- [ ] P2.5 Verification and closeout.

Acceptance boundary:

- May consume the full-TSA WS3 bridge through the FRESH bridge convention.
- Must not re-implement WS3.
- Must not use the predecessor 11-LU stand subset.

## Phase 3: Principal-Side Linear HiGHS LP

Parent issue: TBD

Branch: `feature/p3-principal-lp`

Status: planned

Goal: re-implement the principal-side salvage-subsidy model as a continuous
linear HiGHS LP that emits offer fractions per aggregate opportunity.

- [ ] P3.1 Define principal LP formulation from the refactor contract.
- [ ] P3.2 Implement the HiGHS driver.
- [ ] P3.3 Wire the `solve-principal` CLI command.
- [ ] P3.4 Add synthetic tests and parity checks where possible.
- [ ] P3.5 Verification and closeout.

Acceptance boundary:

- May emit continuous offer fractions in `[0, 1]`.
- Must stay linear (no binaries, no rounding).
- Must not claim production results without approved configuration.

## Phase 4: Agent-Side Linear HiGHS LP And Annual Fire Simulation

Parent issue: TBD

Branch: `feature/p4-agent-fire`

Status: planned

Goal: re-implement the agent-side harvest decision as a continuous linear HiGHS
LP bounded by the principal offer, and add an annual fire simulation that
generates salvage supply using development-type burn rates (`1/MFRI`).

- [ ] P4.1 Define agent LP formulation from the refactor contract.
- [ ] P4.2 Implement the HiGHS driver.
- [ ] P4.3 Implement the annual fire simulation.
- [ ] P4.4 Wire the `solve-agent` CLI command.
- [ ] P4.5 Add synthetic tests and docs.
- [ ] P4.6 Verification and closeout.

Acceptance boundary:

- May emit continuous purchase fractions bounded by the principal offer.
- May simulate annual fire with DT-wise burn rate `1/MFRI`.
- Must not embed fire dynamics inside the LPs.

## Phase 5: Rolling-Horizon Coordination Loop

Parent issue: TBD

Branch: `feature/p5-rolling-horizon`

Status: planned

Goal: coordinate principal and agent models in a rolling-horizon loop with
state updates across periods and optional salvage decay.

- [ ] P5.1 Define rolling-horizon state and iteration semantics.
- [ ] P5.2 Implement the coordination loop.
- [ ] P5.3 Wire the `rh-run` CLI command.
- [ ] P5.4 Add synthetic integration tests.
- [ ] P5.5 Verification and closeout.

Acceptance boundary:

- May carry aggregate remaining area forward between periods.
- May apply the documented optional salvage decay.
- Must keep outputs deterministic and threshold-free.

## Phase 6: Validation Against Predecessor Gurobi Results

Parent issue: TBD

Branch: `feature/p6-validation`

Status: planned

Goal: validate the linear HiGHS pipeline against the predecessor Gurobi results
and document remaining structural differences.

- [ ] P6.1 Define parity metrics and acceptance tolerances.
- [ ] P6.2 Build regression fixtures from public-safe synthetic inputs.
- [ ] P6.3 Wire the `export` CLI command for result artifacts.
- [ ] P6.4 Document limitations and open questions.
- [ ] P6.5 Verification and closeout.

Acceptance boundary:

- May compare aggregate objectives, volumes, and selected area against the
  predecessor models on synthetic fixtures.
- Must not claim exact parity with the binary stand-level models where the
  formulations differ structurally.

## Current Next Steps

Phase 0 is complete on `main`. The next phase is Phase 1 (data ingestion and
typed input records) on `feature/p1-data-ingestion`.
