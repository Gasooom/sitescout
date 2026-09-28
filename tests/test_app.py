"""app/index.html is a read-only view over the export (SPEC §10; D-048), and
app/investigation.js is its optional, locally served investigation layer (D-062)."""

import json
import re

import pytest

from sitescout.config import PROJECT_ROOT

PAGE = (PROJECT_ROOT / "app" / "index.html").read_text(encoding="utf-8")
CLIENT = (PROJECT_ROOT / "app" / "investigation.js").read_text(encoding="utf-8")
EXPORT = PROJECT_ROOT / "data" / "export" / "sitescout.json"


def test_the_page_loads_the_export_by_script_so_it_works_from_disk():
    assert '<script src="../data/export/sitescout.js"></script>' in PAGE
    assert "window.SITESCOUT" in PAGE
    assert "fetch(" not in PAGE and "XMLHttpRequest" not in PAGE


def test_the_page_uses_no_external_resource():
    urls = set(re.findall(r"https?://[^\s\"'<>]+", PAGE))
    assert urls == {"http://www.w3.org/2000/svg"}  # the SVG namespace, not a request


def test_the_page_inserts_text_safely():
    assert "innerHTML" not in PAGE and "outerHTML" not in PAGE


@pytest.mark.skipif(not EXPORT.is_file(), reason="run scripts/export.py first")
@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_no_result_from_the_export_is_written_into_the_page(text):
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    headline = data["headline"]
    values = {headline["exact_vs_greedy_gap"]}
    for selection in headline["selections"]:
        values |= {selection["population_display"], selection["mean_score_display"]}
    values |= {site["score"] for site in data["sites"] if site["selected_mclp"]}
    values |= {site["unique_coverage"] for site in data["sites"] if site["unique_coverage"]}
    written = sorted(value for value in values if value in text)
    assert written == []
    for disclaimer in data["meta"]["disclaimers"]:
        assert disclaimer not in text  # disclaimers come from the export too


# --- The optional investigation layer (D-062) ------------------------------------------------


def test_the_page_loads_the_investigation_client_locally_after_the_export():
    export_tag = PAGE.index('<script src="../data/export/sitescout.js"></script>')
    client_tag = PAGE.index('<script src="investigation.js"></script>')
    assert export_tag < client_tag


def test_the_client_requests_only_its_two_local_endpoints():
    targets = re.findall(r"fetch\(\s*\"([^\"]*)\"", CLIENT)
    assert sorted(set(targets)) == ["/api/investigate", "/api/status"]
    assert CLIENT.count("fetch(") == len(targets)  # every request names a literal endpoint
    for other in ("XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import("):
        assert other not in CLIENT


def test_the_client_makes_no_request_when_the_page_is_opened_from_disk():
    assert CLIENT.index('location.protocol === "file:"') < CLIENT.index("fetch(")


def test_the_client_uses_no_external_resource():
    assert re.findall(r"https?://", CLIENT) == []


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_text_is_inserted_safely_and_nothing_is_stored(text):
    unsafe = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")
    storage = ("new Function", "localStorage", "sessionStorage", "indexedDB", "document.cookie")
    for banned in unsafe + storage:
        assert banned not in text, banned


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_no_credential_or_provider_detail_reaches_the_browser(text):
    for banned in ("OPENAI", "ANTHROPIC", "api_key", "apiKey", "Authorization", "Bearer", "sk-"):
        assert banned not in text, banned


@pytest.mark.parametrize("text", [PAGE, CLIENT], ids=["index.html", "investigation.js"])
def test_ai_is_named_only_where_it_adds_context(text):
    uses = re.findall(r"\bAI\b.{0,40}", text)
    assert all(u.startswith(("AI Analyst", "AI investigation unavailable")) for u in uses), uses
    lowered = text.lower()
    for banned in (
        "ai-powered",
        "powered by ai",
        "ai-driven",
        "generative",
        "chatbot",
        "thinking",
        "reasoning…",
        "sparkle",
        "@keyframes",
        "gradient(",
    ):
        assert banned not in lowered, banned  # fmt: skip


def test_the_client_shows_only_what_the_server_returns():
    # The loading line is the one honest state while a request runs; results are rendered only
    # from the response, and a fallback shows retrieved records, never generated text.
    assert CLIENT.count("Investigating…") == 1
    assert "Validated summary unavailable." in CLIENT
    for field in ("data.answer", "data.records", "data.trace", "data.validation"):
        assert field in CLIENT
    assert "reason" not in CLIENT  # the server never sends a fallback's reason
