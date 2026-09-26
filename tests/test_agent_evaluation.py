"""M10 Phase 4 (D-058): the agent evaluation's cases, runner, assertions, metrics and report.

The evaluation is scripted and offline: no model, API key or network, and the same inputs give
the same result. These tests check the case file's schema, run the whole case set on the
SYNTHETIC world, pin what each rule and each documented limit does, and check the accounting."""

import importlib.util
import logging
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_support import limits, make_context
from sitescout.agent_eval import (
    CATEGORIES,
    EVAL_VERSION,
    Case,
    CaseError,
    CaseSet,
    Evaluation,
    evaluate,
    load_cases,
    render_report,
    run_case_with_result,
)
from sitescout.agent_eval.runner import Tally, _outcome, _tally
from sitescout.config import PROJECT_ROOT, load_config

CASE_FILE = PROJECT_ROOT / "tests" / "agent_cases.yaml"
REPORT = PROJECT_ROOT / "reports" / "agent_eval.md"
SCRIPT = PROJECT_ROOT / "scripts" / "agent_eval.py"

FALSE_ACCEPTS = {"A09", "C06", "D07", "D10", "E06"}
FALSE_REJECTS = {"G03", "G05"}


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture(scope="module")
def cases() -> CaseSet:
    return load_cases(CASE_FILE)


@pytest.fixture(scope="module")
def evaluation(context, cases) -> Evaluation:
    return evaluate(context, cases, limits())


def by_id(evaluation: Evaluation) -> dict:
    return {r.id: r for r in evaluation.cases}


def case_of(**changes) -> Case:
    """A small valid case (a stored score copied from get_site), with fields changed."""
    raw = {
        "id": "B99",
        "category": "B",
        "title": "t",
        "kind": "legitimate",
        "question": "What is the stored score of cand-a?",
        "provider": [
            {"tool": "get_site", "arguments": {"candidate_id": "cand-a"}},
            {
                "answer": {
                    "direct_answer": [
                        {
                            "text": "The stored score of cand-a is {{display|cand-a/score}}.",
                            "kind": "CALCULATED",
                            "evidence_ids": ["cand-a/score"],
                        }
                    ]
                }
            },
        ],
        "expect": {"decision": "accept", "status": "answered", "termination": "answered"},
    }
    for key, value in changes.items():
        if key in raw["expect"] or key in ("validator_decision", "rules"):
            raw["expect"][key] = value
        else:
            raw[key] = value
    return Case.model_validate(raw)


# --- The case file ----------------------------------------------------------------------------


def test_the_case_file_loads_and_is_valid(cases):
    assert cases.version == 1 and len(cases.cases) == 73


def test_cases_have_unique_ids_in_a_fixed_order(cases):
    ids = [c.id for c in cases.cases]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_every_category_has_cases_and_both_kinds_occur(cases):
    assert {c.category for c in cases.cases} == set(CATEGORIES)
    assert {c.kind for c in cases.cases} == {"legitimate", "adversarial"}


def test_every_case_states_its_termination_and_a_ground_truth(cases):
    for case in cases.cases:
        assert case.expect.termination and case.expect.decision in {"accept", "reject", "none"}


def test_the_configured_paths_point_at_the_case_file_and_a_separate_report():
    paths = load_config().settings.paths
    assert paths.agent_cases == "tests/agent_cases.yaml"
    assert paths.agent_eval_report == "reports/agent_eval.md"
    assert paths.agent_eval_report != paths.knowledge_eval_report


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "X01"},  # not a case id
        {"id": "A01"},  # id and category disagree
        {"provider": []},
        {"provider": [{"tool": "get_site", "answer": {"direct_answer": []}}]},  # two kinds
        {"provider": [{"arguments": {"a": 1}, "provider_error": True}]},  # arguments, no tool
        {"limits": "many"},
        {"kind": "hostile"},
        {"validator_decision": "reject"},  # a pin without a known limit
        {"known_limit": "because"},  # a known limit without a pin
        {"rules": ("number_not_grounded",)},  # rules on an accepted answer
        {"decision": "reject"},  # a rejected answer without rules
    ],
)
def test_an_invalid_case_is_rejected(changes):
    with pytest.raises((ValidationError, ValueError)):
        case_of(**changes)


