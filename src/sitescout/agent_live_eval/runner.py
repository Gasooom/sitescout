"""Milestone 10, Phase 6 (D-060): run the live evaluation's cases through a real provider.

Each case's question goes to the unmodified ``run_agent`` (Phase 3) with the real provider
built by Phase 5's unmodified ``sitescout.agent_provider``; the model chooses every tool call
and its order. This module only: resolves a case's optional ``site_selector`` against the
real processed data before spending any call (never a hardcoded id); classifies the finished
run's outcome (:mod:`sitescout.agent_live_eval.instrumentation`); checks a case's soft
expectations against what actually happened; and aggregates. It adds no grounding rule and
relaxes none — grounding is decided entirely by the unchanged
``sitescout.agent.validate.validate_agent_answer``, exactly as for a ``FakeModel`` run.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict

from sitescout.agent import AgentContext, AgentLimits, AgentResult, run_agent
from sitescout.agent_live_eval.cases import LiveCase, LiveCaseSet, LiveExpectation
from sitescout.agent_live_eval.instrumentation import CallUsage, FailureCategory, classify_result
from sitescout.analyst.provider import Provider
from sitescout.config import AgentSettings

EVAL_VERSION = "agent-live-eval-v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LiveCaseResult(_Model):
    id: str
    category: str
    title: str
    question: str  # the question actually put to the model, after any {{site}} substitution
    status: str  # "answered", "fallback", "skipped" or "infra_error"
    failure_category: FailureCategory | None = None
    skip_reason: str | None = None
    termination: str | None = None
    validation_attempts: int = 0
    trajectory: tuple[str, ...] = ()
    counters: dict[str, int] = {}
    calls: tuple[CallUsage, ...] = ()
    expect_met: dict[str, bool] = {}
    answer_excerpt: str | None = None  # the first statement's text, for the report only

    @property
    def ran(self) -> bool:
        return self.status in ("answered", "fallback")


def live_agent_limits(settings: AgentSettings) -> AgentLimits:
    """The live evaluation's own, tighter tool/retrieval/iteration ceilings
    (``agent.live_eval``), with the loop's other safety limits unchanged."""
    live = settings.live_eval
    return AgentLimits(
        max_iterations=live.max_iterations,
        max_tool_calls=live.max_tool_calls,
        max_retrieval_calls=live.max_retrieval_calls,
        max_recoverable_errors=settings.max_recoverable_errors,
        min_quote_words=settings.min_quote_words,
    )


def resolve_site(context: AgentContext, selector: dict[str, Any]) -> str | None:
    """The first (lowest-rank) real candidate matching ``selector``, or ``None``."""
    from sitescout.analyst.tools import FindQuery, find_sites

    query = FindQuery.model_validate(selector)
    found = find_sites(context.investigation.analyst, query)
    return found.sites[0].candidate_id if found.sites else None


def prepare_question(context: AgentContext, case: LiveCase) -> tuple[str | None, str | None]:
    """``(question, None)`` ready to run, or ``(None, reason)`` when the case must be skipped
    because a named site selector matched no real candidate. Two placeholders may resolve to
    the same site (nothing here forces them to differ)."""
    question = case.question
    for name, selector in case.sites.items():
        site_id = resolve_site(context, selector)
        if site_id is None:
            return None, f"no real candidate matches sites[{name!r}] = {selector!r}"
        question = question.replace(f"{{{{{name}}}}}", site_id)
    return question, None


def _texts_of(result: AgentResult) -> list[str]:
    if result.answer is None:
        return []
    return [s.text for _, statements in result.answer.sections() for s in statements]


def _cited_of(result: AgentResult) -> set[str]:
    if result.answer is None:
        return set()
    return {
        i for _, statements in result.answer.sections() for s in statements for i in s.evidence_ids
    }


def check_expectations(expect: LiveExpectation, result: AgentResult) -> dict[str, bool]:
    """Which of ``expect``'s soft properties actually held; every key present regardless."""
    trajectory = {o.tool for o in result.state.observations}
    cited = _cited_of(result)
    texts = " ".join(_texts_of(result)).lower()
    met: dict[str, bool] = {}
    if expect.status != "either":
        met["status"] = result.status == expect.status
    for tool in expect.tools_include:
        met[f"tool_include:{tool}"] = tool in trajectory
    if expect.tools_any:
        met["tools_any"] = any(t in trajectory for t in expect.tools_any)
    for prefix in expect.cites_prefix:
        met[f"cites_prefix:{prefix}"] = any(i.startswith(prefix) for i in cited)
    for group in expect.contains_any:
        label = "contains_any:" + "|".join(group)
        met[label] = any(phrase.lower() in texts for phrase in group)
    if expect.expect_unknown:
        kinds = {
            s.kind
            for _, statements in (result.answer.sections() if result.answer else ())
            for s in statements
        }
        met["expect_unknown"] = "UNKNOWN" in kinds
    return met


