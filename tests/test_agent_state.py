"""M10 Phase 3 (D-058): the agent state, its serialization and the evidence recovered from it."""

import pytest
from pydantic import ValidationError

from agent_support import (
    call,
    final,
    limits,
    make_context,
    quote_from,
    reactive,
    script,
    seen,
    stmt,
    unknown_answer,
)
from sitescout.agent import (
    AgentResult,
    AgentState,
    Observation,
    ObservationError,
    records_in,
    run_agent,
    session_from_observations,
    validate_agent_answer,
)


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


def _answering_run(context) -> AgentResult:
    """A run that uses a structured tool, a search, a duplicate and an error, then answers."""

    def model(ctx):
        n = len(ctx.transcript)
        plan = [
            call("get_site", candidate_id="cand-a"),
            call("search_knowledge", query="how the score is built"),
            call("get_site", candidate_id="cand-a"),  # a duplicate
            call("get_site", candidate_id="cand-nope"),  # a refusal
            call("compare_sites", a="cand-a", b="cand-b"),
        ]
        if n < len(plan):
            return plan[n]
        records = seen(ctx)
        chunk = next(r for r in records.values() if r.id.startswith("kb/"))
        return final(
            stmt(
                f"The stored score is {records['cand-a/score'].display}.",
                "CALCULATED",
                ("cand-a/score",),
            ),
            stmt(
                "The documentation describes how it is built.",
                "RETRIEVED_FACT",
                (chunk.id,),
                (quote_from(chunk, 5),),
            ),
        )

    return run_agent(context, "What supports cand-a?", reactive(model), limits())


def test_the_state_is_an_immutable_snapshot(context):
    state = _answering_run(context).state
    with pytest.raises(ValidationError):
        state.iterations = 99  # type: ignore[misc]
    assert isinstance(state.observations, tuple) and isinstance(state.actions, tuple)


def test_a_fresh_state_is_empty():
    state = AgentState(question="q")
    assert (
        state.iterations,
        state.tool_calls,
        state.retrieval_calls,
        state.recoverable_errors,
    ) == (0, 0, 0, 0)
    assert state.actions == () and state.observations == () and state.termination is None
    assert state.tool_observations == () and state.retrieval_observations == ()


def test_the_counters_describe_the_run(context):
    result = _answering_run(context)
    state = result.state
    assert result.status == "answered" and state.question == "What supports cand-a?"
    assert state.iterations == 6  # five tool turns and the answer
    assert state.tool_calls == 3  # get_site, the refusal, compare_sites; the duplicate was not run
    assert (state.retrieval_calls, state.duplicate_calls, state.recoverable_errors) == (1, 1, 1)
    assert [a.tool for a in state.actions] == [o.tool for o in state.observations]
    assert state.termination == "answered" and state.answer is not None


def test_the_trajectory_is_a_compact_ordered_record_of_the_run(context):
    trajectory = _answering_run(context).state.trajectory()
    assert [(t["index"], t["tool"], t["status"], t["cached"]) for t in trajectory] == [
        (1, "get_site", "ok", False),
        (2, "search_knowledge", "ok", False),
        (3, "get_site", "ok", True),
        (4, "get_site", "error", False),
        (5, "compare_sites", "ok", False),
    ]
    assert trajectory[3]["error"] == "tool_error" and trajectory[0]["error"] is None
    assert trajectory[0]["records"] == trajectory[2]["records"] > 0
    assert set(trajectory[0]) == {
        "index",
        "tool",
        "arguments",
        "status",
        "cached",
        "error",
        "records",
    }


def test_the_provenance_of_every_record_survives_in_the_observations(context):
    state = _answering_run(context).state
    site, search, duplicate, refused, compare = state.observations
    assert "cand-a/score" in site.record_ids and "cand-a/score" in duplicate.record_ids
    assert any(i.startswith("kb/") for i in search.record_ids) and refused.record_ids == ()
    assert any(i.startswith("compare/cand-a/cand-b/") for i in compare.record_ids)
    assert duplicate.duplicate_of == site.index and duplicate.result["cached"] is True


def test_the_evidence_is_recovered_from_the_observations_alone(context):
    state = _answering_run(context).state
    session = session_from_observations(state.observations)
    ids = {r.id for r in session.records}
    executed = [o for o in state.observations if o.status == "ok" and not o.cached]
    assert ids | {c.id for c in session.comparisons} == {i for o in executed for i in o.record_ids}
    assert any(c.id.startswith("compare/cand-a/cand-b/") for c in session.comparisons)
    assert "cand-nope/score" not in ids  # a refused call holds no records


def test_a_cached_duplicate_and_an_error_add_no_records():
    site = Observation(
        index=1, tool="get_site", arguments={}, tool_kind="analyst", status="ok",
        result={"records": []}, record_ids=(),
    )  # fmt: skip
    duplicate = site.model_copy(update={"index": 2, "cached": True, "duplicate_of": 1})
    error = Observation(
        index=3, tool="get_site", arguments={}, tool_kind="analyst", status="error",
        error=ObservationError(kind="tool_error", message="x"),
    )  # fmt: skip
    assert session_from_observations([site, duplicate, error]).records == ()


def test_an_answer_revalidates_from_the_logged_state_with_the_same_result(context):
    result = _answering_run(context)
    session = session_from_observations(result.state.observations)
    again = validate_agent_answer(result.answer, session, min_quote_words=3)
    assert again == result.validation and again.passed


def test_the_whole_result_survives_a_json_round_trip(context):
    result = _answering_run(context)
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


def test_a_fallback_result_round_trips_too(context):
    result = run_agent(context, "q", script(call("get_site", candidate_id="cand-a")), limits())
    assert result.status == "fallback"
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


def test_what_the_provider_is_shown_is_the_result_or_the_structured_error(context):
    ok, error = Observation(
        index=1, tool="get_site", arguments={}, tool_kind="analyst", status="ok", result={"a": 1}
    ), Observation(
        index=2, tool="x", arguments={}, tool_kind=None, status="error",
        error=ObservationError(kind="unknown_tool", message="no such tool"),
    )  # fmt: skip
    assert ok.as_model_result() == {"a": 1}
    assert error.as_model_result() == {"error": {"kind": "unknown_tool", "message": "no such tool"}}


def test_records_are_found_in_nested_results_and_other_dicts_are_ignored():
    record = {
        "id": "x/y", "claim": "c", "type": "CALCULATED",
        "evidence": {"source": "s", "metric": "m", "value": 1, "unit": None}, "display": "1",
    }  # fmt: skip
    found, compared = records_in({"a": [{"records": [record]}], "b": {"id": "not a record"}})
    assert list(found) == ["x/y"] and compared == {}


def test_a_run_without_observations_has_no_evidence(context):
    result = run_agent(context, "q", script(unknown_answer()), limits())
    assert session_from_observations(result.state.observations).records == ()