def test_a_known_limit_must_differ_from_the_ground_truth():
    with pytest.raises(ValidationError):
        case_of(known_limit="x", validator_decision="accept")


def test_a_case_set_must_be_sorted_and_unique():
    one, two = case_of(id="B01"), case_of(id="B02")
    assert CaseSet(version=1, world="w", cases=(one, two)).cases == (one, two)
    for bad in ((two, one), (one, one)):
        with pytest.raises(ValidationError):
            CaseSet(version=1, world="w", cases=bad)


def test_an_unreadable_case_file_is_a_case_error(tmp_path):
    bad = tmp_path / "cases.yaml"
    bad.write_text("version: 1\nworld: w\ncases: []\n", encoding="utf-8")
    with pytest.raises(CaseError):
        load_cases(bad)


# --- The whole set on the SYNTHETIC world -----------------------------------------------------


def test_every_case_matches_what_it_expects(evaluation):
    failed = {r.id: r.failures for r in evaluation.cases if not r.passed}
    assert failed == {} and evaluation.total.failed == 0
    assert (evaluation.version, evaluation.case_count) == (EVAL_VERSION, 73)


def test_the_decision_counts_are_pinned(evaluation):
    t = evaluation.total
    assert (t.true_accept, t.true_reject, t.false_accept, t.false_reject, t.not_applicable) == (
        37,
        24,
        5,
        2,
        5,
    )
    assert t.cases == sum(
        (t.true_accept, t.true_reject, t.false_accept, t.false_reject, t.not_applicable)
    )


def test_the_categories_partition_the_cases(evaluation):
    assert list(evaluation.by_category) == sorted(CATEGORIES)
    assert sum(t.cases for t in evaluation.by_category.values()) == evaluation.case_count
    assert {c: t.cases for c, t in evaluation.by_category.items()} == {
        "A": 9, "B": 9, "C": 8, "D": 10, "E": 6, "F": 8, "G": 7, "H": 16,
    }  # fmt: skip


def test_false_accepts_and_rejects_are_exactly_the_documented_limits(evaluation):
    results = by_id(evaluation)
    assert {i for i, r in results.items() if r.outcome == "false_accept"} == FALSE_ACCEPTS
    assert {i for i, r in results.items() if r.outcome == "false_reject"} == FALSE_REJECTS
    assert set(evaluation.known_limit_cases) == FALSE_ACCEPTS | FALSE_REJECTS
    assert all(results[i].known_limit for i in FALSE_ACCEPTS | FALSE_REJECTS)


def test_no_unsupported_answer_is_accepted_and_no_legitimate_one_rejected_outside_the_limits(
    evaluation,
):
    for r in evaluation.cases:
        if r.known_limit is None:
            assert r.outcome in {"true_accept", "true_reject", "not_applicable"}, r.id


def test_no_grounding_failure_is_masked_by_a_retry(evaluation):
    # The retry cases still count their first, rejected attempt.
    results = by_id(evaluation)
    for case_id in ("H15", "H16"):
        assert results[case_id].outcome == "true_reject" and results[case_id].attempts == 2
    assert results["H15"].status == "answered" and results["H16"].status == "fallback"
    assert evaluation.retries == 1


