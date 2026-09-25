"""Milestone 9 (D-054): the OpenAI provider behind the phase 3a ``Provider`` interface.

``OpenAIProvider.next_step`` turns one :class:`ModelContext` into one Responses API call
(``client.responses.create``) and the reply into one :class:`ModelStep`, exactly as the
Anthropic provider does for the Messages API. It is stateless and asks OpenAI not to store
the exchange (``store=False``): every turn rebuilds the input from the context (the
question, each executed tool call as a ``function_call`` item with its
``function_call_output``, and, on the retry turn only, the validator's errors), so the loop
in ``sitescout.analyst.run`` stays the only owner of state, tool execution, the single
retry and the fallback.

What it sends: the shared system prompt as ``instructions`` (``provider_common``: the rules
and the ``Answer`` JSON schema), the question as delimited data, and exactly the six
function tools the loop supplies from ``REGISTRY``, with parallel tool calls off. It
declares no built-in tool (no code interpreter, web search, file search, shell or computer
use). What it returns: a function call exactly as the model asked for it (the loop validates
the name and the arguments), or the final answer as raw data (the loop parses it against
``Answer`` and runs ``validate_answer``). A built-in tool call, if one ever appeared, is
passed on under its own type name, which the loop refuses as an unknown tool. Output that is
not a JSON object becomes ``{"unparsed_output": ...}``, which fails the schema, so the loop's
single retry handles it. The SDK's own retries are off.

The ``openai`` package is an optional extra (``uv sync --extra analyst``); it is imported
only inside ``build_provider``, so the rest of SiteScout, and every test, runs without it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import SecretStr

from sitescout.analyst.credentials import read_openai_api_key
from sitescout.analyst.provider import ModelContext, ModelStep, Provider, ToolCallRequest
from sitescout.analyst.provider_common import (
    MAX_DIAGNOSTIC,
    MAX_OUTPUT_ECHO,
    SYSTEM_PROMPT,
    ProviderError,
    ProviderUnavailable,
    answer_json_from_text,
    check_tools,
    question_text,
    redact,
    retry_note,
    tool_output,
)
from sitescout.config import AnalystSettings

log = logging.getLogger(__name__)

# Passed explicitly so the SDK never takes its endpoint from OPENAI_BASE_URL.
API_BASE_URL = "https://api.openai.com/v1"


def _call_id(index: int) -> str:
    return f"call_sitescout_{index:02d}"


def build_request(context: ModelContext, settings: AnalystSettings) -> dict[str, Any]:
    """The Responses API arguments for one turn, built only from ``context`` and ``settings``."""
    check_tools(context)
    tools = [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.arguments_schema,
            # Not strict: OpenAI's strict mode needs every field required, which the optional
            # find_sites filters are not. The loop validates every call's arguments anyway.
            "strict": False,
        }
        for t in context.tools
    ]
    items: list[dict[str, Any]] = [{"role": "user", "content": question_text(context.question)}]
    for index, call in enumerate(context.transcript):
        call_id = _call_id(index)
        items.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": call.tool,
                "arguments": json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
            }
        )
        items.append(
            {"type": "function_call_output", "call_id": call_id, "output": tool_output(call.result)}
        )
    if context.validation_errors:
        note = retry_note(context.validation_errors, context.previous_answer_json)
        items.append({"role": "user", "content": note})
    request: dict[str, Any] = {
        "model": settings.model,
        "instructions": SYSTEM_PROMPT,
        "input": items,
        "tools": tools,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "max_output_tokens": settings.max_tokens,
        "store": False,
    }
    if settings.temperature is not None:
        request["temperature"] = settings.temperature
    return request


def _arguments(raw: Any) -> dict[str, Any]:
    """A function call's arguments (a JSON string) as a dict; anything else is passed on in a
    shape the loop's argument validation refuses."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {"unparsed_arguments": str(raw)[:MAX_OUTPUT_ECHO]}
    return parsed if isinstance(parsed, dict) else {"input": parsed}


