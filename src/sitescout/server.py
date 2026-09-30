"""Milestone 11 (D-062): the local server behind the decision page's optional investigation layer.

The page (``app/index.html``) is complete on its own and still opens from disk. Served by this
module on 127.0.0.1, it can also ask the M10 agent to investigate a site or the network. Only
when started with an explicit public hostname (the Render deployment, D-066) does it listen
publicly, for that hostname alone, and answer the configured cross origins (CORS):

- ``GET /api/status`` answers ``{"investigation_available": bool}`` and nothing else;
- ``POST /api/investigate`` takes a fixed investigation *kind* plus validated identifiers, never
  free text. The server writes the question from a fixed template, runs the unmodified agent
  (``sitescout.agent_provider.ask``) within the ``agent:`` limits, and returns what the run
  recorded: the validated answer or the fallback's evidence records, the cited records, the
  tool-call trace and the validation outcome (rule names only).

Nothing here is written anywhere: an investigation lives only in this request and its response.
The provider's key stays in this process (read by ``sitescout.analyst.credentials``); no
response carries it, a fallback's free-text reason, an exception message or rejected answer
text. Static files come from an explicit allow-list. The deterministic decision (scores,
network, export, briefs) is only read, by the agent's own read-only tools.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from sitescout.agent import AgentContext, AgentResult, session_from_observations
from sitescout.agent_provider import ask
from sitescout.analyst.provider import Provider
from sitescout.config import AgentSettings, Config

log = logging.getLogger(__name__)

HOST = "127.0.0.1"  # D-062: loopback by default, never a setting
PUBLIC_BIND = "0.0.0.0"  # D-066: only when started with an explicit public hostname
HOSTNAME = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
PREFLIGHT_PATHS = frozenset({"/api/status", "/api/investigate"})
CANDIDATE_ID = re.compile(r"^cand-[0-9a-f]{12}$")
MAX_BODY_BYTES = 2048  # an investigation request is a kind and two identifiers
DRAIN_BYTES = 65536  # at most this much of a refused request's body is read and dropped
SOCKET_TIMEOUT_S = 30  # a client that stops sending mid-request cannot hold a handler

Kind = Literal["site_investigation", "network_comparison", "unknowns", "evidence_explanation"]
Group = Literal["demand", "access", "host", "charging_gap", "grid_evidence"]

GROUP_WORDS: dict[str, str] = {
    "demand": "demand",
    "access": "access",
    "host": "host and nearby activity",
    "charging_gap": "charging gap",
    "grid_evidence": "grid evidence",
}

# The only questions the agent is ever asked from the page (D-062). None invites a claim
# SiteScout never makes; the agent's own prompt and validator still apply unchanged.
QUESTIONS: dict[str, str] = {
    "site_investigation": (
        "Why is candidate site {candidate_id} in or out of the optimized network? Answer from its "
        "stored score, rank and network contribution, and name what remains unknown."
    ),
    "network_comparison": (
        "How does the optimized network differ from the Top-30 by score, and why? Answer from "
        "the stored network results: what differs in modelled population coverage, spread "
        "across districts and provinces and mean site score; how the optimized network is "
        "chosen; the trade-off this involves; and what remains unknown."
    ),
    "unknowns": (
        "What is not known about candidate site {candidate_id} from public data, and what would "
        "need to be verified next?"
    ),
    "evidence_explanation": (
        "Explain the {group} evidence recorded for candidate site {candidate_id}: which stored "
        "values it rests on, what they show, and what they do not show."
    ),
}

TRACE_LABELS: dict[str, str] = {
    "find_sites": "Searched candidate sites",
    "get_site": "Retrieved site record",
    "compare_sites": "Compared sites",
    "explain_score": "Explained score components",
    "network_contribution": "Checked network contribution",
    "generate_brief": "Read site brief",
    "network_summary": "Read network summary",
    "nearby_sites": "Listed nearby candidates",
    "search_knowledge": "Searched project knowledge",
}

STATIC: dict[str, tuple[str, str]] = {
    "/app/index.html": ("app/index.html", "text/html; charset=utf-8"),
    "/app/investigation.js": ("app/investigation.js", "text/javascript; charset=utf-8"),
    # The page's typefaces (D-065); the CSP's default-src 'self' already covers fonts.
    "/app/fonts/IBMPlexSans-Regular.woff2": ("app/fonts/IBMPlexSans-Regular.woff2", "font/woff2"),
    "/app/fonts/IBMPlexSans-Medium.woff2": ("app/fonts/IBMPlexSans-Medium.woff2", "font/woff2"),
    "/app/fonts/IBMPlexSans-SemiBold.woff2": ("app/fonts/IBMPlexSans-SemiBold.woff2", "font/woff2"),
    "/app/fonts/IBMPlexMono-Regular.woff2": ("app/fonts/IBMPlexMono-Regular.woff2", "font/woff2"),
    "/data/export/sitescout.js": ("data/export/sitescout.js", "text/javascript; charset=utf-8"),
    "/data/export/sitescout.json": ("data/export/sitescout.json", "application/json"),
}
REPORTS_PREFIX = "/reports/"

SECURITY_HEADERS = {
    # The page's own inline script and style attributes need 'unsafe-inline' (D-062).
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

ProviderFactory = Callable[[AgentSettings], Provider]


# --- Requests --------------------------------------------------------------------------------


class InvestigationRequest(BaseModel):
    """What the page may ask for: a kind and the identifiers that kind needs, nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Kind
    candidate_id: str | None = None
    group: Group | None = None

    @model_validator(mode="after")
    def _fields_match_the_kind(self) -> InvestigationRequest:
        needs_site = self.kind != "network_comparison"
        needs_group = self.kind == "evidence_explanation"
        if needs_site != (self.candidate_id is not None):
            raise ValueError(f"{self.kind} {'needs' if needs_site else 'takes no'} candidate_id")
        if needs_group != (self.group is not None):
            raise ValueError(f"{self.kind} {'needs' if needs_group else 'takes no'} group")
        return self

    def question(self) -> str:
        return QUESTIONS[self.kind].format(
            candidate_id=self.candidate_id,
            group=GROUP_WORDS.get(self.group or "", ""),
        )


