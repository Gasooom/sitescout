"""The agent's retry note (M11 Phase 2): a rejection comes back to the model with one concrete
correction per rule it broke, on both agent adapters, without changing M9's own note, relaxing
a rule or adding a retry. Offline: no model, key or network."""

import re

import pytest

from agent_support import call, final, limits, make_context, reactive, stmt
from sitescout.agent import AGENT_TOOL_DEFINITIONS, ANSWER_RETRIES, run_agent
from sitescout.agent.validate import AgentAnswer, validate_agent_answer
from sitescout.agent_provider.anthropic_adapter import build_request as anthropic_request
from sitescout.agent_provider.common import CORRECTIONS, agent_retry_note
from sitescout.agent_provider.openai_adapter import build_request as openai_request
from sitescout.analyst.provider import ModelContext, ToolCallRecord
from sitescout.analyst.provider_common import retry_note
from sitescout.analyst.validate import ToolRecords, ValidationIssue
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.config import PROJECT_ROOT, AgentProviderSettings
from sitescout.evidence import record


def issue(rule: str, statement: str = "direct_answer[0]") -> ValidationIssue:
    return ValidationIssue(rule=rule, statement_id=statement, message=f"{rule} message")


PREVIOUS = {"direct_answer": [{"text": "x", "kind": "UNKNOWN", "evidence_ids": [], "quotes": []}]}


def test_a_quote_failure_is_returned_with_the_exact_quotation_correction():
    note = agent_retry_note([issue("quote_not_in_cited_chunk")], PREVIOUS)
    assert "quote_not_in_cited_chunk" in note
    assert "one contiguous span copied exactly" in note
    assert "Markdown characters" in note and "do not paraphrase" in note


def test_a_missing_disclaimer_is_returned_with_the_sentence_and_the_boundaries():
    note = agent_retry_note([issue("grid_disclaimer_missing", "unknowns[0]")], PREVIOUS)
    assert f'"{GRID_DISCLAIMER}"' in note and "unknowns[0]" in note
    for boundary in ("grid connection capacity", "transformer capacity", "land availability"):
        assert boundary in note


@pytest.mark.parametrize(
    ("rule", "idiom", "rephrasing"),
    [("number_word", '"one another"', '"each other"'),
     ("approximation_language", '"around Kigali"', '"near"')],
)  # fmt: skip
def test_a_word_rule_says_the_word_itself_must_go(rule, idiom, rephrasing):
    # M9's own note suggests an absence phrase or digits; for "one another" neither applies,
    # and the live retry copied it unchanged. The agent's correction names the idiom.
    note = agent_retry_note([issue(rule)], PREVIOUS)
    corrections = note.split("Corrections for the rules named above:")[1]
    assert "Rephrase so the word is gone" in corrections
    assert idiom in corrections and rephrasing in corrections
    assert "where they differ from the general advice above, follow these" in corrections


# --- Which quotation failed, and the chunk's exact text --------------------------------------

SPEC = "kb/spec/8-network-optimization-mclp#1"
DECISION = "kb/decisions/d-046#1"
SPEC_TEXT = (
    "## 8. Network optimization (MCLP)\n\n- **Objective:** maximize Σ wᵢ·yᵢ + λ·Σ sⱼ·xⱼ, with "
    "demand weights wᵢ normalized to sum to 1"
)
DECISION_TEXT = "Sites strictly closer than 2 km cannot both be selected."


def _chunk(chunk_id: str, text: str):
    return record(chunk_id, "chunk", "RETRIEVED_FACT", "SiteScout project knowledge",
                  "docs/SPEC.md", "0" * 64, text)  # fmt: skip


TRANSCRIPT = (
    ToolCallRecord(
        tool="search_knowledge",
        arguments={"query": "how the network is chosen"},
        result={"chunks": [_chunk(SPEC, SPEC_TEXT).model_dump(mode="json"),
                           _chunk(DECISION, DECISION_TEXT).model_dump(mode="json")]},
    ),
)  # fmt: skip


def _quoting(*quotes: str, ids=(SPEC,)) -> dict:
    return {"direct_answer": [{"text": "The network is chosen by an exact MCLP.",
                               "kind": "RETRIEVED_FACT", "evidence_ids": list(ids),
                               "quotes": list(quotes)}]}  # fmt: skip


