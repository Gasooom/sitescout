"""Milestone 9, phase 3b: the Anthropic provider behind the phase 3a ``Provider`` interface.

``AnthropicProvider.next_step`` turns one :class:`ModelContext` into one Messages API call
and the reply into one :class:`ModelStep`. It is stateless: every turn rebuilds the whole
conversation from the context (the question, each executed tool call with its result, and,
on the retry turn only, the validator's errors), so the loop in ``sitescout.analyst.run``
stays the only owner of state, of tool execution, of the one retry and of the fallback.

What it sends: a fixed system prompt (the rules and the ``Answer`` JSON schema), the
question as delimited data, and exactly the six tool definitions the loop supplies from
``REGISTRY``. It declares no other tool, so the model has no code execution, shell, file,
web or arbitrary function access. What it returns: a tool call exactly as the model asked
for it (the loop validates the name and the arguments), or the final answer as raw data
(the loop parses it against ``Answer`` and runs ``validate_answer``). It never checks,
fixes, retries or computes anything itself: output that is not a JSON object is passed
on as ``{"unparsed_output": ...}``, which fails the schema, so the loop's single retry
handles it.

The ``anthropic`` package is an optional extra (``uv sync --extra analyst``); it is
imported only inside ``build_provider``, so the rest of SiteScout, and every test, runs
without it.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import SecretStr

from sitescout.analyst.credentials import read_api_key
from sitescout.analyst.provider import ModelContext, ModelStep, Provider, ToolCallRequest
from sitescout.analyst.provider_common import (  # shared by every provider (D-054)
    MAX_OUTPUT_ECHO,
    SYSTEM_PROMPT,
    ProviderError,
    ProviderUnavailable,
    answer_json_from_text,
    check_tools,
    question_text,
    retry_note,
    tool_output,
)
from sitescout.config import AnalystSettings

__all__ = [
    "API_BASE_URL",
    "MAX_OUTPUT_ECHO",
    "SYSTEM_PROMPT",
    "AnthropicProvider",
    "ProviderError",
    "ProviderUnavailable",
    "build_provider",
    "build_request",
    "to_step",
]

log = logging.getLogger(__name__)

# Passed explicitly so the SDK never takes its endpoint from ANTHROPIC_BASE_URL.
API_BASE_URL = "https://api.anthropic.com"


def _tool_use_id(index: int) -> str:
    return f"toolu_sitescout_{index:02d}"


def build_request(context: ModelContext, settings: AnalystSettings) -> dict[str, Any]:
    """The Messages API arguments for one turn, built only from ``context`` and ``settings``."""
    check_tools(context)
    tools = [
        {"name": t.name, "description": t.description, "input_schema": t.arguments_schema}
        for t in context.tools
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": question_text(context.question)}]}
    ]
    for index, call in enumerate(context.transcript):
        tool_id = _tool_use_id(index)
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tool_id, "name": call.tool, "input": call.arguments}
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": tool_output(call.result),
                    }
                ],
            }
        )
    if context.validation_errors:
        messages[-1]["content"].append(
            {
                "type": "text",
                "text": retry_note(context.validation_errors, context.previous_answer_json),
            }
        )
    request: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "tools": tools,
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    }
    if settings.temperature is not None:
        # The SDK (1.8) has no temperature parameter any more: sent as a raw field, it takes
        # effect only for a model that still accepts it, and a rejection falls back.
        request["extra_body"] = {"temperature": settings.temperature}
    return request


def to_step(response: Any) -> ModelStep:
    """One Messages API reply as one ``ModelStep``: its first tool call, else its text."""
    blocks = list(getattr(response, "content", None) or [])
    for block in blocks:
        if getattr(block, "type", None) == "tool_use":
            arguments = getattr(block, "input", None)
            return ModelStep(
                tool_call=ToolCallRequest(
                    tool=str(getattr(block, "name", "")),
                    arguments=arguments if isinstance(arguments, dict) else {"input": arguments},
                )
            )
    text = "".join(
        getattr(block, "text", "") for block in blocks if getattr(block, "type", None) == "text"
    ).strip()
    return ModelStep(answer_json=answer_json_from_text(text))


class AnthropicProvider(Provider):
    """The real provider. ``client`` is an ``anthropic.Anthropic`` (or, in tests, any object
    with ``messages.create(**kwargs)``); build the real one with :func:`build_provider`."""

    def __init__(self, settings: AnalystSettings, client: Any, *, secret: SecretStr | None = None):
        self.settings = settings
        self._client = client
        self._secret = secret

    def __repr__(self) -> str:
        return f"AnthropicProvider(model={self.settings.model!r})"

    def next_step(self, context: ModelContext) -> ModelStep:
        request = build_request(context, self.settings)
        try:
            response = self._client.messages.create(**request)
        except Exception as error:  # noqa: BLE001 - reported to the loop, which falls back
            raise ProviderError(self._describe(error)) from None
        log.info(
            "Analyst model turn: stop_reason=%s, content blocks=%d",
            getattr(response, "stop_reason", None),
            len(getattr(response, "content", None) or []),
        )
        return to_step(response)

    def _describe(self, error: Exception) -> str:
        """A short, secret-free description: the error type and HTTP status, if any."""
        status = getattr(error, "status_code", None)
        text = f"{type(error).__name__}" + (f" (HTTP {status})" if status else "")
        if self._secret is not None:
            text = text.replace(self._secret.get_secret_value(), "**********")
        return f"the Anthropic API call failed: {text}"


def build_provider(settings: AnalystSettings) -> AnthropicProvider:
    """The configured provider. Raises ``CredentialError`` if the key is missing and
    ``ProviderUnavailable`` if the optional ``anthropic`` package is not installed."""
    if settings.provider != "anthropic":  # the factory routes only "anthropic" here
        raise ProviderError(f"unsupported analyst provider {settings.provider!r}")
    secret = read_api_key()
    try:
        import anthropic
    except ImportError:
        raise ProviderUnavailable(
            "the optional analyst dependencies are not installed; run "
            "`uv sync --extra analyst` to use the AI Site Analyst"
        ) from None
    client = anthropic.Anthropic(
        api_key=secret.get_secret_value(),
        base_url=API_BASE_URL,
        timeout=settings.timeout_s,
        max_retries=0,  # the loop's single answer retry is the only retry (D-052)
    )
    return AnthropicProvider(settings, client, secret=secret)
