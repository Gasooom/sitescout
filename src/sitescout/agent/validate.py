"""Milestone 10, Phase 3 (D-058): the agent's answer schema and its grounding layer.

The final answer has the M9 shape (five sections of statements) plus one field per statement,
``quotes``: verbatim quotations from the knowledge chunks the statement cites.

``validate_agent_answer`` runs the M9 validator first, unchanged, on the answer with its quotes
dropped: every number must be copied exactly from a cited record, every statement must cite
records the model was actually shown, no prohibited claim, no evaluative comparison, the exact
grid disclaimer, and so on. An answer that fails any M9 rule fails here. The agent layer adds
only rules about where a claim comes from:

- **Knowledge claims.** A statement that cites a knowledge chunk (a ``kb/`` record) must carry
  at least ``min_quote_words`` words quoted verbatim from a chunk it cites; a quotation that
  is not an exact substring of a cited chunk, or that cites no chunk, is rejected.
- **Structured numbers.** If a statement cites both structured records and knowledge chunks,
  every number in it must come from a structured record. If a number in a statement is also
  shown by a structured record this run fetched, the statement must cite that record: a
  current SiteScout number is never taken from documentation prose.
- **No invented site facts.** A candidate id in a statement must appear in a record this run
  fetched.
- **Unknown stays unknown.** A statement that is not UNKNOWN may not rest only on UNKNOWN
  records.

What this cannot verify: that a paraphrase means what its chunk says (the quotation shows
where it comes from, not that the wording is faithful), and a number that only documentation
holds is accepted from a cited chunk. D-058 records both limits.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sitescout.analyst.validate import (
    Answer,
    Statement,
    StatementKind,
    ToolRecords,
    ValidationIssue,
    ValidationResult,
    collect_session,
    number_tokens,
    validate_answer,
)
from sitescout.evidence import EvidenceRecord

KNOWLEDGE_PREFIX = "kb/"
_CANDIDATE = re.compile(r"\bcand-[0-9a-f]{12}\b")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentStatement(_Model):
    text: str = Field(min_length=1)
    kind: StatementKind
    evidence_ids: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()


class AgentAnswer(_Model):
    direct_answer: tuple[AgentStatement, ...] = Field(min_length=1)
    evidence: tuple[AgentStatement, ...] = ()
    interpretation: tuple[AgentStatement, ...] = ()
    unknowns: tuple[AgentStatement, ...] = ()
    next_investigation: tuple[AgentStatement, ...] = ()

    def sections(self) -> tuple[tuple[str, tuple[AgentStatement, ...]], ...]:
        return (
            ("direct_answer", self.direct_answer),
            ("evidence", self.evidence),
            ("interpretation", self.interpretation),
            ("unknowns", self.unknowns),
            ("next_investigation", self.next_investigation),
        )

    def to_m9(self) -> Answer:
        """The same answer as an M9 ``Answer``: identical statements, quotations dropped."""

        def convert(items: tuple[AgentStatement, ...]) -> tuple[Statement, ...]:
            return tuple(
                Statement(text=s.text, kind=s.kind, evidence_ids=s.evidence_ids) for s in items
            )

        return Answer(
            direct_answer=convert(self.direct_answer),
            evidence=convert(self.evidence),
            interpretation=convert(self.interpretation),
            unknowns=convert(self.unknowns),
            next_investigation=convert(self.next_investigation),
        )


def parse_answer(raw: dict[str, Any]) -> AgentAnswer:
    """The model's raw answer data as an ``AgentAnswer``; a pydantic error if it does not fit."""
    return AgentAnswer.model_validate(raw)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _is_knowledge(record: EvidenceRecord) -> bool:
    return record.id.startswith(KNOWLEDGE_PREFIX)


def _numbers(record: EvidenceRecord) -> set[str]:
    return set(number_tokens(record.display))


def _known_sites(records: dict[str, EvidenceRecord]) -> set[str]:
    found: set[str] = set()
    for record in records.values():
        for text in (record.id, record.claim, record.display, str(record.evidence.value)):
            found.update(_CANDIDATE.findall(text))
    return found


def _check_statement(
    statement_id: str,
    statement: AgentStatement,
    records: dict[str, EvidenceRecord],
    structured_numbers: set[str],
    known_sites: set[str],
    min_quote_words: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def fail(rule: str, message: str) -> None:
        issues.append(ValidationIssue(rule=rule, statement_id=statement_id, message=message))

    cited = [records[i] for i in statement.evidence_ids if i in records]
    knowledge = [r for r in cited if _is_knowledge(r)]
    structured = [r for r in cited if not _is_knowledge(r)]

    # Knowledge claims are quoted from the chunks they cite.
    if knowledge and not statement.quotes:
        fail(
            "knowledge_quote_missing",
            "a statement citing a knowledge chunk must quote it verbatim (quotes)",
        )
    chunk_text = [_squash(r.display) for r in knowledge]
    for quote in statement.quotes:
        if not knowledge:
            fail("quote_without_knowledge_citation", "a quotation needs a cited knowledge chunk")
        elif len(quote.split()) < min_quote_words:
            fail("quote_too_short", f"a quotation needs at least {min_quote_words} words")
        elif not any(_squash(quote) in text for text in chunk_text):
            fail("quote_not_in_cited_chunk", "the quotation is not verbatim in a cited chunk")

    # A current SiteScout number comes from a structured record, not from documentation.
    structured_tokens = set().union(*(_numbers(r) for r in structured)) if structured else set()
    knowledge_tokens = set().union(*(_numbers(r) for r in knowledge)) if knowledge else set()
    for token in dict.fromkeys(number_tokens(statement.text)):
        if structured and knowledge:
            if token not in structured_tokens and token in knowledge_tokens:
                fail(
                    "mixed_number_not_structured",
                    f"{token!r} appears only in a knowledge chunk; a number in a statement that "
                    "cites structured records must come from a structured record",
                )
        elif cited and not structured and token in structured_numbers:
            fail(
                "number_must_cite_structured_record",
                f"{token!r} is shown by a structured record this run fetched; cite that record",
            )

    # No site the run never saw.
    for site in dict.fromkeys(_CANDIDATE.findall(statement.text)):
        if site not in known_sites:
            fail("unknown_site_reference", f"{site} is not in any record this run fetched")

    # UNKNOWN is never turned into a fact.
    if statement.kind != "UNKNOWN" and cited and all(r.type == "UNKNOWN" for r in cited):
        fail(
            "unknown_stated_as_fact",
            "a statement that is not UNKNOWN may not rest only on UNKNOWN records",
        )
    return issues


def validate_agent_answer(
    answer: AgentAnswer, session: ToolRecords, *, min_quote_words: int
) -> ValidationResult:
    """The M9 validator on the answer, then the agent's grounding rules; never raises."""
    result = validate_answer(answer.to_m9(), [session])
    issues = list(result.errors)
    records, _ = collect_session([session])
    structured_numbers = set().union(
        *(_numbers(r) for r in records.values() if not _is_knowledge(r))
    )
    known_sites = _known_sites(records)
    for section, statements in answer.sections():
        for index, statement in enumerate(statements):
            issues += _check_statement(
                f"{section}[{index}]",
                statement,
                records,
                structured_numbers,
                known_sites,
                min_quote_words,
            )
    return ValidationResult(passed=not issues, errors=tuple(issues))


def issues_text(issues: Sequence[ValidationIssue]) -> str:
    return "; ".join(f"{i.rule} ({i.statement_id})" for i in issues)
