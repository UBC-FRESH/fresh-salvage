# Phase 9 WS4 Aspatial Scale Test — Measured Results

Status: evidence recorded — P9.1/P9.2 complete (issues #41, #42 closed);
**evidence for the 2026-10-19 no-go decision** (keep WS3 — see the Decision
section of `planning/phase9-ws4-backend-spike.md`).

Date: 2026-10-19

Parent issue: #40. Companion notes: `planning/phase9-ws4-backend-spike.md`
(scoping) and `planning/phase9-ws4-assessment.md` (pre-evidence assessment,
which this note now corrects where the estimates were measured).

This note is the sanitized record of the full-TSA29 **aspatial** scale test
of WS4 — the P9.1/P9.2 evidence for the two checkpoint questions that can
kill the phase ("can WS4 reach the WS3 volume level at TSA29 scale?" and
"are the scale numbers tolerable?"). All heavy runs executed in a
clean-room spike workspace (untracked; the full raw report, scripts, logs,
and run outputs live there) against read-only checkouts: WS4 at `b380c128`,
the femic TSA29 instance, and this repository. Nothing in any repository was
modified, and no WS4 code or data is vendored here. Timings are single-core
wall times (every run pinned to four cores, one worker) on a shared 72-core
host under heavy load (~30–45 load average); ws4's own performance notes
require reading timings together with the load, so treat these as
conservative.

## 1. Environment and provenance

- WS4 `0.1.0`, installed editable from a **copy** of the reviewed
  `b380c128` checkout (`pip install -e ".[geo,opt]"`, Python 3.12.3 venv):
  49 s, exit 0, no dependency conflicts; the upstream clone stayed clean.
- Dependency resolution: pandas **3.0.6**, numpy **2.5.4**, numba 0.68.0,
  ray 2.59.0, geopandas 1.2.0, scipy 1.18.1, pydantic 2.14.1.
- **Flag — versions unverified against WS4 CI.** WS4 declares lower bounds
  only (`pandas>=2.2`, `numpy>=1.26`), so this environment is newer than
  anything upstream CI records. Every exercised path (build, ingest,
  validate, simulate, candidates, optimizer, reports) ran clean on it, but
  these versions must be pinned and treated as unverified before any adapter
  work.
- Environment proof: WS4's own smallest example (packaged synthetic
  dataset) passes the full `build_canonical_dataset → Model.from_dataset →
  Simulator.simulate → Optimizer.run` API chain end-to-end (`status: ok`,
  objective 0.0; 2.0 s / 340 MB).

## 2. Input and the TRACK derivation rule (fragments → track portions)

The input is the femic instance's **legacy compiled matrix** plus a prepared
fragments file, because the WS4 legacy ingest path needs a fragment row with
an owned track and the fragments layer carries no `TRACK` column. The matrix
splits 224,086 blocks into managed/unmanaged rows (646,031 matrix rows for
421,945 distinct blocks). The derivation rule applied was:

- **One fragment per matrix portion** — `(BLOCK, TRACK)` with the portion's
  own AREA and the block's F_AGE. Result: **646,031 fragment-portions**
  over 421,945 blocks; the portion split reproduces the matrix's own area
  accounting exactly (managed 1,218,310.5 ha; unmanaged 2,567,681.6 ha;
  total 3,785,992.2 ha).
- **BLOCK-ascending order with the managed portion FIRST within each
  block** — required, not cosmetic: `Optimizer.candidates()` takes the
  *first* fragment's track per block (`grouped["track_id"].first()`).
  Verified first-row-managed for every block that has a managed portion
  (421,945/421,945 checks). With the source file's unmanaged-first order,
  **224,086 blocks (1,218,311 managed ha) would have drawn candidates from
  a treatment-less track and been unharvestable.**
- **The one-fragment-per-block alternative was rejected.** Putting the
  whole block on the managed track would read 2,564,842 ha of
  managed-track area against the matrix's 1,218,310 ha portion basis
  (+110% overstatement); 197,859 unmanaged-only blocks stay unmanaged
  either way.
- **Exactness checks:** portion TYPE ⇔ track-strata IFM on all 646,031
  rows; `OFFSET == F_AGE` everywhere; portion-area sums match the DBF
  within source rounding (max 0.0082 ha on 265,827 blocks, mean
  1.3e-5 ha); managed tracks are exactly the 210 treated tracks (each one
  CC treatment, windows beginning in [70, 250]); 4 of 524 matrix tracks
  carry no blocks.
- **2903xxx (H site-index) AU coverage resolved:** all 54 AUs present in
  both fragments and strata; the 2903 band is fully covered (151,768
  blocks / 243,381 portions / 1,319,780 ha, 34.9% of the estate).
