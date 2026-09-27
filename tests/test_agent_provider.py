"""M10 Phase 5 (D-059): a real provider driving the M10 agent loop through the existing seam.

No test here calls a real API: every provider is driven by a fake client (an object with
``messages.create`` or ``responses.create``), exactly as the M9 provider tests are. The only
"key" used anywhere is the obviously fake ``FAKE_KEY``. Nothing here modifies M9, Phase 1,
Phase 2, Phase 3 or the Phase 4 evaluation; it proves that the seam those phases already
built can carry a real provider, not that any model answers well.
"""

import json
import re
import sys
from types import SimpleNamespace

import pytest

from agent_support import (
    call,
    limits,
    make_context,
    script,
)
from sitescout.agent import AGENT_TOOL_DEFINITIONS, AGENT_TOOL_NAMES, run_agent
from sitescout.agent_provider import (
    build_configured_agent_provider,
    build_system_prompt,
    check_agent_tools,
)
from sitescout.agent_provider.anthropic_adapter import AgentAnthropicProvider
from sitescout.agent_provider.anthropic_adapter import build_provider as build_anthropic_agent
from sitescout.agent_provider.anthropic_adapter import build_request as anthropic_build_request
from sitescout.agent_provider.ask import ask
from sitescout.agent_provider.openai_adapter import AgentOpenAIProvider
from sitescout.agent_provider.openai_adapter import build_provider as build_openai_agent
from sitescout.agent_provider.openai_adapter import build_request as openai_build_request
from sitescout.analyst import TOOL_DEFINITIONS, TOOLS
from sitescout.analyst.anthropic_provider import SYSTEM_PROMPT as M9_ANTHROPIC_PROMPT
from sitescout.analyst.anthropic_provider import to_step as anthropic_to_step
from sitescout.analyst.credentials import (
    API_KEY_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    CredentialError,
    read_api_key,
    read_openai_api_key,
)
from sitescout.analyst.openai_provider import to_step as openai_to_step
from sitescout.analyst.provider import ModelContext, ToolCallRecord
from sitescout.analyst.provider_common import ProviderError, ProviderUnavailable, check_tools
from sitescout.config import PROJECT_ROOT, build_config

FAKE_KEY = "fake-agent-credential-for-offline-tests"


# --- Fixtures and fakes -------------------------------------------------------------------------


@pytest.fixture
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture
def anthropic_settings(settings_data, weights_data):
    settings_data["agent"]["model_provider"].update(provider="anthropic", model="claude-sonnet-5")
    return build_config(settings_data, weights_data).settings.agent


@pytest.fixture
def openai_settings(settings_data, weights_data):
    settings_data["agent"]["model_provider"].update(provider="openai", model="gpt-test-model")
    return build_config(settings_data, weights_data).settings.agent


@pytest.fixture
def score(context):
    from sitescout.analyst.tools import FindQuery, find_sites

    found = find_sites(context.investigation.analyst, FindQuery())
    site = next(s for s in found.sites if s.selected_mclp)
    return next(r for r in found.records if r.id == f"{site.candidate_id}/score")


def agent_context(**extra) -> ModelContext:
    return ModelContext(question="What supports cand-x?", tools=AGENT_TOOL_DEFINITIONS, **extra)


def anthropic_tool_use(name, arguments):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="toolu_x", name=name, input=arguments)],
    )


def anthropic_text(body):
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=body)]
    )


def agent_answer_json(*statements, quotes=None) -> dict:
    """One ``direct_answer`` statement's raw JSON, in the ``AgentAnswer`` shape (with quotes)."""
    quotes = quotes or [[] for _ in statements]
    return {
        "direct_answer": [
            {"text": text, "kind": kind, "evidence_ids": list(ids), "quotes": list(q)}
            for (text, kind, ids), q in zip(statements, quotes, strict=True)
        ]
    }


