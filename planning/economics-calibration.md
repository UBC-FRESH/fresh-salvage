# Economics Calibration (Phase 6 recalibration)

Status: implemented (data.py constants; config-visible via `Economics`);
adjusted to the prompt-salvage regime on 2026-08-12 (see the adjustment
note below)

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

## Adjustment note (2026-08-12, prompt-salvage regime)

The first recalibration overshot: its grade-mixed salvage margins landed at
−45 to −48 $/m3 (behavioral flip ≈ 48 $/m3), which the user judged
DISTRACTINGLY LARGE — the subsidy cost of a flip is then obviously several
times the benefit NPV, and the minimum-subsidy question is no longer
genuinely open. The cause was double-counted time decay:
`BURNED_GRADE_TRANSITION` sent 55% of burned saw volume straight to pulp at
year 0. That pulp collapse is a GREY-STAGE (5–10 yr post-fire) outcome; the
model already removes grey-stage volume through the 0.85/yr burned-inventory
decay, so baking it into the initial grade mix charged the decay twice.

The adjustment targets the FRESH/PROMPT-SALVAGE regime (year 1–3 after the
kill), which is the regime a subsidy program actually operates in:

- **Grade mix**: year-1 sawlog retention ~0.80 for every species group
  (Plank 1984; Loeffler & Anderson 2018 red-stage evidence: sawlog share
  85% -> 73% over years 1–2, lumber value −10%; checking loss is already
  priced by `BURNED_PRICE_DISCOUNT` = 0.65). Sawlog ->
  {Saw 0.80, Peel 0.10, Pulp 0.10}; Peeler -> {Peel 0.55, Saw 0.35,
  Pulp 0.10}; Pulpwood stays Pulpwood 1.0. The grey-stage collapse remains
  in the model — via the decay term, where it belongs.
- **Burned cost premium**: +35% -> +25% over green, the mild,
  recently-killed case consistent with prompt year-1–3 salvage
  (45 x 1.25 = 56.25 -> 56; 30 x 1.25 = 37.5 -> 38).
- Green prices/costs/stumpage are unchanged.

User requirement for the adjusted calibration: the unsubsidized burned-wood
marginal benefit must be neither trivially small (~0) nor distractingly
large — a moderate negative band, so the minimum-subsidy question stays
open. Post-adjustment the volume-weighted development-type margins at
subsidy 0 span ≈ −10 (Cedar) to −36 $/m3 (Other) with SPF ≈ −19, and the
subsidy response becomes a RAMP across ~10–25 $/m3 (see the margins section
and `phase6-validation-report.md`).

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
| BURNED_HARVEST_COST | 35 | 56 | +25% premium on green logging for the mild, recently-killed (prompt year-1–3 salvage) case: salvage shows +15–46% unit-cost increases and −20–40% productivity hits, plus snag-safety overhead; +25% sits at the mild end of that evidence | Loeffler & Anderson (MPB salvage, +15–46%), FERIC productivity studies (−20–40%), BC snag-safety practice; +25% is a DERIVED mild-case premium (adjusted from +35%/61 after the grey-stage double-count fix) |
| TRANSPORT_COST_PER_M3 (green) | — (not modeled) | 30 | NEW. Haul cost for a 100–200 km one-way haul in a 4.93 Mha TSA | DERIVED from TimberTracks-style haul rates and IAM cycle-time conventions ($24–40/m3 for 100–200 km); midpoint chosen |
| BURNED_TRANSPORT_COST_PER_M3 | — (not modeled) | 38 | NEW. +25% over green haul (same mild, recently-killed premium as burned harvest; 30 x 1.25 = 37.5 -> 38): burn blocks are scattered and remote, lengthening cycles | DERIVED: same premium structure as burned harvest cost (adjusted from +35%/41) |
| BURNED_GRADE_TRANSITION | Saw 0.40/0.05/0.55, Peel 0.0/0.20/0.80, Pulp 1.0 | Saw 0.80/0.10/0.10, Peel 0.35/0.55/0.10, Pulp 1.0 | Prompt-salvage (year 1–3) grade retention: ~80% of burned saw volume holds sawlog grade in year 1; checking loss is already in the 0.65 price discount; the grey-stage (5–10 yr) collapse to pulp is handled by the 0.85/yr decay, not the initial mix (the old mix double-counted it) | Plank (1984) and Loeffler & Anderson (2018) red-stage evidence: sawlog share 85% -> 73% over years 1–2, lumber value −10%; DERIVED year-1 retention 0.80 |
| GREEN_STUMPAGE_RATE | 30 | 15 | Appraised stumpage for the South Central (Williams Lake) interior market, mid-range | BC South Central appraised stumpage mid-range (DERIVED point from the published range) |
| BURNED_STUMPAGE_RATE | 5 | 0.25 | Fire-damaged timber stumpage floor | BC tabular stumpage rate for fire-damaged timber, Table 6-4a (floor rate) |
| SUBSIDY_RATE_PER_M3 | 3.0 | 3.0 | Unchanged default policy lever | Predecessor default retained |
| decay_rate (burned retention) | 0.85 | 0.85 | Unchanged | See the decay-semantics note below |
| discount_rate | 0.03 | 0.03 | Unchanged | Predecessor default retained |

