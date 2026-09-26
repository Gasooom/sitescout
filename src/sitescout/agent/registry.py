"""Milestone 10, Phase 3 (D-058): the nine capabilities the agent can call, in one registry.

    6 analyst tools (M9)         find_sites, get_site, compare_sites, explain_score,
                                 network_contribution, generate_brief
  + 2 investigation tools (M10) network_summary, nearby_sites
  + 1 knowledge tool (M10)      search_knowledge
  = 9

The registry is the agent layer's own. It reads M9's ``REGISTRY`` and Phase 2's
``INVESTIGATION_TOOLS`` and wraps each entry; it changes neither, and M9's six-tool registry
(pinned by the provider tests) stays exactly as it was. Every capability is read-only. Structured
SiteScout facts come from the eight deterministic tools; ``search_knowledge`` returns
documentation chunks as evidence records (id ``kb/...``, type RETRIEVED_FACT, display = the
chunk text), and never a current site or network number.

An ``AgentTool`` is called with the ``AgentContext`` (the loaded data and the knowledge index)
and its validated arguments. Its ``kind`` says which budget a call spends: ``knowledge`` calls
count as retrieval calls, the other two kinds as tool calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict

from sitescout.agent.state import ToolKind
from sitescout.analyst.provider import ToolDefinition
from sitescout.analyst.run import REGISTRY as M9_REGISTRY
from sitescout.analyst.tools import TOOLS as M9_TOOL_NAMES
from sitescout.config import Config
from sitescout.evidence import EvidenceRecord, record
from sitescout.investigation import INVESTIGATION_TOOLS, InvestigationData
from sitescout.knowledge import (
    KnowledgeIndex,
    KnowledgeSearchResult,
    SearchFilters,
    build_knowledge_index,
    search_knowledge,
)

KNOWLEDGE_TOOL = "search_knowledge"
INVESTIGATION_NAMES = ("network_summary", "nearby_sites")
AGENT_TOOL_NAMES = (*M9_TOOL_NAMES, *INVESTIGATION_NAMES, KNOWLEDGE_TOOL)
KNOWLEDGE_SOURCE = "SiteScout project documentation (knowledge index)"


@dataclass(frozen=True)
class AgentContext:
    """What the tools read: the M9 data with the processed directory, the knowledge index and
    how many chunks a search returns. Loaded once; never written."""

    investigation: InvestigationData
    knowledge: KnowledgeIndex
    knowledge_top_k: int

    @classmethod
    def load(cls, config: Config, processed_dir: Path) -> AgentContext:
        return cls(
            investigation=InvestigationData.load(config, processed_dir),
            knowledge=build_knowledge_index(config),
            knowledge_top_k=config.settings.agent.knowledge_top_k,
        )


class SearchKnowledgeArgs(BaseModel):
    """The arguments of ``search_knowledge``. The number of chunks is not the model's to set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1)
    doc_type: str | None = None
    topic: str | None = None
    milestone: Annotated[int, Strict()] | None = None
    decision_id: str | None = None


class KnowledgeHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int
    score: float
    matched_terms: tuple[str, ...]
    record_id: str


class KnowledgeObservation(BaseModel):
    """A knowledge search as an observation: the hits by rank, and each chunk as a record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Literal["search_knowledge"] = "search_knowledge"
    query: str
    filters: dict[str, str | int]
    top_k: int
    total_matches: int
    corpus_fingerprint: str
    retrieval_fingerprint: str
    hits: list[KnowledgeHit]
    records: list[EvidenceRecord]


def knowledge_observation(result: KnowledgeSearchResult) -> KnowledgeObservation:
    records = [
        record(
            hit.chunk.chunk_id,
            " > ".join(hit.chunk.section_path),
            "RETRIEVED_FACT",
            KNOWLEDGE_SOURCE,
            hit.chunk.source_path,
            hit.chunk.content_sha256,
            hit.chunk.text,
        )
        for hit in result.hits
    ]
    return KnowledgeObservation(
        query=result.query,
        filters=result.filters.active(),
        top_k=result.top_k,
        total_matches=result.total_matches,
        corpus_fingerprint=result.corpus_fingerprint,
        retrieval_fingerprint=result.retrieval_fingerprint,
        hits=[
            KnowledgeHit(
                rank=hit.rank,
                score=hit.score,
                matched_terms=hit.matched_terms,
                record_id=hit.chunk.chunk_id,
            )
            for hit in result.hits
        ],
        records=records,
    )


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    kind: ToolKind
    args_model: type[BaseModel]
    call: Callable[[AgentContext, BaseModel], BaseModel]

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            arguments_schema=self.args_model.model_json_schema(),
        )


def _search_knowledge(context: AgentContext, args: BaseModel) -> BaseModel:
    assert isinstance(args, SearchKnowledgeArgs)
    filters = SearchFilters(
        doc_type=args.doc_type,
        topic=args.topic,
        milestone=args.milestone,
        decision_id=args.decision_id,
    )
    result = search_knowledge(
        context.knowledge, args.query, top_k=context.knowledge_top_k, filters=filters
    )
    return knowledge_observation(result)


def _analyst(spec) -> AgentTool:  # spec: sitescout.analyst.run.ToolSpec
    return AgentTool(
        name=spec.name,
        description=spec.description,
        kind="analyst",
        args_model=spec.args_model,
        call=lambda context, args, spec=spec: spec.call(context.investigation.analyst, args),
    )


def _investigation(tool) -> AgentTool:  # tool: InvestigationTool
    return AgentTool(
        name=tool.name,
        description=tool.description,
        kind="investigation",
        args_model=tool.args_model,
        call=lambda context, args, tool=tool: tool.call(context.investigation, args),
    )


def build_agent_registry() -> dict[str, AgentTool]:
    """The nine capabilities, in a fixed order; a wrong count or name is an error, not a guess."""
    tools = [
        *(_analyst(M9_REGISTRY[name]) for name in M9_TOOL_NAMES),
        *(_investigation(tool) for tool in INVESTIGATION_TOOLS),
        AgentTool(
            name=KNOWLEDGE_TOOL,
            description=(
                "Search SiteScout's project documentation (methodology, definitions, decisions, "
                "limitations, architecture, what SiteScout does not claim). Returns documentation "
                "chunks only, never current site, score or network numbers: use the other tools "
                "for those. Optional exact filters: doc_type, topic, milestone, decision_id."
            ),
            kind="knowledge",
            args_model=SearchKnowledgeArgs,
            call=_search_knowledge,
        ),
    ]
    registry = {tool.name: tool for tool in tools}
    if tuple(registry) != AGENT_TOOL_NAMES or len(registry) != 9:
        raise RuntimeError(f"the agent registry must hold exactly {AGENT_TOOL_NAMES}")
    return registry


AGENT_REGISTRY: dict[str, AgentTool] = build_agent_registry()
AGENT_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = tuple(
    tool.definition() for tool in AGENT_REGISTRY.values()
)
