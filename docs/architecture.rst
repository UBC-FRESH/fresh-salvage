Architecture
============

``fresh-salvage`` implements a linear principal-agent salvage-subsidy
pipeline for the Williams Lake Timber Supply Area (TSA29). The predecessor
Gurobi binary stand-level models are re-implemented as continuous linear
programs solved with HiGHS through ``highspy``; the durable source of truth
is typed data records, compiled WS3 schedules, explicit LP formulations, and
verification evidence — not one-off script chains.

Data Flow
---------

.. code-block:: text

   external VRI polygon layer (WL_VFSL)          femic TSA29 package
   (246,957 stands; 2025 severity layer)         (stage-1 Woodstock CSVs +
                |                                 validated WS3 bridge)
                v                                          |
   (1) data: ingestion, typed stand records                v
        severity ladder, coverage scaling,        (2) ws3: no-LU bridge rebuild
        economic surface at the boundary                (1,608 aggregated cohorts),
                |                                   full-TSA schedule solve under
                |                                   the 2,937,509 m3/yr AAC ceiling
                +----------------+-------------------------+
                                 v
                    (3) principal: offer-fraction LP
                        (1-year timesteps, green AAC ceiling)
                                 v
                    (4) agent: harvest/salvage LP
                        (annual MFRI fire dynamics, NPV)
                                 v
                    (5) rh: rolling-horizon engine
                        (10 decadal steps, in-memory inventory injection,
                         fire replay, area-conserving transitions)
                        ensemble: scenario-grid driver
                        (cartesian axes, spawn process pool)
                                 v
             run manifests and tabular artifacts (parquet/csv/JSONL)

The standalone commands (``ingest``, ``ws3-run``, ``principal-run``,
``agent-run``) exercise layers 1-4 individually against on-disk boundary
artifacts; ``rh-run`` couples all layers in memory with the canonical ARE
round-trip per step; ``ensemble-run`` replicates ``rh-run`` over a scenario
grid.

Module Map
----------

``models``
   Pydantic v2 data models for inputs, configs, decisions, results, and
   manifests (``ScenarioRunConfig``, ``WS3RunConfig``, ``PrincipalRunConfig``,
   ``AgentRunConfig``, ``RHRunConfig``, ``EnsembleConfig``, the ``*Manifest``
   and ``*Result`` records, ``ArtifactLayout``, ``Diagnostic``). Config
   classes carry ``read()``/``write_json()`` helpers; validation fails at
   parse time.

``data``
   Ingestion of the external stand layer at the pipeline boundary: severity
   ladder and coverage scaling (FS-VAL-01/02), species/grade volume splits,
   development-type derivation, and the calibrated economic constants every
   other layer defaults to.

``ws3``
   Full-TSA WS3 bridge rebuild (LU-theme drop at the source, midpoint age
   smashing, femic writer aggregation, area-conservation gate) and schedule
   solve (``cc`` action at operable ages [60, 300], AAC ceiling via
   ``cgen_data``, even flow, HiGHS). ws3 and femic are imported lazily so
   the pure helpers stay unit-testable without them.

``principal``
   Principal-side continuous HiGHS LP for salvage-subsidy offers (offer
   fractions, expected burned-wood loss, green AAC ceiling, optional burned
   cap).

``agent``
   Agent-side continuous HiGHS LP for harvest/salvage decisions; its rows
   implement the ``fire.py`` dynamics directly.

``fire``
   The MFRI burn-rate table and the pure annual fire-dynamics primitives
   (``simulate_cohort_years`` drives full-horizon replays and fails fast on
   infeasible schedules). Single source of truth for the dynamics.

``rh``
   Rolling-horizon coordination loop: cohort state table (ARE round-trip),
   in-memory period-0 inventory injection, decadal-to-annual ceiling split,
   principal/agent coupling, fire replay, area-conserving transitions.

``ensemble``
   Scenario-grid driver: cartesian expansion over named ``RHRunConfig``
   axes, spawn-based process pool, shared read-only bridge, per-scenario
   failure isolation, deterministic grid-order output.

``io``
   Tabular and JSON artifact input/output helpers.

``cli`` / ``__main__``
   Thin Typer wrappers over the module APIs (``fresh-salvage`` entry point
   and ``python -m fresh_salvage``); they own only argument parsing, the
   ``--json``/Rich reporting, and the failure-diagnostic conversion.

``rolling_horizon``
   Reserved Phase-0 scaffold stub (docstring only); the implemented engine
   is ``rh``. Import nothing from it.

