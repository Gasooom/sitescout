"""Milestone 10, Phase 3 (D-058): the agent loop.

``run_agent(context, question, provider)`` repeats until it stops:

    ModelContext (question, the nine tools, everything observed so far)
      -> provider.next_step -> a tool call, or a final answer

    a tool call: check the name and arguments against the registry -> reuse an identical
                 earlier result, or run the tool (spending the tool or the retrieval budget)
                 -> an observation (a structured result, or a structured error) -> the state
    a final answer: parse it as an ``AgentAnswer`` and validate it (M9 validator first, then the
                 agent's grounding rules) -> PASS ends the run; FAIL retries exactly once with
                 the errors; a second failure ends it with the fallback

The model chooses every action; the loop chooses none. Nothing is scripted or fixed in order.

**Provider boundary.** The loop uses M9's ``Provider``, ``ModelContext`` and ``ModelStep``
unchanged: a step is either one tool call (name and arguments as data) or the raw final answer.
It never touches OpenAI or Anthropic code. The M9 adapters still check for exactly M9's six
tools and send M9's prompt, so they cannot drive this loop until a later phase adds that seam;
the tests use ``FakeModel``.

**Limits** come from the ``agent:`` block of ``config/settings.yaml``. The model never sees
them. Every provider call is an iteration. A tool call that would exceed the tool or the
retrieval budget is not run: the run ends. Errors that a model can react to (unknown tool,
malformed arguments, a refused tool call, a search with a bad filter) are returned as
observations and counted; the one after ``max_recoverable_errors`` ends the run. An identical
call (same tool, same normalized arguments) is never run twice: it returns a note pointing at
the earlier observation, spends no tool budget and is counted as a duplicate.

**Ending.** ``termination`` says why: ``answered``, or one of the fallback reasons. Every
fallback is M9's ``FallbackAnswer``, labelled "no AI summary": the reason and the raw evidence
records the run gathered, with no prose and no new number. An unvalidated answer is never
returned, and reaching a limit never produces an answer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from sitescout.agent.registry import AGENT_REGISTRY, AGENT_TOOL_DEFINITIONS, AgentContext, AgentTool
from sitescout.agent.state import (
    Action,
    AgentState,
    Observation,
    ObservationError,
    TerminationReason,
    records_in,
    session_from_observations,
)
from sitescout.agent.validate import AgentAnswer, parse_answer, validate_agent_answer
from sitescout.analyst.provider import ModelContext, Provider, ToolCallRecord
from sitescout.analyst.run import FallbackAnswer
from sitescout.analyst.tools import AnalystError
from sitescout.analyst.validate import ValidationIssue, ValidationResult, collect_session
from sitescout.config import AgentSettings
from sitescout.knowledge import KnowledgeError

log = logging.getLogger(__name__)

ANSWER_RETRIES = 1  # the same single retry M9 allows (D-052); a fixed rule, not a setting


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_iterations: int
    max_tool_calls: int
    max_retrieval_calls: int
    max_recoverable_errors: int
    min_quote_words: int

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> AgentLimits:
        return cls(
            max_iterations=settings.max_iterations,
            max_tool_calls=settings.max_tool_calls,
            max_retrieval_calls=settings.max_retrieval_calls,
            max_recoverable_errors=settings.max_recoverable_errors,
            min_quote_words=settings.min_quote_words,
        )


class AgentResult(BaseModel):
    """The whole run: the outcome and the full state, which is the audit trail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    status: Literal["answered", "fallback"]
    termination: TerminationReason
    answer: AgentAnswer | None = None
    fallback: FallbackAnswer | None = None
    state: AgentState

    @property
    def validation(self) -> ValidationResult | None:
        attempts = self.state.validation_attempts
        return attempts[-1] if attempts else None


def _summarise(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}"
        for item in error.errors()[:5]
    )


def _cache_key(tool: AgentTool, arguments: dict[str, Any]) -> str:
    return f"{tool.name}|{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"


