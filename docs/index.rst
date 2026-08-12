fresh-salvage
=============

``fresh-salvage`` is a clean-reboot linear implementation of the
principal-agent salvage-subsidy model for the Williams Lake Timber Supply Area
(TSA29), British Columbia. It replaces the predecessor Gurobi binary models with
pure-HiGHS linear programs and adds full-TSA WS3 schedule integration and an
annual fire simulation.

The package is currently a public-alpha scaffold. Phase 1 ships data
ingestion and typed input records, and Phase 2 ships full-TSA WS3 schedule
integration. The principal and agent linear programs, fire simulation,
rolling-horizon coordination, and export are implemented in later phases.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   architecture
