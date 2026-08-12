# Economics Calibration (Phase 6 recalibration)

Status: implemented (data.py constants; config-visible via `Economics`)

Date: 2026-08-12

## Motivation

The Phase 6 validation (`phase6-validation-report.md`, pre-recalibration)
found that the subsidy instrument had NO behavioral effect: with the
predecessor's placeholder economics the agent salvage margin was
`burned_price − 35 − 5 + subsidy ≈ +90 + subsidy` $/m3 — positive at any
non-negative subsidy, so salvage was maximal at subsidy 0 and the 20-level
sweep was flat. Economically that says a fire-killed cubic metre is worth
~+$93/m3 standing in the bush unsubsidized, which is not credible for the BC
Interior.

User directive for this recalibration: the UNSUBSIDIZED salvage marginal
benefit must be negative for at least a substantial fraction of the burned
volume. Parameters may be semi-synthetic, but each must carry a coherent,
documented rationale, and every parameter must stay config-visible
(`ScenarioRunConfig.economics` for ingestion; `PrincipalRunConfig.economics`
/ `AgentRunConfig.economics` sections; flat `RHRunConfig` fields so the
ensemble driver can vary any of them as a named axis).

## Parameter table

| Parameter | Old | New | Rationale | Source / provenance |
| --- | --- | --- | --- | --- |
| GREEN_PRICES SPF saw/peel/pulp | 200 / 180 / 150 | 127 / 146 / 55 | SPF sawlog at the Q4-2023 BC Interior log market level; peeler = saw x 1.15 (assumption); pulp at market pulpwood (54.89 -> 55) | BC Interior Log Market Report Q4-2023 anchors (market report); peeler premium and pulp rounding are ASSUMPTIONS |
| GREEN_PRICES Df-Larch saw/peel/pulp | 220 / 200 / 100 | 103 / 118 / 55 | Same anchors, Df-Larch sawlog benchmark | BC Interior Log Market Report Q4-2023 (market report); peeler = saw x 1.15 ASSUMPTION; pulp ASSUMPTION (same pulpwood market) |
| GREEN_PRICES Hem-Bal saw/peel/pulp | 210 / 190 / 110 | 120 / 138 / 55 | Same anchors, Hem-Bal sawlog benchmark | BC Interior Log Market Report Q4-2023 (market report); peeler/pulp ASSUMPTIONS as above |
| GREEN_PRICES Cedar saw/peel/pulp | 250 / 230 / 120 | 144 / 166 / 55 | Same anchors, cedar sawlog benchmark | BC Interior Log Market Report Q4-2023 (market report); peeler/pulp ASSUMPTIONS as above |
| GREEN_PRICES Other | 90 | 90 | Unchanged; mixed-secondary basket price | Predecessor value retained (ASSUMPTION, consistent with a pulp-plus basket) |
| BURNED_PRICE_DISCOUNT | 0.65 | 0.65 | Unchanged; fire-damaged timber realizes ~65% of green value | BC fire-damaged timber pricing adjustments of −$34–36/m3 (EWB/fire appraisal practice) plus observed sawlog->pulpwood downgrade of burned lots |
| GREEN_HARVEST_COST | 30 | 45 | Tree-to-truck logging cost $30–40/m3 plus road, admin, and silviculture allocation | Interior logging cost (ILCR-style) ranges; road/admin/silv allocation is a DERIVED add-on |
| BURNED_HARVEST_COST | 35 | 61 | +35% premium on green logging: salvage shows +15–46% unit-cost increases and −20–40% productivity hits, plus snag-safety overhead | Loeffler & Anderson (MPB salvage, +15–46%), FERIC productivity studies (−20–40%), BC snag-safety practice; +35% is a DERIVED mid-range premium |
| TRANSPORT_COST_PER_M3 (green) | — (not modeled) | 30 | NEW. Haul cost for a 100–200 km one-way haul in a 4.93 Mha TSA | DERIVED from TimberTracks-style haul rates and IAM cycle-time conventions ($24–40/m3 for 100–200 km); midpoint chosen |
| BURNED_TRANSPORT_COST_PER_M3 | — (not modeled) | 41 | NEW. +35% over green haul: burn blocks are scattered and remote, lengthening cycles | DERIVED: same premium structure as burned harvest cost |
| GREEN_STUMPAGE_RATE | 30 | 15 | Appraised stumpage for the South Central (Williams Lake) interior market, mid-range | BC South Central appraised stumpage mid-range (DERIVED point from the published range) |
| BURNED_STUMPAGE_RATE | 5 | 0.25 | Fire-damaged timber stumpage floor | BC tabular stumpage rate for fire-damaged timber, Table 6-4a (floor rate) |
| SUBSIDY_RATE_PER_M3 | 3.0 | 3.0 | Unchanged default policy lever | Predecessor default retained |
| decay_rate (burned retention) | 0.85 | 0.85 | Unchanged | See the decay-semantics note below |
| discount_rate | 0.03 | 0.03 | Unchanged | Predecessor default retained |