# --- Responses -------------------------------------------------------------------------------


def _trace_arguments(
    arguments: dict[str, Any], id_pattern: re.Pattern[str], max_query_chars: int
) -> dict[str, Any]:
    """Only arguments that are safe and meaningful to show: the knowledge query, site ids and
    plain numbers or flags. Anything else a model sent is left out, never echoed."""
    shown: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "query" and isinstance(value, str):
            shown[key] = value[:max_query_chars]
        elif isinstance(value, str) and id_pattern.fullmatch(value):
            shown[key] = value
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(v, str) and id_pattern.fullmatch(v) for v in value)
        ):
            shown[key] = list(value)
        elif isinstance(value, bool | int):
            shown[key] = value
    return shown


def build_response(
    request: InvestigationRequest,
    result: AgentResult,
    elapsed_s: float,
    *,
    id_pattern: re.Pattern[str] = CANDIDATE_ID,
    max_query_chars: int = 500,
) -> dict[str, Any]:
    """The JSON the page renders, built only from what the run recorded (D-062)."""
    state = result.state
    session = session_from_observations(state.observations)
    records = {r.id: r for r in session.records}
    comparisons = {c.id: c for c in session.comparisons}

    shown_records: dict[str, Any] = {}
    shown_comparisons: dict[str, Any] = {}
    answer = None
    if result.status == "answered" and result.answer is not None:
        answer = result.answer.model_dump(mode="json")
        cited = dict.fromkeys(
            i for _, statements in result.answer.sections() for s in statements
            for i in s.evidence_ids
        )  # fmt: skip
        for evidence_id in cited:
            if evidence_id in records:
                shown_records[evidence_id] = records[evidence_id].model_dump(mode="json")
            elif evidence_id in comparisons:
                field = comparisons[evidence_id]
                shown_comparisons[evidence_id] = field.model_dump(mode="json")
                for side in (field.a_record_id, field.b_record_id):
                    if side in records:
                        shown_records[side] = records[side].model_dump(mode="json")
    elif result.fallback is not None:
        # The fallback's own records, never its reason (which may hold exception text).
        shown_records = {r.id: r.model_dump(mode="json") for r in result.fallback.records}

    trace = []
    for o in state.observations:
        if o.status == "error":
            label = "Tool call refused"
        elif o.cached:
            label = "Reused earlier result"
        else:
            label = TRACE_LABELS.get(o.tool, "Tool call")
        trace.append(
            {
                "step": o.index,
                "tool": o.tool,
                "label": label,
                "arguments": _trace_arguments(o.arguments, id_pattern, max_query_chars),
                "status": o.status,
                "cached": o.cached,
            }
        )
    validation = [
        {"attempt": number, "passed": v.passed, "rules": sorted({e.rule for e in v.errors})}
        for number, v in enumerate(state.validation_attempts, start=1)
    ]
    return {
        "status": result.status,
        "kind": request.kind,
        "candidate_id": request.candidate_id,
        "group": request.group,
        "termination": result.termination,
        "answer": answer,
        "records": shown_records,
        "comparisons": shown_comparisons,
        "trace": trace,
        "validation": validation,
        "elapsed_display": f"{elapsed_s:.1f} s",
    }


