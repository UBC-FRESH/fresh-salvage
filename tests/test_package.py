"""Basic package import and metadata tests."""

from importlib import metadata

import fresh_salvage


def test_version_is_exposed() -> None:
    assert fresh_salvage.__version__ == "0.1.0a1"


def test_package_metadata_matches_version() -> None:
    assert metadata.version("fresh-salvage") == fresh_salvage.__version__
