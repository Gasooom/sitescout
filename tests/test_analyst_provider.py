"""M9 phase 3b: config-driven Anthropic provider, the D-050 credential, and ``ask``.

No test calls the real Anthropic API. The provider is driven by a fake client (an object
with ``messages.create``); one test runs the real SDK, when the optional extra is
installed, through an in-memory HTTP transport, so no request leaves the machine. The only
"key" used anywhere is the obviously fake ``FAKE_KEY``.
"""

import json
import logging
import re
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from sitescout.analyst import (
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
from sitescout.analyst.anthropic_provider import (
    API_BASE_URL,
    SYSTEM_PROMPT,
    AnthropicProvider,
    ProviderError,
    ProviderUnavailable,
    build_provider,
    build_request,
    to_step,
)
from sitescout.analyst.ask import ask, render_result
from sitescout.analyst.credentials import API_KEY_VARIABLE, CredentialError, read_api_key
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.config import PROJECT_ROOT, build_config

FAKE_KEY = "fake-analyst-credential-for-offline-tests"


# --- Fixtures and fakes ------------------------------------------------------------------------


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
def settings(settings_data, weights_data):
    # Config only: tests of the request and the reply need no pipeline run (and no solver).
    # Pinned to anthropic regardless of the live YAML (this file is Anthropic-specific; D-054
    # lets config/settings.yaml name either provider).
    settings_data["analyst"].update(provider="anthropic", model="claude-sonnet-5")
    return build_config(settings_data, weights_data).settings.analyst


@pytest.fixture
def score(data):
    found = find_sites(data, FindQuery())
    site = next(s for s in found.sites if s.selected_mclp)
    return next(r for r in found.records if r.id == f"{site.candidate_id}/score")


def tool_use(name, arguments):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="toolu_x", name=name, input=arguments)],
    )


def text(body):
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=body)]
    )


def answer_text(statement_text, kind, ids):
    payload = {"direct_answer": [{"text": statement_text, "kind": kind, "evidence_ids": ids}]}
    return text(json.dumps(payload))


