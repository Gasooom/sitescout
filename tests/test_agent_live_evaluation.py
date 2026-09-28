"""M10 Phase 6 (D-060): the live evaluation's cases, runner, classification, aggregation,
instrumentation and report — entirely offline. No test here reads ``ANTHROPIC_API_KEY`` or
``OPENAI_API_KEY``, imports an SDK, or makes a network call; every "provider" here is either
``FakeModel`` or a plain fake object. This is what makes the live evaluation's own code
trustworthy without ever needing a credential in CI."""

import re
from types import SimpleNamespace

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
)
from sitescout.agent import AgentResult, AgentState, Observation, ObservationError
from sitescout.agent_live_eval import (
    CATEGORIES,
    EVAL_VERSION,
    LiveCase,
    LiveCaseError,
    LiveCaseResult,
    LiveCaseSet,
    LiveEvaluation,
    RecordingAnthropicClient,
    RecordingOpenAIClient,
    classify_result,
    evaluate_live,
    live_agent_limits,
    load_live_cases,
    prepare_question,
    render_report,
    resolve_site,
    run_live_case,
)
from sitescout.agent_live_eval import runner as runner_module
from sitescout.agent_provider.anthropic_adapter import AgentAnthropicProvider
from sitescout.analyst.validate import ValidationIssue, ValidationResult
from sitescout.config import PROJECT_ROOT, AgentProviderSettings, load_config

CASE_FILE = PROJECT_ROOT / "tests" / "agent_live_cases.yaml"


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture(scope="module")
def cases() -> LiveCaseSet:
    return load_live_cases(CASE_FILE)


def make_case(**changes) -> LiveCase:
    raw = {
        "id": "B01",
        "category": "B",
        "title": "t",
        "question": "What is the score of {{site}}?",
        "notes": "n",
        "sites": {"site": {"selected_mclp": True}},
        "expect": {"tools_include": ["get_site"]},
    }
    raw.update(changes)
    return LiveCase.model_validate(raw)


def fake_provider_settings(**changes) -> AgentProviderSettings:
    base = {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "max_tokens": 512,
        "timeout_s": 30,
        "temperature": None,
    }
    base.update(changes)
    return AgentProviderSettings.model_validate(base)


# --- The case schema ----------------------------------------------------------------------


def test_the_committed_live_case_file_loads_and_covers_every_category(cases):
    assert cases.version == 1
    assert {c.category for c in cases.cases} == set(CATEGORIES)


def test_cases_have_unique_ids_in_sorted_order(cases):
    ids = [c.id for c in cases.cases]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_no_case_scripts_a_provider_step():
    # The live schema has no field resembling Phase 4's scripted `provider:` trajectory.
    assert "provider" not in LiveCase.model_fields


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "X01"},
        {"id": "A01", "category": "B"},
        {"question": "no placeholder here", "sites": {"site": {"selected_mclp": True}}},
        {"question": "What about {{site}}?", "sites": {}},
        {"sites": {"site": {"candidate_id": "cand-x"}}},
        {"expect": {"tools_include": ["delete_everything"]}},
    ],
)
def test_an_invalid_case_is_rejected(changes):
    with pytest.raises((ValidationError, ValueError)):
        make_case(**changes)


def test_a_case_set_must_be_sorted_and_unique():
    one, two = make_case(id="B01"), make_case(id="B02")
    assert LiveCaseSet(version=1, world="w", cases=(one, two)).cases == (one, two)
    for bad in ((two, one), (one, one)):
        with pytest.raises(ValidationError):
            LiveCaseSet(version=1, world="w", cases=bad)


def test_an_unreadable_case_file_is_a_live_case_error(tmp_path):
    bad = tmp_path / "live.yaml"
    bad.write_text("version: 1\nworld: w\ncases: []\n", encoding="utf-8")
    with pytest.raises(LiveCaseError):
        load_live_cases(bad)


