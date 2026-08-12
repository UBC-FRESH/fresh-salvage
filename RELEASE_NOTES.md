# Release Notes

## 0.1.0a1

`fresh-salvage` `0.1.0a1` is the initial scaffold of the clean-reboot
linear principal-agent salvage-subsidy pipeline. It establishes the package
skeleton, governance, planning contract, docs, automation, and stub CLI
commands. No model logic is implemented in this release.

### Implemented

- Package-backed Python project using `src/` layout and versioned package
  metadata.
- Typer CLI with `fresh-salvage --version`, `--help`, and stub
  commands `ingest`, `ws3-run`, `solve-principal`, `solve-agent`, `rh-run`,
  and `export`.
- Module stubs for `models`, `data`, `ws3`, `principal`, `agent`, `fire`,
  `rolling_horizon`, and `io`.
- Refactor contract in `planning/phase0-refactor-contract.md`.
- Sphinx documentation, GitHub Pages deployment workflow, CI, release-artifact
  workflow, roadmap, changelog, and public contribution/governance files.

### Explicit Limitations

- Model logic is intentionally absent; every stub command exits with a
  not-implemented diagnostic.
- The package does not ingest predecessor data, compile WS3 schedules, solve
  LPs, simulate fire, or run a rolling horizon.
- Predecessor data files are not vendored in this repository.
- Public APIs remain alpha and may change before a stable release.

### Verification

The release is expected to pass:

- `python -m pip install -e .[dev]`
- `python -m ruff check .`
- `python -m pytest`
- `fresh-salvage --help`
