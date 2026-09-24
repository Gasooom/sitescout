"""scripts/candidates.py calls the package; exit code 1 when generation stops."""

import importlib.util
import logging
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sitescout.candidates import CandidateBudgetError
from sitescout.config import PROJECT_ROOT
from sitescout.ingest.layers import LayerResult

SCRIPT = PROJECT_ROOT / "scripts" / "candidates.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("candidates_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def test_exit_0_when_candidates_are_written(script, monkeypatch):
    written = [
        LayerResult("candidates_eligible", "ok", 595, Path("candidates_eligible.parquet"), 1, "x"),
        LayerResult("candidates", "ok", 300, Path("candidates.parquet"), 1, "y"),
    ]
    monkeypatch.setattr(script, "run_candidates", lambda settings, processed: written)
    assert script.main() == 0


def test_exit_1_when_fewer_candidates_are_eligible_than_the_budget(script, monkeypatch, capsys):
    def stop(settings, processed):
        raise CandidateBudgetError("250 eligible candidates, fewer than the budget of 300")

    monkeypatch.setattr(script, "run_candidates", stop)
    assert script.main() == 1
    assert "250 eligible candidates" in capsys.readouterr().err
