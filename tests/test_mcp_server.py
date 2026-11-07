"""Milestone 16 (D-071): the MCP server is the agent's registry plus the agent's validator.

Every test runs on the SYNTHETIC analyst world and the real knowledge index; no model, API or
network is involved. The last tests drive the real SDK server through its in-memory client."""

import asyncio
import json
import subprocess
import sys

import pytest

from agent_support import make_context
from sitescout.agent import AGENT_REGISTRY, AGENT_TOOL_NAMES, AgentAnswer
from sitescout.mcp_server import (
    VALIDATE_TOOL,
    SiteScoutTools,
    UnknownToolError,
    ValidateArgs,
)


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture
def tools(context, analyst_world):
    config = analyst_world[0]
    return SiteScoutTools(context, min_quote_words=config.settings.agent.min_quote_words)


def first_record(payload, prefix: str = "") -> dict:
    """The first evidence record in a tool result whose id starts with ``prefix``."""
    found: list[dict] = []

    def walk(value):
        if isinstance(value, dict):
            if {"id", "type", "display", "evidence"} <= set(value) and value["id"].startswith(
                prefix
            ):
                found.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    assert found, f"no record starting with {prefix!r}"
    return found[0]


def answer_citing(record: dict, text: str | None = None) -> dict:
    statement = {
        "text": text or f"The value is {record['display']}.",
        "kind": record["type"],
        "evidence_ids": [record["id"]],
    }
    return {"answer": {"direct_answer": [statement]}}


# --- The tool list is the agent's registry, not a second copy of it ----------------------------


def test_the_tools_are_the_agent_registry_then_the_validator(tools):
    specs = tools.list_tools()
    assert [s.name for s in specs] == [*AGENT_TOOL_NAMES, VALIDATE_TOOL]
    for spec in specs[:-1]:
        tool = AGENT_REGISTRY[spec.name]
        assert spec.description == tool.description
        assert spec.input_schema == tool.args_model.model_json_schema()  # one definition
    assert specs[-1].input_schema == ValidateArgs.model_json_schema()


def test_the_validator_takes_the_agents_own_answer_schema():
    schema = ValidateArgs.model_json_schema()
    assert schema["required"] == ["answer"] and schema["additionalProperties"] is False
    assert ValidateArgs.model_fields["answer"].annotation is AgentAnswer


# --- Calls -------------------------------------------------------------------------------------


def test_a_tool_call_returns_the_tools_json_and_fills_the_ledger(tools):
    assert tools.session_record_ids == ()
    payload, is_error = tools.call_tool("network_summary", {})
    assert not is_error and payload["tool"] == "network_summary"
    json.dumps(payload)  # plain JSON, as the client receives it
    assert first_record(payload, "network/")["id"] in tools.session_record_ids


def test_bad_arguments_and_unknown_sites_are_correctable_errors(tools):
    payload, is_error = tools.call_tool("get_site", {"candidate": "x"})
    assert is_error and payload["error"] == "malformed_arguments"
    payload, is_error = tools.call_tool("get_site", {"candidate_id": "cand-doesnotexist"})
    assert (
        is_error and payload["error"] == "tool_error" and "cand-doesnotexist" in payload["message"]
    )
    assert tools.session_record_ids == ()  # an error adds no evidence


def test_an_unknown_tool_is_not_a_tool_result(tools):
    with pytest.raises(UnknownToolError, match="drop_tables"):
        tools.call_tool("drop_tables", {})


# --- validate_answer: the agent's rules, against this session's evidence only ------------------


def test_a_grounded_answer_passes(tools):
    payload, _ = tools.call_tool("network_summary", {})
    record = first_record(payload, "network/")
    result, is_error = tools.call_tool(VALIDATE_TOOL, answer_citing(record))
    assert not is_error
    assert result == {
        "passed": True,
        "issues": [],
        "records_in_session": len(tools.session_record_ids),
    }


