Quickstart
==========

This guide goes from a clean checkout to a first 100-year rolling-horizon
run. Every command reads a YAML or JSON config; example configs live in
``examples/`` and point at machine-specific input paths you must replace
with your own.

Install
-------

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]

Resolve the two external FRESH-ecosystem dependencies (source checkouts,
not PyPI):

- make ``ws3`` importable, typically via a sibling checkout on
  ``PYTHONPATH`` (for example ``export PYTHONPATH=/path/to/ws3``);
- point ``FEMIC_SRC`` at the femic source tree so the bridge writer is
  importable (a configured default path is used as fallback).

Smoke check:

.. code-block:: bash

   fresh-salvage --help
   fresh-salvage --version

Ingest The Stand Layer
----------------------

Edit ``examples/scenario_tsa29.yaml`` so ``wl_vfsl_path`` points at your
copy of the WL_VFSL polygon layer, then:

.. code-block:: bash

   fresh-salvage ingest examples/scenario_tsa29.yaml

This writes the typed stands table (parquet and csv) plus a provenance
manifest under the configured ``output_root``.

Solve The WS3 Schedule
----------------------

Edit ``examples/ws3_tsa29.yaml`` so ``bridge_path`` points at your
validated femic TSA29 WS3 bridge, then run the deterministic smoke profile
first:

.. code-block:: bash

   fresh-salvage ws3-run examples/ws3_tsa29.yaml --smoke

The smoke profile solves a 3-period horizon in seconds. The production
profile (drop ``--smoke``) rebuilds the no-LU bridge, compiles the full-TSA
model, and solves the configured horizon.

First Rolling-Horizon Run
-------------------------

Edit ``examples/rh_tsa29.yaml`` so ``stands_path`` matches the ingestion
output above and ``yields_path`` / ``bridge_path`` point at the femic TSA29
package, then:

.. code-block:: bash

   fresh-salvage rh-run examples/rh_tsa29.yaml --json

The default dev profile runs 10 steps of 10 implemented years (100 years)
with a 15-period WS3 horizon per step, in roughly 150 s on a 64-core host.
Artifacts land under the configured ``output_root``: a per-step JSONL
record table, the final cohort state, per-step WS3/principal/agent tables,
and the run manifest.

Next Steps
----------

- :doc:`model_semantics` for the fire and inventory equations the LPs
  implement.
- :doc:`cli` for every command, flag, and artifact.
- ``examples/ensemble_tsa29.yaml`` for a 4-scenario parallel smoke grid.
