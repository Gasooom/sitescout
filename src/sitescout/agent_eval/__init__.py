"""Milestone 10, Phase 4 (D-058): deterministic evaluation of the agent's grounding and loop.

Scripted-provider cases (``cases``) are run through the production agent loop (``runner``),
checked against what each should do (``checks``) and reported as counts (``report``). It
measures the validator and the loop, on a small fixed case set; it does not measure a model.
"""

from sitescout.agent_eval.cases import CATEGORIES, Case, CaseError, CaseSet, load_cases
from sitescout.agent_eval.report import render_report
from sitescout.agent_eval.runner import (
    EVAL_VERSION,
    CaseResult,
    Evaluation,
    evaluate,
    run_case,
    run_case_with_result,
)

__all__ = [
    "CATEGORIES",
    "EVAL_VERSION",
    "Case",
    "CaseError",
    "CaseResult",
    "CaseSet",
    "Evaluation",
    "evaluate",
    "load_cases",
    "render_report",
    "run_case",
    "run_case_with_result",
]
