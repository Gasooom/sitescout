"""Milestone 10, Phase 4 (D-058): run the agent evaluation cases with scripted providers.

Every case is run through the production ``run_agent`` with a ``FakeModel`` that plays the
case's steps in order, so a run is deterministic: no model, no network, no clock, no
randomness. The runner adds nothing to the agent; it builds the provider, runs the loop, reads
the resulting ``AgentState`` and hands it to ``checks.check_case``.

The decision under test is the validator's verdict on the *first* answer attempt (a retry
after a rejected answer is a trajectory question, tested separately): ``accept`` when that
attempt passed, ``reject`` when it failed, ``none`` when the run never got as far as an
answer.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict

from sitescout.agent import AgentContext, AgentLimits, AgentResult, records_in, run_agent
from sitescout.agent_eval.cases import Case, CaseSet
from sitescout.agent_eval.checks import check_case, trajectory_of
from sitescout.analyst import FakeModel, ModelContext, ModelStep, ToolCallRequest
from sitescout.analyst.provider_common import ProviderError

EVAL_VERSION = "agent-eval-v1"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\|([^}]*)\}\}")
_SITE_ID = re.compile(r"^(cand-[^/]+)/")


class FixtureError(Exception):
    """A case refers to a value the scripted provider has not been shown: the case is stale."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseResult(_Model):
    id: str
    category: str
    kind: str
    tags: tuple[str, ...]
    title: str
    known_limit: str | None
    expected_decision: str  # the ground truth
    actual_decision: str  # the validator's verdict on the first answer attempt
    outcome: str  # true_accept, true_reject, false_accept, false_reject or not_applicable
    rules: tuple[str, ...]  # the rules the first answer attempt failed, sorted
    status: str
    termination: str
    attempts: int
    trajectory: tuple[str, ...]
    counters: dict[str, int]
    failures: tuple[str, ...]  # what differs from the case's expectations, in words

    @property
    def passed(self) -> bool:
        return not self.failures


def _shown(context: ModelContext) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for item in context.transcript:
        found.update(records_in(item.result)[0])
    return found


def _first_site(context: ModelContext) -> str:
    for item in reversed(context.transcript):
        if item.tool == "find_sites":
            for record_id in records_in(item.result)[0]:
                match = _SITE_ID.match(record_id)
                if match:
                    return match.group(1)
    raise FixtureError("{{first_site|}}: no find_sites result has been shown yet")


def _fill(value: Any, context: ModelContext) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, argument = match.groups()
            if name == "first_site":
                return _first_site(context)
            if name == "display":
                records = _shown(context)
                if argument not in records:
                    raise FixtureError(f"{{{{display|{argument}}}}}: that record was not shown")
                return str(records[argument].display)
            raise FixtureError(f"unknown placeholder {match.group(0)!r}")

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_fill(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, context) for key, item in value.items()}
    return value


def _provider(case: Case, problems: list[str]) -> FakeModel:
    remaining = [step for step in case.provider for _ in range(step.times)]

    def next_step(context: ModelContext) -> ModelStep:
        if not remaining:
            problems.append("the provider script ended before the run did")
            raise ProviderError("script exhausted")
        step = remaining.pop(0)
        if step.provider_error:
            raise ProviderError("scripted provider failure")
        try:
            if step.tool is not None:
                arguments = _fill(step.arguments, context)
                return ModelStep(tool_call=ToolCallRequest(tool=step.tool, arguments=arguments))
            return ModelStep(answer_json=_fill(step.answer, context))
        except FixtureError as error:
            problems.append(f"fixture: {error}")
            raise

    return FakeModel(next_step)


def _actual_decision(result: AgentResult) -> str:
    attempts = result.state.validation_attempts
    if not attempts:
        return "none"
    return "accept" if attempts[0].passed else "reject"


def _outcome(expected: str, actual: str) -> str:
    if expected == "none":
        return "not_applicable"
    if expected == "accept":
        return "true_accept" if actual == "accept" else "false_reject"
    return "true_reject" if actual == "reject" else "false_accept"


