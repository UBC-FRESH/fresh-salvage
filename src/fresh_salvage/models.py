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

from pydantic import BaseModel, Field, field_validator, model_validator

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


def _default_severity_to_burned_frac() -> dict[str, float]:
    """Return the shared severity-ladder default.

    Deferred import: ``data`` already imports this module, so a module-level
    import here would be circular.
    """

    from fresh_salvage.data import SEVERITY_TO_BURNED_FRAC

    return dict(SEVERITY_TO_BURNED_FRAC)


def _default_severity_aliases() -> dict[str, str]:
    """Return the shared severity-alias default (deferred import, see above)."""

    from fresh_salvage.data import SEVERITY_ALIASES

    return dict(SEVERITY_ALIASES)


class SeverityMapping(BaseModel):
    """Scenario-visible burn-severity ladder (FS-VAL-01 parameterization).

    ``severity_to_burned_frac`` maps a burn-severity rating to the fraction of
    live volume that becomes salvageable; ``severity_aliases`` normalizes
    dataset label variants (the WL_VFSL layer labels the mid tier "Medium"
    while the ladder calls it "Moderate"). Both default to the shared
    ``data.py`` constants; a scenario may override the whole ladder, but
    fractions must lie in [0, 1], alias sources must not collide with ladder
    labels, and every alias target must itself be a ladder label — invalid
    ladders fail at config parse time, not mid-pipeline.
    """

    severity_to_burned_frac: dict[str, float] = Field(
        default_factory=_default_severity_to_burned_frac
    )
    severity_aliases: dict[str, str] = Field(default_factory=_default_severity_aliases)

    @field_validator("severity_to_burned_frac")
    @classmethod
    def _validate_ladder(cls, value: dict[str, float]) -> dict[str, float]:
        for label, fraction in value.items():
            if not str(label).strip():
                raise ValueError("severity ladder labels must not be empty")
            if not 0.0 <= fraction <= 1.0:
                raise ValueError("severity ladder fractions must lie in [0, 1]")
        return value

    @model_validator(mode="after")
    def _validate_aliases(self) -> SeverityMapping:
        for source, target in self.severity_aliases.items():
            if not str(source).strip() or not str(target).strip():
                raise ValueError("severity alias labels must not be empty")
        colliding = sorted(set(self.severity_aliases) & set(self.severity_to_burned_frac))
        if colliding:
            raise ValueError(
                "severity alias sources must not also be ladder labels: "
                + ", ".join(colliding)
            )
        dangling = sorted(
            set(self.severity_aliases.values()) - set(self.severity_to_burned_frac)
        )
        if dangling:
            raise ValueError(
                "severity aliases target labels missing from the ladder: "
                + ", ".join(dangling)
            )
        return self


class ScenarioRunConfig(BaseModel):
    """Configuration for one full-TSA ingestion run."""

    run_id: str = "tsa29-full"
    inputs: ScenarioInputs
    fire: FireDefaults = Field(default_factory=FireDefaults)
    severity: SeverityMapping = Field(default_factory=SeverityMapping)
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


