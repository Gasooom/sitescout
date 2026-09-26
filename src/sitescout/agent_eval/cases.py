"""Milestone 10, Phase 4 (D-058): the agent evaluation's case schema and loader.

A case is one scripted provider trajectory (tool calls, then answers) with what a correct
system must do with it: whether the validator should accept or reject the first answer
(``decision``, the ground truth), which rules it should report, how the run should end and
which tools it should have called. Nothing here judges wording or asks a model anything.

Values a case needs from the data (a score, a network share, the id of the first site a
search returned) are written as placeholders and filled from the tool outputs the scripted
provider has actually been shown, never typed in by hand:

    {{display|cand-a/score}}    the display text of that evidence record
    {{first_site|}}             the candidate id of the first site the latest find_sites returned

A case where the validator is known to err (D-058's documented limits) says so: ``decision``
stays the ground truth, ``validator_decision`` pins what the validator actually does and
``known_limit`` says why. The metrics count such a case as the false accept or false reject
it is; the suite passes only while the behaviour stays exactly as pinned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sitescout.config import read_yaml

CATEGORIES = {
    "A": "knowledge only",
    "B": "structured only",
    "C": "mixed sources",
    "D": "unknown",
    "E": "candidate identity",
    "F": "number provenance",
    "G": "structured-first edge cases",
    "H": "trajectory and termination",
}
Decision = Literal["accept", "reject", "none"]


class CaseError(Exception):
    """The case file is invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_Model):
    """One provider turn: a tool call, a final answer (raw JSON), or a provider failure."""

    tool: str | None = None
    arguments: dict[str, Any] = {}
    answer: dict[str, Any] | None = None
    provider_error: bool = False
    times: int = Field(default=1, ge=1)  # the same step repeated, for a repeated bad answer

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        kinds = [self.tool is not None, self.answer is not None, self.provider_error]
        if sum(kinds) != 1:
            raise ValueError("a step is exactly one of: tool, answer, provider_error")
        if self.arguments and self.tool is None:
            raise ValueError("arguments belong to a tool step")
        return self


class Expect(_Model):
    decision: Decision
    validator_decision: Literal["accept", "reject"] | None = None
    rules: tuple[str, ...] = ()  # the rules the first answer attempt fails, exactly
    status: Literal["answered", "fallback"]
    termination: str
    trajectory: tuple[str, ...] | None = None  # "tool", "tool:duplicate", "tool:error"
    attempts: int | None = None
    counters: dict[str, int] = {}
    cites: tuple[str, ...] = ()  # evidence ids the final answer must cite
    contains: tuple[str, ...] = ()  # phrases the final answer's statements must contain
    excludes: tuple[str, ...] = ()


class Case(_Model):
    id: str = Field(pattern=r"^[A-H][0-9]{2}$")
    category: str
    tags: tuple[str, ...] = ()
    title: str = Field(min_length=1)
    kind: Literal["legitimate", "adversarial"]
    question: str = Field(min_length=1)
    provider: tuple[Step, ...] = Field(min_length=1)
    limits: dict[str, int] = {}
    known_limit: str | None = None
    expect: Expect

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if self.category not in CATEGORIES or not self.id.startswith(self.category):
            raise ValueError(f"{self.id}: the id must start with its category letter")
        e = self.expect
        pinned = e.validator_decision is not None
        if pinned != (self.known_limit is not None):
            raise ValueError(f"{self.id}: known_limit and validator_decision go together")
        if pinned and e.decision == "none":
            raise ValueError(f"{self.id}: a known limit needs an accept or reject decision")
        if pinned and e.validator_decision == e.decision:
            raise ValueError(f"{self.id}: a known limit is where the validator differs")
        effective = e.validator_decision or e.decision
        if (effective == "reject") != bool(e.rules):
            raise ValueError(f"{self.id}: rules are listed exactly when the answer is rejected")
        return self


class CaseSet(_Model):
    version: int
    world: str
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> CaseSet:
        ids = [c.id for c in self.cases]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("case ids must be unique and in sorted order")
        return self


def load_cases(path: Path) -> CaseSet:
    """The case file at ``path``; ``CaseError`` if it does not fit the schema."""
    try:
        return CaseSet.model_validate(read_yaml(path))
    except ValidationError as error:
        raise CaseError(f"{path}: {error}") from error
