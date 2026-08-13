Validation
==========

This page summarizes the Phase 6 validation record. The authoritative,
full-detail source is ``planning/phase6-validation-report.md`` (status:
both validation defects resolved, results re-generated, 2026-08-12;
grade-transition monotonicity erratum fixed 2026-08-13); the
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
     - 36,279,574.5 m3
     - decadal profile 4.951 / 4.564 / 4.230 / 3.973 / 3.709 / 3.469 /
       3.210 / 2.971 / 2.715 / 2.486 M m3 (workers 64)
   * - RH 100-year burned salvage at subsidy 3.0
     - 0.00 m3
     - as calibrated: the smallest DT margin (Cedar_ESSF -20.70 + 3.0)
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
multiplier 1.0; per-scenario ``workers: 1``; 31/31 scenarios of a
15.0-30.0 step-0.5 sweep optimal, plus a 10-scenario fine probe):

.. list-table::
   :header-rows: 1

   * - Subsidy ($/m3)
     - Total salvage, 100 yr (m3)
     - Step-1 salvage (m3)
   * - 0 - 23.8
     - 0.00
     - 0.00
   * - 23.85
     - 61,549.50
     - 15,907.90
   * - 23.9 - 24.05
     - 288,479.53
     - 43,142.77
   * - >= 24.1
     - 1,338,477.16 (flat, maximal)
     - 181,238.68

(The sweep measured 15.0-30.0 directly; below 15.0 the zero follows from
the margin arithmetic — every DT margin at subsidy 0 is <= -20.70 $/m3, so
no subsidy under 20.70 can make salvage positive — plus the pre-erratum
sweeps, which measured 0.00 across 0-15 under a strictly higher price
surface.)

Turn-on at ~23.85, ramp across 23.85-24.1, saturation by 24.1 — exactly
the volume-weighted SPF development-type breakevens (23.85-24.07)
predicted by the calibration. The ramp is narrow because every ARE cohort
in the WS3 bridge maps to one of the four fire-exposed SPF development
types; the wider species-level heterogeneity (Cedar ~20.7-21.5, Hem-Bal
~24.3, Other ~29-32 $/m3 breakevens) does not bind because no bridge
stratum maps to those DTs. The FESBC 14-15 $/m3 benchmark sits well below
the turn-on: benchmark-level support closes ~60% of the margin gap but
does not flip the program.

Green harvest is subsidy-invariant (36,279,196.26 m3 at burn x1.0, max
pairwise spread 7.5e-9 — solver precision); the small delta vs the
36,279,574.5 m3 base-case total above is the ``workers``-numerics caveat
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
     - 73.55
     - -20.70
   * - Cedar_ICH
     - 72.79
     - -21.46
   * - SPF_ESSF
     - 70.40
     - -23.85
   * - SPF_CWH
     - 70.39
     - -23.86
   * - SPF_MS
     - 70.39
     - -23.86
   * - SPF_IDF
     - 70.20
     - -24.05
   * - SPF_SBPS
     - 70.18
     - -24.07
   * - Hem-Bal_ICH
     - 69.98
     - -24.27
   * - SPF_ICH
     - 69.62
     - -24.63
   * - Other_MS
     - 64.82
     - -29.43
   * - Other_ICH
     - 64.29
     - -29.96
   * - Other_SBPS
     - 63.10
     - -31.15
   * - Other_IDF
     - 62.38
     - -31.87

**History, for honesty.** The first calibration round produced no flip
inside 0-25 $/m3 (the grey-stage grade mix was double-counted with the
0.85/yr decay, driving breakevens to ~48 $/m3); the prompt-salvage
adjustment of 2026-08-12 retargeted the grade mix to the year 1-3 regime
and moved the flip to the high teens — but its Sawlog row still carried a
physically impossible 0.10 Sawlog->Peeler upgrade, caught in review and
fixed on 2026-08-13 (the burned sawlog remainder now drops straight to
pulp), moving the flip to 23.85-24.1 $/m3. The pre-calibration placeholder
economics showed a flat, subsidy-invariant response (~+93 $/m3
unsubsidized margin). All superseded states are documented in the
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
