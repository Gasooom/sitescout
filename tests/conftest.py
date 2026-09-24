"""Shared fixtures: fresh copies of the real config files, temporary directories and the
SYNTHETIC ingestion configuration."""

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sitescout.config import SETTINGS_FILE, WEIGHTS_FILE, read_yaml


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """An empty temporary directory, removed after the test.

    Used instead of pytest's ``tmp_path``, which also creates a "current" symlink for each
    test; on the Windows development machine every symlink call takes about 4 seconds.
    """
    with tempfile.TemporaryDirectory(prefix="sitescout-test-") as name:
        yield Path(name)


@pytest.fixture
def settings_data() -> dict[str, Any]:
    """A fresh copy of config/settings.yaml."""
    return read_yaml(SETTINGS_FILE)


@pytest.fixture
def weights_data() -> dict[str, Any]:
    """A fresh copy of config/weights.yaml."""
    return read_yaml(WEIGHTS_FILE)


@pytest.fixture
def config(settings_data, weights_data):
    """The real configuration, with boundary counts matching the SYNTHETIC districts."""
    from synthetic import synthetic_config

    return synthetic_config(settings_data, weights_data)


@pytest.fixture
def dirs(temp_dir):
    """Empty raw/, processed/ and work/ directories for one SYNTHETIC pipeline run."""
    paths = {name: temp_dir / name for name in ("raw", "processed", "work")}
    for path in paths.values():
        path.mkdir()
    return paths
