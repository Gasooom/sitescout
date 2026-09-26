"""M10 Phase 3 (D-058): the agent loop, its dispatch, limits, duplicate handling, provider
protocol and fallback. Every model here is scripted (``FakeModel``): no API, no network."""

import dataclasses
import hashlib
import logging

import pytest

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
    AGENT_REGISTRY,
    AGENT_TOOL_DEFINITIONS,
    ANSWER_RETRIES,
    AgentLimits,
    AgentResult,
    run_agent,
)
from sitescout.analyst import ModelContext, ModelStep, find_sites
from sitescout.analyst.tools import FindQuery
from sitescout.investigation import nearby_sites, network_summary


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture(scope="module")
def processed(analyst_world):
    return analyst_world[1]


def run(context, model, question="a question", **changes) -> AgentResult:
    return run_agent(context, question, model, limits(**changes))


def _score_answer(ctx: ModelContext, site: str = "cand-a") -> ModelStep:
    record = seen(ctx)[f"{site}/score"]
    return final(stmt(f"The stored score is {record.display}.", "CALCULATED", (record.id,)))


# --- One tool, then the answer ----------------------------------------------------------------


def test_one_tool_then_a_final_answer(context):
    model = reactive(
        lambda ctx: (
            _score_answer(ctx) if ctx.transcript else call("get_site", candidate_id="cand-a")
        )
    )
    result = run(context, model)
    assert (result.status, result.termination) == ("answered", "answered")
    state = result.state
    assert (state.iterations, state.tool_calls, state.retrieval_calls) == (2, 1, 0)
    assert [o.tool for o in state.observations] == ["get_site"]
    assert result.answer is not None and result.fallback is None
    assert result.validation is not None and result.validation.passed


def test_each_of_the_nine_capabilities_can_be_called(context):
    calls = [
        ("find_sites", {"candidate_id": "cand-a"}),
        ("get_site", {"candidate_id": "cand-a"}),
        ("compare_sites", {"a": "cand-a", "b": "cand-b"}),
        ("explain_score", {"candidate_id": "cand-a"}),
        ("network_contribution", {"candidate_id": "cand-a"}),
        ("generate_brief", {"candidate_id": "cand-a"}),
        ("network_summary", {}),
        ("nearby_sites", {"candidate_id": "cand-a", "radius_m": 1}),
        ("search_knowledge", {"query": "how the score is built"}),
    ]
    result = run(context, script(*(call(t, **a) for t, a in calls), unknown_answer()))
    assert result.status == "answered"
    assert [(o.tool, o.status, o.tool_kind) for o in result.state.observations] == [
        (t, "ok", AGENT_REGISTRY[t].kind) for t, _ in calls
    ]
    assert (result.state.tool_calls, result.state.retrieval_calls) == (8, 1)
    for observation in result.state.observations:
        assert observation.result is not None and observation.result["tool"] == observation.tool


def test_structured_results_are_preserved_exactly(context):
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("network_summary"),
        call("nearby_sites", candidate_id="cand-a", radius_m=1),
        unknown_answer(),
    )
    result = run(context, model)
    site, network, nearby = result.state.observations[:3]
    direct = find_sites(context.investigation.analyst, FindQuery(candidate_id="cand-a"))
    assert direct.sites[0].candidate_id == "cand-a"
    assert network.result == network_summary(context.investigation).model_dump(mode="json")
    assert nearby.result == nearby_sites(context.investigation.analyst, "cand-a", 1).model_dump(
        mode="json"
    )
    assert {r["id"] for r in site.result["records"]} <= set(site.record_ids)
    assert "cand-a/score" in site.record_ids and "network/mclp/sites" in network.record_ids


# --- Several steps ----------------------------------------------------------------------------


def test_several_tool_calls_accumulate_in_the_state(context):
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("explain_score", candidate_id="cand-a"),
        call("compare_sites", a="cand-a", b="cand-b"),
        unknown_answer(),
    )
    state = run(context, model).state
    assert (state.iterations, state.tool_calls, state.retrieval_calls) == (4, 3, 0)
    assert [a.index for a in state.actions] == [1, 2, 3]
    assert [o.index for o in state.observations] == [1, 2, 3]
    assert [a.tool for a in state.actions] == [o.tool for o in state.observations]
    assert state.termination == "answered" and state.answer is not None
    assert len(state.tool_observations) == 3 and state.retrieval_observations == ()


