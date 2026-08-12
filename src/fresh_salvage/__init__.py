"""Linear principal-agent salvage-subsidy pipeline for TSA29 Williams Lake."""

from fresh_salvage.data import ingest
from fresh_salvage.models import ArtifactLayout, ScenarioRunConfig, Stand

__version__ = "0.1.0a1"

__all__ = [
    "ArtifactLayout",
    "ScenarioRunConfig",
    "Stand",
    "__version__",
    "ingest",
]
