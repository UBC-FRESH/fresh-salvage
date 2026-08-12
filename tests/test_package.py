"""Basic package import and metadata tests."""

from importlib import metadata

import masc_yunhao_xu_linear


def test_version_is_exposed() -> None:
    assert masc_yunhao_xu_linear.__version__ == "0.1.0a1"


def test_package_metadata_matches_version() -> None:
    assert metadata.version("masc-yunhao-xu-linear") == masc_yunhao_xu_linear.__version__
