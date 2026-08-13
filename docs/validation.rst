Validation
==========

This page summarizes the Phase 6 validation record. The authoritative,
full-detail source is ``planning/phase6-validation-report.md`` (status:
both validation defects resolved, results re-generated, 2026-08-12); the
economic rationale is ``planning/economics-calibration.md``. Numbers below
are quoted from those records; ``outputs/`` paths referenced there are
local, uncommitted run artifacts.

What Was Validated
------------------

Phase 6 audited the pipeline on the real TSA29 inputs: the Phase 1 ingestion
(severity handling and salvageable-volume accounting), the WS3 smoke
regression, the 100-year rolling-horizon base case, and the subsidy response
of the coupled system. It found and fixed two ingestion defects, then
recalibrated the economic surface when the audit showed the placeholder
economics made salvage trivially profitable (margin ~ +93 $/m3
unsubsidized — not credible for the BC Interior).

FS-VAL-01: Severity Ladder
--------------------------

**Finding.** The burn-severity ladder was hardcoded, and any unmatched
non-null severity label was silently mapped to fraction 0.0 by a terminal
``fillna``; only the literal label "Unknown" raised a warning (12 raw / 10
retained stands on the real layer).

**Fix.** The ladder is a scenario-visible parameter (defaults Unburned 0.0 /
Low 0.30 / Moderate 0.60 / High 0.85, alias ``Medium -> Moderate``),
validated at config parse time; any other unmatched non-null rating is fatal
(``data_severity_unmatched``, listing labels and counts). The effective
ladder is echoed into the manifest.

FS-VAL-02: Coverage-Scaled Burned Volume
----------------------------------------

**Finding.** The severity fraction was applied to the stand's entire live
volume, although the rating describes a burn-severity survey polygon that
generally covers only part of the VRI polygon.

**Fix.** Rated rows are scaled by
``coverage = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)`` — an upper-bound
proxy, not a spatial intersection (see :doc:`model_semantics`). On the real
layer, 47% of rated rows fall below coverage 1; the correction cut total
salvageable volume **34%**, from 119,585.72 to **79,087.38 m3**, with green
volume unchanged (19,773,448.62 m3, row-identical). The post-fix run matches
an independent from-raw-CSV recomputation to the cent.

**Why the RH impact is small.** Re-running the 100-year dev profile changed
total burned salvage by -746.2 m3 (-0.055%): decadal burned flows are
dominated by *new* MFRI fire influx (~2.01 Mha burned over 100 years), not
by the ~152 kha initially rated 2025 stock. FS-VAL-02 corrects the initial
stock, which is a minor share of the 100-year salvage total.

Base-Case Sanity Numbers
------------------------

Post-adjustment reference numbers (calibrated defaults; subsidy 3.0 $/m3):

.. list-table::
   :header-rows: 1

   * - Quantity
     - Value
     - Note
   * - Retained stands
     - 246,957
     - from 317,750 raw WL_VFSL rows (null/zero-live drops)
   * - Green volume
     - 19,773,448.62 m3
     - row-identical across calibration rounds
   * - Salvageable (burned) volume
     - 79,087.38 m3
     - post-FS-VAL-02; 2025 season only
   * - WS3 smoke objective (3 periods)
     - 24,328,759.75 m3
     - bit-identical regression anchor (WS3 consumes the bridge, not the
       stands table or the economics)
   * - RH 100-year green harvest
     - 36,249,151.9 m3
     - decadal profile 4.849 / 4.569 / 4.238 / 3.979 / 3.717 / 3.479 /
       3.217 / 2.982 / 2.726 / 2.494 M m3 (workers 64)
   * - RH 100-year burned salvage at subsidy 3.0
     - 0.00 m3
     - as calibrated: the smallest DT margin (Cedar_ESSF -15.54 + 3.0)
       stays negative
   * - Area burned
     - ~201 kha/decade (~2.01 Mha over 100 years)
     - the MFRI fire influx still burns; recovering it is not economic at
       3 $/m3

Runtime Table
-------------

Recorded on the reference 64-core host:

.. list-table::
   :header-rows: 1

   * - Workload
     - Wall time
     - Note
   * - Ingestion (246,957 stands)
     - 33.9 - 34.7 s
     - post-fix / post-adjustment runs
   * - ``ws3-run --smoke`` (3 periods)
     - ~3.9 s end-to-end; 0.077 s solve
     - deterministic regression gate
   * - ``ws3-run`` production (30 periods)
     - 1,407.8 s
     - compile ~600 s + solve ~774 s (Phase 2.5 gate)
   * - ``rh-run`` 100 years (dev profile)
     - 150.4 - 158.9 s
     - 10 steps, 15-period WS3 re-solves, workers 64
   * - Ensemble, 20 scenarios
     - 153.6 s
     - ``workers: 1`` per scenario, ``max_workers: 64``
   * - Ensemble, 26 scenarios (flip sweep)
     - 151.8 s
     - same settings; 26/26 optimal
   * - Ensemble, ~1,000 scenarios (projection)
     - ~40 min
     - same settings

The WS3 horizon policy set at Phase 2.5: 15 periods for development, 20 for
production; 30 validated.

