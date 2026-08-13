Model Semantics
===============

This page records what the model computes: the equations, the state
variables, and the assumptions behind them. The same fire equations appear
in the agent LP rows and in the standalone fire simulation
(``fresh_salvage.fire.simulate_cohort_years``), so the optimization layers
and the state replay share one source of truth. Read this page before
trusting any number the pipeline emits.

Decision Units: Stands, Development Types, Cohorts
--------------------------------------------------

Three granularities appear in the pipeline; do not mix them up.

**Stands** (ingestion only). One row per polygon of the WL_VFSL layer, the
BC Vegetation Resources Inventory (VRI) extract for TSA29 joined with
burn-severity attributes: 246,957 retained stands over the full TSA (all
12 Biogeoclimatic Ecosystem Classification (BEC) zones; the predecessor's
subset filter is removed). Each stand is assigned a
**development type** (DT) key ``{leading_species_group}_{BEC}`` (for
example ``SPF_SBPS``, ``Cedar_IDF``) from its leading species code and BEC
zone, and carries derived green and burned volumes split over 13
species/grade buckets.

**Strata and analysis units** (the WS3 bridge). The validated femic TSA29
instance stratifies the TSA into 54 analysis units — 18 strata
(``{bec_zone}_{leading_species}`` codes such as ``sbps_pli``) crossed with
3 site-index levels — each carrying yield curves from BC's VDYP and TIPSY
models, keyed by ``curve_id``. Crossed with the 2 IFMs (the
managed/unmanaged lanes) and the raw single-year age dimension, the femic
stage-1 areas table holds 44,998 rows at its native stratification. The
next section describes how that table becomes the WS3 input files.

**Cohorts** (the LP decision units). After the adjustment described below,
the areas table collapses to **1,608 aggregated cohorts**
keyed by ``(tsa, ifm, au_id, stratum_code, curve_id, age)``. A cohort's
standing volume is its area times the curve yield (m3/ha) at the cohort
age, with linear interpolation between curve points and constant endpoint
extension beyond the tabulated age range. The principal and agent LPs run
at 1-year timesteps over these cohorts; the rolling-horizon engine
implements 10 years per step and re-solves WS3 between steps.

How the WS3 input files are built
---------------------------------

WS3 reads its model definition from Woodstock text files
(``.lan``/``.are``/``.act``/``.trn``/``.yld``). fresh-salvage derives those
files from a staging table that femic exports: ``woodstock_areas.csv``,
which stratifies the TSA's area over analysis unit, managed/unmanaged
lane, yield curve, and raw inventory age. The current export holds 44,998
stratified rows, totalling 2,977,503.74 ha.

That staging table is valid and current. It was never meant to be consumed
line-for-line: an early ``.are`` file that did exactly that — kept every
staging key column and the raw single-year ages against a five-theme
model — was the broken artifact, not the table behind it.

When fresh-salvage builds the WS3 model, it adjusts the staging table,
hands it to femic's own stage-2 writer
(``femic.ws3_bridge.build_ws3_sections_from_femic_woodstock``), and then
verifies what the writer emitted:

1. **Drop the landscape-unit column.** The column leaves the staging key
   before anything is aggregated.
2. **Snap ages to class midpoints.** Each raw inventory age moves to the
   midpoint of its 10-year class: 11 and 17 both become 15. The code and
   config call this ``age_smashing``; the rule is
   ``age // 10 * 10 + 5``, so the age lattice runs 5/15/25/....
3. **femic's writer re-groups, sums, and writes.** The writer groups rows
   on the remaining key ``(tsa, ifm, au_id, stratum_code, curve_id,
   age)``, sums their areas — the 44,998 staged rows collapse to 1,608 —
   and writes the Woodstock files.
4. **fresh-salvage verifies the written files.** The written bridge must
   carry the expected theme count, every written age must lie on the
   midpoint lattice, and the written ARE section must conserve the staged
   area total to a relative tolerance of 1e-6 — otherwise the build
   refuses to proceed (``area_conservation_failed``).

