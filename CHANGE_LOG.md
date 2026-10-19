# Change Log

This is the project narrative for `fresh-salvage`. Newest entries first
(reverse-chronological). Commit references are short hashes on `main`.

## Unreleased

- Phase 9 closed **NO-GO** on the WS4 backend swap (2026-10-19; decision
  gate P9.4, #44; parent issue #40): the project keeps WS3 as its results
  pipeline. Rationale: the aspatial WS4 step is ~16x the WS3 per-decade
  step and ~30x the single-pass pipeline (~30 min vs 62 s/193 s) and
  heuristic where WS3 is exact; WS4's spatial differentiator (patch
  targets, roads, mills) is blocked on non-existent topology/roads/mill
  data; and the project's results pipeline (thesis numbers, Phase 8
  evidence) stays on WS3. P9.3 (#43) closed as not planned; P9.5 (#45)
  closed as deferred (future lab work if WS4 adoption is revisited);
  P9.1/P9.2 evidence retained
  (`planning/phase9-ws4-scale-test-results.md`). What could reopen it:
  spatial datasets built for TSA29, and parallel-scaling evidence bringing
  WS4 within budget; WS4 remains a lab-level candidate (clone and
  clean-room evidence retained). Docs-only change: a decision section in
  `planning/phase9-ws4-backend-spike.md`, the assessment and scale-test
  status lines updated, and `ROADMAP.md` marks the phase closed.
- Phase 9 aspatial scale test complete on the full TSA29 estate (P9.1/P9.2
  evidence; issues #41 and #42 closed, checkpoint held on #40). Clean-room
  spike (untracked workspace; WS4 `0.1.0` installed editable from a copy of
  the reviewed `b380c128` checkout — the clone stayed clean and no repo or
  instance was modified). Sanitized record:
  `planning/phase9-ws4-scale-test-results.md`. No code changes.
  - Environment: Python 3.12 venv, `pip install -e .[geo,opt]` 49 s, no
    conflicts; deps resolve to pandas 3.0.6 / numpy 2.5.4 (WS4 pins lower
    bounds only — versions unverified against WS4 CI; pin before any
    adapter work). The packaged synthetic example passes the full API
    chain build → simulate → optimize.
  - Ingest: legacy matrix + a portions-based fragments file — one fragment
    per (BLOCK, TRACK) portion, **managed portion first** within each block
    as `Optimizer.candidates()` requires (verified 421,945/421,945 checks;
    the source's unmanaged-first order would make 224,086 blocks /
    1,218,311 managed ha unharvestable). 646,031 fragment-portions over
    421,945 blocks; `ws4 validate` clean; 12 tables; ingest 2.0 s /
    validate 1.4 s. Crosswalk exact at portion level (managed 1,218,310.5
    ha; portion type ⇔ IFM on all 646,031 rows; the 2903xxx band fully
    covered); the one-fragment-per-block alternative (+110% managed-area
    overstatement) rejected.
  - Simulate: full-estate 100-y empty replay 8.28 s / 823 MB (30-y 3.25 s);
    `feature.Area.managed` = 1,218,310.51 ha exactly.
  - Candidates: **15.4 M chains / 212,011 blocks** (2–113 per block) in
    17.7 s / 2.29 GB; the ~27.7 M estimate did not hold (WS4 caps chains
    per block); enumeration is not a bottleneck.
  - Optimizer (SA, seed 42, 350k iterations, single core, `lns_weight=0`):
    prime run (floors + ceiling) 1,809 s — 9/10 WS3 per-decade floors met
    (p1 at 85.3%, p10 100.0%); mirror run (+ even-flow band) 1,817 s —
    9/10 floors, p1 at 96.8%, band met every period, terminal liquidation
    99.8% of the AAC-derived ceiling; residual 0.18 Mm³ closing at
    ~40 m³/iteration ⇒ ~355–400k iterations (~30–35 min single-core) to
    zero; peak RSS 3.56 GB. Two runs bit-identical over 148,018 common
    iterations (deterministic per seed).
  - Verdict: the WS3 volume level is **reachable** at full estate aspatial
    scale; the scale kill-criterion (hours per step / memory blowup) and
    the level-reachability kill-criterion are **not triggered** — ~181 s
    per decade-step as run vs the WS3 rolling step's ~11 s and the ~62 s
    single-pass (slower, not hours; islands untested). Portability gotchas
    for any driver: `time_limit` never enforced; outputs flush only at the
    end (external kills lose the incumbent — add incremental
    checkpointing); the default `lns_weight=0.2` is prohibitive at this
    scale.
  - Docs: new `planning/phase9-ws4-scale-test-results.md`; the assessment
    gains the measured section and the two cleared kill criteria; the
    spike note marks P9.1/P9.2 recorded; `ROADMAP.md` ticks P9.1/P9.2 and
    records the next decision point (P9.3 and/or P9.5); issues #41/#42
    closed with evidence comments and a checkpoint comment on #40.
- Phase 8 review round (fix-first findings applied; parent issue #33):
  - M1: `cli.rh_run` now calls `rh.run_planning(config)` instead of
    `rh.run_rh`, so an `rh-run` config with `planning_mode: single_pass`
    runs the single-pass path (no forced mode; `single-pass-run` keeps its
    forced re-validation). The CLI summary follows the executed mode. New
    CLI tests cover both modes.
  - M2: the single-pass lineage replay no longer re-simulates salvage. The
    agent LP owns salvage feasibility (it re-expresses carried burned
    inventory across decade boundaries by `v_k / v_{k+1}`), and every
    replayed output is salvage-independent, so feeding the raw LP salvage
    fractions against the replay's unconverted t=0-fraction carry could
    reject a feasible schedule (`single_pass_fire_simulation_failed`).
    `area_salvaged_ha` is still summed from the raw LP schedule; new
    declining-`v` regression tests (hand-built and LP-driven) fail on the
    pre-fix module and pass now.
  - M3: CI checks out full history (`fetch-depth: 0`), so the cross-module
    growth-equivalence tests against `53ba8d1` run in CI instead of
    skipping on the shallow checkout.
  - Folded minors: truthful `run_single_pass` exception contract (m2);
    unused `period_length` parameter dropped from `rh._fraction_schedules`
    (m3); `single_pass_invalid_shape` /
    `single_pass_invalid_regeneration_age` added to the documented failure
    codes (m6); `horizon < period_length` reduction tests, a gated
    real-data `steps=2` replay smoke test, and the M2 regressions (m8); the
    tautological status branch in `run_single_pass` simplified to a
    commented `status = ws3_status` (m9). Deferred to backlog: m1 (zero-t0
    burned-cap series), m4 (unconsumed surface), m5, m7.
  - Docs aligned: `docs/model_semantics.rst` (replay salvage/burned units),
    `docs/cli.rst` (failure codes), the planning note section 3.3, and the
    `single_pass.py` docstrings.
- Phase 8 P8.6 (issue #39): the single-pass documentation suite and the
  Phase 8 closeout. Documentation only — no code changes.
  - `docs/model_semantics.rst` gains a "Single-Pass Planning Mode" section
    (sibling to "The Rolling-Horizon Loop") covering the purpose and the
    deterministic-fire interpretation guard (the RH-vs-single-pass
    difference measures re-planning and structural effects, not information
    value); decision units and the `steps x period_length` horizon; the
    decadal growth surface (D2: unclamped valuation ages, absolute per-ha
    volumes, the zero-t0-volume cohort behaviour); the grade clock (D5);
    burned-stock carry and reset semantics (D4/D6); the lineage replay
    (formation-area tracking, midpoint regeneration, 1e-6 area
    conservation, per-decade snapshots); the decade-record and manifest
    contract versus `rh-run` (D7/D8: decade-start versus base-year
    objectives, WS3 fields on decade 1 only, so a summed WS3 objective is
    not comparable across modes); and the mode constraints (`horizon >=
    steps`, age smashing enabled, `period_length` divisible by the smashing
    width, midpoint off the WS3 regeneration residue).
  - The principal/agent LP sections now describe the per-cohort WS3 ceiling
    row and the per-year growth coefficients; the rolling-horizon section
    points at the new mode; the Known Limitations list covers the
    mode-conditional burned-stock boundary, the WS3-objective
    comparability rule, and the replayed-not-reoptimized regenerated
    stands.
  - `docs/cli.rst`: the full `single-pass-run` reference (fields the command
    forces/validates, a minimal config example, artifacts, failure codes,
    and the D8 non-comparability callout). `docs/ensembles.rst`:
    `planning_mode` as an ensemble axis with a paired RH-vs-single-pass
    grid example and the single-pass-only constraint on
    `burned_stock_reset`.
  - Pointers in `docs/index.rst`, `docs/quickstart.rst`, and
    `docs/validation.rst`; `examples/README.md` cross-links the paired
    runs (`examples/single_pass_tsa29.yaml` landed with P8.4).
  - Every documented field, code, and artifact was cross-checked against
    `single_pass.py`, `principal.py`, `agent.py`, `rh.py`, `models.py`, and
    `cli.py`.
  - Verification (local, `taskset -c 0-3`, `OMP_NUM_THREADS=4`): `python -m
    ruff check .` clean; `python -m pytest` (322 tests) all pass;
    `sphinx-build -b html docs _build/html -W` clean; `python -m build` and
    `twine check dist/*` pass.
- Phase 8 P8.5 (issue #38): full-TSA29 paired RH vs single-pass evidence at an
  active subsidy, plus a bit-identical replay memoization.
  - Three-arm evidence run (configs and run outputs under
    `outputs/p8_evidence/`, gitignored; summary in
    `p8-evidence-summary.{md,json}`): RH vs single pass vs single pass with
    `burned_stock_reset`, at `subsidy_rate_per_m3: 30.0` (active region),
    `workers: 1`, 10 decades x 10 years. Decade 1 is bit-identical across all
    arms; SP-vs-RH divergence starts in decade 2 (green |Δ|/RH 11.6%, then
    7-23%; RH green total +6.4%, area burned within 0.3% per decade). The
    burned-salvage gap is dominated by the RH boundary artifact:
    continuous-burn single pass salvages 1.46M m3 over the run vs RH 0.27M,
    while the reset arm lands at 0.24M and pulls the principal objective
    toward RH (393.8M vs SP 220.7M, RH 446.2M).
  - `single_pass._zero_harvest_decade` memoizes one fresh zero-harvest
    burn-rate trajectory and serves each regen sub-cohort its decade slice at
    the lineage's `elapsed_years` offset. Regenerated stands are never
    harvested or salvaged (D3), so the slice applies exactly the float
    operations `simulate_cohort_years` would run on the carried state; replay
    A/B on identical inputs 73.2 s -> 17.9 s (4.1x), full single-pass runs
    118.4 s -> 62.0 s. Outputs re-run and verified bit-identical file-by-file
    (steps.jsonl, manifest, final state, all 11 snapshots, modulo timing
    fields); new unit test
    `test_replay_regen_decade_slices_match_carried_state_simulation`.
  - The tractability probe (evidence comment on #38) showed principal/agent
    LP build + solve is within budget at T=100 (worst 27.7 s build, 46.2 s
    solve, 2.4 GB peak RSS), so the batched-CSR LP assembly and recursive
    `cum_offer` rewrites (planning R1/R2) remain future work.
- Phase 9 feasibility brief completed. The condensed assessment is
  `planning/phase9-ws4-assessment.md`: WS4 reviewed at `b380c128`
  (Apache-2.0, `0.1.0`, Phase 12 active), a weighted goal-programming
  metaheuristic with soft targets and no optimality claim; premise
  corrections (a dataset build step remains; capability superset does not
  imply semantic parity — the all-zero product-criterion patch targets and
  the mill-destination policy difference are WS4's own measured backlog
  items; spatial features are inert on TSA29 until topology/roads data
  exist); TSA29 data plumbing (both ingest formats and fragments present,
  crosswalk and the 2903xxx AU coverage the top unknowns, topology empty, no
  road/mill tables or rates); capability matrix; three integration paths with
  the path (b) repo-contract conflict; decisive questions; viability and kill
  criteria; and the P9.1+P9.2-first sequencing (~4-6 person-days) with a
  checkpoint before any coupling. Scoping refined in
  `planning/phase9-ws4-backend-spike.md` (P9.1: `ws4 validate` clean on
  matrix+fragments ingest, crosswalk note incl. the 2903xxx AU question,
  simulate smoke, wall/RSS recorded; P9.2: mirror the WS3 target set, measure
  level reachability and iteration/scale numbers, state whether the WS3 level
  is attainable), with the scale/candidate, soft-constraint, patch-criterion,
  and inert-`--time-limit` risks recorded. New P9.5 topology/roads data
  assessment issue (#45) added to the roadmap; issue bodies #40-#44 updated
  to the refined scoping.
- Kickoff: Phase 9 WS4 backend spike scoped on `feature/p9-ws4-backend` as a
  decision-gated evaluation of swapping the WS3 wood-supply backend (and
  possibly the custom principal/agent LPs) for WS4
  (https://github.com/UBC-FRESH/ws4). Parent issue #40 with child issues
  #41-#44; seed planning note `planning/phase9-ws4-backend-spike.md`. The
  WS3-based pipeline (including the Phase 8 results) remains the reference
  until the decision gate passes.
- Phase 8 P8.4 (issue #37): the single-pass 100-year orchestrator,
  `planning_mode`/`burned_stock_reset` config surface, `single-pass-run` CLI,
  and ensemble dispatch.
  - `src/fresh_salvage/single_pass.py` adds
    `run_single_pass(config, verbose) -> RHResult`: one WS3 solve at t=0 (the
    same `horizon`-period problem `rh-run` solves at step 1, D1), per-cohort
    per-year WS3 ceilings from `rh.schedule_cohort_period_volumes` /
    `rh.annual_ceiling_matrix`, one principal LP and one agent LP over
    `steps * period_length` years with the decadal growth surface, the grade
    clock (D5) and the configured burned-stock semantics (D4/D6), then a
    100-year decade-block lineage replay (`single_pass.replay_lineage`) that
    ages survivors, regenerates everything that left the live pool at the
    smashing midpoint, replays the salvage-independent live dynamics (the
    LP owns salvage feasibility; salvage areas are reported from the raw
    schedule), and writes `derived/rh_state/step_{k:02d}.are` for
    `k = 0..steps`.
  - `RHRunConfig` gained `planning_mode: Literal["rolling_horizon",
    "single_pass"]` (default `rolling_horizon`) and `burned_stock_reset: bool`
    with cross-field validation: single pass requires `horizon >= steps` and
    the P8.1 age-lattice preconditions; `burned_stock_reset` is rejected
    outside single pass. Both are flat, so either is an ensemble/sensitivity
    axis.
  - `RHStepRecord` now allows absent WS3 fields (populated for decade 1 only,
    D8) and carries `principal_objective_value_base_year` /
    `agent_objective_value_base_year` next to the decade-start objective
    values (D7). `RHManifest` gained the defaulted `planning_mode` plus
    `total_principal_objective` / `total_agent_objective_base_year`, summed
    from the records in both modes. `RHResult` carries `planning_mode`.
  - `principal.decadal_objective_values` / `agent.decadal_objective_values`
    decompose the solved objectives per decade (the principal's constant
    offset included) from the built LP's own cost vector, which the builders
    now expose on `PrincipalLP`/`AgentLP`; `solve_principal`/`solve_agent` fill
    the new `decadal_objective_values` result field when given
    `period_length`.
  - `rh.run_planning(config, verbose)` dispatches by `planning_mode`; the
    ensemble worker and `cli.rh_run` use it, so paired RH-vs-single-pass
    ensembles work through an axis. `run_rh` and `run_single_pass` each
    reject the other mode with a structured diagnostic. The thin
    `single-pass-run` command forces `planning_mode="single_pass"` through a
    full re-validation (planning Q1 resolved).
  - Correction surfaced by the `steps=1` oracle: a cohort with zero t=0
    per-ha volume had its decade-0 burn influx scaled by 1 instead of
    `v_0 = 0` in `principal._annual_burned_volumes`, so the principal charged
    a burned-value loss the rolling-horizon LP does not have (75 TSA29
    cohorts; a 2-ulp objective difference). Fixed, with a regression test in
    the growth-equivalence suite.
  - Perf finisher from the issue #38 probe: `agent._annual_burned_prices` /
    `_annual_burned_mixes` price each grade-clock residue once per cohort
    instead of rebuilding the `O(year)` grade-mix trajectory per cohort-year
    (identical floats; the equivalence tests still pass bit-exactly).
  - Tests: `tests/test_single_pass.py` (42 tests: lineage replay assembly,
    conservation, caps, reset invariance and failure paths; both decades
    decompositions including the principal offset; decade record slicing;
    config validation; old-manifest parsing; dispatcher and ensemble-axis
    routing; CLI failure paths and mode forcing; the `steps=1` oracle in a
    ws3-free variant and against the local TSA29 bridge). Real-data smoke
    (local, not tracked): single pass with `steps=1` reproduces the `rh-run`
    step-1 record with a bit-identical final state; the full 100-year run
    (steps=10, horizon=15, WS3 `workers=1`, `taskset -c 0-3`) completes in
    122 s wall with area conserved bit-exactly and totals comparable with the
    local `rh_100yr_calib` run.
- Phase 8 P8.2/P8.3 (issues #35, #36): decadal growth-aware volume
  coefficients, the grade clock, and burned-stock continuity/reset
  semantics.
  - `principal.decadal_per_ha_volumes` returns each t=0 cohort's per-ha
    volume at the UNCLAMPED decadal ages `age0 + L*k`, with the exact
    boundary-parser lookup (constant extension beyond the tabulated
    maximum). That is the WS3 period valuation convention, so the LP
    coefficients stay consistent with the WS3 per-period ceilings
    (supervisor decision; planning risk R3 resolved; `rh.curve_age_caps`
    still governs RH state aging, unchanged).
  - `PrincipalCohort`/`AgentCohort` gained the optional
    `decadal_volume_scale`, holding those absolute per-ha volumes (m3/ha).
    The principal LP scales green and burned volumes in `annual_offer`,
    `annual_burn_loss`, the AAC row, the burned-cap row, and the
    ceiling-row coefficient, and its predetermined fire/decay path tracks
    live volume on the parsed t=0 basis (live AREA when `v_0 = 0`) so burn
    influx in decade `k` is `R * live_area * v_k`. The agent keeps H/S/V/B
    as t=0 area fractions, scales margins by `area_ha * v_k(y)`, and
    re-expresses carried burned inventory at decade boundaries by
    `v_k / v_{k+1}` (linear, volume-conserving). `v_{k+1} = 0 < v_k` fails
    fast (`agent_degenerate_growth_ratio`); on the 108 TSA29 yield curves
    and all 1,608 t=0 bridge cohorts no such boundary occurs within 100
    years.
  - `grade_clock_period: int | None` indexes the annual burned-grade mix
    at `year % period` (D5) in both LPs, threaded through the builders and
    solvers; `None` keeps the LP-year index. Burned stock aged more than one
    period is priced at index `year mod period`, which mirrors the RH window
    and is intended.
  - `burned_stock_reset: bool = False` re-seeds the principal burned path
    from `burned_volume_m3 * v_k / v_0` and restarts the `annual_burn_loss`
    decay exponent at every decade start (D6), and zeroes the agent's
    carried `B[t-1]` coefficient in the first-year burned and salvage rows
    of each decade (D4).
  - RH passes nothing new, so the rolling-horizon LPs are bit-identical:
    `tests/test_phase8_growth_equivalence.py` checks LP arrays and
    solutions for the RH-shaped usage (horizon at most one `period_length`,
    including `horizon < period_length`) in-module and against the
    branch-base module extracted from git (`53ba8d1`, content-identical to
    `09ed633`; CI checks out full history so the cross-module tests run
    there too — review M3).
- Phase 8 P8.1 (issue #34): added year-indexed WS3 ceilings for multi-period
  plans.
  - `rh.schedule_cohort_period_volumes` maps the `cc` volume of every
    implemented WS3 period back onto the t=0 cohorts. An untouched cohort
    of t=0 age `a0` appears in period `p` at age `a0 + L*(p-1)`, on the
    age-smashing midpoint lattice. This was verified against ws3 aging
    (`+period_length` per period, no clamp) and on the local TSA29 step-01
    schedule, where all 557 period-1..10 rows map.
  - A clear-cut resets age to 0 (`*ACTION cc Y`), so regenerated stands
    sit on the `0 (mod L)` lattice. A harvest of a regenerated stand
    inside the implemented periods fails fast
    (`rh_schedule_regenerated_harvest`); on TSA29 the first one is in
    period 12. Unknown cohorts, off-lattice rows, ambiguous lattice
    configurations, and off-lattice state ages also fail fast.
  - `rh.annual_ceiling_matrix` splits each period's volume into annual
    ceilings.
  - `principal.build_principal_lp` / `solve_principal` now accept, per
    cohort, either a scalar ceiling (broadcast, the existing RH path) or a
    sequence of exactly `horizon` per-year ceilings.
  - RH still uses `_period1_cohort_volumes` and scalar ceilings. Its LP is
    bit-identical: a scalar-vs-broadcast equivalence test covers this, and
    the LP arrays and solution were also checked against the `9b5a0a0`
    principal module.
- Opened Phase 8, the single-pass 100-year planning baseline (parent issue
  #33, child issues #34-#39, branch `feature/p8-single-pass-baseline`, built
  on PR #32). Single pass solves the coupled WS3 -> principal -> agent ->
  fire pipeline once at t=0 over the full run, as the baseline that `rh-run`
  (decadal re-planning) is compared against. The design contract (decisions
  D1-D9, config surface, artifact contract, test plan, risks) is
  `planning/phase8-single-pass-baseline.md`.
- Fixed CI test collection: `tests/test_ensemble.py` imported `Result` from
  `click.testing`, and `click` is not a declared dependency (recent Typer
  bundles its own copy of click), so collection failed with
  `ModuleNotFoundError: No module named 'click'`. The import now comes from
  `typer.testing`, and no dependency was added.
- Fixed WS3 schedule row volumes double-counting duplicated groups (issue
  #31, branch `fix/schedule-group-volume-dedup`). `ws3._compile_schedule`
  attached the `compile_product` total of each `(period, dtype_key,
  age_class, harvest_action)` group to every row of that group. WS3 emits
  one row per Model-I path, so a group split over several paths was counted
  once per row. This inflated the RH per-cohort period-1 WS3 ceilings
  (`rh._period1_cohort_volumes`) and the `ws3-run` / RH WS3 objective. Row
  volumes are now the group volume apportioned by area share (exact, because
  the rows share a development type and age). `compile_product` runs once
  per group, and a zero-area group that still reports volume fails fast
  (`ws3_schedule_volume_without_area`). Re-apportioning the local
  100-year RH monotonicity-fix schedules gives these period-1 `cc` ceiling
  sums:
  - step 01: 5,108,975.6 -> 5,003,960.3 m3 (-2.06%); one cohort's ceiling
    was doubled;
  - step 10: 2,765,242.1 -> 2,565,864.6 m3 (-7.21%); again one doubled
    cohort.

  Across all periods, the summed schedule volume falls by 37.8% (step 01)
  and 17.9% (step 10). A fresh 10-period `ws3-run` on the fixed code
  reproduces the earlier schedule row-for-row. Its objective changes from
  70,541,148.96 to 46,380,218.18 (0.85 x 54,564,962.6 m3), its per-period
  volumes now sit inside the even-flow bands (period 10 drops from 34.5M m3
  to 6.06M m3), and 84 duplicated groups now sum exactly to their group
  totals. RH, ensemble, flip-point, and sensitivity results generated before
  this fix (including the August 2026 runs) used inflated ceilings and are
  superseded.
- Added annual predetermined burned-grade mixes for the principal and agent
  coefficients. Year 1 uses the ingested burned-grade composition and each
  later year applies the configured downgrade-only transition once. Existing
  scalar LP states and objective structures remain unchanged; agent results
  now report grade volumes that sum to scalar `B[c,t]`. Tracked in GitHub
  parent issue #18, child issues #20/#21, with commits `e7d4a1c`, `d1d97ca`,
  `8a8251c`, `dec54ad`, and `ad92cf0`.
- Opened principal follow-up issue #28 for predetermined per-offer-year fire
  and burned-volume-decay objective coefficients without changing the current
  objective expression or LP structure.
- Implemented issue #28 on branch `version1.3(PrincipalModelFire/Decay)`:
  principal offer and burned-loss coefficients now use deterministic annual
  fire, volume-retention, and grade-transition paths while preserving the
  existing LP columns, rows, objective algebra, and constraints.
- Created GitHub Phase 7 issue #3 and linked child issues #5-#16 for the
  sensitivity-analysis and adaptive subsidy flip-point work on branch
  `version1.1(SensivityAnalysis&BinaryFlippingPoint)`.
- Added `planning/first-best-hidden-information.md`, which defines a
  first-best benchmark and hidden-information experiment design for measuring
  moral-hazard and information losses against the current principal-agent
  pipeline.
- ``sensitivity-run`` can now optionally perform an independent adaptive
  subsidy flip-point search for each non-subsidy sensitivity setting and add
  the resulting brackets to its Markdown report and CSV evidence. The TSA29
  sensitivity example enables this analysis; ``docs/sensitivity_report.rst``
  documents the report structure and evidence files. Expected unbracketed
  endpoint outcomes (such as the fire-free control) are reported as
  ``not_bracketed`` rather than aborting the full sensitivity analysis.
- Added the TSA29 burned-salvage operation-cost sensitivity: calibrated
  ``burned_harvest_cost`` (56 $/m3) plus 20% and 40% increases, with their
  corresponding subsidy flip-point brackets.
- Added ``docs/flip_point_search.rst``, a standalone explanation of the
  adaptive subsidy flip-point algorithm, input prerequisites, command, and
  result interpretation.
- Grade-transition monotonicity fix: `BURNED_GRADE_TRANSITION` Sawlog row
  corrected from {Peel 0.10, Saw 0.80, Pulp 0.10} to {Peel 0.00, Saw 0.80,
  Pulp 0.20} — the 0.10 Sawlog->Peeler share was a physically impossible
  upgrade (grade hierarchy Peel > Saw > Pulp; fire can only degrade
  grade), caught in review. Rows remain downgrade-only and sum to 1.0, so
  burned volume is conserved (`Total_Burned_Vol` 79,087.38 m3
  bit-identical; the `B_*_Peelers_Vol`/`B_*_Pulpwood_Vol` split
  re-derived). Re-derived economics: SPF transition-mix salvage margin
  -15.15 -> -21.06 $/m3, DT-mix margin -19.10 -> -23.86 $/m3; the
  coupled-system flip moves from ≈ 19.1-19.4 to ≈ 23.9-24.1 $/m3
  (turn-on 23.85, saturated by 24.1 — the SPF cluster's breakevens), with
  the ramp levels bit-identical and the FESBC 14-15 $/m3 benchmark now
  closing only ~60% of the margin gap. Tests re-pinned from the constants
  (with new downgrade-only and row-sum guards); ingestion, ws3 smoke
  (24,328,759.75 m3, bit-identical), the 100-year RH dev run (salvage
  0.00 m3 at the default subsidy 3), and the flip sweep (31 + 10 + 2
  scenarios, all optimal) re-run; `planning/economics-calibration.md`
  carries the erratum note and `planning/phase6-validation-report.md`
  supersedes the 19.1-19.4 flip record.
- `ensemble-run --strict`: a completed ensemble now exits 1 when any
  scenario failed (status `partial` or `failed`); without the flag,
  completed ensembles still exit 0. Fatal grid/input/bridge failures exit 1
  in both modes, and the `--json` payload shape is unchanged — the flag
  gates the exit code only.

## 2026-08-12 — Phase 6: validation, economic recalibration

- Fixed FS-VAL-01/02 (`82688b6`): the burn-severity ladder became a
  scenario-visible parameter with a fatal guard on unmatched non-null
  labels (`data_severity_unmatched`), and severity fractions are now
  coverage-scaled by `min(1, SHAPE_Area_1 / FEATURE_AREA_SQM)` — an
  upper-bound proxy, not a spatial intersection. Total salvageable volume
  on the real layer corrected 119,585.72 -> 79,087.38 m3 (-34%); green
  volume unchanged.
- Updated the validation report with the FS-VAL resolution and corrected
  numbers (`ca65048`); the full audit trail is in
  `planning/phase6-validation-report.md`.
- Economic recalibration (`bb4707e`): all parameters recalibrated against
  BC anchors (Q4-2023 BC Interior log market levels, ILCR-style logging
  costs, new haul costs, South Central stumpage, BC tabular fire-damaged
  floor 0.25 $/m3) and threaded through a config-visible `Economics`
  surface (ingestion scenario, principal/agent config sections, flat
  `RHRunConfig` fields as ensemble axes). Rationale and provenance per
  parameter: `planning/economics-calibration.md`.
- Validation report updated with the recalibration evidence (`0e94ff4`);
  the pre-calibration flat-subsidy finding (salvage subsidy-invariant
  because the placeholder margin was ~+93 $/m3 unsubsidized) is superseded.
- Prompt-salvage adjustment (`e828dc6`): burned grade mix retargeted to
  the year 1-3 prompt-salvage regime (sawlog retention 0.80; the grey-stage
  pulp collapse stays in the 0.85/yr decay where it belongs — the first
  recalibration double-counted it) and the burned cost premium set to +25%
  (harvest 56, transport 38 $/m3). Unsubsidized salvage margin now
  ≈ -15 $/m3 (SPF transition-mix basis; DT band ≈ -10 to -36 $/m3); the
  coupled-system flip sits at ≈ 19.2 $/m3 (turn-on 19.1, saturated by
  19.4), just above the FESBC 14-15 $/m3 benchmark; the default 3 $/m3
  policy produces zero salvage, as calibrated.

## 2026-08-12 — Phase 5: rolling-horizon engine and ensemble driver

- Rolling-horizon coupling engine (`91b4aad`): `rh.py` plus the `rh-run`
  CLI. Each step re-solves a 15-period WS3 schedule from the current cohort
  state (period-0 inventory injection, no full model rebuild), splits the
  period-1 decadal harvest into 10 annual per-cohort green-volume ceilings,
  solves the principal and agent LPs over the implemented decade, replays
  the years with fire dynamics, and transitions the cohort table with
  area conservation verified to 1e-6. 10 steps = 100 implemented years;
  ≈ 150 s per scenario on a 64-core host.
- Review follow-ups (`74bb3b3`): decision-order guard, fail-path gate
  tests, nits.
- Ensemble driver (`01b4f65`): `ensemble.py` plus the `ensemble-run` CLI.
  Cartesian scenario grids over named `RHRunConfig` axes (subsidy rate,
  burn-rate multiplier, any flat economic field); spawn-based process pool;
  the no-LU bridge is built once and shared read-only; a failed scenario is
  recorded and never aborts the ensemble. 4-scenario smoke grid: 4.41x
  speedup; ~1,000 scenarios ≈ 40 min at `max_workers: 64` / `workers: 1`.
- Review follow-ups (`d81e42e`): reserved `bridge_path` axis, verbose
  forwarding, worker-crash test, checksum-first bridge reuse, shared
  subsidy constant, public file checksums.

## 2026-08-12 — Phase 4: agent LP and fire dynamics

- Agent-side harvest/salvage LP (`bde6b96`): continuous fractions of cohort
  standing volume in `[0, 1]`, 1-year timesteps, NPV objective with 3%
  discounting. The LP rows implement the fire dynamics of `fire.py`
  directly (harvest -> fire -> salvage -> decay; `V`/`B` balances; salvage
  feasibility against the on-hand burned inventory; no double selling), so
  the LP and the simulation share one source of truth.

## 2026-08-12 — Phase 3: principal LP

- Principal-side offer LP (`b573f59`): continuous offer fractions per
  cohort-year under the green-volume AAC ceiling (2,937,509 m3/yr),
  expected burned-wood loss weighted by the MFRI burn rate, optional
  burned-volume cap. Added the MFRI fire-rate table (`fire.py`).
- Review follow-ups (`07da484`): structured error contracts, CLI stub
  retirement, area guard, test-gap closures.

## 2026-08-12 — Phase 2: ingestion and WS3 full-TSA pipeline

- Full-TSA data ingestion (`752075c`): pydantic v2 records
  (`ScenarioRunConfig`, `ArtifactLayout`, `Stand`, `DevelopmentType`,
  manifests), a faithful port of the predecessor preprocessing with the
  11-landscape-unit subset filter removed (246,957 stands retained, all 12
  BEC zones), the `development_type` stratum key, and the wired `ingest`
  CLI with `--json`.
- WS3 full-TSA solve pipeline (`11c1f1d`): `WS3RunConfig` and records,
  `ws3.py` port for the entire bridge with age smashing,
  minimum-harvest-age enforcement, the AAC ceiling, even-flow constraints,
  HiGHS solve, and the wired `ws3-run` CLI with `--smoke` and `--json`.
- Bridge rebuild via the femic writer (`08d5014`): the landscape-unit theme
  is dropped at the source from the femic stage-1 Woodstock CSVs, fragment
  ages are smashed to 10-year class midpoints, and femic's own stage-2
  writer aggregates area over the unique cohort keys — the ARE section
  contracts from 44,998 raw rows to 1,608 aggregated, area-conserving
  lines. Depends on the femic writer fix (`femic` commit `d057aea`).
- Review-hardening gates (`01cf464`): area-conservation fail-fast gate on
  the written ARE section and strict age parsing on the bridge rebuild.
- P2.5 verification closed: the 30-period production horizon solves
  end-to-end in 1,407.8 s (compile ≈ 600 s + solve ≈ 774 s); policy set to
  15-period dev / 20-period production horizons, with 30p validated.

## 2026-08-12 — Phase 1: scaffold and rename

- Initial scaffold (`65f1238`): freshforge-style skeleton — governance
  docs, roadmap, changelog, release notes, planning refactor contract,
  package skeleton with Typer CLI stubs, Sphinx docs, and CI/docs/release
  workflows.
- Repo and package rename (`a44ce82`): `masc-yunhao-xu-linear` ->
  `fresh-salvage` (GitHub repository, local directory, import name
  `fresh_salvage`, console script `fresh-salvage`).
