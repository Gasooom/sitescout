"""M10 Phase 3 (D-058): the agent's grounding layer on top of the unchanged M9 validator.

The evidence here is hand-built (SYNTHETIC): a structured record per kind, a few knowledge
chunks and one UNKNOWN record, so every rule is tested on its own."""

import pytest
from pydantic import ValidationError

from sitescout.agent import AgentAnswer, AgentStatement, parse_answer, validate_agent_answer
from sitescout.analyst.validate import ToolRecords, validate_answer
from sitescout.evidence import EvidenceRecord, record

SITE = "cand-aaaaaaaaaaaa"


def rec(id: str, display: str, type: str = "CALCULATED") -> EvidenceRecord:
    return record(id, "a claim", type, "SYNTHETIC", "metric", display, display)  # type: ignore[arg-type]


SCORE = rec(f"{SITE}/score", "76.6")
SHARE = rec("network/mclp/population_covered_share", "49.8%")
TOP30 = rec("network/top30/population_covered_share", "24.0%")
UNKNOWN = rec(f"{SITE}/grid_status", "missing", "UNKNOWN")
CONFIDENCE = rec(
    "kb/scoring/confidence",
    "## Confidence\n\nA level, never\na percentage, built only from factors that differ:",
    "RETRIEVED_FACT",
)
REASON = rec(
    "kb/decisions/d-046#2",
    "- **Reason:** the exact MCLP covers 49.8% of the population, against 24.0% for the Top-30",
    "RETRIEVED_FACT",
)
CONSTANT = rec("kb/spec/8", "the score term has λ = 0.01 by default", "RETRIEVED_FACT")
SESSION = ToolRecords(records=(SCORE, SHARE, TOP30, UNKNOWN, CONFIDENCE, REASON, CONSTANT))


def answer(*statements: AgentStatement) -> AgentAnswer:
    return AgentAnswer(direct_answer=tuple(statements))


def s(text, kind="RETRIEVED_FACT", ids=(), quotes=()) -> AgentStatement:
    return AgentStatement(text=text, kind=kind, evidence_ids=tuple(ids), quotes=tuple(quotes))


def check(*statements, min_quote_words=3, session=SESSION):
    return validate_agent_answer(answer(*statements), session, min_quote_words=min_quote_words)


def rules(result) -> set[str]:
    return {e.rule for e in result.errors}


# --- The schema -------------------------------------------------------------------------------


def test_a_quotation_is_part_of_the_statement_and_dropped_for_the_m9_check():
    original = answer(
        s("Confidence is a level.", ids=[CONFIDENCE.id], quotes=["never a percentage"])
    )
    m9 = original.to_m9()
    assert m9.direct_answer[0].text == "Confidence is a level."
    assert m9.direct_answer[0].evidence_ids == (CONFIDENCE.id,)
    assert not hasattr(m9.direct_answer[0], "quotes")


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"direct_answer": []},
        {"direct_answer": [{"text": "x", "kind": "GUESS"}]},
        {"direct_answer": [{"text": "", "kind": "UNKNOWN"}]},
        {"direct_answer": [{"text": "x", "kind": "UNKNOWN", "confidence": 0.9}]},
        {"direct_answer": [{"text": "x", "kind": "UNKNOWN"}], "summary": "extra section"},
    ],
)
def test_the_schema_is_strict(raw):
    with pytest.raises(ValidationError):
        parse_answer(raw)


def test_a_valid_answer_parses():
    raw = {"direct_answer": [{"text": "It is not known.", "kind": "UNKNOWN"}]}
    assert parse_answer(raw).direct_answer[0].quotes == ()


# --- Knowledge claims -------------------------------------------------------------------------


def test_a_knowledge_claim_with_a_verbatim_quotation_passes():
    ok = s(
        "Confidence is a level and never a percentage.",
        ids=[CONFIDENCE.id],
        quotes=["never a percentage"],
    )
    assert check(ok).passed


def test_a_quotation_may_span_a_line_break_in_the_chunk():
    ok = s(
        "Confidence is a level.", ids=[CONFIDENCE.id], quotes=["A level, never a percentage, built"]
    )
    assert check(ok).passed


def test_a_knowledge_claim_without_a_quotation_is_rejected():
    result = check(s("Confidence is a level.", ids=[CONFIDENCE.id]))
    assert rules(result) == {"knowledge_quote_missing"}


def test_a_quotation_that_is_not_in_a_cited_chunk_is_rejected():
    result = check(s("Confidence is a level.", ids=[CONFIDENCE.id], quotes=["always a percentage"]))
    assert rules(result) == {"quote_not_in_cited_chunk"}


def test_a_quotation_from_a_chunk_the_statement_does_not_cite_is_rejected():
    result = check(
        s("Confidence is a level.", ids=[CONFIDENCE.id], quotes=["the exact MCLP covers"])
    )
    assert rules(result) == {"quote_not_in_cited_chunk"}


def test_a_quotation_needs_a_cited_knowledge_chunk():
    result = check(
        s("The stored score is 76.6.", "CALCULATED", [SCORE.id], quotes=["never a percentage"])
    )
    assert rules(result) == {"quote_without_knowledge_citation"}


@pytest.mark.parametrize(("minimum", "passes"), [(2, True), (3, True), (4, False)])
def test_the_shortest_quotation_is_configurable(minimum, passes):
    ok = s("Confidence is a level.", ids=[CONFIDENCE.id], quotes=["never a percentage"])
    result = check(ok, min_quote_words=minimum)
    assert result.passed is passes
    assert passes or rules(result) == {"quote_too_short"}