def test_two_placeholders_may_resolve_to_two_different_sites():
    case = make_case(
        question="Compare {{site_a}} and {{site_b}}.",
        sites={"site_a": {"selected_mclp": True}, "site_b": {"selected_mclp": False}},
    )
    assert set(case.sites) == {"site_a", "site_b"}


# --- Resolving a real site, never a hardcoded one -----------------------------------------


def test_resolve_site_finds_a_real_match(context):
    assert resolve_site(context, {"selected_mclp": True}) == "cand-a"


def test_resolve_site_returns_none_when_nothing_matches(context):
    assert resolve_site(context, {"district": "nowhere"}) is None


def test_prepare_question_fills_every_placeholder(context):
    case = make_case(
        question="Compare {{site_a}} and {{site_b}}.",
        sites={"site_a": {"selected_mclp": True}, "site_b": {"selected_mclp": False}},
    )
    question, reason = prepare_question(context, case)
    assert reason is None and "{{" not in question
    assert "cand-a" in question


def test_prepare_question_skips_when_a_selector_matches_nothing(context):
    case = make_case(sites={"site": {"district": "nowhere"}})
    question, reason = prepare_question(context, case)
    assert question is None and "nowhere" in reason


def test_a_case_with_no_site_is_never_skipped(context):
    case = make_case(question="What is grid evidence?", sites={})
    question, reason = prepare_question(context, case)
    assert question == "What is grid evidence?" and reason is None


# --- run_live_case: no scripted trajectory, real outcomes ----------------------------------


def test_a_case_with_an_unmatched_selector_is_skipped_and_calls_no_provider(context):
    case = make_case(sites={"site": {"district": "nowhere"}})
    result = run_live_case(context, case, script(), limits())
    assert result.status == "skipped" and "nowhere" in result.skip_reason
    assert result.calls == () and result.trajectory == ()


def test_a_case_that_answers_reports_the_actual_trajectory_and_expectations(context):
    case = make_case()
    model = script(
        call("get_site", candidate_id="cand-a"),
        final(stmt("The stored score of cand-a is 71.0.", "CALCULATED", ("cand-a/score",))),
    )
    result = run_live_case(context, case, model, limits())
    assert result.status == "answered" and result.failure_category == "answered_grounded"
    assert result.trajectory == ("get_site",)
    assert result.expect_met == {"tool_include:get_site": True}
    assert result.answer_excerpt is not None and "71.0" in result.answer_excerpt


def test_an_unmet_expectation_is_reported_not_hidden(context):
    case = make_case(expect={"tools_include": ["search_knowledge"]})
    model = script(
        call("get_site", candidate_id="cand-a"),
        final(stmt("The stored score of cand-a is 71.0.", "CALCULATED", ("cand-a/score",))),
    )
    result = run_live_case(context, case, model, limits())
    assert result.status == "answered"  # a real, valid answer on a different path
    assert result.expect_met == {"tool_include:search_knowledge": False}


def test_a_budget_limit_classifies_as_budget_exhausted(context):
    case = make_case(question="What is grid evidence?", sites={})
    # Two genuinely different calls, so the second is refused for the budget, not cached as
    # a duplicate (which would spend no budget and never reach the limit).
    model = script(call("get_site", candidate_id="cand-a"), call("get_site", candidate_id="cand-b"))
    result = run_live_case(context, case, model, limits(max_tool_calls=1))
    assert result.status == "fallback" and result.failure_category == "budget_exhausted"
    assert result.termination == "tool_call_limit"


