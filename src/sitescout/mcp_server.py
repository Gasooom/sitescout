"""Milestone 16 (D-071): SiteScout's read-only tools and its answer validator over MCP.

Any Model Context Protocol client can call the nine capabilities the agent uses (the eight
deterministic tools and ``search_knowledge``) and one more, ``validate_answer``: it checks a
draft answer with the agent's own validator against the records **this session** has fetched,
the same rule the agent loop applies ("only records from this run's observations are ever
trusted"). A client can therefore use SiteScout's facts and be held to SiteScout's rules.

Two layers:

- ``SiteScoutTools`` holds every decision and needs no MCP package: it lists the tools (each
  input schema is the tool's own argument model, so there is one definition, not two), checks
  arguments, calls the tool, keeps the session's evidence ledger and validates answers.
- ``build_server`` wraps it in the SDK's low-level server. Transport is stdio only: one
  process serves one client, so one process is one session, and no network port is opened.

Nothing here computes a score, a coverage figure or a selection, writes a file or calls a
model. Every tool is read-only; the ledger lives in memory and ends with the process.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sitescout.agent import (
    AGENT_REGISTRY,
    AgentAnswer,
    AgentContext,
    records_in,
    validate_agent_answer,
)
from sitescout.analyst.tools import AnalystError, ComparisonField
from sitescout.analyst.validate import ToolRecords
from sitescout.config import Config
from sitescout.evidence import EvidenceRecord
from sitescout.knowledge import KnowledgeError

if TYPE_CHECKING:  # the optional extra is imported only where the server is built
    from mcp.server.lowlevel import Server

log = logging.getLogger(__name__)

SERVER_NAME = "sitescout"
VALIDATE_TOOL = "validate_answer"

INSTRUCTIONS = (
    "SiteScout ranks candidate EV charging sites in Rwanda and selects a 30-site network from "
    "public data. Its tools are read-only and deterministic: they return stored facts as "
    "evidence records (each with an id, a type of RETRIEVED_FACT, CALCULATED, INFERRED or "
    "UNKNOWN, and a display value), never a new score or selection. Use search_knowledge for "
    "methodology and decisions, the other tools for site and network facts. Before showing an "
    "answer, send it to validate_answer: every number must be copied from a record the "
    "statement cites, documentation claims must quote their chunk verbatim, UNKNOWN stays "
    "unknown, and only records fetched in this session count. Actual grid connection "
    "feasibility requires utility confirmation."
)

VALIDATE_DESCRIPTION = (
    "Check a draft answer with SiteScout's own validator before showing it. The answer has a "
    "direct_answer section and optional evidence, interpretation, unknowns and "
    "next_investigation sections; each statement has text, a kind (RETRIEVED_FACT, "
    "CALCULATED, INFERRED or UNKNOWN), the evidence_ids it relies on and, for documentation "
    "claims, verbatim quotes. Only records returned by the other tools in this session count "
    "as evidence. Returns passed and every issue found; it never changes the answer."
)


class ValidateArgs(BaseModel):
    """The one argument of ``validate_answer``: the draft, in the agent's answer schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: AgentAnswer


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool as a client sees it. Every SiteScout tool is read-only."""

    name: str
    description: str
    input_schema: dict[str, Any]


class UnknownToolError(LookupError):
    """A call names a tool that does not exist: a protocol error, not a tool result."""


def _summarise(error: ValidationError) -> str:
    """The first problems in a rejected input, in the agent loop's own wording."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}"
        for item in error.errors()[:5]
    )


class SiteScoutTools:
    """The tools, one session's evidence ledger and the validator, without any transport."""

    def __init__(self, context: AgentContext, *, min_quote_words: int) -> None:
        self._context = context
        self._min_quote_words = min_quote_words
        self._registry = AGENT_REGISTRY
        self._records: dict[str, EvidenceRecord] = {}
        self._comparisons: dict[str, ComparisonField] = {}

    @classmethod
    def load(cls, config: Config, processed_dir: Path) -> SiteScoutTools:
        """Read the processed outputs and build the knowledge index once, at start-up."""
        return cls(
            AgentContext.load(config, processed_dir),
            min_quote_words=config.settings.agent.min_quote_words,
        )

    @property
    def session_record_ids(self) -> tuple[str, ...]:
        """Every evidence id this session has been shown, in the order first seen."""
        return tuple(self._records)

    def list_tools(self) -> list[ToolSpec]:
        """The agent's nine capabilities in the registry's order, then ``validate_answer``."""
        specs = [
            ToolSpec(tool.name, tool.description, tool.args_model.model_json_schema())
            for tool in self._registry.values()
        ]
        specs.append(
            ToolSpec(VALIDATE_TOOL, VALIDATE_DESCRIPTION, ValidateArgs.model_json_schema())
        )
        return specs

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
        """Run one tool. Returns the JSON result and whether it is an error the caller can
        correct (bad arguments, a site that does not exist). Raises ``UnknownToolError`` for a
        name that is not a tool; any other failure propagates, as it would stop the agent."""
        if name == VALIDATE_TOOL:
            return self._validate(arguments or {})
        tool = self._registry.get(name)
        if tool is None:
            raise UnknownToolError(f"unknown tool {name!r}; the available tools are {self.names}")
        try:
            args = tool.args_model.model_validate(arguments or {})
        except ValidationError as failure:
            return {"error": "malformed_arguments", "message": _summarise(failure)}, True
        try:
            result = tool.call(self._context, args).model_dump(mode="json")
        except (AnalystError, KnowledgeError) as failure:
            message = getattr(failure, "message", None) or str(failure)
            code = getattr(failure, "code", None)
            return {"error": "tool_error", "code": code, "message": message}, True
        found, compared = records_in(result)
        self._records.update(found)
        self._comparisons.update(compared)
        return result, False

    @property
    def names(self) -> list[str]:
        return [spec.name for spec in self.list_tools()]

    def _validate(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            args = ValidateArgs.model_validate(arguments)
        except ValidationError as failure:
            return {"error": "malformed_answer", "message": _summarise(failure)}, True
        session = ToolRecords(
            records=tuple(self._records.values()), comparisons=tuple(self._comparisons.values())
        )
        outcome = validate_agent_answer(args.answer, session, min_quote_words=self._min_quote_words)
        return {
            "passed": outcome.passed,
            "issues": [issue.model_dump(mode="json") for issue in outcome.errors],
            "records_in_session": len(self._records),
        }, False


def build_server(tools: SiteScoutTools) -> Server:
    """The SDK's low-level server over ``tools``: list and call, nothing else."""
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import MCPError

    read_only = types.ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    annotations=read_only,
                )
                for spec in tools.list_tools()
            ]
        )

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            payload, is_error = tools.call_tool(params.name, params.arguments)
        except UnknownToolError as failure:
            raise MCPError(types.INVALID_PARAMS, str(failure)) from None
        log.info("tool %s -> %s", params.name, "error" if is_error else "ok")
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=is_error,
        )

    return Server(
        SERVER_NAME,
        version=version("sitescout"),
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio(server: Server) -> None:
    """Serve one client over this process's stdin and stdout until it disconnects."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