def test_retrieval_then_a_structured_tool(context):
    order = [
        call("search_knowledge", query="how confidence is determined"),
        call("network_summary"),
    ]
    result = run(context, script(*order, unknown_answer()))
    state = result.state
    assert [o.tool_kind for o in state.observations] == ["knowledge", "investigation"]
    assert (state.tool_calls, state.retrieval_calls) == (1, 1)
    assert len(state.retrieval_observations) == 1 and len(state.tool_observations) == 1


def test_a_structured_tool_then_retrieval(context):
    result = run(
        context,
        script(
            call("get_site", candidate_id="cand-a"),
            call("search_knowledge", query="grid evidence"),
            unknown_answer(),
        ),
    )
    assert [o.tool_kind for o in result.state.observations] == ["analyst", "knowledge"]
    assert result.status == "answered"


def test_the_model_sees_every_earlier_observation_and_can_use_it(context):
    contexts: list[ModelContext] = []

    def model(ctx: ModelContext) -> ModelStep:
        contexts.append(ctx)
        if len(ctx.transcript) == 0:
            return call("find_sites", selected_mclp=True)
        if len(ctx.transcript) == 1:  # the id comes from the observation: a real data dependency
            found = ctx.transcript[0].result["sites"][0]["candidate_id"]
            return call("network_contribution", candidate_id=found)
        return unknown_answer()

    result = run(context, reactive(model))
    assert result.status == "answered"
    used = result.state.observations[1].arguments["candidate_id"]
    assert used == result.state.observations[0].result["sites"][0]["candidate_id"]
    assert [len(c.transcript) for c in contexts] == [0, 1, 2]
    assert [t.tool for t in contexts[-1].transcript] == ["find_sites", "network_contribution"]


def test_the_model_is_shown_nine_tools_the_question_and_no_limit(context):
    contexts: list[ModelContext] = []

    def model(ctx):
        contexts.append(ctx)
        return unknown_answer()

    run(context, reactive(model), question="Why these sites?")
    first = contexts[0]
    assert first.question == "Why these sites?" and first.tools == AGENT_TOOL_DEFINITIONS
    assert len(first.tools) == 9 and first.transcript == () and first.validation_errors == ()
    assert set(ModelContext.model_fields) == {
        "question", "tools", "transcript", "validation_errors", "previous_answer_json",
    }  # fmt: skip


# --- Limits -----------------------------------------------------------------------------------


def test_the_configured_limits_are_the_defaults(context):
    settings = context.investigation.analyst.config.settings.agent
    assert AgentLimits.from_settings(settings) == AgentLimits(12, 10, 3, 2, 3)


def test_the_iteration_limit_ends_the_run_and_produces_no_answer(context):
    counter = {"turns": 0}

    def model(ctx):
        counter["turns"] += 1
        return call("get_site", candidate_id="cand-a")  # identical calls: cached, never an answer

    result = run(context, reactive(model), max_iterations=4)
    assert (result.status, result.termination) == ("fallback", "iteration_limit")
    assert counter["turns"] == 4 and result.state.iterations == 4 and result.answer is None
    assert result.fallback is not None and "iteration limit (4)" in result.fallback.reason


def test_reaching_a_limit_never_turns_into_an_answer(context):
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("get_site", candidate_id="cand-b"),
        unknown_answer(),
    )
    result = run(context, model, max_iterations=2)
    assert result.termination == "iteration_limit" and result.answer is None


def test_the_tool_call_limit_refuses_the_call_that_would_exceed_it(context):
    calls = [call("get_site", candidate_id=s) for s in ("cand-a", "cand-b", "cand-c")]
    result = run(context, script(*calls, unknown_answer()), max_tool_calls=2)
    assert (result.status, result.termination) == ("fallback", "tool_call_limit")
    state = result.state
    assert state.tool_calls == 2 and len(state.observations) == 2  # the third was not run
    assert len(state.actions) == 3 and state.iterations == 3


def test_the_retrieval_limit_refuses_the_search_that_would_exceed_it(context):
    searches = [call("search_knowledge", query=q) for q in ("percentile points", "spacing")]
    result = run(context, script(*searches, unknown_answer()), max_retrieval_calls=1)
    assert (result.status, result.termination) == ("fallback", "retrieval_call_limit")
    assert result.state.retrieval_calls == 1 and len(result.state.observations) == 1