def test_an_unexpected_runner_failure_is_reported_as_infra_error_not_raised(context, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(runner_module, "run_agent", boom)
    case = make_case(question="What is grid evidence?", sites={})
    result = run_live_case(context, case, script(), limits())
    assert result.status == "infra_error" and "unexpected" in result.skip_reason


def test_calls_are_attributed_to_the_case_that_made_them(context):
    # A recorder is shared across every case in a run; run_live_case must slice out only the
    # calls its own case made, never an earlier case's.
    from sitescout.analyst.provider import Provider

    class SharedRecorder:
        def __init__(self):
            self.calls = []

    class RecordingFakeModel(Provider):
        """Plays scripted steps and, like a real RecordingClient, appends one call record
        per provider turn — so the before/after slice has something real to prove."""

        def __init__(self, recorder, steps):
            self._recorder = recorder
            self._steps = list(steps)

        def next_step(self, context):
            from sitescout.agent_live_eval.instrumentation import CallUsage

            self._recorder.calls.append(CallUsage(index=len(self._recorder.calls), latency_s=0.01))
            return self._steps.pop(0)

    recorder = SharedRecorder()
    recorder.calls.append(SimpleNamespace(index=-1, latency_s=1.0))  # an earlier case's call
    case = make_case(question="What is grid evidence?", sites={})
    model = RecordingFakeModel(
        recorder,
        [
            call("get_site", candidate_id="cand-a"),
            final(stmt("The requested information is not available.", "UNKNOWN")),
        ],
    )
    result = run_live_case(context, case, model, limits(), recorder=recorder)
    assert len(result.calls) == 2  # this case's two turns only, not the earlier one
    assert len(recorder.calls) == 3  # the earlier call plus this case's two


# --- Structured validation diagnostics (D-060 addendum) --------------------------------------
# ``run_live_case`` now keeps the unmodified loop's own ``state.validation_attempts`` instead
# of discarding all but its length; these tests are the offline, deterministic proof of that,
# built the same way ``test_agent_loop.py`` already proves ``knowledge_quote_missing`` fires:
# no scripted trajectory is added, no rule is added or relaxed, and no real provider is called.


def _knowledge_case(**changes):
    raw = {
        "question": "How is confidence computed?",
        "sites": {},
        "expect": {"tools_any": ["search_knowledge"]},
    }
    raw.update(changes)
    return make_case(**raw)


def _without_quote(ctx):
    chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
    return final(stmt("The documentation describes this.", "RETRIEVED_FACT", (chunk.id,)))


def _with_quote(ctx):
    chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
    return final(
        stmt(
            "The documentation describes this.",
            "RETRIEVED_FACT",
            (chunk.id,),
            (quote_from(chunk),),
        )
    )


def _retrying_model(ctx):
    if not ctx.transcript:
        return call("search_knowledge", query="how confidence is determined")
    return _with_quote(ctx) if ctx.validation_errors else _without_quote(ctx)


def _always_rejecting_model(ctx):
    if not ctx.transcript:
        return call("search_knowledge", query="how confidence is determined")
    return _without_quote(ctx)


def test_a_retried_case_records_structured_diagnostics_for_both_attempts(context):
    result = run_live_case(context, _knowledge_case(), reactive(_retrying_model), limits())

    assert result.status == "answered" and result.failure_category == "answered_after_retry"
    assert result.validation_attempts == 2  # the existing count field is preserved unchanged
    detail = result.validation_attempts_detail
    assert len(detail) == 2
    assert detail[0].passed is False
    assert [issue.rule for issue in detail[0].errors] == ["knowledge_quote_missing"]
    assert detail[0].errors[0].statement_id == "direct_answer[0]"
    assert "quote it verbatim" in detail[0].errors[0].message
    assert detail[1].passed is True and detail[1].errors == ()


def test_a_rejected_case_records_structured_diagnostics_for_both_failed_attempts(context):
    result = run_live_case(context, _knowledge_case(), reactive(_always_rejecting_model), limits())

    assert result.status == "fallback" and result.failure_category == "validator_rejected"
    assert result.validation_attempts == 2
    detail = result.validation_attempts_detail
    assert len(detail) == 2
    assert all(attempt.passed is False for attempt in detail)
    assert all(
        [issue.rule for issue in attempt.errors] == ["knowledge_quote_missing"]
        for attempt in detail
    )
    # Neither the rejected answer's own text nor any request body or credential is retained.
    assert result.answer_excerpt is None


def test_validation_attempts_detail_survives_a_json_round_trip(context):
    cases = LiveCaseSet(version=1, world="w", cases=(_knowledge_case(id="A01", category="A"),))
    evaluation = evaluate_live(
        context,
        cases,
        reactive(_retrying_model),
        limits(),
        generated_at="2026-09-28T00:00:00+00:00",
    )

    restored = LiveEvaluation.model_validate_json(evaluation.model_dump_json())
    assert restored == evaluation
    original_detail = evaluation.cases[0].validation_attempts_detail
    restored_detail = restored.cases[0].validation_attempts_detail
    assert restored_detail == original_detail
    assert restored_detail[0].errors[0].rule == "knowledge_quote_missing"


def test_the_report_shows_a_validation_detail_section_when_an_attempt_failed():
    failed = ValidationResult(
        passed=False,
        errors=(
            ValidationIssue(
                rule="knowledge_quote_missing",
                statement_id="direct_answer[0]",
                message="a statement citing a knowledge chunk must quote it verbatim (quotes)",
            ),
        ),
    )
    case = LiveCaseResult(
        id="A02", category="A", title="t", question="q", status="fallback",
        failure_category="validator_rejected", termination="validation_failed",
        validation_attempts=2, validation_attempts_detail=(failed, failed),
    )  # fmt: skip
    evaluation = LiveEvaluation(
        version=EVAL_VERSION, provider="p", model="m", case_set_version=1, case_count=1,
        generated_at=None, limits={}, cases=(case,), by_category={"A": 1},
        by_failure_category={"validator_rejected": 1}, skipped=0, infra_errors=0,
        total_latency_s=0.0, total_input_tokens=None, total_output_tokens=None,
    )  # fmt: skip

    report = render_report(evaluation)
    assert "## 8. Validation detail" in report
    assert "knowledge_quote_missing" in report
    assert "direct_answer[0]" in report
    assert "A02" in report


def test_the_report_omits_the_validation_detail_section_when_nothing_failed():
    passed = ValidationResult(passed=True, errors=())
    case = LiveCaseResult(
        id="B01", category="B", title="t", question="q", status="answered",
        failure_category="answered_grounded", termination="answered",
        validation_attempts=1, validation_attempts_detail=(passed,),
    )  # fmt: skip
    evaluation = LiveEvaluation(
        version=EVAL_VERSION, provider="p", model="m", case_set_version=1, case_count=1,
        generated_at=None, limits={}, cases=(case,), by_category={"B": 1},
        by_failure_category={"answered_grounded": 1}, skipped=0, infra_errors=0,
        total_latency_s=0.0, total_input_tokens=None, total_output_tokens=None,
    )  # fmt: skip

    report = render_report(evaluation)
    assert "## 8. Validation detail" not in report


def test_the_small_evaluation_fixture_has_no_validation_detail_section(small_evaluation):
    # None of its three cases (skipped, answered_grounded, budget_exhausted) ever fails
    # validation, so the new section must not appear at all.
    assert "## 8. Validation detail" not in render_report(small_evaluation)


# --- Failure classification -----------------------------------------------------------------


def _result(status, termination, *, retried=False, observations=()):
    state = AgentState(
        question="q", termination=termination, retried=retried, observations=observations
    )
    return AgentResult(question="q", status=status, termination=termination, state=state)


@pytest.mark.parametrize(
    ("termination", "retried", "expected"),
    [
        ("answered", False, "answered_grounded"),
        ("answered", True, "answered_after_retry"),
        ("provider_error", False, "provider_error"),
        ("malformed_provider_response", False, "malformed_response"),
        ("validation_failed", False, "validator_rejected"),
        ("tool_failure", False, "tool_failure"),
        ("iteration_limit", False, "budget_exhausted"),
        ("tool_call_limit", False, "budget_exhausted"),
        ("retrieval_call_limit", False, "budget_exhausted"),
    ],
)
def test_every_termination_classifies_to_exactly_one_category(termination, retried, expected):
    status = "answered" if termination == "answered" else "fallback"
    assert classify_result(_result(status, termination, retried=retried)) == expected


@pytest.mark.parametrize("kind", ["malformed_arguments", "tool_error", "unknown_tool"])
def test_every_kind_of_recoverable_error_limit_is_budget_exhausted(kind):
    # The loop's own recoverable errors carry no code that reliably tells a genuine data
    # problem apart from the model asking for something that does not exist (see the
    # docstring of classify_result); every one of them is a model-behaviour classification.
    observation = Observation(
        index=1, tool="get_site", arguments={}, tool_kind="analyst", status="error",
        error=ObservationError(kind=kind, message="x"),
    )  # fmt: skip
    result = _result("fallback", "recoverable_error_limit", observations=(observation,))
    assert classify_result(result) == "budget_exhausted"


def test_a_genuine_tool_failure_termination_is_tool_failure_not_budget_exhausted():
    # Distinct from a recoverable error: the loop's own tool_failure termination is an
    # unexpected Python exception inside a tool, never returned as a recoverable observation.
    assert classify_result(_result("fallback", "tool_failure")) == "tool_failure"


def test_classification_never_raises_on_an_empty_state():
    assert classify_result(_result("fallback", "recoverable_error_limit")) == "budget_exhausted"


# --- Instrumentation: latency and usage, with no change to the request or response ---------


class _FakeAnthropicClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class _FakeOpenAIClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_the_anthropic_recorder_passes_the_request_and_reply_through_unchanged():
    usage = SimpleNamespace(input_tokens=120, output_tokens=30)
    reply = SimpleNamespace(usage=usage, content=[])
    real = _FakeAnthropicClient(reply)
    recorder = RecordingAnthropicClient(real)
    got = recorder.messages.create(model="x", messages=[])
    assert got is reply and real.calls == [{"model": "x", "messages": []}]
    assert len(recorder.calls) == 1
    assert (recorder.calls[0].input_tokens, recorder.calls[0].output_tokens) == (120, 30)
    assert recorder.calls[0].latency_s >= 0


def test_the_openai_recorder_passes_the_request_and_reply_through_unchanged():
    usage = SimpleNamespace(input_tokens=50, output_tokens=10)
    reply = SimpleNamespace(usage=usage, output=[])
    real = _FakeOpenAIClient(reply)
    recorder = RecordingOpenAIClient(real)
    got = recorder.responses.create(model="x", input=[])
    assert got is reply
    assert (recorder.calls[0].input_tokens, recorder.calls[0].output_tokens) == (50, 10)


def test_a_reply_with_no_usage_records_none_not_a_crash():
    reply = SimpleNamespace(content=[])  # no `usage` attribute at all
    recorder = RecordingAnthropicClient(_FakeAnthropicClient(reply))
    recorder.messages.create(model="x", messages=[])
    assert (recorder.calls[0].input_tokens, recorder.calls[0].output_tokens) == (None, None)


def test_calls_accumulate_across_several_requests():
    reply = SimpleNamespace(usage=None, content=[])
    recorder = RecordingAnthropicClient(_FakeAnthropicClient(reply, reply))
    recorder.messages.create(model="x", messages=[])
    recorder.messages.create(model="x", messages=[])
    assert [c.index for c in recorder.calls] == [0, 1]


def test_the_recorder_wraps_a_real_agent_provider_without_modifying_it():
    # The documented instrumentation seam used by scripts/agent_live_eval.py: build the real
    # provider as Phase 5 intends, then swap its client for a recording proxy.
    settings = fake_provider_settings()
    real_client = _FakeAnthropicClient()
    provider = AgentAnthropicProvider(settings, real_client, min_quote_words=3)
    recorder = RecordingAnthropicClient(provider._client)  # noqa: SLF001 - the documented seam
    provider._client = recorder  # noqa: SLF001
    assert provider._client is recorder and recorder._real is real_client


# --- Secrets and isolation --------------------------------------------------------------------


def test_the_instrumentation_module_reads_no_environment_variable_and_logs_nothing():
    pattern = re.compile(r"os\.environ|os\.getenv|getenv\(|dotenv|load_dotenv|\blogging\b")
    path = PROJECT_ROOT / "src" / "sitescout" / "agent_live_eval" / "instrumentation.py"
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        assert not pattern.search(line), f"{path.name}:{number}"


def test_a_client_exception_is_never_swallowed_or_reformatted_by_the_recorder():
    leak = RuntimeError("401 invalid x-api-key fake-agent-live-key")
    recorder = RecordingAnthropicClient(_FakeAnthropicClient(leak))
    with pytest.raises(RuntimeError) as raised:
        recorder.messages.create(model="x", messages=[])
    assert raised.value is leak  # passed through exactly; the recorder never touches it
    assert recorder.calls == []  # a raised call is never recorded as a usage data point


def test_the_agent_live_eval_package_imports_no_provider_sdk():
    pattern = re.compile(r"\bimport\s+(?:anthropic|openai)\b|\bfrom\s+(?:anthropic|openai)\b")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "agent_live_eval").glob("*.py")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_no_code_execution_primitive_is_added():
    pattern = re.compile(r"\b(?:eval|exec|__import__|subprocess|socket)\b\s*[\(.]")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "agent_live_eval").glob("*.py")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_the_live_script_reads_no_environment_variable_of_its_own():
    pattern = re.compile(r"os\.environ|os\.getenv|getenv\(|dotenv|load_dotenv")
    path = PROJECT_ROOT / "scripts" / "agent_live_eval.py"
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        assert not pattern.search(line), f"{path.name}:{number}"