class AgeSmashing(BaseModel):
    """Deterministic initial-inventory age-class compression settings."""

    enabled: bool = True
    width: int = 10
    midpoint: int = 5

    @field_validator("width")
    @classmethod
    def _validate_width(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("age-smashing width must be positive")
        return value

    @model_validator(mode="after")
    def _validate_midpoint(self) -> AgeSmashing:
        if not 0 <= self.midpoint < self.width:
            raise ValueError("age-smashing midpoint must lie inside the width class")
        return self


class WS3Objective(BaseModel):
    """Objective and constraint tuning for the full-TSA WS3 solve."""

    action_code: str = "cc"
    utilization: float = 0.85
    even_flow_tolerance: float = 0.1


class WS3RunConfig(BaseModel):
    """Configuration for one full-TSA WS3 schedule solve."""

    run_id: str = "tsa29-ws3"
    bridge_path: Path
    base_year: int
    horizon: int
    period_length: int = 10
    max_age: int = 999
    workers: int = 64
    age_smashing: AgeSmashing = Field(default_factory=AgeSmashing)
    objective: WS3Objective = Field(default_factory=WS3Objective)
    aac_annual_m3: float = 2_937_509
    output_root: Path
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    @field_validator("base_year", "horizon", "workers")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("base_year, horizon, and workers must be positive")
        return value

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> WS3RunConfig:
        """Read a WS3 run config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class WS3Manifest(BaseModel):
    """Evidence manifest for one full-TSA WS3 solve."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    bridge_path: Path
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str
    periods: int = Field(ge=0)
    objective_value: float = Field(ge=0.0)
    schedule_rows: int = Field(ge=0)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    solve_seconds: float = Field(ge=0.0)
    bridge_checksums: dict[str, str] = Field(default_factory=dict)
    config: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> WS3Manifest:
        """Read a run manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class PrincipalRunConfig(BaseModel):
    """Configuration for one principal-LP solve at cohort granularity.

    Inputs are the Phase 2a stands table (parquet written by ``ingest``), the
    derived WS3 bridge ARE section (cohort units), and the femic stage-1
    yields table (volume per hectare by curve and age). Years are 1-year
    timesteps; ``horizon`` counts them (10 = one rolling-horizon step).
    """

    run_id: str = "tsa29-principal"
    stands_path: Path
    are_path: Path
    yields_path: Path
    horizon: int = 10
    aac_annual_m3: float = 2_937_509
    burned_limit_annual_m3: float | None = None
    decay_rate: float = 0.85
    output_root: Path
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    @field_validator("horizon")
    @classmethod
    def _validate_horizon(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("horizon must be a positive number of 1-year timesteps")
        return value

    @field_validator("aac_annual_m3")
    @classmethod
    def _validate_aac(cls, value: float) -> float:
        if value < 0:
            raise ValueError("aac_annual_m3 cannot be negative")
        return value

    @field_validator("burned_limit_annual_m3")
    @classmethod
    def _validate_burned_limit(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("burned_limit_annual_m3 cannot be negative")
        return value

    @field_validator("decay_rate")
    @classmethod
    def _validate_decay_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("decay_rate must lie in [0, 1]")
        return value

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> PrincipalRunConfig:
        """Read a principal run config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class PrincipalOfferRecord(BaseModel):
    """One (cohort, year) principal offer fraction, emitted including zeros."""

    cohort_id: str
    year: int = Field(ge=1)
    offer_fraction: float = Field(ge=0.0, le=1.0)


class PrincipalYearVolumes(BaseModel):
    """Offered green and burned volume (m3) of one timestep."""

    year: int = Field(ge=1)
    green_volume_m3: float = Field(ge=0.0)
    burned_volume_m3: float = Field(ge=0.0)


class PrincipalManifest(BaseModel):
    """Evidence manifest for one principal-LP solve."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    stands_path: Path
    are_path: Path
    yields_path: Path
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str
    horizon: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    objective_value: float
    offered_cohort_years: int = Field(ge=0)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    solve_seconds: float = Field(ge=0.0)
    source_sha256: dict[str, str] = Field(default_factory=dict)
    config: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> PrincipalManifest:
        """Read a run manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class PrincipalResult(BaseModel):
    """Typed result of one principal-LP solve."""

    run_id: str
    status: str
    horizon: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    objective_value: float
    offers: list[PrincipalOfferRecord] = Field(default_factory=list)
    per_year_volumes: list[PrincipalYearVolumes] = Field(default_factory=list)
    offered_cohort_years: int = Field(ge=0)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    solve_seconds: float = Field(ge=0.0)
    data_path: Path | None = None
    csv_path: Path | None = None
    manifest_path: Path | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly run summary."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "horizon": self.horizon,
            "cohorts": self.cohorts,
            "objective_value": round(self.objective_value, 2),
            "offered_cohort_years": self.offered_cohort_years,
            "per_year_volumes_m3": {
                str(volumes.year): {
                    "green": round(volumes.green_volume_m3, 2),
                    "burned": round(volumes.burned_volume_m3, 2),
                }
                for volumes in self.per_year_volumes
            },
            "lp_rows": self.lp_rows,
            "lp_columns": self.lp_columns,
            "solve_seconds": round(self.solve_seconds, 3),
            "artifacts": {
                "data": str(self.data_path),
                "csv": str(self.csv_path),
                "manifest": str(self.manifest_path),
            },
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
        }


