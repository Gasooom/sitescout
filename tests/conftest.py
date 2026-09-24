"""Shared fixtures: fresh copies of the real config files, and an empty temporary directory."""

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
