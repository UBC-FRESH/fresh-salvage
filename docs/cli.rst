Command Line
============

All commands are subcommands of the ``fresh-salvage`` entry point, take a
single YAML or JSON config path, and share one flag convention:

- ``--json`` — emit a deterministic, machine-readable JSON summary (sorted
  keys) instead of the Rich report. Failures also emit a structured JSON
  diagnostic and exit with code 1.

Run artifacts follow a stable layout under each config's ``output_root``:
``data/`` for tabular outputs (parquet plus csv twins, or JSONL record
streams), ``manifests/`` for JSON run manifests (config snapshot, input
checksums, timing, diagnostics), and ``logs/`` for execution logs.

ingest
------

Ingest the external WL_VFSL polygon layer into typed stand records for the
full TSA.

.. code-block:: bash

   fresh-salvage ingest examples/scenario_tsa29.yaml [--json]

Config: ``run_id``, ``inputs.wl_vfsl_path``, ``inputs.output_root``,
optional ``fire``, optional ``severity`` (scenario-visible burn-severity
ladder and aliases), optional ``economics`` (partial overrides of the
calibrated surface), ``metadata``.

Artifacts: ``data/<run>-stands.parquet`` / ``.csv`` (246,957 stands on the
real layer), ``manifests/<run>-manifest.json`` (source SHA-256, effective
severity ladder and economic parameters).

ws3-run
-------

Rebuild the Landscape-Unit-free WS3 bridge, compile the full-TSA model, and
solve the wood-supply schedule.

.. code-block:: bash

   fresh-salvage ws3-run examples/ws3_tsa29.yaml [--json] [--smoke]

- ``--smoke`` — run the deterministic 3-period smoke profile (fast
  regression gate; output under ``outputs/ws3_smoke``).

Config: ``bridge_path``, ``base_year``, ``horizon`` (periods),
``period_length``, ``max_age``, ``workers``, ``age_smashing``,
``objective`` (action code, utilization, even-flow tolerance),
``aac_annual_m3``, ``output_root``.

Artifacts: ``data/<run>-schedule.parquet`` / ``.csv``,
``manifests/<run>-ws3-manifest.json`` (bridge checksums, LP size, solve
seconds). The derived no-LU bridge is materialized under
``<output_root>/derived/ws3_bridge_no_lu``.

principal-run
-------------

Build and solve the principal offer LP over the bridge cohorts.

.. code-block:: bash

   fresh-salvage principal-run examples/principal_tsa29.yaml [--json]

Config: ``stands_path``, ``are_path``, ``yields_path``, ``horizon``
(1-year timesteps), ``aac_annual_m3``, ``burned_limit_annual_m3``
(optional), ``decay_rate``, optional ``economics`` section, ``output_root``.

Artifacts: ``data/<run>-offers.parquet`` / ``.csv`` (per-cohort-year offer
fractions, zeros emitted explicitly), ``manifests/<run>-principal-manifest.json``.

agent-run
---------

Build and solve the agent harvest/salvage LP over offered cohorts.

.. code-block:: bash

   fresh-salvage agent-run examples/agent_tsa29.yaml [--json]

Config: same boundary inputs as the principal, plus ``discount_rate``,
``default_offer_fraction`` (uniform offer when no offer table is given),
``offers_path`` (optional principal offer table with
``cohort_id``/``year``/``offer_fraction`` columns), optional ``economics``
section.

Artifacts: ``data/<run>-decisions.parquet`` / ``.csv`` (per-cohort-year
harvest and salvage fractions), ``manifests/<run>-agent-manifest.json``.

rh-run
------

Run the rolling-horizon principal-agent coordination loop.

.. code-block:: bash

   fresh-salvage rh-run examples/rh_tsa29.yaml [--json]

Config: ``stands_path``, ``yields_path``, ``bridge_path``, ``base_year``,
``horizon`` (WS3 periods per step; 15 is the dev profile, 20 the production
profile), ``period_length`` (implemented years per step), ``steps``,
``workers``, ``age_smashing``, ``objective``, ``aac_annual_m3``,
``decay_rate``, ``discount_rate``, ``burned_limit_annual_m3``, and the flat
economic fields — ``subsidy_rate_per_m3``, ``green_prices``,
``burned_price_discount``, ``green_harvest_cost``, ``burned_harvest_cost``,
``green_transport_cost_per_m3``, ``burned_transport_cost_per_m3``,
``green_stumpage_rate``, ``burned_stumpage_rate`` — plus
``burn_rate_multiplier`` (fire-pattern axis).

Artifacts: ``data/<run>-steps.jsonl`` (one record per implemented step),
``data/<run>-final-state.csv`` (terminal cohort table), per-step WS3
schedule / principal offer / agent decision tables,
``manifests/<run>-rh-manifest.json``.

ensemble-run
------------

Run a scenario ensemble of rolling-horizon runs in parallel.

.. code-block:: bash

   fresh-salvage ensemble-run examples/ensemble_tsa29.yaml [--json]

Config: ``base`` (shared rolling-horizon field values), ``axes`` (named
``RHRunConfig`` fields mapped to value lists; the scenario grid is the
cartesian product), ``max_workers`` (concurrent scenario processes),
``output_root``. ``run_id``/``output_root`` are driver-owned and reserved;
``bridge_path`` is required in ``base`` and reserved as an axis (the no-LU
bridge is built once and shared read-only).

A failed scenario is recorded with ``status: failed`` and the structured
error code; it never aborts the ensemble. The command exits non-zero only
on fatal grid, input, or bridge failures.

Artifacts: ``data/<ensemble>-scenarios.jsonl`` (one record per scenario in
grid order), ``manifests/<ensemble>-ensemble-manifest.json``, and the full
per-scenario rolling-horizon artifact set under
``<output_root>/<scenario>/``.

export
------

Reserved for the tabular export phase. The command currently fails fast
with a not-implemented diagnostic (exit code 1), including under ``--json``.