def test_a_knowledge_chunk_cannot_ground_a_calculated_statement():
    result = check(
        s("Confidence is a level.", "CALCULATED", [CONFIDENCE.id], ["never a percentage"])
    )
    assert "calculated_not_grounded" in rules(result)


# --- Structured numbers -----------------------------------------------------------------------


def test_a_number_copied_from_a_structured_record_passes():
    ok = s(
        "The optimized network covers 49.8% of the modelled population.", "CALCULATED", [SHARE.id]
    )
    assert check(ok).passed


def test_an_unsupported_number_is_rejected_by_the_m9_rule():
    result = check(
        s(
            "The optimized network covers 51.2% of the modelled population.",
            "CALCULATED",
            [SHARE.id],
        )
    )
    assert "number_not_grounded" in rules(result)


def test_a_mixed_statement_takes_its_numbers_from_the_structured_record():
    ok = s(
        "The optimized network covers 49.8% of the modelled population, as the method intends.",
        "CALCULATED",
        [SHARE.id, REASON.id],
        ["the exact MCLP covers"],
    )
    assert check(ok).passed


def test_a_mixed_statement_may_not_take_a_number_only_documentation_holds():
    bad = s(
        "The Top-30 covers 24.0% of the population.", "CALCULATED", [SHARE.id, REASON.id],
        ["the exact MCLP covers"],
    )  # fmt: skip
    assert rules(check(bad)) == {"mixed_number_not_structured"}


def test_a_current_number_is_not_taken_from_documentation_when_a_tool_shows_it():
    bad = s(
        "The exact method covers 49.8% of the population.",
        ids=[REASON.id],
        quotes=["the exact MCLP covers"],
    )
    assert rules(check(bad)) == {"number_must_cite_structured_record"}
    fixed = s(
        "The exact method covers 49.8% of the population.",
        ids=[SHARE.id, REASON.id],
        quotes=["the exact MCLP covers"],
    )
    assert check(fixed).passed


def test_a_number_only_documentation_holds_is_accepted_from_the_chunk_it_cites():
    # A documented limit (D-058): a methodology constant that no tool shows.
    ok = s(
        "The score term has weight 0.01 by default.",
        ids=[CONSTANT.id],
        quotes=["= 0.01 by default"],
    )
    assert check(ok).passed


def test_the_number_rule_only_applies_when_a_structured_record_was_fetched():
    only_knowledge = ToolRecords(records=(REASON,))
    ok = s(
        "The exact method covers 49.8% of the population.",
        ids=[REASON.id],
        quotes=["the exact MCLP covers"],
    )
    assert check(ok, session=only_knowledge).passed  # nothing structured to cite instead


# --- Sites, unknowns, and the M9 rules --------------------------------------------------------


def test_a_site_the_run_never_saw_is_rejected():
    bad = s("The site cand-bbbbbbbbbbbb has a stored score of 76.6.", "CALCULATED", [SCORE.id])
    assert rules(check(bad)) == {"unknown_site_reference"}
    ok = s(f"The site {SITE} has a stored score of 76.6.", "CALCULATED", [SCORE.id])
    assert check(ok).passed


def test_unknown_is_never_stated_as_fact():
    bad = s("The status of the site is recorded.", "RETRIEVED_FACT", [UNKNOWN.id])
    assert rules(check(bad)) == {"unknown_stated_as_fact"}


def test_unknown_information_may_be_stated_as_unknown():
    ok = s("The connection status is not available.", "UNKNOWN", [UNKNOWN.id])
    assert check(ok).passed
    assert check(s("The connection status is settled.", "UNKNOWN", [UNKNOWN.id])).passed is False


def test_the_m9_rules_still_apply_unchanged():
    cases = {
        "prohibited_comparison_language": s("This site is better.", "INFERRED", [SCORE.id]),
        "prohibited_claim": s("The permit was approved.", "INFERRED", [SCORE.id]),
        "grid_disclaimer_missing": s("The grid is nearby.", "INFERRED", [SCORE.id]),
        "unknown_evidence_id": s(
            "The stored score is 76.6.", "CALCULATED", ["cand-zzzzzzzzzzzz/score"]
        ),
        "missing_citation": s("The site is a fuel station.", "RETRIEVED_FACT"),
    }
    for rule, statement in cases.items():
        assert rule in rules(check(statement)), rule


def test_an_answer_that_fails_an_m9_rule_fails_here_with_the_same_issues():
    bad = answer(s("The site is better than the rest.", "INFERRED", [SCORE.id]))
    expected = {(e.rule, e.statement_id) for e in validate_answer(bad.to_m9(), [SESSION]).errors}
    got = {
        (e.rule, e.statement_id)
        for e in validate_agent_answer(bad, SESSION, min_quote_words=3).errors
    }
    assert expected and expected <= got


def test_statements_are_located_by_section_and_index():
    two = AgentAnswer(
        direct_answer=(s("The connection status is not available.", "UNKNOWN"),),
        evidence=(s("Confidence is a level.", ids=[CONFIDENCE.id]),),
    )
    result = validate_agent_answer(two, SESSION, min_quote_words=3)
    assert [(e.rule, e.statement_id) for e in result.errors] == [
        ("knowledge_quote_missing", "evidence[0]")
    ]


def test_validation_never_raises_on_an_empty_session():
    result = validate_agent_answer(
        answer(s("The stored score is 76.6.", "CALCULATED", [SCORE.id])),
        ToolRecords(),
        min_quote_words=3,
    )
    assert not result.passed and "unknown_evidence_id" in rules(result)