class FakeAnthropicClient:
    """Records every ``messages.create`` call and replies from a script (mirrors the M9 test)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def openai_function_call(name, arguments):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    item = SimpleNamespace(type="function_call", call_id="call_x", name=name, arguments=raw)
    return SimpleNamespace(status="completed", output=[item])


def openai_message(text):
    content = [SimpleNamespace(type="output_text", text=text)]
    return SimpleNamespace(
        status="completed", output=[SimpleNamespace(type="message", content=content)]
    )


class FakeOpenAIClient:
    """Records every ``responses.create`` call and replies from a script."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# --- The provider context: nine tools, deterministic, distinct from M9 --------------------------


def test_the_nine_agent_tools_are_exposed_in_registry_order(anthropic_settings):
    request = anthropic_build_request(
        agent_context(), anthropic_settings.model_provider, min_quote_words=3
    )
    assert [t["name"] for t in request["tools"]] == list(AGENT_TOOL_NAMES)
    assert len(AGENT_TOOL_NAMES) == 9
    for tool in request["tools"]:
        assert set(tool) == {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"


def test_the_openai_adapter_also_exposes_the_nine_tools_in_the_same_order(openai_settings):
    request = openai_build_request(
        agent_context(), openai_settings.model_provider, min_quote_words=3
    )
    assert [t["name"] for t in request["tools"]] == list(AGENT_TOOL_NAMES)
    for tool in request["tools"]:
        assert set(tool) == {"type", "name", "description", "parameters", "strict"}
        assert tool["strict"] is False


def test_the_schema_is_deterministic_given_the_same_registry(anthropic_settings):
    first = anthropic_build_request(
        agent_context(), anthropic_settings.model_provider, min_quote_words=3
    )
    second = anthropic_build_request(
        agent_context(), anthropic_settings.model_provider, min_quote_words=3
    )
    assert first["tools"] == second["tools"]


def test_a_context_offering_the_m9_six_tools_is_refused_by_the_agent_check(anthropic_settings):
    with pytest.raises(ProviderError, match="exactly the nine agent tools"):
        anthropic_build_request(
            ModelContext(question="?", tools=TOOL_DEFINITIONS),
            anthropic_settings.model_provider,
            min_quote_words=3,
        )
    with pytest.raises(ProviderError, match="exactly the nine agent tools"):
        check_agent_tools(ModelContext(question="?", tools=TOOL_DEFINITIONS))


def test_the_openai_adapter_refuses_the_m9_context_too(openai_settings):
    with pytest.raises(ProviderError, match="exactly the nine agent tools"):
        openai_build_request(
            ModelContext(question="?", tools=TOOL_DEFINITIONS),
            openai_settings.model_provider,
            min_quote_words=3,
        )


def test_the_m9_six_tool_registry_is_unaffected_by_the_agent_check():
    # M9's own check still wants exactly six, and still accepts exactly M9's own definitions:
    # neither check was touched to build the other.
    check_tools(ModelContext(question="?", tools=TOOL_DEFINITIONS))  # does not raise
    with pytest.raises(ProviderError, match="exactly the six"):
        check_tools(agent_context())
    assert sorted(TOOLS) == sorted(AGENT_TOOL_NAMES[: len(TOOLS)])


# --- The agent's own prompt, distinct from M9's --------------------------------------------------


def test_the_agent_prompt_is_not_the_m9_prompt():
    prompt = build_system_prompt(3)
    assert prompt != M9_ANTHROPIC_PROMPT
    assert '"quotes"' in prompt and '"quotes"' not in M9_ANTHROPIC_PROMPT


def test_the_agent_prompt_carries_the_needed_concepts():
    prompt = build_system_prompt(3)
    for needle in (
        "search_knowledge",
        "deterministic tool",
        "UNKNOWN",
        "cite",
        "never write or cite a site you have not seen",
        "Actual grid connection feasibility requires utility confirmation.",
    ):
        assert needle in prompt, needle
    assert "at least 3 words" in prompt


def test_the_prompt_scales_with_the_configured_minimum_quote_length():
    assert "at least 5 words" in build_system_prompt(5)
    assert "at least 2 words" in build_system_prompt(2)


def test_the_prompt_requests_the_agentanswer_schema_not_the_m9_answer_schema():
    schema = build_system_prompt(3)
    assert '"AgentStatement"' in schema or '"quotes"' in schema
    assert "direct_answer" in schema and "next_investigation" in schema


# --- Tool call conversion: reused unchanged from M9 ----------------------------------------------


def test_to_step_is_m9s_own_function_reused_not_duplicated():
    from sitescout.agent_provider.anthropic_adapter import to_step as agent_anthropic_to_step
    from sitescout.agent_provider.openai_adapter import to_step as agent_openai_to_step

    assert agent_anthropic_to_step is anthropic_to_step
    assert agent_openai_to_step is openai_to_step


def test_an_anthropic_tool_use_reply_becomes_a_tool_call():
    step = anthropic_to_step(anthropic_tool_use("get_site", {"candidate_id": "cand-x"}))
    assert step.kind == "tool_call"
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "get_site",
        {"candidate_id": "cand-x"},
    )


