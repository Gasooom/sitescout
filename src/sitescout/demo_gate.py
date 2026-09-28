"""Milestone 11 (D-063): the demo-readiness gate's live part.

Before the page's investigation layer is shown as a working demo, three investigation kinds run
``demo_gate.runs_per_kind`` times each through the page's own path (``InvestigationService.run``,
the same code ``POST /api/investigate`` calls), with the configured real provider and the
agent's own limits. A kind passes when at least ``demo_gate.min_validated_per_kind`` of its runs
end with a validated answer and every run completes (an answer or the agent's fallback). The
other gate items (invalid sites refused, fallback shown, export unchanged, no key in the browser,
nothing persisted, limits respected, the static page complete) are deterministic tests in
``tests/test_server.py`` and ``tests/test_app.py``.

The report records metadata only: kind, site, outcome, validation rules, the recorded steps and
the elapsed time. It never holds answer text. It is a readiness check for one demo on one day,
not a measure of the agent's reliability.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sitescout.agent_live_eval.instrumentation import classify_result
from sitescout.agent_live_eval.runner import resolve_site
from sitescout.config import Config
from sitescout.server import InvestigationRequest, InvestigationService

TEMPLATE = Path(__file__).resolve().parent / "templates" / "demo_gate.md"
SITE_SELECTOR: dict[str, Any] = {"selected_mclp": True}  # the highest-ranked network site
GATE_KINDS: tuple[tuple[str, str | None], ...] = (
    ("site_investigation", None),
    ("evidence_explanation", "grid_evidence"),
    ("unknowns", None),
)
DETERMINISTIC = (
    ("An invalid or nonexistent site is refused before any provider call", "tests/test_server.py"),
    ("A rejection returns the fallback's records, never answer text", "tests/test_server.py"),
    ("The export is unchanged by investigations", "tests/test_server.py"),
    ("No credential reaches a response or the page", "tests/test_server.py, tests/test_app.py"),
    ("No investigation output is written anywhere", "tests/test_server.py"),
    ("Runs stay within the agent's configured limits", "tests/test_server.py"),
    ("The page opened from disk is complete and makes no request", "tests/test_app.py"),
)  # fmt: skip


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateRun(_Model):
    kind: str
    group: str | None
    candidate_id: str | None
    status: str
    termination: str | None
    category: str | None
    attempts: int
    rules: tuple[str, ...]
    steps: tuple[str, ...]
    elapsed_display: str | None


class KindVerdict(_Model):
    kind: str
    runs: int
    validated: int
    completed: int
    passed: bool


class GateResult(_Model):
    generated_at: str | None
    provider: str
    model: str
    candidate_id: str
    runs_per_kind: int
    min_validated_per_kind: int
    runs: tuple[GateRun, ...]
    verdicts: tuple[KindVerdict, ...]
    passed: bool


def gate_run(request: InvestigationRequest, outcome) -> GateRun:
    """Metadata of one investigation; never its answer text."""
    response, result = outcome.response, outcome.result
    return GateRun(
        kind=request.kind,
        group=request.group,
        candidate_id=request.candidate_id,
        status=response["status"],
        termination=response.get("termination"),
        category=classify_result(result) if result is not None else None,
        attempts=len(response.get("validation", [])),
        rules=tuple(sorted({r for v in response.get("validation", []) for r in v["rules"]})),
        steps=tuple(t["label"] for t in response.get("trace", [])),
        elapsed_display=response.get("elapsed_display"),
    )


def verdicts(runs: list[GateRun], runs_per_kind: int, min_validated: int) -> list[KindVerdict]:
    out = []
    for kind, _ in GATE_KINDS:
        mine = [r for r in runs if r.kind == kind]
        validated = sum(1 for r in mine if r.status == "answered")
        completed = sum(1 for r in mine if r.status in ("answered", "fallback"))
        out.append(
            KindVerdict(
                kind=kind,
                runs=len(mine),
                validated=validated,
                completed=completed,
                passed=len(mine) == runs_per_kind
                and completed == len(mine)
                and validated >= min_validated,
            )
        )
    return out


def run_gate(
    service: InvestigationService,
    config: Config,
    *,
    generated_at: str | None = None,
    on_run: Callable[[GateRun], None] | None = None,
) -> GateResult:
    """Every gated kind ``runs_per_kind`` times, in order, through the page's own path."""
    settings = config.settings.demo_gate
    candidate_id = resolve_site(service.context, SITE_SELECTOR)
    if candidate_id is None:
        raise ValueError("no network site matches the gate's site selector")
    runs: list[GateRun] = []
    for kind, group in GATE_KINDS:
        request = InvestigationRequest.model_validate(
            {"kind": kind, "candidate_id": candidate_id, "group": group}
        )
        for _ in range(settings.runs_per_kind):
            run = gate_run(request, service.run(request))
            runs.append(run)
            if on_run is not None:
                on_run(run)
    kinds = verdicts(runs, settings.runs_per_kind, settings.min_validated_per_kind)
    provider = config.settings.agent.model_provider
    return GateResult(
        generated_at=generated_at,
        provider=provider.provider,
        model=provider.model,
        candidate_id=candidate_id,
        runs_per_kind=settings.runs_per_kind,
        min_validated_per_kind=settings.min_validated_per_kind,
        runs=tuple(runs),
        verdicts=tuple(kinds),
        passed=all(v.passed for v in kinds),
    )


def _table(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def render_report(result: GateResult) -> str:
    runs = _table(
        ("Kind", "Status", "Ending", "Attempts", "Validation rules", "Steps", "Time"),
        [
            (
                f"{r.kind}" + (f" ({r.group})" if r.group else ""),
                r.status,
                r.termination or "-",
                r.attempts,
                ", ".join(f"`{rule}`" for rule in r.rules) or "none",
                " → ".join(r.steps) or "none",
                r.elapsed_display or "-",
            )
            for r in result.runs
        ],
    )
    verdict_rows = [
        (v.kind, v.runs, v.validated, v.completed, "pass" if v.passed else "**fail**")
        for v in result.verdicts
    ]
    verdict_table = _table(("Kind", "Runs", "Validated", "Completed", "Verdict"), verdict_rows)
    return TEMPLATE.read_text(encoding="utf-8").format(
        generated_at=result.generated_at or "not set (offline run)",
        provider=result.provider,
        model=result.model,
        candidate_id=result.candidate_id,
        runs_per_kind=result.runs_per_kind,
        min_validated=result.min_validated_per_kind,
        overall="PASS" if result.passed else "FAIL",
        verdicts=verdict_table,
        runs=runs,
        deterministic=_table(("Gate item", "Enforced by"), list(DETERMINISTIC)),
    )
