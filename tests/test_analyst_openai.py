"""M9 (D-054): the OpenAI provider, its credential, the provider factory, and the loop.

No test calls the OpenAI API. The provider is driven by a fake client (an object with
``responses.create``); one test runs the real SDK, when the optional extra is installed,
through an in-memory HTTP transport, so no request leaves the machine. The only "key" used
is the obviously fake ``FAKE_KEY``.
"""

import importlib.util
import json
import logging
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sitescout.analyst import (
    REGISTRY,
    TOOL_DEFINITIONS,
    TOOLS,
    AnalystData,
    FindQuery,
    ModelContext,
    ToolCallRecord,
    ToolDefinition,
    ValidationIssue,
    find_sites,
    run_analyst,
)
from sitescout.analyst.anthropic_provider import SYSTEM_PROMPT as ANTHROPIC_PROMPT
from sitescout.analyst.anthropic_provider import AnthropicProvider
from sitescout.analyst.ask import ask, render_result
from sitescout.analyst.credentials import (
    API_KEY_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    CredentialError,
    read_openai_api_key,
)
from sitescout.analyst.factory import build_configured_provider
from sitescout.analyst.openai_provider import (
    API_BASE_URL,
    OpenAIProvider,
    build_provider,
    build_request,
    to_step,
)
from sitescout.analyst.provider_common import (
    SYSTEM_PROMPT,
    ProviderError,
    ProviderUnavailable,
    tool_output,
)
from sitescout.config import PROJECT_ROOT, build_config

FAKE_KEY = "fake-openai-credential-for-offline-tests"


# --- Fixtures and fakes ------------------------------------------------------------------------


@pytest.fixture
def settings(settings_data, weights_data):
    settings_data["analyst"].update(provider="openai", model="gpt-test-model")
    return build_config(settings_data, weights_data).settings.analyst


@pytest.fixture
def world(analyst_world):
    config, processed, _ = analyst_world
    return config, processed


@pytest.fixture
def data(world):
    return AnalystData.load(*world)


@pytest.fixture
def score(data):
    found = find_sites(data, FindQuery())
    site = next(s for s in found.sites if s.selected_mclp)
    return next(r for r in found.records if r.id == f"{site.candidate_id}/score")


def function_call(name, arguments):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    item = SimpleNamespace(type="function_call", call_id="call_x", name=name, arguments=raw)
    return SimpleNamespace(status="completed", output=[item])


def message(*parts):
    content = [
        SimpleNamespace(type="output_text", text=p)
        if isinstance(p, str)
        else SimpleNamespace(type="refusal", refusal=p["refusal"])
        for p in parts
    ]
    reasoning = SimpleNamespace(type="reasoning", summary=[])
    return SimpleNamespace(
        status="completed", output=[reasoning, SimpleNamespace(type="message", content=content)]
    )


def answer(text, kind, ids):
    return message(
        json.dumps({"direct_answer": [{"text": text, "kind": kind, "evidence_ids": ids}]})
    )


class FakeClient:
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


class FakeAPIError(Exception):
    """Shaped like the SDK's APIStatusError: a message, an HTTP status and an error code."""

    def __init__(self, message, status_code=400, code="model_not_found"):
        super().__init__(message)
        self.message, self.status_code, self.code = message, status_code, code


def context(**extra):
    return ModelContext(question="Why is cand-x not selected?", tools=TOOL_DEFINITIONS, **extra)


def site_of(record):
    return record.id.split("/")[0]


# --- The credential ---------------------------------------------------------------------------


def test_the_openai_reader_reads_only_openai_api_key(monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    for other in (API_KEY_VARIABLE, "OPENAI_ADMIN_KEY", "OPENAI_KEY", "SITESCOUT_OPENAI_KEY"):
        monkeypatch.setenv(other, FAKE_KEY)
    with pytest.raises(CredentialError, match="OPENAI_API_KEY is not set"):
        read_openai_api_key()
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    secret = read_openai_api_key()
    assert secret.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in str(secret) and FAKE_KEY not in repr(secret)


def test_a_blank_openai_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, "  ")
    with pytest.raises(CredentialError) as raised:
        read_openai_api_key()
    assert "  " not in str(raised.value).split("is not set")[0].removeprefix("OPENAI_API_KEY")


