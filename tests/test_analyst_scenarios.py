"""M9 phase 3c: the scenario set and the deterministic evaluation harness.

Offline only: the model is a scripted ``FakeModel`` (a small "reference" model that looks
the site up and copies its score), the world is SYNTHETIC, and no test calls the API. The
real scenario file and, when present, the real processed outputs are checked as they are.
"""

import importlib.util
import json
import logging
import re

import pytest

from sitescout.analyst import FakeModel, ModelStep, ToolCallRequest, run_analyst
from sitescout.analyst.run import RunResult
from sitescout.analyst.scenarios import (
    CATEGORIES,
    EvaluationError,
    Expectation,
    FirstAnswerSabotage,
    Scenario,
    ScenarioSet,
    evaluate,
    format_probes,
    load_scenarios,
    preflight,
    render_report,
    run_evaluation,
    run_scenario,
    session_from_transcript,
)
from sitescout.analyst.tools import AnalystData, FindQuery, find_sites
from sitescout.analyst.validate import Answer, Statement, ValidationResult, validate_answer
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.config import PROJECT_ROOT, load_config

SCENARIO_FILE = PROJECT_ROOT / "tests" / "analyst_scenarios.yaml"
REAL = PROJECT_ROOT / "data" / "processed"
needs_real = pytest.mark.skipif(
    not (REAL / "evidence.json").is_file(), reason="real processed outputs are not present"
)


@pytest.fixture
def world(analyst_world):
    config, processed, _ = analyst_world
    return config, processed


@pytest.fixture
def data(world):
    return AnalystData.load(*world)


@pytest.fixture
def sites(data):
    found = find_sites(data, FindQuery()).sites
    network = next(s.candidate_id for s in found if s.selected_mclp)
    hosted_other = next(
        s.candidate_id
        for s in found
        if not s.selected_mclp and data.site(s.candidate_id)["host_type"] != "none"
    )
    return network, hosted_other


def reference_model() -> FakeModel:
    """Looks the site in the question up, then states its score, copied exactly."""

    def step(context):
        candidate_id = re.search(r"cand-[a-z0-9]+", context.question).group(0)
        if not context.transcript:
            return ModelStep(
                tool_call=ToolCallRequest(tool="get_site", arguments={"candidate_id": candidate_id})
            )
        records = context.transcript[0].result["records"]
        score = next(r for r in records if r["id"] == f"{candidate_id}/score")
        statement = {
            "text": f"The overall score is {score['display']}.",
            "kind": "CALCULATED",
            "evidence_ids": [score["id"]],
        }
        return ModelStep(answer_json={"direct_answer": [statement]})

    return FakeModel(step)


def scenario(category="A", question="What is the score of {site}?", site="", **expect):
    return Scenario(
        id=f"{category}9",
        category=category,
        title="test",
        question=question.format(site=site),
        expect=Expectation(**expect),
    )


def answered(data, question, statements):
    """A run whose final answer is given directly (after a real get_site call)."""
    candidate_id = re.search(r"cand-[a-z0-9]+", question).group(0)
    tool = ModelStep(
        tool_call=ToolCallRequest(tool="get_site", arguments={"candidate_id": candidate_id})
    )
    answer = ModelStep(answer_json={"direct_answer": statements})
    return run_analyst(data, question, FakeModel([tool, answer, answer]))


def points(result):
    return {p.name: p.passed for p in result.points}


# --- The scenario set --------------------------------------------------------------------------


def test_the_scenario_set_loads_and_covers_every_category():
    scenarios = load_scenarios(SCENARIO_FILE).scenarios
    assert {s.category for s in scenarios} == set(CATEGORIES)
    assert len({s.id for s in scenarios}) == len(scenarios) >= 12
    modes = {s.mode for s in scenarios}
    assert {"live", "live_sabotaged_first_answer", "scripted_invalid_twice"} <= modes
    for s in scenarios:  # every scenario has at least one scenario-specific check
        specific = s.expect.model_dump(exclude_defaults=True)
        assert specific or s.probes, s.id