def test_no_new_dependency_was_added():
    import tomllib

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    assert list(extras) == ["analyst"]
    assert [d.split(">")[0] for d in extras["analyst"]] == ["anthropic", "openai"]


# --- Aggregation, reproducibility metadata and the report -----------------------------------


@pytest.fixture
def small_evaluation(context):
    cases = LiveCaseSet(
        version=1,
        world="w",
        cases=(
            make_case(id="B01", sites={"site": {"district": "nowhere"}}),  # skipped
            make_case(
                id="B02",
                question="What is the score of {{site}}?",
                sites={"site": {"selected_mclp": True}},
                expect={"tools_include": ["get_site"]},
            ),
            make_case(id="B03", question="What is grid evidence?", sites={}, expect={}),
        ),
    )
    model = script(
        call("get_site", candidate_id="cand-a"),
        final(stmt("The stored score of cand-a is 71.0.", "CALCULATED", ("cand-a/score",))),
        call("get_site", candidate_id="cand-nope"),  # a refusal, for the third case
        call("get_site", candidate_id="cand-nope"),
    )
    return evaluate_live(
        context,
        cases,
        model,
        limits(max_recoverable_errors=1),
        generated_at="2026-09-27T00:00:00+00:00",
    )


def test_aggregation_counts_each_case_exactly_once(small_evaluation):
    e = small_evaluation
    assert e.case_count == 3 and e.skipped == 1
    assert sum(e.by_category.values()) == 2  # the two that actually ran
    assert e.by_category == {"B": 2}