def test_an_openai_function_call_reply_becomes_a_tool_call():
    step = openai_to_step(openai_function_call("search_knowledge", {"query": "confidence"}))
    assert step.kind == "tool_call"
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "search_knowledge",
        {"query": "confidence"},
    )


def test_a_tool_call_missing_a_required_field_is_passed_on_unchanged():
    # The provider never validates arguments; the loop does. Proven directly here so the
    # adapters are not silently relied upon to catch what only the loop must catch.
    step = anthropic_to_step(anthropic_tool_use("compare_sites", {"a": "cand-a"}))
    assert step.tool_call.arguments == {"a": "cand-a"}  # "b" is missing, passed through as-is


def test_an_unexpected_response_type_is_passed_on_for_the_loop_to_refuse():
    # An OpenAI built-in tool SiteScout never declared (a genuinely unexpected item type).
    item = SimpleNamespace(type="web_search_call")
    response = SimpleNamespace(status="completed", output=[item])
    step = openai_to_step(response)
    assert step.kind == "tool_call" and step.tool_call.tool == "web_search_call"


# --- Final answer conversion ----------------------------------------------------------------------


def test_an_anthropic_json_reply_becomes_raw_agent_answer_data():
    payload = agent_answer_json(("x", "UNKNOWN", []))
    step = anthropic_to_step(anthropic_text(json.dumps(payload)))
    assert step.kind == "answer" and step.answer_json == payload


def test_malformed_output_is_passed_on_as_unparsed_for_the_loops_retry():
    step = anthropic_to_step(anthropic_text("not json"))
    assert step.answer_json == {"unparsed_output": "not json"}


# --- Observation round trip: tool output survives into the next request --------------------------


