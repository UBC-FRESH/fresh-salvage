Command Line
============

All commands are subcommands of the ``fresh-salvage`` entry point
(equivalently ``python -m fresh_salvage``), take a single YAML or JSON
config path as a positional argument, and share one flag convention:

- ``--json`` — emit a deterministic, machine-readable JSON summary (sorted
  keys) instead of the Rich report. Failures also emit a structured JSON
  diagnostic and exit with code 1.

Conventions
-----------

**Artifact layout.** Run artifacts follow a stable layout under each
config's ``output_root``: ``data/`` for tabular outputs (parquet plus csv
twins, or JSONL record streams), ``manifests/`` for JSON run manifests
(config snapshot, input checksums, timing, diagnostics), and ``logs/`` for
execution logs. File names are derived from the ``run_id`` (or
``ensemble_id``), so re-running a config overwrites its own artifacts.

**Exit codes.** Success exits 0. Any failure — config validation, boundary
parse, solver non-optimality, or a structured pipeline error — exits 1 with
a diagnostic. ``ensemble-run`` is the exception in detail: per-scenario
failures are recorded, not raised, so a partially failed ensemble still
exits 0 unless ``--strict`` is passed (see `ensemble-run`_).

**``--json`` shapes.** On success the payload is
``{"ok": true, "command": <name>, ...summary fields...}`` where the summary
fields are per-command (run ids, status, objectives, per-period/per-year
volumes, artifact paths, diagnostics). On failure the payload carries
``"ok": false`` and ``"command"`` plus the flattened diagnostic fields
(``"diagnostic"``/``"message"`` text, ``"severity"``, the structured
``"code"``, and a ``"context"`` map).

**Structured error codes.** Every pipeline layer raises one error class
carrying a machine-readable code (``IngestError``, ``WS3Error``,
``PrincipalError``, ``AgentError``, ``RHError``, ``EnsembleError``); codes
are snake_case and layer-prefixed (for example ``data_severity_unmatched``,
``ws3_solve_not_optimal``, ``rh_state_duplicate_cohort``). The CLI surfaces
the code in the failure diagnostic; :doc:`development` covers the
convention for contributors.

ingest
------

Ingest the external WL_VFSL polygon layer into typed stand records for the
full TSA.

.. code-block:: bash

   fresh-salvage ingest examples/scenario_tsa29.yaml [--json]

Config (``ScenarioRunConfig``):

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``run_id``
     - ``tsa29-full``
     - Run identifier; used to name the artifacts.
   * - ``inputs.wl_vfsl_path``
     - (required)
     - Path to the external WL_VFSL polygon layer CSV. Never vendored.
   * - ``inputs.output_root``
     - (required)
     - Output root for the artifact layout.
   * - ``fire``
     - ``{}``
     - Reserved fire-simulation defaults surface
       (``mfri_by_development_type``, ``metadata``); Phase-4 fire rates
       come from the MFRI table in ``fire.py``.
   * - ``severity.severity_to_burned_frac``
     - Unburned 0.0, Low 0.30, Moderate 0.60, High 0.85
     - Scenario-visible burn-severity ladder (FS-VAL-01). Fractions must
       lie in [0, 1].
   * - ``severity.severity_aliases``
     - ``Medium -> Moderate``
     - Label normalization map; targets must be ladder labels and sources
       must not collide with them.
   * - ``economics``
     - calibrated defaults
     - Optional partial override of the economic surface (prices, costs,
       stumpage, subsidy); see :doc:`model_semantics`.
   * - ``metadata``
     - ``{}``
     - Free-form run metadata.

Artifacts: ``data/<run>-stands.parquet`` / ``.csv`` (246,957 stands on the
real layer), ``manifests/<run>-manifest.json`` (source SHA-256, row
drop/retention counts, effective severity ladder and economic parameters,
per-BEC-zone and per-development-type stand counts).

Notable failure codes: ``ingest_source_missing``,
``ingest_missing_columns``, ``data_severity_unmatched``,
``data_coverage_denominator_invalid``, ``data_coverage_numerator_invalid``.

ws3-run
-------

Rebuild the WS3 input files, compile the full-TSA model, and solve the
wood-supply schedule.

.. code-block:: bash

   fresh-salvage ws3-run examples/ws3_tsa29.yaml [--json] [--smoke]

