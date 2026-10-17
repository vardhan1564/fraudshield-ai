"""Scaffold check: every backend package imports."""

import importlib

import pytest

PACKAGES = [
    "app",
    "app.api.v1",
    "app.services",
    "app.models",
    "app.schemas",
    "app.streaming",
    "app.jobs",
    "app.core",
    "app.ml_runtime",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None