# --- Grounding, by rule -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_id", "rules"),
    [
        ("A03", {"quote_too_short"}),
        ("A04", {"knowledge_quote_missing"}),
        ("A05", {"quote_not_in_cited_chunk"}),  # the quotation is in another retrieved chunk
        ("A06", {"quote_not_in_cited_chunk"}),
        ("A07", {"missing_citation", "quote_without_knowledge_citation"}),
        ("B07", {"number_not_grounded"}),
        ("C05", {"mixed_number_not_structured"}),
        ("D02", {"unknown_stated_as_fact"}),
        ("D03", {"unknown_must_state_absence"}),
        ("D04", {"unknown_must_state_absence"}),
        ("D05", {"unknown_must_state_absence"}),
        ("E02", {"unknown_site_reference"}),
        ("F02", {"number_not_grounded"}),
        ("F03", {"number_not_grounded"}),
        ("F05", {"number_not_grounded"}),
        ("F07", {"number_not_grounded"}),
        ("G06", {"number_must_cite_structured_record"}),
    ],
)
def test_each_unsupported_answer_is_rejected_by_the_intended_rule(evaluation, case_id, rules):
    result = by_id(evaluation)[case_id]
    assert result.actual_decision == "reject" and set(result.rules) == rules


@pytest.mark.parametrize(
    "case_id",
    ["A01", "A02", "A08", "B01", "B02", "C01", "C02", "C03", "C04", "C08", "D01", "D06", "D08"]
    + ["D09", "E01", "E04", "E05", "F01", "F04", "F06", "F08", "G01", "G02", "G04"],
)
def test_each_supported_answer_is_accepted_with_no_rule_reported(evaluation, case_id):
    result = by_id(evaluation)[case_id]
    assert (result.actual_decision, result.rules, result.status) == ("accept", (), "answered")


def test_a_quotation_is_accepted_at_the_minimum_length_and_rejected_below_it(evaluation):
    results = by_id(evaluation)
    assert results["A02"].outcome == "true_accept"  # three words
    assert results["A03"].outcome == "true_reject"  # two words


def test_a_number_may_appear_in_both_sources_when_both_are_cited(evaluation):
    results = by_id(evaluation)
    assert results["C08"].outcome == results["F06"].outcome == "true_accept"
    assert results["G04"].outcome == "true_accept"  # a small common number, the record cited


def test_a_documentation_only_constant_is_accepted_from_its_chunk(evaluation):
    assert by_id(evaluation)["G01"].outcome == "true_accept"


def test_unknown_is_never_made_a_fact_and_keeps_its_evidence_type(evaluation):
    results = by_id(evaluation)
    unknown = [r for r in evaluation.cases if "unknown" in r.tags]
    assert len(unknown) == 10
    assert results["D01"].outcome == results["D06"].outcome == "true_accept"
    assert {results[i].outcome for i in ("D02", "D03", "D04", "D05")} == {"true_reject"}
    assert results["D09"].outcome == "true_accept"  # INFERRED stated as INFERRED
    assert results["D10"].outcome == "false_accept"  # the documented gap in the M9 rule set


def test_the_limits_of_the_number_rule_are_reported_not_hidden(evaluation):
    results = by_id(evaluation)
    assert results["G03"].rules == ("number_must_cite_structured_record",)
    assert results["G05"].rules == ("number_must_cite_structured_record",)
    assert results["C06"].actual_decision == "accept"  # a documentation-only number


def test_a_paraphrase_that_misstates_its_chunk_is_a_known_false_accept(evaluation):
    result = by_id(evaluation)["A09"]
    assert result.outcome == "false_accept" and "Faithfulness" in result.known_limit


# --- Trajectories -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_id", "trajectory", "termination"),
    [
        ("H01", ("search_knowledge",), "answered"),
        ("H02", ("get_site",), "answered"),
        ("H03", ("search_knowledge", "get_site"), "answered"),
        ("H04", ("get_site", "search_knowledge"), "answered"),
        ("H05", ("get_site", "network_summary"), "answered"),
        ("H06", ("search_knowledge", "search_knowledge"), "answered"),
        ("H07", ("get_site:error", "get_site"), "answered"),
        ("H08", ("get_site", "get_site:duplicate"), "answered"),
        ("H09", ("get_site", "network_summary"), "tool_call_limit"),
        ("H10", ("search_knowledge",), "retrieval_call_limit"),
        ("H11", ("get_site", "network_summary"), "iteration_limit"),
        ("H12", ("get_site:error",) * 3, "recoverable_error_limit"),
        ("H13", ("delete_site:error", "get_site"), "answered"),
        ("H14", ("get_site",), "provider_error"),
        ("H15", ("get_site",), "answered"),
        ("H16", ("get_site",), "validation_failed"),
    ],
)
def test_each_trajectory_ends_as_expected(evaluation, case_id, trajectory, termination):
    result = by_id(evaluation)[case_id]
    assert (result.trajectory, result.termination) == (trajectory, termination)
    assert result.status == ("answered" if termination == "answered" else "fallback")