- ``--smoke`` — run the deterministic 3-period smoke profile (workers 2,
  base year 2025; fast regression gate; output under
  ``outputs/ws3_smoke`` regardless of the configured ``output_root``).

Config (``WS3RunConfig``):

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``run_id``
     - ``tsa29-ws3``
     - Run identifier.
   * - ``bridge_path``
     - (required)
     - Path to the WS3 bridge. Point it at the canonical femic bridge and
       the command first rebuilds the input files from the sibling femic
       stage-1 Woodstock CSVs (see :doc:`model_semantics` for the
       adjustment); an already-rebuilt bridge is used as-is.
   * - ``base_year``
     - (required)
     - WS3 base year (2025 for TSA29).
   * - ``horizon``
     - (required)
     - Solve horizon in periods (3 smoke / 15 dev / 20 production / 30
       validated).
   * - ``period_length``
     - 10
     - Years per WS3 period.
   * - ``max_age``
     - 999
     - WS3 model age ceiling; bridge ages outside the domain stop the run.
   * - ``workers``
     - 64
     - WS3 tree-generation/build worker threads. Hold fixed for cross-run
       comparisons (±0.014% objective shift observed).
   * - ``age_smashing.enabled``
     - true
     - Compress initial-inventory ages to class midpoints.
   * - ``age_smashing.width`` / ``age_smashing.midpoint``
     - 10 / 5
     - Smashing rule ``age // width * width + midpoint`` (midpoint inside
       the class).
   * - ``objective.action_code``
     - ``cc``
     - Harvest action code; constrained to operable ages [60, 300] and
       removed for never-merchantable curves.
   * - ``objective.utilization``
     - 0.85
     - Utilized-volume fraction in the objective
       (``totvol * utilization``).
   * - ``objective.even_flow_tolerance``
     - 0.1
     - Even-flow tolerance (±10%) across periods.
   * - ``aac_annual_m3``
     - 2,937,509
     - AAC ceiling; bounds each period at
       ``aac_annual_m3 * period_length``.
   * - ``output_root``
     - (required)
     - Output root.
   * - ``metadata``
     - ``{}``
     - Free-form run metadata.

Artifacts: ``data/<run>-schedule.parquet`` / ``.csv`` (columns ``period``,
``year``, ``dtype_key``, ``stratum``, ``age_class``, ``area_ha``,
``harvest_action``, ``volume_m3``, ``etype``),
``manifests/<run>-ws3-manifest.json`` (bridge checksums, LP row/column
counts, solve seconds, objective). The derived bridge is materialized
under ``<output_root>/derived/ws3_bridge_no_lu`` with the staged CSVs beside
it (``<output_root>/derived/woodstock_no_lu_smashed``).

Notable failure codes: ``ws3_bridge_missing``, ``ws3_bridge_incomplete``,
``ws3_import_failed``, ``femic_import_failed``, ``ws3_stage1_incomplete``,
``invalid_age_values``, ``invalid_area_values``, ``area_conservation_failed``,
``ws3_bridge_age_unsmashed``, ``ws3_action_absent``, ``ws3_solve_failed``,
``ws3_solve_not_optimal``.

principal-run
-------------

Build and solve the principal offer LP over the bridge cohorts (standalone;
the rolling-horizon engine embeds this layer).

.. code-block:: bash

   fresh-salvage principal-run examples/principal_tsa29.yaml [--json]

Config (``PrincipalRunConfig``):

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``run_id``
     - ``tsa29-principal``
     - Run identifier.
   * - ``stands_path``
     - (required)
     - Ingestion stands table (parquet or csv) — the DT burn-share and
       economics source.
   * - ``are_path``
     - (required)
     - Derived WS3 bridge ARE section (the cohort units).
   * - ``yields_path``
     - (required)
     - femic stage-1 yields table (m3/ha by curve and age).
   * - ``horizon``
     - 10
     - 1-year timesteps to solve (10 = one rolling-horizon step).
   * - ``aac_annual_m3``
     - 2,937,509
     - Green-volume AAC ceiling per year.
   * - ``burned_limit_annual_m3``
     - null (unbounded)
     - Optional annual burned-volume offer cap.
   * - ``decay_rate``
     - 0.85
     - Annual retention of unsalvaged burned volume in the loss term.
   * - ``economics``
     - calibrated defaults
     - Optional partial override of the economic surface.
   * - ``output_root``
     - (required)
     - Output root.
   * - ``metadata``
     - ``{}``
     - Free-form run metadata.

