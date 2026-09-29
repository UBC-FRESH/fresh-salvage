# Thesis Results Plan: Salvage-Subsidy Model

Status: planning document; discussion-session analysis not yet generated

## Purpose

This note defines a practical results package for a master's thesis using the
current `fresh-salvage` TSA29 pipeline. The recommended analysis is a calibrated
baseline followed by subsidy, fire-risk, economic, and principal-agent
sensitivity experiments.

The results should be presented as scenario-based model evidence, not as a
precise empirical forecast. Every reported result must identify the effective
configuration, input artifacts, solver status, and relevant model caveats.

This note complements `planning/first-best-hidden-information.md`, which
defines a future first-best and hidden-information experiment. The present
note focuses on results that can be collected from the implemented ingestion,
WS3, principal, agent, rolling-horizon, ensemble, and sensitivity pipeline.

## Central Discussion Question

The central discussion question is:

> Under the calibrated TSA29 forest, fire, and economic assumptions, when does
> salvage become economically active, how does fire risk affect the result, and
> what difference is created by the separation between principal offers and
> agent actions?

## Recommended Results Package

The discussion-session package should contain:

1. One calibrated baseline results table.
2. One subsidy-response figure.
3. One fire-risk sensitivity figure.
4. One principal-offer versus agent-realization figure.
5. One development-type comparison table.
6. One limitations slide explaining the treatment of future fire grades and
   rolling-horizon burned-stock reset.

## Result 1: Calibrated Baseline

Run the default 100-year rolling-horizon scenario with a fixed WS3 worker
count. Record the run configuration and report:

- total green harvest volume;
- total burned salvage volume;
- total area burned;
- total subsidy paid;
- principal objective value;
- agent objective value or NPV;
- annual or decadal green harvest;
- annual or decadal burned salvage;
- final live/cohort age distribution;
- solver status and wall time.

The default calibrated subsidy is `$3/m3`. Current validation evidence shows
that this subsidy produces zero burned salvage in the calibrated default run.
This is a useful baseline result: under the current cost, price, stumpage,
grade, and decay assumptions, salvage is not privately profitable at the
default subsidy.

### Suggested baseline table

| Metric | Baseline value | Unit | Source |
| --- | ---: | --- | --- |
| Green harvest | TBD | m3 | RH manifest |
| Burned salvage | TBD | m3 | RH manifest |
| Area burned | TBD | ha | RH manifest |
| Subsidy paid | TBD | currency | RH/agent records |
| Principal objective | TBD | currency | principal records |
| Agent objective | TBD | currency | agent records |
| Final live inventory | TBD | ha or m3 | final-state artifact |
| Run status | TBD | status | RH manifest |

Do not fill this table with numbers from a superseded calibration run. The
current grade-transition erratum and the current economics are authoritative.

## Result 2: Subsidy Threshold

The subsidy sweep is the clearest policy result. Vary
`subsidy_rate_per_m3` over a broad range, then run a fine probe around the
turn-on point. A suitable first sweep is:

```text
0, 5, 10, 15, 20, 23, 24, 25, 30, 40 $/m3
```

The fine probe should include values around `$23.85-$24.10/m3`, subject to
reconfirmation with the current run configuration and fixed WS3 worker count.

For every subsidy level, record:

- total burned salvage;
- first-step burned salvage;
- total green harvest;
- total subsidy expenditure;
- principal objective;
- agent objective;
- number of active development types;
- solver status.

### Main interpretation

The current corrected calibration identifies a subsidy turn-on of
approximately `$23.85/m3`, with additional development types becoming active
through roughly `$24.10/m3`, and saturation at the current physical salvage
opportunity above that range. The default `$3/m3` subsidy and the FESBC
benchmark range of approximately `$14-$15/m3` are below this current modeled
turn-on.

These values are model outputs, not universal policy thresholds. They depend
on the calibrated prices, costs, burned-grade mix, fire rates, AAC, decay
semantics, and the current offer structure.

### Suggested figure

Plot:

```text
x-axis: subsidy rate ($/m3)
y-axis: total burned salvage (m3)
series: calibrated fire, fire-free control if useful
```

Add a vertical marker for the policy benchmark and a second marker for the
modeled turn-on range.

## Result 3: Fire-Risk Sensitivity

Vary `burn_rate_multiplier` while holding all economic settings fixed:

```text
0.0  fire-free counterfactual
0.5  lower fire risk
1.0  calibrated fire risk
```

Use only values allowed by the configuration. The `0.0` case is especially
useful as a control because it should produce no annual fire influx and no
dynamic burned salvage.