- **Volume-quantity check vs WS3:** WS3's raw bridge `totvol` and WS4's
  `product.HarvestedVolume.managed.Total.CC` are the same curve family in
  the same units (checked at AU 2902002, age 275: ~201.1 m³/ha both
  sides; WS3's objective applied a 0.85 utilization factor on top), so the
  levels below are like-for-like comparisons. Maturity context: 84,954
  managed portions (410,294.7 ha) are operable at year 0; the static year-0
  yield potential on mature portions is 81.3 Mm³.

## 3. Stage table — full estate

| Stage | Wall (single core) | Peak RSS | Result |
| --- | --- | --- | --- |
| `ws4 ingest` | 2.0 s | 591 MB | 12 tables + manifest; 646,031 fragment-portions / 421,945 blocks |
| `ws4 validate` | 1.4 s | 539 MB | `Dataset is valid` |
| Simulate, 30 y empty | 3.25 s | 705 MB | — |
| Simulate, 100 y empty | 8.28 s | 823 MB | `feature.Area.managed` = **1,218,310.51 ha** exactly |
| Candidates, full estate | **17.7 s** | **2.29 GB** | **15,426,141 chains**; 212,011 blocks with chains |
| Optimizer, prime run | **1,809 s** | 3.56 GB | 350k iterations; 9/10 floors; p1 85.3%; p10 100.0% |
| Optimizer, mirror run | **1,817 s** | 3.56 GB | 350k iterations; 9/10 floors; p1 96.8%; band met every period |

Dataset counts: fragments 646,031; block rows 646,031 (portion rows);
tracks 524; strata 524; treatments 210 (all CC); curves 15,910 rows
(655 curve ids); features 3,144; products 630; accounts 117 (+117
protoaccounts); groups 421,945. A full-horizon (100 y) full-estate replay
costs ~8–10 s single-core — the baseline unit for the optimizer's base
values and the final report.

## 4. Candidates

- Full estate: 15,426,141 chains (212,011 blocks; 2–113 chains/block, mean
  72.8), enumerated in 17.7 s single-threaded at 2.29 GB peak.
- **The earlier ~27.7 M-chain estimate did not hold**: WS4's candidate
  builder caps chains per block (`_MAX_CHAINS=320`, sampled rotations);
  the measured count is 56% of the estimate. Enumeration is repeated once
  inside a search and is **not a bottleneck**.
- 12,075 managed blocks have no chain at all (their CC window opens more
  than 100 years after their current age); the 197,859 unmanaged-only
  blocks are chain-less by construction.
- Subsample calibration on the same estate (5 k and 50 k blocks) shows
  per-iteration cost flat in estate size (~0.5 ms warm; 2,162 it/s at
  50 k blocks) with the non-flat costs in load, enumeration, and base
  values (337,987 / 3,254,177 chains respectively).

## 5. Optimizer results (SA, seed 42, 350,000 iterations)

Config for both reported runs: simulated annealing (geometric), seed 42,
1 start, `workers=1`, `islands=0`, `lns_weight=0` (the default 0.2 is
prohibited at this scale — each LNS move touches ~10% of the schedule),
100-year horizon, no topology/route/patch config. Throughput starts near
450 iterations/s and decays toward ~140–150 iterations/s as the schedule
fills; end-to-end at full estate the kernel is ~5 ms/iteration.

**Mirror target set (WS3 basis).** Floors are the raw per-decade volumes of
the WS3 10-period gate solve (5.5116, 4.9605×5, 6.0628×3, 6.0635, 34.4866
Mm³; sum 82.99 Mm³). The ceiling is the AAC (2,937,509 m³/yr × 10 ÷ 0.85 =
**34.5589 Mm³ raw per decade**), consistent with the unit check above.
Even flow mirrors WS3's `cflw_e` (each period ≥ 90% of period 1, reference
period 1, one-sided — no upper bound, as in WS3).

- **Prime run (floors + ceilings only):** 1,809 s; 94,701 blocks scheduled
  (95,485 harvest events); 9/10 floors met. p1 (years 1–10) = 85.3% of the
  floor; p10 = 34,492,343 m³ = **100.0%** of the WS3 terminal level and
  99.8% of the ceiling; total 89.2 Mm³ (107% of WS3). Trajectory after the
  run: residual closing at ~39 m³ per 1,000 iterations at the cut.
- **Mirror run (floors + ceilings + even-flow band):** 1,817 s; 94,677
  blocks scheduled; 9/10 floors met, **p1 = 96.8%** (5,334,427 /
  5,511,612 m³); the even-flow band is met in every period (−6% to +78%
  over p1); every ceiling met; p10 = **100.0%** of the WS3 terminal level
  (99.8% of the ceiling); total 90.5 Mm³ (109% of WS3). The single
  residual is p1's last 0.18 Mm³, still closing at ~40 m³/iteration ⇒
  **~355k–400k iterations (~30–35 min single-core) estimated for full
  convergence** on this estate.
- The even-flow term acted as a mild spread force: the mirror beat the
  prime run on p1 at the same budget. The realized profile grows after p5;
  that satisfies both one-sided bands but is not the WS3 *shape* — neither
  formulation has an upper flow bound, so a flat-profile variant would need
  an explicit upper band (not run).
- **Determinism:** two separate processes hold bit-identical objective
  values on all 148,018 common iterations (max |Δ| = 0.0); reproducibility
  is per seed/config, as WS4 documents.
- Reachability context: the WS3 floors sit well inside the physical
  envelope (static per-decade maxima 92.2–220.3 Mm³ from the matrix's own
  curves); what is hard is the joint timing of the harvests, not the total
  volume — and the first decade (with the largest early floor relative to
  mature area) converges last.