def test_tool_and_retrieval_budgets_are_separate(context):
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("search_knowledge", query="score"),
        unknown_answer(),
    )
    result = run(context, model, max_tool_calls=1, max_retrieval_calls=1)
    assert (
        result.status == "answered" and result.state.tool_calls == 1 == result.state.retrieval_calls
    )


def test_the_default_tool_limit_is_ten_and_the_default_retrieval_limit_is_three(context):
    radii = [call("nearby_sites", candidate_id="cand-a", radius_m=r) for r in range(1, 13)]
    result = run_agent(context, "q", script(*radii))
    assert (result.termination, result.state.tool_calls) == ("tool_call_limit", 10)
    searches = [call("search_knowledge", query=q) for q in ("alpha", "beta", "gamma", "delta")]
    result = run_agent(context, "q", script(*searches))
    assert (result.termination, result.state.retrieval_calls) == ("retrieval_call_limit", 3)


def test_the_default_iteration_limit_is_twelve(context):
    result = run_agent(context, "q", reactive(lambda ctx: call("get_site", candidate_id="cand-a")))
    assert result.termination == "iteration_limit" and result.state.iterations == 12
    assert result.state.tool_calls == 1 and result.state.duplicate_calls == 11


def test_recoverable_errors_are_tolerated_up_to_the_limit_and_the_next_ends_the_run(context):
    bad = call("get_site")  # missing candidate_id
    two = run(context, script(bad, call("get_site", candidate_id="nope"), unknown_answer()))
    assert two.status == "answered" and two.state.recoverable_errors == 2
    three = run(
        context, script(bad, call("nope"), call("get_site", candidate_id="nope"), unknown_answer())
    )
    assert (three.status, three.termination) == ("fallback", "recoverable_error_limit")
    assert three.state.recoverable_errors == 3 and len(three.state.observations) == 3


def test_a_zero_error_budget_ends_the_run_on_the_first_error(context):
    result = run(context, script(call("get_site"), unknown_answer()), max_recoverable_errors=0)
    assert result.termination == "recoverable_error_limit" and result.state.iterations == 1


# --- Errors as observations -------------------------------------------------------------------


def test_an_unknown_tool_is_an_invalid_action_the_model_can_react_to(context):
    contexts: list[ModelContext] = []

    def model(ctx):
        contexts.append(ctx)
        return call("delete_everything", path="/") if not ctx.transcript else unknown_answer()

    result = run(context, reactive(model))
    observation = result.state.observations[0]
    assert (observation.status, observation.tool_kind) == ("error", None)
    assert observation.error is not None and observation.error.kind == "unknown_tool"
    assert (
        "delete_everything" in observation.error.message and "get_site" in observation.error.message
    )
    assert contexts[1].transcript[0].result["error"]["kind"] == "unknown_tool"
    assert result.state.tool_calls == 0 and result.state.recoverable_errors == 1


@pytest.mark.parametrize(
    "name", ["os.system", "__import__", "eval", "python", "shell", "../get_site", ""]
)
def test_no_arbitrary_action_can_run(context, name):
    result = run(context, script(call(name, command="whoami"), unknown_answer()))
    assert result.state.observations[0].error.kind == "unknown_tool"
    assert result.state.tool_calls == 0


@pytest.mark.parametrize(
    ("tool", "arguments", "fragment"),
    [
        ("get_site", {}, "candidate_id"),
        ("get_site", {"candidate_id": "cand-a", "extra": 1}, "extra"),
        ("get_site", {"candidate_id": 5}, "candidate_id"),
        ("compare_sites", {"a": "cand-a"}, "b"),
        ("nearby_sites", {"candidate_id": "cand-a", "radius_m": "5"}, "radius_m"),
        ("nearby_sites", {"candidate_id": "cand-a", "radius_m": 2.5}, "radius_m"),
        ("nearby_sites", {"candidate_id": "cand-a"}, "radius_m"),
        ("network_summary", {"radius": 1}, "radius"),
        ("search_knowledge", {}, "query"),
        ("search_knowledge", {"query": "x", "top_k": 3}, "top_k"),
    ],
)
def test_malformed_arguments_are_rejected_before_any_tool_runs(context, tool, arguments, fragment):
    result = run(context, script(call(tool, **arguments), unknown_answer()))
    observation = result.state.observations[0]
    assert observation.status == "error" and observation.error.kind == "malformed_arguments"
    assert fragment in observation.error.message
    assert result.state.tool_calls == 0 == result.state.retrieval_calls
    assert observation.arguments == arguments  # kept exactly as the model gave them