# --- The request: the shared rules, six function tools, the retry errors ------------------


def test_the_rules_are_shared_with_the_anthropic_provider():
    assert SYSTEM_PROMPT is ANTHROPIC_PROMPT


def test_exactly_the_six_tools_as_non_strict_functions(settings):
    request = build_request(context(), settings)
    assert [t["name"] for t in request["tools"]] == [t.name for t in TOOL_DEFINITIONS]
    assert sorted(t["name"] for t in request["tools"]) == sorted(TOOLS)
    for tool in request["tools"]:
        assert set(tool) == {"type", "name", "description", "parameters", "strict"}
        assert tool["type"] == "function" and tool["strict"] is False  # no built-in tools
        # The schema is the loop's own argument model, unloosened.
        assert tool["parameters"] == REGISTRY[tool["name"]].args_model.model_json_schema()
        assert tool["parameters"]["additionalProperties"] is False
    assert request["tool_choice"] == "auto" and request["parallel_tool_calls"] is False


def test_a_context_offering_another_tool_is_refused(settings):
    extra = ToolDefinition(name="run_python", description="x", arguments_schema={"type": "object"})
    with pytest.raises(ProviderError, match="exactly the six"):
        build_request(ModelContext(question="?", tools=(*TOOL_DEFINITIONS, extra)), settings)


def test_settings_come_from_config_and_nothing_is_stored(settings):
    request = build_request(context(), settings)
    assert request["model"] == "gpt-test-model"
    assert request["max_output_tokens"] == settings.max_tokens
    assert request["instructions"] == SYSTEM_PROMPT
    assert request["store"] is False
    assert "temperature" not in request  # null in YAML: left out
    warm = settings.model_copy(update={"temperature": 0.2})
    assert build_request(context(), warm)["temperature"] == 0.2


def test_the_question_is_delimited_data(settings):
    first = build_request(context(), settings)["input"][0]
    assert first == {
        "role": "user",
        "content": "<question>\nWhy is cand-x not selected?\n</question>",
    }


def test_the_transcript_becomes_paired_function_calls_and_outputs(settings):
    calls = (
        ToolCallRecord(tool="find_sites", arguments={"rank": 1}, result={"count": 1}),
        ToolCallRecord(tool="get_site", arguments={"candidate_id": "cand-x"}, result={"a": 1}),
    )
    items = build_request(context(transcript=calls), settings)["input"]
    assert [i.get("type", i.get("role")) for i in items] == [
        "user", "function_call", "function_call_output", "function_call", "function_call_output",
    ]  # fmt: skip
    for index, call in enumerate(calls):
        use, output = items[1 + 2 * index], items[2 + 2 * index]
        assert (use["name"], json.loads(use["arguments"])) == (call.tool, call.arguments)
        assert output["call_id"] == use["call_id"]
        assert output["output"] == tool_output(call.result)


def test_retry_errors_are_passed_on_the_retry_turn_only(settings):
    plain = build_request(context(), settings)["input"]
    assert all("rejected" not in json.dumps(i) for i in plain)
    issue = ValidationIssue(
        rule="number_not_grounded", statement_id="direct_answer[0]", message="'82.5' x"
    )
    retry = build_request(context(validation_errors=(issue,)), settings)["input"]
    note = retry[-1]
    assert note["role"] == "user"
    assert "number_not_grounded" in note["content"] and "direct_answer[0]" in note["content"]
    assert "only retry" in note["content"]
    assert "Your previous answer, exactly as JSON" not in note["content"]  # none was given