fresh-salvage never writes Woodstock text itself. One wart to know about:
the femic writer silently drops rows whose ``(tsa, ifm, au_id)`` key has
no yield curve, so the written-area check in step 4 is what stands between
a quiet data defect and a wrong model.

Where the adjustment lives. The adjustment runs at consumption time,
inside fresh-salvage, rather than at the source in femic. The cleaner
arrangement — regenerating the staging table in femic at the grain this
model consumes — remains an option. The current split exists because the
adjustment was defined after femic's staging export already existed;
adjusting at consumption time kept the existing valid export unchanged.

Fire Dynamics
-------------

The annual burn probability of a development type is ``R = 1 / MFRI``
where MFRI is the mean fire return interval (years) of its BEC zone:

.. list-table::
   :header-rows: 1

   * - BEC zone
     - MFRI (years)
     - Annual burn rate R
   * - SBPS
     - 100
     - 0.0100
   * - SBS
     - 125
     - 0.0080
   * - MS
     - 150
     - 0.0067
   * - IDF
     - 200
     - 0.0050
   * - ESSF
     - 200
     - 0.0050
   * - ICH
     - 250
     - 0.0040

An optional ``burn_rate_multiplier`` scales every rate (1.0 is the published
table; 0.0 is a fire-free counterfactual); a scaled rate above 1.0 is
rejected. An unmapped BEC zone stops the run (``UnknownBurnRateError``) —
the pipeline never silently borrows a neighbouring zone's fire regime.

Within one annual timestep the ordering is **harvest -> fire -> salvage ->
decay**. Per cohort ``c`` and year ``t``:

.. code-block:: text

   exposed[t]  = V[t-1] - H[t]                      (harvest first: harvested
                                                     volume is no longer
                                                     exposed to this year's
                                                     fire)
   BURN_IN[t]  = R * exposed[t]                     (MFRI fire influx)
   V[t]        = V[t-1] - H[t] - BURN_IN[t]         (live balance)
   S[t]       <= B[t-1] + BURN_IN[t]                (salvage feasibility:
                                                     only burned inventory on
                                                     hand after this year's
                                                     fire can be salvaged)
   B[t]        = (B[t-1] + BURN_IN[t] - S[t]) * d   (burned balance)

Symbol definitions (all of ``V``, ``B``, ``H``, ``S`` are fractions of the
cohort's initial standing volume; the same equations run in m3 in the
standalone simulation):

``V[c, t]``
   Live (standing) fraction at end of year ``t``. Initial condition
   ``V[c, 0] = 1`` inside the LPs.

``B[c, t]``
   Burned-inventory fraction at end of year ``t`` — the salvageable pool.
   Initial condition ``B[c, 0] = 0`` inside the LPs; the year-0 burned stock
   of the 2025 severity-rated stands enters through the cohort state
   assembled at the rolling-horizon boundary.

``H[c, t]``
   Fraction of the cohort's initial standing volume harvested green in year
   ``t`` (decision variable of the agent; bounded by the principal offer).

``S[c, t]``
   Fraction salvaged from the burned inventory in year ``t`` (decision
   variable of the agent; bounded by the offer and by salvage feasibility).

``R``
   Annual burn probability ``1 / MFRI[bec_zone]``, optionally scaled by
   ``burn_rate_multiplier``.

``d``
   Annual retention fraction of unsalvaged burned volume
   (``decay_rate``, default 0.85): 15% of the on-hand burned volume decays
   away each year.

``BURN_IN[t]``
   This year's fire influx, ``R * exposed[t]``.

A cohort's volume is sold at most once across the green and burned
channels:

.. code-block:: text

   sum_t H[c,t] + sum_t S[c,t] <= 1                 (no double selling)

The 0.85 retention is a deliberate **volume-decay** semantics: the burned
inventory is the salvageable pool, so physical volume retention is the
operationally relevant state variable. The salvage literature's value-decay
framing (stain/checking/downgrade progression on value) would be an explicit
parameter change, not a silent assumption; see the decay-semantics note in
``planning/economics-calibration.md``.