class _Stop(Exception):
    """Ends the run with a fallback; raised inside the step handlers."""

    def __init__(self, termination: TerminationReason, reason: str) -> None:
        super().__init__(reason)
        self.termination = termination
        self.reason = reason


def run_agent(
    context: AgentContext,
    question: str,
    provider: Provider,
    limits: AgentLimits | None = None,
    registry: dict[str, AgentTool] | None = None,
) -> AgentResult:
    """Run the loop described in the module docstring; never raises for a model's mistake.

    ``limits`` defaults to the configured ones; ``registry`` to the nine capabilities.
    """
    settings = context.investigation.analyst.config.settings.agent
    limits = limits or AgentLimits.from_settings(settings)
    tools = registry if registry is not None else AGENT_REGISTRY
    state = AgentState(question=question)
    cache: dict[str, Observation] = {}
    validation_errors: tuple[ValidationIssue, ...] = ()
    previous_answer_json: dict[str, Any] | None = None

    def fallback(reason: str, termination: TerminationReason) -> AgentResult:
        records, _ = collect_session([session_from_observations(state.observations)])
        final = FallbackAnswer(reason=reason, records=tuple(records.values()))
        ended = state.model_copy(update={"termination": termination, "fallback": final})
        log.info("Agent run ended: %s (%s)", termination, reason)
        return AgentResult(
            question=question,
            status="fallback",
            termination=termination,
            fallback=final,
            state=ended,
        )

    def observe(
        action: Action,
        tool: AgentTool | None,
        arguments: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: ObservationError | None = None,
        cached_from: Observation | None = None,
    ) -> Observation:
        record_ids: tuple[str, ...] = ()
        if cached_from is not None:
            record_ids = cached_from.record_ids
        elif result is not None:
            found, compared = records_in(result)
            record_ids = (*found, *compared)  # records, then comparison fields
        return Observation(
            index=action.index,
            tool=action.tool,
            arguments=arguments,
            tool_kind=tool.kind if tool else None,
            status="error" if error else "ok",
            cached=cached_from is not None,
            duplicate_of=cached_from.index if cached_from is not None else None,
            result=result,
            error=error,
            record_ids=record_ids,
        )

    def take(observation: Observation, **counters: int) -> None:
        nonlocal state
        state = state.model_copy(
            update={
                "observations": (*state.observations, observation),
                **{name: getattr(state, name) + step for name, step in counters.items()},
            }
        )
        if observation.status == "error":
            state = state.model_copy(update={"recoverable_errors": state.recoverable_errors + 1})
            if state.recoverable_errors > limits.max_recoverable_errors:
                raise _Stop(
                    "recoverable_error_limit",
                    f"more than {limits.max_recoverable_errors} recoverable errors "
                    f"(the last: {observation.error.kind if observation.error else ''})",
                )

    def call_tool(action: Action) -> None:
        tool = tools.get(action.tool)
        if tool is None:
            error = ObservationError(
                kind="unknown_tool",
                message=f"unknown tool {action.tool!r}; the available tools are {sorted(tools)}",
            )
            take(observe(action, None, dict(action.arguments), error=error))
            return
        try:
            arguments = tool.args_model.model_validate(action.arguments)
        except ValidationError as failure:
            error = ObservationError(kind="malformed_arguments", message=_summarise(failure))
            take(observe(action, tool, dict(action.arguments), error=error))
            return
        normalized = arguments.model_dump(exclude_none=True, mode="json")
        key = _cache_key(tool, normalized)
        earlier = cache.get(key)
        if earlier is not None:
            duplicate: dict[str, Any] | None = None
            if earlier.status == "ok":
                duplicate = {
                    "cached": True,
                    "duplicate_of": earlier.index,
                    "note": "an identical call was already made; see that observation",
                }
            take(
                observe(
                    action,
                    tool,
                    normalized,
                    result=duplicate,
                    error=earlier.error,
                    cached_from=earlier,
                ),
                duplicate_calls=1,
            )
            return
        if tool.kind == "knowledge":
            if state.retrieval_calls >= limits.max_retrieval_calls:
                raise _Stop(
                    "retrieval_call_limit",
                    f"the retrieval-call limit ({limits.max_retrieval_calls}) was reached",
                )
            spent = {"retrieval_calls": 1}
        else:
            if state.tool_calls >= limits.max_tool_calls:
                raise _Stop(
                    "tool_call_limit", f"the tool-call limit ({limits.max_tool_calls}) was reached"
                )
            spent = {"tool_calls": 1}
        try:
            result = tool.call(context, arguments).model_dump(mode="json")
        except (AnalystError, KnowledgeError) as failure:
            code = getattr(failure, "code", None)
            message = getattr(failure, "message", None) or str(failure)
            observation = observe(
                action,
                tool,
                normalized,
                error=ObservationError(kind="tool_error", code=code, message=message),
            )
            cache[key] = observation
            take(observation, **spent)
            return
        except Exception as failure:
            log.exception("Tool %s failed unexpectedly", tool.name)
            raise _Stop(
                "tool_failure", f"tool {tool.name!r} failed unexpectedly ({type(failure).__name__})"
            ) from None
        observation = observe(action, tool, normalized, result=result)
        cache[key] = observation
        take(observation, **spent)

    def check_answer(raw: dict[str, Any]) -> AgentResult | None:
        nonlocal state, validation_errors, previous_answer_json
        answer: AgentAnswer | None = None
        try:
            answer = parse_answer(raw)
        except ValidationError as failure:
            outcome = ValidationResult(
                passed=False,
                errors=(
                    ValidationIssue(
                        rule="malformed_answer", statement_id="-", message=str(failure)
                    ),
                ),
            )
        else:
            outcome = validate_agent_answer(
                answer,
                session_from_observations(state.observations),
                min_quote_words=limits.min_quote_words,
            )
        attempts = (*state.validation_attempts, outcome)
        state = state.model_copy(update={"validation_attempts": attempts})
        if outcome.passed and answer is not None:
            state = state.model_copy(update={"termination": "answered", "answer": answer})
            log.info("Agent run answered after %d iteration(s)", state.iterations)
            return AgentResult(
                question=question,
                status="answered",
                termination="answered",
                answer=answer,
                state=state,
            )
        if len(attempts) > ANSWER_RETRIES:
            raise _Stop("validation_failed", "the final answer failed validation twice")
        state = state.model_copy(update={"retried": True})
        validation_errors = outcome.errors
        previous_answer_json = raw
        return None

    while True:
        try:
            if state.iterations >= limits.max_iterations:
                raise _Stop(
                    "iteration_limit", f"the iteration limit ({limits.max_iterations}) was reached"
                )
            model_context = ModelContext(
                question=question,
                tools=AGENT_TOOL_DEFINITIONS,
                transcript=tuple(
                    ToolCallRecord(tool=o.tool, arguments=o.arguments, result=o.as_model_result())
                    for o in state.observations
                ),
                validation_errors=validation_errors,
                previous_answer_json=previous_answer_json,
            )
            try:
                step = provider.next_step(model_context)
            except Exception as failure:  # noqa: BLE001 - a provider must never crash the loop
                log.warning("Provider failed: %s", type(failure).__name__)
                raise _Stop("provider_error", f"the model raised an error: {failure}") from None
            state = state.model_copy(update={"iterations": state.iterations + 1})
            kind = step.kind
            if kind == "invalid":
                raise _Stop(
                    "malformed_provider_response",
                    "the model returned neither a tool call nor a final answer",
                )
            if kind == "answer":
                assert step.answer_json is not None
                if (finished := check_answer(step.answer_json)) is not None:
                    return finished
                continue
            request = step.tool_call
            assert request is not None
            action = Action(
                index=state.iterations, tool=request.tool, arguments=dict(request.arguments)
            )
            state = state.model_copy(update={"actions": (*state.actions, action)})
            call_tool(action)
        except _Stop as stop:
            return fallback(stop.reason, stop.termination)
