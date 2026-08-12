# Examples

This directory holds public-safe example fixtures and configuration
templates, one per CLI command:

- `scenario_tsa29.yaml` — full-TSA ingestion (`fresh-salvage ingest`).
- `ws3_tsa29.yaml` — full-TSA WS3 schedule solve (`fresh-salvage ws3-run`,
  with a `--smoke` fast path).
- `principal_tsa29.yaml` — principal offer LP (`fresh-salvage
  principal-run`).
- `agent_tsa29.yaml` — agent harvest/salvage LP (`fresh-salvage agent-run`).
- `rh_tsa29.yaml` — 100-year rolling-horizon coupled run
  (`fresh-salvage rh-run`).
- `ensemble_tsa29.yaml` — 4-scenario smoke ensemble grid
  (`fresh-salvage ensemble-run`).

The configs point at machine-specific input paths (the WL_VFSL polygon
layer, the validated femic TSA29 WS3 bridge, and the run outputs of earlier
commands); replace them with your own paths before running. Predecessor
data files are never vendored in this repository.