Compare:

- green harvest;
- burned salvage;
- area burned;
- agent NPV;
- principal objective;
- final live inventory;
- total burned volume entering the agent dynamics.

### Suggested interpretation

This experiment separates the effect of physical fire exposure from the effect
of the subsidy. It can show whether higher fire risk increases salvage supply,
reduces live green inventory, changes the principal's timing incentives, or
changes agent participation.

Do not describe the future fire volume as receiving the ingestion severity
grade transition. The current fire model generates annual burned volume from
the BEC-zone burn rate and tracks it as an undifferentiated burned pool.

## Result 4: Economic Sensitivity

Vary the parameters that directly control burned salvage margins:

- `burned_harvest_cost`;
- `burned_transport_cost_per_m3`;
- `burned_price_discount`;
- `burned_stumpage_rate`;
- `subsidy_rate_per_m3`.

The principal margin and agent margin should be decomposed explicitly. The
agent's burned margin is:

```text
burned price
- burned harvest cost
- burned transport cost
- burned stumpage
+ subsidy
```

For each sensitivity, report the change in:

- burned salvage volume;
- subsidy turn-on level;
- agent objective;
- principal objective;
- green harvest displacement, if any.

### Suggested figure

Use a tornado chart or a small multiple of subsidy-response curves. Keep the
baseline economics and all non-varied parameters fixed.

## Result 5: Principal-Agent Comparison

The principal and agent should not be treated as producing the same burned
volume calculation.

Within a rolling-horizon step:

- the principal uses current WS3 standing volume multiplied by the
  ingestion-derived development-type burned share;
- the principal's burned-volume basis is fixed while its LP solves;
- the principal includes future fire through an expected-loss term;
- the agent generates annual fire influx dynamically from remaining live
  volume;
- the agent chooses actual green harvest and burned salvage subject to the
  principal's offer;
- the agent applies explicit burned-inventory decay to unsalvaged burned
  volume.

Compare:

- volume offered by the principal;
- volume harvested by the agent;
- volume salvaged by the agent;
- offered-but-unharvested volume;
- offered-but-unsalvaged volume;
- principal objective;
- agent objective;
- timing of offers and realized decisions.

Useful ratios include:

```text
offer realization rate = actual realized volume / offered eligible volume

salvage realization rate = actual salvage / principal burned-volume offer

green realization rate = actual green harvest / principal green offer
```

These should be reported by year, rolling-horizon step, and development type
where the artifacts support the breakdown.

### Suggested figure

Use paired bars or lines for:

```text
principal offered volume
agent green harvest
agent burned salvage
```

This makes the principal-agent separation visible without claiming that the
current pipeline is a solved bilevel contract model.

## Result 6: Development-Type Heterogeneity

Aggregate results by `development_type`, for example:

```text
SPF_SBPS
Cedar_IDF
Df-Larch_SBS
```

For each development type, report:

- green volume;
- ingested burned volume;
- burned share;
- volume-weighted burned price;
- unsubsidized burned margin;
- subsidy breakeven;
- salvage volume;
- total subsidy received.

This can show that the subsidy is not equally effective across ecological and
species groups. Development type is the model's operational bridge between
stand-level ingestion and cohort-level WS3/LP decisions.

### Suggested table

| Development type | Green volume | Burn share | Burned price | Margin at subsidy 0 | Breakeven subsidy | Salvage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Use the effective configuration and report units consistently.

## Result 7: Timing and Decay

Within the agent model, report how burned volume is divided among:

- burned volume generated by new annual fire;
- burned volume salvaged in the same year;
- burned volume carried after salvage;
- volume removed by the annual decay factor.

The current dynamics are:

```text
harvest -> fire -> salvage -> decay

burned_after[t] =
    (burned_before[t] + burn_influx[t] - salvaged[t]) * 0.85
```

This supports a discussion of prompt salvage and the cost of delaying recovery.
The current `0.85` factor is a deliberate volume-retention assumption. It is
not a claim that physical volume, rather than timber value, is the only valid
interpretation of post-fire deterioration.

## Recommended Experiment Matrix

The minimum experiment matrix for the discussion session is:

| Experiment | Main axis | Purpose |
| --- | --- | --- |
| Baseline | calibrated defaults | establish the reference outcome |
| Fire-free control | burn multiplier 0.0 | isolate fire effects |
| Subsidy sweep | subsidy rate | estimate salvage turn-on and saturation |
| Fire sensitivity | burn multiplier | test future fire exposure |
| Cost sensitivity | burned harvest/transport cost | test economic robustness |
| Grade/economic sensitivity | burned price discount; transition only if separately implemented | test value assumptions |
| Principal-agent comparison | offers versus realized actions | quantify implementation gap |
| Development-type table | development type | identify heterogeneous effects |