Artifacts: ``data/<run>-offers.parquet`` / ``.csv`` (per-cohort-year offer
fractions, zeros emitted explicitly; columns ``cohort_id``, ``year``,
``offer_fraction``), ``manifests/<run>-principal-manifest.json`` (input
SHA-256 digests, LP size, solve seconds, objective, per-year offered
volumes).

agent-run
---------

Build and solve the agent harvest/salvage LP over offered cohorts
(standalone; the rolling-horizon engine embeds this layer).

.. code-block:: bash

   fresh-salvage agent-run examples/agent_tsa29.yaml [--json]

Config (``AgentRunConfig``): the same boundary inputs as the principal
(``stands_path``, ``are_path``, ``yields_path``, ``horizon``,
``decay_rate``, ``economics``, ``output_root``, ``metadata``), plus:

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``discount_rate``
     - 0.03
     - NPV discount rate; ``df_t = 1 / (1 + discount_rate)**t``.
   * - ``default_offer_fraction``
     - 1.0
     - Uniform per-cohort-year offer when no offer table is given (1.0 =
       every cohort fully offered every year).
   * - ``offers_path``
     - null
     - Optional principal offer table (parquet or csv with ``cohort_id``,
       ``year``, ``offer_fraction`` columns). Unknown cohorts, duplicate
       rows, and out-of-range fractions are rejected with an error.

Artifacts: ``data/<run>-decisions.parquet`` / ``.csv`` (per-cohort-year
``harvest_fraction``/``salvage_fraction`` plus m3 volumes, zeros emitted
explicitly), ``manifests/<run>-agent-manifest.json``.

rh-run
------

Run the rolling-horizon principal-agent coordination loop.

.. code-block:: bash

   fresh-salvage rh-run examples/rh_tsa29.yaml [--json]

Config (``RHRunConfig``):

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``run_id``
     - ``tsa29-rh``
     - Run identifier.
   * - ``stands_path`` / ``yields_path`` / ``bridge_path``
     - (required)
     - Stands table, femic stage-1 yields, and WS3 bridge (rebuilt from
       the staging export once per run when canonical).
   * - ``base_year``
     - 2025
     - Calendar year of step 1.
   * - ``horizon``
     - 15
     - WS3 periods solved per step (15 dev / 20 production).
   * - ``period_length``
     - 10
     - Implemented years per step (1 is the degenerate annual alternative).
   * - ``steps``
     - 10
     - Implemented rolling-horizon steps (10 x 10 years = 100 years).
   * - ``max_age``
     - 999
     - WS3 model age ceiling.
   * - ``workers``
     - 64
     - Per-step WS3 worker threads (see the ±0.014% numerics caveat).
   * - ``age_smashing``
     - enabled, width 10, midpoint 5
     - Same semantics as ``ws3-run``; the midpoint is also the
       regeneration age.
   * - ``objective``
     - ``cc`` / utilization 0.85 / even-flow 0.1
     - Same semantics as ``ws3-run``.
   * - ``aac_annual_m3``
     - 2,937,509
     - Green AAC ceiling passed to both WS3 and the principal LP.
   * - ``decay_rate``
     - 0.85
     - Burned-inventory annual retention.
   * - ``discount_rate``
     - 0.03
     - Agent NPV discount rate.
   * - ``burned_limit_annual_m3``
     - null (unbounded)
     - Optional principal burned-volume offer cap.
   * - ``subsidy_rate_per_m3``
     - 3.0
     - Salvage subsidy ($/m3 of burned volume salvaged): a cost in the
       principal cashflow, revenue in the agent margin.
   * - ``green_prices``
     - calibrated table
     - Flat grade-price map (13 canonical keys; must match exactly).
   * - ``burned_price_discount``
     - 0.65
     - Burned prices derive as green x discount.
   * - ``green_harvest_cost`` / ``burned_harvest_cost``
     - 45.0 / 56.0
     - Harvest costs ($/m3).
   * - ``green_transport_cost_per_m3`` / ``burned_transport_cost_per_m3``
     - 30.0 / 38.0
     - Haul costs ($/m3).
   * - ``green_stumpage_rate`` / ``burned_stumpage_rate``
     - 15.0 / 0.25
     - Stumpage rates ($/m3).
   * - ``burn_rate_multiplier``
     - 1.0
     - Scales every MFRI burn rate (the future-fire-pattern ensemble axis;
       0.0 = fire-free counterfactual).
   * - ``output_root``
     - (required)
     - Output root.
   * - ``metadata``
     - ``{}``
     - Free-form run metadata.