def test_the_previous_answer_is_included_on_the_retry_turn(settings):
    issue = ValidationIssue(
        rule="number_not_grounded", statement_id="direct_answer[0]", message="x"
    )
    previous = {"direct_answer": [{"text": "x", "kind": "CALCULATED", "evidence_ids": ["a/score"]}]}
    retry = build_request(
        context(validation_errors=(issue,), previous_answer_json=previous), settings
    )["input"]
    note = retry[-1]["content"]
    assert "Your previous answer, exactly as JSON, was" in note
    assert json.dumps(previous, sort_keys=True) in note
    assert "Copy every statement whose index is not named above" in note


# --- The reply: translated, never checked or fixed by the provider -------------------------


def test_a_function_call_becomes_a_tool_call():
    step = to_step(function_call("get_site", {"candidate_id": "cand-x"}))
    assert step.kind == "tool_call"
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "get_site",
        {"candidate_id": "cand-x"},
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"candidate_id": ', {"unparsed_arguments": '{"candidate_id": '}),
        ("[1, 2]", {"input": [1, 2]}),
    ],
    ids=["truncated-json", "not-an-object"],
)
def test_malformed_arguments_are_passed_on_for_the_loop_to_refuse(raw, expected):
    assert to_step(function_call("get_site", raw)).tool_call.arguments == expected


def test_a_built_in_tool_call_is_named_for_the_loop_to_refuse():
    item = SimpleNamespace(type="code_interpreter_call", code="import os")
    step = to_step(SimpleNamespace(output=[item]))
    assert step.tool_call.tool == "code_interpreter_call" and step.tool_call.arguments == {}


@pytest.mark.parametrize(
    "body", ['{"direct_answer": []}', '```json\n{"direct_answer": []}\n```'], ids=["json", "fenced"]
)
def test_a_json_message_becomes_raw_answer_data(body):
    assert to_step(message(body)).answer_json == {"direct_answer": []}


@pytest.mark.parametrize(
    ("reply", "text"),
    [
        (message("The site is great."), "The site is great."),
        (message({"refusal": "I can't help with that."}), "I can't help with that."),
        (SimpleNamespace(status="incomplete", output=[]), ""),
        (SimpleNamespace(output=None), ""),
    ],
    ids=["prose", "refusal", "no-output", "none"],
)
def test_malformed_or_empty_output_is_passed_on_for_the_loops_retry(reply, text):
    step = to_step(reply)
    assert step.kind == "answer" and step.answer_json == {"unparsed_output": text}


# --- Calls and errors ----------------------------------------------------------------------


def test_one_next_step_is_exactly_one_api_call(settings):
    client = FakeClient(RuntimeError("boom"))
    with pytest.raises(ProviderError):
        OpenAIProvider(settings, client).next_step(context())
    assert len(client.calls) == 1  # no provider-level retry


def test_api_errors_are_concise_and_never_carry_the_key(settings, caplog):
    from pydantic import SecretStr

    leak = FakeAPIError(
        f"Incorrect API key provided: {FAKE_KEY} and sk-proj-abc123****wxyz", 401, "invalid_api_key"
    )
    provider = OpenAIProvider(settings, FakeClient(leak), secret=SecretStr(FAKE_KEY))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as raised:
        provider.next_step(context())
    assert (
        str(raised.value)
        == "the OpenAI API call failed: FakeAPIError (HTTP 401), code invalid_api_key"
    )
    assert raised.value.__cause__ is None
    assert FAKE_KEY not in caplog.text and "sk-proj-abc123" not in caplog.text
    assert "Incorrect API key provided" in caplog.text  # the diagnostic is kept, redacted
    assert FAKE_KEY not in repr(provider)


def test_an_unknown_model_fails_visibly_with_the_api_code(settings):
    error = FakeAPIError("The model `gpt-test-model` does not exist", 404, "model_not_found")
    with pytest.raises(ProviderError, match="HTTP 404.*model_not_found"):
        OpenAIProvider(settings, FakeClient(error)).next_step(context())


# --- Building the provider, and the factory ---------------------------------------------------


