Scenario Ensembles
==================

Thesis-scale sensitivity analysis runs one full rolling-horizon coupled run
per scenario, where a scenario is one point of a cartesian grid over named
``RHRunConfig`` fields. This page covers why the driver exists, the grid
syntax, the parallelism and failure models, performance budgeting, and a
worked subsidy flip-point sweep.

Why Ensembles
-------------

The coupled model has three families of assumptions a policy analysis must
sweep rather than fix:

**Principal policy.** ``subsidy_rate_per_m3`` is the instrument itself —
the analysis question is the response surface of salvage volume and program
cost to the subsidy level.

**Agent profit function.** The flat economic fields (``green_prices``,
``burned_price_discount``, ``green_harvest_cost``, ``burned_harvest_cost``,
``green_transport_cost_per_m3``, ``burned_transport_cost_per_m3``,
``green_stumpage_rate``, ``burned_stumpage_rate``) plus ``discount_rate``
parameterize the agent's margins; sweeping them stress-tests the calibrated
semi-synthetic surface (see :doc:`model_semantics`).

**Future fire pattern.** ``burn_rate_multiplier`` scales every MFRI-derived
annual burn rate: 1.0 is the published MFRI table, 0.0 a fire-free
counterfactual control, 2.0 a doubled-fire future.

Every ``RHRunConfig`` field except the reserved ones is a valid axis, so the
same mechanism also covers structural sensitivity (``horizon``, ``steps``,
``decay_rate``, ...).

Grid Syntax
-----------

.. code-block:: yaml

   ensemble_id: tsa29-ensemble-smoke
   base:                       # shared RHRunConfig field values
     stands_path: outputs/full_tsa/data/tsa29-full-stands.parquet
     yields_path: /path/to/femic-tsa29/woodstock_yields.csv
     bridge_path: /path/to/femic-tsa29/ws3_bridge
     base_year: 2025
     horizon: 15
     period_length: 10
     steps: 10
     workers: 1                # per-scenario WS3 threads
   axes:                       # the cartesian grid
     subsidy_rate_per_m3: [0.0, 3.0]
     burn_rate_multiplier: [1.0, 2.0]
   max_workers: 4
   output_root: outputs/ensemble_tsa29_smoke

Rules enforced at expansion time (fail fast, before any work starts):

- Every ``base`` and ``axes`` key must be an ``RHRunConfig`` field name —
  there is no positional ambiguity and no silent ignore
  (``ensemble_axis_unknown``).
- ``run_id`` and ``output_root`` are **driver-owned**: the ensemble assigns
  them per scenario and rejects them in ``base``/``axes``
  (``ensemble_field_reserved``).
- ``bridge_path`` is **required in** ``base`` **and reserved as an axis**:
  every scenario is bound to the once-prebuilt shared bridge, so a
  ``bridge_path`` axis would be silently discarded.
- Every axis needs at least one value (``ensemble_axis_empty``); duplicate
  scenario names fail (``ensemble_duplicate_scenario``); each expanded
  scenario must form a valid ``RHRunConfig`` (``ensemble_scenario_invalid``).

Scenario names are deterministic: ``axis-value`` fragments joined by ``__``
in sorted axis order, slugged (for example
``burn_rate_multiplier-1.0__subsidy_rate_per_m3-0.0``). An empty ``axes``
mapping yields the single ``baseline`` scenario. Records and artifacts are
written in grid order regardless of completion order, so identical inputs
give byte-comparable scenario tables.

Parallelism Model
-----------------

- Each scenario runs ``rh.run_rh`` in its own **spawn-context** worker
  process (``ProcessPoolExecutor``) with its own output root
  ``<ensemble output_root>/<scenario name>/`` — no shared mutable state.
  Spawn workers start from a clean interpreter (no fork-with-threads
  hazards) and inherit ``PYTHONPATH``, which the ws3 dependency requires.
- The one shared input is the WS3 bridge: when the configured bridge is the
  canonical Landscape-Unit bridge, the parent process rebuilds the derived
  no-LU bridge **once** under the ensemble output root before the pool
  starts, and every scenario then reads it. The bridge is strictly read-only
  during the parallel phase; there is no write contention.
- ``max_workers: 1`` runs scenarios sequentially **in-process** — the debug
  and test profile, with identical failure-capture semantics.

Failure Isolation
-----------------

A scenario failure never kills the ensemble. The worker converts any
exception into a scenario record with ``status: "failed"`` and the
structured error code (the underlying ``RHError.code``, or the exception
type name); a worker that dies without returning (pickle failure, hard
crash) is recorded as ``ensemble_worker_crashed``. The ensemble completes
the remaining scenarios and reports:

