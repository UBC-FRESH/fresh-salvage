# fresh-salvage

`fresh-salvage` is an early public-alpha Python package implementing a
clean-reboot, pure-HiGHS linear version of the principal-agent
salvage-subsidy model for the Williams Lake Timber Supply Area (TSA29), British
Columbia.

The predecessor repository (`masc-yunhao-xu`) contains Gurobi binary models
(`P_RH_Version.py`, `A_RH_Version.py`, and the binary `Version 2.py` /
`Version3.3.py` scripts) built on a stand-level subset of the landscape. This
package re-implements those models as continuous linear programs solved with
HiGHS, drops the 11-landscape-unit subset in favour of the full TSA, adds an
annual fire simulation with development-type burn rates (`1/MFRI`), and wraps
the whole pipeline in a rolling-horizon principal-agent coordination loop.

Documentation: https://ubc-fresh.github.io/fresh-salvage/

Repository: https://github.com/UBC-FRESH/fresh-salvage

## Statement Of Need

The predecessor Gurobi models work but are hard to scale and hard to audit. The
binary stand-level formulations limit the number of decision variables that can
be solved under the current setup, the models embed economic assumptions in
script bodies, and the 11-landscape-unit subset does not represent the full TSA
decision problem.

This package starts from a different premise: the model should be a linear,
solver-agnostic pipeline where the durable source of truth is typed data
records, compiled WS3 schedules, and explicit LP formulations. HiGHS removes
the Gurobi license and variable-count limits; the full-TSA WS3 bridge removes
the landscape-unit subset; and the annual fire simulation makes salvage supply
a modelled state rather than a static input.

## Current Alpha Scope

Supported in `0.1.0a1`:

- Python package skeleton using `src/` layout;
- minimal `fresh-salvage` command-line interface with stub commands;
- strict roadmap, changelog, planning, and governance workflow;
- Sphinx documentation;
- CI, documentation, and release-artifact workflows;
- refactor contract documenting the Gurobi-to-HiGHS migration.

Not supported yet:

- model logic (data ingestion, WS3 integration, LPs, fire simulation,
  rolling horizon, export);
- real full-TSA WS3 schedule ingestion;
- stable public APIs or production results.

## Install For Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

Run the local checks:

```bash
python -m ruff check .
python -m pytest
sphinx-build -b html docs _build/html -W
python -m build
twine check dist/*
```

## Command Line

```bash
fresh-salvage --version
fresh-salvage --help
```

Phase 1 ships stub commands that fail fast with a not-implemented diagnostic:

```bash
fresh-salvage ingest
fresh-salvage ws3-run
fresh-salvage solve-principal
fresh-salvage solve-agent
fresh-salvage rh-run
fresh-salvage export
```

Each stub accepts `--json` for deterministic diagnostic output.

## Roadmap

Near-term phases are tracked in `ROADMAP.md`:

- Phase 0: skeleton scaffold, governance, docs, and automation.
- Phase 1: data ingestion and typed input records.
- Phase 2: full-TSA WS3 schedule integration.
- Phase 3: principal-side linear HiGHS LP.
- Phase 4: agent-side linear HiGHS LP and annual fire simulation.
- Phase 5: rolling-horizon coordination loop.
- Phase 6: validation against predecessor Gurobi results.

Development follows the FRESH phase/task/subtask workflow:

- `ROADMAP.md` maps phases and tasks to GitHub issues.
- `CHANGE_LOG.md` records the dated project narrative.
- `planning/` stores focused design notes and decisions.
- One active phase generally maps to one parent issue and feature branch.
- Roadmap tasks map to child issues linked from the parent issue body.

## Public-Repo Hygiene

Do not commit private project data, raw chat transcripts, unpublished source
documents, generated local outputs, or machine-specific paths. Keep scratch
material under ignored local paths such as `tmp/`, `local/`, `data/private/`, or
`outputs/`.

Use GitHub issues for public bug reports, documentation issues, and feature
requests. Do not attach private project material to public issues.
