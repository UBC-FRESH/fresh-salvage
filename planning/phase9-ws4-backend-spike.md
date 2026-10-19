# Phase 9 WS4 Backend Spike (Seed)

Status: **CLOSED — decision gate outcome: NO-GO for this project (keep
WS3), 2026-10-19.** P9.1/P9.2 evidence recorded
(`planning/phase9-ws4-scale-test-results.md`); P9.3 not pursued; P9.4
decision recorded below; P9.5 deferred (future lab work).

Date: 2026-10-18 (findings update 2026-10-19; decision recorded 2026-10-19)

> **Seed version.** This note scopes the Phase 9 spike. The kickoff framing
> has been refined from the WS4 feasibility review, whose condensed record is
> `planning/phase9-ws4-assessment.md`; the premises it corrected (a dataset
> build step remains; capability superset does not imply semantic parity;
> spatial features are inert on TSA29 until topology/roads data exist) are
> carried into the task scoping below. Statements about WS4 capabilities
> remain hypotheses until P9.1-P9.4 record observed-run evidence — P9.1/P9.2
> have now recorded the aspatial runs; see
> `planning/phase9-ws4-scale-test-results.md`. Do not cite numbers from this
> note in the thesis or any publication.

## Decision (2026-10-19): NO-GO for this project — keep WS3

The P9.4 decision gate (#44) closed as **no-go for adopting WS4 in this
project**: the project keeps the WS3-based pipeline (rolling-horizon and
single-pass; the Phase 8 results) as its results pipeline. The WS4 swap is
not adopted here.

Rationale:

- **Cost, and heuristic where WS3 is exact.** A full 100-yr aspatial mirror
  run costs ~30 min single-core (~181 s per decade-step) — ~16x the WS3
  rolling step's ~11 s and ~30x the single-pass pipeline (~30 min vs
  62 s/193 s as measured). WS4 is a seeded metaheuristic search ("good
  schedules, not optimal ones", soft bands, inert `--time-limit`) where WS3
  is an exact LP solve — more search time without a project capability that
  justifies it.
- **The spatial value — WS4's only real differentiator — is blocked.**
  Patch targets, road routing, and mill destinations are inert on TSA29:
  the topology table is header-only, and there are no route/mill tables or
  rates. P9.5 (topology/roads assessment) was deferred rather than run, so
  the spatial half is not just unmeasured — it is blocked on data that does
  not exist.
- **Pipeline stability.** The project's results pipeline (thesis numbers,
  Phase 8 single-pass and RH evidence) stays on WS3; a WS4 swap would
  invalidate comparability without an offsetting measurable gain.

What could reopen the decision:

- Spatial datasets built for TSA29 (adjacency topology, road network with
  mill destinations and rates — the P9.5 scope), turning the spatial
  features from inert to testable; and
- Parallel-scaling evidence bringing WS4 runs within the project budget
  (multi-island / multi-process scaling; untested here at one worker).

WS4 remains a **lab-level candidate**: the clean-room clone and the
measured evidence (`planning/phase9-ws4-scale-test-results.md`) are
retained for future lab work, and nothing WS4-related is vendored in this
repository. Companion records: `planning/phase9-ws4-assessment.md`
(feasibility), `planning/phase9-ws4-scale-test-results.md` (evidence),
`ROADMAP.md` ("Phase 9", closed), and `CHANGE_LOG.md`.

## Purpose

Phase 9 evaluates whether WS4 (`https://github.com/UBC-FRESH/ws4`) can
replace the WS3 wood-supply backend — and possibly the custom principal/agent
HiGHS LPs — for `fresh-salvage`.

This is a **spike with a decision gate, not a big-bang swap**. The WS3-based
pipeline (including the Phase 8 results) remains the reference implementation
until a swap is proven on evidence. The phase ends with a recommendation:
continue, kill, or defer.

## Why WS4

- WS4 skips the LP matrix build and runs a goal-programming metaheuristic
  search (simulated annealing, tabu, ILS) instead. The current stack builds
  sizeable continuous LPs (the principal `cum_offer` rows alone are ~8.1M
  nonzeros at 1,608 cohorts x 100 years), and solve time dominates the
  pipeline runtime. Note the corrected premise: a WS4 dataset build/ingest
  step is still required — see the assessment.
- WS4 is spatially explicit: fragments are the decision units and blocks are
  a runtime aggregate, with adjacency, patch structure, road access, and mill
  deliveries enforced during simulation/optimization rather than allocated
  afterwards. Patch size distribution targets, roads, and mill deliveries
  could better capture net value creation per block than the WS3 schedule.
- A cleaner, maintained backend may serve the thesis defense and publication
  better than the current custom one-shot LP stack.

## Candidate integration paths to assess

Assess all three; the decision gate picks among them, or kills the phase.

- **(a) Drop-in schedule backend.** WS4 supplies the schedule (replacing the
  `ws3-run` solve inside the pipeline); the principal/agent LPs stay.
  Smallest code impact. Risk: WS4 schedules may not expose the per-cohort
  ceilings and entity mapping the LPs consume today.
