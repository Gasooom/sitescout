"""scripts/knowledge.py: verify, list, show, search and eval (Milestone 10, Phase 1)."""

import importlib.util
import json
import logging
from collections.abc import Iterator
from types import ModuleType

import pytest

from sitescout.config import PROJECT_ROOT, ConfigError
from sitescout.knowledge import CorpusError
from sitescout.knowledge.evaluation import EvaluationError

SCRIPT = PROJECT_ROOT / "scripts" / "knowledge.py"


@pytest.fixture
def load(monkeypatch) -> Iterator:
    spec = importlib.util.spec_from_file_location("knowledge_script", SCRIPT)
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


def test_verify_prints_the_counts_and_both_fingerprints(load, capsys):
    assert load("verify").main() == 0
    out = capsys.readouterr().out
    assert out.startswith("Chunks: ") and " in 7 documents (" in out
    fingerprints = [line.split(": ")[1] for line in out.splitlines() if "fingerprint" in line]
    assert len(fingerprints) == 2 and all(len(value) == 64 for value in fingerprints)


def test_list_prints_one_line_per_chunk_and_can_filter_by_document_type(load, capsys):
    assert load("list").main() == 0
    everything = capsys.readouterr().out.splitlines()
    assert load("list", "--doc-type", "overview").main() == 0
    overview = capsys.readouterr().out.splitlines()
    assert len(everything) > 100 and len(overview) == 3
    assert all(line.startswith("kb/readme/") for line in overview)


def test_show_prints_the_metadata_and_the_exact_text(load, capsys):
    assert load("show", "kb/scoring/method/4-score").main() == 0
    out = capsys.readouterr().out
    assert "chunk_id: kb/scoring/method/4-score" in out and "doc_type: method" in out
    assert "### 4. Score" in out and "Score = Σ profile weight × component" in out


def test_show_reports_an_unknown_chunk_id(load, capsys):
    assert load("show", "kb/nothing").main() == 1
    assert capsys.readouterr().out == ""


def test_search_prints_ranked_hits(load, capsys):
    assert load("search", "percentile", "--top-k", "3").main() == 0
    out = capsys.readouterr().out
    assert "chunks match; showing 3" in out
    assert [f"\n{n}. kb/" in out for n in (1, 2, 3)] == [True, True, True]


def test_search_json_is_the_full_reproducible_result(load, capsys):
    assert load("search", "spacing", "--top-k", "2", "--json").main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["query"] == "spacing" and result["top_k"] == 2 and len(result["hits"]) == 2
    assert len(result["corpus_fingerprint"]) == 64 and len(result["retrieval_fingerprint"]) == 64
    assert [hit["rank"] for hit in result["hits"]] == [1, 2]
    assert "text" in result["hits"][0]["chunk"]


def test_search_filters_are_exact_and_combined(load, capsys):
    argv = ("search", "percentile", "--top-k", "5", "--json", "--decision-id", "D-040")
    assert load(*argv).main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["hits"] and {h["chunk"]["decision_id"] for h in result["hits"]} == {"D-040"}
    assert result["filters"]["decision_id"] == "D-040"
    assert load(*argv, "--milestone", "6").main() == 0
    assert json.loads(capsys.readouterr().out)["hits"] == []


def test_top_k_is_required(load):
    with pytest.raises(SystemExit) as error:
        load("search", "percentile").main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "extra",
    [("--top-k", "0"), ("--top-k", "11"), ("--top-k", "3", "--topic", "nonsense")],
)
def test_an_invalid_search_is_an_error_not_an_empty_result(load, capsys, extra):
    assert load("search", "percentile", *extra).main() == 1
    assert capsys.readouterr().out == ""


def test_an_unknown_command_is_invalid_usage(load):
    with pytest.raises(SystemExit) as error:
        load("summon").main()
    assert error.value.code == 2


def test_eval_no_write_prints_the_report_and_the_summary(load, capsys, temp_dir, monkeypatch):
    script = load("eval", "--no-write")
    real = script.run_evaluation
    seen = {}

    def spy(config, write):
        seen["write"] = write
        return real(config, write=write, report_path=temp_dir / "never.md")

    monkeypatch.setattr(script, "run_evaluation", spy)
    assert script.main() == 0
    out = capsys.readouterr().out
    assert seen == {"write": False} and not (temp_dir / "never.md").exists()
    assert out.startswith("# Knowledge retrieval evaluation")
    for text in ("Gold queries: 42 (36 in scope)", "Gold set review status: draft", "MRR:"):
        assert text in out
    for k in (1, 3, 5, 10):
        assert f"Hit@{k}: " in out and f"Recall@{k}: " in out


def test_eval_writes_the_report_to_the_configured_path(load, capsys, temp_dir, monkeypatch):
    script = load("eval")
    real = script.run_evaluation
    target = temp_dir / "knowledge_eval.md"
    monkeypatch.setattr(
        script,
        "run_evaluation",
        lambda config, write: real(config, write=write, report_path=target),
    )
    assert script.main() == 0
    assert target.read_text(encoding="utf-8").startswith("# Knowledge retrieval evaluation")
    assert "Report: reports/knowledge_eval.md" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("command", "attribute", "error"),
    [
        ("verify", "verify_corpus", CorpusError("the corpus is broken")),
        ("eval", "run_evaluation", EvaluationError("the gold set is invalid")),
        ("verify", "load_config", ConfigError("no settings")),
    ],
)
def test_a_corpus_gold_or_config_problem_exits_1_with_the_message_logged(
    load, monkeypatch, capsys, command, attribute, error
):
    script = load(command)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(script, attribute, fail)
    assert script.main() == 1
    assert str(error) in capsys.readouterr().err