class AgentRunConfig(BaseModel):
    """Configuration for one agent-LP solve at cohort granularity.

    Mirrors :class:`PrincipalRunConfig`: same boundary inputs (stands, ARE
    cohorts, yields), 1-year timesteps counted by ``horizon``. Offered
    fractions are an input: either ``offers_path`` (a principal offer table
    with ``cohort_id``/``year``/``offer_fraction`` columns) or a uniform
    ``default_offer_fraction`` applied to every cohort-year. ``decay_rate``
    is the annual retention of unsalvaged burned volume and
    ``discount_rate`` drives the NPV divisor ``(1 + discount_rate) ** year``.
    """

    run_id: str = "tsa29-agent"
    stands_path: Path
    are_path: Path
    yields_path: Path
    horizon: int = 10
    decay_rate: float = 0.85
    discount_rate: float = 0.03
    default_offer_fraction: float = 1.0
    offers_path: Path | None = None
    output_root: Path
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    @field_validator("horizon")
    @classmethod
    def _validate_horizon(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("horizon must be a positive number of 1-year timesteps")
        return value

    @field_validator("decay_rate", "default_offer_fraction")
    @classmethod
    def _validate_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("fraction parameters must lie in [0, 1]")
        return value

    @field_validator("discount_rate")
    @classmethod
    def _validate_discount_rate(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("discount_rate cannot be negative")
        return value

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> AgentRunConfig:
        """Read an agent run config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class AgentDecisionRecord(BaseModel):
    """One (cohort, year) agent action, emitted including zeros."""

    cohort_id: str
    year: int = Field(ge=1)
    harvest_fraction: float = Field(ge=0.0, le=1.0)
    salvage_fraction: float = Field(ge=0.0, le=1.0)
    harvest_volume_m3: float = Field(ge=0.0)
    salvage_volume_m3: float = Field(ge=0.0)


class AgentYearVolumes(BaseModel):
    """Aggregate agent-side volumes (m3) of one timestep.

    ``live_volume_m3``/``burned_volume_m3`` are end-of-year inventories;
    ``burn_influx_m3`` is the volume that burned during the year after the
    year's harvest was removed.
    """

    year: int = Field(ge=1)
    harvest_volume_m3: float = Field(ge=0.0)
    salvage_volume_m3: float = Field(ge=0.0)
    burn_influx_m3: float = Field(ge=0.0)
    live_volume_m3: float = Field(ge=0.0)
    burned_volume_m3: float = Field(ge=0.0)


class AgentManifest(BaseModel):
    """Evidence manifest for one agent-LP solve."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    stands_path: Path
    are_path: Path
    yields_path: Path
    offers_path: Path | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str
    horizon: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    objective_value: float
    active_cohort_years: int = Field(ge=0)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    solve_seconds: float = Field(ge=0.0)
    source_sha256: dict[str, str] = Field(default_factory=dict)
    config: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> AgentManifest:
        """Read a run manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class AgentResult(BaseModel):
    """Typed result of one agent-LP solve."""

    run_id: str
    status: str
    horizon: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    objective_value: float
    decisions: list[AgentDecisionRecord] = Field(default_factory=list)
    per_year_volumes: list[AgentYearVolumes] = Field(default_factory=list)
    active_cohort_years: int = Field(ge=0)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    solve_seconds: float = Field(ge=0.0)
    data_path: Path | None = None
    csv_path: Path | None = None
    manifest_path: Path | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly run summary."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "horizon": self.horizon,
            "cohorts": self.cohorts,
            "objective_value": round(self.objective_value, 2),
            "active_cohort_years": self.active_cohort_years,
            "per_year_volumes_m3": {
                str(volumes.year): {
                    "harvest": round(volumes.harvest_volume_m3, 2),
                    "salvage": round(volumes.salvage_volume_m3, 2),
                    "burn_influx": round(volumes.burn_influx_m3, 2),
                    "live_end": round(volumes.live_volume_m3, 2),
                    "burned_end": round(volumes.burned_volume_m3, 2),
                }
                for volumes in self.per_year_volumes
            },
            "lp_rows": self.lp_rows,
            "lp_columns": self.lp_columns,
            "solve_seconds": round(self.solve_seconds, 3),
            "artifacts": {
                "data": str(self.data_path),
                "csv": str(self.csv_path),
                "manifest": str(self.manifest_path),
            },
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
        }


class IngestManifest(BaseModel):
    """Evidence manifest for one ingestion run."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    source_file: Path
    source_sha256: str = ""
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


class WS3Result(BaseModel):
    """Summary result of one full-TSA WS3 solve."""

    run_id: str
    status: str
    periods: int = Field(ge=0)
    period_length: int = 10
    objective_value: float = Field(ge=0.0)
    schedule_row_counts: dict[str, int] = Field(default_factory=dict)
    lp_rows: int = Field(default=0, ge=0)
    lp_columns: int = Field(default=0, ge=0)
    per_period_volumes_m3: dict[str, float] = Field(default_factory=dict)
    per_period_area_ha: dict[str, float] = Field(default_factory=dict)
    solve_seconds: float = Field(ge=0.0)
    data_path: Path
    csv_path: Path
    manifest_path: Path
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly run summary."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "periods": self.periods,
            "period_length": self.period_length,
            "objective_value": round(self.objective_value, 2),
            "schedule_row_counts": dict(sorted(self.schedule_row_counts.items())),
            "per_period_volumes_m3": {
                period: round(volume, 2)
                for period, volume in sorted(self.per_period_volumes_m3.items())
            },
            "per_period_area_ha": {
                period: round(area, 2)
                for period, area in sorted(self.per_period_area_ha.items())
            },
            "artifacts": {
                "data": str(self.data_path),
                "csv": str(self.csv_path),
                "manifest": str(self.manifest_path),
            },
            "solve_seconds": round(self.solve_seconds, 3),
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
        }


def _default_subsidy_rate_per_m3() -> float:
    """Return the shared subsidy default.

    Deferred import: ``data`` already imports this module, so a module-level
    import here would be circular.
    """

    from fresh_salvage.data import SUBSIDY_RATE_PER_M3

    return SUBSIDY_RATE_PER_M3


class RHRunConfig(BaseModel):
    """Configuration for one rolling-horizon coupled WS3/principal/agent run.

    ``horizon`` WS3 periods (of ``period_length`` years each) are solved per
    step, but only the first period (``period_length`` years) is implemented;
    ``steps`` counts the implemented rolling-horizon steps. The principal and
    agent LPs run at 1-year timesteps over the implemented window
    (``horizon = period_length`` on their side). ``decay_rate`` is the annual
    retention of unsalvaged burned volume and ``discount_rate`` drives the
    agent NPV divisor; both default to the standalone principal/agent values.

    ``subsidy_rate_per_m3`` is the salvage subsidy ($/m3 of burned volume
    salvaged) charged in the principal cashflow and paid in the agent salvage
    margin; it defaults to the shared ``data.SUBSIDY_RATE_PER_M3`` constant.
    ``burn_rate_multiplier`` scales every MFRI-derived annual burn rate
    (1.0 = the MFRI table as published; 0.0 = a fire-free counterfactual);
    it is the ensemble axis for future-fire-pattern assumptions.
    """

    run_id: str = "tsa29-rh"
    stands_path: Path
    yields_path: Path
    bridge_path: Path
    base_year: int = 2025
    horizon: int = 15
    period_length: int = 10
    steps: int = 10
    max_age: int = 999
    workers: int = 64
    age_smashing: AgeSmashing = Field(default_factory=AgeSmashing)
    objective: WS3Objective = Field(default_factory=WS3Objective)
    aac_annual_m3: float = 2_937_509
    decay_rate: float = 0.85
    discount_rate: float = 0.03
    burned_limit_annual_m3: float | None = None
    subsidy_rate_per_m3: float = Field(default_factory=_default_subsidy_rate_per_m3)
    burn_rate_multiplier: float = 1.0
    output_root: Path
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("run_id must not be empty")
        return text

    @field_validator("base_year", "horizon", "period_length", "steps", "workers")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(
                "base_year, horizon, period_length, steps, and workers must be positive"
            )
        return value

    @field_validator("aac_annual_m3")
    @classmethod
    def _validate_aac(cls, value: float) -> float:
        if value < 0:
            raise ValueError("aac_annual_m3 cannot be negative")
        return value

    @field_validator("decay_rate")
    @classmethod
    def _validate_decay_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("decay_rate must lie in [0, 1]")
        return value

    @field_validator("discount_rate")
    @classmethod
    def _validate_discount_rate(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("discount_rate cannot be negative")
        return value

    @field_validator("burned_limit_annual_m3")
    @classmethod
    def _validate_burned_limit(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("burned_limit_annual_m3 cannot be negative")
        return value

    @field_validator("subsidy_rate_per_m3")
    @classmethod
    def _validate_subsidy_rate(cls, value: float) -> float:
        if value < 0:
            raise ValueError("subsidy_rate_per_m3 cannot be negative")
        return value

    @field_validator("burn_rate_multiplier")
    @classmethod
    def _validate_burn_rate_multiplier(cls, value: float) -> float:
        if value < 0:
            raise ValueError("burn_rate_multiplier cannot be negative")
        return value

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> RHRunConfig:
        """Read a rolling-horizon run config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class RHStepRecord(BaseModel):
    """Typed record of one implemented rolling-horizon step (one JSONL line)."""

    step: int = Field(ge=1)
    start_year: int
    ws3_status: str
    ws3_objective_value: float
    ws3_build_seconds: float = Field(ge=0.0)
    ws3_solve_seconds: float = Field(ge=0.0)
    principal_objective_value: float
    principal_solve_seconds: float = Field(ge=0.0)
    agent_objective_value: float
    agent_solve_seconds: float = Field(ge=0.0)
    annual_green_harvest_m3: list[float] = Field(default_factory=list)
    annual_burned_harvest_m3: list[float] = Field(default_factory=list)
    area_burned_ha: float = Field(ge=0.0)
    wall_seconds: float = Field(ge=0.0)


class RHManifest(BaseModel):
    """Evidence manifest for one rolling-horizon coupled run."""

    manifest_version: str = MANIFEST_VERSION
    run_id: str
    stands_path: Path
    yields_path: Path
    bridge_path: Path
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str
    steps: int = Field(ge=1)
    horizon: int = Field(ge=1)
    period_length: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    total_green_harvest_m3: float = Field(ge=0.0)
    total_burned_harvest_m3: float = Field(ge=0.0)
    total_area_burned_ha: float = Field(ge=0.0)
    wall_seconds: float = Field(ge=0.0)
    source_sha256: dict[str, str] = Field(default_factory=dict)
    step_records: list[RHStepRecord] = Field(default_factory=list)
    config: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> RHManifest:
        """Read a run manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class RHResult(BaseModel):
    """Typed result of one rolling-horizon coupled run."""

    run_id: str
    status: str
    steps: int = Field(ge=1)
    horizon: int = Field(ge=1)
    period_length: int = Field(ge=1)
    cohorts: int = Field(ge=0)
    step_records: list[RHStepRecord] = Field(default_factory=list)
    decadal_green_harvest_m3: list[float] = Field(default_factory=list)
    decadal_burned_harvest_m3: list[float] = Field(default_factory=list)
    decadal_area_burned_ha: list[float] = Field(default_factory=list)
    final_age_distribution_ha: dict[str, float] = Field(default_factory=dict)
    wall_seconds: float = Field(ge=0.0)
    steps_path: Path | None = None
    final_state_path: Path | None = None
    manifest_path: Path | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly run summary."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "steps": self.steps,
            "horizon": self.horizon,
            "period_length": self.period_length,
            "cohorts": self.cohorts,
            "wall_seconds": round(self.wall_seconds, 3),
            "decadal_green_harvest_m3": [
                round(volume, 2) for volume in self.decadal_green_harvest_m3
            ],
            "decadal_burned_harvest_m3": [
                round(volume, 2) for volume in self.decadal_burned_harvest_m3
            ],
            "decadal_area_burned_ha": [
                round(area, 2) for area in self.decadal_area_burned_ha
            ],
            "final_age_distribution_ha": {
                age: round(area, 2)
                for age, area in sorted(
                    self.final_age_distribution_ha.items(), key=lambda item: int(item[0])
                )
            },
            "per_step": [
                {
                    "step": record.step,
                    "ws3_objective_value": round(record.ws3_objective_value, 2),
                    "principal_objective_value": round(
                        record.principal_objective_value, 2
                    ),
                    "agent_objective_value": round(record.agent_objective_value, 2),
                    "wall_seconds": round(record.wall_seconds, 3),
                }
                for record in self.step_records
            ],
            "artifacts": {
                "steps": str(self.steps_path),
                "final_state": str(self.final_state_path),
                "manifest": str(self.manifest_path),
            },
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
        }


class EnsembleConfig(BaseModel):
    """Configuration for one scenario ensemble of rolling-horizon runs.

    The scenario grid is the cartesian product of the named ``axes``: every
    axis is an explicit ``RHRunConfig`` field name mapped to the values it
    takes across the ensemble (no positional ambiguity). ``base`` carries
    the shared ``RHRunConfig`` field values (input paths, horizon, steps,
    per-scenario WS3 ``workers``...). The driver owns ``run_id`` and
    ``output_root`` per scenario, so both are reserved and must not appear
    in ``base`` or ``axes``; ``bridge_path`` is required in ``base`` (the
    bridge source) but reserved as an axis, since every scenario is bound to
    the once-prebuilt shared bridge. Grid-shape violations raise
    :class:`fresh_salvage.ensemble.EnsembleError` with a structured code at
    expansion time.
    """

    ensemble_id: str = "tsa29-ensemble"
    base: dict[str, object] = Field(default_factory=dict)
    axes: dict[str, list[object]] = Field(default_factory=dict)
    max_workers: int = 4
    output_root: Path
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("ensemble_id")
    @classmethod
    def _validate_ensemble_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("ensemble_id must not be empty")
        return text

    @field_validator("max_workers")
    @classmethod
    def _validate_max_workers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_workers must be positive")
        return value

    def write_json(self, path: Path) -> Path:
        """Write this config as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> EnsembleConfig:
        """Read an ensemble config from JSON or YAML."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return cls.model_validate(_load_yaml(text))
        return cls.model_validate_json(text)


class ScenarioRecord(BaseModel):
    """Outcome record of one ensemble scenario (one JSONL line).

    ``status`` is the rolling-horizon run status on success (``"optimal"``)
    or ``"failed"``; a failed scenario carries the structured error code of
    the underlying exception (``RHError.code``) or the exception type name.
    """

    name: str
    run_id: str
    overrides: dict[str, object] = Field(default_factory=dict)
    status: str
    error_code: str | None = None
    error_message: str | None = None
    wall_seconds: float = Field(ge=0.0)
    output_root: Path
    manifest_path: Path | None = None
    steps_path: Path | None = None


class EnsembleManifest(BaseModel):
    """Evidence manifest for one scenario-ensemble run."""

    manifest_version: str = MANIFEST_VERSION
    ensemble_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str
    scenario_count: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    max_workers: int = Field(ge=1)
    wall_seconds: float = Field(ge=0.0)
    source_sha256: dict[str, str] = Field(default_factory=dict)
    scenarios: list[ScenarioRecord] = Field(default_factory=list)
    config: dict[str, object] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        """Write this manifest as formatted JSON."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> EnsembleManifest:
        """Read an ensemble manifest from JSON."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class EnsembleResult(BaseModel):
    """Typed result of one scenario-ensemble run."""

    ensemble_id: str
    status: str
    scenario_count: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    max_workers: int = Field(ge=1)
    wall_seconds: float = Field(ge=0.0)
    scenarios: list[ScenarioRecord] = Field(default_factory=list)
    scenarios_path: Path | None = None
    manifest_path: Path | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-friendly ensemble summary."""

        return {
            "ensemble_id": self.ensemble_id,
            "status": self.status,
            "scenario_count": self.scenario_count,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "max_workers": self.max_workers,
            "wall_seconds": round(self.wall_seconds, 3),
            "scenarios": [
                {
                    "name": record.name,
                    "status": record.status,
                    "error_code": record.error_code,
                    "wall_seconds": round(record.wall_seconds, 3),
                    "output_root": str(record.output_root),
                }
                for record in self.scenarios
            ],
            "artifacts": {
                "scenarios": str(self.scenarios_path),
                "manifest": str(self.manifest_path),
            },
            "diagnostics": [diagnostic.model_dump() for diagnostic in self.diagnostics],
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
    "AgentDecisionRecord",
    "AgentManifest",
    "AgentResult",
    "AgentRunConfig",
    "AgentYearVolumes",
    "AgeSmashing",
    "ArtifactLayout",
    "DevelopmentType",
    "Diagnostic",
    "EnsembleConfig",
    "EnsembleManifest",
    "EnsembleResult",
    "FireDefaults",
    "IngestManifest",
    "IngestResult",
    "MANIFEST_VERSION",
    "PrincipalManifest",
    "PrincipalOfferRecord",
    "PrincipalResult",
    "PrincipalRunConfig",
    "PrincipalYearVolumes",
    "RHManifest",
    "RHResult",
    "RHRunConfig",
    "RHStepRecord",
    "ScenarioInputs",
    "ScenarioRecord",
    "ScenarioRunConfig",
    "SeverityMapping",
    "Stand",
    "WS3Manifest",
    "WS3Objective",
    "WS3Result",
    "WS3RunConfig",
    "safe_slug",
]
