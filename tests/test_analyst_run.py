"""M9 phase 3a: the provider interface, the fake model and the tool-calling loop.

Everything here runs offline, with no model, no network and no environment variable, on
the SYNTHETIC world through M6 (`world`/`data` fixtures, one network site: cand-a).
"""

import tomllib

import pytest

from sitescout.analyst import (
    REGISTRY,
    AnalystData,
    FakeModel,
    FindQuery,
    ModelContext,
    ModelStep,
    RunLimits,
    ScriptExhausted,
    ToolCallRequest,
    find_sites,
    run_analyst,
)


@pytest.fixture
def world(analyst_world):
    """The SYNTHETIC world through M6, built once per module (conftest.analyst_world)."""
    config, processed, _ = analyst_world
    return config, processed


@pytest.fixture
def data(world):
    config, processed = world
    return AnalystData.load(config, processed)


@pytest.fixture
def selected(data):
    return next(s.candidate_id for s in find_sites(data, FindQuery()).sites if s.selected_mclp)


@pytest.fixture
def score(data, selected):
    """The real EvidenceRecord for the network site's score, never a hand-typed value."""
    return next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")


def _tool_call(tool, **arguments) -> ModelStep:
    return ModelStep(tool_call=ToolCallRequest(tool=tool, arguments=arguments))


def _answer(text, kind, ids) -> ModelStep:
    statement = {"text": text, "kind": kind, "evidence_ids": list(ids)}
    return ModelStep(answer_json={"direct_answer": [statement]})


def _scripted(*steps: ModelStep) -> FakeModel:
    return FakeModel(list(steps))


# --- 1, 2. Valid one-tool and multi-tool flows -------------------------------------------------


def test_valid_one_tool_flow(data, selected):
    site = next(r for r in find_sites(data, FindQuery(candidate_id=selected)).sites)
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "What is the score?", model)
    assert result.status == "answered"
    assert result.answer.direct_answer[0].text == f"The score is {score.display}."
    assert [c.tool for c in result.transcript] == ["get_site"]
    assert site.candidate_id == selected  # sanity: the fixture found the right site


def test_valid_multi_tool_flow(data, selected):
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    model = _scripted(
        _tool_call("find_sites", selected_mclp=True),
        _tool_call("get_site", candidate_id=selected),
        _tool_call("explain_score", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "Explain the network site's score.", model)
    assert result.status == "answered"
    assert [c.tool for c in result.transcript] == ["find_sites", "get_site", "explain_score"]
    assert all(isinstance(c.result, dict) for c in result.transcript)


# --- 3, 4, 5. Argument validation, unknown tools, invalid arguments -----------------------------


def test_tool_arguments_are_validated_before_execution(data):
    # find_sites rejects unknown filter fields (extra="forbid"): caught before any tool runs.
    model = _scripted(_tool_call("find_sites", similar_to="fuel"))
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert "invalid arguments" in result.fallback.reason
    assert result.transcript == ()


def test_unknown_tool_rejection(data):
    model = _scripted(_tool_call("delete_all_sites", candidate_id="x"))
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert "unknown tool" in result.fallback.reason
    assert "delete_all_sites" in result.fallback.reason
    assert result.transcript == ()


def test_invalid_arguments_for_a_known_tool(data, selected):
    # get_site's SiteIdArgs needs candidate_id, not "id".
    model = _scripted(_tool_call("get_site", id=selected))
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert "invalid arguments" in result.fallback.reason


def test_a_tools_own_refusal_falls_back_without_a_retry(data):
    model = _scripted(_tool_call("get_site", candidate_id="cand-does-not-exist"))
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert "refused" in result.fallback.reason
    assert not result.retried


# --- 6, 7, 8, 12. Answer validation and the one retry ------------------------------------------


def test_6_valid_answer_passes(data, selected):
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", model)
    assert result.status == "answered" and not result.retried
    assert len(result.attempts) == 1 and result.attempts[0].passed


def test_7_8_invalid_answer_then_a_corrected_one_passes(data, selected):
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer("The score is roughly there.", "CALCULATED", [score.id]),  # fails: approximation
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),  # corrected
    )
    result = run_analyst(data, "?", model)
    assert result.status == "answered" and result.retried
    assert len(result.attempts) == 2
    assert not result.attempts[0].passed and result.attempts[1].passed