def test_the_scenario_questions_hold_no_secret_and_no_numbers_to_copy():
    text = SCENARIO_FILE.read_text(encoding="utf-8")
    assert "sk-" not in text and "ANTHROPIC_API_KEY" not in text


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["scenarios"].append(dict(d["scenarios"][0])), "unique"),
        (
            lambda d: d.update(scenarios=[s for s in d["scenarios"] if s["category"] != "H"]),
            "categories",
        ),
        (
            lambda d: d["scenarios"][0]["expect"].update(tools_include=["run_python"]),
            "unknown tools",
        ),
        (lambda d: d["scenarios"][0].update(category="B"), "not in category"),
        (lambda d: d["scenarios"][0].update(judge="llm"), "Extra inputs"),
    ],
    ids=["duplicate-id", "missing-category", "unknown-tool", "id-category", "unknown-key"],
)
def test_invalid_scenario_sets_are_rejected(change, message):
    from sitescout.config import read_yaml

    raw = read_yaml(SCENARIO_FILE)
    change(raw)
    with pytest.raises(ValueError, match=message):
        ScenarioSet.model_validate(raw)


@needs_real
def test_every_stated_site_fact_holds_on_the_real_outputs():
    config = load_config()
    data = AnalystData.load(config, config.resolve(config.settings.paths.processed_dir))
    assert preflight(data, load_scenarios(SCENARIO_FILE)) == []


def test_preflight_reports_a_fact_that_no_longer_holds(data, sites):
    network, _ = sites
    stale = Scenario(
        id="A9", category="A", title="t", question="?",
        sites={network: {"selected_mclp": False}, "cand-gone": {}},
        expect=Expectation(),
    )  # fmt: skip
    problems = preflight(data, ScenarioSet.model_construct(scenarios=(stale,)))
    assert any("selected_mclp is True, not False" in p for p in problems)
    assert any("cand-gone is not a candidate" in p for p in problems)


# --- J: number-format probes ------------------------------------------------------------------


def test_the_number_format_probes_give_the_expected_verdicts(data, sites):
    _, other = sites
    probes = format_probes(data, other)
    assert all(p.actual_pass == p.expected_pass for p in probes), [
        p for p in probes if p.actual_pass != p.expected_pass
    ]
    assert {True, False} == {p.expected_pass for p in probes}
    assert all("number_not_grounded" in p.rules for p in probes if not p.expected_pass)


@needs_real
def test_the_number_format_probes_hold_on_the_real_site():
    config = load_config()
    data = AnalystData.load(config, config.resolve(config.settings.paths.processed_dir))
    probes = format_probes(data, "cand-8366165e2a19")
    assert all(p.actual_pass == p.expected_pass for p in probes)
    assert any(p.label == "OSM id copied" for p in probes)


# --- Scoring one run -----------------------------------------------------------------------


def test_a_grounded_answer_passes_every_point(data, sites):
    network, _ = sites
    sc = scenario(site=network, tools_include=["get_site"], cites_prefix=[f"{network}/"],
                  contains_any=[["overall score"]])  # fmt: skip
    result = evaluate(sc, run_analyst(data, sc.question, reference_model()), data)
    assert all(p.passed is not False for p in result.points), result.points
    assert result.revalidation.passed and result.numbers_checked == 1
    assert points(result)["shown answer validated"] and points(result)["numbers grounded"]


def test_missing_tools_citations_and_phrases_fail_their_points(data, sites):
    network, _ = sites
    sc = scenario(site=network, tools_include=["explain_score"], cites_prefix=["compare/"],
                  contains_any=[["component"]], not_contains=["overall score"])  # fmt: skip
    result = evaluate(sc, run_analyst(data, sc.question, reference_model()), data)
    got = points(result)
    assert got["calls explain_score"] is False
    assert got["cites compare/…"] is False
    assert got["says one of ['component']"] is False
    assert got["avoids unsupported claims"] is False
    assert got["shown answer validated"] is True  # validation and scenario points are separate