"DERIVED" = computed from the cited evidence by a stated rule; "ASSUMPTION"
= no direct measurement, chosen for coherence with the cited anchors.

## Expected margins (SPF price bases)

```
green   = 127.00 − 45 − 30 − 15       = +37.00 $/m3   (sawlog basis)
salvage =  82.55 − 56 − 38 − 0.25     = −11.70 $/m3 + subsidy   (sawlog basis)
salvage =  79.105 − 56 − 38 − 0.25    = −15.15 $/m3 + subsidy   (transition mix)
```

The sawlog basis prices a burned sawlog as a sawlog (127 x 0.65 = 82.55
$/m3); the TRANSITION MIX prices a green sawlog at its expected
post-burn grade distribution (0.65 x (0.80 x 127 + 0.10 x 146 + 0.10 x 55)
= 79.105 $/m3) — the headline ≈ −15 $/m3 prompt-salvage margin. These
decompositions are pinned by
`test_calibrated_margin_decomposition_spf_basis` in `tests/test_agent.py`,
and the no-salvage-at-subsidy-0 / salvage-at-25 behavior is pinned by
`test_unsubsidized_salvage_is_not_economic_at_calibrated_costs` and
`test_subsidy_above_the_margin_gap_flips_salvage_on`.

### Model basis vs sawlog basis (important)

The agent LP does not price cohorts at the sawlog price: each cohort carries
its development type's VOLUME-WEIGHTED average price over the grade columns,
and burned volume is grade-degraded by `BURNED_GRADE_TRANSITION` (under the
prompt-salvage mix ~68% of SPF burned volume holds sawlog grade, ~13% lands
in peeler, ~19% in pulpwood). On the real stands table the calibrated
constants give, per development type (species-group level; DTs within a
group differ only by their volume weights):

- green margins: all positive (SPF ≈ +31.3, Cedar ≈ +46.9, Hem-Bal ≈ +25.0,
  Df-Larch ≈ +9.4, Other ≈ 0 $/m3);
- salvage margins at subsidy 0: Cedar ≈ −9.9, SPF ≈ −19.1, Hem-Bal ≈ −22.9,
  Df-Larch ≈ −32.0, Other ≈ −35.8 $/m3 — negative for 100% of the burned
  volume (the directive's "substantial fraction" requirement) but in a
  MODERATE band, neither trivially small nor distractingly large;
- subsidy breakeven (salvage margin = 0): ≈ 10 (Cedar), ≈ 19 (SPF),
  ≈ 23 (Hem-Bal), ≈ 32 (Df-Larch), ≈ 36 $/m3 (Other) — the behavioral
  response is a RAMP across ~10–25 $/m3 as successive species groups cross
  their breakevens, not a single step.

## FESBC benchmark note

FESBC (Forest Enhancement Society of BC) salvage funding practice puts the
empirical salvage-support benchmark at roughly $14–15/m3. Post-adjustment
the benchmark sits INSIDE the model's ramp: above the Cedar breakeven
(~10 $/m3), below the SPF breakeven (~19 $/m3). A FESBC-level subsidy
therefore activates the sawlog-rich, low-cost end of the salvage program
without flooding it — the model flip lands in the low-to-mid teens, the
range where the minimum-subsidy question is thesis-relevant.

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

Division of labour between the two burned-degradation mechanisms (fixed by
the 2026-08-12 adjustment): the INITIAL GRADE MIX
(`BURNED_GRADE_TRANSITION`) prices the fresh/prompt-salvage regime — what a
year-1–3 salvage program actually recovers — while the grey-stage (5–10 yr)
collapse of unsalvaged wood is represented by the 0.85/yr decay shrinking
the salvageable pool. Putting the grey-stage pulp collapse in the initial
mix as well double-counts the time decay and inflates the apparent subsidy
need.
