"""Milestone 10, Phase 4 (D-058): the evaluation as a Markdown report.

Filled from ``templates/agent_eval.md`` with ``str.format``; deterministic (no timestamps, no
paths that vary), so the committed report changes only when the evaluation does.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sitescout.agent_eval.cases import CATEGORIES
from sitescout.agent_eval.runner import Evaluation, Tally

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "agent_eval.md"
_TALLY_HEAD = ("Cases", "True accept", "True reject", "False accept", "False reject", "N/A", "Pass")


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _tally_row(label: str, t: Tally) -> list[object]:
    return [
        label,
        t.cases,
        t.true_accept,
        t.true_reject,
        t.false_accept,
        t.false_reject,
        t.not_applicable,
        f"{t.passed} of {t.cases}",
    ]


def _setup(e: Evaluation) -> str:
    rows = [
        ("Evaluation version", f"`{e.version}`"),
        ("World", e.world),
        ("Cases", e.case_count),
        ("Corpus chunks (context only)", e.corpus_chunks),
        ("Corpus fingerprint (context only)", f"`{e.corpus_fingerprint}`"),
        ("`knowledge_top_k`", e.knowledge_top_k),
        *((f"`{name}`", value) for name, value in e.limits.items()),
    ]
    return _table(("Setting", "Value"), rows)


def _tags(e: Evaluation) -> str:
    return _table(("Tag", *_TALLY_HEAD), [_tally_row(f"`{tag}`", t) for tag, t in e.by_tag.items()])


def _categories(e: Evaluation) -> str:
    rows = [_tally_row(f"{c}: {CATEGORIES[c]}", t) for c, t in e.by_category.items()]
    rows.append(_tally_row("**All cases**", e.total))
    return _table(("Category", *_TALLY_HEAD), rows)


def _counts(counts: dict[str, int], label: str) -> str:
    return _table((label, "Cases"), [(f"`{k}`", v) for k, v in counts.items()] or [("none", 0)])


def _terminations(e: Evaluation) -> str:
    extra = f"Runs that answered after one rejected answer (retries): {e.retries}. "
    extra += f"Fallbacks: {e.status_counts.get('fallback', 0)}."
    return "\n\n".join(
        [_counts(e.termination_counts, "Termination"), _counts(e.status_counts, "Status"), extra]
    )


def _limits(e: Evaluation) -> str:
    rows = [
        (f"{r.id}", r.outcome.replace("_", " "), r.known_limit)
        for r in e.cases
        if r.known_limit is not None
    ]
    return _table(("Case", "Counted as", "Why"), rows) if rows else "None."


def _cases(e: Evaluation) -> str:
    rows = [
        (
            r.id,
            r.kind,
            r.title,
            r.expected_decision,
            r.actual_decision,
            r.outcome.replace("_", " "),
            r.termination,
            "yes" if r.passed else "**no**",
        )
        for r in e.cases
    ]
    header = ("Id", "Kind", "Title", "Ground truth", "Validator", "Outcome", "Ended", "Pass")
    return _table(header, rows)


def render_report(evaluation: Evaluation) -> str:
    return TEMPLATE.read_text(encoding="utf-8").format(
        setup=_setup(evaluation),
        categories=_categories(evaluation),
        tags=_tags(evaluation),
        rules=_counts(evaluation.rule_counts, "Rule"),
        terminations=_terminations(evaluation),
        limits=_limits(evaluation),
        cases=_cases(evaluation),
    )