def test_a_missing_key_fails_before_the_sdk_is_touched(settings, monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)  # another provider's key is never used
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(CredentialError, match="OPENAI_API_KEY"):
        build_provider(settings)


def test_a_missing_sdk_is_reported_without_the_key(settings, monkeypatch):
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ProviderUnavailable, match="uv sync --extra analyst") as raised:
        build_provider(settings)
    assert FAKE_KEY not in str(raised.value)


def test_the_client_gets_the_key_the_fixed_endpoint_and_no_sdk_retries(settings, monkeypatch):
    seen = {}
    fake_sdk = SimpleNamespace(OpenAI=lambda **kwargs: seen.update(kwargs) or FakeClient())
    monkeypatch.setitem(sys.modules, "openai", fake_sdk)
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid")
    provider = build_provider(settings)
    assert seen == {
        "api_key": FAKE_KEY,
        "base_url": API_BASE_URL,  # explicit, so OPENAI_BASE_URL is never consulted
        "timeout": settings.timeout_s,
        "max_retries": 0,
    }
    assert isinstance(provider, OpenAIProvider) and FAKE_KEY not in repr(provider)


def test_the_openai_builder_refuses_another_provider(settings):
    with pytest.raises(ProviderError, match="unsupported analyst provider 'anthropic'"):
        build_provider(settings.model_copy(update={"provider": "anthropic"}))


def test_the_factory_builds_exactly_the_configured_provider(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **k: FakeClient()))
    monkeypatch.setitem(
        sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **k: FakeClient())
    )
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    assert isinstance(build_configured_provider(settings), OpenAIProvider)
    claude = settings.model_copy(update={"provider": "anthropic", "model": "claude-test"})
    assert isinstance(build_configured_provider(claude), AnthropicProvider)


