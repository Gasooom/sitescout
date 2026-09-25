"""scripts/ask.py: --tools-only calls one tool and prints its JSON (M9 phase 1); a question
runs the analyst loop and prints the validated answer or the labelled fallback (phase 3b)."""

import importlib.util
import json
import logging
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.analyst import AnalystError, Answer, RunResult, Statement
from sitescout.config import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "scripts" / "ask.py"


@pytest.fixture
def load(monkeypatch) -> Iterator:
    spec = importlib.util.spec_from_file_location("ask_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level

    def run(*argv):
        monkeypatch.setattr("sys.argv", [str(SCRIPT), *argv])
        return module

    yield run
    root.handlers[:] = handlers
    root.setLevel(level)


class _Result:
    def model_dump(self):
        return {"tool": "get_site", "candidate_id": "cand-x", "records": []}


def test_tools_only_prints_the_structured_result(load, monkeypatch, capsys):
    script = load("--tools-only", "get_site", "cand-x")
    monkeypatch.setattr(script.AnalystData, "load", classmethod(lambda cls, c, p: object()))
    monkeypatch.setattr(script, "get_site", lambda data, cid: _Result())
    assert script.main() == 0
    assert json.loads(capsys.readouterr().out)["candidate_id"] == "cand-x"


def test_a_question_runs_the_analyst_and_prints_its_answer(load, monkeypatch, capsys):
    script = load("Why is cand-x not in the network?")
    seen = {}

    def fake_ask(config, question):
        seen["question"] = question
        return RunResult(
            question=question,
            status="answered",
            answer=Answer(direct_answer=(Statement(text="It is unknown.", kind="UNKNOWN"),)),
        )

    monkeypatch.setattr(script, "ask", fake_ask)
    assert script.main() == 0
    assert seen["question"] == "Why is cand-x not in the network?"
    assert "It is unknown." in capsys.readouterr().out


def test_a_missing_key_prints_the_labelled_fallback_and_exits_1(load, monkeypatch, capsys):
    # Whichever provider is configured live (config/settings.yaml): its own key is missing.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    script = load("Why is cand-x not in the network?")
    assert script.main() == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "no AI summary"
    from sitescout.config import load_config

    variable = (
        "ANTHROPIC_API_KEY"
        if load_config().settings.analyst.provider == "anthropic"
        else "OPENAI_API_KEY"
    )
    assert f"{variable} is not set" in out


def test_a_question_is_required_without_tools_only(load):
    with pytest.raises(SystemExit) as stop:
        load().main()
    assert stop.value.code == 2


def test_a_refused_tool_call_exits_1(load, monkeypatch, capsys):
    script = load("--tools-only", "get_site", "cand-x")
    monkeypatch.setattr(script.AnalystData, "load", classmethod(lambda cls, c, p: object()))

    def refuse(data, cid):
        raise AnalystError("Unknown site id 'cand-x'")

    monkeypatch.setattr(script, "get_site", refuse)
    assert script.main() == 1
    assert "Unknown site id" in capsys.readouterr().err


def test_find_sites_arguments_become_an_exact_query(load, monkeypatch, capsys):
    script = load("--tools-only", "find_sites", "--district", "Huye", "--selected-mclp", "yes")
    monkeypatch.setattr(script.AnalystData, "load", classmethod(lambda cls, c, p: object()))
    seen = {}

    def fake_find(data, query):
        seen["query"] = query
        return _Result()

    monkeypatch.setattr(script, "find_sites", fake_find)
    assert script.main() == 0
    assert seen["query"].district == "Huye" and seen["query"].selected_mclp is True
    assert seen["query"].selected_top30 is None