def test_a_limit_or_a_provider_error_gives_a_fallback_with_no_answer_attempt(evaluation):
    results = by_id(evaluation)
    for case_id in ("H09", "H10", "H11", "H12", "H14"):
        r = results[case_id]
        assert (r.status, r.attempts, r.actual_decision) == ("fallback", 0, "none")
        assert r.outcome == "not_applicable"


def test_the_counters_follow_the_calls(evaluation):
    results = by_id(evaluation)
    assert results["H01"].counters == {
        "tool_calls": 0, "retrieval_calls": 1, "duplicate_calls": 0, "recoverable_errors": 0,
    }  # fmt: skip
    assert results["H08"].counters["tool_calls"] == 1
    assert results["H08"].counters["duplicate_calls"] == 1
    assert results["H12"].counters["recoverable_errors"] == 3


def test_a_tool_argument_can_come_from_an_earlier_result_not_from_invention(context, cases):
    case = next(c for c in cases.cases if c.id == "E04")
    scored, result = run_case_with_result(context, case, limits())
    find, get = result.state.observations
    assert (find.tool, get.tool) == ("find_sites", "get_site")
    assert get.arguments == {"candidate_id": "cand-a"}
    assert "cand-a/rank" in find.record_ids  # the id the argument came from was returned first
    assert scored.passed and result.status == "answered"


# --- The runner's own accounting --------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual", "outcome"),
    [
        ("accept", "accept", "true_accept"),
        ("accept", "reject", "false_reject"),
        ("accept", "none", "false_reject"),
        ("reject", "reject", "true_reject"),
        ("reject", "accept", "false_accept"),
        ("none", "none", "not_applicable"),
        ("none", "reject", "not_applicable"),
    ],
)
def test_the_four_outcomes(expected, actual, outcome):
    assert _outcome(expected, actual) == outcome


def test_an_unsupported_answer_that_is_accepted_is_a_false_accept_and_a_failing_case(context):
    bad = case_of(
        provider=[
            {"tool": "get_site", "arguments": {"candidate_id": "cand-a"}},
            {
                "answer": {
                    "direct_answer": [
                        {
                            "text": "The stored score of cand-a is {{display|cand-a/score}}.",
                            "kind": "RETRIEVED_FACT",  # a calculated value called a fact
                            "evidence_ids": ["cand-a/score"],
                        }
                    ]
                }
            },
        ],
        decision="reject",
        rules=("evidence_type_mismatch",),
    )
    scored, _ = run_case_with_result(context, bad, limits())
    assert scored.outcome == "false_accept" and not scored.passed
    assert any("first-attempt decision" in f for f in scored.failures)


def test_a_legitimate_answer_that_is_rejected_is_a_false_reject_and_a_failing_case(context):
    wrong = case_of(
        provider=[
            {"tool": "get_site", "arguments": {"candidate_id": "cand-a"}},
            {
                "answer": {
                    "direct_answer": [
                        {"text": "The stored score of cand-a is 1.5.", "kind": "CALCULATED",
                         "evidence_ids": ["cand-a/score"]}
                    ]
                },
                "times": 2,
            },
        ]
    )  # fmt: skip
    scored, _ = run_case_with_result(context, wrong, limits())
    assert scored.outcome == "false_reject" and not scored.passed


