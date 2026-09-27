"""Milestone 10, Phase 6 (D-060): a bounded, unscripted live-provider evaluation of the M10
agent, kept strictly separate from the Phase 4 scripted, offline evaluation
(``sitescout.agent_eval``).

Each case (``cases.LiveCase``) is a free-text question; the model chooses every tool call and
its order for itself through the unmodified agent loop (``sitescout.agent``) and the
unmodified real provider (``sitescout.agent_provider``). ``runner`` runs the cases and
classifies each outcome (``instrumentation.classify_result``); ``report`` renders the result.
Nothing here adds a grounding rule, relaxes one, or calls a real provider itself — that is
``scripts/agent_live_eval.py``'s job alone, and it is never run by pytest.
"""

from sitescout.agent_live_eval.cases import (
    CATEGORIES,
    LiveCase,
    LiveCaseError,
    LiveCaseSet,
    LiveExpectation,
    load_live_cases,
)
from sitescout.agent_live_eval.instrumentation import (
    CallUsage,
    FailureCategory,
    RecordingAnthropicClient,
    RecordingOpenAIClient,
    classify_result,
)
from sitescout.agent_live_eval.report import render_report
from sitescout.agent_live_eval.runner import (
    EVAL_VERSION,
    LiveCaseResult,
    LiveEvaluation,
    evaluate_live,
    live_agent_limits,
    prepare_question,
    resolve_site,
    run_live_case,
)

__all__ = [
    "CATEGORIES",
    "EVAL_VERSION",
    "CallUsage",
    "FailureCategory",
    "LiveCase",
    "LiveCaseError",
    "LiveCaseResult",
    "LiveCaseSet",
    "LiveEvaluation",
    "LiveExpectation",
    "RecordingAnthropicClient",
    "RecordingOpenAIClient",
    "classify_result",
    "evaluate_live",
    "live_agent_limits",
    "load_live_cases",
    "prepare_question",
    "render_report",
    "resolve_site",
    "run_live_case",
]
