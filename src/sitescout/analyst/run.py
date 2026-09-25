"""Milestone 9, phase 3a: the tool-calling loop (SPEC §11).

``run_analyst(data, question, model)`` is the whole flow:

    question -> provider.next_step -> a tool call (executed, its result added to the
    transcript) or a final answer (checked by validate_answer) -> PASS returns it,
    FAIL retries exactly once with the validator's errors -> a second failure, an
    unknown tool, bad arguments, a tool's own refusal, the tool-call limit, or a
    malformed step -> the deterministic fallback (labelled "no AI summary"): the raw
    evidence records the session already gathered, with no prose written about them.

A provider (real or fake) only ever returns data; every tool call, every check and every
file read happens here and in ``sitescout.analyst.tools``, never inside the provider. Only
the six allow-listed tools can run, their arguments are validated before a tool is ever
called, and nothing here reads a file, the network, a subprocess or an environment
variable.

**Limits.** The tool-call limit comes from the ``analyst:`` block of
``config/settings.yaml`` (D-051), through ``data.config``; the single retry is a fixed rule
(D-052), not a setting. Nothing here reads the environment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as SchemaError

from sitescout.analyst.provider import (
    ModelContext,
    Provider,
    ToolCallRecord,
    ToolCallRequest,
    ToolDefinition,
)
from sitescout.analyst.tools import (
    TOOLS,
    AnalystData,
    AnalystError,
    FindQuery,
    compare_sites,
    explain_score,
    find_sites,
    generate_brief,
    get_site,
    network_contribution,
)
from sitescout.analyst.validate import (
    Answer,
    EvidenceRecord,
    ValidationIssue,
    ValidationResult,
    collect_session,
    validate_answer,
)
from sitescout.config import AnalystSettings

FALLBACK_LABEL = "no AI summary"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteIdArgs(_Model):
    """Arguments of every tool that takes a single site id."""

    candidate_id: str


class CompareArgs(_Model):
    a: str
    b: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    call: Callable[[AnalystData, BaseModel], BaseModel]


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "find_sites",
            "Look up candidates by exact filters (id, district, province, rank, profile, "
            "confidence, network/Top-30 flags, grid evidence status). No fuzzy search.",
            FindQuery,
            lambda data, args: find_sites(data, args),
        ),
        ToolSpec(
            "get_site",
            "Everything about one candidate: identity, score, components, evidence, "
            "selection flags, unknowns and (for network sites) next actions.",
            SiteIdArgs,
            lambda data, args: get_site(data, args.candidate_id),
        ),
        ToolSpec(
            "compare_sites",
            "Two candidates side by side, field by field, with a value-neutral relation "
            "per field. Never a winner, ranking or difference.",
            CompareArgs,
            lambda data, args: compare_sites(data, args.a, args.b),
        ),
        ToolSpec(
            "explain_score",
            "The stored score of one candidate broken into its components, weights, "
            "percentile points and bonuses. Nothing is recomputed.",
            SiteIdArgs,
            lambda data, args: explain_score(data, args.candidate_id),
        ),
        ToolSpec(
            "network_contribution",
            "What the exact MCLP establishes about one candidate: eligibility, the "
            "demand it covers or would add, the nearest network site, spacing conflicts, "
            "and why a non-selected site is out, when an M6 rule establishes a reason.",
            SiteIdArgs,
            lambda data, args: network_contribution(data, args.candidate_id),
        ),
        ToolSpec(
            "generate_brief",
            "The Milestone 7 Site Evidence Brief sections of a network site.",
            SiteIdArgs,
            lambda data, args: generate_brief(data, args.candidate_id),
        ),
    )
}
assert set(REGISTRY) == set(TOOLS)

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = tuple(
    ToolDefinition(
        name=spec.name,
        description=spec.description,
        arguments_schema=spec.args_model.model_json_schema(),
    )
    for spec in REGISTRY.values()
)


@dataclass(frozen=True, slots=True)
class RunLimits:
    """The loop's limits: ``max_tool_calls`` from config, and the fixed single retry."""

    max_tool_calls: int
    max_retries: int = 1

    @classmethod
    def from_settings(cls, analyst: AnalystSettings) -> RunLimits:
        return cls(max_tool_calls=analyst.max_tool_calls)