The Principal LP
----------------

The principal chooses continuous offer fractions ``offer[c, y]`` in
``[0, 1]`` per cohort ``c`` and year ``y`` (1-year timesteps). It maximizes
stumpage cashflow — stumpage is the per-m3 fee the licensee pays the Crown
for standing timber — net of the subsidy, minus the expected loss of
burned wood:

.. code-block:: text

   maximize  sum_{c,y} cashflow[c] * offer[c,y]
             - R[c] * burned_value[c] * (1 - d**(y-1)) * (1 - cum_offer[c,y])

   cum_offer[c,y] = sum_{t<=y} offer[c,t]           (definition rows)
   sum_y offer[c,y] <= 1                            (offer once)
   sum_c green_volume_m3[c] * offer[c,y] <= aac_annual_m3   (AAC ceiling)
   sum_c burned_volume_m3[c] * offer[c,y] <= burned_limit_annual_m3
                                                    (only when configured)

where, per cohort (all volumes m3, parsed at the boundary):

``standing_volume_m3[c]``
   ``area_ha[c] * curve_volume_m3_per_ha(curve, age)``;
   ``green_volume_m3[c]`` is the same quantity (the live standing volume).

``burned_volume_m3[c]``
   ``standing_volume_m3[c] * burn_share[dt]`` with
   ``burn_share[dt] = Total_Burned_Vol / Total_Green_Vol`` aggregated over
   the stands of the cohort's development type — this is how the ingested
   2025 severity stock enters the LP layers.

``cashflow[c]``
   ``green_vol * green_stumpage + burned_vol * burned_stumpage
   - burned_vol * subsidy`` — the principal's take when the whole cohort is
   offered once (the subsidy is a cost to the principal).

``burned_value[c]``
   The cohort's burned volume priced at its development type's
   volume-weighted average burned price.

``R[c] * burned_value[c] * (1 - d**(y-1))``
   The expected burned-wood loss charged against volume not yet offered by
   year ``y``: the MFRI-weighted probability of burning times the decayed
   burned value. (The prototype charged the full decayed value every year,
   implicitly ``R = 1``; here it is an expected loss.)