def test_the_transcript_becomes_paired_tool_use_and_tool_result_turns(anthropic_settings, context):
    result = run_agent(
        context, "What supports cand-a?", script(call("get_site", candidate_id="cand-a")), limits()
    )
    observation = result.state.observations[0]
    calls = (
        ToolCallRecord(
            tool="get_site",
            arguments={"candidate_id": "cand-a"},
            result=observation.as_model_result(),
        ),
    )
    messages = anthropic_build_request(
        agent_context(transcript=calls), anthropic_settings.model_provider, min_quote_words=3
    )["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    use, result_block = messages[1]["content"][0], messages[2]["content"][0]
    assert (use["type"], use["name"], use["input"]) == (
        "tool_use",
        "get_site",
        {"candidate_id": "cand-a"},
    )
    assert result_block["type"] == "tool_result"
    round_tripped = json.loads(result_block["content"])
    assert round_tripped == observation.as_model_result()
    assert any(r["id"] == "cand-a/score" for r in round_tripped["records"])  # record ids preserved


def test_retry_errors_and_the_previous_answer_are_passed_through_on_retry_only(anthropic_settings):
    from sitescout.analyst.validate import ValidationIssue

    plain = anthropic_build_request(
        agent_context(), anthropic_settings.model_provider, min_quote_words=3
    )
    assert all("rejected" not in json.dumps(m) for m in plain["messages"])
    issue = ValidationIssue(
        rule="knowledge_quote_missing", statement_id="direct_answer[0]", message="x"
    )
    previous = agent_answer_json(("x", "RETRIEVED_FACT", ["kb/readme/what-it-does-not-claim"]))
    retry = anthropic_build_request(
        agent_context(validation_errors=(issue,), previous_answer_json=previous),
        anthropic_settings.model_provider,
        min_quote_words=3,
    )["messages"]
    note = retry[-1]["content"][-1]["text"]
    assert "knowledge_quote_missing" in note and "direct_answer[0]" in note
    assert json.dumps(previous, sort_keys=True) in note


# --- Multiple turns: the real seam driving the loop to a validated answer ------------------------


def test_the_anthropic_adapter_drives_the_loop_through_a_tool_call_a_search_and_an_answer(
    context, anthropic_settings, score
):
    site = score.id.split("/")[0]
    client = FakeAnthropicClient(
        anthropic_tool_use("get_site", {"candidate_id": site}),
        anthropic_tool_use(
            "search_knowledge", {"query": "what it does not claim", "doc_type": "overview"}
        ),
        anthropic_text(
            json.dumps(
                agent_answer_json(
                    (f"The stored score of {site} is {score.display}.", "CALCULATED", [score.id]),
                    (
                        "The documentation lists what SiteScout does not claim.",
                        "RETRIEVED_FACT",
                        ["kb/readme/what-it-does-not-claim"],
                    ),
                    quotes=[[], ["never claims grid approval, transformer capacity"]],
                )
            )
        ),
    )
    provider = AgentAnthropicProvider(
        anthropic_settings.model_provider,
        client,
        min_quote_words=anthropic_settings.min_quote_words,
    )
    result = run_agent(context, "What supports this site?", provider, limits())
    assert result.status == "answered" and len(client.calls) == 3
    assert result.state.tool_calls == 1 and result.state.retrieval_calls == 1
    second_request_tools = [t["name"] for t in client.calls[0]["tools"]]
    assert second_request_tools == list(AGENT_TOOL_NAMES)


def test_the_openai_adapter_drives_the_loop_the_same_way(context, openai_settings, score):
    site = score.id.split("/")[0]
    client = FakeOpenAIClient(
        openai_function_call("get_site", {"candidate_id": site}),
        openai_message(
            json.dumps(
                agent_answer_json(
                    (f"The stored score of {site} is {score.display}.", "CALCULATED", [score.id])
                )
            )
        ),
    )
    provider = AgentOpenAIProvider(
        openai_settings.model_provider, client, min_quote_words=openai_settings.min_quote_words
    )
    result = run_agent(context, "What is the score?", provider, limits())
    assert result.status == "answered" and len(client.calls) == 2


def test_a_malformed_first_reply_uses_the_loops_single_retry(context, anthropic_settings, score):
    site = score.id.split("/")[0]
    client = FakeAnthropicClient(
        anthropic_tool_use("get_site", {"candidate_id": site}),
        anthropic_text("The score is high."),  # not JSON: fails the schema
        anthropic_text(
            json.dumps(
                agent_answer_json(
                    (f"The stored score of {site} is {score.display}.", "CALCULATED", [score.id])
                )
            )
        ),
    )
    provider = AgentAnthropicProvider(anthropic_settings.model_provider, client, min_quote_words=3)
    result = run_agent(context, "?", provider, limits())
    assert result.status == "answered" and result.state.retried
    assert len(client.calls) == 3


def test_two_rejected_answers_fall_back_with_no_third_answer_attempt(
    context, anthropic_settings, score
):
    site = score.id.split("/")[0]
    bad = anthropic_text(
        json.dumps(
            agent_answer_json((f"The stored score of {site} is 1.2.", "CALCULATED", [score.id]))
        )
    )
    client = FakeAnthropicClient(anthropic_tool_use("get_site", {"candidate_id": site}), bad, bad)
    provider = AgentAnthropicProvider(anthropic_settings.model_provider, client, min_quote_words=3)
    result = run_agent(context, "?", provider, limits())
    assert result.status == "fallback" and result.termination == "validation_failed"
    assert len(client.calls) == 3


def test_an_unknown_tool_call_is_a_recoverable_error_and_the_run_continues(
    context, anthropic_settings, score
):
    site = score.id.split("/")[0]
    client = FakeAnthropicClient(
        anthropic_tool_use("delete_site", {"candidate_id": site}),
        anthropic_tool_use("get_site", {"candidate_id": site}),
        anthropic_text(
            json.dumps(
                agent_answer_json(
                    (f"The stored score of {site} is {score.display}.", "CALCULATED", [score.id])
                )
            )
        ),
    )
    provider = AgentAnthropicProvider(anthropic_settings.model_provider, client, min_quote_words=3)
    result = run_agent(context, "?", provider, limits())
    assert result.status == "answered" and result.state.recoverable_errors == 1


# --- Provider errors: safe handling, never a loop crash -------------------------------------------


def test_one_next_step_is_exactly_one_api_call_on_a_client_exception(anthropic_settings):
    client = FakeAnthropicClient(RuntimeError("boom"))
    provider = AgentAnthropicProvider(anthropic_settings.model_provider, client, min_quote_words=3)
    with pytest.raises(ProviderError):
        provider.next_step(agent_context())
    assert len(client.calls) == 1


def test_a_provider_exception_ends_the_run_with_the_fallback_not_a_crash(
    context, anthropic_settings
):
    client = FakeAnthropicClient(RuntimeError("boom"))
    provider = AgentAnthropicProvider(anthropic_settings.model_provider, client, min_quote_words=3)
    result = run_agent(context, "?", provider, limits())
    assert result.status == "fallback" and result.termination == "provider_error"


def test_api_errors_never_carry_the_key(anthropic_settings):
    leak = RuntimeError(f"401 invalid x-api-key {FAKE_KEY}")
    client = FakeAnthropicClient(leak)
    provider = AgentAnthropicProvider(
        anthropic_settings.model_provider, client, min_quote_words=3, secret=read_key_like(FAKE_KEY)
    )
    with pytest.raises(ProviderError) as raised:
        provider.next_step(agent_context())
    assert FAKE_KEY not in str(raised.value) and FAKE_KEY not in repr(provider)


def read_key_like(value):
    from pydantic import SecretStr

    return SecretStr(value)


# --- Building the real provider: the same credential, no new dependency --------------------------


def test_a_missing_key_fails_before_the_sdk_is_touched(anthropic_settings, monkeypatch):
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(CredentialError):
        build_anthropic_agent(anthropic_settings.model_provider, min_quote_words=3)


def test_a_missing_sdk_is_reported_without_the_key(anthropic_settings, monkeypatch):
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ProviderUnavailable, match="uv sync --extra analyst") as raised:
        build_anthropic_agent(anthropic_settings.model_provider, min_quote_words=3)
    assert FAKE_KEY not in str(raised.value)