For all comparisons, hold fixed:

- WS3 worker count;
- bridge and yield inputs;
- horizon and number of rolling steps;
- AAC and objective settings;
- source artifacts and checksums.

The WS3 documentation records small numerical shifts with worker count, so
cross-run comparisons should not mix worker settings.

## Artifacts To Collect

Each experiment should retain:

- the YAML configuration;
- ingestion data and manifest, or its source checksum;
- WS3 schedule and manifest;
- rolling-horizon steps JSONL;
- principal offers;
- agent decisions;
- final cohort state;
- ensemble or sensitivity observations;
- solver status and diagnostics;
- a short interpretation note.

The manifest is the authoritative provenance record for the run. A result that
does not have an optimal or explicitly interpreted degraded status should not
be presented as a headline finding.

## Discussion Claims Supported By The Current Model

The current pipeline can support claims such as:

- under the calibrated baseline, the default subsidy does not activate
  burned salvage;
- salvage responds nonlinearly or in a narrow threshold band to subsidy;
- fire risk changes the quantity and timing of salvage opportunities;
- salvage margins vary across development types;
- principal eligibility and agent realization are different quantities;
- the modeled subsidy threshold is sensitive to burned harvesting and
  transport costs.

The pipeline cannot by itself support claims that:

- the modeled threshold is the empirically correct subsidy for all TSA29
  operators;
- future burned wood receives a severity-specific grade transition;
- the principal solves a full bilevel incentive-compatible contract problem;
- the result is unbiased without external validation of prices, costs, fire
  rates, yields, and inventory data.

## Required Limitations

The thesis discussion should explicitly state:

1. **Initial versus future burned grade treatment.** Ingestion applies the
   burned-grade transition to the initial severity-rated stock. Future annual
   fire influx is tracked as total burned volume and does not receive a new
   grade-specific transition.
2. **Principal versus agent burned volume.** The principal uses a static
   ingestion-derived development-type burn share multiplied by current WS3
   standing volume. The agent generates annual burn influx dynamically.
3. **Principal decay semantics.** The principal has no explicit burned-stock
   balance with annual physical decay. It uses the decay parameter in an
   expected-loss term. Explicit burned-inventory decay occurs in the agent/fire
   dynamics.
4. **Rolling-horizon boundary.** Unsalvaged burned area is reset to
   regeneration at the rolling-horizon step boundary rather than carried as a
   burned-volume state into the next WS3/principal/agent step.
5. **Coverage scaling.** Ingested salvageable volume uses an upper-bound area
   ratio, not a true spatial intersection.
6. **Economic assumptions.** The calibrated economic surface is semi-synthetic
   and should be presented with its documented provenance and uncertainty.
7. **Scenario interpretation.** Ensemble and sensitivity results are
   conditional on the stated inputs and should not be presented as empirical
   estimates without additional validation.

## Discussion-Session Slide Order

1. Research question and model pipeline.
2. Decision units: stands, development types, and cohorts.
3. Calibrated baseline.
4. Subsidy-response curve and modeled turn-on range.
5. Fire-risk sensitivity and fire-free control.
6. Principal offers versus agent realizations.
7. Development-type heterogeneity.
8. Limitations and implications for further work.

## Relationship To Future First-Best Analysis

If the thesis requires a formal welfare decomposition, use
`planning/first-best-hidden-information.md` as the extension specification.
That analysis would add:

- a first-best planner benchmark;
- known-type moral-hazard cases;
- hidden-information cases;
- expected welfare and information-loss decomposition;
- synthetic agent types.

Those cases are not currently implemented and must be labeled as future work
until their LPs, tests, and evidence artifacts exist.

## Verification Checklist

Before presenting results:

- [ ] Confirm all runs use the current grade-transition constants.
- [ ] Confirm the subsidy sweep includes a fire-free control.
- [ ] Hold WS3 worker count fixed across comparison runs.
- [ ] Confirm every run has an acceptable solver status.
- [ ] Save source checksums and effective configurations.
- [ ] Separate initial ingestion burned stock from future annual fire influx.
- [ ] Do not claim future-fire grade transitions that are not implemented.
- [ ] Report the burned-stock rolling-boundary reset.
- [ ] Include units for every volume, area, and monetary result.
- [ ] Mark superseded calibration results as historical rather than current.
