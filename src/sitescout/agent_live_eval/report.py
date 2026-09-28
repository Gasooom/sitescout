"""Milestone 10, Phase 6 (D-060): the live evaluation as a Markdown report.

Filled from ``templates/agent_live_eval.md`` with ``str.format``. Unlike the Phase 4 report,
this one is **not** reproducible run to run: a real model is free to answer differently on
different days. ``render_report`` is a pure, deterministic function of one already-finished
``LiveEvaluation``, which is what the offline tests check; the non-determinism lives entirely
in what a live run of ``scripts/agent_live_eval.py`` produces as that input, never in this
module.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sitescout.agent_live_eval.cases import CATEGORIES
from sitescout.agent_live_eval.runner import LiveEvaluation
from sitescout.config import LivePriceSettings

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "agent_live_eval.md"

CAVEAT = (
    "This is one run against one provider and one model, on the date above; it is not "
    "evidence that the agent is reliable in general. See `reports/agent_eval.md` for the "
    "deterministic, reproducible evaluation (73 scripted cases, no live model)."
)


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _setup(e: LiveEvaluation) -> str:
    rows = [
        ("Evaluation version", f"`{e.version}`"),
        ("Generated (UTC)", e.generated_at or "not set (offline run)"),
        ("Provider", e.provider),
        ("Model", e.model),
        ("Case set version", e.case_set_version),
        ("Cases in file", e.case_count),
        ("Skipped (site not found)", e.skipped),
        ("Infrastructure errors", e.infra_errors),
        *((f"`{name}`", value) for name, value in e.limits.items()),
    ]
    return _table(("Setting", "Value"), rows)


def _cases(e: LiveEvaluation) -> str:
    rows = [
        (
            r.id,
            CATEGORIES.get(r.category, r.category),
            r.title,
            r.status,
            r.failure_category or r.skip_reason or "",
            ", ".join(r.trajectory) or "none",
            r.validation_attempts,
            f"{sum(c.latency_s for c in r.calls):.1f}s" if r.calls else "-",
        )
        for r in e.cases
    ]
    header = ("Id", "Category", "Title", "Status", "Outcome", "Tools used", "Attempts", "Latency")
    return _table(header, rows)


def _failure_categories(e: LiveEvaluation) -> str:
    rows = [(f"`{k}`", v) for k, v in e.by_failure_category.items()]
    return _table(("Failure category", "Cases"), rows) if rows else "No case ran."


def _categories(e: LiveEvaluation) -> str:
    rows = [(f"{c}: {CATEGORIES[c]}", e.by_category.get(c, 0)) for c in sorted(CATEGORIES)]
    return _table(("Category", "Cases run"), rows)


def _expectations(e: LiveEvaluation) -> str:
    rows = []
    for r in e.cases:
        for key, met in r.expect_met.items():
            rows.append((r.id, key, "met" if met else "**unmet**"))
    return _table(("Case", "Expectation", "Result"), rows) if rows else "No case declared one."


def _validation_detail(e: LiveEvaluation) -> str:
    """Rendered only when at least one attempt actually failed (D-060 diagnostics addendum):
    the rule, the flagged statement and the validator's own message for every failed attempt,
    from the same unchanged ``sitescout.agent.validate`` issues already behind §3's counts.
    Passed attempts, and cases with none failed, are omitted; an all-passing run renders no
    section at all, never an empty one."""
    rows = [
        (r.id, index, issue.rule, issue.statement_id, issue.message)
        for r in e.cases
        for index, attempt in enumerate(r.validation_attempts_detail, start=1)
        if not attempt.passed
        for issue in attempt.errors
    ]
    if not rows:
        return ""
    header = ("Case", "Attempt", "Rule", "Statement", "Message")
    return (
        "\n## 8. Validation detail (failed attempts)\n\n"
        "For every attempt that failed the agent validator (`sitescout.agent.validate`, "
        "unchanged): the rule it flagged, the statement, and the validator's own message. "
        "A passed attempt, and a case where every attempt passed, is omitted. This adds no "
        "rule and changes no outcome; see §3 for the failure category each case actually "
        "ended in.\n\n" + _table(header, rows) + "\n"
    )


def _or_unavailable(value: int | None) -> str:
    return str(value) if value is not None else "not available"


def _usage(e: LiveEvaluation, price_table: dict[str, LivePriceSettings] | None) -> str:
    lines = [
        f"Total latency across every provider call: {e.total_latency_s:.1f}s.",
        f"Total input tokens: {_or_unavailable(e.total_input_tokens)}.",
        f"Total output tokens: {_or_unavailable(e.total_output_tokens)}.",
    ]
    price = (price_table or {}).get(e.model)
    if price is None or e.total_input_tokens is None or e.total_output_tokens is None:
        lines.append(
            "Estimated cost: not available (no price configured for this model, or no "
            "usage was reported by the provider)."
        )
    else:
        cost = (
            e.total_input_tokens / 1000 * price.input_per_1k
            + e.total_output_tokens / 1000 * price.output_per_1k
        )
        lines.append(f"Estimated cost: ${cost:.4f} (best-effort, from a configured price table).")
    return "\n".join(lines)


def render_report(
    evaluation: LiveEvaluation, price_table: dict[str, LivePriceSettings] | None = None
) -> str:
    rendered = TEMPLATE.read_text(encoding="utf-8").format(
        setup=_setup(evaluation),
        categories=_categories(evaluation),
        failures=_failure_categories(evaluation),
        cases=_cases(evaluation),
        expectations=_expectations(evaluation),
        usage=_usage(evaluation, price_table),
        validation_detail=_validation_detail(evaluation),
        caveat=CAVEAT,
    )
    # Strips the blank line the {validation_detail} placeholder leaves behind when it renders
    # empty, so a clean run's report is byte-identical to one with no diagnostics section.
    return rendered.rstrip("\n") + "\n"