def _hints(previous) -> str:
    note = agent_retry_note([issue("quote_not_in_cited_chunk")], previous, TRANSCRIPT)
    return note.split("Quotations the validator could not find, one by one:")[1]


def test_a_quotation_without_its_markdown_is_named_with_the_chunks_exact_text():
    # The live network_comparison fallback: "**Objective:**" quoted as "Objective:" twice.
    exact = "Sites strictly closer than 2 km"
    hints = _hints(_quoting("Objective: maximize Σ wᵢ·yᵢ", exact, ids=(SPEC, DECISION)))
    assert '"Objective: maximize Σ wᵢ·yᵢ"' in hints
    assert f'{SPEC}, which reads "**Objective:** maximize Σ wᵢ·yᵢ"' in hints
    assert exact not in hints  # a quotation that was found is not named


def test_the_suggested_text_passes_the_unchanged_validator():
    suggested = "**Objective:** maximize Σ wᵢ·yᵢ"
    assert f'"{suggested}"' in _hints(_quoting("OBJECTIVE: Maximize Σ wᵢ·yᵢ"))
    session = ToolRecords(records=(_chunk(SPEC, SPEC_TEXT),))
    answer = AgentAnswer.model_validate(_quoting(suggested))
    assert validate_agent_answer(answer, session, min_quote_words=3).passed


def test_a_quotation_from_a_chunk_the_statement_does_not_cite_is_pointed_to_it():
    hints = _hints(_quoting("Sites strictly closer than 2 km"))
    assert f"is from {DECISION}, which that statement does not cite" in hints
    assert f"Cite {DECISION}" in hints


@pytest.mark.parametrize(
    "quote",
    ["maximize Σ wᵢ·yᵢ ... normalized to sum to 1",  # joined passages
     "The objective maximizes weighted covered demand"],  # paraphrase
)  # fmt: skip
def test_a_joined_or_invented_quotation_gets_no_suggested_text(quote):
    hints = _hints(_quoting(quote))
    assert "is not in any chunk you were shown" in hints and "reads" not in hints


@pytest.mark.parametrize(
    "previous",
    [None, {}, {"direct_answer": "x"}, {"direct_answer": []}, {"direct_answer": ["x"]},
     _quoting("Objective: maximize Σ wᵢ·yᵢ") | {"evidence": None}],
)  # fmt: skip
def test_hints_never_fail_on_an_unexpected_previous_answer(previous):
    errors = [issue("quote_not_in_cited_chunk"), issue("quote_not_in_cited_chunk", "evidence[3]")]
    note = agent_retry_note(errors, previous, TRANSCRIPT)
    assert CORRECTIONS["quote_not_in_cited_chunk"] in note


def test_both_agent_adapters_name_the_failed_quotation():
    context = ModelContext(
        question="How is the network chosen?",
        tools=AGENT_TOOL_DEFINITIONS,
        transcript=TRANSCRIPT,
        validation_errors=(issue("quote_not_in_cited_chunk"),),
        previous_answer_json=_quoting("Objective: maximize Σ wᵢ·yᵢ"),
    )
    anthropic = anthropic_request(context, _settings("anthropic", "claude-sonnet-5"),
                                  min_quote_words=3)  # fmt: skip
    openai = openai_request(context, _settings("openai", "gpt-x"), min_quote_words=3)
    for note in (anthropic["messages"][-1]["content"][-1]["text"], openai["input"][-1]["content"]):
        assert '"**Objective:** maximize Σ wᵢ·yᵢ"' in note


def test_only_the_rules_a_rejection_names_get_a_correction():
    note = agent_retry_note([issue("number_not_grounded")], PREVIOUS)
    corrections = note.split("Corrections for the rules named above:")[1]
    assert "- number_not_grounded:" in corrections and "Top-30" in corrections
    assert "- quote_not_in_cited_chunk:" not in corrections
    assert "keep its quotes exactly" in corrections  # the agent's own copy instruction


def test_the_agent_note_extends_m9s_note_without_changing_it():
    errors = [issue("number_not_grounded"), issue("quote_not_in_cited_chunk", "evidence[1]")]
    assert agent_retry_note(errors, PREVIOUS).startswith(retry_note(errors, PREVIOUS))


