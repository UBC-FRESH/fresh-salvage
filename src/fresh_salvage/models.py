"""Typed pydantic records for scenario configuration, artifacts, and stands.

Mirrors the figrecover house style: configuration records are pydantic v2
``BaseModel`` classes with ``read()``/``write_json()`` helpers, artifact paths
live behind an ``ArtifactLayout``, and pipeline outcomes carry structured
``Diagnostic`` records instead of bare exceptions.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MANIFEST_VERSION = "1.0"

ARTIFACT_DIRECTORIES = ("data", "manifests", "logs")


class Diagnostic(BaseModel):
    """A structured pipeline diagnostic (house evidence style)."""

    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    context: dict[str, object] = Field(default_factory=dict)


class ScenarioInputs(BaseModel):
    """External inputs selected by one scenario run."""

    wl_vfsl_path: Path
    output_root: Path


class FireDefaults(BaseModel):
    """Placeholder fire-simulation defaults for the Phase 4 annual fire sim.

    Phase 4 burns development-type area at ``1/MFRI``; this record reserves the
    per-development-type configuration surface so scenario files remain stable.
    """

    mfri_by_development_type: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class ScenarioRunConfig(BaseModel):
    """Configuration for one full-TSA ingestion run."""

    run_id: str = "tsa29-full"
    inputs: ScenarioInputs
    fire: FireDefaults = Field(default_factory=FireDefaults)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> ScenarioRunConfig:
        """Read a scenario config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class ArtifactLayout(BaseModel):
    """Stable artifact paths under an output root."""

    output_root: Path

    @property
    def data_dir(self) -> Path:
        """Stand data artifact directory."""

        return self.output_root / "data"

    @property
    def manifests_dir(self) -> Path:
        """Run manifest directory."""

        return self.output_root / "manifests"

    @property
    def logs_dir(self) -> Path:
        """Execution log directory."""

        return self.output_root / "logs"

    def initialize(self) -> ArtifactLayout:
        """Create the standard artifact directories."""

        for directory in self.directories().values():
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def directories(self) -> dict[str, Path]:
        """Return standard artifact directory names and paths."""

        return {
            "data": self.data_dir,
            "manifests": self.manifests_dir,
            "logs": self.logs_dir,
        }

    def data_path(self, name: str, *, ext: str = "parquet") -> Path:
        """Return a stable stand-data artifact path."""

        return self.data_dir / f"{safe_slug(name)}.{ext}"

    def manifest_path(self, name: str, *, ext: str = "json") -> Path:
        """Return a stable manifest path."""

        return self.manifests_dir / f"{safe_slug(name)}.{ext}"

    def log_path(self, name: str, *, ext: str = "log") -> Path:
        """Return a stable log path."""

        return self.logs_dir / f"{safe_slug(name)}.{ext}"


class Stand(BaseModel):
    """One polygon stand with derived volumes and economics.

    Boundary record for downstream phases; produced from an ingested stand
    frame by :func:`fresh_salvage.data.stands_from_frame`.
    """

    feature_id: str
    polygon_id: str
    map_id: str
    polygon_area: float = Field(ge=0.0)
    bec_zone: str
    development_type: str
    landscape_unit_id: str | None = None
    burn_severity_rating: str | None = None
    total_green_vol: float = Field(ge=0.0)
    total_burned_vol: float = Field(ge=0.0)
    subsidy_rate: float
    green_stumpage_rate: float
    burned_stumpage_rate: float
    harvest_cost_green: float
    harvest_cost_burned: float
    subsidy_total: float
    stumpage_green_total: float
    stumpage_burned_total: float
    green_prices: dict[str, float]
    burned_prices: dict[str, float]


class DevelopmentType(BaseModel):
    """Aggregate record keyed by the stratum ``development_type``."""

    development_type: str
    bec_zone: str
    species_group: str
    stand_count: int = Field(ge=0)
    area_ha: float = Field(ge=0.0)
    total_green_vol: float = Field(ge=0.0)
    total_burned_vol: float = Field(ge=0.0)


class IngestManifest(BaseModel):
    """Evidence manifest for one ingestion run."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    source_file: Path
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    input_rows: int = Field(ge=0)
    retained_rows: int = Field(ge=0)
    dropped_null_rows: int = Field(ge=0)
    dropped_zero_live_rows: int = Field(ge=0)
    burned_stands: int = Field(ge=0)
    burned_volume: float = Field(ge=0.0)
    green_volume: float = Field(ge=0.0)
    per_bec_zone_counts: dict[str, int] = Field(default_factory=dict)
    per_development_type_counts: dict[str, int] = Field(default_factory=dict)
    parameters: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> IngestManifest:
        """Read a run manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class IngestResult(BaseModel):
    """Summary result of one ingestion run."""

    run_id: str
    source_file: Path
    data_path: Path
    csv_path: Path
    manifest_path: Path
    total_stands: int = Field(ge=0)
    dropped_null_rows: int = Field(ge=0)
    dropped_zero_live_rows: int = Field(ge=0)
    burned_stands: int = Field(ge=0)
    burned_volume: float = Field(ge=0.0)
    green_volume: float = Field(ge=0.0)
    per_bec_zone_counts: dict[str, int] = Field(default_factory=dict)
    per_development_type_counts: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0.0)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly run summary."""

        return {
            "run_id": self.run_id,
            "source_file": str(self.source_file),
            "artifacts": {
                "data": str(self.data_path),
                "csv": str(self.csv_path),
                "manifest": str(self.manifest_path),
            },
            "total_stands": self.total_stands,
            "dropped_null_rows": self.dropped_null_rows,
            "dropped_zero_live_rows": self.dropped_zero_live_rows,
            "burned_stands": self.burned_stands,
            "burned_volume": round(self.burned_volume, 2),
            "green_volume": round(self.green_volume, 2),
            "per_bec_zone_counts": dict(sorted(self.per_bec_zone_counts.items())),
            "per_development_type_counts": dict(
                sorted(self.per_development_type_counts.items())
            ),
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
            "duration_seconds": round(self.duration_seconds, 3),
        }


def safe_slug(value: str) -> str:
    """Return a filesystem-safe identifier slug."""

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug or "artifact"


def _load_yaml(text: str) -> object:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "YAML config loading requires PyYAML. Install it separately or use JSON config."
        ) from exc
    return yaml.safe_load(text)


__all__ = [
    "ARTIFACT_DIRECTORIES",
    "ArtifactLayout",
    "DevelopmentType",
    "Diagnostic",
    "FireDefaults",
    "IngestManifest",
    "IngestResult",
    "MANIFEST_VERSION",
    "ScenarioInputs",
    "ScenarioRunConfig",
    "Stand",
    "safe_slug",
]