The AAC ceiling (default **2,937,509 m3/yr**; AAC is the Annual Allowable
Cut, the regulator's annual harvest ceiling) bounds annual *offered green*
volume and binds in the calibrated base case. Because one fraction variable
scales both volumes, an offer's green:burned split always equals the
cohort's standing fractions, and offer-once is exactly volume conservation.
The LP is pure linear — no binaries, no thresholding — and zero offer rows
are emitted explicitly so downstream consumers see a complete panel.

The Agent LP
------------

The agent chooses continuous harvest and salvage fractions bounded by the
principal's offers, maximizing discounted net present value (NPV) over the
same cohorts and 1-year timesteps. Variables per cohort-year: ``H[c,t]``,
``S[c,t]``, ``V[c,t]``, ``B[c,t]`` — the balance rows are exactly the fire
dynamics above:

.. code-block:: text

   maximize  sum_{c,t} df_t * standing_volume_m3[c]
             * (green_margin_m3[c] * H[c,t]
                + salvage_margin_m3[c] * S[c,t])

   df_t = 1 / (1 + discount_rate)**t                (default 3%)

   green_margin_m3   = green_price - green_harvest_cost
                       - green_transport_cost - green_stumpage_rate
   salvage_margin_m3 = burned_price - burned_harvest_cost
                       - burned_transport_cost - burned_stumpage_rate
                       + subsidy_rate_per_m3

   H[c,t], S[c,t] <= offer[c,t]                     (principal coupling)
   S[c,t] <= B[c,t-1] + R * (V[c,t-1] - H[c,t])     (salvage feasibility)
   sum_t H[c,t] + sum_t S[c,t] <= 1                 (no double selling)

Prices are the development type's volume-weighted average grade prices
(weighted by the configured ``green_prices``), and burned prices carry the
burned price discount (0.65) through the prompt-salvage grade transition
below. The subsidy accrues per m3 of burned volume **actually salvaged**,
not per m3 offered. Offers are an input: a uniform
``default_offer_fraction`` (1.0 = fully offered), or a principal offer
table (``cohort_id``/``year``/``offer_fraction`` columns, parquet or csv).

The Rolling-Horizon Loop
------------------------

``fresh_salvage.rh`` coordinates the three solvers over decadal steps. Each
step *solves* a full ``horizon``-period WS3 schedule (dev profile 15 periods
of 10 years) but *implements* only the first period; the principal/agent LPs
cover exactly the implemented window at 1-year timesteps:

.. code-block:: text

   state  <- cohort table of the derived bridge ARE section
             (1,608 cohorts at the full-TSA scale)
   model  <- one WS3 ForestModel loaded once (bridge and static inputs
             cached; only the inventory changes between steps)

   for step k = 1 .. steps:
       write state to derived/rh_state/step_{k-1}.are and re-parse it
           (the file the LPs parse is bit-identical to the injected state)
       inject state into the model's period-0 inventory in memory
           (area-conservation checked to 1e-6)
       build and solve the horizon-period WS3 problem; a non-optimal
           status is fatal (ws3_solve_not_optimal)
       compile the schedule; sum period-1 `cc` volume per cohort
       split each cohort's decadal volume uniformly into period_length
           annual green-volume ceilings (the split conserves volume)
       solve the principal LP over the implemented decade
           (global AAC ceiling + per-cohort annual ceilings)
       solve the agent LP against the offers (annual fire dynamics)
       replay the implemented years with fire.simulate_cohort_years
       advance the cohort table (below); flush the step record to
           <run>-steps.jsonl

   write the final cohort state CSV and the run manifest

Cohort transitions partition each cohort's area into four exhaustive
fractions (they sum to one by construction; area is conserved to 1e-6 and
verified per step):

- **surviving live area** stays in the cohort at ``age + period_length``,
  clamped to the curve's age cap (the absorbing oldest class on the
  midpoint lattice; volume-neutral up to curve flatness beyond the
  tabulated range);
- **harvested area** (agent ``sum_t H[c,t]``) regenerates at the class
  midpoint age (5 with the default 10/5 rule);
- **salvaged area** (``sum_t S[c,t]``) regenerates at the midpoint age;
- **burned-but-unsalvaged area** (``1 - live_end - H - S``) regenerates at
  the midpoint age at the step boundary.

**Burned-stock boundary (documented deviation).** Unsalvaged burned area
resets to regeneration at the step boundary; no burned-volume inventory
carries into the next step's WS3/principal/agent inputs. This truncates the
multi-year salvage window the agent LP allows within a step. Rationale: (1)
the WS3 ARE inventory carries live-stand area only, so a burned-volume
carry-over has no representation on the WS3 side; (2) the 0.85/yr decay
leaves under 20% of unsalvaged burned volume after 10 years, so the
truncated tail is small; (3) within-step salvage is fully modelled by the
agent LP. The predecessor rolling-horizon scripts carried no burned stock
across steps either.

Severity And Burned Volume At Ingestion
---------------------------------------

The burn-severity ladder converts a stand's rating into the fraction of live
volume that becomes salvageable. It is a scenario-visible parameter
(``severity`` block of the ingestion config) with these package defaults:

.. list-table::
   :header-rows: 1

   * - Rating
     - Burned fraction
     - Note
   * - Unburned
     - 0.0
     - rated but unburned
   * - Low
     - 0.30
     - —
   * - Moderate
     - 0.60
     - the layer labels this tier "Medium"; the alias ``Medium ->
       Moderate`` normalizes it at the boundary
   * - High
     - 0.85
     - —
   * - Unknown
     - 0.0
     - recognized label; treated as unburned with an
       ``ingest_unknown_severity`` warning (12 raw / 10 retained stands on
       the real layer)

Any other unmatched non-null rating **halts ingestion**
(``data_severity_unmatched``, listing the offending labels and counts) — the
predecessor's silent ``fillna(0.0)`` is not reproduced (FS-VAL-01). Ladder
fractions must lie in [0, 1], alias targets must be ladder labels, and alias
sources must not collide with ladder labels; invalid ladders fail at config
parse time.