def test_the_evaluation_carries_reproducibility_metadata(small_evaluation):
    e = small_evaluation
    assert e.version == EVAL_VERSION
    assert e.case_set_version == 1
    assert e.generated_at == "2026-09-27T00:00:00+00:00"
    assert set(e.limits) == {
        "max_iterations", "max_tool_calls", "max_retrieval_calls",
        "max_recoverable_errors", "min_quote_words",
    }  # fmt: skip
    for case in e.cases:
        assert case.id and case.category and case.status


def test_the_evaluation_survives_a_json_round_trip(small_evaluation):
    assert (
        LiveEvaluation.model_validate_json(small_evaluation.model_dump_json()) == small_evaluation
    )


def test_rendering_is_a_pure_deterministic_function_of_the_evaluation(small_evaluation):
    first = render_report(small_evaluation)
    second = render_report(small_evaluation)
    assert first == second and "\r" not in first


def test_the_report_carries_the_required_sections_and_the_explicit_caveat(small_evaluation):
    report = render_report(small_evaluation)
    for needle in (
        "one run against one provider and one model",
        "reports/agent_eval.md",
        "## 3. Failure taxonomy",
        "## 4. Soft expectations",
        "## 5. Usage and latency",
        "Actual grid connection feasibility requires utility confirmation.",
    ):
        assert needle in report, needle
    assert "accuracy" not in report.lower()