def test_9_two_invalid_answers_fall_back(data, selected):
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer("The score is roughly there.", "CALCULATED", [score.id]),
        _answer("The score is still roughly there.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", model)
    assert result.status == "fallback" and result.retried
    assert len(result.attempts) == 2
    assert result.fallback.reason == "the answer failed validation twice"
    assert result.fallback.label == "no AI summary"
    assert any(r.id == score.id for r in result.fallback.records)  # the raw evidence is kept


def test_10_malformed_answer_falls_back_after_one_retry(data, selected):
    model = FakeModel(
        [
            _tool_call("get_site", candidate_id=selected),
            ModelStep(answer_json={"direct_answer": "not a list"}),  # fails Answer's own schema
            ModelStep(answer_json={"direct_answer": "still not a list"}),
        ]
    )
    result = run_analyst(data, "?", model)
    assert result.status == "fallback" and result.retried
    assert len(result.attempts) == 2
    assert all(not a.passed for a in result.attempts)
    assert result.attempts[0].errors[0].rule == "malformed_answer"


def test_12_the_validation_errors_are_passed_back_on_the_retry(data, selected):
    score = next(r for r in find_sites(data, FindQuery()).records if r.id == f"{selected}/score")
    seen: list[tuple] = []

    def fn(ctx: ModelContext) -> ModelStep:
        seen.append(ctx.validation_errors)
        if not ctx.transcript:
            return _tool_call("get_site", candidate_id=selected)
        if not ctx.validation_errors:
            return _answer("The score is roughly there.", "CALCULATED", [score.id])
        # the retry: correct the flaw the validator actually reported
        assert any(e.rule == "approximation_language" for e in ctx.validation_errors)
        return _answer(f"The score is {score.display}.", "CALCULATED", [score.id])

    result = run_analyst(data, "?", FakeModel(fn))
    assert result.status == "answered"
    assert seen[0] == () and seen[1] == () and seen[2] != ()  # only the retry turn carries errors


def test_the_previous_answer_is_passed_back_on_the_retry_turn_only(data, selected, score):
    seen: list[dict | None] = []

    def fn(ctx: ModelContext) -> ModelStep:
        seen.append(ctx.previous_answer_json)
        if not ctx.transcript:
            return _tool_call("get_site", candidate_id=selected)
        if not ctx.validation_errors:
            return _answer("The score is roughly there.", "CALCULATED", [score.id])
        statement = {
            "text": "The score is roughly there.",
            "kind": "CALCULATED",
            "evidence_ids": [score.id],
        }
        assert ctx.previous_answer_json == {
            "direct_answer": [statement],
            "evidence": [],
            "interpretation": [],
            "unknowns": [],
            "next_investigation": [],
        }
        return _answer(f"The score is {score.display}.", "CALCULATED", [score.id])

    result = run_analyst(data, "?", FakeModel(fn))
    assert result.status == "answered"
    assert seen[0] is None and seen[1] is None and seen[2] is not None


def test_the_previous_malformed_answer_is_passed_back_verbatim(data, selected):
    seen: list[dict | None] = []

    def fn(ctx: ModelContext) -> ModelStep:
        seen.append(ctx.previous_answer_json)
        if not ctx.transcript:
            return _tool_call("get_site", candidate_id=selected)
        if ctx.previous_answer_json is None:
            return ModelStep(answer_json={"direct_answer": "not a list"})
        assert ctx.previous_answer_json == {"direct_answer": "not a list"}
        return _answer("The score is 76.6.", "CALCULATED", [f"{selected}/score"])

    result = run_analyst(data, "?", FakeModel(fn))
    assert result.status in ("answered", "fallback")  # the real point: previous_answer_json above
    assert seen[0] is None and seen[1] is None and seen[2] is not None


