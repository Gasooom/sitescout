"""Milestone 10, Phase 6 (D-060): the live evaluation's case schema.

A live case is a single free-text question with no scripted trajectory: the model chooses
every tool call and its order for itself through the unmodified agent loop (Phase 3) and the
unmodified real provider (Phase 5). This is deliberately not the Phase 4 ``Case`` schema
(``sitescout.agent_eval.cases``), which scripts a fixed ``provider:`` step sequence; a live
case instead carries only:

- ``sites``: zero or more named site selectors (a subset of ``find_sites``' own filters),
  each resolved against the real processed data before any API call, to fill the matching
  ``{{name}}`` placeholder in ``question`` with a real candidate id. This is deliberately
  never a hardcoded id that would go stale as the real data changes: a case whose selector
  matches no site is *skipped*, never run, and reported as skipped, exactly as a case whose
  site-fact no longer held would be in the M9 scenario evaluation, but decided per case
  rather than for the whole set.
- an optional ``expect`` block of *soft*, property-based expectations (which tools were used
  at some point, which evidence-id prefixes were cited, which phrases the answer contains,
  whether it answered UNKNOWN) — checked and reported as met or unmet, never as a pass/fail
  gate: the model is free to choose a different, equally valid path, and a live case failing
  a soft expectation is reported as data, not scored as a defect in the harness.

The pass/fail signal for grounding still comes entirely from the unchanged
``sitescout.agent.validate.validate_agent_answer``; this module defines no new rule.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sitescout.agent import AGENT_TOOL_NAMES
from sitescout.config import read_yaml

CATEGORIES = {
    "A": "knowledge only",
    "B": "structured only",
    "C": "mixed sources",
    "D": "unknown",
    "E": "candidate identity",
    "F": "number provenance",
    "G": "structured-first",
    "H": "multi-step trajectory",
}

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# The subset of find_sites' own filters a live case may resolve a site by (SPEC-defined
# fields only; never a raw, hand-typed candidate id, which is exactly what would go stale).
_SELECTOR_FIELDS = frozenset(
    {
        "district",
        "province",
        "rank",
        "profile",
        "confidence",
        "selected_mclp",
        "selected_top30",
        "grid_evidence_status",
    }
)


class LiveCaseError(Exception):
    """The live case file is invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LiveExpectation(_Model):
    """Soft, property-based expectations, checked against the actual outcome and reported as
    met or unmet; never a scripted step order and never a pass/fail gate on their own."""

    status: Literal["answered", "fallback", "either"] = "either"
    tools_include: tuple[str, ...] = ()  # every one of these must have been called at least once
    tools_any: tuple[str, ...] = ()  # at least one of these must have been called
    cites_prefix: tuple[str, ...] = ()  # a cited evidence id starting with each prefix
    contains_any: tuple[tuple[str, ...], ...] = ()  # each group: at least one phrase present
    expect_unknown: bool = False  # if answered, at least one statement is kind UNKNOWN

    @model_validator(mode="after")
    def _known_tools(self) -> LiveExpectation:
        named = {*self.tools_include, *self.tools_any}
        if unknown := sorted(named - set(AGENT_TOOL_NAMES)):
            raise ValueError(f"unknown tools {unknown}")
        return self


class LiveCase(_Model):
    id: str = Field(pattern=r"^[A-H]\d{2}$")
    category: str
    title: str = Field(min_length=1)
    question: str = Field(min_length=1)
    notes: str = Field(min_length=1)  # why this case is here, for the reviewer
    sites: dict[str, dict[str, Any]] = {}  # {{name}} in question -> a find_sites selector
    expect: LiveExpectation = LiveExpectation()

    @model_validator(mode="after")
    def _consistent(self) -> LiveCase:
        if self.category not in CATEGORIES or not self.id.startswith(self.category):
            raise ValueError(f"{self.id}: the id must start with its category letter")
        placeholders = set(_PLACEHOLDER.findall(self.question))
        if placeholders != set(self.sites):
            raise ValueError(
                f"{self.id}: question placeholders {sorted(placeholders)} must match "
                f"sites {sorted(self.sites)} exactly"
            )
        for name, selector in self.sites.items():
            if unknown := sorted(set(selector) - _SELECTOR_FIELDS):
                raise ValueError(f"{self.id}: sites[{name!r}] has unknown field(s) {unknown}")
        return self


class LiveCaseSet(_Model):
    version: int
    world: str
    cases: tuple[LiveCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> LiveCaseSet:
        ids = [c.id for c in self.cases]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("case ids must be unique and in sorted order")
        return self


def load_live_cases(path: Path) -> LiveCaseSet:
    """The live case file at ``path``; ``LiveCaseError`` if it does not fit the schema."""
    try:
        return LiveCaseSet.model_validate(read_yaml(path))
    except ValidationError as error:
        raise LiveCaseError(f"{path}: {error}") from error