def test_the_openai_agent_provider_reads_the_openai_key(openai_settings, monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(CredentialError):
        build_openai_agent(openai_settings.model_provider, min_quote_words=3)


def test_the_credential_functions_are_reused_unchanged():
    # The agent adapters import M9's own credential readers; no second credential path exists.
    from sitescout.agent_provider import anthropic_adapter, openai_adapter

    assert anthropic_adapter.read_api_key is read_api_key
    assert openai_adapter.read_openai_api_key is read_openai_api_key


def test_the_client_gets_the_key_the_fixed_endpoint_and_no_sdk_retries(
    anthropic_settings, monkeypatch
):
    seen = {}
    fake_sdk = SimpleNamespace(
        Anthropic=lambda **kwargs: seen.update(kwargs) or FakeAnthropicClient()
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    provider = build_anthropic_agent(anthropic_settings.model_provider, min_quote_words=3)
    assert seen == {
        "api_key": FAKE_KEY,
        "base_url": "https://api.anthropic.com",
        "timeout": anthropic_settings.model_provider.timeout_s,
        "max_retries": 0,
    }
    assert FAKE_KEY not in repr(provider)


def test_the_factory_builds_only_the_configured_agent_provider(
    anthropic_settings, openai_settings, monkeypatch
):
    fake_anthropic = SimpleNamespace(Anthropic=lambda **kwargs: FakeAnthropicClient())
    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: FakeOpenAIClient())
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    anthropic_provider = build_configured_agent_provider(
        anthropic_settings.model_provider, min_quote_words=3
    )
    openai_provider = build_configured_agent_provider(
        openai_settings.model_provider, min_quote_words=3
    )
    assert isinstance(anthropic_provider, AgentAnthropicProvider)
    assert isinstance(openai_provider, AgentOpenAIProvider)


# --- ask(): the configured agent provider, or the labelled fallback -------------------------------


def _pinned_to_anthropic(context):
    # ask() drives the real factory from settings.agent.model_provider: pin it here
    # regardless of the live YAML, to create a genuine, controlled credential test.
    agent = context.investigation.analyst.config.settings.agent
    provider = agent.model_provider.model_copy(
        update={"provider": "anthropic", "model": "claude-sonnet-5"}
    )
    return agent.model_copy(update={"model_provider": provider})


def test_ask_with_a_missing_key_is_the_labelled_fallback_and_calls_no_tool(context, monkeypatch):
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    result = ask(context, "Why?", _pinned_to_anthropic(context))
    assert result.status == "fallback" and result.termination == "provider_error"
    assert result.state.observations == ()  # no tool was ever called
    assert "ANTHROPIC_API_KEY is not set" in result.fallback.reason


def test_ask_never_substitutes_another_provider(context, monkeypatch):
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    result = ask(context, "Why?", _pinned_to_anthropic(context))
    assert result.status == "fallback" and "uv sync --extra analyst" in result.fallback.reason
    assert FAKE_KEY not in result.model_dump_json()


def test_ask_runs_the_loop_with_the_configured_provider(context, score):
    site = score.id.split("/")[0]
    client = FakeAnthropicClient(
        anthropic_tool_use("get_site", {"candidate_id": site}),
        anthropic_text(
            json.dumps(
                agent_answer_json(
                    (f"The stored score of {site} is {score.display}.", "CALCULATED", [score.id])
                )
            )
        ),
    )
    settings = context.investigation.analyst.config.settings.agent
    result = ask(
        context,
        "?",
        settings,
        factory=lambda s: AgentAnthropicProvider(s.model_provider, client, min_quote_words=3),
    )
    assert result.status == "answered"
    assert score.display in result.answer.direct_answer[0].text


# --- Isolation: no SDK inside the agent package, no new dependency --------------------------------


def test_the_agent_package_imports_no_provider_sdk():
    pattern = re.compile(r"\bimport\s+(?:anthropic|openai)\b|\bfrom\s+(?:anthropic|openai)\b")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "agent").glob("*.py")):
        code = path.read_text(encoding="utf-8")
        assert not pattern.search(code), path.name


def test_no_code_execution_primitive_is_added_to_the_agent_provider():
    pattern = re.compile(r"\b(?:eval|exec|__import__|subprocess|socket)\b\s*[\(.]")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "agent_provider").glob("*.py")):
        code = path.read_text(encoding="utf-8")
        assert not pattern.search(code), path.name


def test_the_agent_provider_reads_no_environment_variable_of_its_own():
    pattern = re.compile(r"os\.environ|os\.getenv|getenv\(|dotenv|load_dotenv")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "agent_provider").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not pattern.search(line), f"{path.name}:{number}"


def test_no_new_sdk_dependency_was_added():
    import tomllib

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    assert list(extras) == ["analyst"]
    assert [d.split(">")[0] for d in extras["analyst"]] == ["anthropic", "openai"]


def test_fakemodel_and_the_phase_4_evaluation_are_unaffected_by_the_new_package():
    # A pure import check: agent_provider does not change what FakeModel-driven code imports.
    code = (
        "import sys, sitescout.agent, sitescout.agent_eval, sitescout.agent_provider; "
        "print('anthropic' in sys.modules, 'openai' in sys.modules)"
    )
    import subprocess

    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"
