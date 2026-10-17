"""Scaffold check: every ml package imports."""

import importlib

import pytest

PACKAGES = ["ml", "ml.simulator", "ml.features", "ml.models", "ml.pipelines"]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None