class FallbackAnswer(_Model):
    """The deterministic fallback: no prose, only the evidence records the session already
    gathered from its tool calls, exactly as those tools returned them."""

    label: Literal["no AI summary"] = FALLBACK_LABEL
    reason: str
    records: tuple[EvidenceRecord, ...] = ()


class RunResult(_Model):
    """The whole run, kept for the audit trail: the question, every tool call and its
    result, every validator check the run made, whether it retried, and the outcome."""

    question: str
    transcript: tuple[ToolCallRecord, ...] = ()
    attempts: tuple[ValidationResult, ...] = ()
    retried: bool = False
    status: Literal["answered", "fallback"]
    answer: Answer | None = None
    fallback: FallbackAnswer | None = None


def run_analyst(
    data: AnalystData, question: str, model: Provider, limits: RunLimits | None = None
) -> RunResult:
    """Run the loop described in the module docstring; never raises for a model's mistake.

    ``limits`` defaults to the configured ones (``data.config.settings.analyst``).
    """
    limits = limits or RunLimits.from_settings(data.config.settings.analyst)
    transcript: list[ToolCallRecord] = []
    session_results: list[BaseModel] = []
    attempts: list[ValidationResult] = []
    validation_errors: tuple[ValidationIssue, ...] = ()
    previous_answer_json: dict[str, Any] | None = None
    retried = False

    def fallback(reason: str) -> RunResult:
        records, _ = collect_session(session_results)
        return RunResult(
            question=question,
            transcript=tuple(transcript),
            attempts=tuple(attempts),
            retried=retried,
            status="fallback",
            fallback=FallbackAnswer(reason=reason, records=tuple(records.values())),
        )

    while True:
        context = ModelContext(
            question=question,
            tools=TOOL_DEFINITIONS,
            transcript=tuple(transcript),
            validation_errors=validation_errors,
            previous_answer_json=previous_answer_json,
        )
        try:
            step = model.next_step(context)
        except Exception as error:  # noqa: BLE001 - a provider must never crash the loop
            return fallback(f"the model raised an error: {error}")

        kind = step.kind
        if kind == "invalid":
            return fallback("the model returned neither a tool call nor a final answer")

        if kind == "answer":
            assert step.answer_json is not None
            try:
                answer = Answer.model_validate(step.answer_json)
            except SchemaError as error:
                validation_errors = (
                    ValidationIssue(rule="malformed_answer", statement_id="-", message=str(error)),
                )
                attempts.append(ValidationResult(passed=False, errors=validation_errors))
                if len(attempts) > limits.max_retries:
                    return fallback("the model's answer did not match the answer schema twice")
                retried = True
                previous_answer_json = step.answer_json
                continue
            result = validate_answer(answer, session_results)
            attempts.append(result)
            if result.passed:
                return RunResult(
                    question=question,
                    transcript=tuple(transcript),
                    attempts=tuple(attempts),
                    retried=retried,
                    status="answered",
                    answer=answer,
                )
            if len(attempts) > limits.max_retries:
                return fallback("the answer failed validation twice")
            retried = True
            validation_errors = result.errors
            previous_answer_json = answer.model_dump(mode="json")
            continue

        # kind == "tool_call"
        call: ToolCallRequest = step.tool_call  # type: ignore[assignment]
        if len(transcript) >= limits.max_tool_calls:
            return fallback(f"the tool-call limit ({limits.max_tool_calls}) was reached")
        spec = REGISTRY.get(call.tool)
        if spec is None:
            return fallback(f"unknown tool {call.tool!r}; only {sorted(REGISTRY)} may be called")
        try:
            arguments = spec.args_model.model_validate(call.arguments)
        except SchemaError as error:
            return fallback(f"invalid arguments for {call.tool!r}: {error}")
        try:
            tool_result = spec.call(data, arguments)
        except AnalystError as error:
            return fallback(f"{call.tool} refused its arguments: {error}")

        session_results.append(tool_result)
        transcript.append(
            ToolCallRecord(
                tool=call.tool,
                arguments=arguments.model_dump(exclude_none=True),
                result=tool_result.model_dump(),
            )
        )
