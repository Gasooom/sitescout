"""Milestone 9: the SiteScout Analyst (SPEC §11).

Phase 1 holds the deterministic, read-only tools (``sitescout.analyst.tools``). Phase 2 adds
the answer schema and the offline validator (``sitescout.analyst.validate``). Phase 3a adds
the model provider interface (``sitescout.analyst.provider``), a scripted fake model for
tests (``sitescout.analyst.fake_model``) and the tool-calling loop
(``sitescout.analyst.run``) that ties every earlier phase together with one corrective
retry and a deterministic fallback. No phase so far uses a real model, an API or network
access, and none reads an environment variable; ``scripts/ask.py --tools-only`` calls the
tools directly, without going through the loop.
"""

from sitescout.analyst.fake_model import FakeModel, ScriptExhausted
from sitescout.analyst.provider import (
    ModelContext,
    ModelStep,
    Provider,
    ToolCallRecord,
    ToolCallRequest,
    ToolDefinition,
)
from sitescout.analyst.run import (
    REGISTRY,
    TOOL_DEFINITIONS,
    FallbackAnswer,
    RunLimits,
    RunResult,
    run_analyst,
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
    Statement,
    ToolRecords,
    ValidationIssue,
    ValidationResult,
    validate_answer,
)

__all__ = [
    "REGISTRY",
    "TOOLS",
    "TOOL_DEFINITIONS",
    "AnalystData",
    "AnalystError",
    "Answer",
    "FakeModel",
    "FallbackAnswer",
    "FindQuery",
    "ModelContext",
    "ModelStep",
    "Provider",
    "RunLimits",
    "RunResult",
    "ScriptExhausted",
    "Statement",
    "ToolCallRecord",
    "ToolCallRequest",
    "ToolDefinition",
    "ToolRecords",
    "ValidationIssue",
    "ValidationResult",
    "compare_sites",
    "explain_score",
    "find_sites",
    "generate_brief",
    "get_site",
    "network_contribution",
    "run_analyst",
    "validate_answer",
]
