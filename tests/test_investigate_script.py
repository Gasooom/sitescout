"""scripts/investigate.py: network_summary and nearby_sites print structured JSON (M10 Phase 2)."""

import dataclasses
import importlib.util
import json
import logging
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT
from sitescout.ingest import SourceMissingError
from sitescout.investigation import InvestigationData

SCRIPT = PROJECT_ROOT / "scripts" / "investigate.py"


@pytest.fixture
def load(monkeypatch, analyst_world) -> Iterator:
    """Run the script's ``main`` on the SYNTHETIC world instead of the real processed outputs."""
    config, processed, _ = analyst_world
    data = InvestigationData.load(config, processed)
    spec = importlib.util.spec_from_file_location("investigate_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.InvestigationData, "load", classmethod(lambda cls, c, p: data))
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level

    def run(*argv):
        monkeypatch.setattr("sys.argv", [str(SCRIPT), *argv])
        return module

    run.data = data
    yield run
    root.handlers[:] = handlers
    root.setLevel(level)


def test_network_summary_prints_the_structured_result_as_json(load, capsys):
    assert load("network_summary").main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tool"] == "network_summary" and result["mode"] == "production"
    assert [m["key"] for m in result["methods"]] == ["mclp", "greedy", "top30"]
    assert any(r["id"] == "network/mclp/population_covered_share" for r in result["records"])


def test_nearby_sites_prints_the_structured_result_as_json(load, capsys):
    assert load("nearby_sites", "cand-a", "--radius-m", "1").main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tool"] == "nearby_sites" and result["candidate_id"] == "cand-a"
    assert (result["count_within_radius"], result["neighbours"], result["truncated"]) == (
        0,
        [],
        False,
    )


def test_the_output_is_identical_on_every_call(load, capsys):
    outputs = []
    for _ in range(2):
        assert load("network_summary").main() == 0
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (("nearby_sites", "cand-nope", "--radius-m", "500"), "unknown_site"),
        (("nearby_sites", "cand-a", "--radius-m", "0"), "invalid_arguments"),
        (("nearby_sites", "cand-a", "--radius-m", "999999"), "invalid_arguments"),
    ],
)
def test_a_refused_call_prints_its_error_code_and_exits_1(load, capsys, argv, code):
    assert load(*argv).main() == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == code


def test_the_error_code_is_in_the_printed_json(load, capsys):
    assert load("nearby_sites", "cand-nope", "--radius-m", "500").main() == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "unknown_site" and "cand-nope" in error["message"]


def test_an_unavailable_network_summary_exits_1_with_its_code(load, capsys, temp_dir, monkeypatch):
    script = load("network_summary")
    empty = dataclasses.replace(load.data, processed_dir=temp_dir)
    monkeypatch.setattr(script.InvestigationData, "load", classmethod(lambda cls, c, p: empty))
    assert script.main() == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "network_summary_unavailable"


def test_missing_processed_outputs_exit_1_without_output(load, capsys, monkeypatch):
    script = load("network_summary")

    def missing(cls, config, processed):
        raise SourceMissingError("data/processed/scores_production.parquet does not exist")

    monkeypatch.setattr(script.InvestigationData, "load", classmethod(missing))
    assert script.main() == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("summon",),
        ("nearby_sites", "cand-a"),
        ("nearby_sites", "--radius-m", "5"),
        ("nearby_sites", "cand-a", "--radius-m", "5.5"),
        ("nearby_sites", "cand-a", "--radius-m", "far"),
        ("network_summary", "--radius-m", "5"),
    ],
)
def test_invalid_usage_exits_2(load, argv):
    with pytest.raises(SystemExit) as error:
        load(*argv).main()
    assert error.value.code == 2