def test_a_changed_number_is_rejected(tools):
    payload, _ = tools.call_tool("network_summary", {})
    record = first_record(payload, "network/mclp/population_covered_share")
    result, _ = tools.call_tool(
        VALIDATE_TOOL, answer_citing(record, "The optimized network covers 61.3% of people.")
    )
    assert not result["passed"]
    assert {issue["rule"] for issue in result["issues"]} == {"number_not_grounded"}


def test_a_record_not_fetched_in_this_session_is_not_evidence(context, analyst_world):
    config = analyst_world[0]
    fetching = SiteScoutTools(context, min_quote_words=config.settings.agent.min_quote_words)
    payload, _ = fetching.call_tool("network_summary", {})
    record = first_record(payload, "network/")
    assert fetching.call_tool(VALIDATE_TOOL, answer_citing(record))[0]["passed"]
    # The same record exists in the data, but a second session never fetched it.
    fresh = SiteScoutTools(context, min_quote_words=config.settings.agent.min_quote_words)
    result, _ = fresh.call_tool(VALIDATE_TOOL, answer_citing(record))
    assert not result["passed"] and result["records_in_session"] == 0


def test_a_quotation_must_be_verbatim(tools):
    payload, _ = tools.call_tool("search_knowledge", {"query": "confidence level percentage"})
    chunk = first_record(payload, "kb/")
    words = chunk["display"].split()
    quote = " ".join(words[:6])
    statement = {
        "text": "The documentation describes confidence.",
        "kind": "RETRIEVED_FACT",
        "evidence_ids": [chunk["id"]],
    }
    ok = {"answer": {"direct_answer": [{**statement, "quotes": [quote]}]}}
    assert tools.call_tool(VALIDATE_TOOL, ok)[0]["passed"]
    altered = {"answer": {"direct_answer": [{**statement, "quotes": [quote + " never"]}]}}
    assert not tools.call_tool(VALIDATE_TOOL, altered)[0]["passed"]


def test_a_malformed_answer_is_a_correctable_error(tools):
    payload, is_error = tools.call_tool(VALIDATE_TOOL, {"answer": {"direct_answer": []}})
    assert is_error and payload["error"] == "malformed_answer"
    payload, is_error = tools.call_tool(VALIDATE_TOOL, {"answer": {}, "verdict": "pass"})
    assert is_error and payload["error"] == "malformed_answer"


# --- The real SDK server, through its in-memory client -----------------------------------------


def _client_session(tools, script):
    pytest.importorskip("mcp")
    from mcp import Client

    from sitescout.mcp_server import build_server

    async def run():
        async with Client(build_server(tools)) as client:
            return await script(client)

    return asyncio.run(run())


def test_the_sdk_server_lists_every_tool_as_read_only(tools):
    async def script(client):
        return (await client.list_tools()).tools

    listed = _client_session(tools, script)
    assert [t.name for t in listed] == [*AGENT_TOOL_NAMES, VALIDATE_TOOL]
    for tool in listed:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


def test_the_sdk_server_fetches_then_validates_in_one_session(tools):
    async def script(client):
        summary = await client.call_tool("network_summary", {})
        record = first_record(summary.structured_content, "network/")
        good = await client.call_tool(VALIDATE_TOOL, answer_citing(record))
        bad = await client.call_tool(
            VALIDATE_TOOL, answer_citing(record, "The network covers 99.9% of people.")
        )
        missing = await client.call_tool("get_site", {"candidate_id": "cand-doesnotexist"})
        return summary, good, bad, missing

    summary, good, bad, missing = _client_session(tools, script)
    assert (
        not summary.is_error and json.loads(summary.content[0].text) == summary.structured_content
    )
    assert good.structured_content["passed"] is True
    assert bad.structured_content["passed"] is False
    assert missing.is_error is True


def test_the_sdk_server_answers_an_unknown_tool_with_a_protocol_error(tools):
    from mcp import MCPError

    async def script(client):
        with pytest.raises(MCPError, match="unknown tool"):
            await client.call_tool("drop_tables", {})
        return True

    assert _client_session(tools, script)


def test_importing_the_server_module_does_not_import_the_sdk():
    # The tools and the ledger need no MCP package; only build_server and serve_stdio import it.
    code = "import sys, sitescout.mcp_server; print('mcp' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