def test_a_tools_own_refusal_is_a_structured_observation_with_its_code(context):
    model = script(
        call("nearby_sites", candidate_id="cand-nope", radius_m=100),
        call("nearby_sites", candidate_id="cand-a", radius_m=10**9),
        call("get_site", candidate_id="cand-nope"),
        unknown_answer(),
    )
    observations = run(context, model, max_recoverable_errors=3).state.observations
    assert [(o.error.kind, o.error.code) for o in observations] == [
        ("tool_error", "unknown_site"),
        ("tool_error", "invalid_arguments"),
        ("tool_error", None),  # M9's own refusal has no code
    ]
    assert "cand-nope" in observations[0].error.message


def test_a_search_with_an_invalid_filter_is_a_recoverable_error_that_lists_valid_values(context):
    result = run(
        context, script(call("search_knowledge", query="x", topic="nonsense"), unknown_answer())
    )
    error = result.state.observations[0].error
    assert error.kind == "tool_error" and "valid values" in error.message
    assert result.state.retrieval_calls == 1 and result.state.recoverable_errors == 1


# --- Duplicate calls --------------------------------------------------------------------------


def _counting_registry(name):
    executed = []
    tool = AGENT_REGISTRY[name]

    def counted(ctx, args):
        executed.append(args)
        return tool.call(ctx, args)

    return {**AGENT_REGISTRY, name: dataclasses.replace(tool, call=counted)}, executed


def test_an_identical_call_is_not_run_twice(context):
    registry, executed = _counting_registry("get_site")
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("get_site", candidate_id="cand-a"),
        call("get_site", candidate_id="cand-b"),
        call("get_site", candidate_id="cand-b"),
        unknown_answer(),
    )
    result = run_agent(context, "q", model, limits(), registry)
    assert len(executed) == 2
    first, again = result.state.observations[0], result.state.observations[1]
    assert (again.cached, again.duplicate_of, first.cached) == (True, first.index, False)
    assert again.record_ids == first.record_ids and again.result["cached"] is True
    state = result.state
    assert (state.tool_calls, state.duplicate_calls) == (2, 2)  # a duplicate spends no tool budget


def test_different_arguments_run_separately(context):
    registry, executed = _counting_registry("nearby_sites")
    model = script(
        call("nearby_sites", candidate_id="cand-a", radius_m=1),
        call("nearby_sites", candidate_id="cand-a", radius_m=2),
        call("nearby_sites", candidate_id="cand-b", radius_m=1),
        unknown_answer(),
    )
    result = run_agent(context, "q", model, limits(), registry)
    assert len(executed) == 3 and result.state.duplicate_calls == 0
    assert not any(o.cached for o in result.state.observations)


def test_argument_order_and_omitted_none_values_do_not_defeat_the_cache(context):
    registry, executed = _counting_registry("nearby_sites")
    model = script(
        call("nearby_sites", candidate_id="cand-a", radius_m=5),
        call("nearby_sites", radius_m=5, candidate_id="cand-a"),
        unknown_answer(),
    )
    assert run_agent(context, "q", model, limits(), registry).state.duplicate_calls == 1
    assert len(executed) == 1
    registry, executed = _counting_registry("find_sites")
    model = script(call("find_sites", district=None), call("find_sites"), unknown_answer())
    assert run_agent(context, "q", model, limits(), registry).state.duplicate_calls == 1


def test_an_identical_search_is_not_repeated_and_spends_no_retrieval_budget(context):
    search = call("search_knowledge", query="percentile points")
    result = run(context, script(search, search, search, unknown_answer()), max_retrieval_calls=1)
    assert result.status == "answered"
    assert (result.state.retrieval_calls, result.state.duplicate_calls) == (1, 2)