def test_the_wider_evaluative_list_applies_to_comparison_scenarios(data, sites):
    network, _ = sites
    question = f"Is {network} a good site?"
    score_id = f"{network}/score"
    display = next(r.display for r in find_sites(data, FindQuery()).records if r.id == score_id)
    statement = {
        "text": f"It is the superior site, scoring {display}.",
        "kind": "CALCULATED",
        "evidence_ids": [score_id],
    }
    run = answered(data, question, [statement])
    assert run.status == "answered"  # "superior" is not in the validator's own list
    plain = evaluate(scenario(question=question), run, data)
    compared = evaluate(scenario(question=question, comparison=True), run, data)
    assert points(plain)["neutral language"] is True
    assert points(compared)["neutral language"] is False
    assert compared.evaluative_hits == ("superior",)


def test_grid_and_unknown_points(data, sites):
    network, _ = sites
    question = f"Can {network} connect to the grid?"
    text = f"Grid connection capacity is unknown. {GRID_DISCLAIMER}"
    run = answered(data, question, [{"text": text, "kind": "UNKNOWN", "evidence_ids": []}])
    result = evaluate(
        scenario(question=question, grid_disclaimer=True, unknown_statement=True), run, data
    )
    got = points(result)
    assert got["exact grid disclaimer"] and got["keeps the unknown UNKNOWN"] and got["grid rule"]


def test_a_derived_trap_value_is_caught(data, sites):
    network, other = sites
    va = next(
        r.evidence.value
        for r in find_sites(data, FindQuery()).records
        if r.id == f"{network}/score"
    )
    vb = next(
        r.evidence.value for r in find_sites(data, FindQuery()).records if r.id == f"{other}/score"
    )
    real = run_analyst(data, f"score of {network}?", reference_model())
    derived = f"{abs(va - vb):.1f}"
    statement = Statement(
        text=f"The difference is {derived}.", kind="CALCULATED", evidence_ids=(f"{network}/score",)
    )
    forged = RunResult(
        question=real.question, transcript=real.transcript, status="answered",
        answer=Answer(direct_answer=(statement,)), attempts=(ValidationResult(passed=True),),
    )  # fmt: skip
    trap = {"kind": "difference", "field": "score", "a": network, "b": other}
    result = evaluate(scenario(site=network, traps=[trap]), forged, data)
    got = points(result)
    assert got["no derived difference of score"] is False
    assert got["numbers grounded"] is False and got["shown answer validated"] is False
    assert derived in result.trap_hits


def test_the_answer_is_re_validated_from_the_log_alone(data, sites):
    network, _ = sites
    run = run_analyst(data, f"score of {network}?", reference_model())
    logged = RunResult.model_validate_json(run.model_dump_json())  # as read back from the log
    again = validate_answer(logged.answer, session_from_transcript(logged))
    assert again == run.attempts[-1]


# --- L: the retry and the fallback -----------------------------------------------------------


def test_the_sabotage_wrapper_replaces_only_the_first_answer(data, sites):
    network, _ = sites
    question = f"score of {network}?"
    wrapped = FirstAnswerSabotage(reference_model(), {"direct_answer": [{
        "text": "The overall score is 999.9.", "kind": "CALCULATED",
        "evidence_ids": [f"{network}/score"]}]})  # fmt: skip
    run = run_analyst(data, question, wrapped)
    assert run.status == "answered" and run.retried
    assert [a.passed for a in run.attempts] == [False, True]
    assert run.attempts[0].errors[0].rule == "number_not_grounded"


def test_the_scripted_mode_ends_in_the_labelled_fallback(data, sites):
    network, _ = sites
    sc = Scenario(
        id="L9", category="L", title="t", question=f"score of {network}?",
        mode="scripted_invalid_twice", sites={network: {}},
        expect=Expectation(status="fallback", retried=True, tools_include=["get_site"]),
    )  # fmt: skip
    run = run_scenario(sc, data, provider=None)
    result = evaluate(sc, run, data)
    assert run.status == "fallback" and len(run.attempts) == 2
    assert all(p.passed is not False for p in result.points)
    assert points(result)["fallback labelled"]


def test_a_live_scenario_without_a_provider_is_refused(data, sites):
    network, _ = sites
    with pytest.raises(EvaluationError, match="needs the configured provider"):
        run_scenario(scenario(site=network), data, provider=None)


