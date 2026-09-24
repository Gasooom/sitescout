"""scripts/ingest.py parses arguments and calls the package; its exit code reflects failures."""

import importlib.util
import logging
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT
from sitescout.ingest.pipeline import SourceReport

SCRIPT = PROJECT_ROOT / "scripts" / "ingest.py"


@pytest.fixture
def script(monkeypatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("ingest_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield module
    root.handlers[:] = handlers
    root.setLevel(level)


def _fake(status_by_source: dict[str, str], calls: list[tuple[str, tuple[str, ...]]], stage):
    def run(config, sources, **kwargs):
        calls.append((stage, sources))
        return [SourceReport(s, status_by_source.get(s, "ok"), 0.0) for s in sources]

    return run


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [({}, 0), ({"chargers": "missing"}, 0), ({"grid": "failed"}, 1)],
    ids=["all-ok", "chargers-missing", "a-source-failed"],
)
def test_exit_code(script, monkeypatch, statuses, expected):
    calls: list = []
    for stage, name in [("fetch", "fetch_sources"), ("process", "process_sources")]:
        monkeypatch.setattr(script, name, _fake(statuses, calls, stage))
    monkeypatch.setattr(script, "validate_outputs", _fake(statuses, calls, "validate"))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "all"])
    assert script.main() == expected


def test_a_failed_source_is_not_processed_further(script, monkeypatch):
    calls: list = []
    monkeypatch.setattr(script, "fetch_sources", _fake({"osm": "failed"}, calls, "fetch"))
    monkeypatch.setattr(script, "process_sources", _fake({}, calls, "process"))
    monkeypatch.setattr(script, "validate_outputs", _fake({}, calls, "validate"))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "all", "--source", "osm", "--source", "grid"])
    assert script.main() == 1
    assert calls == [
        ("fetch", ("grid", "osm")),
        ("process", ("grid",)),
        ("validate", ("grid",)),
    ]