def run_case_with_result(
    context: AgentContext, case: Case, limits: AgentLimits
) -> tuple[CaseResult, AgentResult]:
    """Run one case through ``run_agent``; the comparison with what the case expects, and the
    full agent result it was made from."""
    problems: list[str] = []
    changed = dataclasses.replace(limits, **case.limits)
    result = run_agent(context, case.question, _provider(case, problems), changed)
    state = result.state
    attempts = state.validation_attempts
    actual = _actual_decision(result)
    rules = tuple(sorted({e.rule for e in attempts[0].errors})) if attempts else ()
    counters = {
        "tool_calls": state.tool_calls,
        "retrieval_calls": state.retrieval_calls,
        "duplicate_calls": state.duplicate_calls,
        "recoverable_errors": state.recoverable_errors,
    }
    trajectory = trajectory_of(state)
    failures = problems + check_case(
        case.expect,
        result,
        actual_decision=actual,
        rules=rules,
        counters=counters,
        trajectory=trajectory,
    )
    scored = CaseResult(
        id=case.id,
        category=case.category,
        kind=case.kind,
        tags=case.tags,
        title=case.title,
        known_limit=case.known_limit,
        expected_decision=case.expect.decision,
        actual_decision=actual,
        outcome=_outcome(case.expect.decision, actual),
        rules=rules,
        status=result.status,
        termination=result.termination,
        attempts=len(attempts),
        trajectory=trajectory,
        counters=counters,
        failures=tuple(failures),
    )
    return scored, result


def run_case(context: AgentContext, case: Case, limits: AgentLimits) -> CaseResult:
    return run_case_with_result(context, case, limits)[0]


class Tally(_Model):
    """Counts of the four decision outcomes; never combined into one score."""

    cases: int = 0
    true_accept: int = 0
    true_reject: int = 0
    false_accept: int = 0
    false_reject: int = 0
    not_applicable: int = 0
    passed: int = 0
    failed: int = 0


class Evaluation(_Model):
    version: str
    world: str
    case_count: int
    corpus_fingerprint: str
    corpus_chunks: int
    limits: dict[str, int]
    knowledge_top_k: int
    cases: tuple[CaseResult, ...]
    by_category: dict[str, Tally]
    by_tag: dict[str, Tally]
    total: Tally
    rule_counts: dict[str, int]  # first-attempt rejections by rule, over every case
    termination_counts: dict[str, int]
    status_counts: dict[str, int]
    retries: int  # runs whose first answer was rejected and which then went on to answer
    known_limit_cases: tuple[str, ...]


def _tally(results: list[CaseResult]) -> Tally:
    outcomes = Counter(r.outcome for r in results)
    passed = sum(1 for r in results if r.passed)
    return Tally(
        cases=len(results),
        true_accept=outcomes["true_accept"],
        true_reject=outcomes["true_reject"],
        false_accept=outcomes["false_accept"],
        false_reject=outcomes["false_reject"],
        not_applicable=outcomes["not_applicable"],
        passed=passed,
        failed=len(results) - passed,
    )


def evaluate(context: AgentContext, cases: CaseSet, limits: AgentLimits) -> Evaluation:
    """Run every case, in file order, and aggregate; the same inputs give the same result."""
    results = [run_case(context, case, limits) for case in cases.cases]
    tags = sorted({t for r in results for t in r.tags})
    corpus = context.knowledge.corpus
    return Evaluation(
        version=EVAL_VERSION,
        world=cases.world,
        case_count=len(results),
        corpus_fingerprint=corpus.fingerprint,
        corpus_chunks=len(corpus.chunks),
        limits=dataclasses.asdict(limits),
        knowledge_top_k=context.knowledge_top_k,
        cases=tuple(results),
        by_category={
            c: _tally([r for r in results if r.category == c])
            for c in sorted({r.category for r in results})
        },
        by_tag={t: _tally([r for r in results if t in r.tags]) for t in tags},
        total=_tally(results),
        rule_counts=dict(sorted(Counter(rule for r in results for rule in r.rules).items())),
        termination_counts=dict(sorted(Counter(r.termination for r in results).items())),
        status_counts=dict(sorted(Counter(r.status for r in results).items())),
        retries=sum(1 for r in results if r.attempts > 1 and r.status == "answered"),
        known_limit_cases=tuple(r.id for r in results if r.known_limit),
    )