def test_the_factory_never_substitutes_a_provider(settings, monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    monkeypatch.setenv(
        API_KEY_VARIABLE, FAKE_KEY
    )  # Anthropic would be available, but is not configured
    with pytest.raises(CredentialError, match="OPENAI_API_KEY"):
        build_configured_provider(settings)


# --- Through the phase 3a loop ------------------------------------------------------------


def test_the_provider_drives_the_loop_to_a_validated_answer(data, settings, score):
    client = FakeClient(
        function_call("get_site", {"candidate_id": site_of(score)}),
        answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "What is the score?", OpenAIProvider(settings, client))
    assert result.status == "answered" and len(client.calls) == 2
    last = client.calls[1]["input"][-1]
    assert last["type"] == "function_call_output"  # the tool output went back to the model


def test_malformed_then_corrected_output_uses_the_loops_single_retry(data, settings, score):
    client = FakeClient(
        function_call("get_site", {"candidate_id": site_of(score)}),
        message("The score is high."),
        answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", OpenAIProvider(settings, client))
    assert result.status == "answered" and result.retried
    assert "malformed_answer" in client.calls[2]["input"][-1]["content"]


def test_two_rejected_answers_fall_back_with_no_third_call(data, settings, score):
    client = FakeClient(
        function_call("get_site", {"candidate_id": site_of(score)}),
        answer("The score is 999.9.", "CALCULATED", [score.id]),
        answer("The score is 999.9.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", OpenAIProvider(settings, client))
    assert result.status == "fallback" and len(client.calls) == 3
    assert result.fallback.label == "no AI summary"
    assert any(r.id == score.id for r in result.fallback.records)


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (SimpleNamespace(output=[SimpleNamespace(type="code_interpreter_call")]), "unknown tool"),
        (function_call("python", {"code": "print(1)"}), "unknown tool"),
        (function_call("get_site", '{"candidate_id": '), "invalid arguments"),
        (FakeAPIError("server error", 500, "server_error"), "the OpenAI API call failed"),
    ],
    ids=["built-in-tool", "unknown-function", "malformed-arguments", "api-error"],
)
def test_unsafe_or_failed_steps_end_in_the_fallback(data, settings, reply, reason):
    result = run_analyst(data, "?", OpenAIProvider(settings, FakeClient(reply)))
    assert result.status == "fallback" and reason in result.fallback.reason
    assert result.fallback.label == "no AI summary"


def test_the_key_never_reaches_the_model_the_results_or_the_logs(
    data, settings, score, monkeypatch, caplog
):
    monkeypatch.setenv(OPENAI_API_KEY_VARIABLE, FAKE_KEY)
    client = FakeClient(
        function_call("get_site", {"candidate_id": site_of(score)}),
        answer(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    provider = OpenAIProvider(settings, client, secret=read_openai_api_key())
    with caplog.at_level(logging.DEBUG):
        result = run_analyst(data, "?", provider)
    assert FAKE_KEY not in json.dumps(client.calls)
    assert FAKE_KEY not in result.model_dump_json() + render_result(result) + caplog.text


# --- ask() and the command line --------------------------------------------------------------


def test_ask_with_openai_and_no_key_is_the_labelled_fallback(world, monkeypatch):
    config, _ = world
    analyst = config.settings.analyst.model_copy(update={"provider": "openai", "model": "gpt-x"})
    settings = config.settings.model_copy(update={"analyst": analyst})
    openai_config = SimpleNamespace(settings=settings, resolve=config.resolve)
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    result = ask(openai_config, "Why?")
    assert result.status == "fallback" and "OPENAI_API_KEY is not set" in result.fallback.reason
    assert render_result(result).splitlines()[0] == "no AI summary"


def test_tools_only_mode_needs_no_credential(data, monkeypatch, capsys):
    path = PROJECT_ROOT / "scripts" / "ask.py"
    spec = importlib.util.spec_from_file_location("ask_tools_only", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv(OPENAI_API_KEY_VARIABLE, raising=False)
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    monkeypatch.setattr(module.AnalystData, "load", classmethod(lambda cls, c, p: data))
    site = find_sites(data, FindQuery()).sites[0].candidate_id
    monkeypatch.setattr("sys.argv", [str(path), "--tools-only", "get_site", site])
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        assert module.main() == 0
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    assert json.loads(capsys.readouterr().out)["candidate_id"] == site


# --- The optional dependency, and the real SDK offline ---------------------------------------


def test_importing_the_providers_and_the_factory_imports_no_sdk():
    code = (
        "import sys, sitescout.analyst, sitescout.analyst.openai_provider, "
        "sitescout.analyst.anthropic_provider, sitescout.analyst.factory, sitescout.analyst.ask; "
        "print(sorted(m for m in ('openai', 'anthropic') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_the_real_sdk_offline_through_an_in_memory_transport(settings):
    # Skipped on the base install; runs with `uv sync --extra analyst`. No request leaves
    # the machine: the SDK's HTTP client is given an in-memory transport.
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_site",
            "arguments": json.dumps({"candidate_id": "cand-x"}), "status": "completed"}  # fmt: skip
    reply = {"id": "resp_1", "object": "response", "created_at": 1, "model": settings.model,
             "status": "completed", "output": [{"type": "reasoning", "id": "rs_1"}, call],
             "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}  # fmt: skip
    sent = []

    def handler(request):
        sent.append((dict(request.headers), json.loads(request.content), str(request.url)))
        return httpx2.Response(200, json=reply)

    client = openai.OpenAI(
        api_key=FAKE_KEY,
        base_url=API_BASE_URL,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    warm = settings.model_copy(update={"temperature": 0.2})
    step = OpenAIProvider(warm, client).next_step(context())
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "get_site",
        {"candidate_id": "cand-x"},
    )
    headers, body, url = sent[0]
    assert url == f"{API_BASE_URL}/responses"
    assert headers["authorization"] == f"Bearer {FAKE_KEY}"  # the key is only ever an auth header
    assert FAKE_KEY not in json.dumps(body)
    assert sorted(t["name"] for t in body["tools"]) == sorted(TOOLS)
    assert body["temperature"] == 0.2 and body["store"] is False
