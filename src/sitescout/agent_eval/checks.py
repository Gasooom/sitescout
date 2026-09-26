"""Milestone 10, Phase 4 (D-058): the assertions applied to one evaluated run.

``check_case`` returns what differs between a run and its case's expectations, as sentences;
an empty list means the run matched. Where a case pins a known validator limit, the expectation
is the pinned behaviour (``validator_decision``), so the suite fails if that behaviour changes
either way. The ground-truth ``decision`` is used only for the false-accept and false-reject
counts, in ``runner``.
"""

from __future__ import annotations

from sitescout.agent import AgentResult, AgentState
from sitescout.agent_eval.cases import Expect


def trajectory_of(state: AgentState) -> tuple[str, ...]:
    """The run's tool calls in order: ``tool``, ``tool:duplicate`` or ``tool:error``."""
    marks = []
    for o in state.observations:
        suffix = ":duplicate" if o.cached else ":error" if o.status == "error" else ""
        marks.append(f"{o.tool}{suffix}")
    return tuple(marks)


def _answer_texts(result: AgentResult) -> list[str]:
    if result.answer is None:
        return []
    return [s.text for _, statements in result.answer.sections() for s in statements]


def _cited(result: AgentResult) -> set[str]:
    if result.answer is None:
        return set()
    return {
        i for _, statements in result.answer.sections() for s in statements for i in s.evidence_ids
    }


def check_case(
    expect: Expect,
    result: AgentResult,
    *,
    actual_decision: str,
    rules: tuple[str, ...],
    counters: dict[str, int],
    trajectory: tuple[str, ...],
) -> list[str]:
    failures: list[str] = []

    def differs(what: str, expected: object, actual: object) -> None:
        if expected != actual:
            failures.append(f"{what}: expected {expected!r}, got {actual!r}")

    differs("first-attempt decision", expect.validator_decision or expect.decision, actual_decision)
    differs("first-attempt rules", tuple(sorted(expect.rules)), rules)
    differs("status", expect.status, result.status)
    differs("termination", expect.termination, result.termination)
    if expect.trajectory is not None:
        differs("trajectory", expect.trajectory, trajectory)
    if expect.attempts is not None:
        differs("validation attempts", expect.attempts, len(result.state.validation_attempts))
    for name, value in expect.counters.items():
        differs(f"counter {name}", value, counters.get(name))
    if missing := sorted(set(expect.cites) - _cited(result)):
        failures.append(f"the final answer does not cite {missing}")
    texts = " ".join(_answer_texts(result)).lower()
    failures += [f"the final answer lacks {p!r}" for p in expect.contains if p.lower() not in texts]
    failures += [f"the final answer contains {p!r}" for p in expect.excludes if p.lower() in texts]
    if result.status == "answered" and result.validation is not None:
        differs("the final answer's validation", True, result.validation.passed)
    if result.status == "fallback":
        differs("a fallback carries no answer", None, result.answer)
    return failures
