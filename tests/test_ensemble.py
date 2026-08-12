"""Ensemble driver tests.

Grid expansion, failure isolation, output isolation, manifest schema, and
CLI wiring run against a mocked ``rh.run_rh`` (driver-logic tests). One real
end-to-end path (2 scenarios x 1 step at horizon 3, process pool included)
needs the ws3 package and the local TSA29 inputs; it skips cleanly when
unavailable.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fresh_salvage import ensemble, rh
from fresh_salvage.cli import app
from fresh_salvage.models import (
    EnsembleConfig,
    EnsembleManifest,
    EnsembleResult,
    RHResult,
    ScenarioRecord,
)

runner = CliRunner()


def _base(tmp_path: Path) -> dict[str, object]:
    """Minimal shared base: placeholder input files (provenance digests them)."""

    stands = tmp_path / "stands.parquet"
    stands.write_bytes(b"stands")
    yields = tmp_path / "yields.csv"
    yields.write_text("curve,age,volume\n", encoding="utf-8")
    bridge = tmp_path / "bridge"
    bridge.mkdir(exist_ok=True)
    (bridge / "femic_tsa_ws3.are").write_text("*A 29 m 1 sbps_pli 1 5 1.0\n")
    return {
        "stands_path": str(stands),
        "yields_path": str(yields),
        "bridge_path": str(bridge),
        "horizon": 3,
        "steps": 1,
        "workers": 1,
    }


def _config(
    tmp_path: Path,
    axes: dict[str, list[object]],
    *,
    max_workers: int = 1,
    base: dict[str, object] | None = None,
) -> EnsembleConfig:
    return EnsembleConfig(
        ensemble_id="test-ensemble",
        base=_base(tmp_path) if base is None else base,
        axes=axes,
        max_workers=max_workers,
        output_root=tmp_path / "ensemble",
    )


def _stub_rh_result(run_id: str) -> RHResult:
    return RHResult(
        run_id=run_id,
        status="optimal",
        steps=1,
        horizon=3,
        period_length=10,
        cohorts=7,
        wall_seconds=0.25,
    )


# --- grid expansion ----------------------------------------------------------


def test_expand_scenarios_cartesian_order_and_overrides(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        {"subsidy_rate_per_m3": [0.0, 3.0], "burn_rate_multiplier": [1.0, 2.0]},
    )

    specs = ensemble.expand_scenarios(config)

    assert [spec.name for spec in specs] == [
        "burn_rate_multiplier-1.0__subsidy_rate_per_m3-0.0",
        "burn_rate_multiplier-1.0__subsidy_rate_per_m3-3.0",
        "burn_rate_multiplier-2.0__subsidy_rate_per_m3-0.0",
        "burn_rate_multiplier-2.0__subsidy_rate_per_m3-3.0",
    ]
    # Overrides land on the matching RHRunConfig fields; no cross-contamination.
    assert [spec.run_config.subsidy_rate_per_m3 for spec in specs] == [0.0, 3.0, 0.0, 3.0]
    assert [spec.run_config.burn_rate_multiplier for spec in specs] == [1.0, 1.0, 2.0, 2.0]
    assert [spec.overrides["subsidy_rate_per_m3"] for spec in specs] == [0.0, 3.0, 0.0, 3.0]
    # Base values flow through untouched.
    assert all(spec.run_config.horizon == 3 for spec in specs)
    assert all(spec.run_config.steps == 1 for spec in specs)
    # Driver-owned fields are assigned per scenario, distinct and isolated.
    assert len({spec.run_config.run_id for spec in specs}) == 4
    roots = [spec.run_config.output_root for spec in specs]
    assert len(set(roots)) == 4
    assert all(root.parent == tmp_path / "ensemble" for root in roots)


def test_expand_scenarios_empty_axes_yields_single_baseline(tmp_path: Path) -> None:
    specs = ensemble.expand_scenarios(_config(tmp_path, {}))

    assert len(specs) == 1
    assert specs[0].name == "baseline"
    assert specs[0].overrides == {}
    assert specs[0].run_config.subsidy_rate_per_m3 == 3.0
    assert specs[0].run_config.burn_rate_multiplier == 1.0


def test_expand_scenarios_rejects_unknown_axis(tmp_path: Path) -> None:
    config = _config(tmp_path, {"not_a_field": [1.0]})

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(config)

    assert excinfo.value.code == "ensemble_axis_unknown"


def test_expand_scenarios_rejects_unknown_base_key(tmp_path: Path) -> None:
    base = {**_base(tmp_path), "not_a_field": 1.0}
    config = _config(tmp_path, {"subsidy_rate_per_m3": [3.0]}, base=base)

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(config)

    assert excinfo.value.code == "ensemble_axis_unknown"


@pytest.mark.parametrize("reserved", ["run_id", "output_root"])
def test_expand_scenarios_rejects_reserved_fields(tmp_path: Path, reserved: str) -> None:
    in_axes = _config(tmp_path, {reserved: ["x"] if reserved == "run_id" else ["o"]})
    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(in_axes)
    assert excinfo.value.code == "ensemble_field_reserved"

    base = {**_base(tmp_path), reserved: "x"}
    in_base = _config(tmp_path, {"subsidy_rate_per_m3": [3.0]}, base=base)
    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(in_base)
    assert excinfo.value.code == "ensemble_field_reserved"


def test_expand_scenarios_rejects_empty_axis(tmp_path: Path) -> None:
    config = _config(tmp_path, {"subsidy_rate_per_m3": []})

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(config)

    assert excinfo.value.code == "ensemble_axis_empty"


def test_expand_scenarios_rejects_duplicate_scenario_names(tmp_path: Path) -> None:
    config = _config(tmp_path, {"subsidy_rate_per_m3": [3.0, 3.0]})

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(config)

    assert excinfo.value.code == "ensemble_duplicate_scenario"


def test_expand_scenarios_rejects_invalid_override_values(tmp_path: Path) -> None:
    config = _config(tmp_path, {"discount_rate": ["not-a-rate"]})

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.expand_scenarios(config)

    assert excinfo.value.code == "ensemble_scenario_invalid"


# --- driver: failure isolation, output isolation, artifacts -------------------


def test_run_ensemble_isolates_scenario_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_rh(config, verbose: bool = False) -> RHResult:
        if config.subsidy_rate_per_m3 == 0.0:
            raise rh.RHError("rh_test_boom", "forced failure for the no-subsidy case")
        return _stub_rh_result(config.run_id)

    monkeypatch.setattr(ensemble.rh, "run_rh", fake_run_rh)
    config = _config(tmp_path, {"subsidy_rate_per_m3": [0.0, 3.0, 6.0]})

    result = ensemble.run_ensemble(config)

    assert result.status == "partial"
    assert result.scenario_count == 3
    assert result.succeeded == 2
    assert result.failed == 1
    by_name = {record.name: record for record in result.scenarios}
    failed = by_name["subsidy_rate_per_m3-0.0"]
    assert failed.status == "failed"
    assert failed.error_code == "rh_test_boom"
    assert "forced failure" in (failed.error_message or "")
    assert by_name["subsidy_rate_per_m3-3.0"].status == "optimal"
    assert by_name["subsidy_rate_per_m3-6.0"].status == "optimal"
    # Records stay in deterministic grid order, failures included.
    assert [record.name for record in result.scenarios] == [
        "subsidy_rate_per_m3-0.0",
        "subsidy_rate_per_m3-3.0",
        "subsidy_rate_per_m3-6.0",
    ]


def test_run_ensemble_all_failed_reports_failed(tmp_path: Path, monkeypatch) -> None:
    def fake_run_rh(config, verbose: bool = False) -> RHResult:
        raise ValueError("always broken")

    monkeypatch.setattr(ensemble.rh, "run_rh", fake_run_rh)
    result = ensemble.run_ensemble(_config(tmp_path, {"subsidy_rate_per_m3": [0.0, 3.0]}))

    assert result.status == "failed"
    assert result.succeeded == 0
    assert {record.error_code for record in result.scenarios} == {"ValueError"}


def test_run_ensemble_writes_jsonl_and_manifest_in_grid_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ensemble.rh,
        "run_rh",
        lambda config, verbose=False: _stub_rh_result(config.run_id),
    )
    config = _config(
        tmp_path,
        {"subsidy_rate_per_m3": [0.0, 3.0], "burn_rate_multiplier": [1.0, 2.0]},
    )

    result = ensemble.run_ensemble(config)

    assert result.status == "ok"
    assert result.scenarios_path is not None and result.manifest_path is not None
    lines = result.scenarios_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    jsonl_names = [ScenarioRecord.model_validate_json(line).name for line in lines]
    assert jsonl_names == [record.name for record in result.scenarios]

    manifest = EnsembleManifest.read_json(result.manifest_path)
    assert manifest.manifest_version == "1.0"
    assert manifest.ensemble_id == "test-ensemble"
    assert manifest.status == "ok"
    assert manifest.scenario_count == 4
    assert manifest.succeeded == 4
    assert manifest.failed == 0
    assert manifest.max_workers == 1
    assert manifest.wall_seconds >= 0.0
    assert [record.name for record in manifest.scenarios] == jsonl_names
    # Provenance: grid config, shared inputs, and bridge files are digested.
    assert set(manifest.source_sha256) == {
        "grid_config",
        "stands",
        "yields",
        "bridge/femic_tsa_ws3.are",
    }
    assert all(len(digest) == 64 for digest in manifest.source_sha256.values())
    # The full grid config is embedded for provenance.
    assert manifest.config["axes"] == {
        "subsidy_rate_per_m3": [0.0, 3.0],
        "burn_rate_multiplier": [1.0, 2.0],
    }


def test_run_ensemble_rejects_missing_input_files(tmp_path: Path) -> None:
    config = _config(tmp_path, {"subsidy_rate_per_m3": [3.0]})
    Path(config.base["stands_path"]).unlink()

    with pytest.raises(ensemble.EnsembleError) as excinfo:
        ensemble.run_ensemble(config)

    assert excinfo.value.code == "ensemble_input_missing"


def test_ensemble_config_yaml_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "ensemble.yaml"
    config_path.write_text(
        "ensemble_id: yaml-ensemble\n"
        "base:\n"
        f"  stands_path: {tmp_path / 'stands.parquet'}\n"
        f"  yields_path: {tmp_path / 'yields.csv'}\n"
        f"  bridge_path: {tmp_path / 'bridge'}\n"
        "  horizon: 15\n"
        "  steps: 10\n"
        "axes:\n"
        "  subsidy_rate_per_m3: [0.0, 3.0]\n"
        "  burn_rate_multiplier: [1.0, 2.0]\n"
        "max_workers: 4\n"
        f"output_root: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    config = EnsembleConfig.read(config_path)

    assert config.ensemble_id == "yaml-ensemble"
    assert config.axes["subsidy_rate_per_m3"] == [0.0, 3.0]
    assert config.max_workers == 4
    specs = ensemble.expand_scenarios(config)
    assert len(specs) == 4
    assert specs[0].run_config.horizon == 15


# --- CLI wiring ----------------------------------------------------------------


def _stub_ensemble_result(tmp_path: Path) -> EnsembleResult:
    return EnsembleResult(
        ensemble_id="test-ensemble",
        status="ok",
        scenario_count=1,
        succeeded=1,
        failed=0,
        max_workers=1,
        wall_seconds=0.5,
        scenarios=[
            ScenarioRecord(
                name="baseline",
                run_id="test-ensemble-baseline",
                status="optimal",
                wall_seconds=0.5,
                output_root=tmp_path / "ensemble" / "baseline",
            )
        ],
        scenarios_path=tmp_path / "ensemble" / "data" / "test-ensemble-scenarios.jsonl",
        manifest_path=tmp_path
        / "ensemble"
        / "manifests"
        / "test-ensemble-ensemble-manifest.json",
    )


def test_cli_ensemble_run_help() -> None:
    result = runner.invoke(app, ["ensemble-run", "--help"])

    assert result.exit_code == 0
    assert "scenario ensemble" in result.stdout


def test_cli_ensemble_run_json_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "ensemble.yaml"
    _config(tmp_path, {"subsidy_rate_per_m3": [3.0]}).write_json(config_path)
    monkeypatch.setattr(
        "fresh_salvage.cli.ensemble.run_ensemble",
        lambda config, verbose=False: _stub_ensemble_result(tmp_path),
    )

    result = runner.invoke(app, ["ensemble-run", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == "ensemble-run"
    assert payload["scenario_count"] == 1
    assert payload["scenarios"][0]["name"] == "baseline"


def test_cli_ensemble_run_grid_error_exit_code(tmp_path: Path) -> None:
    config_path = tmp_path / "ensemble.yaml"
    _config(tmp_path, {"not_a_field": [1.0]}).write_json(config_path)

    result = runner.invoke(app, ["ensemble-run", str(config_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "ensemble-run"
    assert payload["code"] == "ensemble_axis_unknown"


# --- real 2-scenario end-to-end (opt-in integration) --------------------------

STANDS_PATH = Path("outputs/full_tsa/data/tsa29-full-stands.parquet")
STAGE1_YIELDS_PATH = Path(
    "/srv/shared-data/gep/jupyterhub07-projects/davis/femic/external/"
    "femic-tsa29-instance/output/woodstock_tsa29_validated/woodstock_yields.csv"
)


def _require_integration_inputs() -> None:
    """Skip when the ws3 package or the local TSA29 inputs are unavailable."""

    try:
        import ws3 as _ws3  # noqa: F401
    except ImportError:
        pytest.skip("ws3 package unavailable (set PYTHONPATH to the ws3 repo)")
    from fresh_salvage import ws3 as fs_ws3

    for path in (STANDS_PATH, STAGE1_YIELDS_PATH, fs_ws3.CANONICAL_TSA29_BRIDGE):
        if not path.exists():
            pytest.skip(f"local TSA29 input unavailable: {path}")


def test_two_scenario_ensemble_end_to_end(tmp_path: Path) -> None:
    """Real pool, real runs: 2 subsidy scenarios x 1 step at horizon 3.

    Exercises the spawn process pool, the once-only bridge prebuild (the
    canonical LU bridge resolves to the derived bridge under the ensemble
    root), per-scenario output isolation, and the summary manifest.
    """

    _require_integration_inputs()
    from fresh_salvage import ws3 as fs_ws3

    config = EnsembleConfig(
        ensemble_id="e2e-ensemble",
        base={
            "stands_path": str(STANDS_PATH),
            "yields_path": str(STAGE1_YIELDS_PATH),
            "bridge_path": str(fs_ws3.CANONICAL_TSA29_BRIDGE),
            "base_year": 2025,
            "horizon": 3,
            "period_length": 10,
            "steps": 1,
            "workers": 2,
        },
        axes={"subsidy_rate_per_m3": [0.0, 3.0]},
        max_workers=2,
        output_root=tmp_path / "ensemble",
    )

    result = ensemble.run_ensemble(config)

    assert result.status == "ok"
    assert result.succeeded == 2
    assert [record.name for record in result.scenarios] == [
        "subsidy_rate_per_m3-0.0",
        "subsidy_rate_per_m3-3.0",
    ]
    roots = {record.output_root for record in result.scenarios}
    assert len(roots) == 2
    for record in result.scenarios:
        assert record.status == "optimal"
        assert record.manifest_path is not None and record.manifest_path.is_file()
        assert record.steps_path is not None and record.steps_path.is_file()
        # One implemented step was flushed to the scenario's own JSONL.
        assert len(record.steps_path.read_text(encoding="utf-8").splitlines()) == 1
        # Every scenario ran against the once-built derived bridge (read-only
        # shared input under the ensemble root, not a per-scenario rebuild).
        scenario_manifest = rh.RHManifest.read_json(record.manifest_path)
        assert scenario_manifest.bridge_path == fs_ws3.derived_bridge_path(
            config.output_root
        )
        assert scenario_manifest.config["subsidy_rate_per_m3"] == float(
            record.overrides["subsidy_rate_per_m3"]
        )
    manifest = EnsembleManifest.read_json(result.manifest_path)
    assert manifest.scenario_count == 2
    assert set(manifest.source_sha256) >= {"grid_config", "stands", "yields"}
    assert any(key.startswith("bridge/") for key in manifest.source_sha256)
