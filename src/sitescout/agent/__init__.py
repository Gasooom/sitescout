"""Milestone 10, Phase 3 (D-058): the agent loop over nine read-only capabilities.

A provider (M9's ``Provider`` interface) chooses, one step at a time, which of nine
capabilities to call (six M9 analyst tools, ``network_summary``, ``nearby_sites`` and
``search_knowledge``) or gives its final answer. The loop validates and runs each action,
records the observation in an explicit ``AgentState``, enforces hard limits, reuses identical
results, and ends with a validated answer or M9's deterministic fallback. It reads structured
facts only through the deterministic tools and project knowledge only through the knowledge
index, writes nothing and calls no model or network itself.
"""

from sitescout.agent.loop import ANSWER_RETRIES, AgentLimits, AgentResult, run_agent
from sitescout.agent.registry import (
    AGENT_REGISTRY,
    AGENT_TOOL_DEFINITIONS,
    AGENT_TOOL_NAMES,
    AgentContext,
    AgentTool,
    KnowledgeObservation,
    SearchKnowledgeArgs,
)
from sitescout.agent.state import (
    Action,
    AgentState,
    Observation,
    ObservationError,
    TerminationReason,
    records_in,
    session_from_observations,
)
from sitescout.agent.validate import (
    AgentAnswer,
    AgentStatement,
    parse_answer,
    validate_agent_answer,
)

__all__ = [
    "AGENT_REGISTRY",
    "AGENT_TOOL_DEFINITIONS",
    "AGENT_TOOL_NAMES",
    "ANSWER_RETRIES",
    "Action",
    "AgentAnswer",
    "AgentContext",
    "AgentLimits",
    "AgentResult",
    "AgentState",
    "AgentStatement",
    "AgentTool",
    "KnowledgeObservation",
    "Observation",
    "ObservationError",
    "SearchKnowledgeArgs",
    "TerminationReason",
    "parse_answer",
    "records_in",
    "run_agent",
    "session_from_observations",
    "validate_agent_answer",
]
