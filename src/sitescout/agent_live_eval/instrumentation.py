"""Milestone 10, Phase 6 (D-060): latency and token-usage capture with no change to what a
provider adapter sends or receives, and failure classification for a finished run.

``RecordingAnthropicClient``/``RecordingOpenAIClient`` wrap a real (or, in tests, fake) SDK
client and record only the wall-clock time and the reply's own ``usage`` object, if any, for
exactly the one method the corresponding ``agent_provider`` adapter calls
(``messages.create``/``responses.create``). Every request and reply passes through
unmodified; the wrapper never reads, stores or logs the API key (which the SDK sends as an
HTTP header, never as part of the request or response body it sees). Building the real
provider and swapping the wrapper in is the live script's job (``scripts/agent_live_eval.py``
wraps the already-built provider's ``._client`` in place, after
``sitescout.agent_provider.build_configured_agent_provider`` has built it exactly as Phase 5
intends); nothing here touches ``agent_provider`` or ``agent``.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sitescout.agent import AgentResult

FailureCategory = Literal[
    "answered_grounded",
    "answered_after_retry",
    "validator_rejected",
    "provider_error",
    "malformed_response",
    "budget_exhausted",
    "tool_failure",
]


class CallUsage(BaseModel):
    """One provider call's timing and, when the reply carried one, its token usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None


def _usage_of(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


class RecordingAnthropicClient:
    """Wraps a real (or fake) Anthropic client: ``self.messages.create`` records, then
    delegates. ``calls`` accumulates across every case run through this one instance."""

    def __init__(self, real_client: Any) -> None:
        self._real = real_client
        self.calls: list[CallUsage] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        started = time.monotonic()
        response = self._real.messages.create(**kwargs)
        elapsed = time.monotonic() - started
        input_tokens, output_tokens = _usage_of(response)
        self.calls.append(
            CallUsage(
                index=len(self.calls),
                latency_s=elapsed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return response


class RecordingOpenAIClient:
    """Wraps a real (or fake) OpenAI client: ``self.responses.create`` records, then
    delegates. ``calls`` accumulates across every case run through this one instance."""

    def __init__(self, real_client: Any) -> None:
        self._real = real_client
        self.calls: list[CallUsage] = []
        self.responses = self

    def create(self, **kwargs: Any) -> Any:
        started = time.monotonic()
        response = self._real.responses.create(**kwargs)
        elapsed = time.monotonic() - started
        input_tokens, output_tokens = _usage_of(response)
        self.calls.append(
            CallUsage(
                index=len(self.calls),
                latency_s=elapsed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return response


def classify_result(result: AgentResult) -> FailureCategory:
    """Which of the five failure kinds (or which of the two answered kinds) this run ended
    as, from ``AgentResult`` alone: model failure, provider/API failure, tool failure or
    validator rejection. Never raises; every ``TerminationReason`` maps to exactly one kind.

    ``recoverable_error_limit`` is classified as ``budget_exhausted`` (a model-behaviour
    category), not ``tool_failure``: the loop's own recoverable errors (an unknown tool,
    malformed arguments, a refused tool call) are, by construction, ones a well-behaved model
    could have avoided, and M9's own tool errors carry no machine-readable code that would
    let this classifier tell a genuine data problem apart from a model asking for something
    that does not exist. ``tool_failure`` is reserved for the loop's own distinct
    ``tool_failure`` termination: an unexpected Python exception inside a tool, which the
    loop never treats as recoverable."""
    if result.status == "answered":
        return "answered_after_retry" if result.state.retried else "answered_grounded"
    reason = result.termination
    if reason == "provider_error":
        return "provider_error"
    if reason == "malformed_provider_response":
        return "malformed_response"
    if reason == "validation_failed":
        return "validator_rejected"
    if reason == "tool_failure":
        return "tool_failure"
    return "budget_exhausted"  # iteration_limit, tool_call_limit, retrieval_call_limit,
    # recoverable_error_limit
