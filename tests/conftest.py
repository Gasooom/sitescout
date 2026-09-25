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


@pytest.fixture(scope="module")
def analyst_world() -> Iterator[tuple[Any, Path, Path]]:
    """One SYNTHETIC world through M6 (one network site) and M7 (its brief), per test module.

    The M9 analyst tests only read it (the tools never write, which test_analyst_tools
    checks), so each module builds it once instead of once per test: a few solver runs
    instead of dozens, and a much faster suite. Yields (config, processed dir, briefs dir).
    """
    from sitescout.briefs import run_briefs
    from sitescout.features import run_features
    from sitescout.optimize import run_network
    from sitescout.scoring import run_scores
    from synthetic_features import feature_config, write_feature_world

    config = feature_config(
        read_yaml(SETTINGS_FILE), read_yaml(WEIGHTS_FILE), **{"settings.optimization.n_sites": 1}
    )
    with tempfile.TemporaryDirectory(prefix="sitescout-test-") as name:
        processed, briefs = Path(name) / "processed", Path(name) / "briefs"
        processed.mkdir()
        write_feature_world(processed, config.settings)
        run_features(config.settings, processed)
        run_scores(config.settings, config.weights, processed)
        run_network(config, processed)
        run_briefs(config, processed, briefs)
        yield config, processed, briefs


@pytest.fixture
def dirs(temp_dir):
    """Empty raw/, processed/ and work/ directories for one SYNTHETIC pipeline run."""
    paths = {name: temp_dir / name for name in ("raw", "processed", "work")}
    for path in paths.values():
        path.mkdir()
    return paths
