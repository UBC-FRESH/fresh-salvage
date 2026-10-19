# Phase 9 WS4 Feasibility Assessment

Status: assessment complete; P9.1/P9.2 measured evidence recorded (aspatial
scale test — see `planning/phase9-ws4-scale-test-results.md`); checkpoint
held on #40; P9.3–P9.5 open

Date: 2026-10-18 (measured-results section added 2026-10-19)

Parent issue: #40. Companion scoping note:
`planning/phase9-ws4-backend-spike.md`.

This note is the condensed record of the WS4 feasibility review. Every
statement about WS4 is sourced from the WS4 repository at the reviewed commit
and cites a repository-relative path; every statement about the femic TSA29
instance is a count or observation read from that instance's files. Nothing
here is a thesis result: capability claims become usable evidence only when
P9.1/P9.2 record observed runs — which are now recorded in
`planning/phase9-ws4-scale-test-results.md` (sections 9 and the corrected
estimates below reflect the measurements).

## 1. WS4 facts (reviewed at `b380c128`)

- **Provenance and license.** Repository `UBC-FRESH/ws4`, reviewed at
  `b380c128` ("P11 close-out carried out; Phase 12 launched"), Apache-2.0,
  version `0.1.0` released 2026-10-15. Twelve-phase project; Phase 12
  ("Spatial decision match") is active. Working maturity is high for a
  `0.1.x`: a single quality dashboard runs lint, format, strict types,
  tests with a 90-percent branch-coverage gate, a workflow validator, and a
  warnings-as-errors docs build on every commit (`AGENTS.md`,
  `scripts/dashboard.py`).