def test_a_malformed_answer_uses_the_same_single_retry_as_a_content_failure(data, selected, score):
    # A malformed first answer is attempt 1; the corrected second answer is the one retry.
    model = FakeModel(
        [
            _tool_call("get_site", candidate_id=selected),
            ModelStep(answer_json={"direct_answer": "not a list"}),
            _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
        ]
    )
    result = run_analyst(data, "?", model)
    assert result.status == "answered" and result.retried
    assert [a.passed for a in result.attempts] == [False, True]
    assert result.attempts[0].errors[0].rule == "malformed_answer"


def test_a_malformed_then_content_invalid_answer_falls_back(data, selected, score):
    # Both failure kinds draw on one budget: malformed, then invalid content, is two failures.
    model = FakeModel(
        [
            _tool_call("get_site", candidate_id=selected),
            ModelStep(answer_json={"direct_answer": "not a list"}),
            _answer("The score is roughly there.", "CALCULATED", [score.id]),
        ]
    )
    result = run_analyst(data, "?", model)
    assert result.status == "fallback" and len(result.attempts) == 2


# --- 11. The tool-call limit --------------------------------------------------------------------


def test_11_tool_call_limit_exceeded_falls_back(data, selected):
    limits = RunLimits(max_tool_calls=2)
    model = _scripted(
        _tool_call("find_sites"),
        _tool_call("get_site", candidate_id=selected),
        _tool_call("explain_score", candidate_id=selected),  # the third call: over the limit
    )
    result = run_analyst(data, "?", model, limits)
    assert result.status == "fallback"
    assert "tool-call limit" in result.fallback.reason
    assert len(result.transcript) == 2  # the first two calls did run


def test_the_tool_call_limit_comes_from_config(data, selected):
    # D-051: the loop's limit is the analyst block's max_tool_calls, not a code default.
    configured = data.config.settings.analyst.max_tool_calls
    assert RunLimits.from_settings(data.config.settings.analyst).max_tool_calls == configured
    model = _scripted(*[_tool_call("find_sites") for _ in range(configured + 1)])
    result = run_analyst(data, "?", model)
    assert len(result.transcript) == configured
    assert "tool-call limit" in result.fallback.reason


# --- Protocol violations and provider failures --------------------------------------------------


def test_neither_tool_call_nor_answer_falls_back(data):
    model = _scripted(ModelStep())
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert "neither" in result.fallback.reason


def test_both_tool_call_and_answer_falls_back(data, selected):
    model = _scripted(
        ModelStep(
            tool_call=ToolCallRequest(tool="get_site", arguments={"candidate_id": selected}),
            answer_json={"direct_answer": [{"text": "x", "kind": "UNKNOWN"}]},
        )
    )
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"


def test_a_provider_that_raises_falls_back_instead_of_crashing(data):
    def fn(ctx):
        raise RuntimeError("the model backend is unreachable")

    result = run_analyst(data, "?", FakeModel(fn))
    assert result.status == "fallback"
    assert "unreachable" in result.fallback.reason


def test_an_exhausted_script_is_treated_as_a_provider_failure(data):
    model = FakeModel([])  # no steps at all
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    with pytest.raises(ScriptExhausted):
        model.next_step(ModelContext(question="?", tools=()))


# --- Security: what the fake model cannot do ----------------------------------------------------


def test_the_model_cannot_call_an_unknown_tool(data):
    for bad in ("os.system", "eval", "__import__", "write_file", ""):
        result = run_analyst(data, "?", _scripted(_tool_call(bad)))
        assert result.status == "fallback" and "unknown tool" in result.fallback.reason