def test_a_repeated_failing_call_is_not_rerun_but_still_counts_as_an_error(context):
    registry, executed = _counting_registry("get_site")
    bad = call("get_site", candidate_id="cand-nope")
    result = run_agent(context, "q", script(bad, bad, bad, unknown_answer()), limits(), registry)
    assert len(executed) == 1  # the refusal was reused
    assert result.termination == "recoverable_error_limit"  # a stuck model still stops
    assert [o.cached for o in result.state.observations] == [False, True, True]
    assert all(o.error.code is None for o in result.state.observations)


# --- The provider protocol --------------------------------------------------------------------


@pytest.mark.parametrize(
    "step",
    [
        ModelStep(),
        ModelStep(tool_call=call("get_site").tool_call, answer_json={"direct_answer": []}),
    ],
    ids=["neither", "both"],
)
def test_a_step_that_is_neither_a_tool_call_nor_an_answer_ends_the_run_safely(context, step):
    result = run(context, script(call("get_site", candidate_id="cand-a"), step, unknown_answer()))
    assert (result.status, result.termination) == ("fallback", "malformed_provider_response")
    assert result.fallback.label == "no AI summary" and result.state.iterations == 2


def test_a_provider_that_raises_ends_the_run_with_a_fallback_holding_what_was_gathered(context):
    def model(ctx):
        if not ctx.transcript:
            return call("get_site", candidate_id="cand-a")
        raise RuntimeError("the provider is down")

    result = run(context, reactive(model))
    assert (result.status, result.termination) == ("fallback", "provider_error")
    assert "the provider is down" in result.fallback.reason
    assert any(r.id == "cand-a/score" for r in result.fallback.records)


def test_a_script_that_runs_out_is_a_provider_failure_not_a_crash(context):
    result = run(context, script(call("get_site", candidate_id="cand-a")))
    assert result.termination == "provider_error" and result.state.iterations == 1


def test_a_malformed_final_answer_gets_one_retry_with_the_errors(context):
    seen_errors = []

    def model(ctx):
        seen_errors.append((ctx.validation_errors, ctx.previous_answer_json))
        return (
            ModelStep(answer_json={"nonsense": 1})
            if not ctx.validation_errors
            else unknown_answer()
        )

    result = run(context, reactive(model))
    assert result.status == "answered" and result.state.retried is True
    assert [a.passed for a in result.state.validation_attempts] == [False, True]
    errors, previous = seen_errors[1]
    assert errors[0].rule == "malformed_answer" and previous == {"nonsense": 1}


def test_two_invalid_final_answers_end_in_the_fallback(context):
    result = run(context, script(ModelStep(answer_json={"x": 1}), ModelStep(answer_json={"y": 2})))
    assert (result.status, result.termination) == ("fallback", "validation_failed")
    assert len(result.state.validation_attempts) == ANSWER_RETRIES + 1 == 2
    assert result.state.iterations == 2 and result.answer is None


def test_an_answer_needs_no_tool_call_when_nothing_is_known(context):
    result = run(context, script(unknown_answer()), question="What is the weather in Kigali?")
    assert result.status == "answered" and result.state.observations == ()


# --- Grounding through the loop ---------------------------------------------------------------


def _answer_after(context, first, good, bad):
    """Fetch ``first``; answer with ``bad(ctx)``, then with ``good(ctx)`` after the errors."""

    def model(ctx):
        if not ctx.transcript:
            return first
        return good(ctx) if ctx.validation_errors else bad(ctx)

    return run(context, reactive(model))


def test_an_unsupported_number_is_rejected_then_corrected(context):
    result = _answer_after(
        context,
        call("get_site", candidate_id="cand-a"),
        good=_score_answer,
        bad=lambda ctx: final(stmt("The stored score is 99.9.", "CALCULATED", ("cand-a/score",))),
    )
    assert result.status == "answered" and result.state.retried
    assert result.state.validation_attempts[0].errors[0].rule == "number_not_grounded"


def test_an_unsupported_site_fact_is_rejected_then_corrected(context):
    result = _answer_after(
        context,
        call("get_site", candidate_id="cand-a"),
        good=_score_answer,
        bad=lambda ctx: final(
            stmt(
                f"The site cand-ffffffffffff has score {seen(ctx)['cand-a/score'].display}.",
                "CALCULATED",
                ("cand-a/score",),
            )
        ),
    )
    assert result.status == "answered"
    assert "unknown_site_reference" in {e.rule for e in result.state.validation_attempts[0].errors}