- ``ok`` — every scenario succeeded;
- ``partial`` — at least one failed, at least one succeeded;
- ``failed`` — every scenario failed.

All three exit 0: per-scenario failure is data, not a crash. The command
exits non-zero only on fatal grid, input, or bridge failures (a missing
stands/yields input is detected during provenance checks **before** the
bridge prebuild, so it fails cheap). Each successful scenario flushes its
own RH step JSONL and manifest incrementally inside its own output root, so
partial evidence survives even a fatal late failure.

Performance Guidance
--------------------

The binding constraint is the per-scenario WS3 work: at the 15-period dev
horizon the per-step problem build plus solve costs about 11 s and is
serial-bound, so 10 steps lands at roughly **150 s per scenario** on the
reference 64-core host. Consequences:

- Prefer ``workers: 1`` per scenario on large grids and spend cores on
  ``max_workers`` instead — scenario parallelism scales; intra-scenario WS3
  threads do not (workers 8 vs 64 made no measurable difference at the
  15-period horizon; the smoke example's ``workers: 8`` is the tight
  default for small grids). Total threads = ``max_workers`` x ``workers``.
- Hold ``workers`` fixed across any comparison set: WS3 step objectives
  shift slightly with worker count (±0.014% observed).

Recorded budgets (all on the 64-core host, 100 implemented years per
scenario):

.. list-table::
   :header-rows: 1

   * - Ensemble
     - Scenarios
     - Settings
     - Wall
   * - Smoke grid (``examples/ensemble_tsa29.yaml``)
     - 4 (2 subsidy x 2 fire)
     - ``max_workers: 4``, ``workers: 8``
     - 4.41x parallel speedup vs sequential
   * - Flip sweep, prescribed grid
     - 26 (13 subsidy x 2 fire)
     - ``max_workers: 64``, ``workers: 1``
     - 151.8 s
   * - Flat-sweep regression (pre-calibration)
     - 20
     - ``max_workers: 64``, ``workers: 1``
     - 153.6 s
   * - Thesis-scale projection
     - ~1,000
     - ``max_workers: 64``, ``workers: 1``
     - ~40 minutes

Worked Example: The Subsidy Flip-Point Sweep
--------------------------------------------

``examples/ensemble_flip_sweep.yaml`` reproduces the prescribed post-
calibration sweep: ``subsidy_rate_per_m3`` in {0, 5, 8, 10, 12, 14, 15, 16,
18, 20, 22, 25, 30} x ``burn_rate_multiplier`` in {0.0, 1.0} (26 scenarios,
~2.5 minutes at ``max_workers: 64`` / ``workers: 1``):

.. code-block:: bash

   fresh-salvage ensemble-run examples/ensemble_flip_sweep.yaml --json

Reading the response curve. Join
``data/<ensemble>-scenarios.jsonl`` to each scenario's RH manifest (the
record carries ``manifest_path``) and plot ``total_burned_harvest_m3``
against the subsidy axis. The recorded curve (burn x1.0; from
``planning/phase6-validation-report.md``):

.. list-table::
   :header-rows: 1

   * - Subsidy ($/m3)
     - Total salvage, 100 yr (m3)
     - Reading
   * - 0 - 19.0
     - 0.00
     - Below every development type's breakeven: salvage is a net cost, the
       agent declines it, and the subsidy is a pure transfer on the offered
       burned stock (principal objective falls ~33.4k $ per $/m3; agent
       objective flat at 130,041,680.42).
   * - 19.1
     - 61,549.50
     - Turn-on: the lowest-breakeven SPF DT (SPF_ESSF, 19.09) crosses zero
       margin.
   * - 19.2 - 19.3
     - 288,479.53
     - Ramp: SPF_MS joins; the principal's offer surface re-prices.
   * - >= 19.4
     - 1,338,477.16
     - Saturation: all four SPF DTs are over breakeven and the program
       reaches the physical maximum — the full fire influx on the
       offered-but-unharvested slack.

Three structural facts to check in any sweep you run:

- **The burn x0.0 control is exactly zero** at every subsidy — no fire, no
  salvage; a nonzero value there indicates a wiring defect, not a result.
- **Green harvest is subsidy-invariant** (36,279,196.26 m3 at burn x1.0,
  max pairwise spread at solver precision): salvage never displaces green
  harvest, it monetizes volume that would otherwise decay.
- The flip location is the volume-weighted SPF breakeven predicted by the
  calibration (~19.1-19.4 $/m3); the FESBC 14-15 $/m3 benchmark sits just
  below the turn-on, which is exactly the gap a minimum-subsidy analysis
  targets. If a future recalibration moves the DT margins, expect the flip
  to move with them.
