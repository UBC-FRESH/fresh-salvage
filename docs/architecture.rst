Architecture
============

``fresh-salvage`` implements a linear principal-agent salvage-subsidy
pipeline for the Williams Lake Timber Supply Area (TSA29). The predecessor
Gurobi binary stand-level models are re-implemented as continuous linear
programs solved with HiGHS through ``highspy``.

Pipeline Layers
---------------

.. code-block:: text

   external VRI polygon layer (WL_VFSL)
               |
               v
   (1) data: ingestion, typed stand records (246,957 stands)
               |
               v
   (2) ws3: no-LU bridge rebuild (1,608 aggregated cohorts),
       full-TSA schedule solve under the AAC ceiling
               |
               v
   (3) principal: offer-fraction LP (1-year timesteps)
               |
               v
   (4) agent: harvest/salvage LP with annual MFRI fire dynamics
               |
               v
   (5) rh: rolling-horizon engine (10 decadal steps, fire replay,
       cohort transitions, inventory injection)
       ensemble: scenario-grid driver (spawn process pool)
               |
               v
   run manifests and tabular artifacts (parquet/csv/JSONL)

Module Layout
-------------

``models``
   Pydantic data models for inputs, configs, decisions, results, and
   manifests.

``data``
   Ingestion and parsing of the external stand layer at the pipeline
   boundary; holds the calibrated economic constants.

``ws3``
   Full-TSA WS3 bridge rebuild (age smashing, LU-theme drop, femic writer
   aggregation, area-conservation gate) and schedule solve.

``principal``
   Principal-side linear HiGHS LP for salvage-subsidy offers.

``agent``
   Agent-side linear HiGHS LP for harvest/salvage decisions.

``fire``
   Annual fire dynamics with development-type burn rates (``1/MFRI``),
   shared by the agent LP and the rolling-horizon replay.

``rh``
   Rolling-horizon coordination loop between WS3, the principal LP, and
   the agent LP.

``ensemble``
   Scenario-grid driver: cartesian expansion over named config axes and
   parallel execution in a spawn-based process pool.

``io``
   Tabular and JSON artifact input/output helpers.

The detailed refactor contract for the predecessor-to-linear migration is
documented in ``planning/phase0-refactor-contract.md``; the as-built model
equations are documented in :doc:`model_semantics`.