def test_a_knowledge_claim_needs_a_verbatim_quotation(context):
    def with_quote(ctx):
        chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
        return final(
            stmt(
                "The documentation describes this.",
                "RETRIEVED_FACT",
                (chunk.id,),
                (quote_from(chunk),),
            )
        )

    def without_quote(ctx):
        chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
        return final(stmt("The documentation describes this.", "RETRIEVED_FACT", (chunk.id,)))

    result = _answer_after(
        context,
        call("search_knowledge", query="how confidence is determined"),
        with_quote,
        without_quote,
    )
    assert result.status == "answered"
    assert result.state.validation_attempts[0].errors[0].rule == "knowledge_quote_missing"


def test_a_methodology_answer_with_knowledge_evidence_passes(context):
    def model(ctx):
        if not ctx.transcript:
            return call("search_knowledge", query="why network optimization instead of top 30")
        chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
        return final(
            stmt(
                "The documentation explains this choice.",
                "RETRIEVED_FACT",
                (chunk.id,),
                (quote_from(chunk, 5),),
            )
        )

    result = run(context, reactive(model))
    assert result.status == "answered" and not result.state.retried


def test_a_mixed_answer_cites_structured_numbers_and_quotes_the_methodology(context):
    def model(ctx):
        if not ctx.transcript:
            return call("network_summary")
        if len(ctx.transcript) == 1:
            return call("search_knowledge", query="MCLP maximum coverage")
        records = seen(ctx)
        share = records["network/mclp/population_covered_share"]
        chunk = next(r for r in records.values() if r.id.startswith("kb/"))
        return final(
            stmt(
                f"The optimized network covers {share.display} of the modelled population.",
                "CALCULATED",
                (share.id,),
            ),
            stmt(
                "The documentation explains the method.",
                "RETRIEVED_FACT",
                (chunk.id,),
                (quote_from(chunk, 5),),
            ),
        )

    result = run(context, reactive(model))
    assert result.status == "answered"
    assert (result.state.tool_calls, result.state.retrieval_calls) == (1, 1)


def test_unknown_information_stays_unknown(context):
    result = run(
        context,
        script(
            call("get_site", candidate_id="cand-nope"),
            final(stmt("That site is not available in the data.")),
        ),
    )
    assert result.status == "answered" and result.state.observations[0].error is not None
    assert result.answer.direct_answer[0].kind == "UNKNOWN"


# --- Fallback ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "changes", "termination"),
    [
        ([call("get_site", candidate_id="cand-a")] * 3, {"max_iterations": 2}, "iteration_limit"),
        (
            [call("get_site", candidate_id="cand-a"), call("get_site", candidate_id="cand-b")],
            {"max_tool_calls": 1},
            "tool_call_limit",
        ),
        (
            [call("search_knowledge", query="a"), call("search_knowledge", query="b")],
            {"max_retrieval_calls": 1},
            "retrieval_call_limit",
        ),
        (
            [call("nope"), call("get_site")],
            {"max_recoverable_errors": 1},
            "recoverable_error_limit",
        ),
        ([ModelStep()], {}, "malformed_provider_response"),
        ([], {}, "provider_error"),
    ],
    ids=["iterations", "tools", "retrieval", "errors", "malformed", "provider"],
)
def test_every_ending_but_an_answer_is_the_labelled_deterministic_fallback(
    context, steps, changes, termination
):
    result = run(context, script(*steps), **changes)
    assert (result.status, result.termination) == ("fallback", termination)
    assert result.answer is None and result.fallback is not None
    assert result.fallback.label == "no AI summary" and result.fallback.reason
    assert result.state.fallback == result.fallback and result.state.termination == termination


def test_the_fallback_holds_the_records_the_run_gathered_and_no_prose(context):
    model = script(
        call("get_site", candidate_id="cand-a"),
        call("search_knowledge", query="confidence"),
        call("network_summary"),
        call("get_site", candidate_id="cand-b"),
    )
    result = run(context, model, max_tool_calls=2)
    ids = {r.id for r in result.fallback.records}
    assert "cand-a/score" in ids and "network/mclp/sites" in ids
    assert "cand-b/score" not in ids  # the refused third tool call was not run
    assert any(i.startswith("kb/") for i in ids)
    assert set(result.fallback.model_dump()) == {"label", "reason", "records"}