class FakeClient:
    """Records every ``messages.create`` call and replies from a script."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))  # a deep, JSON-only copy
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def context(**extra) -> ModelContext:
    return ModelContext(question="Why is cand-x not selected?", tools=TOOL_DEFINITIONS, **extra)


# --- The D-050 credential --------------------------------------------------------------------


def test_the_credential_reader_reads_only_anthropic_api_key(monkeypatch):
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    for other in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "API_KEY", "SITESCOUT_API_KEY"):
        monkeypatch.setenv(other, FAKE_KEY)
    with pytest.raises(CredentialError):
        read_api_key()
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    assert read_api_key().get_secret_value() == FAKE_KEY


def test_a_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv(API_KEY_VARIABLE, "   ")
    with pytest.raises(CredentialError, match="ANTHROPIC_API_KEY is not set"):
        read_api_key()


def test_the_key_is_masked_when_formatted(monkeypatch):
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    secret = read_api_key()
    assert FAKE_KEY not in str(secret) and FAKE_KEY not in repr(secret)
    assert FAKE_KEY not in f"{secret}"


def test_the_credential_function_is_the_only_environment_access():
    # D-050: exactly one place in SiteScout's own code reads the environment.
    pattern = re.compile(r"os\.environ|os\.getenv|getenv\(|dotenv|load_dotenv")
    hits = []
    sources = [*(PROJECT_ROOT / "src").rglob("*.py"), *(PROJECT_ROOT / "scripts").glob("*.py")]
    for path in sorted(sources):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append((path.relative_to(PROJECT_ROOT).as_posix(), number))
    assert [path for path, _ in hits] == ["src/sitescout/analyst/credentials.py"]


def test_no_code_execution_primitive_is_added_to_the_analyst():
    pattern = re.compile(r"\b(?:eval|exec|__import__|subprocess|socket)\b\s*[\(.]")
    for path in sorted((PROJECT_ROOT / "src" / "sitescout" / "analyst").glob("*.py")):
        code = path.read_text(encoding="utf-8")
        assert not pattern.search(code), path.name


# --- The request: six tools, the question as data, the retry errors -----------------------


def test_exactly_the_six_tools_are_exposed_and_nothing_else(settings):
    request = build_request(context(), settings)
    assert sorted(t["name"] for t in request["tools"]) == sorted(TOOLS)
    for tool in request["tools"]:
        assert set(tool) == {"name", "description", "input_schema"}  # no server/code tools
        assert tool["input_schema"]["type"] == "object"
    assert request["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_a_context_offering_another_tool_is_refused(settings):
    extra = ToolDefinition(name="run_python", description="x", arguments_schema={"type": "object"})
    with pytest.raises(ProviderError, match="exactly the six"):
        build_request(ModelContext(question="?", tools=(*TOOL_DEFINITIONS, extra)), settings)
    with pytest.raises(ProviderError, match="exactly the six"):
        build_request(ModelContext(question="?", tools=TOOL_DEFINITIONS[:5]), settings)


def test_settings_come_from_config(settings):
    request = build_request(context(), settings)
    assert request["model"] == settings.model
    assert request["max_tokens"] == settings.max_tokens
    assert settings.temperature is None  # the configured default: left out of the request
    assert "temperature" not in json.dumps(request) and "extra_body" not in request
    warm = settings.model_copy(update={"temperature": 0.2})
    assert build_request(context(), warm)["extra_body"] == {"temperature": 0.2}


def test_the_question_is_delimited_data_and_the_rules_hold_the_one_grid_disclaimer(settings):
    request = build_request(context(), settings)
    first = request["messages"][0]["content"][0]["text"]
    assert first == "<question>\nWhy is cand-x not selected?\n</question>"
    assert request["system"] == SYSTEM_PROMPT
    assert GRID_DISCLAIMER in SYSTEM_PROMPT
    assert (
        "never a grid connection decision" in SYSTEM_PROMPT
    )  # explanation, not a second disclaimer
    assert '"direct_answer"' in SYSTEM_PROMPT  # the Answer JSON schema is requested


def test_the_transcript_becomes_paired_tool_use_and_tool_result_turns(settings):
    calls = (
        ToolCallRecord(tool="find_sites", arguments={"rank": 1}, result={"count": 1}),
        ToolCallRecord(tool="get_site", arguments={"candidate_id": "cand-x"}, result={"a": 1}),
    )
    messages = build_request(context(transcript=calls), settings)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    for index, call in enumerate(calls):
        use = messages[1 + 2 * index]["content"][0]
        result = messages[2 + 2 * index]["content"][0]
        assert (use["type"], use["name"], use["input"]) == ("tool_use", call.tool, call.arguments)
        assert result["type"] == "tool_result" and result["tool_use_id"] == use["id"]
        assert json.loads(result["content"]) == call.result


def test_retry_errors_are_passed_through_on_the_retry_turn_only(settings):
    plain = build_request(context(), settings)["messages"]
    assert all("rejected" not in json.dumps(m) for m in plain)
    issue = ValidationIssue(
        rule="number_not_grounded",
        statement_id="direct_answer[0]",
        message="'82.5' is not in a cited display",
    )
    errors = (issue,)
    retry = build_request(context(validation_errors=errors), settings)["messages"]
    note = retry[-1]["content"][-1]["text"]
    assert "number_not_grounded" in note and "direct_answer[0]" in note and "'82.5'" in note
    assert "only retry" in note
    assert "Your previous answer, exactly as JSON" not in note  # none was given


def test_the_previous_answer_is_included_on_the_retry_turn(settings):
    issue = ValidationIssue(
        rule="number_not_grounded", statement_id="direct_answer[0]", message="x"
    )
    previous = {"direct_answer": [{"text": "x", "kind": "CALCULATED", "evidence_ids": ["a/score"]}]}
    retry = build_request(
        context(validation_errors=(issue,), previous_answer_json=previous), settings
    )["messages"]
    note = retry[-1]["content"][-1]["text"]
    assert "Your previous answer, exactly as JSON, was" in note
    assert json.dumps(previous, sort_keys=True) in note
    assert "Copy every statement whose index is not named above" in note


# --- The reply: translated, never checked or fixed by the provider -------------------------


def test_a_tool_use_reply_becomes_a_tool_call():
    step = to_step(tool_use("get_site", {"candidate_id": "cand-x"}))
    assert step.kind == "tool_call"
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "get_site",
        {"candidate_id": "cand-x"},
    )


def test_an_unknown_tool_name_is_passed_on_for_the_loop_to_refuse():
    step = to_step(tool_use("python", {"code": "import os"}))
    assert step.tool_call.tool == "python"  # the provider does not filter; the loop refuses


@pytest.mark.parametrize(
    "body",
    ['{"direct_answer": []}', '```json\n{"direct_answer": []}\n```'],
    ids=["json", "fenced-json"],
)
def test_a_json_reply_becomes_raw_answer_data(body):
    assert to_step(text(body)).answer_json == {"direct_answer": []}


@pytest.mark.parametrize(
    "body",
    ["The site is great.", "", "[1, 2]", '{"direct_answer": '],
    ids=["prose", "empty", "json-list", "truncated"],
)
def test_malformed_output_is_passed_on_as_unparsed_for_the_loops_retry(body):
    step = to_step(text(body))
    assert step.kind == "answer"
    assert step.answer_json == {"unparsed_output": body.strip()}


def test_one_next_step_is_exactly_one_api_call(settings):
    client = FakeClient(RuntimeError("boom"))
    provider = AnthropicProvider(settings, client)
    with pytest.raises(ProviderError):
        provider.next_step(context())
    assert len(client.calls) == 1  # no provider-level retry


def test_api_errors_never_carry_the_key(settings, caplog):
    leak = RuntimeError(f"401 invalid x-api-key {FAKE_KEY}")
    provider = AnthropicProvider(settings, FakeClient(leak), secret=read_key_like(FAKE_KEY))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as raised:
        provider.next_step(context())
    assert FAKE_KEY not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is not None
    assert "RuntimeError" in str(raised.value)
    assert FAKE_KEY not in caplog.text and FAKE_KEY not in repr(provider)


def read_key_like(value):
    from pydantic import SecretStr

    return SecretStr(value)


# --- Building the real provider ---------------------------------------------------------------


def test_a_missing_key_fails_before_the_sdk_is_touched(settings, monkeypatch):
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # importing it would fail
    with pytest.raises(CredentialError):
        build_provider(settings)


def test_a_missing_sdk_is_reported_without_the_key(settings, monkeypatch):
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ProviderUnavailable, match="uv sync --extra analyst") as raised:
        build_provider(settings)
    assert FAKE_KEY not in str(raised.value)


def test_the_client_gets_the_key_the_fixed_endpoint_and_no_sdk_retries(settings, monkeypatch):
    seen = {}
    fake_sdk = SimpleNamespace(Anthropic=lambda **kwargs: seen.update(kwargs) or FakeClient())
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.invalid")
    provider = build_provider(settings)
    assert seen == {
        "api_key": FAKE_KEY,
        "base_url": API_BASE_URL,  # explicit, so ANTHROPIC_BASE_URL is never consulted
        "timeout": settings.timeout_s,
        "max_retries": 0,
    }
    assert FAKE_KEY not in repr(provider)


# --- Through the phase 3a loop ------------------------------------------------------------


def test_the_provider_drives_the_loop_to_a_validated_answer(data, settings, score):
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        answer_text(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "What is the score?", AnthropicProvider(settings, client))
    assert result.status == "answered"
    assert len(client.calls) == 2
    second = client.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result"  # the tool output went back


def test_malformed_then_corrected_output_uses_the_loops_single_retry(data, settings, score):
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        text("The score is high."),  # not JSON: fails the schema
        answer_text(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", AnthropicProvider(settings, client))
    assert result.status == "answered" and result.retried
    retry_note = client.calls[2]["messages"][-1]["content"][-1]["text"]
    assert "malformed_answer" in retry_note


def test_two_rejected_answers_fall_back_with_no_third_call(data, settings, score):
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        answer_text("The score is 999.9.", "CALCULATED", [score.id]),
        answer_text("The score is 999.9.", "CALCULATED", [score.id]),
    )
    result = run_analyst(data, "?", AnthropicProvider(settings, client))
    assert result.status == "fallback" and len(client.calls) == 3
    assert result.fallback.label == "no AI summary"


def test_a_model_asking_for_code_execution_gets_the_fallback(data, settings):
    client = FakeClient(tool_use("code_execution", {"code": "print(1)"}))
    result = run_analyst(data, "?", AnthropicProvider(settings, client))
    assert result.status == "fallback" and "unknown tool" in result.fallback.reason


def test_the_key_never_reaches_the_model_the_results_or_the_logs(
    data, settings, score, monkeypatch, caplog
):
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        answer_text(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    provider = AnthropicProvider(settings, client, secret=read_api_key())
    with caplog.at_level(logging.DEBUG):
        result = run_analyst(data, "?", provider)
    assert FAKE_KEY not in json.dumps(client.calls)
    assert FAKE_KEY not in result.model_dump_json()
    assert FAKE_KEY not in render_result(result)
    assert FAKE_KEY not in caplog.text


# --- ask(): the configured provider, or the labelled fallback ------------------------------


def _with_anthropic(config):
    # ask() drives the real factory from config.settings.analyst.provider: pin it to
    # anthropic here regardless of the live YAML (D-054 lets it name either provider).
    analyst = config.settings.analyst.model_copy(
        update={"provider": "anthropic", "model": "claude-sonnet-5"}
    )
    settings = config.settings.model_copy(update={"analyst": analyst})
    return SimpleNamespace(settings=settings, resolve=config.resolve)


def test_ask_with_a_missing_key_is_the_labelled_fallback_and_reads_no_data(world, monkeypatch):
    config, _ = world
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)
    refuse = classmethod(lambda cls, c, p: pytest.fail("no data may be read without a key"))
    monkeypatch.setattr(AnalystData, "load", refuse)
    result = ask(_with_anthropic(config), "Why?")
    assert result.status == "fallback" and result.transcript == ()
    rendered = render_result(result)
    assert rendered.splitlines()[0] == "no AI summary"
    assert "ANTHROPIC_API_KEY is not set" in rendered


def test_ask_never_substitutes_another_provider(world, monkeypatch):
    config, _ = world
    monkeypatch.setenv(API_KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    result = ask(_with_anthropic(config), "Why?")
    assert result.status == "fallback" and "uv sync --extra analyst" in result.fallback.reason
    assert FAKE_KEY not in result.model_dump_json()


def test_ask_runs_the_loop_with_the_configured_provider(world, data, score, monkeypatch):
    config, _ = world
    monkeypatch.setattr(AnalystData, "load", classmethod(lambda cls, c, p: data))
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        answer_text(f"The score is {score.display}.", "CALCULATED", [score.id]),
    )
    result = ask(config, "?", factory=lambda s: AnthropicProvider(s, client))
    assert result.status == "answered"
    rendered = render_result(result)
    assert f"The score is {score.display}." in rendered and score.id in rendered


def test_the_fallback_render_lists_the_raw_records(data, settings, score):
    client = FakeClient(
        tool_use("get_site", {"candidate_id": score.id.split("/")[0]}),
        text("not json"),
        text("still not json"),
    )
    rendered = render_result(run_analyst(data, "?", AnthropicProvider(settings, client)))
    assert rendered.splitlines()[0] == "no AI summary"
    assert f"- {score.id}: " in rendered


# --- The optional dependency --------------------------------------------------------------


def test_the_sdk_is_an_optional_extra_only():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = " ".join(project["project"]["dependencies"]).lower()
    assert "anthropic" not in base
    extras = project["project"]["optional-dependencies"]
    assert list(extras) == ["analyst"]
    assert [d.split(">")[0] for d in extras["analyst"]] == ["anthropic", "openai"]  # D-054


def test_importing_the_analyst_and_its_provider_does_not_import_the_sdk():
    code = (
        "import sys, sitescout.analyst, sitescout.analyst.anthropic_provider, "
        "sitescout.analyst.ask; print('anthropic' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_the_real_sdk_offline_through_an_in_memory_transport(settings):
    # Skipped on the base install; runs with `uv sync --extra analyst`. No request leaves
    # the machine: the SDK's HTTP client is given an in-memory transport.
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    sent = []

    arguments = {"candidate_id": "cand-x"}
    block = {"type": "tool_use", "id": "toolu_1", "name": "get_site", "input": arguments}
    reply = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": settings.model,
        "content": [block], "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }  # fmt: skip

    def handler(request):
        sent.append((dict(request.headers), json.loads(request.content), str(request.url)))
        return httpx2.Response(200, json=reply)

    client = anthropic.Anthropic(
        api_key=FAKE_KEY,
        base_url=API_BASE_URL,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    warm = settings.model_copy(update={"temperature": 0.2})
    step = AnthropicProvider(warm, client).next_step(context())
    assert (step.tool_call.tool, step.tool_call.arguments) == (
        "get_site",
        {"candidate_id": "cand-x"},
    )
    headers, body, url = sent[0]
    assert url == f"{API_BASE_URL}/v1/messages"
    assert FAKE_KEY not in json.dumps(body)  # the key is only ever an auth header
    assert sorted(t["name"] for t in body["tools"]) == sorted(TOOLS)
    assert body["temperature"] == 0.2  # accepted by the SDK as a raw field
    assert headers["x-api-key"] == FAKE_KEY