def run_live_case(
    context: AgentContext,
    case: LiveCase,
    provider: Provider,
    limits: AgentLimits,
    *,
    recorder: Any | None = None,
) -> LiveCaseResult:
    """Run one case with no scripted trajectory; never raises (an unexpected failure is
    reported as ``infra_error``, not propagated, so one bad case cannot abort the batch)."""
    before = len(recorder.calls) if recorder is not None else 0
    try:
        question, skip_reason = prepare_question(context, case)
    except Exception as failure:  # noqa: BLE001 - preflight must not crash the batch
        return LiveCaseResult(
            id=case.id, category=case.category, title=case.title, question=case.question,
            status="infra_error", skip_reason=f"{type(failure).__name__}: {failure}",
        )  # fmt: skip
    if question is None:
        return LiveCaseResult(
            id=case.id, category=case.category, title=case.title, question=case.question,
            status="skipped", skip_reason=skip_reason,
        )  # fmt: skip
    try:
        result = run_agent(context, question, provider, limits)
    except Exception as failure:  # noqa: BLE001 - the harness must survive one bad case
        calls = tuple(recorder.calls[before:]) if recorder is not None else ()
        return LiveCaseResult(
            id=case.id, category=case.category, title=case.title, question=question,
            status="infra_error", skip_reason=f"{type(failure).__name__}: {failure}", calls=calls,
        )  # fmt: skip
    calls = tuple(recorder.calls[before:]) if recorder is not None else ()
    texts = _texts_of(result)
    return LiveCaseResult(
        id=case.id,
        category=case.category,
        title=case.title,
        question=question,
        status=result.status,
        failure_category=classify_result(result),
        termination=result.termination,
        validation_attempts=len(result.state.validation_attempts),
        trajectory=tuple(o.tool for o in result.state.observations),
        counters={
            "tool_calls": result.state.tool_calls,
            "retrieval_calls": result.state.retrieval_calls,
            "duplicate_calls": result.state.duplicate_calls,
            "recoverable_errors": result.state.recoverable_errors,
        },
        calls=calls,
        expect_met=check_expectations(case.expect, result),
        answer_excerpt=texts[0][:200] if texts else None,
    )


class LiveEvaluation(_Model):
    version: str
    provider: str
    model: str
    case_set_version: int
    case_count: int
    generated_at: str | None  # ISO-8601 UTC; set by the script only, absent in offline tests
    limits: dict[str, int]
    cases: tuple[LiveCaseResult, ...]
    by_category: dict[str, int]  # cases run (not skipped, not infra_error) per category
    by_failure_category: dict[str, int]
    skipped: int
    infra_errors: int
    total_latency_s: float
    total_input_tokens: int | None
    total_output_tokens: int | None


def evaluate_live(
    context: AgentContext,
    cases: LiveCaseSet,
    provider: Provider,
    limits: AgentLimits,
    *,
    recorder: Any | None = None,
    generated_at: str | None = None,
) -> LiveEvaluation:
    """Run every case, in file order, and aggregate. Not reproducible run to run against a
    real provider (the model is free); the aggregation and rendering of one finished run are
    deterministic, and that is what the offline tests check."""
    results = [
        run_live_case(context, case, provider, limits, recorder=recorder) for case in cases.cases
    ]
    ran = [r for r in results if r.ran]
    input_tokens = [c.input_tokens for r in results for c in r.calls if c.input_tokens is not None]
    output_tokens = [
        c.output_tokens for r in results for c in r.calls if c.output_tokens is not None
    ]
    settings_provider = getattr(provider, "settings", None)
    return LiveEvaluation(
        version=EVAL_VERSION,
        provider=getattr(settings_provider, "provider", "unknown"),
        model=getattr(settings_provider, "model", "unknown"),
        case_set_version=cases.version,
        case_count=len(results),
        generated_at=generated_at,
        limits=dataclasses.asdict(limits),
        cases=tuple(results),
        by_category=dict(sorted(Counter(r.category for r in ran).items())),
        by_failure_category=dict(
            sorted(Counter(r.failure_category for r in ran if r.failure_category).items())
        ),
        skipped=sum(1 for r in results if r.status == "skipped"),
        infra_errors=sum(1 for r in results if r.status == "infra_error"),
        total_latency_s=sum(c.latency_s for r in results for c in r.calls),
        total_input_tokens=sum(input_tokens) if input_tokens else None,
        total_output_tokens=sum(output_tokens) if output_tokens else None,
    )