The severity rating describes a burn-severity survey polygon that generally
covers only part of the VRI polygon, so each rated row is coverage-scaled
(FS-VAL-02). Two area columns feed the scaling:

``FEATURE_AREA_SQM``
   Area (m2) of the VRI stand polygon — the inventory feature the row
   describes.

``SHAPE_Area_1``
   Area (m2) of the fire-severity polygon from the severity-rating layer
   that overlaps this stand.

.. code-block:: text

   coverage    = min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)
   salvageable = severity_fraction * coverage * live_volume

Both columns are whole-polygon attributes of their respective layers;
neither is clipped to the other. The ratio is therefore an upper bound on
the covered share: if the severity polygon is smaller than the stand
polygon the ratio assumes it lies entirely inside this stand, and if it is
larger the clamp to 1 assumes full coverage. Salvageable volume on rated
stands is therefore an **upper bound**. Rated rows with a
missing/non-positive denominator (``FEATURE_AREA_SQM``) halt ingestion
(``data_coverage_denominator_invalid``); a missing/non-positive numerator
(``SHAPE_Area_1``) halts ingestion (``data_coverage_numerator_invalid``).

On the real layer this correction cut total salvageable volume from
119,585.72 to 79,087.38 m3 (-34%; green volume unchanged). Burned volume is
split across the same species/grade buckets as green volume and degraded
through the prompt-salvage grade transition below. Every rated row carries
``FIRE_YEAR = 2025``: the severity layer covers the 2025 fire season only,
and earlier burns seed no initial salvageable volume.

Economics
---------

Every economic parameter can be set in the config: an ``economics``
section on the ingestion/principal/agent configs, and flat fields on the
rolling-horizon config so the ensemble driver can vary any of them as a
named axis. The defaults are the calibrated constants:

.. list-table::
   :header-rows: 1

   * - Parameter
     - Default
     - Provenance
   * - Green prices, SPF peel/saw/pulp ($/m3)
     - 146 / 127 / 55
     - SPF sawlog at the Q4-2023 BC Interior Log Market Report level
       (market anchor); peeler = saw x 1.15 and pulp at market pulpwood are
       assumptions
   * - Green prices, Df-Larch peel/saw/pulp ($/m3)
     - 118 / 103 / 55
     - same anchors (sawlog benchmark; peeler/pulp assumptions)
   * - Green prices, Hem-Bal peel/saw/pulp ($/m3)
     - 138 / 120 / 55
     - same anchors
   * - Green prices, Cedar peel/saw/pulp ($/m3)
     - 166 / 144 / 55
     - same anchors
   * - Green price, Other ($/m3)
     - 90
     - predecessor mixed-secondary basket price (assumption)
   * - ``burned_price_discount``
     - 0.65
     - BC fire-damaged pricing adjustments of -$34-36/m3 plus observed
       sawlog-to-pulpwood downgrade of burned lots
   * - ``green_harvest_cost`` ($/m3)
     - 45
     - Interior logging cost ranges plus road/admin/silviculture allocation
       (derived)
   * - ``burned_harvest_cost`` ($/m3)
     - 56
     - +25% over green: mild, recently-killed prompt-salvage case (derived;
       salvage literature shows +15-46% unit costs)
   * - ``green_transport_cost_per_m3`` ($/m3)
     - 30
     - 100-200 km one-way haul in a 4.93 Mha TSA (derived)
   * - ``burned_transport_cost_per_m3`` ($/m3)
     - 38
     - +25% over green haul (derived)
   * - ``green_stumpage_rate`` ($/m3)
     - 15
     - BC South Central appraised stumpage, mid-range (derived)
   * - ``burned_stumpage_rate`` ($/m3)
     - 0.25
     - BC tabular stumpage floor for fire-damaged timber (Table 6-4a)
   * - ``subsidy_rate_per_m3`` ($/m3)
     - 3.0
     - predecessor default policy lever, retained
   * - ``decay_rate`` (burned retention/yr)
     - 0.85
     - deliberate volume-decay semantics (see Fire Dynamics)
   * - ``discount_rate``
     - 0.03
     - predecessor default, retained
   * - Burned grade transition (peel -> peel/saw/pulp)
     - 0.55 / 0.35 / 0.10
     - prompt-salvage (year 1-3) retention; red-stage evidence (Plank 1984;
       Loeffler & Anderson 2018); the grey-stage pulp collapse lives in the
       0.85/yr decay, not the initial mix; downgrade-only (Peel > Saw >
       Pulp — fire never upgrades grade)
   * - Burned grade transition (saw -> peel/saw/pulp)
     - 0.00 / 0.80 / 0.20
     - same regime; the saw remainder drops straight to pulp
   * - Burned grade transition (pulp)
     - stays pulp (1.0)
     - same regime

