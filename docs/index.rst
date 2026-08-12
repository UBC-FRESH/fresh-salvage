fresh-salvage
=============

``fresh-salvage`` is an open-source Python package implementing a linear
principal-agent salvage-subsidy model for the Williams Lake Timber Supply
Area (TSA29), British Columbia. The pipeline is pure HiGHS: the principal
and agent decision problems are continuous linear programs solved through
``highspy``, coordinated in a rolling-horizon loop with an annual
MFRI-driven fire simulation, and scaled to scenario ensembles through a
spawn-based process pool.

The pipeline runs in five layers:

1. **Ingestion** — the external VRI polygon layer is parsed into typed
   stand records for the full TSA (246,957 stands), with a scenario-visible
   burn-severity ladder and coverage scaling.
2. **WS3 wood supply** — a Landscape-Unit-free WS3 bridge (1,608 aggregated
   cohorts) is rebuilt and solved over the full TSA under the
   2,937,509 m3/yr AAC ceiling.
3. **Principal LP** — continuous offer fractions per cohort-year under the
   green-volume AAC ceiling.
4. **Agent LP** — continuous harvest/salvage fractions under annual fire
   dynamics, burned-inventory decay, and the per-m3 salvage subsidy.
5. **Rolling horizon and ensembles** — 10 decadal steps (100 years) of
   WS3 re-solves, principal/agent coupling, and fire replay, driven in
   parallel over scenario grids.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   installation
   model_semantics
   cli
   architecture
   development
