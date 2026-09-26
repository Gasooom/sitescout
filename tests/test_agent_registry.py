"""M10 Phase 3 (D-058): the nine agent-visible capabilities and the isolation of the agent layer."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from sitescout.agent import (
    AGENT_REGISTRY,
    AGENT_TOOL_DEFINITIONS,
    AGENT_TOOL_NAMES,
    SearchKnowledgeArgs,
)
from sitescout.analyst import REGISTRY, TOOLS
from sitescout.analyst.provider import ToolDefinition
from sitescout.config import PROJECT_ROOT
from sitescout.investigation import INVESTIGATION_TOOLS

M9_SIX = (
    "find_sites",
    "get_site",
    "compare_sites",
    "explain_score",
    "network_contribution",
    "generate_brief",
)


def test_there_are_exactly_nine_capabilities_in_a_fixed_order():
    assert len(AGENT_REGISTRY) == 9
    assert tuple(AGENT_REGISTRY) == AGENT_TOOL_NAMES
    assert (*M9_SIX, "network_summary", "nearby_sites", "search_knowledge") == AGENT_TOOL_NAMES


def test_six_analyst_two_investigation_one_knowledge():
    kinds = [tool.kind for tool in AGENT_REGISTRY.values()]
    assert kinds == ["analyst"] * 6 + ["investigation"] * 2 + ["knowledge"]
    assert {t.name for t in AGENT_REGISTRY.values() if t.kind == "knowledge"} == {
        "search_knowledge"
    }


def test_the_m9_six_tool_registry_is_unchanged():
    assert TOOLS == M9_SIX
    assert tuple(REGISTRY) == M9_SIX and len(REGISTRY) == 6
    assert not {"network_summary", "nearby_sites", "search_knowledge"} & set(REGISTRY)


def test_the_investigation_tools_are_the_phase_2_ones():
    assert [t.name for t in INVESTIGATION_TOOLS] == ["network_summary", "nearby_sites"]


def test_each_agent_tool_wraps_the_tool_it_names():
    for name in M9_SIX:
        assert AGENT_REGISTRY[name].args_model is REGISTRY[name].args_model
        assert AGENT_REGISTRY[name].description == REGISTRY[name].description
    for tool in INVESTIGATION_TOOLS:
        assert AGENT_REGISTRY[tool.name].args_model is tool.args_model


def test_the_provider_is_shown_nine_tool_definitions():
    assert [d.name for d in AGENT_TOOL_DEFINITIONS] == list(AGENT_TOOL_NAMES)
    for definition in AGENT_TOOL_DEFINITIONS:
        assert isinstance(definition, ToolDefinition) and definition.description
        assert definition.arguments_schema["type"] == "object"
        assert definition.arguments_schema.get("additionalProperties") is False


def test_search_knowledge_takes_a_query_and_exact_filters_but_not_the_number_of_chunks():
    schema = AGENT_REGISTRY["search_knowledge"].definition().arguments_schema
    assert set(schema["properties"]) == {"query", "doc_type", "topic", "milestone", "decision_id"}
    assert schema["required"] == ["query"]
    with pytest.raises(ValidationError):
        SearchKnowledgeArgs(query="confidence", top_k=10)
    with pytest.raises(ValidationError):
        SearchKnowledgeArgs(query="")
    with pytest.raises(ValidationError):
        SearchKnowledgeArgs(query="x", milestone="6")


def test_the_knowledge_tool_description_keeps_numbers_with_the_structured_tools():
    text = AGENT_REGISTRY["search_knowledge"].description
    assert "never current site, score or network numbers" in text


# --- Isolation ------------------------------------------------------------------------------------

PACKAGE = sorted((PROJECT_ROOT / "src" / "sitescout" / "agent").glob("*.py"))
FORBIDDEN_TOP = {
    "openai", "anthropic", "httpx", "httpx2", "requests", "urllib", "http", "socket", "ssl",
    "subprocess", "langchain", "langgraph", "chromadb", "faiss",
}  # fmt: skip
FORBIDDEN_ANALYST = {
    "openai_provider", "anthropic_provider", "provider_common", "factory", "credentials", "ask",
    "scenarios",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


@pytest.mark.parametrize("path", PACKAGE, ids=lambda p: p.name)
def test_the_agent_imports_no_sdk_provider_adapter_or_network_code(path):
    for module in _imports(path):
        parts = module.split(".")
        assert parts[0] not in FORBIDDEN_TOP, module
        if module.startswith("sitescout.analyst."):
            assert parts[2] not in FORBIDDEN_ANALYST, module


@pytest.mark.parametrize("path", PACKAGE, ids=lambda p: p.name)
def test_the_agent_never_writes_or_reads_the_environment(path):
    source = path.read_text(encoding="utf-8")
    for call in (
        "write_bytes", "write_text", "open(", "mkdir(", "os.environ", "getenv", "to_parquet",
        "write_layer", "write_json", "subprocess", "eval(", "exec(", "__import__",
    ):  # fmt: skip
        assert call not in source, f"{path.name} uses {call}"


def test_importing_the_agent_loads_no_sdk_and_no_provider_adapter():
    code = (
        "import sys, sitescout.agent\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('openai', 'anthropic', 'httpx', "
        "'httpx2') or m.startswith(('sitescout.analyst.openai_provider', "
        "'sitescout.analyst.anthropic_provider', 'sitescout.analyst.provider_common', "
        "'sitescout.analyst.factory', 'sitescout.analyst.credentials'))]\n"
        "print(bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
