# fresh-salvage

`fresh-salvage` is an open-source Python package implementing a linear
principal-agent salvage-subsidy model for the Williams Lake Timber Supply
Area (TSA29), British Columbia. The pipeline is pure HiGHS: every
optimization layer is a continuous linear program solved through `highspy`,
with no commercial solver dependency.

The predecessor repository (`masc-yunhao-xu`) contains Gurobi binary
stand-level models built on an 11-landscape-unit subset of the TSA. This
package re-implements the model as continuous linear programs over aggregate
cohorts, drops the landscape-unit subset in favour of the full TSA, adds an
annual MFRI-driven fire simulation, and wraps the principal and agent LPs in
a rolling-horizon coordination loop with a parallel scenario-ensemble driver.

Documentation: https://ubc-fresh.github.io/fresh-salvage/

Repository: https://github.com/UBC-FRESH/fresh-salvage

## Statement Of Need

The predecessor Gurobi models work but are hard to scale and hard to audit.
The binary stand-level formulations hit solver variable-count limits, the
models embed economic assumptions in script bodies, and the 11-landscape-unit
subset does not represent the full-TSA decision problem.

This package starts from a different premise: the durable source of truth is
typed data records, compiled WS3 schedules, and explicit LP formulations.
HiGHS removes the Gurobi license and variable-count limits; the full-TSA WS3
bridge removes the landscape-unit subset; the annual fire simulation makes
salvage supply a modelled state rather than a static input; and every run
emits a provenance manifest so results stay auditable.

## Pipeline Overview

The model is a five-layer pipeline. Each layer is a typed module with a CLI
entry point and a manifest-emitting run record.

1. **Ingestion** (`fresh_salvage.data`). The external VRI polygon layer
   (`WL_VFSL.csv`) is parsed into typed stand records for the full TSA29
   (246,957 stands). Burn-severity ratings are mapped through a
   scenario-visible ladder with coverage scaling; the economic surface
   (prices, costs, stumpage, subsidy) is attached at the boundary.
2. **WS3 wood supply** (`fresh_salvage.ws3`). A Landscape-Unit-free WS3
   bridge is rebuilt from the femic stage-1 Woodstock CSVs: fragment ages
   are smashed to 10-year class midpoints and femic's own writer aggregates
   area over unique cohort keys (44,998 raw ARE rows become 1,608 aggregated
   cohorts, gated by area conservation). WS3 solves the wood-supply schedule
   over the full TSA with a `cc` clear-cut action operable at ages [60, 300]
   and the 2,937,509 m3/yr AAC ceiling.
3. **Principal LP** (`fresh_salvage.principal`). The principal chooses
   continuous offer fractions per cohort and year (1-year timesteps),
   maximizing stumpage net of subsidy minus expected burned-wood loss, under
   the green-volume AAC ceiling.
4. **Agent LP** (`fresh_salvage.agent`). The agent chooses continuous
   harvest and salvage fractions bounded by the principal's offers,
   maximizing discounted NPV under annual fire dynamics: MFRI-derived burn
   rates generate burned volume year by year, unsalvaged burned volume
   decays at 0.85/yr, and the subsidy accrues per m3 actually salvaged.
5. **Rolling-horizon engine and ensemble driver** (`fresh_salvage.rh`,
   `fresh_salvage.ensemble`). The rolling-horizon engine runs 10 decadal
   steps (100 years): each step re-solves a 15-period WS3 schedule from the
   current cohort state, splits the period-1 decadal harvest into annual
   ceilings, solves the principal and agent LPs over the implemented decade,
   replays the years with fire dynamics, and injects the transitioned
   cohort inventory back into WS3. The ensemble driver maps a cartesian
   scenario grid over named config axes (subsidy, fire multiplier, any
   economic field) onto a spawn-based process pool.

## Install For Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

Two external FRESH-ecosystem dependencies are resolved from source
checkouts, not from PyPI:

- `ws3` must be importable, typically via a sibling checkout on
  `PYTHONPATH` (for example `export PYTHONPATH=/path/to/ws3`);
- the femic bridge writer is imported from the femic source tree, resolved
  via the `FEMIC_SRC` environment variable (falling back to the configured
  default path).

## Quickstart

Every command reads a YAML or JSON config and accepts `--json` for
deterministic machine-readable output. Example configs live in `examples/`;
edit the paths to point at your own data and run outputs.

```bash
# 1. Ingest the full-TSA stand layer into typed records.
fresh-salvage ingest examples/scenario_tsa29.yaml

# 2. Compile and solve the full-TSA WS3 wood-supply schedule.
fresh-salvage ws3-run examples/ws3_tsa29.yaml          # add --smoke for a fast check

# 3. Solve the principal offer LP.
fresh-salvage principal-run examples/principal_tsa29.yaml

# 4. Solve the agent harvest/salvage LP.
fresh-salvage agent-run examples/agent_tsa29.yaml

# 5. Run the 100-year rolling-horizon coupled pipeline.
fresh-salvage rh-run examples/rh_tsa29.yaml --json

# 6. Run a scenario ensemble in parallel.
fresh-salvage ensemble-run examples/ensemble_tsa29.yaml --json
```

A 100-year rolling-horizon scenario takes roughly 150 s on a 64-core host;
a 4-scenario smoke ensemble shows a 4.41x parallel speedup, and a
~1,000-scenario grid completes in about 40 minutes at `max_workers: 64` with
per-scenario WS3 `workers: 1`.

## Data Requirements

Model inputs are never vendored in this repository. The required external
inputs are:

- the WL_VFSL polygon layer (referenced by `wl_vfsl_path` in the ingestion
  scenario config);
- the validated femic TSA29 WS3 bridge package (referenced by
  `bridge_path`), with the femic stage-1 Woodstock CSVs as siblings of the
  canonical bridge.

Tracked examples and tests use synthetic or public-safe fixtures only.

## Testing

```bash
python -m ruff check .
python -m pytest
```

The suite holds 198 tests; `ruff` is configured for `E`, `F`, `I`, `UP`, and
`W` at a 100-column line length on Python >= 3.11. WS3-dependent tests
expect the `ws3` checkout on `PYTHONPATH`.

## License And Citation

`fresh-salvage` is released under the MIT license (UBC FRESH Lab, 2026); see
`LICENSE`. If you use the software in academic work, see `CITATION.cff` for
the citation record, and `RELEASE_NOTES.md` / `CHANGE_LOG.md` for the
release history.

## Public-Repo Hygiene

Do not commit private project data, raw chat transcripts, unpublished source
documents, generated local outputs, or machine-specific paths. Keep scratch
material under ignored local paths such as `tmp/`, `local/`, `data/private/`,
or `outputs/`.

Use GitHub issues for public bug reports, documentation issues, and feature
requests. Do not attach private project material to public issues.
