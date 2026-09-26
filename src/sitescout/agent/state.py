"""Milestone 10, Phase 3 (D-058): the agent's explicit, serializable state.

``AgentState`` holds everything needed to inspect and re-check one run: the question, every
action the model requested, every observation it got back (with the evidence records the tool
returned), the counters the limits are enforced on, the validation attempts, and how the run
ended. It is an immutable snapshot: the loop makes a new one after every step, so a test (or a
later evaluation) can look at any point of the trajectory. It holds no provider internals.

An observation keeps the tool's structured result as JSON-ready data. The evidence records and
comparison fields in it are recoverable with ``session_from_observations``, which is what the
answer validator checks a final answer against. A cached duplicate keeps no records of its own:
the observation it repeats already holds them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sitescout.agent.validate import AgentAnswer
from sitescout.analyst.run import FallbackAnswer
from sitescout.analyst.tools import ComparisonField
from sitescout.analyst.validate import ToolRecords, ValidationResult
from sitescout.evidence import EvidenceRecord

TerminationReason = Literal[
    "answered",
    "iteration_limit",
    "tool_call_limit",
    "retrieval_call_limit",
    "recoverable_error_limit",
    "provider_error",
    "malformed_provider_response",
    "validation_failed",
    "tool_failure",
]
# The errors that are returned to the model as observations (and counted).
ErrorKind = Literal["unknown_tool", "malformed_arguments", "tool_error"]
ToolKind = Literal["analyst", "investigation", "knowledge"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Action(_Model):
    """One action the model requested, exactly as requested (before any validation)."""

    index: int  # the iteration that requested it, from 1
    tool: str
    arguments: dict[str, Any]


class ObservationError(_Model):
    kind: ErrorKind
    code: str | None = None  # a tool's own error code, e.g. unknown_site
    message: str


class Observation(_Model):
    """What came back for one action: a structured result or a structured error."""

    index: int
    tool: str
    arguments: dict[str, Any]  # validated and normalized when they were valid, else as given
    tool_kind: ToolKind | None  # None for an unknown tool
    status: Literal["ok", "error"]
    cached: bool = False  # true when an identical earlier call's result was reused
    duplicate_of: int | None = None  # the earlier observation's index, when cached
    result: dict[str, Any] | None = None
    error: ObservationError | None = None
    record_ids: tuple[str, ...] = ()  # every evidence record id the result holds

    def as_model_result(self) -> dict[str, Any]:
        """The dict a provider is shown for this observation."""
        if self.status == "error":
            assert self.error is not None
            return {"error": self.error.model_dump(exclude_none=True)}
        assert self.result is not None
        return self.result


class AgentState(_Model):
    question: str
    iterations: int = 0
    tool_calls: int = 0  # deterministic tool calls executed (not cached duplicates)
    retrieval_calls: int = 0  # search_knowledge calls executed
    recoverable_errors: int = 0
    duplicate_calls: int = 0
    actions: tuple[Action, ...] = ()
    observations: tuple[Observation, ...] = ()
    validation_attempts: tuple[ValidationResult, ...] = ()
    retried: bool = False
    termination: TerminationReason | None = None
    answer: AgentAnswer | None = None
    fallback: FallbackAnswer | None = None

    @property
    def tool_observations(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.tool_kind in ("analyst", "investigation"))

    @property
    def retrieval_observations(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.tool_kind == "knowledge")

    def trajectory(self) -> list[dict[str, Any]]:
        """A compact, ordered view of the run for logs and evaluation: what was asked, what
        came back and which records it held."""
        return [
            {
                "index": o.index,
                "tool": o.tool,
                "arguments": o.arguments,
                "status": o.status,
                "cached": o.cached,
                "error": o.error.kind if o.error else None,
                "records": len(o.record_ids),
            }
            for o in self.observations
        ]


# --- Recovering the evidence from the observations ---------------------------------------------

_RECORD_KEYS = frozenset(EvidenceRecord.model_fields)
_COMPARISON_KEYS = frozenset(ComparisonField.model_fields)


def _walk(value: Any, records: dict[str, EvidenceRecord], comparisons: dict[str, ComparisonField]):
    if isinstance(value, dict):
        keys = frozenset(value)
        if keys == _RECORD_KEYS:
            records[value["id"]] = EvidenceRecord.model_validate(value)
        elif keys == _COMPARISON_KEYS:
            comparisons[value["id"]] = ComparisonField.model_validate(value)
        else:
            for item in value.values():
                _walk(item, records, comparisons)
    elif isinstance(value, list | tuple):
        for item in value:
            _walk(item, records, comparisons)


def records_in(result: Any) -> tuple[dict[str, EvidenceRecord], dict[str, ComparisonField]]:
    """The evidence records and comparison fields inside a tool result's JSON data."""
    records: dict[str, EvidenceRecord] = {}
    comparisons: dict[str, ComparisonField] = {}
    _walk(result, records, comparisons)
    return records, comparisons


def session_from_observations(observations: Sequence[Observation]) -> ToolRecords:
    """Every record and comparison field the model was actually shown, as the M9 validator's
    session item. Only records from this run's observations are ever trusted."""
    records: dict[str, EvidenceRecord] = {}
    comparisons: dict[str, ComparisonField] = {}
    for observation in observations:
        if observation.status == "ok" and not observation.cached:
            found, compared = records_in(observation.result)
            records.update(found)
            comparisons.update(compared)
    return ToolRecords(records=tuple(records.values()), comparisons=tuple(comparisons.values()))