The flat economic fields exist (instead of a nested ``economics`` section)
so the ensemble driver can vary any of them as a named axis.

Artifacts: ``data/<run>-steps.jsonl`` (one record per implemented step:
objectives, solve seconds, annual green/salvage volumes, area burned, wall
time; flushed incrementally so a failed run leaves its partial trajectory),
``data/<run>-final-state.csv`` (terminal cohort table),
``data/<run>-step-NN-schedule.csv`` / ``-offers.parquet`` /
``-decisions.parquet`` per step, ``derived/rh_state/step_NN.are`` (canonical
injected cohort states), ``manifests/<run>-rh-manifest.json`` (run status
``optimal``/``degraded``, totals, ``source_sha256`` digests, config
snapshot, embedded step records).

ensemble-run
------------

Run a scenario ensemble of rolling-horizon runs in parallel.

.. code-block:: bash

   fresh-salvage ensemble-run examples/ensemble_tsa29.yaml [--json] [--strict]

Config (``EnsembleConfig``):

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Meaning
   * - ``ensemble_id``
     - ``tsa29-ensemble``
     - Ensemble identifier; used to name the artifacts.
   * - ``base``
     - ``{}``
     - Shared ``RHRunConfig`` field values (input paths, horizon, steps,
       per-scenario ``workers``...). Every key must be an ``RHRunConfig``
       field; ``run_id``/``output_root`` are driver-owned and rejected.
   * - ``axes``
     - ``{}``
     - Named ``RHRunConfig`` fields mapped to value lists; the scenario
       grid is the cartesian product (empty axes = one ``baseline``
       scenario). ``bridge_path`` is reserved as an axis.
   * - ``max_workers``
     - 4
     - Concurrent scenario processes (1 = sequential in-process debug
       profile).
   * - ``output_root``
     - (required)
     - Ensemble output root; each scenario writes under
       ``<output_root>/<scenario>/``.
   * - ``metadata``
     - ``{}``
     - Free-form metadata.

**Partial vs fatal semantics.** A failed scenario is recorded with
``status: failed`` and the structured error code (``RHError.code`` or the
exception type name); it never aborts the ensemble, and a worker that dies
without returning is captured as ``ensemble_worker_crashed``. The ensemble
status is ``ok`` (no failures), ``partial`` (some failed), or ``failed``
(all failed) — all three exit 0. The command exits non-zero **only** on
fatal grid, input, or bridge failures before or around the scenario runs
(``ensemble_axis_unknown``, ``ensemble_axis_empty``,
``ensemble_duplicate_scenario``, ``ensemble_field_reserved``,
``ensemble_scenario_invalid``, ``ensemble_input_missing``,
``ensemble_bridge_failed``).

**``--strict`` exit-code gate.** With ``--strict`` a completed ensemble
exits 0 only when the status is ``ok``; ``partial`` or ``failed`` exits 1.
Without the flag, every completed ensemble exits 0 regardless of scenario
failures. Fatal grid/input/bridge failures exit 1 in both modes, and the
``--json`` payload shape is unchanged — the flag gates the exit code only.

Artifacts: ``data/<ensemble>-scenarios.jsonl`` (one record per scenario in
deterministic grid order — name, overrides, status, error code, wall
seconds, per-scenario artifact paths), ``manifests/<ensemble>-ensemble-manifest.json``
(grid config digest, input and bridge checksums, outcomes), the once-built
shared bridge under ``<output_root>/derived/ws3_bridge_no_lu``, and
the full per-scenario rolling-horizon artifact set under
``<output_root>/<scenario>/``. See :doc:`ensembles` for the parallelism
model, performance budgets, and a worked sweep.

export
------

Reserved for the tabular export phase. The command currently exits 1 with
a not-implemented diagnostic, including under ``--json``; do not build
workflows on it until the roadmap records the export phase.