- **(b) Offer/take-leave block formulation.** The principal offers and the
  agent accepts/declines ENTIRE blocks (integer/combinatorial choices), with
  WS4's heuristic (or a custom simulated-annealing layer) handling the
  combinatorics. May require replacing the LP solver for the coupled stage;
  changes the formulation from continuous to combinatorial and must be
  justified for the thesis.
- **(c) Full swap.** Principal/agent decision logic expressed as WS4-side
  wrappers (e.g., subsidy-dependent acceptance rules plus programmatic
  re-runs / bisection), retiring the custom LPs. Cleanest long-term story if
  the gate passes; depends on WS4 exposing objective/constraint hooks or on a
  robust external driver.

## Child tasks (scoping refined after the feasibility brief)

- **P9.1 WS4 intake and API smoke.** Install WS4 at the reviewed commit,
  reproduce its demo, and document the CLI/Python API/config surface, input
  formats, and performance baseline. On the TSA29 case specifically: ingest
  the legacy matrix plus fragments (or build from the ForestModelXML recipe
  plus fragments), and require `ws4 validate` to be clean; write the
  crosswalk note, including the resolved coverage of the 2903xxx (H
  site-index) AU band and the split-block track rule; run one `ws4 simulate`
  smoke; record wall time and peak RSS. Inventory what is missing —
  especially the spatial layers for patch targets, roads, and mills —
  without substituting stubbed or fabricated data.
- **P9.2 Schedule-only bridge proof of concept.** Produce a WS4 schedule for
  the TSA29 case **mirroring the WS3 target set** (the `aac_annual_m3` level
  under the configured even-flow tolerance), comparable/mappable to a
  `ws3-run` schedule. Measure level reachability and record iteration/scale
  numbers (iterations to converge, per-iteration cost, memory, candidate
  count). State explicitly whether the WS3 level is attainable. Document the
  entity/format mapping, period semantics, and even-flow/AAC handling;
  list gaps and the required data plumbing.
- **P9.3 Coupled experiment.** Smallest meaningful coupling: a subsidy scan
  where block acceptance follows take-or-leave rules driven by the same
  economics, using programmatic WS4 runs plus a driver (e.g., bisection).
  Compare outcomes and feasibility against the ws3+LP pipeline (e.g., the
  single-pass reference results). Assess whether WS4 exposes
  objective/constraint hooks or whether policy logic must live outside.
  Gated on the P9.x checkpoint.
- **P9.4 Evaluation and decision gate.** Capability matrix vs fresh-salvage
  needs; runtime/simplicity assessment; per-path code-impact estimate; a
  continue/kill/defer recommendation with evidence; thesis/publishing
  implications. Gated on the P9.x checkpoint.
- **P9.5 Topology and roads data assessment for TSA29.** Can adjacency and
  road/mill data be built for ~422k blocks, at what cost, with what missing
  rates? Recorded as its own child issue; informs the checkpoint's spatial
  half but does not gate P9.1/P9.2.

## Decision gate (continue only if)

- WS4 ingests TSA29-comparable inputs and yields usable schedules.
- Performance is competitive with the current stack.
- A coupling path exists without unacceptable hacks or data fabrication.
- The result can be explained and defended as cleanly as the current stack.

## Checkpoint recommendation

Run **P9.1 and P9.2 only** first (~4-6 person-days), then hold a checkpoint on
the parent issue before any coupling work (P9.3/P9.4). The checkpoint asks two
questions that can kill the phase on their own — **can WS4 reach the WS3
level on TSA29, and are the scale numbers tolerable?** — and records the
level/scale evidence, the crosswalk audit, and the 2903xxx AU coverage
answer. If either question fails, close the phase with the evidence rather
than proceeding; defer is the default outcome for merely incomplete
evidence. Full detail and kill criteria: `planning/phase9-ws4-assessment.md`.

**Status (2026-10-19): executed.** P9.1/P9.2 ran in a clean-room spike; the
checkpoint is held on #40 and both questions pass on the aspatial surface
(scale: ~30 min single-core per full 100-yr mirror, 3.56 GB peak; level:
9/10 WS3 floors, p1 at 96.8%, terminal liquidation 100.0%). The next
decision point is P9.3 and/or P9.5; the 2903xxx coverage question and the
crosswalk audit are answered in `planning/phase9-ws4-scale-test-results.md`.

## Known constraints and risks

- Current thesis numbers are WS3-based; a swap invalidates comparability
  unless the bridge is carefully specified and the evidence re-run.
- Data compatibility: Woodstock-style WS3/femic bridge outputs vs WS4 inputs
  (fragment shapefiles, XML build recipes) is unknown.
- Spatial dataset availability for patch size targets, roads, and mill
  deliveries at TSA29 scale.
- WS4 maturity, license, and support status.
- Hidden WS3-specific assumptions in fresh-salvage's couplings: per-cohort
  ceilings, schedule entity mapping, even-flow/AAC handling, period
  semantics.
- **Scale and candidates.** ~421,945 blocks. The ~27.7M candidate-chain
  estimate was **corrected by measurement to 15.4M chains** (17.7 s /
  2.29 GB enumeration on the full estate; WS4's benchmark sample is 16,174
  fragments / 3,183 blocks). See the results note.