def to_step(response: Any) -> ModelStep:
    """One Responses API reply as one ``ModelStep``: its first tool call, else its text."""
    items = list(getattr(response, "output", None) or [])
    for item in items:
        kind = getattr(item, "type", None)
        if kind == "function_call":
            return ModelStep(
                tool_call=ToolCallRequest(
                    tool=str(getattr(item, "name", "")),
                    arguments=_arguments(getattr(item, "arguments", None)),
                )
            )
        if isinstance(kind, str) and kind.endswith("_call"):
            # A built-in tool SiteScout never declared: named as it is, so the loop refuses it.
            return ModelStep(tool_call=ToolCallRequest(tool=kind, arguments={}))
    parts = []
    for item in items:
        if getattr(item, "type", None) != "message":
            continue  # e.g. reasoning items carry no answer text
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                parts.append(getattr(part, "text", ""))
            elif getattr(part, "type", None) == "refusal":
                parts.append(getattr(part, "refusal", ""))
    return ModelStep(answer_json=answer_json_from_text("".join(parts).strip()))


class OpenAIProvider(Provider):
    """The OpenAI provider. ``client`` is an ``openai.OpenAI`` (or, in tests, any object with
    ``responses.create(**kwargs)``); build the real one with :func:`build_provider`."""

    def __init__(self, settings: AnalystSettings, client: Any, *, secret: SecretStr | None = None):
        self.settings = settings
        self._client = client
        self._secret = secret

    def __repr__(self) -> str:
        return f"OpenAIProvider(model={self.settings.model!r})"

    def next_step(self, context: ModelContext) -> ModelStep:
        request = build_request(context, self.settings)
        try:
            response = self._client.responses.create(**request)
        except Exception as error:  # noqa: BLE001 - reported to the loop, which falls back
            summary = self._describe(error)
            log.warning("%s; provider message: %s", summary, self._diagnostic(error))
            raise ProviderError(summary) from None
        details = getattr(response, "incomplete_details", None)
        log.info(
            "Analyst model turn: status=%s%s, output items=%d",
            getattr(response, "status", None),
            f" ({getattr(details, 'reason', details)})" if details else "",
            len(getattr(response, "output", None) or []),
        )
        return to_step(response)

    def _describe(self, error: Exception) -> str:
        """A short, secret-free description: the error type, HTTP status and API error code."""
        status = getattr(error, "status_code", None)
        code = getattr(error, "code", None)
        text = f"{type(error).__name__}" + (f" (HTTP {status})" if status else "")
        if isinstance(code, str) and code:
            text += f", code {code}"
        return f"the OpenAI API call failed: {redact(text, self._secret)}"

    def _diagnostic(self, error: Exception) -> str:
        """The API's own error message, redacted and shortened, for the log only."""
        message = getattr(error, "message", None) or str(error)
        return redact(str(message), self._secret)[:MAX_DIAGNOSTIC]


def build_provider(settings: AnalystSettings) -> OpenAIProvider:
    """The configured OpenAI provider. Raises ``CredentialError`` if ``OPENAI_API_KEY`` is
    missing and ``ProviderUnavailable`` if the optional ``openai`` package is not installed."""
    if settings.provider != "openai":  # the factory routes only "openai" here
        raise ProviderError(f"unsupported analyst provider {settings.provider!r}")
    secret = read_openai_api_key()
    try:
        import openai
    except ImportError:
        raise ProviderUnavailable(
            "the optional analyst dependencies are not installed; run "
            "`uv sync --extra analyst` to use the AI Site Analyst"
        ) from None
    client = openai.OpenAI(
        api_key=secret.get_secret_value(),
        base_url=API_BASE_URL,
        timeout=settings.timeout_s,
        max_retries=0,  # the loop's single answer retry is the only retry (D-052)
    )
    return OpenAIProvider(settings, client, secret=secret)
