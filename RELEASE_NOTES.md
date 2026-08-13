# Release Notes

## 0.1.0a1

`fresh-salvage` `0.1.0a1` is the first public alpha of the clean-reboot
linear principal-agent salvage-subsidy pipeline for the Williams Lake
Timber Supply Area (TSA29), British Columbia. The full model stack is
implemented: full-TSA ingestion, WS3 wood-supply scheduling, the principal
and agent continuous HiGHS LPs, the rolling-horizon coupling engine, and
the parallel scenario-ensemble driver.

### Features

- Full-TSA29 data ingestion (246,957 stands) from the external WL_VFSL
  polygon layer, with a scenario-visible burn-severity ladder, fatal
  unmatched-label guards, and coverage-scaled salvageable volume
  (FS-VAL-01/02 resolved).
- Full-TSA WS3 wood-supply layer: Landscape-Unit-free bridge rebuilt from
  the femic stage-1 Woodstock CSVs (aggregated to 1,608 midpoint-smashed,
  area-conserving cohort lines), `cc` clear-cut action operable at
  ages [60, 300], and the 2,937,509 m3/yr AAC ceiling.
- Principal LP: continuous offer fractions per cohort-year, maximizing
  stumpage net of subsidy minus MFRI-weighted expected burned-wood loss,
  under the green-volume AAC ceiling.
- Agent LP: continuous harvest/salvage fractions under annual MFRI-driven
  fire dynamics (harvest -> fire -> salvage -> decay), 0.85/yr burned
  inventory retention, salvage feasibility against the on-hand burned
  stock, no double selling, and 3% NPV discounting.
- Rolling-horizon engine: 10 decadal steps (100 implemented years) of
  15-period WS3 re-solves with period-0 inventory injection, principal and
  agent coupling, fire replay, and area-conserving cohort transitions.
- Ensemble driver: cartesian scenario grids over any named `RHRunConfig`
  axis (subsidy rate, burn-rate multiplier, the flat economic fields),
  executed in a spawn-based process pool with per-scenario failure
  isolation.
- Typer CLI with six implemented commands (`ingest`, `ws3-run`,
  `principal-run`, `agent-run`, `rh-run`, `ensemble-run`), deterministic
  `--json` output, and provenance manifests for every run.
- Calibrated, config-visible economic surface (BC-anchored prices, costs,
  and stumpage) with the rationale recorded in
  `planning/economics-calibration.md`.

### Headline Numbers

- ~150 s per 100-year rolling-horizon scenario on a 64-core host.
- 4.41x parallel speedup on the 4-scenario smoke ensemble; ~1,000
  scenarios in ~40 minutes at `max_workers: 64` / per-scenario
  `workers: 1`.
- 198 tests, `ruff` clean (`E`/`F`/`I`/`UP`/`W` at 100 columns), Python
  >= 3.11.
- Calibrated economics: unsubsidized salvage margin ≈ -15 $/m3; subsidy
  flip of the coupled system ≈ 19.2 $/m3 (FESBC benchmark support of
  14-15 $/m3 sits just below the turn-on).

### Known Limitations

- The burn-severity layer covers the 2025 fire season only; earlier burns
  carry no severity rating and seed no initial salvageable volume.
- WS3 step objectives shift slightly with the configured worker count
  (±0.014% observed); hold `workers` fixed for cross-run comparisons.
- Unsalvaged burned area resets to regeneration at the rolling-horizon
  step boundary; no burned-volume inventory carries across steps
  (documented deviation; the 0.85/yr decay keeps the truncated tail under
  20% of the unsalvaged volume).
- The economic parameters are semi-synthetic calibrated values; the
  per-parameter rationale and provenance labels live in
  `planning/economics-calibration.md`.
- The `export` CLI command remains a reserved stub.
- Public APIs are alpha and may change before a stable release.

### Verification

The release is expected to pass (the suite was green at the pre-docs HEAD
`e828dc6` and this entry accompanies docs-only changes):

- `python -m ruff check .` — clean.
- `python -m pytest` — 198 passed (with `PYTHONPATH` pointing at the ws3
  checkout).
- `sphinx-build -b html docs _build/html -W` — clean.

### Post-Release Notes

- **2026-08-13 — grade-transition monotonicity erratum.** Review caught a
  modeling error in the shipped `BURNED_GRADE_TRANSITION`: the Sawlog row's
  0.10 Sawlog->Peeler share was a physically impossible upgrade (fire can
  only degrade grade; hierarchy Peel > Saw > Pulp). Fixed on `main` to
  {Peel 0.00, Saw 0.80, Pulp 0.20}; rows remain downgrade-only and sum to
  1.0, so burned volume is conserved. Re-derived consequences versus the
  numbers quoted above: SPF transition-mix salvage margin -15.15 ->
  -21.06 $/m3, development-type mix -19.10 -> -23.86 $/m3, and the
  coupled-system subsidy flip ≈ 19.2 -> ≈ 23.9-24.1 $/m3 (turn-on 23.85,
  saturated by 24.1). The "Calibrated economics" headline above and any
  flip ≈ 19.2 $/m3 / margin ≈ -15 $/m3 statements elsewhere in this
  entry are superseded; `planning/economics-calibration.md` (erratum note)
  and `planning/phase6-validation-report.md` carry the current values.