# --- The whole evaluation ----------------------------------------------------------------


def synthetic_set(network):
    items = [
        Scenario(
            id=f"{c}1", category=c, title=f"category {c}",
            question=f"What is the score of {network}?",
            mode="scripted_invalid_twice" if c == "L" else "live", sites={network: {}},
            expect=Expectation(status="fallback", retried=True) if c == "L"
            else Expectation(tools_include=["get_site"], cites_prefix=[f"{network}/"],
                             comparison=c == "C", grid_disclaimer=False),
        )
        for c in CATEGORIES
    ]  # fmt: skip
    return ScenarioSet(scenarios=tuple(items))


def test_an_end_to_end_run_with_a_reference_model(world, data, sites, monkeypatch, caplog):
    config, _ = world
    network, _ = sites
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-analyst-credential-for-offline-tests")
    with caplog.at_level(logging.INFO):
        evaluation = run_evaluation(
            config,
            lambda s: reference_model(),
            data=data,
            scenarios=synthetic_set(network),
            write=False,
        )
    summary = evaluation.summary
    assert summary.run == 12 and summary.answered == 11 and summary.fallbacks == 1
    assert summary.points_passed == summary.points_total
    assert summary.ungrounded_numbers == 0 and summary.evaluative_hits == 0
    report = render_report(evaluation)
    for heading in ("## 1. Run", "## 2. Thresholds", "## 3. Summary by scenario", "## 4. Totals",
                    "## 5. Failures", "## 6. Scenarios", "## 7. Limitations"):  # fmt: skip
        assert heading in report
    assert "no AI summary" in report and "- None." in report
    dumped = evaluation.model_dump_json()
    assert "fake-analyst-credential" not in report + dumped + caplog.text


def test_offline_runs_only_the_deterministic_parts(world, data, sites):
    config, _ = world
    network, _ = sites
    evaluation = run_evaluation(
        config, None, offline=True, data=data, scenarios=synthetic_set(network), write=False
    )
    assert evaluation.offline and evaluation.summary.run == 1  # only the scripted scenario
    assert evaluation.summary.passed is None
    assert all(t.met is None for t in evaluation.summary.thresholds)


def test_only_unknown_scenario_ids_are_refused(world, data, sites):
    config, _ = world
    network, _ = sites
    with pytest.raises(EvaluationError, match="unknown scenario ids"):
        run_evaluation(config, None, offline=True, data=data, scenarios=synthetic_set(network),
                       only=["Z1"], write=False)  # fmt: skip


# --- The script ------------------------------------------------------------------------------


@pytest.fixture
def script(monkeypatch):
    path = PROJECT_ROOT / "scripts" / "analyst_eval.py"
    spec = importlib.util.spec_from_file_location("analyst_eval_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level

    def run(*argv):
        monkeypatch.setattr("sys.argv", [str(path), *argv])
        return module.main()

    yield run
    root.handlers[:] = handlers
    root.setLevel(level)


def outputs():
    config = load_config()
    return (
        config.resolve(config.settings.paths.analyst_eval_report),
        config.resolve(config.settings.paths.processed_dir) / "analyst_eval.json",
    )


def _state(paths):
    return {p: p.read_bytes() if p.is_file() else None for p in paths}


@needs_real
def test_the_script_without_a_key_runs_nothing_and_writes_nothing(script, monkeypatch, capsys):
    # Whichever provider is configured live (config/settings.yaml): its own key is missing.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    before = _state(outputs())
    assert script() == 1
    assert _state(outputs()) == before
    variable = (
        "ANTHROPIC_API_KEY"
        if load_config().settings.analyst.provider == "anthropic"
        else "OPENAI_API_KEY"
    )
    assert f"{variable} is not set" in capsys.readouterr().err


@needs_real
def test_the_script_offline_writes_nothing(script, capsys):
    before = _state(outputs())
    assert script("--offline") == 0
    assert _state(outputs()) == before
    out = capsys.readouterr().out
    assert out.startswith("Offline") and "not run" in out
    assert json.dumps(out)  # plain text, no secret, no prompt
