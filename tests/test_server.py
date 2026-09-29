"""M11 (D-062): the local server behind the page's optional investigation layer —
entirely offline. Every provider here is a scripted ``FakeModel`` or a fake that must never be
called; no test reads a real key, imports an SDK or makes a network call beyond 127.0.0.1."""

import hashlib
import http.client
import json
import re
import threading
from contextlib import contextmanager
from typing import get_args

import pytest

from agent_support import call, final, limits, make_context, reactive, script, stmt
from sitescout.agent import AGENT_TOOL_NAMES
from sitescout.analyst.credentials import CredentialError
from sitescout.analyst.provider import Provider
from sitescout.analyst.provider_common import ProviderError
from sitescout.config import PROJECT_ROOT, load_config
from sitescout.server import (
    CANDIDATE_ID,
    QUESTIONS,
    SECURITY_HEADERS,
    TRACE_LABELS,
    InvestigationRequest,
    InvestigationService,
    Kind,
    _trace_arguments,
    build_server,
)

SYNTHETIC_ID = re.compile(r"^cand-[a-z]$")  # the SYNTHETIC world's short ids (cand-a, ...)
SITE = {"kind": "site_investigation", "candidate_id": "cand-a"}
SCORE_ANSWER = final(stmt("The stored score of cand-a is 71.0.", "CALCULATED", ("cand-a/score",)))


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


@pytest.fixture(scope="module")
def config():
    return load_config()


class NeverCalled(Provider):
    def next_step(self, context):
        raise AssertionError("the provider must not be called")


class Raises(Provider):
    def __init__(self, message: str) -> None:
        self.message = message

    def next_step(self, context):
        raise ProviderError(self.message)


def service(context, config, provider):
    return InvestigationService(context, config.settings.agent, provider, id_pattern=SYNTHETIC_ID)


def answered_model():
    return script(call("get_site", candidate_id="cand-a"), SCORE_ANSWER)


def rejected_model():
    wrong = stmt("The stored score of cand-a is 99.9.", "CALCULATED", ("cand-a/score",))
    return script(call("get_site", candidate_id="cand-a"), final(wrong), final(wrong))


class Client:
    def __init__(self, server):
        self.server = server
        self.port = server.server_address[1]

    def request(self, method, path, body=None, headers=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), data

    def investigate(self, payload, headers=None):
        body = json.dumps(payload) if not isinstance(payload, str | bytes) else payload
        sent = {"Content-Type": "application/json", **(headers or {})}
        return self.request("POST", "/api/investigate", body, sent)


@contextmanager
def serving(config, svc):
    server = build_server(config, svc, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server)
    finally:
        server.shutdown()
        server.server_close()


# --- Requests: a fixed kind and validated identifiers, never free text -----------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "site_investigation", "candidate_id": "cand-a"},
        {"kind": "network_comparison"},
        {"kind": "unknowns", "candidate_id": "cand-b"},
        {"kind": "evidence_explanation", "candidate_id": "cand-a", "group": "grid_evidence"},
    ],
)
def test_each_kind_accepts_exactly_its_fields(context, config, payload):
    assert service(context, config, NeverCalled()).parse(payload) is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "site_investigation", "candidate_id": "cand-a", "question": "anything"},
        {"kind": "site_investigation"},
        {"kind": "network_comparison", "candidate_id": "cand-a"},
        {"kind": "evidence_explanation", "candidate_id": "cand-a"},
        {"kind": "evidence_explanation", "candidate_id": "cand-a", "group": "revenue"},
        {"kind": "unknowns", "candidate_id": "cand-a", "group": "demand"},
        {"kind": "chat", "prompt": "hello"},
        {"kind": "site_investigation", "candidate_id": 7},
        {"kind": "unknowns", "candidate_id": "cand-z"},  # well formed, no such candidate
        ["site_investigation", "cand-a"],
        "site_investigation",
        None,
    ],
)
def test_anything_else_is_refused(context, config, payload):
    assert service(context, config, NeverCalled()).parse(payload) is None


@pytest.mark.parametrize(
    "candidate_id",
    ["cand-a", "CAND-0123456789ab", "cand-0123456789ab ", "../etc/passwd", "cand-0123456789abc"],
)
def test_the_default_pattern_accepts_only_real_candidate_ids(context, config, candidate_id):
    strict = InvestigationService(context, config.settings.agent, NeverCalled())
    assert strict.parse({"kind": "unknowns", "candidate_id": candidate_id}) is None
    assert CANDIDATE_ID.fullmatch("cand-0123456789ab")


