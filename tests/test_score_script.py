"""scripts/score.py calls the package; exit code 1 when scoring stops."""

import importlib.util
import logging
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT
from sitescout.ingest import SourceMissingError
from sitescout.ingest.layers import LayerResult
from sitescout.scoring import ScoreError

SCRIPT = PROJECT_ROOT / "scripts" / "score.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("score_script", SCRIPT)
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
        LayerResult("scores_production", "ok", 300, Path("scores_production.parquet"), 1, "x"),
        LayerResult("scores_backtest", "ok", 300, Path("scores_backtest.parquet"), 1, "y"),
    ]
    monkeypatch.setattr(script, "run_scores", lambda settings, weights, processed: written)
    assert script.main() == 0


@pytest.mark.parametrize(
    "error",
    [
        ScoreError("Scores out of bounds (backtest): charging gap differs"),
        SourceMissingError("features_backtest.parquet does not exist"),
    ],
)
def test_exit_1_when_scoring_stops(script, monkeypatch, capsys, error):
    def stop(settings, weights, processed):
        raise error

    monkeypatch.setattr(script, "run_scores", stop)
    assert script.main() == 1
    assert str(error) in capsys.readouterr().err
