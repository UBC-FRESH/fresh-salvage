Architecture
============

``fresh-salvage`` implements a linear principal-agent salvage-subsidy
pipeline for the Williams Lake Timber Supply Area (TSA29). The predecessor
Gurobi binary models (``P_RH_Version.py``, ``A_RH_Version.py``, and the binary
``Version 2.py`` / ``Version3.3.py`` scripts) are re-implemented as continuous
linear programs solved with HiGHS.

Intended Pipeline
-----------------

.. code-block:: text

   predecessor data sources
              |
              v
        ingest / models
              |
              v
        ws3 schedule (full TSA)
              |
              v
        annual fire simulation
              |
              v
   principal LP --> offer fractions
              |
              v
   agent LP --> purchase fractions
              |
              v
   rolling-horizon coordination
              |
              v
        export artifacts

Module Layout
-------------

``models``
   Pydantic data models for inputs, decisions, and results.

``data``
   Ingestion and parsing of predecessor data sources at the pipeline boundary.

``ws3``
   Full-TSA WS3 bridge integration and schedule compilation.

``principal``
   Principal-side linear HiGHS LP for salvage-subsidy offers.

``agent``
   Agent-side linear HiGHS LP for harvest purchase decisions.

``fire``
   Annual fire simulation with development-type burn rates (``1/MFRI``).

``rolling_horizon``
   Rolling-horizon coordination loop between principal and agent models.

``io``
   Tabular and JSON artifact input/output helpers.

The detailed refactor contract for the predecessor-to-linear migration is
documented in ``planning/phase0-refactor-contract.md``.
