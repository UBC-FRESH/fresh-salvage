"""Data ingestion pipeline tests on synthetic stands (no real WL_VFSL data)."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from fresh_salvage import data
from fresh_salvage.models import (
    IngestManifest,
    ScenarioInputs,
    ScenarioRunConfig,
    SeverityMapping,
)

# Canonical 11-LU subset used by the predecessor (must NOT filter the output).
PREDECESSOR_11_LANDSCAPE_UNITS = {
    "1389",
    "1390",
    "1391",
    "1378",
    "1376",
    "1383",
    "1384",
    "1387",
    "1393",
    "1382",
    "1404",
}


def _stand_row(**overrides: object) -> dict[str, object]:
    """Return a valid synthetic stand row with safe defaults for every column.

    The VRI polygon is 10 ha (FEATURE_AREA_SQM 100,000 m2). SHAPE_Area_1
    defaults to None (unrated stand carries no severity polygon); rated rows
    must set it — a rated row without a positive severity-polygon area is a
    boundary defect (FS-VAL-02 fail-fast).
    """

    row: dict[str, object] = {column: None for column in data.INPUT_COLUMNS}
    row.update(
        {
            "FEATURE_ID": "1",
            "MAP_ID": "M1",
            "POLYGON_ID": "P1",
            "POLYGON_AREA": "10.0",
            "BASAL_AREA": "20.0",
            "VRI_LIVE_STEMS_PER_HA": "500",
            "PROJ_HEIGHT_1": "18.0",
            "SPECIES_CD_1": "FD",
            "SPECIES_PCT_1": "100.0",
            "LIVE_VOL_PER_HA_SPP1_175": "100.0",
            "DEAD_VOL_PER_HA_SPP1_175": "0.0",
            "LIVE_STAND_VOLUME_175": "100.0",
            "DEAD_STAND_VOLUME_175": "0.0",
            "BURN_SEVERITY_RATING": None,
            "MEAN": "10.0",
            "LANDSCAPE_UNIT_ID": "1389",
            "LANDSCAPE_UNIT_NAME": "Canonical",
            "BEC_ZONE_CODE": "SBPS",
            "SHAPE_Area_1": None,
            "FEATURE_AREA_SQM": "100000.0",
        }
    )
    row.update(overrides)
    return row


def _fully_covered(**overrides: object) -> dict[str, object]:
    """Return a rated stand row whose severity polygon covers it (coverage 1)."""

    overrides.setdefault("SHAPE_Area_1", "100000.0")
    return _stand_row(**overrides)


def make_synthetic_frame() -> pd.DataFrame:
    """Build the 13-row synthetic stand frame used by the pipeline tests.

    Retained rows (11) and their expected burned fractions (every rated row is
    fully covered: SHAPE_Area_1 == FEATURE_AREA_SQM, so coverage is 1 and the
    severity fraction passes through unchanged):

    - rows 1-6: FD (SPF) with live 100 and severity
      NaN/Unburned/Low/Medium/High/Unknown -> fractions 0/0/0.30/0.60/0.85/0
    - row 7: zero live SPP1 with live SPP2 -> DROPPED (faithful DP_PA port)
    - row 8: null MEAN -> DROPPED
    - row 9:  CW (Cedar) live 200, Low   -> burned 60
    - row 10: H  (Hem-Bal) live 150, High -> burned 127.5
    - row 11: LA (Df-Larch) live 120, Medium -> burned 72
    - row 12: AT (Other) live 80, Unburned -> burned 0
    - row 13: FD (SPF) live 100, Moderate (canonical label) -> burned 60
    """

    rows = [
        _stand_row(FEATURE_ID="1"),
        _fully_covered(
            FEATURE_ID="2",
            BURN_SEVERITY_RATING="Unburned",
            LANDSCAPE_UNIT_ID="1390",
            BEC_ZONE_CODE="IDF",
        ),
        _fully_covered(
            FEATURE_ID="3",
            BURN_SEVERITY_RATING="Low",
            LANDSCAPE_UNIT_ID="9999",
            BEC_ZONE_CODE="MS",
        ),
        _fully_covered(
            FEATURE_ID="4",
            BURN_SEVERITY_RATING="Medium",
            LANDSCAPE_UNIT_ID="1382",
            BEC_ZONE_CODE="ESSF",
        ),
        _fully_covered(
            FEATURE_ID="5",
            BURN_SEVERITY_RATING="High",
            LANDSCAPE_UNIT_ID="9999",
            BEC_ZONE_CODE="SBS",
        ),
        _fully_covered(
            FEATURE_ID="6",
            BURN_SEVERITY_RATING="Unknown",
            LANDSCAPE_UNIT_ID="1404",
            BEC_ZONE_CODE="ICH",
        ),
        _stand_row(
            FEATURE_ID="7",
            LIVE_VOL_PER_HA_SPP1_175="0",
            SPECIES_CD_2="FD",
            SPECIES_PCT_2="100.0",
            LIVE_VOL_PER_HA_SPP2_175="100.0",
            BURN_SEVERITY_RATING="High",
        ),
        _stand_row(FEATURE_ID="8", MEAN=None, BURN_SEVERITY_RATING="High"),
        _fully_covered(
            FEATURE_ID="9",
            SPECIES_CD_1="CW",
            LIVE_VOL_PER_HA_SPP1_175="200.0",
            BURN_SEVERITY_RATING="Low",
            LANDSCAPE_UNIT_ID="9999",
            BEC_ZONE_CODE="SBPS",
        ),
        _fully_covered(
            FEATURE_ID="10",
            SPECIES_CD_1="H",
            LIVE_VOL_PER_HA_SPP1_175="150.0",
            BURN_SEVERITY_RATING="High",
            LANDSCAPE_UNIT_ID="1383",
            BEC_ZONE_CODE="IDF",
        ),
        _fully_covered(
            FEATURE_ID="11",
            SPECIES_CD_1="LA",
            LIVE_VOL_PER_HA_SPP1_175="120.0",
            BURN_SEVERITY_RATING="Medium",
            LANDSCAPE_UNIT_ID="9999",
            BEC_ZONE_CODE="MS",
        ),
        _fully_covered(
            FEATURE_ID="12",
            SPECIES_CD_1="AT",
            LIVE_VOL_PER_HA_SPP1_175="80.0",
            BURN_SEVERITY_RATING="Unburned",
            LANDSCAPE_UNIT_ID="1384",
            BEC_ZONE_CODE="BG",
        ),
        _fully_covered(
            FEATURE_ID="13",
            BURN_SEVERITY_RATING="Moderate",
            LANDSCAPE_UNIT_ID="9999",
            BEC_ZONE_CODE="ESSF",
        ),
    ]
    return pd.DataFrame(rows, columns=data.INPUT_COLUMNS)


def _run_ingest(
    tmp_path: Path,
    frame: pd.DataFrame,
    severity: SeverityMapping | None = None,
) -> tuple[data.IngestResult, pd.DataFrame]:
    """Write the synthetic frame to CSV, ingest it, and return the result."""

    csv_path = tmp_path / "wl_vfsl.csv"
    frame.to_csv(csv_path, index=False)
    scenario = ScenarioRunConfig(
        run_id="synthetic-run",
        inputs=ScenarioInputs(wl_vfsl_path=csv_path, output_root=tmp_path / "out"),
        severity=severity or SeverityMapping(),
    )
    result = data.ingest(scenario)
    output = pd.read_parquet(result.data_path)
    return result, output


def test_ingest_retains_expected_stands(tmp_path: Path) -> None:
    result, output = _run_ingest(tmp_path, make_synthetic_frame())

    assert result.total_stands == 11
    assert result.dropped_null_rows == 1
    assert result.dropped_zero_live_rows == 1
    assert len(output) == 11

    manifest = IngestManifest.read_json(result.manifest_path)
    assert manifest.input_rows == 13
    assert manifest.retained_rows == 11


def test_severity_to_burned_fraction(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    burned_by_feature = dict(
        zip(output["FEATURE_ID"].astype(str), output["Total_Burned_Vol"].astype(float))
    )
    assert burned_by_feature["1"] == pytest.approx(0.0)  # unrated -> unburned
    assert burned_by_feature["2"] == pytest.approx(0.0)  # Unburned
    assert burned_by_feature["3"] == pytest.approx(30.0)  # Low = 0.30 x 100
    assert burned_by_feature["4"] == pytest.approx(60.0)  # Medium -> Moderate 0.60
    assert burned_by_feature["5"] == pytest.approx(85.0)  # High = 0.85 x 100
    assert burned_by_feature["6"] == pytest.approx(0.0)  # Unknown -> unburned
    assert burned_by_feature["9"] == pytest.approx(60.0)  # Low x 200
    assert burned_by_feature["10"] == pytest.approx(127.5)  # High x 150
    assert burned_by_feature["11"] == pytest.approx(72.0)  # Moderate x 120
    assert burned_by_feature["12"] == pytest.approx(0.0)  # Unburned
    assert burned_by_feature["13"] == pytest.approx(60.0)  # canonical Moderate


def test_severity_ladder_scenario_override(tmp_path: Path) -> None:
    """FS-VAL-01: the ladder is a scenario-visible parameter, echoed to the manifest."""

    frame = pd.DataFrame(
        [_fully_covered(FEATURE_ID="30", BURN_SEVERITY_RATING="Low")],
        columns=data.INPUT_COLUMNS,
    )
    severity = SeverityMapping(
        severity_to_burned_frac={"Unburned": 0.0, "Low": 0.50},
        severity_aliases={},
    )

    result, output = _run_ingest(tmp_path, frame, severity=severity)

    # Overridden Low = 0.50 (not the default 0.30) x live 100 x coverage 1.
    assert output["Total_Burned_Vol"].iloc[0] == pytest.approx(50.0)
    manifest = IngestManifest.read_json(result.manifest_path)
    assert manifest.parameters["severity_to_burned_frac"] == {
        "Unburned": 0.0,
        "Low": 0.50,
    }
    assert manifest.parameters["severity_aliases"] == {}


def test_severity_ladder_default_echoed_in_manifest(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    manifest = IngestManifest.read_json(result.manifest_path)
    assert manifest.parameters["severity_to_burned_frac"] == data.SEVERITY_TO_BURNED_FRAC
    assert manifest.parameters["severity_aliases"] == data.SEVERITY_ALIASES
    assert manifest.parameters["unknown_severity_label"] == data.UNKNOWN_SEVERITY_LABEL
    coverage = manifest.parameters["coverage_scaling"]
    assert coverage["numerator_column"] == data.COVERAGE_NUMERATOR_COLUMN
    assert coverage["denominator_column"] == data.COVERAGE_DENOMINATOR_COLUMN
    assert "upper bound" in coverage["caveat"].lower()


def test_unmatched_severity_label_is_fatal(tmp_path: Path) -> None:
    """FS-VAL-01: an unrecognized non-null rating halts ingestion (no silent 0)."""

    frame = pd.DataFrame(
        [
            _fully_covered(FEATURE_ID="40", BURN_SEVERITY_RATING="Severe"),
            _fully_covered(FEATURE_ID="41", BURN_SEVERITY_RATING="Severe"),
            _fully_covered(FEATURE_ID="42", BURN_SEVERITY_RATING="Bogus"),
        ],
        columns=data.INPUT_COLUMNS,
    )
    csv_path = tmp_path / "wl_vfsl.csv"
    frame.to_csv(csv_path, index=False)
    scenario = ScenarioRunConfig(
        run_id="synthetic-run",
        inputs=ScenarioInputs(wl_vfsl_path=csv_path, output_root=tmp_path / "out"),
    )

    with pytest.raises(data.IngestError) as excinfo:
        data.ingest(scenario)

    assert excinfo.value.code == "data_severity_unmatched"
    assert "'Severe': 2" in str(excinfo.value)
    assert "'Bogus': 1" in str(excinfo.value)


def test_coverage_scaling_reduces_burned_volume(tmp_path: Path) -> None:
    """FS-VAL-02: salvageable volume scales by SHAPE_Area_1/FEATURE_AREA_SQM."""

    frame = pd.DataFrame(
        [
            _stand_row(
                FEATURE_ID="50",
                BURN_SEVERITY_RATING="High",
                SHAPE_Area_1="30000.0",  # coverage 0.30
            )
        ],
        columns=data.INPUT_COLUMNS,
    )

    _, output = _run_ingest(tmp_path, frame)

    assert output["Total_Burned_Vol"].iloc[0] == pytest.approx(100.0 * 0.85 * 0.30)


def test_coverage_ratio_clamped_to_one(tmp_path: Path) -> None:
    """A severity polygon larger than the VRI polygon caps coverage at 1."""

    frame = pd.DataFrame(
        [
            _stand_row(
                FEATURE_ID="51",
                BURN_SEVERITY_RATING="High",
                SHAPE_Area_1="250000.0",  # 2.5x the polygon area
            )
        ],
        columns=data.INPUT_COLUMNS,
    )

    _, output = _run_ingest(tmp_path, frame)

    assert output["Total_Burned_Vol"].iloc[0] == pytest.approx(100.0 * 0.85)


def test_coverage_missing_denominator_is_fatal(tmp_path: Path) -> None:
    """Rated rows require a positive FEATURE_AREA_SQM (structured error)."""

    frame = pd.DataFrame(
        [
            _stand_row(
                FEATURE_ID="52",
                BURN_SEVERITY_RATING="High",
                SHAPE_Area_1="30000.0",
                FEATURE_AREA_SQM=None,
            )
        ],
        columns=data.INPUT_COLUMNS,
    )
    csv_path = tmp_path / "wl_vfsl.csv"
    frame.to_csv(csv_path, index=False)
    scenario = ScenarioRunConfig(
        run_id="synthetic-run",
        inputs=ScenarioInputs(wl_vfsl_path=csv_path, output_root=tmp_path / "out"),
    )

    with pytest.raises(data.IngestError) as excinfo:
        data.ingest(scenario)

    assert excinfo.value.code == "data_coverage_denominator_invalid"


def test_coverage_missing_severity_area_is_fatal(tmp_path: Path) -> None:
    """Rated rows require a positive SHAPE_Area_1 (structured error)."""

    frame = pd.DataFrame(
        [_stand_row(FEATURE_ID="53", BURN_SEVERITY_RATING="High")],
        columns=data.INPUT_COLUMNS,
    )
    csv_path = tmp_path / "wl_vfsl.csv"
    frame.to_csv(csv_path, index=False)
    scenario = ScenarioRunConfig(
        run_id="synthetic-run",
        inputs=ScenarioInputs(wl_vfsl_path=csv_path, output_root=tmp_path / "out"),
    )

    with pytest.raises(data.IngestError) as excinfo:
        data.ingest(scenario)

    assert excinfo.value.code == "data_coverage_numerator_invalid"


def test_unrated_rows_ignore_coverage_areas(tmp_path: Path) -> None:
    """Unrated rows carry no severity polygon and stay unburned."""

    frame = pd.DataFrame(
        [_stand_row(FEATURE_ID="54", SHAPE_Area_1=None, FEATURE_AREA_SQM=None)],
        columns=data.INPUT_COLUMNS,
    )

    result, output = _run_ingest(tmp_path, frame)

    assert result.total_stands == 1
    assert output["Total_Burned_Vol"].iloc[0] == pytest.approx(0.0)


def test_burned_grade_transition(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    cedar = output[output["FEATURE_ID"].astype(str) == "9"].iloc[0]
    assert cedar["Cedar_Sawlog_Vol"] == pytest.approx(200.0 * 0.805)
    assert cedar["Cedar_Peelers_Vol"] == pytest.approx(200.0 * 0.092)
    assert cedar["Cedar_Pulpwood_Vol"] == pytest.approx(200.0 * 0.103)
    # B_Cedar_Sawlog_Vol = live * frac * split * transition = 200*0.30*0.805*0.40
    assert cedar["B_Cedar_Sawlog_Vol"] == pytest.approx(19.32)
    # B_Cedar_Peelers_Vol includes Sawlog->Peeler and Peeler->Peeler transitions.
    assert cedar["B_Cedar_Peelers_Vol"] == pytest.approx(
        200 * 0.30 * (0.805 * 0.05 + 0.092 * 0.20)
    )


def test_other_species_volume(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    other = output[output["FEATURE_ID"].astype(str) == "12"].iloc[0]
    assert other["Other_Vol"] == pytest.approx(80.0)
    assert other["Total_Green_Vol"] == pytest.approx(80.0)


def test_other_species_burned_volume_conserved(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            _fully_covered(
                FEATURE_ID="20",
                SPECIES_CD_1="AT",
                LIVE_VOL_PER_HA_SPP1_175="100.0",
                BURN_SEVERITY_RATING="High",
                BEC_ZONE_CODE="SBPS",
            )
        ],
        columns=data.INPUT_COLUMNS,
    )

    _, output = _run_ingest(tmp_path, frame)

    row = output.iloc[0]
    assert row["B_Other_Vol"] == pytest.approx(85.0)  # 100 x 0.85 conserved
    assert row["Total_Burned_Vol"] == pytest.approx(85.0)


def test_zero_live_volume_stands_dropped(tmp_path: Path) -> None:
    result, output = _run_ingest(tmp_path, make_synthetic_frame())

    feature_ids = set(output["FEATURE_ID"].astype(str))
    assert "7" not in feature_ids  # zero SPP1 live volume dropped (DP_PA faithful)
    assert "8" not in feature_ids  # null MEAN dropped
    assert result.dropped_zero_live_rows == 1
    assert result.dropped_null_rows == 1


def test_no_landscape_unit_filter(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    landscape_units = set(output["LANDSCAPE_UNIT_ID"].astype(str))
    non_canonical = landscape_units - PREDECESSOR_11_LANDSCAPE_UNITS
    # Stands outside the predecessor 11-LU set must be retained.
    assert "9999" in landscape_units
    assert non_canonical == {"9999"}
    # The output is not restricted to the predecessor subset.
    assert "1389" in landscape_units


def test_bec_zone_attribution(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    zone_by_feature = dict(zip(output["FEATURE_ID"].astype(str), output["BEC_ZONE_CODE"]))
    assert zone_by_feature["1"] == "SBPS"
    assert zone_by_feature["9"] == "SBPS"
    assert zone_by_feature["4"] == "ESSF"
    assert zone_by_feature["12"] == "BG"


def test_development_type_derivation(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    dev_by_feature = dict(
        zip(output["FEATURE_ID"].astype(str), output["development_type"])
    )
    assert dev_by_feature["1"] == "SPF_SBPS"  # FD + SBPS
    assert dev_by_feature["9"] == "Cedar_SBPS"  # CW + SBPS
    assert dev_by_feature["10"] == "Hem-Bal_IDF"  # H + IDF
    assert dev_by_feature["11"] == "Df-Larch_MS"  # LA + MS
    assert dev_by_feature["12"] == "Other_BG"  # AT + BG


def test_price_dicts_parsed(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    assert json.loads(output["green_prices"].iloc[0]) == data.GREEN_PRICES
    assert json.loads(output["burned_prices"].iloc[0]) == data.BURNED_PRICES
    assert data.BURNED_PRICES["SPF_Sawlog"] == pytest.approx(
        data.GREEN_PRICES["SPF_Sawlog"] * data.BURNED_PRICE_DISCOUNT
    )


def test_calibrated_economic_constants() -> None:
    """Pin the calibrated defaults (planning/economics-calibration.md)."""

    assert data.GREEN_PRICES == {
        "SPF_Sawlog": 127,
        "SPF_Peelers": 146,
        "SPF_Pulpwood": 55,
        "Df-Larch_Sawlog": 103,
        "Df-Larch_Peelers": 118,
        "Df-Larch_Pulpwood": 55,
        "Hem-Bal_Sawlog": 120,
        "Hem-Bal_Peelers": 138,
        "Hem-Bal_Pulpwood": 55,
        "Cedar_Sawlog": 144,
        "Cedar_Peelers": 166,
        "Cedar_Pulpwood": 55,
        "Other": 90,
    }
    assert data.BURNED_PRICE_DISCOUNT == pytest.approx(0.65)
    assert data.GREEN_HARVEST_COST == pytest.approx(45.0)
    assert data.BURNED_HARVEST_COST == pytest.approx(61.0)
    assert data.TRANSPORT_COST_PER_M3 == pytest.approx(30.0)
    assert data.BURNED_TRANSPORT_COST_PER_M3 == pytest.approx(41.0)
    assert data.GREEN_STUMPAGE_RATE == pytest.approx(15.0)
    assert data.BURNED_STUMPAGE_RATE == pytest.approx(0.25)
    assert data.SUBSIDY_RATE_PER_M3 == pytest.approx(3.0)


def test_economic_columns(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    assert output["Subsidy_Rate"].iloc[0] == pytest.approx(data.SUBSIDY_RATE_PER_M3)
    assert output["Green_Stumpage_Rate"].iloc[0] == pytest.approx(data.GREEN_STUMPAGE_RATE)
    assert output["Burned_Stumpage_Rate"].iloc[0] == pytest.approx(data.BURNED_STUMPAGE_RATE)
    assert output["Harvest_Cost_Green"].iloc[0] == pytest.approx(data.GREEN_HARVEST_COST)
    assert output["Harvest_Cost_Burned"].iloc[0] == pytest.approx(data.BURNED_HARVEST_COST)


def test_economics_scenario_override(tmp_path: Path) -> None:
    """The economic surface is a scenario-visible parameter, echoed to the manifest."""

    from fresh_salvage.models import Economics

    economics = Economics(subsidy_rate_per_m3=12.0, green_harvest_cost=50.0)
    csv_path = tmp_path / "wl_vfsl.csv"
    make_synthetic_frame().to_csv(csv_path, index=False)
    scenario = ScenarioRunConfig(
        run_id="synthetic-run",
        inputs=ScenarioInputs(wl_vfsl_path=csv_path, output_root=tmp_path / "out"),
        economics=economics,
    )

    result = data.ingest(scenario)
    output = pd.read_parquet(result.data_path)

    assert output["Subsidy_Rate"].iloc[0] == pytest.approx(12.0)
    assert output["Harvest_Cost_Green"].iloc[0] == pytest.approx(50.0)
    row = output[output["FEATURE_ID"].astype(str) == "3"].iloc[0]
    assert row["Subsidy_Total"] == pytest.approx(row["Total_Burned_Vol"] * 12.0)
    # Untouched fields keep the calibrated data.py defaults.
    assert output["Burned_Stumpage_Rate"].iloc[0] == pytest.approx(
        data.BURNED_STUMPAGE_RATE
    )
    manifest = IngestManifest.read_json(result.manifest_path)
    assert manifest.parameters["subsidy_rate_per_m3"] == 12.0
    assert manifest.parameters["green_harvest_cost"] == 50.0


def test_manifest_echoes_all_economic_parameters(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    manifest = IngestManifest.read_json(result.manifest_path)
    assert manifest.parameters["subsidy_rate_per_m3"] == pytest.approx(
        data.SUBSIDY_RATE_PER_M3
    )
    assert manifest.parameters["green_stumpage_rate"] == pytest.approx(
        data.GREEN_STUMPAGE_RATE
    )
    assert manifest.parameters["burned_stumpage_rate"] == pytest.approx(
        data.BURNED_STUMPAGE_RATE
    )
    assert manifest.parameters["green_harvest_cost"] == pytest.approx(
        data.GREEN_HARVEST_COST
    )
    assert manifest.parameters["burned_harvest_cost"] == pytest.approx(
        data.BURNED_HARVEST_COST
    )
    assert manifest.parameters["green_transport_cost_per_m3"] == pytest.approx(
        data.TRANSPORT_COST_PER_M3
    )
    assert manifest.parameters["burned_transport_cost_per_m3"] == pytest.approx(
        data.BURNED_TRANSPORT_COST_PER_M3
    )
    assert manifest.parameters["burned_price_discount"] == pytest.approx(
        data.BURNED_PRICE_DISCOUNT
    )
    assert manifest.parameters["green_prices"] == data.GREEN_PRICES
    assert manifest.parameters["burned_prices"] == data.BURNED_PRICES


def test_ingest_summary_counts(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    assert result.burned_stands == 7
    assert result.burned_volume == pytest.approx(494.5)
    assert result.green_volume == pytest.approx(1250.0)
    assert result.per_bec_zone_counts == {
        "SBPS": 2,
        "IDF": 2,
        "MS": 2,
        "ESSF": 2,
        "SBS": 1,
        "ICH": 1,
        "BG": 1,
    }


def test_ingest_diagnostics(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    codes = [diagnostic.code for diagnostic in result.diagnostics]
    assert "ingest_severity_alias" in codes  # Medium -> Moderate normalization
    assert "ingest_unknown_severity" in codes  # Unknown treated as unburned


def test_manifest_written(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    assert result.manifest_path.is_file()
    assert result.data_path.is_file()
    assert result.csv_path.is_file()
    assert result.data_path.suffix == ".parquet"
    assert result.csv_path.suffix == ".csv"


def test_manifest_records_source_sha256(tmp_path: Path) -> None:
    result, _ = _run_ingest(tmp_path, make_synthetic_frame())

    manifest = IngestManifest.read_json(result.manifest_path)
    expected = hashlib.sha256((tmp_path / "wl_vfsl.csv").read_bytes()).hexdigest()
    assert manifest.source_sha256 == expected
    assert len(manifest.source_sha256) == 64


def test_stands_from_frame(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    stands = data.stands_from_frame(output)

    assert len(stands) == 11
    assert stands[0].development_type == "SPF_SBPS"
    assert stands[0].green_prices == data.GREEN_PRICES
    assert stands[0].burned_prices == data.BURNED_PRICES
    assert stands[0].polygon_area == 10.0


def test_development_types_from_frame(tmp_path: Path) -> None:
    _, output = _run_ingest(tmp_path, make_synthetic_frame())

    development_types = data.development_types_from_frame(output)
    by_key = {record.development_type: record for record in development_types}

    assert by_key["SPF_SBPS"].stand_count == 1
    assert by_key["SPF_ESSF"].stand_count == 2
    assert by_key["Cedar_SBPS"].stand_count == 1
    assert by_key["Other_BG"].species_group == "Other"
    assert by_key["SPF_MS"].total_burned_vol == pytest.approx(30.0)
    assert by_key["Df-Larch_MS"].total_burned_vol == pytest.approx(72.0)


def test_species_group_mapping() -> None:
    assert data.species_group("FD") == "SPF"
    assert data.species_group("fd") == "SPF"
    assert data.species_group("CW") == "Cedar"
    assert data.species_group("H") == "Hem-Bal"
    assert data.species_group("LA") == "Df-Larch"
    assert data.species_group("AT") == "Other"
    assert data.species_group("ZZZ") == "Other"
    assert data.species_group(None) == "Other"
