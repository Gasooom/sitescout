"""scripts/evaluate.py calls the package; exit code 1 when the evaluation stops."""

import importlib.util
import logging
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT
from sitescout.evaluation import EvaluationError
from sitescout.ingest import SourceMissingError

SCRIPT = PROJECT_ROOT / "scripts" / "evaluate.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("evaluate_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def test_exit_0_when_the_report_is_written(script, monkeypatch):
    monkeypatch.setattr(script, "run_evaluation", lambda config, processed: {})
    assert script.main() == 0


@pytest.mark.parametrize(
    "error",
    [EvaluationError("no scores"), SourceMissingError("scores_backtest.parquet does not exist")],
)
def test_exit_1_when_the_evaluation_stops(script, monkeypatch, capsys, error):
    def stop(config, processed):
        raise error

    monkeypatch.setattr(script, "run_evaluation", stop)
    assert script.main() == 1
    assert str(error) in capsys.readouterr().err
