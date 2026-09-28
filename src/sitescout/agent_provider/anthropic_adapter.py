"""Milestone 10, Phase 5 (D-059): the Anthropic provider behind the agent loop.

Structurally this is ``sitescout.analyst.anthropic_provider`` again, one level up: a request
is rebuilt from the ``ModelContext`` every turn (stateless), and the reply becomes one
``ModelStep``. Two things differ from the M9 provider, and only two: the tool count it will
accept (nine, not six: :func:`check_agent_tools`) and the system prompt it sends (the agent's
own, D-059's :mod:`sitescout.agent_provider.prompt`, not M9's). Translating a reply into a
``ModelStep`` does not depend on which tools were offered, so :func:`to_step` is M9's own
function, reused unchanged; the loop that calls this provider is
``sitescout.agent.loop.run_agent``, unmodified.

The ``anthropic`` package is the same optional extra M9 uses (``uv sync --extra analyst``);
it is imported only inside ``build_provider``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import SecretStr

from sitescout.agent_provider.common import agent_retry_note, check_agent_tools
from sitescout.agent_provider.prompt import build_system_prompt
from sitescout.analyst.anthropic_provider import to_step  # reused unchanged (D-059)
from sitescout.analyst.credentials import read_api_key
from sitescout.analyst.provider import ModelContext, ModelStep, Provider
from sitescout.analyst.provider_common import (
    ProviderError,
    ProviderUnavailable,
    question_text,
    tool_output,
)
from sitescout.config import AgentProviderSettings

log = logging.getLogger(__name__)

API_BASE_URL = "https://api.anthropic.com"  # same as the M9 adapter; passed explicitly


def _tool_use_id(index: int) -> str:
    return f"toolu_sitescout_agent_{index:02d}"


def build_request(
    context: ModelContext, settings: AgentProviderSettings, *, min_quote_words: int
) -> dict[str, Any]:
    """The Messages API arguments for one turn, built only from ``context`` and ``settings``."""
    check_agent_tools(context)
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
                "text": agent_retry_note(
                    context.validation_errors, context.previous_answer_json, context.transcript
                ),
            }
        )
    request: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": build_system_prompt(min_quote_words),
        "messages": messages,
        "tools": tools,
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    }
    if settings.temperature is not None:
        request["extra_body"] = {"temperature": settings.temperature}
    return request


class AgentAnthropicProvider(Provider):
    """The real provider for the agent loop. ``client`` is an ``anthropic.Anthropic`` (or, in
    tests, any object with ``messages.create(**kwargs)``); build the real one with
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
        return f"AgentAnthropicProvider(model={self.settings.model!r})"

    def next_step(self, context: ModelContext) -> ModelStep:
        request = build_request(context, self.settings, min_quote_words=self.min_quote_words)
        try:
            response = self._client.messages.create(**request)
        except Exception as error:  # noqa: BLE001 - reported to the loop, which falls back
            raise ProviderError(self._describe(error)) from None
        log.info(
            "Agent model turn: stop_reason=%s, content blocks=%d",
            getattr(response, "stop_reason", None),
            len(getattr(response, "content", None) or []),
        )
        return to_step(response)

    def _describe(self, error: Exception) -> str:
        status = getattr(error, "status_code", None)
        text = f"{type(error).__name__}" + (f" (HTTP {status})" if status else "")
        if self._secret is not None:
            text = text.replace(self._secret.get_secret_value(), "**********")
        return f"the Anthropic API call failed: {text}"


def build_provider(
    settings: AgentProviderSettings, *, min_quote_words: int
) -> AgentAnthropicProvider:
    """The configured Anthropic agent provider. Raises ``CredentialError`` if the key is
    missing and ``ProviderUnavailable`` if the optional ``anthropic`` package is not
    installed."""
    if settings.provider != "anthropic":  # the factory routes only "anthropic" here
        raise ProviderError(f"unsupported agent provider {settings.provider!r}")
    secret = read_api_key()
    try:
        import anthropic
    except ImportError:
        raise ProviderUnavailable(
            "the optional analyst dependencies are not installed; run "
            "`uv sync --extra analyst` to use a real provider with the agent"
        ) from None
    client = anthropic.Anthropic(
        api_key=secret.get_secret_value(),
        base_url=API_BASE_URL,
        timeout=settings.timeout_s,
        max_retries=0,  # the loop's single answer retry is the only retry (D-058)
    )
    return AgentAnthropicProvider(settings, client, min_quote_words=min_quote_words, secret=secret)