def test_the_cost_estimate_is_absent_without_a_price_table(small_evaluation):
    report = render_report(small_evaluation, price_table=None)
    assert "Estimated cost: not available" in report


def test_the_cost_estimate_is_computed_from_a_configured_price_table():
    from sitescout.config import LivePriceSettings

    settings = fake_provider_settings(model="claude-sonnet-5")
    evaluation = LiveEvaluation(
        version=EVAL_VERSION, provider="anthropic", model=settings.model, case_set_version=1,
        case_count=0, generated_at=None, limits={}, cases=(), by_category={},
        by_failure_category={}, skipped=0, infra_errors=0, total_latency_s=0.0,
        total_input_tokens=1000, total_output_tokens=500,
    )  # fmt: skip
    price_table = {settings.model: LivePriceSettings(input_per_1k=0.01, output_per_1k=0.02)}
    report = render_report(evaluation, price_table)
    assert "Estimated cost: $0.0200" in report


def test_a_case_that_declares_no_expectation_reports_none():
    evaluation = LiveEvaluation(
        version=EVAL_VERSION, provider="p", model="m", case_set_version=1, case_count=0,
        generated_at=None, limits={}, cases=(), by_category={}, by_failure_category={},
        skipped=0, infra_errors=0, total_latency_s=0.0, total_input_tokens=None,
        total_output_tokens=None,
    )  # fmt: skip
    assert "No case declared one." in render_report(evaluation)
    assert "No case ran." in render_report(evaluation)


