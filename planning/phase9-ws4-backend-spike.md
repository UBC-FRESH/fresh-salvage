# Phase 9 WS4 Backend Spike (Seed)

Status: seed — kickoff (findings to be completed)

Date: 2026-10-18

> **Seed version.** This note scopes the Phase 9 spike from the kickoff
> framing. The **Findings** section at the end is a placeholder to be
> completed from the in-flight WS4 feasibility brief and from the P9.1-P9.4
> evidence. Every statement about WS4 capabilities below is a hypothesis to
> verify, not an established fact. Do not cite numbers from this note in the
> thesis or any publication.

## Purpose

Phase 9 evaluates whether WS4 (`https://github.com/UBC-FRESH/ws4`) can
replace the WS3 wood-supply backend — and possibly the custom principal/agent
HiGHS LPs — for `fresh-salvage`.

This is a **spike with a decision gate, not a big-bang swap**. The WS3-based
pipeline (including the Phase 8 results) remains the reference implementation
until a swap is proven on evidence. The phase ends with a recommendation:
continue, kill, or defer.

## Why WS4

- WS4 reportedly skips the LP matrix build and runs a heuristic search
  (simulated annealing). The current stack builds sizeable continuous LPs
  (the principal `cum_offer` rows alone are ~8.1M nonzeros at 1,608 cohorts
  x 100 years), and solve time dominates the pipeline runtime.
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

## Child tasks (seed scoping; refine after the feasibility brief)

- **P9.1 WS4 intake and API smoke.** Install WS4, reproduce its demo, and
  document the CLI/Python API/config surface, input formats, and performance
  baseline on a TSA29-scale case. Identify which TSA29 inputs (from the local
  `femic-tsa29-instance` FEMIC checkout) can feed WS4 and what is missing —
  especially the spatial layers for patch targets, roads, and mills.
- **P9.2 Schedule-only bridge proof of concept.** Produce a WS4 schedule for
  the TSA29 case comparable/mappable to a `ws3-run` schedule. Document the
  entity/format mapping, period semantics, and even-flow/AAC handling. List
  gaps and the required data plumbing.
- **P9.3 Coupled experiment.** Smallest meaningful coupling: a subsidy scan
  where block acceptance follows take-or-leave rules driven by the same
  economics, using programmatic WS4 runs plus a driver (e.g., bisection).
  Compare outcomes and feasibility against the ws3+LP pipeline (e.g., the
  Phase 8 single-pass baseline). Assess whether WS4 exposes
  objective/constraint hooks or whether policy logic must live outside.
- **P9.4 Evaluation and decision gate.** Capability matrix vs fresh-salvage
  needs; runtime/simplicity assessment; per-path code-impact estimate; a
  continue/kill/defer recommendation with evidence; thesis/publishing
  implications.

## Decision gate (continue only if)

- WS4 ingests TSA29-comparable inputs and yields usable schedules.
- Performance is competitive with the current stack.
- A coupling path exists without unacceptable hacks or data fabrication.
- The result can be explained and defended as cleanly as the current stack.

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

## Findings (to be completed)

Placeholder — to be completed from the in-flight WS4 feasibility brief and
the P9.1-P9.4 evidence. Record, at minimum:

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
- Roadmap phase: `ROADMAP.md`, "Phase 9: WS4 Backend Spike".
- Child issues: see the Phase 9 parent issue task list.
- TSA29 input instance: the `femic` repository's
  `external/femic-tsa29-instance` checkout (canonical standalone FEMIC TSA29
  instance; TIPSY/VDYP curves, input/output CSVs, and spatial data).