- **Solver model.** Not a linear program. Targets are weighted
  goal-programming penalties — per-period minimum/maximum bands, linear or
  quadratic deviation, per-target weights, an optional discount rate — and
  the objective minimized is the total penalty. The schedulers are
  metaheuristics: simulated annealing, tabu search, and iterated local search
  with operators (toggle, shift, swap, LNS, cluster, merge, split),
  multi-start, and an island model with elite migration. Every search records
  its seed; reproducibility is per configuration ("changing `workers` or
  `islands` is a different search"). See `docs/reference/optimizer.rst`,
  `docs/concepts/the-objective.rst`, `ws4/opt/engine.py`,
  `ws4/opt/solvers.py`, `ws4/opt/operators.py`.
- **No optimality claim.** "The search finds good schedules, not optimal
  ones." A `--mip-bound` option records a penalty lower bound for an
  option-selection relaxation; it is explicitly "not a certificate of
  optimality for the spatial problem" (`docs/guides/limitations-and-boundaries.rst`).
- **Soft constraints.** "Weights trade off; they do not prioritise... the
  search cannot treat one as hard and another as soft"
  (`docs/guides/limitations-and-boundaries.rst`). Even-flow
  (`flow.even`) and non-declining yield (`flow.ndy`) are *shapes* of weighted
  targets over periods, not constraints (`docs/reference/optimizer.rst`,
  `docs/howto/even-flow.rst`).
- **Time model.** "The simulation step is one year and is not configurable";
  accounting periods are whole-year multiples set by `period_width`
  (default 1, with 5 and 10 the forest-planning convention). Patch targets
  are snapshots at a period's end year rather than period averages
  (`docs/reference/optimizer.rst`, `docs/guides/limitations-and-boundaries.rst`).
- **Decision units.** The compiled growth unit is the fragment; a block is a
  runtime operational grouping and the schedule names blocks, so one compiled
  dataset can serve several block layouts (`docs/concepts/the-model.rst`).
  In the femic TSA29 instance the fragment inventory and the block layer are
  1:1 (421,945 rows each), so here "block" and "fragment" coincide.
- **Two construction paths.** A ForestModelXML recipe plus a fragment
  inventory (`ws4 build`, `docs/reference/forestmodelxml.rst`) and a legacy
  compiled-matrix directory plus a fragments table (`ws4 ingest`,
  `ws4/build/legacy.py`). Both produce a versioned canonical dataset with a
  manifest; the simulator and optimizer read only that dataset
  (`notes/adr/0011-forestmodelxml-and-legacy-ingestion.md`).
- **No economics.** WS4 carries no principal-agent or subsidy machinery.
  Costs and values enter only as matrix-carried accounts or constant curves
  and as weighted penalties in the objective; any subsidy policy must live
  outside the tool (wrapper runs plus an external driver).
- **`--time-limit` is inert.** The option is declared (`ws4/cli.py`) and
  stored on `OptimizeConfig` (`ws4/opt/engine.py`), but no code path reads
  it; search length is bounded by `--iterations`, `--starts`, and the island
  settings.

## 2. Premise corrections

Three kickoff premises need correcting before they are built on.

1. **A build step is still required; WS4 is not a drop-in.** Even
   integration path (a) adds a WS4 dataset step: a compiled dataset plus
   manifest produced by `ws4 build` or `ws4 ingest` is the contract the
   simulator and optimizer read. "Replace `ws3-run`" therefore means
   "add a dataset build/ingest step and a schedule adapter", not
   "re-point a file path". The dataset build is a new procedural dependency
   in the pipeline's provenance chain.
2. **A superset in capabilities is not a superset in guarantees.** WS4's
   surface (spatial constraints, patch targets, road routing, mill
   destinations) is wider than WS3's, but WS4 does not reproduce the
   reference's semantics everywhere. Two measured gaps sit in WS4's own gap
   list (`notes/backlog.md`): (i) **patch criterion**: the reference's
   harvested patches use a product criterion, while WS4's patch state
   evaluates a criterion as a feature, so 28 of the reference's 63 patch
   targets are accepted, run, and produce **every cell zero**; (ii)
   **mill-destination policy**: WS4 sends each block's product to its
   cheapest mill (118,498 / 251,703 for the conifer yield over the periods
   compared) whereas the reference's near-balanced 54/46 split
   (1,029,670 / 892,306) comes from its own exclusive-account allocation
   under mill minimums, so route-ratio accounts are comparable only once the
   split rule matches. WS4's Phase 12 exists to close these; they were not
   closed at the reviewed commit.
3. **Spatial features are real but unusable on TSA29 until the data is
   built.** Adjacency requires a topology file
   (`BLOCK1,BLOCK2,DISTANCE,LENGTH` with the `-9999` sentinel); routing
   requires four tables (vertices, segments, destinations, linkages) plus
   haul/build/maintenance rates and per-block linkages. The instance ships a
   header-only topology file and no route tables (section 3). Until P9.5
   settles whether those layers can be built, patch, roads, and mill
   capabilities are inert on TSA29, and a TSA29 run either omits them or
   fabricates data, which AGENTS.md forbids.

## 3. Data plumbing status (femic TSA29 instance)

Both ingest formats exist for TSA29:

| Surface | State | Evidence |
| --- | --- | --- |
| Legacy matrix | Present | `models/tsa29_patchworks_model/tracks/` — 13 tables; `blocks.csv` 646,031 rows, `groups.csv` 421,945 rows, `strata.csv` 524 rows, `curves.csv` 15,910 rows |
| ForestModelXML | Present | `output/patchworks_tsa29_validated/forestmodel.xml` (horizon 300, multi-match) |
| Fragments | Present | `output/patchworks_tsa29_validated/fragments/fragments.shp` (421,945 records; `FRAGMENT_I, BLOCK, AREA_HA, F_AGE, AU, IFM, ORIGIN, SILV_STATE, RETENTION, TSA`) |
| Curves | Present for all 54 AUs | Instance curve tables carry untreated/treated pairs for the 2901xxx, 2902xxx, and 2903xxx series |

Open plumbing items, in priority order:

- **Crosswalk (the number-one unknown).** The block-id overlap is exact —
  421,945 unique `BLOCK` values in the fragment inventory and the same in
  the matrix — but the matrix splits 224,086 blocks into managed/unmanaged
  rows (646,031 matrix rows for 421,945 blocks), and the fragments carry no
  `TRACK` column. The legacy ingest path uses a fragment row's own `TRACK`
  when a block appears on several tracks and **fails fast otherwise**
  (`ws4/build/legacy.py`, `block_track` join); so a crosswalk step must
  expand or annotate fragment rows to the matrix's track layout and
  reconcile areas. This is a join to be verified and audited, not assumed.
- **The 2903xxx AU question.** Fragments use all 54 AU codes including the
  2903xxx (H site-index) band. The compact in-repo TSA29 validation bundle's
  `au_table.csv` is **partial** — 29 of 54 rows — while the broader model
  input bundle's `au_table.csv` carries all 54 with source-mapped columns
  (`source_local_au_id` etc.). P9.1 must state exactly which AU→curve
  crosswalk is authoritative for ingest and whether the 2903xxx band
  resolves completely, rather than letting a partial bundle define coverage.
- **Topology is empty.** `models/tsa29_patchworks_model/blocks/topology_blocks_0r.csv`
  is a header-only file (`BLOCK1,BLOCK2,DISTANCE,LENGTH`); there is no
  adjacency to run spatial constraints or patch accounts against.
- **No road or mill tables, and no rates.** There are no vertices/segments/
  destinations/linkages tables, no haul/build/maintain rates, and no mill
  destination table anywhere in the instance. Raw BC road-network downloads
  exist under the instance's `data/downloads/` directory, but they are
  unconverted and carry neither the rate model nor the block-to-vertex
  linkage WS4 expects. This is P9.5's subject.

The good news is that both construction paths have real TSA29 inputs. The
legacy matrix + fragments path has now been exercised at full estate (P9.1;
`ws4 validate` clean, crosswalk exact — measured numbers in
`planning/phase9-ws4-scale-test-results.md`). The ForestModelXML path remains
unexercised.

## 4. Capability matrix vs fresh-salvage needs

| fresh-salvage need | Current stack (WS3 + HiGHS LPs) | WS4 as reviewed | Verdict / unknown |
| --- | --- | --- | --- |
| Even-flow / AAC level | WS3-side ceiling (`aac_annual_m3 * period_length`) and an even-flow constraint with tolerance; the compiled schedule respects them | Weighted soft targets (`flow.even`/`flow.ndy` plus level bands); nothing is hard | Comparable in the measured mirror: the one-sided WS3-style band is met in every period and all ceilings are met (P9.2, see the results note) |
| Reaching the WS3 level | The reference schedules hit the level by construction | A search minimizes penalty; no guarantee it reaches any particular level | **Answered by P9.2 (aspatial): reachable** — 9/10 WS3 per-decade floors met (p1 at 96.8%), terminal liquidation at 100.0% of the WS3 level; residual 0.18 Mm³ measured and closing |
| Operability / eligibility | Encoded in the WS3/femic bridge; the scheduler respects operable age windows | Model II rule enforced in simulation from the compiled dataset | The dataset is the hook; there is no schedule-format bridge that can express eligibility after the fact |
| Decision unit | Schedule rows per cohort: `(period, dtype key, age class, harvest action)` plus volumes; the principal LP adds per-cohort period-1 ceilings | Schedules name blocks; candidate chains per block; block↔fragment is a runtime view | Cohort ceilings have no WS4 counterpart; an adapter must aggregate block decisions to cohorts, and aggregation is lossy unless the mapping is exact |
| Downstream ceiling coupling (listener) | `rh._period1_cohort_volumes` extracts per-cohort period-1 volumes from a WS3 schedule; the principal LP turns them into annual ceilings | Nothing equivalent; the search returns a block-level incumbent | No listener exists and nothing in WS4 exposes one; it would have to be built in the adapter and is unproven until P9.2 |
| Fire and grade coupling | Pipeline-owned (`fire`, grade transition), backend-independent | Unaffected | Backend swap does not change it, but schedule/cohort mapping still applies |
| Economics / subsidy policy | Principal and agent LPs with explicit economic surfaces | None; costs enter only as accounts/targets | Path (c) would require the policy driver outside WS4 |
| Reproducibility | Deterministic LP solves, recorded inputs | Seeded heuristic; per-configuration reproducibility; "good schedules, not optimal ones" | Meets provenance standards if seeds and configs are recorded; does not meet optimality expectations. Measured: per-seed reproducibility is exact (two processes bit-identical over 148,018 common iterations) |
| Spatial value proposition (patch size distribution, roads, mills) | Absent; allocations are post-hoc | Real and first-class — but inert on TSA29 without the data (section 3) | P9.5 decides whether it can ever be unlocked |

## 5. Integration paths: impact, runtime, risk

Runtime context: the full-estate aspatial scale test has now measured the
previously unmeasured numbers (P9.1/P9.2; detail in
`planning/phase9-ws4-scale-test-results.md`). For scale reference, WS4's own
benchmark sample is 16,174 fragments / 3,183 blocks with ~1 ms per optimizer
iteration on a binding volume-plus-patch workload and 6.0k–18.7k
iterations/s at 8–32 islands on a 72-core host
(`docs/reference/performance.rst`). TSA29 is ~130x the blocks. **Measured on
the full estate (646,031 fragment-portions / 421,945 blocks):** ingest 2.0 s,
validate 1.4 s, 100-yr replay 8.28 s; the candidate enumeration is
**15.4 M chains in 17.7 s / 2.29 GB** (the earlier ~27.7 M estimate did not
hold — WS4 caps chains per block at 320 — so candidate setup is not a
bottleneck); per-iteration cost is ~0.5 ms warm and ~5 ms end-to-end at
full estate; a full 350k-iteration, 100-yr mirror search on one core costs
~30 min and peaks at 3.56 GB. That is ~181 s per decade-step single-core as
run, versus the WS3 rolling step's ~11 s (build+solve) or the standalone
10-period LP solve of 0.36 s — slower, but not infeasible. The search space
does not make a 10-period study unaffordable; the per-move incremental cost
remains the design assumption WS4 documents.

| Path | What changes | Rough code impact | Runtime | Principal risk |
| --- | --- | --- | --- | --- |
| (a) Schedule-only backend | Add the WS4 dataset step (`build`/`ingest`) and an adapter that turns a WS4 run into the schedule records the pipeline consumes; principal/agent LPs stay | One new adapter module plus CLI plumbing; no changes to LP formulations; must build the cohort aggregation and the ceiling listener | One heuristic search replaces one WS3 LP solve, plus a one-off dataset build (ingest ~2 s). Measured: ~30 min single-core per full 100-yr run (~181 s/decade-step), 3.56 GB peak | Soft bands and cohort aggregation may not reproduce the ceilings the LPs depend on; the dataset build adds a procedural dependency |
| (b) Block-integer take-or-leave | Replace the continuous agent LP with per-block integer acceptance handled by WS4's metaheuristics (or a custom SA layer) | Changes the coupled formulation and the drivers; largest formulation churn | Combinatorial search per decision; the candidate/iteration cost is now measured (15.4 M chains, ~5 ms/iteration full estate), so the combinatorics are tractable — the contract question is not | **Repo-contract conflict:** AGENTS.md requires "continuous HiGHS LPs, no binaries, no thresholding or rounding of decision outputs". Path (b) is not reachable without an explicit maintainer-approved contract amendment, and it changes the thesis model class |
| (c) Full swap | Policy logic (subsidy-dependent acceptance) as WS4-side wrappers plus programmatic re-runs/bisection; custom LPs retired | Retires `principal`/`agent`; new driver, new validation story, re-run of the evidence chain | Every subsidy bracket costs multiple full searches; no hooks beyond config/targets/weights | Comparability with existing WS3-based numbers; explainability burden; sinks the thesis timeline |

Path (b) is recorded here for completeness; until the contract question is
answered by the maintainer it is out of contract for this repository and must
not be started.

## 6. Decisive questions and how each is settled

1. **Period semantics: docs versus code.** WS4's optimizer page describes the
   period convention with `year: 0` examples for decade windows while its
   prose — and the project's own changelog entry for the parity fix — name
   the year a period *ends* on, with accumulation applying only to product
   accounts. A bridge must not be built on an assumed reading. **Settled by:**
   reading `ws4/opt/objective.py` (`accumulates()` and the window logic) and a
   two-period smoke run whose per-period values are checked against
   hand-computed window sums.
2. **Can WS4 reach the WS3 level?** **Settled by:** P9.2 mirroring the WS3
   target set (the `aac_annual_m3` level under the configured even-flow
   tolerance) and recording the level reached, the iteration budget, and an
   explicit statement of whether the level is attainable — or a measured
   reason why it cannot be compared. **Settled (aspatial, 2026-10-19): yes**
   — 9/10 WS3 per-decade floors met, p1 at 96.8%, terminal liquidation at
   100.0% of the WS3 level; see `planning/phase9-ws4-scale-test-results.md`.
3. **Scale numbers.** **Settled by:** P9.1/P9.2 on the real instance —
   ingest/build wall time, per-iteration time, peak RSS, and the candidate
   count, replacing the ~421,945-block / ~27.7M-candidate estimates with
   measurements. **Settled:** measured — 646,031 fragment-portions over
   421,945 blocks; ingest 2.0 s; 15.4 M candidate chains (the ~27.7 M
   estimate did not hold); ~5 ms/iteration end-to-end; 3.56 GB peak; see the
   results note.
4. **Crosswalk exactness.** **Settled by:** a join audit that reports
   unmatched blocks in both directions, the split-block expansion rule for
   the 224,086 managed/unmanaged blocks, area reconciliation, and the
   resolved coverage of the 2903xxx AU band (P9.1). **Settled at the
   portion level:** unmatched counts zero; the managed-first portion rule
   reproduces the matrix's own area accounting exactly; the 2903xxx band is
   fully covered; see the results note, section 2.
5. **Subsidy-bracket reproduction.** **Settled by:** P9.3 scanning the same
   subsidy grid on the plainest coupling available and comparing bracket
   endpoints against the recorded P7 brackets — or declaring the comparison
   not meaningful and why.

## 7. Viability conditions and kill criteria

**Continue only if all of the following hold after the checkpoint.**

- P9.1 ingests the TSA29 instance (matrix+fragments or XML+fragments) with
  `ws4 validate` clean, the crosswalk audited to exactness (unmatched counts
  zero or explained), and the 2903xxx AU coverage question answered.
- P9.2 shows the WS3 level is attainable (or the gap is small, measured, and
  explainable) with recorded iteration/scale numbers that make a 10-period
  study affordable on available hardware.
- A coupling path exists that does not fabricate data, does not require the
  forbidden transformations, and produces a schedule the existing pipeline
  can consume without silently changing defaults.
- The result can be explained and defended in the thesis as cleanly as the
  current stack, including the soft-constraint reading.

**Kill the phase if any of the following hold.**

- No coupling path exists without fabricated spatial data or without a
  contract amendment the maintainer declines.
- The search cannot reach a usable level at TSA29 scale, or the
  iteration/scale numbers make even a single 10-period run infeasible.
- The crosswalk cannot be made exact (systematic id/area mismatches), so any
  schedule is incomparable to the WS3 reference.
- The soft-constraint formulation or the measured parity gaps (patch
  criterion, mill destinations) make the results indefensible for the
  thesis.

**Defer** is the default outcome if the P9.1/P9.2 evidence is merely
incomplete at the checkpoint; the phase then closes as
"deferred — evidence recorded", with the decision noted for the post-thesis
scale-up work.

**Checkpoint status (2026-10-19).** The first two continue-conditions are met
on the aspatial surface: P9.1 ingests the full instance with `ws4 validate`
clean and the crosswalk audited to exactness (portion-level; 2903xxx
resolved); P9.2 shows the WS3 level is attainable at full scale with
recorded numbers (`planning/phase9-ws4-scale-test-results.md`). The
coupling-path and explainability conditions remain open, and the phase
continues to the next decision point (P9.3 and/or P9.5; P9.4 stays gated)
rather than killing.

**Cleared kill criteria at the checkpoint (2026-10-19).** Two of the four
kill criteria are **not triggered**:

- *Scale* ("the iteration/scale numbers make even a single 10-period run
  infeasible"): not triggered — a full 100-yr mirror runs in ~30 min
  single-core within 3.56 GB peak, ~181 s per decade-step as run.
- *Level reachability* ("the search cannot reach a usable level at TSA29
  scale"): not triggered — the WS3 volume level is reached aspatial (9/10
  floors, p1 at 96.8%, terminal liquidation at 100.0%, residual 0.18 Mm³
  measured and closing at the recorded rate).
- The crosswalk kill criterion is also not triggered (portion-level
  exactness, zero unmatched).
- The soft-constraint/parity kill criterion (patch criterion, mill
  destinations) is unchanged and remains for P9.4/P9.5.

Measured detail and the full caveat list: `planning/phase9-ws4-scale-test-results.md`.

## 8. Recommended sequencing

**Executed (2026-10-19):** steps 1–2 are complete — P9.1/P9.2 ran in a
clean-room spike, the checkpoint is held on #40, and both killer questions
(level, scale) passed on the aspatial surface. The next decision point is
P9.3 (coupled subsidy scan) and/or P9.5 (topology/roads assessment); P9.4
remains gated on that choice. The steps below are kept as the original plan.

1. **Run P9.1 + P9.2 only, first** — approximately 4–6 person-days. They
   answer the level question and the scale question, which are the two that
   can kill the phase; everything else is detail until they pass.
2. **Hold the checkpoint on issue #40.** Record the level/scale answers and
   take the continue/kill/defer decision (including the path (b) contract
   question if it is still alive) before any coupling work starts.
3. **P9.5 (topology and roads assessment)** is scoped now and informs the
   checkpoint's spatial half, but its execution does not gate P9.1/P9.2.
4. **P9.3 and P9.4 are gated on the checkpoint.** If the level or scale
   questions fail, stop after P9.2 and close the phase with the evidence.

## 9. Measured results (aspatial scale test)

The sanitized measured record of the full-TSA29 aspatial scale test —
environment/pinning notes, the fragments→track-portion rule, the ingest /
simulate / candidates / optimizer stage table, the mirror-run level results,
the verdicts, the cleared kill criteria, and the portability gotchas — is
`planning/phase9-ws4-scale-test-results.md`. Headline corrections to this
assessment's pre-evidence estimates, all measured on the full estate
(646,031 fragment-portions / 421,945 blocks):

| Estimate (this note, 2026-10-18) | Measured (2026-10-19) |
| --- | --- |
| ~27.7 M candidate chains; nothing measured | **15.4 M chains** (WS4 caps chains per block at 320); enumerate in 17.7 s / 2.29 GB; not a bottleneck |
| "unmeasured at TSA29 scale" (runtime) | ingest 2.0 s; 100-yr replay 8.28 s; ~5 ms/iteration; **~30 min single-core** per full 100-yr, 350k-iteration search; 3.56 GB peak |
| "only a measured run can say whether a 10-period search is affordable" | affordable as run: **~181 s per decade-step** single-core (~16× the WS3 rolling step's ~11 s as configured); islands untested |
| level "must be proven on TSA29" | **reached aspatial**: 9/10 WS3 per-decade floors (p1 96.8%), terminal liquidation 100.0% of the WS3 level, even-flow band met every period |
| crosswalk "to be verified and audited, not assumed" | audited exact at portion level (zero unmatched; managed 1,218,310.5 ha; 2903xxx fully covered) |

The `ws4 validate`-clean ingest acceptance bar and the simulate smoke
required by #41 were met without repairs or fabricated data.

## References

- WS4 repository `UBC-FRESH/ws4`, reviewed at `b380c128`; paths cited above
  are repository-relative.
- Measured results: `planning/phase9-ws4-scale-test-results.md` (P9.1/P9.2).
- Spike scoping note: `planning/phase9-ws4-backend-spike.md`.
- Parent issue: #40; children: #41–#45 (P9.5 is the topology/roads issue).
- femic TSA29 instance: the `femic` repository's
  `external/femic-tsa29-instance` checkout (matrix under
  `models/tsa29_patchworks_model/`, build inputs under
  `output/patchworks_tsa29_validated/`).