def test_the_questions_are_fixed_templates_without_prohibited_wording():
    assert set(QUESTIONS) == set(get_args(Kind))
    for kind in QUESTIONS:
        fields = {"kind": kind}
        if kind != "network_comparison":
            fields["candidate_id"] = "cand-0123456789ab"
        if kind == "evidence_explanation":
            fields["group"] = "grid_evidence"
        question = InvestigationRequest.model_validate(fields).question().lower()
        assert "{" not in question
        for banned in ("feasib", "approv", "best", "recommend", "should", "viab", "revenue"):
            assert banned not in question, (kind, banned)


# --- Responses: only what the run recorded ---------------------------------------------------


def test_an_answered_investigation_returns_the_answer_cited_records_and_trace(context, config):
    outcome = service(context, config, answered_model()).run(
        InvestigationRequest.model_validate(SITE)
    )
    r = outcome.response
    assert r["status"] == "answered" and r["termination"] == "answered"
    assert r["answer"]["direct_answer"][0]["evidence_ids"] == ["cand-a/score"]
    assert set(r["records"]) == {"cand-a/score"}  # only what the answer cites
    assert r["records"]["cand-a/score"]["display"] == "71.0"
    assert [(t["tool"], t["label"], t["arguments"]) for t in r["trace"]] == [
        ("get_site", "Retrieved site record", {"candidate_id": "cand-a"})
    ]
    assert r["validation"] == [{"attempt": 1, "passed": True, "rules": []}]
    assert r["elapsed_display"].endswith(" s")


def test_a_double_rejection_returns_rule_names_and_records_but_no_answer_text(context, config):
    outcome = service(context, config, rejected_model()).run(
        InvestigationRequest.model_validate(SITE)
    )
    r = outcome.response
    assert r["status"] == "fallback" and r["termination"] == "validation_failed"
    assert r["answer"] is None
    assert [v["passed"] for v in r["validation"]] == [False, False]
    assert all(v["rules"] == ["number_not_grounded"] for v in r["validation"])
    assert "cand-a/score" in r["records"]  # the evidence the run gathered, as the tool returned it
    text = json.dumps(r)
    assert "99.9" not in text and "reason" not in r  # never the rejected text or the reason


def test_a_provider_error_never_returns_its_message(context, config):
    outcome = service(context, config, Raises("boom SENTINEL-PROVIDER-MESSAGE")).run(
        InvestigationRequest.model_validate(SITE)
    )
    assert outcome.response["termination"] == "provider_error"
    assert "SENTINEL-PROVIDER-MESSAGE" not in json.dumps(outcome.response)


def test_trace_labels_cover_every_agent_tool():
    assert set(TRACE_LABELS) == set(AGENT_TOOL_NAMES)


def test_trace_arguments_are_allow_listed():
    arguments = {
        "query": "q" * 900,
        "candidate_id": "cand-a",
        "candidate_ids": ["cand-a", "cand-b"],
        "radius_m": 5000,
        "selected_mclp": True,
        "district": "free text a model chose",
        "note": {"nested": "anything"},
    }
    assert _trace_arguments(arguments, SYNTHETIC_ID, 500) == {
        "query": "q" * 500,
        "candidate_id": "cand-a",
        "candidate_ids": ["cand-a", "cand-b"],
        "radius_m": 5000,
        "selected_mclp": True,
    }


def test_limits_come_from_the_agent_settings(context, config):
    # Every call is different (a growing radius), so none is cached and each spends budget.
    model = reactive(
        lambda ctx: call("nearby_sites", candidate_id="cand-a", radius_m=len(ctx.transcript) + 1)
    )
    outcome = service(context, config, model).run(InvestigationRequest.model_validate(SITE))
    assert outcome.response["termination"] == "tool_call_limit"
    assert outcome.result.state.tool_calls == config.settings.agent.max_tool_calls
    assert limits().max_tool_calls == config.settings.agent.max_tool_calls


def test_a_service_without_a_provider_is_unavailable_and_calls_nothing(config):
    def missing_key(settings):
        raise CredentialError("OPENAI_API_KEY is not set")

    svc = InvestigationService.start(config, factory=missing_key)
    request = InvestigationRequest.model_validate({"kind": "network_comparison"})
    assert not svc.available
    assert svc.run(request).response == {"status": "unavailable"}


def test_one_investigation_at_a_time(context, config):
    svc = service(context, config, NeverCalled())
    svc._lock.acquire()
    try:
        assert svc.run(InvestigationRequest.model_validate(SITE)).response == {"status": "busy"}
    finally:
        svc._lock.release()


