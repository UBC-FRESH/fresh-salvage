# AGENTS.md

This file is the working contract for AI coding agents in this repository.

## Project Purpose

`masc-yunhao-xu-linear` exists to re-implement the principal-agent
salvage-subsidy model for the Williams Lake Timber Supply Area (TSA29) as a
pure-HiGHS linear pipeline. The goal is not to port the predecessor Gurobi
script bodies. The goal is to rebuild the model so the durable source of truth
is typed data records, compiled WS3 schedules, explicit LP formulations, and
verification evidence — not one-off script chains.

The package should stay aligned with the FRESH ecosystem. It may consume WS3
schedules through the bridge convention and HiGHS through `highspy`, but it
should not re-implement WS3, FHOPS, or other domain packages.

## Current Repo State

This repository has completed the Phase 0 bootstrap scaffold. It contains:

- `README.md`: concise public overview and current status.
- `ROADMAP.md`: phase/task roadmap and issue tracker map.
- `CHANGE_LOG.md`: append-only project narrative.
- `planning/`: focused design notes and research records.
- `pyproject.toml`: package metadata and optional dependency groups.
- `src/masc_yunhao_xu_linear/`: importable package code with CLI stubs and
  module placeholders.
- `tests/`: package-backed tests for package metadata and CLI behavior.
- `docs/`: Sphinx documentation skeleton.
- `examples/`: public-safe example fixtures for later phases.
- `.github/workflows/`: CI, docs, and release-artifact checks.
- `tmp/`: ignored local working area for notes, experiments, and generated
  artifacts.

Do not claim that the package implements data ingestion, WS3 integration,
principal/agent LPs, fire simulation, rolling-horizon coordination, or export
until the relevant roadmap phase records that evidence.

## Workflow Specs And Generated Outputs

Model inputs, compiled schedules, run records, generated reports, scratch
execution logs, and project-specific examples should be treated as local working
material unless the maintainer explicitly asks to track a sanitized artifact.

Rules:

- Keep `tmp/`, `local/`, `data/private/`, and `outputs/` ignored.
- Do not commit private project data, raw transcripts, local workflow outputs,
  credentials, machine-specific paths, or unpublished source documents.
- Do not vendor the predecessor data files; the clean-reboot repo must not
  embed large or private datasets.
- Tracked examples and tests must use synthetic or public-safe fixtures.
- Record provenance for every interpreted data source, WS3 bridge file,
  LP formulation, solver run, environment, and validation result.
- Keep model-specific assumptions explicit rather than silently baking them
  into generic core logic.

## Working Principles

- Read `AGENTS.md`, `ROADMAP.md`, and `CHANGE_LOG.md` before making
  project-shaping changes.
- Keep CLI commands thin wrappers over Python APIs.
- Parse inputs at the boundary into typed Pydantic records; keep core logic
  free of defensive re-validation.
- Keep the linear pipeline linear: continuous HiGHS LPs, no binaries, no
  thresholding or rounding of decision outputs.
- Prefer structured records and parsers over ad hoc string handling.
- Emit explicit diagnostics for missing data, unsupported WS3 features, failed
  solves, uncertain provenance, and failed validation.
- Preserve uncertainty. A model result is only as strong as its declared
  inputs, formulations, and verification evidence.
- Keep public repo content clean of private, irrelevant, or unpublished
  references. Prefer sanitized summaries over raw pasted notes.
- Keep changes scoped to the active roadmap phase and issue.

## Planning Workflow

This repo follows the UBC-FRESH phase/task/subtask workflow:

- `ROADMAP.md` is the current plan and issue tracker map.
- One roadmap phase maps to one GitHub parent issue and one feature branch.
- One roadmap task maps to one child issue linked from the parent issue body.
- Subtasks usually stay as checklist items inside the child issue body.
- Use at most three issue levels: phase, task, implementation subtask.
- Record issue numbers beside roadmap phases and tasks once created.
- Keep `ROADMAP.md`, `CHANGE_LOG.md`, planning notes, issue bodies, and PR
  descriptions synchronized.
- Open a PR from the phase branch to `main` only after phase tasks, tests, docs,
  and closeout notes are complete or explicitly deferred.

## Strict Development Workflow

Use this workflow for active development from the first phase boundary onward:

- One active roadmap phase should generally correspond to one GitHub parent
  issue and one feature branch.
- Create or activate the GitHub parent issue before starting a roadmap phase.
- Create the feature branch from current `main` for that parent issue.
- Create child issues for roadmap tasks under the parent issue.
- Document task subtasks as checklist steps inside the child issue body unless
  they are large enough to deserve third-level implementation issues.
- Work child issues one at a time where practical, usually in roadmap order.
- Before closing a child issue, update every issue-body checklist item to
  checked, or rewrite the issue body to make explicitly clear which items were
  superseded or are not applicable.
- Close each child issue only after its repo changes, documentation, issue-body
  checklist, and verification for that task are complete.
- Keep `ROADMAP.md`, `CHANGE_LOG.md`, and issue comments synchronized as task
  state changes.
- Open a PR from the phase branch back to `main` when the parent issue's child
  issues are complete or explicitly deferred.
- Close the parent issue only after the PR has merged back to `main`.
- Do not start a new active parent issue and branch until the current parent
  issue is closed, unless the maintainer explicitly approves a parallel lane.

## GitHub Issue And Comment Formatting

Formatting matters. GitHub issue bodies and comments must be readable as
rendered Markdown, not flattened prose.

Rules:

- Use short section labels on their own lines, such as `Roadmap task: P1.1`,
  `Parent phase issue: #18`, `Status: active`, and `Checklist:`.
- Use real GitHub task-list syntax, with one checklist item per line.
- Never write inline pseudo-checklists such as
  `Checklist: [ ] first. [ ] second.`
- Wrap branch names, file paths, commands, and commit hashes in backticks.
- For parent phase issues, list child issues as task-list bullets with issue
  numbers and task IDs.
- Before creating or editing several issues, prepare bodies as multi-line
  Markdown strings or temporary body files.

## GitHub Issue Body Quality Standard

Issue bodies are part of the project specification and onboarding material.
Write them so a new lab student, external collaborator, or coding agent can
understand the task, implement it, verify it, and close it without reading the
original chat transcript.

Parent phase issues must include phase identifier, status, branch name, roadmap
links, goal, scope, out-of-scope boundaries, architecture notes, child task
checklist, acceptance criteria, verification, and closeout requirements.

Child task issues must include task identifier, parent phase issue, status,
related planning links, goal, scope, out-of-scope boundaries, subtasks,
acceptance criteria, verification commands, artifacts, risks, and completion
metadata once closed.

Do not create placeholder issue bodies with only a title and a short checklist
unless the maintainer explicitly asks for a placeholder.

## Verification

Default local checks:

```bash
python -m ruff check .
python -m pytest
sphinx-build -b html docs _build/html -W
python -m build
twine check dist/*
```

Default CI must not require private project data, commercial GIS software, local
desktop applications, credentials, Gurobi licenses, or network downloads beyond
package installation.