def test_the_model_cannot_call_a_tool_with_invalid_arguments(data):
    result = run_analyst(data, "?", _scripted(_tool_call("compare_sites", a="x")))  # missing b
    assert result.status == "fallback" and "invalid arguments" in result.fallback.reason


def test_the_model_cannot_bypass_validation(data, selected):
    # An answer citing a real id but asserting an ungrounded number must never be returned.
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer("The score is 999.9.", "CALCULATED", [f"{selected}/score"]),
        _answer("The score is 999.9.", "CALCULATED", [f"{selected}/score"]),
    )
    result = run_analyst(data, "?", model)
    assert result.status == "fallback"
    assert result.answer is None


def test_the_model_cannot_cause_a_file_write(data, selected, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        ModelStep(answer_json={"direct_answer": "'; import os; os.system('touch pwned')"}),
        ModelStep(answer_json={"direct_answer": "still not a list"}),
    )
    run_analyst(data, "?", model)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after == {}


def test_registry_only_exposes_the_six_approved_tools():
    assert set(REGISTRY) == {
        "find_sites",
        "get_site",
        "compare_sites",
        "explain_score",
        "network_contribution",
        "generate_brief",
    }
    for spec in REGISTRY.values():
        assert spec.args_model.model_config.get("extra") == "forbid"


# --- 13, 14, 15, 16, 17. Regression: offline, no side effects, no AI, deterministic ------------


def test_13_no_files_are_written(data, selected, score):
    from pathlib import Path

    before = {p: p.stat().st_mtime for p in Path().glob("*") if p.is_file()}
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    run_analyst(data, "?", model)
    after = {p: p.stat().st_mtime for p in Path().glob("*") if p.is_file()}
    assert before == after


def test_14_no_network_module_is_newly_imported(data, selected, score):
    import sys

    forbidden = {"requests", "httpx", "urllib3"}
    before = {m.split(".")[0] for m in sys.modules}
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    run_analyst(data, "?", model)
    after = {m.split(".")[0] for m in sys.modules}
    assert (after - before) & forbidden == set()


def test_15_no_environment_variable_is_read(data, selected, score, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "poisoned")
    monkeypatch.setenv("SITESCOUT_ANALYST_MODEL", "poisoned")
    monkeypatch.setenv("SITESCOUT_ANALYST_MAX_TOOL_CALLS", "999")

    def build():
        model = _scripted(
            _tool_call("get_site", candidate_id=selected),
            _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
        )
        return run_analyst(data, "?", model)

    a, b = build(), build()
    assert a.status == b.status == "answered"
    configured = RunLimits.from_settings(data.config.settings.analyst)
    assert configured.max_tool_calls == 6  # unaffected by SITESCOUT_ANALYST_MAX_TOOL_CALLS


def test_16_no_ai_sdk_is_imported(data, selected, score):
    import sys

    from sitescout.config import PROJECT_ROOT

    # Running the loop imports no AI package. (With the analyst extra installed, another test
    # may already have imported the SDK in this session, so this compares before and after;
    # test_analyst_provider proves in a clean interpreter that SiteScout never imports it.)
    forbidden = {"anthropic", "openai", "langchain", "langgraph", "chromadb", "faiss"}
    before = {m.split(".")[0] for m in sys.modules}
    model = _scripted(
        _tool_call("get_site", candidate_id=selected),
        _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    run_analyst(data, "?", model)
    assert ({m.split(".")[0] for m in sys.modules} - before) & forbidden == set()
    # The SDK is an optional extra (D-051); the base dependencies stay AI-free.
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = " ".join(project["project"]["dependencies"]).lower()
    assert not [package for package in forbidden if package in base]


def test_17_repeated_runs_give_identical_results(data, selected, score):
    def build():
        model = _scripted(
            _tool_call("get_site", candidate_id=selected),
            _answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
        )
        return run_analyst(data, "?", model)

    first, second = build(), build()
    assert first.model_dump() == second.model_dump()