# --- HTTP ------------------------------------------------------------------------------------


def test_status_is_exactly_one_boolean(context, config):
    with serving(config, service(context, config, NeverCalled())) as client:
        code, headers, body = client.request("GET", "/api/status")
    assert code == 200 and json.loads(body) == {"investigation_available": True}
    assert headers["Cache-Control"] == "no-store"


def test_the_root_redirects_to_the_page(context, config):
    with serving(config, service(context, config, NeverCalled())) as client:
        code, headers, _ = client.request("GET", "/")
    assert code == 302 and headers["Location"] == "/app/index.html"


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/app/index.html", "text/html; charset=utf-8"),
        ("/app/fonts/IBMPlexSans-Regular.woff2", "font/woff2"),
        ("/app/fonts/IBMPlexMono-Regular.woff2", "font/woff2"),
        ("/data/export/sitescout.js", "text/javascript; charset=utf-8"),
        ("/data/export/sitescout.json", "application/json"),
        ("/reports/evaluation.md", "text/plain; charset=utf-8"),
    ],
)
def test_allow_listed_files_are_served_with_their_types(context, config, path, content_type):
    if not (PROJECT_ROOT / path.lstrip("/")).is_file():
        pytest.skip(f"{path} is not in this checkout")
    with serving(config, service(context, config, NeverCalled())) as client:
        code, headers, body = client.request("GET", path)
    assert code == 200 and headers["Content-Type"] == content_type
    assert body == (PROJECT_ROOT / path.lstrip("/")).read_bytes()


def test_the_export_always_arrives_whole(context, config):
    # Under HTTP/1.0 the server closed the socket right after the 0.6 MB export, and on
    # Windows loopback its tail was often lost (about 1 request in 3), so the page loaded
    # without data. The server now speaks HTTP/1.1 and the client closes.
    path = PROJECT_ROOT / "data" / "export" / "sitescout.js"
    if not path.is_file():
        pytest.skip("the export is not in this checkout")
    expected = path.read_bytes()
    with serving(config, service(context, config, NeverCalled())) as client:
        bodies = {client.request("GET", "/data/export/sitescout.js")[2] for _ in range(30)}
    assert bodies == {expected}


def test_a_connection_is_kept_open_between_requests(context, config):
    with serving(config, service(context, config, NeverCalled())) as client:
        connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=30)
        host = {"Host": f"127.0.0.1:{client.port}"}
        for _ in range(2):
            connection.request("GET", "/api/status", headers=host)
            response = connection.getresponse()
            assert response.version == 11 and response.status == 200
            assert json.loads(response.read()) == {"investigation_available": True}
        connection.close()


def test_a_refused_post_closes_its_connection(context, config):
    # Part of a refused body may stay unread; it must never be parsed as a next request.
    with serving(config, service(context, config, NeverCalled())) as client:
        code, headers, _ = client.request("POST", "/api/investigate", "x" * 5000,
                                          {"Content-Type": "application/json"})  # fmt: skip
    assert code == 413 and headers.get("Connection") == "close"


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/config/settings.yaml",
        "/data/processed/network.json",
        "/reports/../config/settings.yaml",
        "/reports/%2e%2e/CLAUDE.md",
        "/reports/%2e%2e/.env",
        "/reports/agent_eval.txt",
        "/app/",
        "/src/sitescout/server.py",
        "/api/investigate",
    ],
)
def test_unlisted_and_traversal_paths_are_not_found(context, config, path):
    with serving(config, service(context, config, NeverCalled())) as client:
        code, _, _ = client.request("GET", path)
    assert code == 404


def test_a_foreign_host_is_refused(context, config):
    with serving(config, service(context, config, NeverCalled())) as client:
        get, _, _ = client.request("GET", "/api/status", host="attacker.example")
        post, _, _ = client.request(
            "POST", "/api/investigate", json.dumps(SITE),
            {"Content-Type": "application/json"}, host="attacker.example",
        )  # fmt: skip
    assert get == 403 and post == 403