"DERIVED" = computed from the cited evidence by a stated rule; "ASSUMPTION"
= no direct measurement, chosen for coherence with the cited anchors.

## Expected margins (SPF sawlog price basis)

```
green   = 127.00 − 45 − 30 − 15    = +37.00 $/m3
salvage =  82.55 − 61 − 41 − 0.25  = −19.70 $/m3 + subsidy
```

(burned SPF sawlog price = 127 x 0.65 = 82.55 $/m3). These basis margins are
pinned by `test_calibrated_margin_decomposition_spf_basis` in
`tests/test_agent.py`, and the no-salvage-at-subsidy-0 / salvage-at-25
behavior is pinned by
`test_unsubsidized_salvage_is_not_economic_at_calibrated_costs` and
`test_subsidy_above_the_margin_gap_flips_salvage_on`.

### Model basis vs sawlog basis (important)

The agent LP does not price cohorts at the sawlog price: each cohort carries
its development type's VOLUME-WEIGHTED average price over the grade columns,
and burned volume is grade-degraded by `BURNED_GRADE_TRANSITION` (~62% of
burned volume lands in pulpwood at 55 x 0.65 = 35.75 $/m3). On the real
stands table the calibrated constants give, per development type:

- green margins: +5.9 to +41.1 $/m3 (all positive; SPF DTs ≈ +29 to +31);
- salvage margins at subsidy 0: −45.1 to −48.1 $/m3 on every DT that carries
  burned volume (SPF ≈ −47.9 to −48.0) — the unsubsidized salvage benefit is
  negative for 100% of the burned volume, comfortably exceeding the
  directive's "substantial fraction" requirement;
- subsidy breakeven (salvage margin = 0): ≈ 45.1–46.5 $/m3 for Other/Cedar
  DTs, ≈ 47.5–48.1 $/m3 for SPF/Hem-Bal DTs.

The sawlog-basis −19.7 $/m3 is the single-grade decomposition requested for
the pinned test; the model's behavioral flip is set by the grade-mixed
breakeven near 45–48 $/m3, because fire degradation reprices most burned
volume at pulp.

## FESBC benchmark note

FESBC (Forest Enhancement Society of BC) salvage funding practice puts the
empirical salvage-support benchmark at roughly $14–15/m3. Under this cost
calibration a FESBC-level subsidy alone does NOT close the model's salvage
margin gap (≈ $45–48/m3 grade-mixed; $19.7/m3 on the sawlog basis): the
$14–15/m3 benchmark sits just below the sawlog-basis gap and well below the
pulp-degraded gap. That is a statement about the cost stack (harvest 61 +
haul 41 on burned wood), not about the benchmark's adequacy for the partial,
sawlog-rich salvage programs FESBC typically funds.

## Decay-semantics note (modeling choice)

The salvage literature applies ~0.85/yr decay to the VALUE of fire-killed
timber (stain/checking/downgrade progression), not to its physical volume.
This model keeps the user-specified VOLUME-decay semantics: unsalvaged
burned volume is retained at 0.85/yr in the burned inventory
(`B[t] = (B[t-1] + influx − salvage) x 0.85`). This is a deliberate modeling
choice (the burned inventory is the salvageable pool, so volume retention is
the operationally relevant state variable here), documented so a future
value-decay variant is an explicit parameter change, not a silent
assumption.