- **Soft constraints.** WS4 cannot express hard constraints: even-flow and
  level bands are weighted penalties, so "reaching the level" is an outcome
  to be measured, not a guarantee. P9.2 measured it: the level is reached
  aspatial (9/10 floors; residual closable at the recorded tail rate) —
  still an outcome, not a guarantee.
- **Patch-criterion gap.** A patch criterion can only be a feature in WS4,
  so 28 of the reference's 63 patch targets run and silently produce all-zero
  cells (WS4's own backlog). Do not treat patch targets as usable evidence.
- **Inert `--time-limit`.** The WS4 `--time-limit` option is accepted but not
  read by the search loop; bound runs by iteration/start/island settings and
  record them.
- **Path (b) repo-contract conflict.** The block-integer take-or-leave path
  contradicts AGENTS.md ("continuous HiGHS LPs, no binaries, no thresholding
  or rounding") and cannot start without an explicit maintainer-approved
  contract amendment.

## Findings

**P9.1/P9.2 evidence recorded (2026-10-19).** The aspatial full-estate scale
test answers both checkpoint killer questions — scale and level both pass —
with the measured record in `planning/phase9-ws4-scale-test-results.md`
(environment, ingest/validate, simulate, candidates 15.4 M measured vs
~27.7 M estimated, optimizer runs, the WS3-mirror level results, cleared
kill criteria, and the portability gotchas). Task issues #41 and #42 are
closed; #40 holds the checkpoint. P9.3–P9.5 remain open; the next decision
point is P9.3 and/or P9.5.

Feasibility-brief summary (full detail:
`planning/phase9-ws4-assessment.md`):

- **WS4 is real and released.** Apache-2.0, `0.1.0` (2026-10-15), twelve
  phases with Phase 12 active, reviewed at `b380c128`. It is a weighted
  goal-programming search over soft targets (SA/tabu/ILS), not an LP:
  no optimality guarantee, no hard constraints, annual simulation with
  `period_width` accounting, block-named schedules.
- **TSA29 plumbing is now proven at the aspatial surface.** Both ingest
  formats exist (legacy matrix and ForestModelXML plus fragments); the
  legacy matrix + portions-based fragments path ingests at full estate with
  `ws4 validate` clean, and the crosswalk (including the split-block portion
  rule and the 2903xxx AU coverage) is audited exact — see the results note.
  Topology is still empty and there are no road/mill tables or rates, so the
  spatial features that motivate WS4 remain inert on TSA29 until P9.5
  settles whether that data can be built.
- **Parity gaps to watch** (WS4's own backlog): the all-zero
  product-criterion patch targets and the mill-destination policy
  difference; both are measured, both are Phase 12 work, neither is closed,
  and neither was exercised in the aspatial test.
- **Recommendation (unchanged in spirit)**: the checkpoint's level and scale
  questions pass, so the phase continues to the next decision point —
  P9.3 (coupled subsidy scan) and/or P9.5 (topology/roads) — before any
  coupling; path (b) stays out of contract until the maintainer decides.

The remaining findings — WS4 entry points actually exercised, TSA29 input
coverage, schedule mapping, coupled-experiment outcome, capability matrix,
runtime comparison, per-path code impact, and the continue/kill/defer
recommendation — are recorded from the P9.1-P9.4 evidence as those run:

- WS4 entry points, config surface, and input formats actually exercised
  (P9.1): **recorded** — `planning/phase9-ws4-scale-test-results.md` (the
  packaged synthetic example was exercised end-to-end through the Python
  API; the CLI `ingest`/`validate` paths were exercised on TSA29 at full
  estate).
- TSA29 input coverage: what fed the smoke run, what was missing, and what
  was stubbed rather than fabricated: **recorded** — the legacy matrix plus
  a portions-based fragments file fed every run; the spatial layers
  (topology, roads, mills) were absent and **nothing was stubbed or
  fabricated**; P9.5 owns whether they can be built.
- Schedule mapping evidence and gaps (P9.2): **recorded at the account
  level** — the WS3 volume quantity is like-for-like with the WS4 product
  account, and level/scale are answered. The block→cohort entity mapping
  and the per-cohort ceiling listener were not built: they are only
  required once a coupling path is selected and move to P9.3 scoping.
- Coupled-experiment outcome and comparison against the ws3+LP pipeline
  (P9.3): **open**.
- Capability matrix, runtime comparison, per-path code-impact estimate, and
  the continue/kill/defer recommendation with rationale (P9.4): **open**
  (gated on the next decision point; the checkpoint so far records the
  aspatial level/scale half).

## References

- WS4 repository: `https://github.com/UBC-FRESH/ws4`
- Feasibility assessment: `planning/phase9-ws4-assessment.md`.
- Roadmap phase: `ROADMAP.md`, "Phase 9: WS4 Backend Spike".
- Child issues: see the Phase 9 parent issue task list.
- TSA29 input instance: the `femic` repository's
  `external/femic-tsa29-instance` checkout (canonical standalone FEMIC TSA29
  instance; TIPSY/VDYP curves, input/output CSVs, and spatial data).