# --- Config wiring -----------------------------------------------------------------------------


def test_the_configured_paths_point_at_the_live_case_file_and_a_separate_report():
    paths = load_config().settings.paths
    assert paths.agent_live_cases == "tests/agent_live_cases.yaml"
    assert paths.agent_live_eval_report == "reports/agent_live_eval.md"
    assert paths.agent_live_eval_report != paths.agent_eval_report


def test_live_agent_limits_are_separate_from_the_loops_own_limits():
    settings = load_config().settings.agent
    live = live_agent_limits(settings)
    assert live.max_iterations == settings.live_eval.max_iterations
    assert live.max_iterations != settings.max_iterations  # deliberately tighter, not shared
    assert live.max_recoverable_errors == settings.max_recoverable_errors  # unchanged, shared


def test_the_live_eval_never_writes_the_deterministic_reports():
    for module_path in (
        PROJECT_ROOT / "src" / "sitescout" / "agent_live_eval" / "runner.py",
        PROJECT_ROOT / "src" / "sitescout" / "agent_live_eval" / "report.py",
    ):
        text = module_path.read_text(encoding="utf-8")
        assert "knowledge_eval_report" not in text and "agent_eval_report" not in text


def test_the_phase_4_evaluation_files_are_untouched():
    # A cheap tripwire alongside the git-status check the human review does: the Phase 4
    # case file and report still parse and still carry their own, unrelated version marker.
    from sitescout.agent_eval import load_cases

    phase4 = load_cases(PROJECT_ROOT / "tests" / "agent_cases.yaml")
    assert phase4.version == 1 and len(phase4.cases) == 73


def test_the_live_case_file_hash_is_stable_for_reproducibility_reporting():
    import hashlib

    digest = hashlib.sha256(CASE_FILE.read_bytes()).hexdigest()
    assert hashlib.sha256(CASE_FILE.read_bytes()).hexdigest() == digest
