"""scripts/features.py calls the package; exit code 1 when feature engineering stops."""

import importlib.util
import logging
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT, PendingParameterError
from sitescout.features import FeatureError
from sitescout.ingest.layers import LayerResult

SCRIPT = PROJECT_ROOT / "scripts" / "features.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("features_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def test_exit_0_when_both_layers_are_written(script, monkeypatch):
    written = [
        LayerResult("features_production", "ok", 300, Path("features_production.parquet"), 1, "x"),
        LayerResult("features_backtest", "ok", 300, Path("features_backtest.parquet"), 1, "y"),
    ]
    monkeypatch.setattr(script, "run_features", lambda settings, processed: written)
    assert script.main() == 0


@pytest.mark.parametrize(
    "error",
    [
        FeatureError("The national median of mapped power features per km² is 0"),
        PendingParameterError("sources.charger_match_radius_m is pending"),
    ],
)
def test_exit_1_when_feature_engineering_stops(script, monkeypatch, capsys, error):
    def stop(settings, processed):
        raise error

    monkeypatch.setattr(script, "run_features", stop)
    assert script.main() == 1
    assert str(error) in capsys.readouterr().err