def test_a_cross_origin_post_is_refused_and_a_same_origin_one_runs(context, config):
    with serving(config, service(context, config, answered_model())) as client:
        foreign, _, _ = client.investigate(SITE, {"Origin": "https://attacker.example"})
        same, _, body = client.investigate(SITE, {"Origin": f"http://127.0.0.1:{client.port}"})
    assert foreign == 403
    assert same == 200 and json.loads(body)["status"] == "answered"


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        (json.dumps(SITE), {"Content-Type": "text/plain"}, 415),
        (json.dumps(SITE), {"Content-Type": "application/x-www-form-urlencoded"}, 415),
        ("x" * 5000, {"Content-Type": "application/json"}, 413),
        ("{not json", {"Content-Type": "application/json"}, 400),
        (json.dumps({"kind": "chat", "prompt": "hi"}), {"Content-Type": "application/json"}, 400),
        (json.dumps({"kind": "unknowns", "candidate_id": "cand-z"}),
         {"Content-Type": "application/json"}, 400),
    ],
)  # fmt: skip
def test_malformed_posts_are_refused_before_the_provider(context, config, body, headers, expected):
    with serving(config, service(context, config, NeverCalled())) as client:
        code, _, response = client.request("POST", "/api/investigate", body, headers)
    assert code == expected
    assert json.loads(response) == {"status": "invalid_request"}


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Content-Type": "application/json"}, 413),
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/json", "Origin": "http://attacker.example"}, 403),
    ],
)
def test_a_refused_post_always_reaches_the_client(context, config, headers, expected):
    # Refusing without reading the body used to reset the connection now and then (about 3%
    # of runs on Windows), so the client lost the reply; the server now drains it first.
    body = "x" * 5000 if expected == 413 else "x" * 1500
    with serving(config, service(context, config, NeverCalled())) as client:
        codes = {client.request("POST", "/api/investigate", body, headers)[0] for _ in range(200)}
    assert codes == {expected}


def test_an_unavailable_service_answers_unavailable(config):
    svc = InvestigationService(None, config.settings.agent, None)
    with serving(config, svc) as client:
        status = json.loads(client.request("GET", "/api/status")[2])
        code, _, body = client.investigate({"kind": "network_comparison"})
    assert status == {"investigation_available": False}
    assert code == 503 and json.loads(body) == {"status": "unavailable"}


def test_security_headers_are_on_every_response(context, config):
    with serving(config, service(context, config, NeverCalled())) as client:
        responses = [client.request("GET", p) for p in ("/api/status", "/nothing", "/")]
    for _, headers, _ in responses:
        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value


def test_the_server_binds_loopback_only(context, config):
    server = build_server(config, service(context, config, NeverCalled()), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


# --- Nothing reaches the browser or the disk -------------------------------------------------


def test_a_sentinel_key_never_reaches_a_response_or_the_app(context, config, monkeypatch):
    sentinel = "sentinel-credential-offline-0000"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    bodies = []
    for model in (answered_model(), rejected_model(), Raises(f"401 invalid key {sentinel}")):
        with serving(config, service(context, config, model)) as client:
            bodies.append(client.request("GET", "/api/status")[2])
            bodies.append(client.investigate(SITE)[2])
    assert all(sentinel.encode() not in body for body in bodies)
    for path in (PROJECT_ROOT / "app").rglob("*"):  # every file, binary fonts included (D-065)
        assert not path.is_file() or sentinel.encode() not in path.read_bytes()


def _snapshot(*roots):
    return {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for root in roots
        if root.exists()
        for path in sorted(root.rglob("*"))
    }


def test_an_investigation_writes_nothing(context, config, analyst_world):
    _, processed, _ = analyst_world
    roots = [PROJECT_ROOT / d for d in ("app", "reports", "data/export", "data/processed")]
    before = _snapshot(*roots, processed)
    for model in (answered_model(), rejected_model()):
        with serving(config, service(context, config, model)) as client:
            client.investigate(SITE)
    assert _snapshot(*roots, processed) == before


def test_the_export_is_unchanged(context, config):
    files = [PROJECT_ROOT / "data" / "export" / n for n in ("sitescout.json", "sitescout.js")]
    present = [f for f in files if f.is_file()]
    if not present:
        pytest.skip("no committed export in this checkout")
    before = [hashlib.sha256(f.read_bytes()).hexdigest() for f in present]
    with serving(config, service(context, config, answered_model())) as client:
        client.investigate(SITE)
    assert [hashlib.sha256(f.read_bytes()).hexdigest() for f in present] == before


def test_the_server_module_writes_no_file():
    text = (PROJECT_ROOT / "src" / "sitescout" / "server.py").read_text(encoding="utf-8")
    for call_name in ("write_text", "write_bytes", "open(", "mkdir", "unlink", "localStorage"):
        assert call_name not in text


def test_the_serve_script_only_parses_arguments_and_calls_src():
    text = (PROJECT_ROOT / "scripts" / "serve.py").read_text(encoding="utf-8")
    assert "from sitescout.server import" in text
    assert "http.server" not in text and "os.environ" not in text and "dotenv" not in text
