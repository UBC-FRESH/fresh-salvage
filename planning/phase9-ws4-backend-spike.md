# Phase 9 WS4 Backend Spike (Seed)

Status: seed — scoping refined after the feasibility brief (findings pending
P9.1-P9.4 evidence)

Date: 2026-10-18

> **Seed version.** This note scopes the Phase 9 spike. The kickoff framing
> has been refined from the WS4 feasibility review, whose condensed record is
> `planning/phase9-ws4-assessment.md`; the premises it corrected (a dataset
> build step remains; capability superset does not imply semantic parity;
> spatial features are inert on TSA29 until topology/roads data exist) are
> carried into the task scoping below. Statements about WS4 capabilities
> remain hypotheses until P9.1-P9.4 record observed-run evidence. Do not cite
> numbers from this note in the thesis or any publication.

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
- **Scale and candidates.** ~421,945 blocks and an estimated ~27.7M
  candidate chains at TSA29 scale; nothing has been measured at that scale
  (WS4's benchmark sample is 16,174 fragments / 3,183 blocks).
- **Soft constraints.** WS4 cannot express hard constraints: even-flow and
  level bands are weighted penalties, so "reaching the level" is an outcome
  to be measured, not a guarantee.
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

## Findings (to be completed)

Feasibility-brief summary (full detail:
`planning/phase9-ws4-assessment.md`):

- **WS4 is real and released.** Apache-2.0, `0.1.0` (2026-10-15), twelve
  phases with Phase 12 active, reviewed at `b380c128`. It is a weighted
  goal-programming search over soft targets (SA/tabu/ILS), not an LP:
  no optimality guarantee, no hard constraints, annual simulation with
  `period_width` accounting, block-named schedules.
- **TSA29 plumbing is promising but unproven.** Both ingest formats exist
  (legacy matrix and ForestModelXML plus fragments). The crosswalk exactness
  (including the split-block track rule and the 2903xxx AU coverage
  question) is the top unknown; topology is empty; there are no road/mill
  tables or rates, so the spatial features that motivate WS4 are inert on
  TSA29 today. P9.5 scopes whether that data can be built.
- **Parity gaps to watch** (WS4's own backlog): the all-zero
  product-criterion patch targets and the mill-destination policy
  difference; both are measured, both are Phase 12 work, neither is closed.
- **Recommendation: run P9.1 + P9.2 first (~4-6 person-days) and stop at
  the checkpoint** if the level or scale questions fail; P9.3/P9.4 stay
  gated, and path (b) stays out of contract until the maintainer decides.

The remaining findings — WS4 entry points actually exercised, TSA29 input
coverage, schedule mapping, coupled-experiment outcome, capability matrix,
runtime comparison, per-path code impact, and the continue/kill/defer
recommendation — are recorded from the P9.1-P9.4 evidence as those run:

- WS4 entry points, config surface, and input formats actually exercised
  (P9.1).
- TSA29 input coverage: what fed the smoke run, what was missing, and what
  was stubbed rather than fabricated.
- Schedule mapping evidence and gaps (P9.2).
- Coupled-experiment outcome and comparison against the ws3+LP pipeline
  (P9.3).
- Capability matrix, runtime comparison, per-path code-impact estimate, and
  the continue/kill/defer recommendation with rationale (P9.4).

## References

- WS4 repository: `https://github.com/UBC-FRESH/ws4`
- Feasibility assessment: `planning/phase9-ws4-assessment.md`.
- Roadmap phase: `ROADMAP.md`, "Phase 9: WS4 Backend Spike".
- Child issues: see the Phase 9 parent issue task list.
- TSA29 input instance: the `femic` repository's
  `external/femic-tsa29-instance` checkout (canonical standalone FEMIC TSA29
  instance; TIPSY/VDYP curves, input/output CSVs, and spatial data).