def test_an_unexpected_tool_failure_is_logged_and_ends_the_run_without_crashing(context, caplog):
    tool = AGENT_REGISTRY["get_site"]

    def broken(ctx, args):
        raise KeyError("a bug")

    registry = {**AGENT_REGISTRY, "get_site": dataclasses.replace(tool, call=broken)}
    with caplog.at_level(logging.ERROR, logger="sitescout.agent.loop"):
        result = run_agent(
            context, "q", script(call("get_site", candidate_id="cand-a")), limits(), registry
        )
    assert (result.status, result.termination) == ("fallback", "tool_failure")
    assert "KeyError" in result.fallback.reason
    assert "failed unexpectedly" in caplog.text


# --- Determinism, isolation -------------------------------------------------------------------


def test_the_same_script_gives_the_same_trajectory_and_result(context):
    def steps():
        return script(
            call("get_site", candidate_id="cand-a"),
            call("search_knowledge", query="score"),
            call("get_site", candidate_id="cand-a"),
            unknown_answer(),
        )

    first, second = run(context, steps()), run(context, steps())
    assert first == second and first.model_dump_json() == second.model_dump_json()


def test_a_run_writes_nothing(context, processed):
    def snapshot():
        return {
            p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(processed.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    run(
        context,
        script(call("network_summary"), call("get_site", candidate_id="cand-a"), unknown_answer()),
    )
    assert snapshot() == before


# --- Which budget a call spends -----------------------------------------------------------

DETERMINISTIC_CALLS = [
    ("find_sites", {"candidate_id": "cand-a"}),
    ("get_site", {"candidate_id": "cand-a"}),
    ("compare_sites", {"a": "cand-a", "b": "cand-b"}),
    ("explain_score", {"candidate_id": "cand-a"}),
    ("network_contribution", {"candidate_id": "cand-a"}),
    ("generate_brief", {"candidate_id": "cand-a"}),
    ("network_summary", {}),
    ("nearby_sites", {"candidate_id": "cand-a", "radius_m": 1}),
]


@pytest.mark.parametrize(
    ("tool", "arguments"), DETERMINISTIC_CALLS, ids=[t for t, _ in DETERMINISTIC_CALLS]
)
def test_each_of_the_eight_deterministic_tools_spends_one_tool_call_and_no_retrieval_call(
    context, tool, arguments
):
    state = run(context, script(call(tool, **arguments), unknown_answer())).state
    assert (state.tool_calls, state.retrieval_calls) == (1, 0)


def test_search_knowledge_spends_one_retrieval_call_and_no_tool_call(context):
    state = run(context, script(call("search_knowledge", query="score"), unknown_answer())).state
    assert (state.tool_calls, state.retrieval_calls) == (0, 1)


def test_searches_never_use_up_the_tool_budget(context):
    searches = [call("search_knowledge", query=q) for q in ("alpha", "beta", "gamma")]
    model = script(*searches, call("get_site", candidate_id="cand-a"), unknown_answer())
    result = run(context, model, max_tool_calls=1, max_retrieval_calls=3)
    assert result.status == "answered"
    assert (result.state.tool_calls, result.state.retrieval_calls) == (1, 3)


def test_deterministic_tools_never_use_up_the_retrieval_budget(context):
    radii = [call("nearby_sites", candidate_id="cand-a", radius_m=r) for r in (1, 2, 3)]
    model = script(*radii, call("search_knowledge", query="score"), unknown_answer())
    result = run(context, model, max_tool_calls=3, max_retrieval_calls=1)
    assert result.status == "answered"
    assert (result.state.tool_calls, result.state.retrieval_calls) == (3, 1)


def test_both_budgets_can_be_exactly_full_at_once_at_the_default_tool_and_retrieval_limits(context):
    radii = [call("nearby_sites", candidate_id="cand-a", radius_m=r) for r in range(1, 11)]
    searches = [call("search_knowledge", query=q) for q in ("alpha", "beta", "gamma")]
    result = run(context, script(*radii, *searches, unknown_answer()), max_iterations=14)
    assert (
        result.status == "answered"
    )  # ten tool calls and three searches: both budgets exactly full
    assert (result.state.tool_calls, result.state.retrieval_calls) == (10, 3)
    assert result.state.iterations == 14  # only the iteration limit was raised, to fit them
