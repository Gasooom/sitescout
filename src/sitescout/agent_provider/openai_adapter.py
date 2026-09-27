"""Milestone 10, Phase 5 (D-059): the OpenAI provider behind the agent loop.

Structurally this is ``sitescout.analyst.openai_provider`` again, one level up: see
``sitescout.agent_provider.anthropic_adapter`` for what differs from the M9 provider (the
tool count it will accept and the system prompt) and what does not (translating a reply into
a ``ModelStep``, which is M9's own :func:`to_step`, reused unchanged).

The ``openai`` package is the same optional extra M9 uses (``uv sync --extra analyst``); it
is imported only inside ``build_provider``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import SecretStr

from sitescout.agent_provider.common import check_agent_tools
from sitescout.agent_provider.prompt import build_system_prompt
from sitescout.analyst.credentials import read_openai_api_key
from sitescout.analyst.openai_provider import to_step  # reused unchanged (D-059)
from sitescout.analyst.provider import ModelContext, ModelStep, Provider
from sitescout.analyst.provider_common import (
    MAX_DIAGNOSTIC,
    ProviderError,
    ProviderUnavailable,
    question_text,
    redact,
    retry_note,
    tool_output,
)
from sitescout.config import AgentProviderSettings

log = logging.getLogger(__name__)

API_BASE_URL = "https://api.openai.com/v1"  # same as the M9 adapter; passed explicitly


def _call_id(index: int) -> str:
    return f"call_sitescout_agent_{index:02d}"


def build_request(
    context: ModelContext, settings: AgentProviderSettings, *, min_quote_words: int
) -> dict[str, Any]:
    """The Responses API arguments for one turn, built only from ``context`` and ``settings``."""
    check_agent_tools(context)
    tools = [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.arguments_schema,
            # Not strict: some agent tools (search_knowledge, find_sites) have optional
            # fields, which OpenAI's strict mode needs made required. The loop validates
            # every call's arguments anyway.
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
        "instructions": build_system_prompt(min_quote_words),
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


class AgentOpenAIProvider(Provider):
    """The real provider for the agent loop. ``client`` is an ``openai.OpenAI`` (or, in tests,
    any object with ``responses.create(**kwargs)``); build the real one with
    :func:`build_provider`."""

    def __init__(
        self,
        settings: AgentProviderSettings,
        client: Any,
        *,
        min_quote_words: int,
        secret: SecretStr | None = None,
    ) -> None:
        self.settings = settings
        self.min_quote_words = min_quote_words
        self._client = client
        self._secret = secret

    def __repr__(self) -> str:
        return f"AgentOpenAIProvider(model={self.settings.model!r})"

    def next_step(self, context: ModelContext) -> ModelStep:
        request = build_request(context, self.settings, min_quote_words=self.min_quote_words)
        try:
            response = self._client.responses.create(**request)
        except Exception as error:  # noqa: BLE001 - reported to the loop, which falls back
            summary = self._describe(error)
            log.warning("%s; provider message: %s", summary, self._diagnostic(error))
            raise ProviderError(summary) from None
        details = getattr(response, "incomplete_details", None)
        log.info(
            "Agent model turn: status=%s%s, output items=%d",
            getattr(response, "status", None),
            f" ({getattr(details, 'reason', details)})" if details else "",
            len(getattr(response, "output", None) or []),
        )
        return to_step(response)

    def _describe(self, error: Exception) -> str:
        status = getattr(error, "status_code", None)
        code = getattr(error, "code", None)
        text = f"{type(error).__name__}" + (f" (HTTP {status})" if status else "")
        if isinstance(code, str) and code:
            text += f", code {code}"
        return f"the OpenAI API call failed: {redact(text, self._secret)}"

    def _diagnostic(self, error: Exception) -> str:
        message = getattr(error, "message", None) or str(error)
        return redact(str(message), self._secret)[:MAX_DIAGNOSTIC]


def build_provider(settings: AgentProviderSettings, *, min_quote_words: int) -> AgentOpenAIProvider:
    """The configured OpenAI agent provider. Raises ``CredentialError`` if ``OPENAI_API_KEY``
    is missing and ``ProviderUnavailable`` if the optional ``openai`` package is not
    installed."""
    if settings.provider != "openai":  # the factory routes only "openai" here
        raise ProviderError(f"unsupported agent provider {settings.provider!r}")
    secret = read_openai_api_key()
    try:
        import openai
    except ImportError:
        raise ProviderUnavailable(
            "the optional analyst dependencies are not installed; run "
            "`uv sync --extra analyst` to use a real provider with the agent"
        ) from None
    client = openai.OpenAI(
        api_key=secret.get_secret_value(),
        base_url=API_BASE_URL,
        timeout=settings.timeout_s,
        max_retries=0,  # the loop's single answer retry is the only retry (D-058)
    )
    return AgentOpenAIProvider(settings, client, min_quote_words=min_quote_words, secret=secret)
