"""Milestone 9, phase 3a: the model provider interface (SPEC §11).

A provider decides, one step at a time, what the SiteScout Analyst does next: call one of
the six deterministic tools, or give its final structured answer. ``Provider`` is the whole
contract; the tool-calling loop (``sitescout.analyst.run``) never depends on anything more
specific than this. Phase 3a ships one implementation,
:class:`~sitescout.analyst.fake_model.FakeModel`, scripted for offline tests; a real API
provider (a later phase) implements the same interface and changes nothing else.

A provider never touches a file, the network or a subprocess itself: it only returns data.
Every side effect (reading the processed layers, running a tool) stays in the loop and in
``sitescout.analyst.tools``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sitescout.analyst.validate import ValidationIssue


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolDefinition(_Model):
    """One tool a provider may call: its name, a short description and its argument shape."""

    name: str
    description: str
    arguments_schema: dict[str, Any]


class ToolCallRequest(_Model):
    """A provider's request to call one tool, before its arguments are checked."""

    tool: str
    arguments: dict[str, Any] = {}


class ToolCallRecord(_Model):
    """One tool call the loop actually executed: its name, checked arguments and result."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ModelContext(_Model):
    """Everything a provider sees when deciding its next step.

    ``validation_errors`` is empty except on the one retry turn, where it holds exactly what
    :func:`sitescout.analyst.validate.validate_answer` (or, for a malformed answer, the
    schema check) found wrong with the previous attempt. ``previous_answer_json`` is empty
    except on that same retry turn, where it holds the previous attempt's raw answer data
    (whatever the model returned as ``answer_json``, whether or not it parsed): each API
    call is stateless, so without this the model has no memory of what it wrote for the
    statements the errors do *not* name, and cannot literally leave them unchanged.
    """

    question: str
    tools: tuple[ToolDefinition, ...]
    transcript: tuple[ToolCallRecord, ...] = ()
    validation_errors: tuple[ValidationIssue, ...] = ()
    previous_answer_json: dict[str, Any] | None = None


class ModelStep(_Model):
    """A provider's one action: exactly one of ``tool_call`` or ``answer_json``.

    ``answer_json`` is the model's final answer as raw, not-yet-validated data (as a real
    model's JSON output would arrive); the loop parses and checks it. Neither set, or both
    set, is a protocol violation the loop refuses outright.
    """

    tool_call: ToolCallRequest | None = None
    answer_json: dict[str, Any] | None = None

    @property
    def kind(self) -> Literal["tool_call", "answer", "invalid"]:
        if self.tool_call is not None and self.answer_json is not None:
            return "invalid"
        if self.tool_call is not None:
            return "tool_call"
        if self.answer_json is not None:
            return "answer"
        return "invalid"


class Provider(ABC):
    """What a model implementation must do. See the module docstring."""

    @abstractmethod
    def next_step(self, context: ModelContext) -> ModelStep:
        """Decide the next action from the question, the transcript so far and any
        validation errors from a previous, failed final answer."""