def test_every_rule_either_validator_can_raise_has_a_correction():
    raised = {"malformed_answer"}
    for path in ("analyst/validate.py", "agent/validate.py"):
        text = (PROJECT_ROOT / "src" / "sitescout" / path).read_text(encoding="utf-8")
        raised |= set(re.findall(r'fail\(\s*"([a-z_]+)"', text))
        raised |= set(re.findall(r'rule="([a-z_]+)"', text))
    assert raised - set(CORRECTIONS) == set()


def _settings(provider: str, model: str) -> AgentProviderSettings:
    return AgentProviderSettings.model_validate(
        {"provider": provider, "model": model, "max_tokens": 512, "timeout_s": 30,
         "temperature": None}
    )  # fmt: skip


def _retry_context() -> ModelContext:
    return ModelContext(
        question="How do the networks differ?",
        tools=AGENT_TOOL_DEFINITIONS,
        validation_errors=(issue("quote_not_in_cited_chunk"), issue("grid_disclaimer_missing")),
        previous_answer_json=PREVIOUS,
    )


def test_both_agent_adapters_send_the_corrections_on_a_retry():
    anthropic = anthropic_request(
        _retry_context(), _settings("anthropic", "claude-sonnet-5"), min_quote_words=3
    )
    openai = openai_request(_retry_context(), _settings("openai", "gpt-x"), min_quote_words=3)
    anthropic_note = anthropic["messages"][-1]["content"][-1]["text"]
    openai_note = openai["input"][-1]["content"]
    for note in (anthropic_note, openai_note):
        assert "Corrections for the rules named above:" in note
        assert CORRECTIONS["quote_not_in_cited_chunk"] in note
        assert CORRECTIONS["grid_disclaimer_missing"] in note


def test_the_m9_analyst_providers_still_send_m9s_own_note():
    for path in ("analyst/anthropic_provider.py", "analyst/openai_provider.py"):
        text = (PROJECT_ROOT / "src" / "sitescout" / path).read_text(encoding="utf-8")
        assert "agent_retry_note" not in text and "retry_note(" in text


# --- In the loop: a correction can validate, and the budget does not grow --------------------


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


def test_a_retry_that_follows_the_grid_correction_validates(context):
    base = "The stored score of cand-a is 71.0, and grid evidence is recorded."

    def model(ctx):
        if not ctx.transcript:
            return call("get_site", candidate_id="cand-a")
        fixed = {e.rule for e in ctx.validation_errors} == {"grid_disclaimer_missing"}
        text = f"{base} {GRID_DISCLAIMER}" if fixed else base
        return final(stmt(text, "CALCULATED", ("cand-a/score",)))

    result = run_agent(context, "What is the score of cand-a?", reactive(model), limits())
    assert result.status == "answered" and result.state.retried
    assert [e.rule for e in result.state.validation_attempts[0].errors] == [
        "grid_disclaimer_missing"
    ]


def test_a_retry_that_rephrases_a_number_word_validates(context):
    # The shape of the live network_comparison failure: grounded numbers, one idiom.
    def model(ctx):
        if not ctx.transcript:
            return call("get_site", candidate_id="cand-a")
        fixed = {e.rule for e in ctx.validation_errors} == {"number_word"}
        phrase = "each other" if fixed else "one another"
        return final(stmt(f"The stored score of cand-a is 71.0; sites are compared with {phrase}.",
                          "CALCULATED", ("cand-a/score",)))  # fmt: skip

    result = run_agent(context, "What is the score of cand-a?", reactive(model), limits())
    assert result.status == "answered" and result.state.retried
    assert [e.rule for e in result.state.validation_attempts[0].errors] == ["number_word"]
    assert result.state.validation_attempts[1].passed


def test_a_rejected_retry_ends_the_run_within_the_answer_budget(context):
    wrong = final(stmt("The stored score of cand-a is 99.9.", "CALCULATED", ("cand-a/score",)))
    model = reactive(lambda ctx: call("get_site", candidate_id="cand-a") if not ctx.transcript
                     else wrong)  # fmt: skip
    result = run_agent(context, "What is the score of cand-a?", model, limits())
    assert result.termination == "validation_failed"
    assert len(result.state.validation_attempts) == ANSWER_RETRIES + 1