The detailed refactor contract for the predecessor-to-linear migration is
documented in ``planning/phase0-refactor-contract.md``; the as-built model
equations are documented in :doc:`model_semantics`.

Design Invariants
-----------------

Hold these when extending the package; each exists because its violation
already happened upstream once.

**femic owns the Woodstock text.** The no-LU bridge is written by femic's
own stage-2 writer (``femic.ws3_bridge.build_ws3_sections_from_femic_woodstock``);
fresh-salvage stages the smashed, LU-dropped CSVs and then *verifies* the
written bridge (LU theme absent, ages on the midpoint lattice, ARE area
conserved to 1e-6 against staging — the writer silently drops area rows
whose ``(tsa, ifm, au_id)`` key has no curve). Never hand-write bridge
sections here.

**fire.py is the single source of truth for dynamics.** The agent LP rows
are the same harvest -> fire -> salvage -> decay equations as
``simulate_cohort_years``; the rolling-horizon replay calls the simulation
directly. A semantics change lands in ``fire.py`` and propagates; it is
never patched into one consumer.

**Boundary parsing, then trust.** External inputs are parsed once into
typed Pydantic records at the module boundary with fail-fast structured
errors; core logic contains no defensive re-validation and no silent
defaults.

**Continuous LPs only.** No integer or binary variables, no thresholding
or rounding of decision outputs; zero decisions are emitted explicitly so
downstream panels are complete.

**Structured fail-fast codes.** Each layer raises one ``*Error(code,
message)`` class; the CLI surfaces the code in its diagnostic. See
:doc:`development` for the convention.

**Provenance on every run.** Every command writes a manifest with input
SHA-256 digests and the full effective config; ``outputs/`` and other local
working areas stay uncommitted (see ``AGENTS.md``).

Glossary
--------

AAC
   Annual Allowable Cut — the regulator's annual harvest ceiling, here
   2,937,509 m3/yr, applied to offered green volume.

AU (analysis unit)
   WS3/Woodstock stratification unit within a TSA; the validated femic
   TSA29 instance carries 54 AUs (18 strata x 3 site-index levels).

DT (development type)
   The stand-level stratum key ``{leading_species_group}_{BEC}`` (for
   example ``SPF_SBPS``) assigned at ingestion; also the WS3-side unit that
   carries the period-0 inventory age map.

IFM
   Initial Forest Management (managed/unmanaged) dimension of the WS3
   bridge key; the TSA29 bridge carries 2 IFMs.

MFRI
   Mean Fire Return Interval (years) of a BEC zone; the annual burn
   probability is ``1 / MFRI``.

Stratum / stratum code
   The WS3 bridge stratum ``{bec_zone}_{leading_species}`` (lowercase, for
   example ``sbps_pli``); the fire-rate lookup parses the BEC zone prefix.

THLB
   Timber Harvesting Land Base — the Crown forest land base available for
   harvest after netdowns; the AAC applies to it.

TSA
   Timber Supply Area — the BC timber-supply management unit; TSA29 is
   Williams Lake (~4.93 Mha).

VRI
   Vegetation Resources Inventory — the BC polygon forest inventory layer;
   ``WL_VFSL.csv`` is the TSA29 extract joined with burn-severity
   attributes.

VDYP / TIPSY
   The BC yield models behind the femic curve tables: VDYP (Variable
   Density Yield Prediction, natural stands) and TIPSY (Table
   Interpolation Program for Stand Yields, managed stands). Curves enter
   this package as the femic stage-1 ``woodstock_yields.csv`` keyed by
   ``curve_id``.

WS3
   The open-source wood supply model (UBC FRESH) this package drives
   through its Python API; schedules are compiled from Woodstock-format
   bridge files (``.lan``/``.are``/``.yld``/``.act``/``.trn``).

femic
   The FRESH-ecosystem package whose stage-1/stage-2 pipeline produces the
   validated TSA29 Woodstock CSVs and WS3 bridge this package consumes.

LU (landscape unit)
   The predecessor subset dimension (11 LUs); deliberately dropped — the
   pipeline runs the full TSA and the rebuilt bridge carries no LU theme.

FESBC
   Forest Enhancement Society of BC — the empirical salvage-support
   benchmark (~14-15 $/m3) the calibrated flip point is compared against.

BEC
   Biogeoclimatic Ecosystem Classification — the zone system behind the
   MFRI table (SBPS, SBS, MS, IDF, ESSF, ICH, ...).