# --- The service -----------------------------------------------------------------------------


def _default_factory(settings: AgentSettings) -> Provider:
    from sitescout.agent_provider import build_configured_agent_provider

    return build_configured_agent_provider(
        settings.model_provider, min_quote_words=settings.min_quote_words
    )


@dataclass(frozen=True)
class Outcome:
    """One investigation: the response the page receives and, when the agent ran, its result
    (kept in memory for the caller only; the demo gate reads its metadata)."""

    response: dict[str, Any]
    result: AgentResult | None = None


class InvestigationService:
    """Runs one investigation at a time with a provider built once at start-up."""

    def __init__(
        self,
        context: AgentContext | None,
        settings: AgentSettings,
        provider: Provider | None,
        *,
        max_query_chars: int = 500,
        id_pattern: re.Pattern[str] = CANDIDATE_ID,
    ) -> None:
        self.context = context
        self.settings = settings
        self._provider = provider
        self._max_query_chars = max_query_chars
        self._id_pattern = id_pattern
        self._lock = threading.Lock()
        self.candidate_ids: frozenset[str] = (
            frozenset(context.investigation.analyst.sites.index) if context else frozenset()
        )

    @property
    def available(self) -> bool:
        return self.context is not None and self._provider is not None

    @classmethod
    def start(cls, config: Config, factory: ProviderFactory = _default_factory):
        """The service for ``config``; unavailable (never failing) when the processed data or
        the provider cannot be loaded. Only a fixed line is logged, never a reason's text."""
        context: AgentContext | None = None
        provider: Provider | None = None
        processed = config.resolve(config.settings.paths.processed_dir)
        try:
            if processed.exists():
                context = AgentContext.load(config, processed)
            provider = factory(config.settings.agent) if context is not None else None
        except Exception as failure:  # noqa: BLE001 - availability is a yes/no to the page
            log.warning("AI investigation unavailable (%s)", type(failure).__name__)
            context, provider = None, None
        service = cls(
            context,
            config.settings.agent,
            provider,
            max_query_chars=config.settings.knowledge.retrieval.max_query_chars,
        )
        log.info("AI investigation available: %s", "yes" if service.available else "no")
        return service

    def parse(self, payload: object) -> InvestigationRequest | None:
        """The validated request, or ``None`` for anything else (never a reason: the page
        only ever sends well-formed requests)."""
        try:
            request = InvestigationRequest.model_validate(payload)
        except ValidationError:
            return None
        if request.candidate_id is not None and (
            not self._id_pattern.fullmatch(request.candidate_id)
            or request.candidate_id not in self.candidate_ids
        ):
            return None
        return request

    def run(self, request: InvestigationRequest) -> Outcome:
        if not self.available:
            return Outcome({"status": "unavailable"})
        if not self._lock.acquire(blocking=False):
            return Outcome({"status": "busy"})
        try:
            provider = self._provider
            started = time.monotonic()
            question = request.question()
            result = ask(self.context, question, self.settings, factory=lambda _: provider)
            elapsed = time.monotonic() - started
        finally:
            self._lock.release()
        log.info("Investigation %s ended: %s", request.kind, result.termination)
        response = build_response(
            request,
            result,
            elapsed,
            id_pattern=self._id_pattern,
            max_query_chars=self._max_query_chars,
        )
        return Outcome(response, result)


# --- HTTP ------------------------------------------------------------------------------------


