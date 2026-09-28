"""Milestone 10, Phase 5 (D-059): what the agent's provider adapters share.

Everything genuinely provider-neutral (the question wrapper, tool-output serialization, the
retry note, unparsed-output handling, secret redaction, the error types) already lives in
``sitescout.analyst.provider_common`` and is reused here unchanged. Two things are the agent's
own: ``check_agent_tools``, because M9's ``provider_common.check_tools`` cannot accept the
agent's nine tools, and ``agent_retry_note`` (Milestone 11), which appends to M9's unchanged
retry note one concrete correction per rule the agent's validator named, including the
agent-only quotation rules M9's note has no instruction for, and names each quotation the
validator could not find, with the chunk's exact text when the model was shown it. Neither touches
``provider_common`` or anything M9 checks, and neither relaxes a rule: the validator still
decides.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from sitescout.agent import AGENT_TOOL_NAMES, records_in
from sitescout.agent.validate import KNOWLEDGE_PREFIX
from sitescout.analyst.provider import ModelContext, ToolCallRecord
from sitescout.analyst.provider_common import ProviderError, retry_note
from sitescout.analyst.validate import ValidationIssue
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.evidence import EvidenceRecord

__all__ = ["CORRECTIONS", "agent_retry_note", "check_agent_tools"]

_MARKUP = frozenset("*`")
_STATEMENT_ID = re.compile(r"([a-z_]+)\[(\d+)\]")

_QUOTE = (
    "use one contiguous span copied exactly from the cited chunk's text as shown (same "
    "capitalization, punctuation, symbols and Markdown characters such as ** or `); do not "
    'paraphrase inside a quotation or join passages with "...". If the chunk does not hold '
    "the words you need, remove the claim or support it from a deterministic record instead."
)
_NUMBER = (
    "every number in that statement, including digits inside names such as Top-30 and units "
    "such as 10 km, must appear in the display of a record cited by that same statement: add "
    "that record's id to its evidence_ids or remove the number."
)
_UNKNOWN_PHRASE = (
    'state it as UNKNOWN using one of the exact absence phrases, such as "not known" or "not '
    'available", word for word.'
)

# One actionable correction per validator rule; only the rules a rejection names are sent.
CORRECTIONS: dict[str, str] = {
    "quote_not_in_cited_chunk": _QUOTE,
    "quote_too_short": "quote at least the minimum number of consecutive words, exactly.",
    "knowledge_quote_missing": (
        "a statement citing a knowledge chunk needs an exact quotation from it in quotes; add "
        "one, or cite a deterministic record instead of the chunk."
    ),
    "quote_without_knowledge_citation": (
        "a quotation needs the knowledge chunk it comes from in that statement's evidence_ids."
    ),
    "grid_disclaimer_missing": (
        "the statement mentions the grid. Grid evidence means mapped infrastructure nearby; it "
        "does not establish grid connection capacity, transformer capacity, land availability "
        f'or permitting. Add this exact sentence to that statement: "{GRID_DISCLAIMER}"'
    ),
    "number_not_grounded": _NUMBER,
    "mixed_number_not_structured": (
        "a statement citing both kinds of record takes its numbers from the deterministic "
        "record; cite it, or leave out a number only the chunk holds, such as a section "
        'number ("SPEC §8"). Quotations belong in quotes, not in the text.'
    ),
    "number_must_cite_structured_record": (
        "that number is shown by a deterministic record this session; cite that record."
    ),
    "comparison_relation_mismatch": (
        "remove the comparative word (higher, lower, more, less, greater, smaller, larger) and "
        "state the values side by side, such as 'X for A and Y for B'."
    ),
    "prohibited_comparison_language": (
        "remove the evaluative word (better, worse, best, prefer, winner, recommend)."
    ),
    "approximation_language": (
        "the word quoted in that entry's message is rejected wherever it appears as a whole "
        'word, even when it is not an approximation ("information about", "around Kigali"). '
        'Rephrase so the word is gone ("on", "in", "near") and copy exact numbers only.'
    ),
    "number_word": (
        "the word quoted at the start of that entry's message is rejected wherever it appears "
        'as a whole word, even when it is not a quantity ("one another", "one of", "no one"). '
        'Rephrase so the word is gone, for example "each other" or "a"; if it was a quantity, '
        "write it as digits copied from a cited record's display."
    ),
    "arithmetic_expression": (
        "do not compute: write only numbers copied from records, with no arithmetic."
    ),
    "unknown_stated_as_fact": "the cited records are all UNKNOWN; " + _UNKNOWN_PHRASE,
    "unknown_must_state_absence": _UNKNOWN_PHRASE,
    "unknown_site_reference": "name only candidate ids a tool returned this session.",
    "unknown_evidence_id": "cite only ids of records you were shown this session.",
    "missing_citation": "cite the records that support every statement.",
    "calculated_not_grounded": (
        "a CALCULATED statement must cite a record whose own type is CALCULATED; cite it, or "
        "use the kind of the records you cite."
    ),
    "prohibited_claim": ("SiteScout never makes that claim; replace it with an UNKNOWN statement."),
    "malformed_answer": (
        "reply with one complete JSON object matching the schema and nothing else; keep it to "
        "3 to 6 short statements so it is complete."
    ),
}


def check_agent_tools(context: ModelContext) -> None:
    """Refuse a context that offers anything but exactly the nine agent capabilities."""
    names = sorted(tool.name for tool in context.tools)
    if names != sorted(AGENT_TOOL_NAMES):
        raise ProviderError(f"expected exactly the nine agent tools, got {names}")


def _squash(text: str) -> str:
    return " ".join(text.split())  # the validator's own whitespace normalization


def _loose(text: str) -> tuple[str, list[int]]:
    """``text`` without Markdown emphasis and code marks, in lower case, with the index in
    ``text`` of every character kept (a character whose lower case is longer stays as is)."""
    kept: list[str] = []
    where: list[int] = []
    for index, char in enumerate(text):
        if char in _MARKUP:
            continue
        lower = char.lower()
        kept.append(lower if len(lower) == 1 else char)
        where.append(index)
    return "".join(kept), where


def _exact_span(quote: str, chunk: str) -> str | None:
    """The span of ``chunk``, exactly as shown, that ``quote`` matches once Markdown marks,
    capitalization and spacing are ignored; ``None`` if there is none. It is only shown to the
    model: the validator still requires the retried quotation to be verbatim."""
    text = _squash(chunk)
    loose, where = _loose(text)
    wanted = _loose(_squash(quote))[0].strip()
    at = loose.find(wanted) if wanted else -1
    if at < 0:
        return None
    start, end = where[at], where[at + len(wanted) - 1] + 1
    while start > 0 and text[start - 1] in _MARKUP:  # keep "**" with the word it opens
        start -= 1
    while end < len(text) and text[end] in _MARKUP:
        end += 1
    return text[start:end]


def _find(quote: str, chunks: Sequence[EvidenceRecord]) -> tuple[str, str] | None:
    """The first chunk holding ``quote`` loosely, as (its id, its exact span in JSON)."""
    for chunk in chunks:
        if (span := _exact_span(quote, chunk.display)) is not None:
            return chunk.id, json.dumps(span, ensure_ascii=False)
    return None


def _statement(answer: dict[str, Any], statement_id: str) -> dict[str, Any] | None:
    match = _STATEMENT_ID.fullmatch(statement_id)
    if match is None:
        return None
    items, index = answer.get(match.group(1)), int(match.group(2))
    if not isinstance(items, list) or index >= len(items):
        return None
    return items[index] if isinstance(items[index], dict) else None


def _quote_hints(
    errors: Sequence[ValidationIssue],
    previous_answer: dict[str, Any] | None,
    transcript: Sequence[ToolCallRecord],
) -> list[str]:
    """For each statement rejected for a quotation, which of its quotations failed and, when a
    chunk the model was shown holds the same words, that chunk's exact text. The validator's
    message names the statement only, and a model that cannot tell which quotation failed,
    or how, tends to send it again unchanged."""
    flagged = dict.fromkeys(e.statement_id for e in errors if e.rule == "quote_not_in_cited_chunk")
    if not flagged or not isinstance(previous_answer, dict):
        return []
    chunks: dict[str, EvidenceRecord] = {}
    for call in transcript:
        found = records_in(call.result)[0]
        chunks.update({i: r for i, r in found.items() if i.startswith(KNOWLEDGE_PREFIX)})
    lines: list[str] = []
    for statement_id in flagged:
        statement = _statement(previous_answer, statement_id)
        if statement is None:
            continue
        ids = [i for i in statement.get("evidence_ids") or [] if isinstance(i, str)]
        cited = [chunks[i] for i in ids if i in chunks]
        others = [r for i, r in chunks.items() if i not in ids]
        for quote in statement.get("quotes") or []:
            if not isinstance(quote, str) or any(
                _squash(quote) in _squash(r.display) for r in cited
            ):
                continue
            shown = json.dumps(quote, ensure_ascii=False)
            if match := _find(quote, cited):
                chunk, exact = match
                lines.append(
                    f"- {statement_id}: the quotation {shown} differs from {chunk}, which "
                    f"reads {exact}. Copy that exactly, or drop the quotation."
                )
            elif match := _find(quote, others):
                chunk, exact = match
                lines.append(
                    f"- {statement_id}: the quotation {shown} is from {chunk}, which that "
                    f"statement does not cite; it reads {exact}. Cite {chunk} and copy that "
                    "exactly, or drop the quotation."
                )
            else:
                lines.append(
                    f"- {statement_id}: the quotation {shown} is not in any chunk you were "
                    "shown. Copy one contiguous span exactly from a cited chunk, or drop the "
                    "quotation and the claim it supports."
                )
    return lines


def agent_retry_note(
    errors: Sequence[ValidationIssue],
    previous_answer: dict[str, Any] | None,
    transcript: Sequence[ToolCallRecord] = (),
) -> str:
    """M9's retry note, unchanged, plus a correction for each rule the rejection named, which
    quotations failed (from the chunks in ``transcript``), and the agent's own copy
    instruction (a copied statement keeps its quotes too)."""
    rules = list(dict.fromkeys(e.rule for e in errors))
    lines = [
        "",
        "Corrections for the rules named above: where they differ from the general advice "
        "above, follow these.",
    ]
    lines += [f"- {rule}: {CORRECTIONS[rule]}" for rule in rules if rule in CORRECTIONS]
    hints = _quote_hints(errors, previous_answer, transcript)
    if hints:
        lines += ["Quotations the validator could not find, one by one:", *hints]
    lines.append("- When you copy a statement that was not named, keep its quotes exactly as well.")
    return retry_note(errors, previous_answer) + "\n".join(lines)
