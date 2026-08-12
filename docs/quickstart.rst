Quickstart
==========

This guide goes from a clean checkout to a first 100-year rolling-horizon
run in four commands. Every command reads a YAML or JSON config; example
configs live in ``examples/`` and point at machine-specific input paths you
must replace with your own (see :doc:`installation` for the external data
requirements).

Prerequisites
-------------

Install the package and resolve the external dependencies as in
:doc:`installation`:

.. code-block:: bash

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]
   export PYTHONPATH=/path/to/ws3
   export FEMIC_SRC=/path/to/femic/src

Smoke check:

.. code-block:: bash

   fresh-salvage --help
   fresh-salvage --version

Step 1: Ingest The Stand Layer
------------------------------

Edit ``examples/scenario_tsa29.yaml`` so ``inputs.wl_vfsl_path`` points at
your copy of the WL_VFSL polygon layer, then:

.. code-block:: bash

   fresh-salvage ingest examples/scenario_tsa29.yaml

This writes the typed stands table plus a provenance manifest under the
configured ``inputs.output_root``:

- ``data/tsa29-full-stands.parquet`` / ``.csv`` — 246,957 retained stands on
  the real layer (77-column schema; roughly 34 s wall time), carrying
  19,773,448.62 m3 of green volume and 79,087.38 m3 of coverage-scaled
  salvageable volume (2025 fire season only).
- ``manifests/tsa29-full-manifest.json`` — source SHA-256, effective
  severity ladder and aliases, effective economic parameters, drop counts.

Step 2: Solve The WS3 Schedule (Smoke Profile)
----------------------------------------------

Edit ``examples/ws3_tsa29.yaml`` so ``bridge_path`` points at your validated
femic TSA29 WS3 bridge, then run the deterministic smoke profile first:

.. code-block:: bash

   fresh-salvage ws3-run examples/ws3_tsa29.yaml --smoke

The smoke profile solves a 3-period horizon in seconds (output under
``outputs/ws3_smoke``); its objective, 24,328,759.75 m3, is the recorded
regression anchor. The production profile (drop ``--smoke``) rebuilds the
no-LU bridge into ``<output_root>/derived/ws3_bridge_no_lu``, compiles the
full-TSA model, and solves the configured 30-period horizon — budget about
24 minutes end-to-end. Artifacts: ``data/<run>-schedule.parquet`` / ``.csv``
and ``manifests/<run>-ws3-manifest.json``.

Step 3: First Rolling-Horizon Run
---------------------------------

Edit ``examples/rh_tsa29.yaml`` so ``stands_path`` matches the Step 1
output and ``yields_path`` / ``bridge_path`` point at the femic TSA29
package, then:

.. code-block:: bash

   fresh-salvage rh-run examples/rh_tsa29.yaml --json

The dev profile runs 10 steps of 10 implemented years (100 years total) with
a 15-period WS3 horizon per step, in roughly 150 s on a 64-core host. Each
step re-solves WS3 from the current cohort state, splits the period-1
decadal harvest into 10 annual per-cohort ceilings, solves the principal and
agent LPs, and replays the implemented years with fire dynamics.

Step 4: Read The Outputs
------------------------

Artifacts land under the config's ``output_root`` in a stable layout
(``data/``, ``manifests/``, ``logs/``; see :doc:`cli`):

- ``data/<run>-steps.jsonl`` — one JSON record per implemented step: WS3 /
  principal / agent objectives and solve seconds, the 10 annual green and
  salvage volumes, area burned, and step wall time.
- ``data/<run>-final-state.csv`` — the terminal cohort table
  (``tsa, ifm, au_id, stratum_code, curve_id, age, area_ha``).
- ``data/<run>-step-NN-schedule.csv``, ``-offers.parquet``,
  ``-decisions.parquet`` — the per-step WS3 schedule, principal offers, and
  agent decisions (zeros emitted explicitly).
- ``derived/rh_state/step_NN.are`` — the canonical cohort state injected
  into WS3 at each step.
- ``manifests/<run>-rh-manifest.json`` — the run manifest.

Reading The Manifest Provenance
-------------------------------

The manifest is the audit record of the run. Fields to check first:

``status``
   ``optimal`` when every step's WS3 solve reached optimal; ``degraded``
   otherwise (treat a degraded run as evidence, not a result).

``total_green_harvest_m3`` / ``total_burned_harvest_m3`` / ``total_area_burned_ha``
   The 100-year headline totals. At the calibrated default subsidy
   (3.0 $/m3) expect zero burned salvage — the smallest development-type
   margin stays negative at that support level (see :doc:`validation`).

``source_sha256``
   SHA-256 digests of the stands table, yields table, and bridge files the
   run consumed — the exact-input fingerprint.

``config``
   The full effective config snapshot (all defaults resolved), so the run
   is reconstructible without consulting the original YAML.

``step_records``
   The same per-step records as the JSONL stream, embedded for one-file
   provenance.

Next Steps
----------

- :doc:`model_semantics` for the fire and inventory equations the LPs
  implement, and for the known limitations you should read before trusting
  any number.
- :doc:`cli` for every command, flag, config field, and artifact.
- ``examples/ensemble_tsa29.yaml`` for a 4-scenario parallel smoke grid, and
  :doc:`ensembles` for scenario sweeps such as the subsidy flip-point curve
  (``examples/ensemble_flip_sweep.yaml``).
- ``principal-run`` and ``agent-run`` solve the two LPs standalone (one
  rolling-horizon step's worth of horizon) when you need to inspect a single
  layer.