## 6. Verdicts

- **Scaled: yes, no blocking scale issue.** Ingest/validate are seconds;
  the 100-yr replay is ~8–10 s; 15.4 M chains enumerate in 17.7 s / 2.29 GB;
  a full 350k-iteration search stays at 3.56 GB peak. The scale
  kill-criterion (hours per step, memory blowup) is **not triggered**.
- **Level reached: yes (aspatial), with a measured whisker.** The full
  mirror reaches 9/10 WS3 per-decade floors including the 34.5 Mm³ terminal
  liquidation, under the AAC-derived ceiling and the even-flow band, with a
  single measured residual of 0.18 Mm³ in the first decade that closes at
  the measured tail rate within another ~5k–50k iterations. The
  level-reachability kill-criterion is **not triggered**.
- **Cost: ~181 s per decade-step single-core as run** (1,817 s / 10
  decades for the full 100-yr mirror), versus the WS3 rolling step's ~11 s
  (build+solve, 15-period horizon, 64 cores) and the ~62 s full
  single-pass run — roughly 16× the rolling step *as configured* (single
  worker, no islands; WS4's own docs claim near-linear island scaling,
  untested here). Slower, but not hours — a phase-gate cost question, not a
  scale blocker.

## 7. Kill-criteria status (checkpoint)

- Scale kill-criterion (iteration/scale numbers make a 10-period run
  infeasible): **not triggered** — a full 100-yr mirror runs in ~30 min
  single-core within 3.6 GB.
- Level-reachability kill-criterion: **not triggered** — the WS3 volume
  level is reachable at full estate aspatial scale (measured above).
- Crosswalk kill-criterion (systematic id/area mismatches): **not
  triggered** — the portion rule reconciles exactly (section 2); the
  crosswalk audit found no unmatched blocks or areas.
- The remaining kill criteria concern the soft-constraint reading and the
  measured parity gaps (patch criterion, mill destinations) — unchanged,
  and still P9.4/P9.5 territory.

## 8. Contradictions and corrections to the feasibility estimate

- "~27.7 M candidate chains": measured **15.4 M** (WS4 caps chains per
  block at 320 with sampled rotations).
- Candidate memory is not a problem: 2.29 GB for the whole enumeration,
  3.56 GB peak for a full run.
- "Schedule search may not reach the level": it reaches it, at ~30 min
  single-core for this horizon and budget (recorded above).
- `ws4 validate` accepts the instance's real matrix + fragments with no
  repairs, and the ingest is ~2 s — the P9.1 acceptance bar.
- The first-decade convergence spill (p1 converges last) is an operator-mix
  artifact worth noting: random-year single-block moves fill decades
  roughly in proportion to their shortfall, so the decade with the largest
  early floor closes last.

## 9. Portability and operation gotchas (for any future driver)

- **`time_limit` is never enforced.** `OptimizeConfig.time_limit` is
  recorded but no code path reads it; bound runs by iteration/start/island
  budgets and kill externally if needed.
- **Outputs flush only at the end.** `Optimizer.run()` and the writers
  write the incumbent only when the run finishes; an external kill loses
  the schedule (two such attempts occurred). Any production driver must
  add incremental checkpointing rather than relying on the tool.
- **Disable `lns_weight` at this scale.** The default 0.2 makes each LNS
  move touch ~10% of the schedule and is prohibitively expensive at full
  estate; the reported runs use toggle/shift/swap only (`lns_weight=0`).
- **Snap-packaged tooling confinement (harness gotcha).** The host's
  snap-packaged GitHub CLI cannot read the shared checkout tree or
  `~/.cache`; files staged for it (issue bodies/comments) must live under
  `$HOME` and the tool must run with its outputs attached to pipes/pty.
  This affected the recording harness only, not WS4.

## 10. What was not measured

- Anything spatial (patch targets, roads, mills, green-up): deliberately
  out of scope; no topology/route config was passed. P9.5 owns whether the
  layers can be built.
- Multi-island / multi-process scaling (capped at 1 worker by directive);
  ws4's near-linear island claims are untested here.
- True uncontended timings and full convergence to objective 0 (the last
  0.18 Mm³ is an extrapolation from the measured tail, not a completed
  run).
- The WS4→fresh-salvage **entity mapping** (block→cohort aggregation,
  per-cohort ceiling listener) and the entity-level schedule comparison:
  the checkpoint's level/scale questions were answered first, and the
  mapping is only required once a coupling path is selected — it moves to
  P9.3 scoping.
- pandas 3.x / numpy 2.5 compatibility with WS4's upstream CI (all
  exercised paths clean; unverified upstream — pin before adapter work).

## Evidence trail

- Task issues #41 (P9.1) and #42 (P9.2) carry the measured-run comments;
  the raw report, scripts, logs, and run outputs remain in the untracked
  spike workspace.
- The assessment's kill/viability section and the spike note's findings
  point back to this record; `ROADMAP.md` marks P9.1/P9.2 complete with
  P9.3/P9.5 as the next decision point.
