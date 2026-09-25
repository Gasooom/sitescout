"""scripts/briefs.py calls the package; exit code 1 when brief generation stops."""

import importlib.util
import logging
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.briefs import BriefError
from sitescout.config import PROJECT_ROOT
from sitescout.ingest import SourceMissingError

SCRIPT = PROJECT_ROOT / "scripts" / "briefs.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("briefs_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def test_exit_0_when_the_briefs_are_written(script, monkeypatch):
    monkeypatch.setattr(script, "run_briefs", lambda config, processed: {})
    assert script.main() == 0


@pytest.mark.parametrize(
    "error",
    [
        BriefError("Grounding 0.99 is below the target 1.0"),
        SourceMissingError("network.parquet does not exist"),
    ],
)
def test_exit_1_when_brief_generation_stops(script, monkeypatch, capsys, error):
    def stop(config, processed):
        raise error

    monkeypatch.setattr(script, "run_briefs", stop)
    assert script.main() == 1
    assert str(error) in capsys.readouterr().err
