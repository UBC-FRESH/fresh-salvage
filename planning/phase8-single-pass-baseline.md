# Phase 8: Single-Pass 100-Year Planning Baseline

Status: active (P8.1 implemented; P8.2-P8.6 open)

Branch: `feature/p8-single-pass-baseline` (cut from
`origin/fix/schedule-group-volume-dedup` at `9b5a0a0`, the PR #32 head;
`main` is merged in after PR #32 merges)

Parent issue: #33 (child issues #34-#39; see the task table below)

This note is the handoff contract for every Phase 8 session. It records the
approved design decisions (D1-D9), the configuration surface, the artifact
contract, the per-task specifications, the test plan, and the known risks.
A later session should be able to implement any open task from this note,
`AGENTS.md`, and the cited code alone.

Code citations use `file:line` at the branch base `9b5a0a0` unless they say
otherwise. Function names are the durable anchors; line numbers drift.

## 1. Purpose

Add a first-class **single-pass** planning mode. It runs the coupled
WS3 -> principal -> agent -> fire pipeline that `rh-run` runs, but solves it
**once at t=0 over the whole run** (`steps x period_length` years, 10 x 10 =
100 years by default) and never re-plans mid-horizon. Single pass is the
baseline that `rh-run` is compared against. `rh-run` re-plans every decade:
it re-solves WS3, principal, and agent from the replayed cohort state at each
step. The comparison answers the thesis question "what does decadal
re-planning change?" (supervisor reference: thesis question Q3).

Interpretation guard (must appear in the docs, P8.6): fire is
**deterministic** in both modes. The LPs and the replay use the expected
annual burn rate `R = 1/MFRI`
(`fire.annual_burn_rate_for_stratum`, `src/fresh_salvage/fire.py:123`), so
RH re-planning never sees information that single pass did not already have.
The RH-vs-single-pass difference therefore measures **re-planning and
structural effects**: the burned-stock reset at step boundaries, growth
handling, the grade-clock reset, re-seeded principal burned paths, and WS3
re-optimization from a replayed state. It does **not** measure the value of
information. A value-of-information study would need stochastic fire
realizations, which are out of Phase 8 scope.

## 2. Current rolling-horizon behaviour (the reference)

`rh.run_rh` (`src/fresh_salvage/rh.py:438`) loops `steps` times over
`rh._run_step` (`rh.py:584`). Each step does the following:

1. Writes the cohort table to `derived/rh_state/step_{k-1}.are` and
   re-parses it (`rh.py:608-609`). Then it injects the table as the WS3
   period-0 inventory (`rh.inject_cohort_table`, `rh.py:400`).
2. Builds and solves a `horizon`-period WS3 problem (default 15 periods of
   10 years) and compiles the schedule (`ws3._compile_schedule`,
   `src/fresh_salvage/ws3.py:911`). PR #32 apportions each
   `(period, dtype_key, age_class, action)` group volume by area share
   (`ws3._apportion_group_volumes`, `ws3.py:961`), so row volumes no
   longer double-count.
3. Sums the **period-1** `cc` volume per cohort
   (`rh._period1_cohort_volumes`, `rh.py:738`). It splits that volume
   uniformly into `period_length` annual ceilings (`rh.annual_ceiling`,
   `rh.py:235`), and passes one scalar ceiling per cohort to the principal
   LP (`rh.py:646-659`).
4. Solves the principal LP over `horizon = period_length` years
   (`principal.build_principal_lp`, `src/fresh_salvage/principal.py:202`).
   Then it solves the agent LP against the offers over the same window
   (`agent.build_agent_lp`, `src/fresh_salvage/agent.py:278`).
5. Replays the decade with `fire.simulate_cohort_years`. It then advances
   the cohort table (`rh.advance_cohort_table`, `rh.py:254`):
   - survivors age by `+period_length`, clamped to the curve age cap
     (`rh.py:360`, `rh.curve_age_caps` `rh.py:206`);
   - harvested, salvaged, and unsalvaged-burned area regenerates in the
     **same development type at age `age_smashing.midpoint` (5)**
     (`rh.py:364-375`);
   - unsalvaged burned volume is dropped at the boundary. This is the
     documented burned-stock reset (`rh.py:48-64`).

Properties of today's LPs that Phase 8 must preserve when the LP horizon is
at most one period:

- Principal: every cohort has one scalar `green_volume_m3` and one scalar
  `burned_volume_m3` (`principal.py:847-857`). The burned path is seeded
  from `burned_volume_m3` at year 0 (`principal._annual_burned_volumes`,
  `principal.py:584`). The grade mix is indexed by the LP year
  (`principal._annual_burned_values`, `principal.py:600-620`, via
  `data.annual_grade_mix`). The objective is **undiscounted**.
- Agent: the variables are fractions of the cohort's **initial** standing
  volume, which equal area fractions. The initial conditions are
  `V[c,0]=1` and `B[c,0]=0` (`agent.py:386-413`). Cash flows are
  discounted by `(1+r)^-(year+1)` (`agent.py:357`). The burned price is
  indexed by the LP year (`agent._annual_burned_price`, `agent.py:472`).

## 3. Decision table (approved)

| ID | Decision | Rationale | Reduction to RH |
| --- | --- | --- | --- |
| D1 | WS3: one solve at t=0 with `horizon` periods (default 15, the same as RH). Implement periods `1..steps` (10). | It is the same WS3 problem as RH step 1, so the WS3 side differs between modes only through re-planning. The 5 extra periods give the end-of-horizon guard that RH also has. | Identical to RH step 1's WS3 problem. |
| D2 | Volume growth: decadal piecewise. For decade `k` (0-based), the cohort per-ha volume comes from its yield curve at age `age0 + L*k`, clamped to the curve age cap (`rh.curve_age_caps`). It is used in the principal (green/burned volumes, offer/loss coefficients, AAC and ceiling rows) and in the agent (margins, fire influx volume). | A 100-year LP that values every cohort at its t=0 volume would misprice long-delayed harvests. RH re-reads volumes at each decade start, so decadal piecewise matches RH's information granularity. | When the LP horizon is at most `L`, only `k=0` exists, so the coefficients must be **bit-identical** to today's. |
| D3 | Regenerated stands are not LP cohorts. If the schedule harvests a regenerated stand (an age on the WS3 regeneration lattice) inside the implemented periods, fail fast with a structured diagnostic. | The LP cohort set is the t=0 inventory. Regenerated area has no t=0 cohort to carry offers or decisions. Evidence: no TSA29 regen harvest occurs in periods 1-10; the first is in period 12 (section 5.4). | Not applicable (RH re-forms cohorts each step). |
| D4 | Burned stock: by default, single pass carries burned inventory continuously over 100 years. Optional flag `burned_stock_reset: bool = False` resets it at decade boundaries, mimicking RH: the agent burned inventory is dropped and the principal burned path is re-seeded. | This isolates the RH burned-stock artifact (`rh.py:48-64`) as its own experimental factor. | `burned_stock_reset` has no effect when horizon is at most `L`. |
| D5 | Grade clock: single pass uses `year mod period_length` as the grade-transition index. | This gives pricing identical to RH, whose window years are `0..L-1`. Grade pricing is then not a confounder. | Identity for `year < L`. RH is unchanged. |
| D6 | Principal burned seed: seeded once at t=0 in single pass. It is re-seeded per decade only under `burned_stock_reset`. | RH re-seeds every step because every step re-parses cohorts (`principal.py:847-849`). | Identity for horizon at most `L`. |
| D7 | NPV comparability: per-decade objective values are re-based to the decade start, which makes them comparable with RH step records. Run totals are re-based to the base year for both modes. | The single-pass agent objective discounts from t=0, while each RH step discounts from its own start. | RH step records are unchanged. New run totals are added. |
| D8 | WS3 fields in decade records: populated for decade 1 only (the rest are null or 0). The summed WS3 objective is **not** comparable across modes. | Single pass has one WS3 solve. | RH records unchanged. |
| D9 | The schedule dedup fix (PR #32, `ws3._apportion_group_volumes`) applies to both modes. | This keeps the ceilings correct in both modes. | Already in RH. |

## 4. Configuration surface (P8.4)

Add to `RHRunConfig` (`src/fresh_salvage/models.py:995`):

```python
planning_mode: Literal["rolling_horizon", "single_pass"] = "rolling_horizon"
burned_stock_reset: bool = False
```

Cross-field validation (`model_validator(mode="after")`):

- `planning_mode == "single_pass"` requires `horizon >= steps` (D1).
- `planning_mode == "single_pass"` requires the lattice preconditions of
  section 5.3: `age_smashing.enabled`, `period_length % age_smashing.width
  == 0`, and `age_smashing.midpoint % width != WS3_HARVEST_RESET_AGE %
  width`. The defaults (10/10/5) satisfy them. The P8.1 function checks
  this again at the call boundary, so the config check is only an
  earlier, friendlier error.
- `burned_stock_reset=True` with `planning_mode == "rolling_horizon"` is
  rejected ("burned_stock_reset only applies to single_pass").

Both fields are flat, so they are valid ensemble axes
(`ensemble.py` validates axes as `RHRunConfig` field names). Old configs
parse unchanged because both fields have defaults.

CLI: add a thin `single-pass-run CONFIG [--json]` command
(`src/fresh_salvage/cli.py`, mirroring `rh-run` at `cli.py:185-212`). It
reads `RHRunConfig`, forces or validates `planning_mode == "single_pass"`
(see the open question in section 9), calls `single_pass.run_single_pass`,
and prints the same summary as `rh-run`.

Dispatch: add one function `rh.run_planning(config, verbose)` (or put it in
the new module) that selects `run_rh` or `run_single_pass` by
`config.planning_mode`. `ensemble._run_scenario` (`ensemble.py:513`) and
`cli.rh_run` call the dispatcher, so `planning_mode` works as an ensemble
axis for paired runs.

## 5. P8.1: schedule to year-indexed ceiling matrix (#34, implemented)

### 5.1 API

All of it lives in `src/fresh_salvage/rh.py`, beside
`_period1_cohort_volumes`, because it shares `cohort_record_id` and
`RHError`:

- `WS3_HARVEST_RESET_AGE = 0`: the age WS3 assigns to clear-cut area
  (section 5.2).
- `schedule_cohort_period_volumes(schedule, action_code, state, *,
  implemented_periods, period_length, age_smashing) -> dict[str,
  tuple[float, ...]]`. It returns the WS3 harvest volume (m3) per t=0
  cohort per implemented period. The keys are every state cohort id in
  state row order, and cohorts with no harvest get all-zero tuples.
- `annual_ceiling_matrix(period_volumes, period_length) ->
  dict[str, tuple[float, ...]]`. For each cohort it returns
  `implemented_periods * L` annual ceilings with
  `ceiling[c, y] = annual_ceiling(vol[c, y // L], L)`, the same float
  operation RH uses today.
- `principal.build_principal_lp(..., cohort_ceilings_m3=...)` and
  `solve_principal` now accept, per cohort, **either** a scalar (broadcast
  to every LP year, the existing RH path) **or** a sequence of exactly
  `horizon` per-year ceilings. Inputs are parsed once by
  `principal._parse_cohort_ceilings` into a `(cohorts, horizon)` matrix,
  which fails fast on a length mismatch or a negative or NaN entry
  (`principal_invalid_cohort_ceilings`).

### 5.2 Age-lattice reasoning (verified against ws3 and the bridge)

Facts, each checked in code and data:

1. **Initial inventory lies on the midpoint lattice.** Bridge ages are
   smashed to `age // width * width + midpoint` (`ws3.midpoint_age`,
   `ws3.py:139-150`; build-time gate `ws3._verify_smashed_bridge`,
   `ws3.py:422-446`). The TSA29 no-LU bridge has 1,608 cohorts, all with
   age `% 10 == 5`.
2. **WS3 ages a stand by `+period_length` per period, with no clamp.** In
   the Model-I tree builder (`ws3/forest.py:1267-1330`,
   `ForestModel._bld_tree_m1`), period 1 starts at the injected period-0
   age (`initialize_areas` copies period 0 to period 1 without growth).
   Each node recurses with age `_age + self.period_length`, and `null`
   keeps `_age = age`. The schedule's `age_class` for a period-`p` action
   on an untouched initial cohort is therefore exactly
   `age0 + L*(p-1)`. WS3 does not clamp to a curve age cap; it only
   enforces `initial_max + horizon*L <= max_age`
   (`ws3._validate_inventory_age_domain`, `ws3.py:749-790`).
3. **Clear-cut resets age to 0 and keeps the development type.** The
   bridge declares `*ACTION cc Y` in `femic_tsa_ws3.act`. ws3 lowercases
   the file and sets `targetage = 0 if tokens[2] == 'y'`
   (`ws3/forest.py:2611`), and `resolve_targetage` then returns 0, because
   the bridge transitions carry no `_AGE` override
   (`ws3/forest.py:1924-1956`). Every `.trn` target maps a managed AU onto
   the same `(stratum, curve)` that its ARE rows already carry (54 of 54
   checked), so the dtype key is preserved. `cc` is operable only for
   `managed` dtypes (the `.act` `*OPERABLE` lines), and
   `ws3.enforce_harvest_age_range` (`ws3.py:187`, applied at
   `ws3.py:622`) limits it to ages 60-300.
4. **Regenerated stands therefore lie on the `0 (mod L)` lattice.** A
   stand cut in period `q` has age `L*(p-q)` at period `p > q`. The
   earliest re-harvest is at age 60, so a period-1 cut can first be
   re-harvested in period 7. Re-harvest is possible in principle inside
   periods 1-10, which is why the D3 guard is needed.

Classification of a positive-area `cc` row with period
`p <= implemented_periods` and age `a`:

```
a0 = a - L*(p-1)
if a0 >= 0 and (a0 - midpoint) % width == 0:   initial lineage -> cohort (dtype, a0)
                                                (unknown -> rh_schedule_unknown_cohort)
elif a % L == WS3_HARVEST_RESET_AGE % L:        regenerated stand
                                                -> rh_schedule_regenerated_harvest
else:                                           -> rh_schedule_off_lattice
```

The two lattices are disjoint whenever `width` divides `L` and
`midpoint % width != WS3_HARVEST_RESET_AGE % width`. Proof sketch: a regen
row has `a0 = L*(1-q) <= 0`, and `a0 % width == 0`, which is never the
midpoint residue. Otherwise the function fails fast with
`rh_schedule_lattice_ambiguous` (for example, age smashing disabled,
`period_length=1`, or `midpoint=0`). It also fails fast with
`rh_schedule_state_off_lattice` when a state age is not on the midpoint
lattice, `rh_schedule_dtype_malformed` when a dtype key does not have
5 elements, and `rh_invalid_period_length` /
`rh_invalid_implemented_periods` when the corresponding argument is not
positive.

Rows with `period > implemented_periods` are ignored, so regen harvests in
periods 11-15 are legal. Zero-area rows and non-`cc` actions are ignored,
the same as `_period1_cohort_volumes`. Rows of one dedup group are summed,
and they add up to the group total because of PR #32.

### 5.3 Why RH keeps `_period1_cohort_volumes`

RH stays on `_period1_cohort_volumes` unchanged. Routing RH through the new
function would add lattice preconditions that legal RH configs do not meet:
`rh.py:12-14` documents `period_length=1` as a supported degenerate mode, and
`age_smashing.enabled=False` is accepted by `RHRunConfig`. RH would then
fail where it runs today. RH's `ceilings` list construction (`rh.py:646-651`)
is also unchanged. The principal receives the same scalar floats, and the
new parser broadcasts them to the same row bounds in the same row order, so
the RH LP is bit-for-bit identical (`tests/test_principal.py` equivalence
test). A cross-check test asserts that the new function's period-1 column
equals `_period1_cohort_volumes` on a lattice-valid fixture.

### 5.4 Evidence (local, not tracked)

On the step-01 schedule of the local 100-year RH run
`outputs/rh_100yr_monotonicity_fix` (15 periods, initial state = bridge
ARE), the positive-area `cc` rows have these `age % 10` residues per
period:

- periods 1-11: only residue 5 (initial lineage);
- period 12: 61 rows with residue 0 (regenerated) and 21 with residue 5;
- periods 13-15: mostly residue 0.

All 557 implemented-period rows (periods 1-10) map onto a t=0 cohort
through `age0 = a - 10*(p-1)`, with none missing. That schedule predates
PR #32, so its volumes are inflated, but the ages and lattice are not
affected.

## 6. Open task specifications

### P8.2: decadal growth-aware volume coefficients (#35)

- Add a pure helper that returns, per cohort, a tuple of per-ha volumes for
  decades `k = 0..ceil(horizon/L)-1` at age `min(age0 + L*k, cap)`. It
  reuses `principal._curve_volume_m3_per_ha` / `agent._curve_volume_m3_per_ha`
  and `rh.curve_age_caps`.
- Carry it on `PrincipalCohort` / `AgentCohort` as an optional
  `decadal_volume_scale: tuple[float, ...] | None` (ratio
  `v_k / v_0`, or absolute volumes). `None` means today's behaviour and is
  the RH path.
- Principal: green and burned volumes in year `y` scale by the decade-`k(y)`
  factor in `annual_offer`, `annual_burn_loss`, the AAC row, the ceiling
  row coefficient, and the burned-cap row. The fire/decay path in
  `_annual_burned_volumes` must track **live area** and convert to volume
  at the decade's per-ha volume, so that burn influx in decade `k` is
  `R * live_area * v_k`.
- Agent: the H/S/V/B variables stay as area fractions of the t=0 cohort.
  Margins in year `y` use `area * v_k(y)`. Burned inventory carried across
  a decade boundary must be re-expressed in decade-`k+1` units. To do that,
  multiply the `B[t-1]` coefficient in the first-year burned and salvage
  rows of decade `k+1` by `v_k / v_{k+1}`, which keeps the model linear and
  conserves burned volume. When horizon is at most `L`, no boundary exists,
  so this reduces exactly. If `v_{k+1} == 0 < v_k`, the step fails fast
  (open risk R5).
- Acceptance: an equivalence test shows the LP built for horizon at most
  `L` is identical (row bounds, matrix, costs, offset) with and without the
  growth argument, plus unit tests of the helper (clamping, interpolation)
  and a 2-decade analytic test.

### P8.3: grade clock and burned-stock semantics (#36)

- Add a `grade_clock_period: int | None` argument (None = LP year index,
  the RH default) to the principal coefficient builders and to
  `agent._annual_burned_price` / `_annual_burned_grade_mix`. With
  `period_length` it uses `year % period_length` (D5).
- Add a `burned_stock_reset: bool` argument (default False):
  - agent: at each decade boundary, set the `B[t-1]` coefficient in the
    first-year burned and salvage rows to 0, which drops the inventory and
    mimics `rh.py:48-64`;
  - principal: re-seed `burned = burned_volume_m3 * v_k / v_0` (D6) and
    restart the decay exponent in `annual_burn_loss` (`decay_rate**year`
    becomes `decay_rate**(year % L)`) at each decade start.
- Without the reset (the default), the single-pass burned path runs
  continuously from the t=0 seed.
- Acceptance: with both options at their defaults, or with horizon at most
  `L`, the LPs are identical (equivalence tests). Analytic 2-decade tests
  cover carry versus reset.

### P8.4: `single_pass` orchestrator (#37)

New module `src/fresh_salvage/single_pass.py` with
`run_single_pass(config, verbose) -> RHResult`:

1. Do the same boundary parse as `run_rh` (`rh.py:479-497`) and write
   `derived/rh_state/step_00.are`.
2. Inject the state and run one WS3 build+solve with `horizon` periods. Save
   the schedule as `<run>-schedule.csv`.
3. Call `schedule_cohort_period_volumes(..., implemented_periods=steps)` and
   then `annual_ceiling_matrix`. Order the ceilings by the principal cohort
   order.
4. Run the principal LP with `horizon = steps * L`, per-year ceilings, the
   growth scale, the grade clock `L`, and `burned_stock_reset`. Write
   `<run>-offers.parquet`.
5. Run the agent LP over `steps * L` years against the offers. Write
   `<run>-decisions.parquet`.
6. Do the 100-year **lineage replay** (below), with one state snapshot
   `derived/rh_state/step_{k:02d}.are` per decade boundary.
7. Emit `steps` decade records (`RHStepRecord`), the manifest, the final
   state CSV, and the final age distribution.

Lineage replay rules:

- Each t=0 cohort is replayed with `fire.simulate_cohort_years` over
  `steps*L` years using its agent H/S fractions. The fractions are of t=0
  area; under `burned_stock_reset`, burned inventory is dropped at
  boundaries exactly as in the agent LP, so the replay and the LP agree.
- At each decade boundary `k`, the area that left the live pool during
  decade `k` (`area * (V_start_k - V_end_k)`: green harvest plus burn
  influx; salvage only reclassifies burned area) regenerates as a
  regen sub-cohort in the same development type at
  `age_smashing.midpoint`, which mirrors `rh.py:364-375`. Regen
  sub-cohorts age `+L` per decade (clamped to the cap), are never
  harvested (D3), and remain exposed to fire. Burned regen area
  regenerates again at the next boundary.
- Surviving live t=0 area ages to `min(age0 + L*k, cap)`.
- Area is conserved to 1e-6 relative at every boundary
  (`rh._require_area_conserved`).
- `area_burned_ha` per decade is the sum of `area * burn_influx` over t=0
  cohorts and regen sub-cohorts.

Decade records: annual green and burned harvest arrays per decade (from the
agent `per_year_volumes` slices), `area_burned_ha` from the replay, and
per-decade objectives. The agent objective is decomposed per decade by
summing `cost * x` over the decade's columns and multiplying by
`(1+r)^(L*k)` to re-base it to the decade start (D7). The principal
objective is decomposed by summing `annual_offer*offer + loss*cum_offer
- loss` per decade; it is undiscounted, so no re-basing is needed. WS3
fields are filled for decade 1 only (D8). This needs
`RHStepRecord.ws3_status: str | None` and float fields defaulting to 0. Run
totals added to `RHManifest`, optional and also filled by RH:
`total_principal_objective`, and `total_agent_objective_base_year`, which
for RH is `sum_k agent_k * (1+r)^-(L*(k-1))`.

Manifest and result: the same `RHManifest`/`RHResult`, plus
`planning_mode: str = "rolling_horizon"` (defaulted, so old manifests parse).
The status is derived from the single WS3 solve status. For single pass,
`horizon` keeps its WS3 meaning.

Oracle test: single pass with `steps=1` (and so `horizon >= 1`) must
reproduce the RH step-1 record: the same ceilings, the same principal and
agent objectives, the same annual arrays, the same `area_burned_ha`, and
the same next state. The full WS3 path needs the bridge (skip in CI), so
the test also has a ws3-free variant that monkeypatches the WS3 stage with
a synthetic schedule.

### P8.5: tractability probe and paired evidence run (#38)

Measure LP build and solve time for principal and agent at 30, 50, and 100
years on full TSA29 (1,608 cohorts). If per-row Python `addRow` loops
dominate, vectorize LP construction with `addRows` (CSR) while keeping the
row order (equivalence tests stay green). Then make one paired evidence run,
RH versus single pass, at a reference subsidy (calibrated default and a
near-flip value), with and without `burned_stock_reset`. Record it in a
planning note under `planning/`.

### P8.6: docs and closeout (#39)

This covers `docs/model_semantics.rst` (a new "Single-pass baseline"
section plus affected RH sections), `docs/cli.rst` (`single-pass-run`),
`docs/architecture.rst`, `docs/ensembles.rst` (the `planning_mode` axis),
`examples/single_pass_tsa29.yaml`, `CHANGE_LOG.md`, the ROADMAP closeout,
and the PR description.

## 7. Artifact contract (single pass)

Every artifact goes under `output_root`, with `<run> = safe_slug(run_id)`:

| Artifact | Path | Notes |
| --- | --- | --- |
| Decade records | `data/<run>-steps.jsonl` | `steps` lines, the same schema as RH |
| Manifest | `manifests/<run>-rh-manifest.json` | `planning_mode="single_pass"` |
| Final state | `data/<run>-final-state.csv` | the same columns as RH |
| State snapshots | `derived/rh_state/step_{k:02d}.are` | `k = 0..steps` |
| Schedule | `data/<run>-schedule.csv` | one `horizon`-period WS3 schedule |
| Offers | `data/<run>-offers.parquet` | `steps*L` years |
| Decisions | `data/<run>-decisions.parquet` | `steps*L` years |

`RHResult` fields keep their meaning: `decadal_*` lists have `steps`
entries, and `final_age_distribution_ha` is built from the replayed final
state.

## 8. Test plan

- P8.1 (done): synthetic schedules covering both lattices, multi-period
  mapping, the regen guard, an unknown cohort, an off-lattice row, lattice
  ambiguity, an off-lattice state, summed dedup rows, ignored rows
  (beyond-horizon, zero-area, non-`cc`), the annual-matrix split and
  conservation, per-year principal ceiling rows, ceiling-parser failures,
  scalar-versus-broadcast LP identity, and agreement with
  `_period1_cohort_volumes` for period 1.
- P8.2/P8.3: LP equivalence when horizon is at most `L`, plus analytic
  2-decade tests (growth, carry versus reset, grade clock).
- P8.4: unit tests for the lineage replay (area conservation, regen
  exposure, record count), config validation, the CLI, ensemble dispatch,
  the oracle test, and old-manifest parsing.
- RH regression: every existing test passes unchanged.

## 9. Risks and open questions

- **R1: principal `cum_offer` density.** Today's rows
  `cum_offer[c,y] = sum_{t<=y} offer[c,t]` (`principal.py:288-295`) hold
  `C*T*(T+1)/2` nonzeros, about 8.1M at `C=1,608`, `T=100`. A recursive form
  (`cum[c,y] - cum[c,y-1] - offer[c,y] = 0`, 3 nonzeros per row, about
  0.5M) is mathematically equivalent, but it changes the RH LP bits. Use it
  only when horizon is greater than `L`, or prove result equality before
  switching RH. This belongs to P8.5.
- **R2: Python row loops.** At `T=100` the principal adds about 160k
  `cum_offer` rows plus about 160k ceiling rows, and the agent adds about
  480k rows, all through `addRow` one at a time. P8.5 must measure this.
- **R3: growth clamp versus WS3 yield.** D2 clamps to the curve age cap,
  while WS3 values period `p` at the unclamped age `age0 + L*(p-1)` from
  the `.yld` curves. For ages in `(cap, max tabulated age]`, the principal
  green coefficient and the WS3 ceiling therefore use slightly different
  per-ha volumes. This is harmless for feasibility, but it is a known
  asymmetry.
- **R4: zero WS3 ceiling blocks salvage too.** A single offer variable
  scales both green and burned volume. A cohort that WS3 does not schedule
  in a decade gets a 0 ceiling and so cannot be offered at all, salvage
  included (`principal.py:330-341`). This holds in RH today, and single pass
  inherits it per decade. It is noted here because it shapes where salvage
  can happen in both modes.
- **R5: degenerate growth ratio.** `v_{k+1} = 0 < v_k` breaks the burned
  re-expression in P8.2. It does not occur on monotone curves; the fix is to
  fail fast.
- **R6: regen age convention.** RH regenerates at age 5 (the midpoint
  lattice), but WS3 itself places regenerated area at age 0 (and `L` one
  period later). Single pass mirrors RH in the replay. The WS3 t=0 solve
  uses its own convention, which only matters after period 10.
- **R7: principal versus agent burned seed.** The principal starts each
  cohort with burned stock `burn_share * standing` (`principal.py:849`),
  while the agent starts with `B[c,0]=0` (`agent.py:386-392`). This
  pre-existing asymmetry is unchanged by Phase 8.
- **Q1:** Should `single-pass-run` override `planning_mode` to
  `single_pass` regardless of config, or fail on `rolling_horizon`?
  Recommendation: override. The CLI name is the intent.
