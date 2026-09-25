"""Milestone 9, phase 3a: a deterministic, scripted model for offline tests.

``FakeModel`` never chooses anything: it returns exactly the :class:`ModelStep` sequence it
was built with (or, for more control, whatever a plain function returns), so a test can
script any scenario — a tool call, several tool calls, a final answer, an invalid answer
followed by a corrected one, two invalid answers in a row, an unknown tool, bad arguments —
and get the same result every time. It uses no randomness and touches no file or network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sitescout.analyst.provider import ModelContext, ModelStep, Provider


class ScriptExhausted(Exception):
    """The scripted steps ran out before the loop stopped; the test's script is too short."""


class FakeModel(Provider):
    """A scripted, deterministic ``Provider``.

    Give it either a fixed sequence of steps (returned one at a time, in order, regardless
    of ``context``) or a function ``context -> ModelStep`` for scripts that need to look at
    the transcript or the validation errors of a retry.
    """

    def __init__(self, steps: Sequence[ModelStep] | Callable[[ModelContext], ModelStep]) -> None:
        self._fn = steps if callable(steps) else _sequence(steps)

    def next_step(self, context: ModelContext) -> ModelStep:
        return self._fn(context)


def _sequence(steps: Sequence[ModelStep]) -> Callable[[ModelContext], ModelStep]:
    remaining = list(steps)

    def next_step(_: ModelContext) -> ModelStep:
        if not remaining:
            raise ScriptExhausted("the fake model's scripted steps ran out")
        return remaining.pop(0)

    return next_step