def test_a_stale_placeholder_is_a_failing_case_not_a_crash(context):
    stale = case_of(
        provider=[
            {"tool": "get_site", "arguments": {"candidate_id": "cand-a"}},
            {
                "answer": {
                    "direct_answer": [
                        {"text": "The score is {{display|cand-a/no_such_record}}.",
                         "kind": "CALCULATED", "evidence_ids": ["cand-a/score"]}
                    ]
                }
            },
        ]
    )  # fmt: skip
    scored, _ = run_case_with_result(context, stale, limits())
    assert not scored.passed and any(f.startswith("fixture:") for f in scored.failures)


def test_a_provider_script_that_ends_early_is_reported(context):
    short = case_of(provider=[{"tool": "get_site", "arguments": {"candidate_id": "cand-a"}}])
    scored, _ = run_case_with_result(context, short, limits())
    assert "the provider script ended before the run did" in scored.failures


def test_the_tally_counts_each_outcome_separately_with_no_combined_score(evaluation):
    tally = _tally([r for r in evaluation.cases if r.category == "G"])
    assert isinstance(tally, Tally) and tally.cases == 7 and tally.failed == 0
    assert (tally.true_accept, tally.true_reject, tally.false_reject) == (3, 2, 2)
    assert not any("accuracy" in name or "score" in name for name in Tally.model_fields)


def test_a_subset_is_aggregated_by_category(context, cases):
    subset = CaseSet(
        version=1, world="w", cases=tuple(c for c in cases.cases if c.id in {"B01", "B07", "H09"})
    )
    result = evaluate(context, subset, limits())
    assert result.case_count == 3 and list(result.by_category) == ["B", "H"]
    assert result.by_category["B"].true_accept == 1 and result.by_category["B"].true_reject == 1
    assert result.by_category["H"].not_applicable == 1
    assert result.rule_counts == {"number_not_grounded": 1}
    assert result.termination_counts == {
        "answered": 1, "tool_call_limit": 1, "validation_failed": 1,
    }  # fmt: skip


# --- Reproducibility and the report -----------------------------------------------------------


def test_the_same_input_gives_the_same_result_in_the_same_order(context, cases, evaluation):
    again = evaluate(context, cases, limits())
    assert again.model_dump_json() == evaluation.model_dump_json()
    assert [r.id for r in again.cases] == [c.id for c in cases.cases]


def test_the_result_survives_a_json_round_trip(evaluation):
    assert Evaluation.model_validate_json(evaluation.model_dump_json()) == evaluation


def test_the_report_is_deterministic_and_carries_the_required_facts(evaluation):
    report = render_report(evaluation)
    assert report == render_report(evaluation) and "\r" not in report
    for needle in (
        EVAL_VERSION,
        "73",
        evaluation.corpus_fingerprint,
        "test-suite metrics",
        "False accept",
        "False reject",
        "## 4. Grounding rules",
        "## 5. Terminations",
        "## 6. Known limits",
        "## 8. Limitations",
    ):
        assert needle in report
    for case in evaluation.cases:
        assert f"| {case.id} |" in report
    assert not re.search(r"accuracy|\bfeasibility\b(?! requires utility confirmation)", report)


def test_the_committed_report_matches_a_fresh_run(evaluation):
    # The corpus lines are context and move with docs/decisions.md, so they are set aside.
    def stable(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if not line.startswith("| Corpus"))

    committed = REPORT.read_text(encoding="utf-8")
    assert stable(committed) == stable(render_report(evaluation))


def test_the_evaluation_never_writes_the_knowledge_report():
    import sitescout.agent_eval.report as report_module
    import sitescout.agent_eval.runner as runner_module

    for module in (report_module, runner_module):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "knowledge_eval_report" not in text and "write_bytes" not in text


def test_the_script_prints_a_summary_and_writes_nothing_with_no_write(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("agent_eval_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = REPORT.read_bytes()
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--no-write"])
    try:
        assert module.main() == 0
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    out = capsys.readouterr().out
    assert "Cases: 73; pass 73, fail 0" in out and "false accept 5" in out
    assert REPORT.read_bytes() == before
