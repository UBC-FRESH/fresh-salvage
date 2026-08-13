fresh-salvage
=============

``fresh-salvage`` is an open-source Python package that implements a
principal-agent salvage-subsidy model for the Williams Lake Timber
Supply Area (TSA29), British Columbia. A public principal (the forest
regulator) chooses per-cohort salvage-subsidy offers; a private agent
(the licensee) chooses green harvest and burned-wood salvage in
response. The model exists to answer one policy question: at what
per-m3 subsidy does post-fire salvage become privately economic, and
what does that support cost the principal against the alternative of
letting fire-killed volume decay on the stump?

Every optimization layer is a continuous linear program (LP) solved by
HiGHS, an open-source solver, through its ``highspy`` Python bindings.
There are no binary variables and no commercial solver dependency. An
earlier generation of the model was a set of Gurobi binary stand-level
programs over a subset of the TSA; we rebuilt it as continuous LPs over
aggregated WS3 cohorts covering the full TSA. WS3
is the open-source wood-supply model of the UBC FRESH lab; the cohorts
come from the femic TSA29 wood-supply package, where 246,957 ingested
inventory stands aggregate to 1,608 decision cohorts. An annual fire
simulation driven by mean fire return intervals (MFRI) runs in a
rolling-horizon loop around the LPs: each decade is planned from the
inventory the previous decade left behind. A process-pool driver runs
whole scenario grids (ensembles) in parallel.

The TSA29 case study is calibrated against the 2025 fire-season
burn-severity layer and Q4-2023 BC Interior log market anchors. Under
the calibrated economics, unsubsidized salvage loses roughly 15 $/m3 on
the SPF transition-mix basis, and the coupled system flips across a
subsidy of approximately 19.1-19.4 $/m3: salvage turns on at 19.1 and
saturates by 19.4. That band sits slightly above the FESBC benchmark
support level of 14-15 $/m3. Every parameter is visible in the config
files, and every run writes a provenance manifest (inputs, checksums,
config snapshot), so results stay auditable. The current release is
``0.1.0a1`` (alpha; public APIs may change).

The Five-Layer Pipeline
-----------------------

1. **Ingestion** (``fresh_salvage.data``) — parses the external VRI
   polygon layer (``WL_VFSL.csv``; VRI is the BC Vegetation Resources
   Inventory, the provincial stand-polygon map) into typed stand
   records for the full TSA (246,957 stands). Burn-severity ratings map
   through a scenario-visible ladder (Unburned 0.0 / Low 0.30 /
   Moderate 0.60 / High 0.85; an unmatched label stops the run), and
   rated stands are coverage-scaled by
   ``min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)``, an upper-bound proxy
   for the burned share of the polygon.
2. **WS3 wood supply** (``fresh_salvage.ws3``) — rebuilds the WS3 input
   files from the femic stage-1 Woodstock CSVs (raw ages snap to 10-year
   class midpoints; the 44,998 staged rows aggregate to 1,608 cohorts —
   see :doc:`model_semantics`), then solves the full-TSA wood-supply
   schedule under the 2,937,509 m3/yr AAC ceiling with even-flow
   constraints (HiGHS).
3. **Principal LP** (``fresh_salvage.principal``) — continuous offer
   fractions per cohort-year, maximizing stumpage cashflow (stumpage is
   the per-m3 fee the licensee pays the Crown for standing timber) net
   of subsidy minus the MFRI-weighted expected burned-wood loss, under
   the green-volume AAC ceiling.
4. **Agent LP** (``fresh_salvage.agent``) — continuous harvest and
   salvage fractions bounded by the principal's offers, maximizing
   discounted net present value (3%/yr) under annual fire dynamics:
   harvest -> fire -> salvage -> decay, with salvage feasible only
   against the burned inventory on hand.
5. **Rolling horizon and ensembles** (``fresh_salvage.rh``,
   ``fresh_salvage.ensemble``) — 10 decadal steps (100 implemented
   years) of 15-period WS3 re-solves with in-memory inventory
   injection, principal and agent coupling, fire replay, and
   area-conserving cohort transitions; the ensemble driver maps a
   scenario grid (the cross-product of named config fields) onto a
   spawn-based process pool with per-scenario failure isolation.

Documentation Map
-----------------

.. list-table::
   :header-rows: 1

   * - Page
     - What it answers
   * - :doc:`installation`
     - How do I get a working environment (including the external ws3 and
       femic source dependencies) and verify it?
   * - :doc:`quickstart`
     - How do I go from a clean checkout to a first 100-year
       rolling-horizon result, and where do the outputs land?
   * - :doc:`model_semantics`
     - What exactly does the model compute — stratification, how the WS3
       input files are built, fire dynamics, both LP formulations, the
       rolling-horizon loop, economics, and known limitations?
   * - :doc:`cli`
     - What does each command read, write, and exit with? Full config field
       reference.
   * - :doc:`ensembles`
     - How do I run scenario grids in parallel, what do failures mean, and
       how do I budget a large sweep?
   * - :doc:`architecture`
     - How are the modules organized, what are the design invariants, and
       what do the forestry acronyms mean?
   * - :doc:`validation`
     - What evidence backs the release — validation findings, sanity
       numbers, runtimes, and the subsidy flip curve?
   * - :doc:`development`
     - How do I test, lint, extend the model (e.g. add a scenario knob),
       and get a PR accepted?

Installation instructions also live in the project ``README.md``; the
authoritative calibration and validation records are
``planning/economics-calibration.md`` and
``planning/phase6-validation-report.md`` in the repository.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   quickstart
   model_semantics
   cli
   ensembles
   architecture
   validation
   development
