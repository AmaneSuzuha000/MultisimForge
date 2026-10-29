"""Shared pytest configuration.

Building a circuit needs a local part library (harvested from the user's own
Multisim installation, never committed). Tests that need it are marked
needs_parts and skipped when the library is absent, so the public CI job
stays green without shipping NI data.
"""
from pathlib import Path

import pytest

from multisimforge import config


def _has_parts() -> bool:
    lib = Path(config.CONFIG["library_dir"])
    return (lib / "index.json").is_file() and (lib / "_struct" / "shell.xml").is_file()


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_parts: requires a harvested part library")


def pytest_collection_modifyitems(items):
    if _has_parts():
        return
    skip = pytest.mark.skip(reason="part library not available (run 'multisimforge parts init' first)")
    for item in items:
        if "needs_parts" in item.keywords:
            item.add_marker(skip)