The Subsidy Flip Curve
----------------------

The coupled system's response to ``subsidy_rate_per_m3`` (burn-rate
multiplier 1.0; per-scenario ``workers: 1``; 26/26 prescribed scenarios
optimal plus a 5-scenario fine probe):

.. list-table::
   :header-rows: 1

   * - Subsidy ($/m3)
     - Total salvage, 100 yr (m3)
     - Step-1 salvage (m3)
   * - 0 - 19.0
     - 0.00
     - 0.00
   * - 19.1
     - 61,549.50
     - 15,907.90
   * - 19.2 - 19.3
     - 288,479.53
     - 43,142.77
   * - >= 19.4
     - 1,338,477.16 (flat, maximal)
     - 181,238.68

Turn-on at ~19.1, ramp across 19.1-19.4, saturation by 19.4 — exactly the
volume-weighted SPF development-type breakevens (19.09-19.40) predicted by
the calibration. The ramp is narrow because every ARE cohort in the WS3
bridge maps to one of the four SPF development types; the wider
species-level heterogeneity (Cedar ~15.5, Hem-Bal ~19.6, Other ~27-30 $/m3
breakevens) does not bind because no bridge stratum maps to those DTs. The
FESBC 14-15 $/m3 benchmark sits slightly below the turn-on: benchmark-level
support closes ~75-80% of the margin gap but does not flip the program.

Green harvest is subsidy-invariant (36,279,196.26 m3 at burn x1.0, max
pairwise spread 7.5e-9 — solver precision); the small delta vs the
36,249,151.9 m3 base-case total above is the ``workers``-numerics caveat
under Predecessor-Parity Caveats below. Salvage never displaces green
harvest. The burn x0.0 control is exactly zero at every subsidy.

Post-adjustment development-type economics on the real stands table (burned
cost stack 56 + 38 + 0.25 = 94.25 $/m3):

.. list-table::
   :header-rows: 1

   * - Development type
     - Burned price ($/m3)
     - Margin at subsidy 0 ($/m3)
   * - Cedar_ESSF
     - 78.71
     - -15.54
   * - Cedar_ICH
     - 77.80
     - -16.45
   * - SPF_ESSF
     - 75.16
     - -19.09
   * - SPF_MS
     - 75.15
     - -19.10
   * - SPF_IDF
     - 74.88
     - -19.37
   * - SPF_SBPS
     - 74.85
     - -19.40
   * - Hem-Bal_ICH
     - 74.66
     - -19.59
   * - SPF_ICH
     - 74.01
     - -20.24
   * - Other_MS
     - 67.35
     - -26.90
   * - Other_ICH
     - 66.52
     - -27.73
   * - Other_SBPS
     - 64.94
     - -29.31
   * - Other_IDF
     - 63.93
     - -30.32

**History, for honesty.** The first calibration round produced no flip
inside 0-25 $/m3 (the grey-stage grade mix was double-counted with the
0.85/yr decay, driving breakevens to ~48 $/m3); the prompt-salvage
adjustment of 2026-08-12 retargeted the grade mix to the year 1-3 regime
and moved the flip to the high teens. The pre-calibration placeholder
economics showed a flat, subsidy-invariant response (~+93 $/m3
unsubsidized margin). Both superseded states are documented in the
validation report — cite the current calibration only.

Predecessor-Parity Caveats
--------------------------

The pipeline is a re-implementation, not a port; documented deviations from
the predecessor Gurobi models are recorded per module (docstrings of
``principal.py``, ``agent.py``, ``rh.py``) and in
``planning/phase0-refactor-contract.md``. The ones that matter when
comparing numbers:

- **Units.** The predecessors summed *area* against the volume-denominated
  AAC in places; here every capacity row is m3-denominated.
- **AAC basis.** The ceiling bounds offered *green* volume (the conventional
  AAC basis), not green + burned.
- **Expected burn loss.** The principal's loss term is weighted by
  ``R = 1/MFRI`` (the prototype implicitly used ``R = 1``).
- **Fire is endogenous.** Burned volume is generated year by year into an
  explicit burned inventory with salvage feasibility, instead of paying a
  static ``Total_Burned_Vol`` on harvest.
- **Subsidy basis.** Paid per m3 actually salvaged, not per m3 offered.
- **WS3 smoke is regression-only.** WS3 consumes the bridge, not the stands
  table, so the smoke objective is bit-identical across ingestion and
  economics changes by construction; it proves the WS3 layer is intact, not
  that the data layers are.
- **Workers numerics.** WS3 step objectives shift slightly with the
  configured worker count (±0.014% observed); every sweep row in the record
  holds ``workers`` fixed, and cross-run comparisons must too.

Reproducing
-----------

Every number above regenerates from the CLI: ingestion
(``examples/scenario_tsa29.yaml``), WS3 smoke (``ws3-run --smoke``), the
100-year dev run (``examples/rh_tsa29.yaml``), and the flip sweep
(``examples/ensemble_flip_sweep.yaml``; see :doc:`ensembles` for how to read
the response curve). Inputs are external and never vendored; the run
manifests carry the input SHA-256 digests that tie any regenerated table to
the audited sources.