Margin decompositions on the SPF price bases (pinned by tests):

.. code-block:: text

   green   = 127.00 - 45 - 30 - 15    = +37.00 $/m3   (sawlog basis)
   salvage =  82.55 - 56 - 38 - 0.25  = -11.70 $/m3   (sawlog basis)
   salvage =  73.19 - 56 - 38 - 0.25  = -21.06 $/m3   (transition mix)

The agent LP does not price cohorts at the sawlog price: each cohort carries
its development type's volume-weighted average over the grade columns, with
burned volume grade-degraded. On the real stands table the calibrated
constants give green margins positive everywhere (SPF ~ +31.3, Cedar
~ +46.9, Hem-Bal ~ +25.0, Df-Larch ~ +9.4, Other ~ 0 $/m3) and salvage
margins at subsidy 0 in a moderate negative band (Cedar ~ -15.7, SPF
~ -23.9, Hem-Bal ~ -27.2, Df-Larch ~ -35.3, Other ~ -35.8 $/m3) — negative
for 100% of the burned volume, so salvage is not economic without support.
The coupled system's behavioural flip sits at a subsidy of approximately
24 $/m3 (turn-on 23.85, saturated by 24.1 — the SPF cluster's breakevens);
the FESBC benchmark support of 14-15 $/m3 closes roughly 60% of the margin
gap but does not flip the program. The full per-DT table and the sweep
evidence are in :doc:`validation`.

Treat these economics as **semi-synthetic calibrated parameters**: they are
built from market anchors plus documented assumptions, not measured on a
specific tenure. Each parameter carries a rationale and a provenance label
(market anchor, derived, or assumption) in
``planning/economics-calibration.md``, which is the authoritative reference.
Two predecessor defects are deliberately not reproduced: burned peeler
volumes are written under the schema's ``B_*_Peelers_Vol`` names (so burned
volume is conserved), and Other-species salvageable volume is routed into
``B_Other_Vol`` instead of being dropped.

Known Limitations
-----------------

- **2025-only severity scope.** Every rated row carries
  ``FIRE_YEAR = 2025``; earlier burns carry no severity rating and seed no
  initial salvageable volume. The 12 raw (10 retained) "Unknown" ratings are
  treated as unburned under an explicit warning.
- **Upper-bound coverage.** The FS-VAL-02 coverage factor is a whole-polygon
  area ratio, not a spatial intersection; rated-stand salvageable volume is
  an upper bound.
- **Burned-stock reset.** Unsalvaged burned area resets to regeneration at
  the rolling-horizon step boundary; no burned-volume inventory carries
  across steps (documented deviation above).
- **WS3 workers numerics.** WS3 step objectives shift slightly with the
  configured worker count (±0.014% observed); hold ``workers`` fixed for
  cross-run comparisons.
- **Semi-synthetic economics.** Prices, costs, and stumpage are calibrated
  semi-synthetic values, not measurements of a specific tenure; the
  per-parameter rationale lives in ``planning/economics-calibration.md``.
- **Alpha APIs.** Public APIs at ``0.1.0a1`` may change before a stable
  release, and the ``export`` CLI command remains a reserved stub.