class SiteScoutServer(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows, reuse would let two servers share the port
    daemon_threads = True

    def __init__(
        self,
        config: Config,
        service: InvestigationService,
        port: int,
        handler: type[BaseHTTPRequestHandler] | None = None,  # tests/qa_server.py only
        *,
        public_host: str | None = None,
    ) -> None:
        if public_host is not None and not HOSTNAME.fullmatch(public_host):
            raise ValueError(f"--public-host must be a lowercase hostname, not {public_host!r}")
        super().__init__((HOST if public_host is None else PUBLIC_BIND, port), handler or _Handler)
        self.config = config
        self.service = service
        bound = self.server_address[1]
        if public_host is None:
            self.allowed_hosts = frozenset({f"{HOST}:{bound}", f"localhost:{bound}"})
            self.cross_origins: frozenset[str] = frozenset()
            self.page_origins = frozenset(f"http://{h}" for h in self.allowed_hosts)
            self.url = f"http://{HOST}:{bound}/"
        else:
            # Behind the host's TLS proxy the page's own origin is https and carries no port.
            self.allowed_hosts = frozenset({public_host})
            self.cross_origins = frozenset(config.settings.server.public_origins)
            self.page_origins = frozenset({f"https://{public_host}"}) | self.cross_origins
            self.url = f"https://{public_host}/"


def build_server(
    config: Config,
    service: InvestigationService,
    port: int | None = None,
    *,
    public_host: str | None = None,
):
    """The server for ``config`` on 127.0.0.1, or on all interfaces for ``public_host`` only
    (D-066); ``port`` 0 picks a free port (tests)."""
    port = config.settings.server.port if port is None else port
    return SiteScoutServer(config, service, port, public_host=public_host)


class _Handler(BaseHTTPRequestHandler):
    server: SiteScoutServer
    server_version = "SiteScout"
    sys_version = ""
    timeout = SOCKET_TIMEOUT_S
    # HTTP/1.1 lets the client close the connection. Under HTTP/1.0 the server closed it right
    # after writing, and on Windows loopback the tail of a large reply (the 0.6 MB export) was
    # then often never delivered, so the page loaded without its data. Every reply here sends
    # Content-Length, which keep-alive needs.
    protocol_version = "HTTP/1.1"

    # -- helpers --

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        log.info("%s %s %s", self.command, urlsplit(self.path).path, code)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.warning(format, *args)

    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if self.server.cross_origins:
            # A request line can fail before any header is parsed.
            headers = getattr(self, "headers", None)
            origin = headers.get("Origin") if headers is not None else None
            if origin in self.server.cross_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def _send(self, code: int, body: bytes, content_type: str, *, no_store: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", no_store=True)

    def _host_allowed(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _static_file(self, path: str) -> tuple[Path, str] | None:
        root = self.server.config.root
        if path in STATIC:
            relative, content_type = STATIC[path]
            return root / relative, content_type
        if path.startswith(REPORTS_PREFIX) and path.endswith(".md"):
            reports = (root / "reports").resolve()
            candidate = (reports / path[len(REPORTS_PREFIX) :]).resolve()
            if candidate.is_relative_to(reports) and candidate.is_file():
                return candidate, "text/plain; charset=utf-8"
        return None

    # -- methods --

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._json(403, {"status": "forbidden"})
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/app/index.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/status":
            self._json(200, {"investigation_available": self.server.service.available})
            return
        found = self._static_file(path)
        if found is None or not found[0].is_file():
            self._json(404, {"status": "not_found"})
            return
        self._send(200, found[0].read_bytes(), found[1])

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        """A browser's CORS preflight, answered only for an allowed cross origin (D-066)."""
        if (
            not self._host_allowed()
            or self.headers.get("Origin") not in self.server.cross_origins
            or urlsplit(self.path).path not in PREFLIGHT_PATHS
        ):
            self._refuse(403, "forbidden")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _refuse(self, code: int, status: str) -> None:
        """Refuse a POST before reading its body. The unread body (at most ``DRAIN_BYTES``) is
        read and dropped first: closing a socket with unread data resets the connection, and
        the client could then lose the reply (seen on Windows)."""
        try:
            pending = max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            pending = 0
        if pending:
            self.rfile.read(min(pending, DRAIN_BYTES))
        self.close_connection = True  # a body left unread must never be read as a request
        self._json(code, {"status": status})

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._refuse(403, "forbidden")
            return
        if urlsplit(self.path).path != "/api/investigate":
            self._refuse(404, "not_found")
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.page_origins:
            self._refuse(403, "forbidden")
            return
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._refuse(415, "invalid_request")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._refuse(400, "invalid_request")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._refuse(413, "invalid_request")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"status": "invalid_request"})
            return
        if not self.server.service.available:
            self._json(503, {"status": "unavailable"})
            return
        request = self.server.service.parse(payload)
        if request is None:
            self._json(400, {"status": "invalid_request"})
            return
        self._json(200, self.server.service.run(request).response)
