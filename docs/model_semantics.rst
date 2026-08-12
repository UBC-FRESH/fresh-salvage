Model Semantics
===============

This page records the equations the pipeline implements. The same equations
appear in the LP rows and in the standalone fire simulation
(``fresh_salvage.fire.simulate_cohort_years``), so the optimization layers
and the state replay share one source of truth.

Cohorts And Timesteps
---------------------

Decision units are aggregate WS3 bridge cohorts keyed by
``(tsa, ifm, au_id, stratum_code, curve_id, age)`` with an area in hectares,
not individual stands. Standing volume is area times the curve yield
(m3/ha) at the cohort age. The principal and agent LPs run at 1-year
timesteps; the rolling-horizon engine implements 10 years per step and
re-solves WS3 between steps.

Fire Dynamics
-------------

The annual burn probability of a development type is ``R = 1 / MFRI`` where
MFRI is the mean fire return interval (years) of its BEC zone. An optional
``burn_rate_multiplier`` scales every rate (1.0 is the published table; 0.0
is a fire-free counterfactual).

Within one annual timestep the ordering is harvest -> fire -> salvage ->
decay. Per cohort ``c`` and year ``t``, with live fraction ``V``, burned
inventory fraction ``B``, harvest fraction ``H``, and salvage fraction
``S`` (all fractions of the cohort's initial standing volume):

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

with the annual retention of unsalvaged burned volume fixed at ``d =
0.85``: 15% of the on-hand burned volume decays away each year. This is a
deliberate volume-decay semantics (the burned inventory is the salvageable
pool); the salvage literature's value-decay framing would be an explicit
parameter change, not a silent assumption.

A cohort's volume is sold at most once across the green and burned
channels:

.. code-block:: text

   sum_t H[c,t] + sum_t S[c,t] <= 1                 (no double selling)

Initial conditions are ``V[c,0] = 1`` and ``B[c,0] = 0`` inside the LPs;
the year-0 burned stock from the 2025 severity-rated stands enters through
the cohort state assembled at the rolling-horizon boundary.

Principal LP
------------

The principal chooses continuous offer fractions ``offer[c,y]`` in
``[0, 1]`` per cohort and year, maximizing stumpage cashflow net of the
subsidy minus the expected loss of burned wood:

.. code-block:: text

   maximize  sum_{c,y} cashflow[c] * offer[c,y]
             - R[c] * burned_value[c] * (1 - d**(y-1)) * (1 - cum_offer[c,y])

   cum_offer[c,y] = sum_{t<=y} offer[c,t]
   sum_y offer[c,y] <= 1                            (offer once)
   sum_c green_volume_m3[c] * offer[c,y] <= aac_annual_m3   (AAC ceiling)

The AAC ceiling (2,937,509 m3/yr) bounds annual offered green volume. An
optional burned-volume cap (``burned_limit_annual_m3``) is unbounded by
default.

Agent LP
--------

The agent chooses continuous harvest and salvage fractions bounded by the
principal's offers, maximizing discounted NPV:

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

Prices are the development type's volume-weighted average grade prices;
burned prices carry the burned price discount (0.65). The subsidy accrues
per m3 of burned volume actually salvaged, not per m3 offered.

Rolling-Horizon State
---------------------

Each step solves a 15-period WS3 schedule from the current cohort state,
splits the period-1 decadal harvest into 10 annual per-cohort green-volume
ceilings for the principal LP, solves the principal and agent LPs over the
implemented decade, replays the implemented years with the fire dynamics
above, and transitions the cohort table: surviving live area ages by the
period length on the midpoint lattice; harvested, salvaged, and
burned-but-unsalvaged area regenerates at the smashing midpoint age. Area
is conserved to 1e-6 and verified.

Unsalvaged burned area resets to regeneration at the step boundary: no
burned-volume inventory is carried into the next step's inputs. This is a
documented deviation — the WS3 inventory represents live-stand area only,
and the 0.85/yr decay leaves under 20% of unsalvaged burned volume after 10
years, so the truncated tail is small; within-step salvage is fully
modelled by the agent LP.

Economics
---------

The economic surface (prices, harvest and transport costs, stumpage rates,
burned price discount, subsidy) is config-visible at every layer and
defaults to the calibrated constants. Under the calibrated prompt-salvage
regime the unsubsidized salvage margin is approximately -15 $/m3 on the SPF
transition-mix basis (a moderate negative band across development types,
roughly -10 to -36 $/m3 volume-weighted), so salvage is not economic
without support; the behavioural flip of the coupled system sits at a
subsidy of approximately 19.2 $/m3, just above the FESBC benchmark of
14-15 $/m3.

The full parameter table, per-parameter rationale, provenance labels
(market anchor, derived, or assumption), and the decay-semantics note are
recorded in ``planning/economics-calibration.md``. That document is the
authoritative reference for the calibrated values; treat the economics as
semi-synthetic calibrated parameters whose rationale lives there.
