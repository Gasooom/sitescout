"""Helpers for the M10 agent tests: scripted models over the SYNTHETIC analyst world, the real
knowledge index, and small builders for tool calls and final answers. No test here calls a
model, an API or the network."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from sitescout.agent import AgentContext, AgentLimits, records_in
from sitescout.analyst import FakeModel, ModelContext, ModelStep, ToolCallRequest
from sitescout.config import load_config
from sitescout.evidence import EvidenceRecord
from sitescout.investigation import InvestigationData
from sitescout.knowledge import KnowledgeIndex, build_knowledge_index


@functools.cache
def knowledge_index() -> KnowledgeIndex:
    """The real project-knowledge index, built once (offline, a fraction of a second)."""
    return build_knowledge_index(load_config())


def make_context(world) -> AgentContext:
    config, processed, _ = world
    return AgentContext(
        investigation=InvestigationData.load(config, processed),
        knowledge=knowledge_index(),
        knowledge_top_k=config.settings.agent.knowledge_top_k,
    )


def limits(**changes: int) -> AgentLimits:
    values = {
        "max_iterations": 12,
        "max_tool_calls": 10,
        "max_retrieval_calls": 3,
        "max_recoverable_errors": 2,
        "min_quote_words": 3,
    }
    return AgentLimits(**{**values, **changes})


def call(tool: str, **arguments: Any) -> ModelStep:
    return ModelStep(tool_call=ToolCallRequest(tool=tool, arguments=arguments))


def stmt(
    text: str, kind: str = "UNKNOWN", ids: tuple[str, ...] = (), quotes: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {"text": text, "kind": kind, "evidence_ids": list(ids), "quotes": list(quotes)}


def final(*direct: dict[str, Any], **sections: list[dict[str, Any]]) -> ModelStep:
    return ModelStep(answer_json={"direct_answer": list(direct), **sections})


def unknown_answer() -> ModelStep:
    return final(stmt("The requested information is not available."))


def script(*steps: ModelStep) -> FakeModel:
    return FakeModel(list(steps))


def reactive(function: Callable[[ModelContext], ModelStep]) -> FakeModel:
    return FakeModel(function)


def seen(context: ModelContext) -> dict[str, EvidenceRecord]:
    """Every evidence record the model has been shown so far, by id."""
    found: dict[str, EvidenceRecord] = {}
    for item in context.transcript:
        found.update(records_in(item.result)[0])
    return found


def quote_from(record: EvidenceRecord, words: int = 6) -> str:
    """A verbatim quotation of the first ``words`` words of a knowledge record."""
    return " ".join(record.display.split()[:words])
