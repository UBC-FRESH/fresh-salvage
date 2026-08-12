fresh-salvage
=============

``fresh-salvage`` is an open-source Python package implementing a linear
principal-agent salvage-subsidy model for the Williams Lake Timber Supply
Area (TSA29), British Columbia. A public principal (the forest regulator)
chooses per-cohort salvage-subsidy offers; a private agent (the licensee)
chooses green harvest and burned-wood salvage in response. The policy
question the model exists to answer: at what per-m3 subsidy does post-fire
salvage become privately economic, and what does that support cost the
principal against the alternative of letting fire-killed volume decay on the
stump?

The pipeline is pure HiGHS: every optimization layer is a continuous linear
program solved through ``highspy``, with no commercial solver dependency and
no binary variables. The predecessor Gurobi binary stand-level models (built
on an 11-landscape-unit subset of the TSA) are re-implemented as continuous
LPs over aggregate WS3 bridge cohorts covering the full TSA — 246,957
ingested stands aggregated to 1,608 decision cohorts — coordinated with an
annual MFRI-driven fire simulation in a rolling-horizon loop, and scaled to
scenario ensembles through a spawn-based process pool.

The TSA29 case study is calibrated against the 2025 fire-season
burn-severity layer and Q4-2023 BC Interior log market anchors. Under the
calibrated economics, unsubsidized salvage runs at roughly -15 $/m3 (SPF
transition-mix basis) and the coupled system flips across a subsidy of
approximately 19.1-19.4 $/m3 (turn-on 19.1, saturated by 19.4) — just above
the FESBC benchmark support level of 14-15 $/m3. Every parameter is
config-visible and every run emits a provenance manifest, so results stay
auditable. The
current release is ``0.1.0a1`` (alpha; public APIs may change).

The Five-Layer Pipeline
-----------------------

1. **Ingestion** (``fresh_salvage.data``) — the external VRI polygon layer
   (``WL_VFSL.csv``) is parsed into typed stand records for the full TSA
   (246,957 stands). Burn-severity ratings map through a scenario-visible
   ladder (Unburned 0.0 / Low 0.30 / Moderate 0.60 / High 0.85; unmatched
   labels are fatal), and rated stands are coverage-scaled by
   ``min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)`` — an upper-bound proxy.
2. **WS3 wood supply** (``fresh_salvage.ws3``) — a Landscape-Unit-free WS3
   bridge is rebuilt from the femic stage-1 Woodstock CSVs (ages smashed to
   10-year class midpoints; 44,998 raw ARE rows aggregate to 1,608
   area-conserving cohorts) and solved over the full TSA under the
   2,937,509 m3/yr AAC ceiling with even-flow constraints (HiGHS).
3. **Principal LP** (``fresh_salvage.principal``) — continuous offer
   fractions per cohort-year, maximizing stumpage cashflow net of subsidy
   minus the MFRI-weighted expected burned-wood loss, under the green-volume
   AAC ceiling.
4. **Agent LP** (``fresh_salvage.agent``) — continuous harvest and salvage
   fractions bounded by the principal's offers, maximizing discounted NPV
   (3%/yr) under annual fire dynamics: harvest -> fire -> salvage -> decay,
   with salvage feasible only against the on-hand burned inventory.
5. **Rolling horizon and ensembles** (``fresh_salvage.rh``,
   ``fresh_salvage.ensemble``) — 10 decadal steps (100 implemented years) of
   15-period WS3 re-solves with in-memory inventory injection, principal and
   agent coupling, fire replay, and area-conserving cohort transitions; the
   ensemble driver maps a cartesian scenario grid over named config axes
   onto a spawn-based process pool with per-scenario failure isolation.

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
     - What exactly does the model compute — stratification, fire dynamics,
       both LP formulations, the rolling-horizon loop, economics, and known
       limitations?
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
