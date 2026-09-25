"""Milestone 9, phase 2: the answer schema and the deterministic answer validator.

``Answer`` is the strict shape a future model (phase 3) must return: five sections, each a
list of ``Statement``. ``validate_answer(answer, session)`` checks an answer against the
tool results of its own session (``session``: any of the phase 1 tool result objects, or the
``ToolRecords`` helper below) and returns a :class:`ValidationResult` — never an exception,
so a caller can always inspect ``passed`` and, on failure, retry with the listed errors.

No model, network access or file write is involved. Every rule here is deterministic text
and structure checking; nothing is inferred about meaning.

**What a statement may say.** Every number in ``text`` must be the exact substring of a
number in the display text of a record the statement cites (``evidence_ids``): no rounding,
no reformatting (comma or scale), no arithmetic, no derived range, no approximation word and
no spelled-out number. A statement that is not ``UNKNOWN`` must cite at least one record that
exists in the session; a ``CALCULATED`` statement must cite at least one record whose own
type is ``CALCULATED``, so the calculation itself is always a tool result, never something
the text works out. An ``UNKNOWN`` statement may cite nothing, but must say plainly that
something is unavailable. Comparative words ("greater", "lower", ...) are allowed only
alongside a cited ``compare_sites`` field whose ``relation`` agrees; evaluative words
("better", "winner", "recommend", ...) are never allowed. A small set of CLAUDE.md's
prohibited claims (grid approval or sufficient capacity, feasibility outside the exact
disclaimer, viability, revenue, land or permit "confirmed") are rejected outright, and any
statement mentioning "grid" must contain the project's exact grid disclaimer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from sitescout.analyst.tools import ComparisonField
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.evidence import EvidenceRecord, EvidenceType

StatementKind = EvidenceType  # RETRIEVED_FACT, CALCULATED, INFERRED or UNKNOWN


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- The answer schema (phase 3 must produce exactly this shape) --------------------------


class Statement(_Model):
    """One sentence or short claim, with what kind of statement it is and what it cites."""

    text: str = Field(min_length=1)
    kind: StatementKind
    evidence_ids: tuple[str, ...] = ()


class Answer(_Model):
    """The five-part structured answer (SPEC §11's answer shape)."""

    direct_answer: tuple[Statement, ...] = Field(min_length=1)
    evidence: tuple[Statement, ...] = ()
    interpretation: tuple[Statement, ...] = ()
    unknowns: tuple[Statement, ...] = ()
    next_investigation: tuple[Statement, ...] = ()

    def sections(self) -> tuple[tuple[str, tuple[Statement, ...]], ...]:
        return (
            ("direct_answer", self.direct_answer),
            ("evidence", self.evidence),
            ("interpretation", self.interpretation),
            ("unknowns", self.unknowns),
            ("next_investigation", self.next_investigation),
        )


class ToolRecords(_Model):
    """A minimal session item: evidence records and comparison fields, built by hand.

    Real tool results (``FindResult``, ``SiteResult``, ...) work directly as session items;
    this is for tests and for bundling records from more than one tool call.
    """

    records: tuple[EvidenceRecord, ...] = ()
    comparisons: tuple[ComparisonField, ...] = ()


# --- Validation result ----------------------------------------------------------------------


class ValidationIssue(_Model):
    rule: str
    statement_id: str
    message: str


class ValidationResult(_Model):
    passed: bool
    errors: tuple[ValidationIssue, ...] = ()


# --- Collecting the session's records --------------------------------------------------------


def _walk(
    value: object, records: dict[str, EvidenceRecord], comparisons: dict[str, ComparisonField]
) -> None:
    if isinstance(value, EvidenceRecord):
        records[value.id] = value
    elif isinstance(value, ComparisonField):
        comparisons[value.id] = value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _walk(getattr(value, name), records, comparisons)
    elif isinstance(value, dict):
        for item in value.values():
            _walk(item, records, comparisons)
    elif isinstance(value, list | tuple):
        for item in value:
            _walk(item, records, comparisons)


def collect_session(
    session: Sequence[BaseModel],
) -> tuple[dict[str, EvidenceRecord], dict[str, ComparisonField]]:
    """Every evidence record and comparison field reachable from this session's tool results.

    Only records that were actually returned to the model in this session are ever trusted;
    an id that merely exists elsewhere in the repository does not count.
    """
    records: dict[str, EvidenceRecord] = {}
    comparisons: dict[str, ComparisonField] = {}
    for result in session:
        _walk(result, records, comparisons)
    return records, comparisons


# --- Text scanning: identifiers, numbers, operators, words --------------------------------

# Matched first and replaced by a token with no digit and no operator character, so ids
# (which contain hex digits and, for OSM objects, a slash) are never mistaken for numbers
# or for a division sign.
_IDENTIFIER = re.compile(
    r"\bcand-[0-9a-f]{12}\b|\bcsv-[0-9a-f]{12}\b|\btx-[0-9a-f]{12}\b|"
    r"\b(?:node|way|relation)/\d+\b"
)
_ID_TOKEN = "IDTOKEN"

# A number: optional sign, digit groups with proper thousands commas (a comma must be
# followed by exactly three digits, so a trailing sentence comma after "131,688," is never
# swallowed), optional decimal part, optional trailing percent. Matched on masked text, so
# an id's digits never appear. The leading "-" is consumed only when it is not itself
# preceded by a letter or digit, so "Top-30" yields "30" (the hyphen there is punctuation,
# not a sign) while a genuine negative coordinate such as "-1.96770" keeps its sign.
_NUMBER = re.compile(r"(?<!\w)-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?")

# =, +, ×, *, / and ÷ never belong in ordinary prose; a hyphen or minus is arithmetic only
# between two numbers (so "Top-30" and "cand-abc123" are untouched: masking removes the
# second case, and a letter never satisfies \d on the other side of the hyphen).
_OPERATOR_CHAR = re.compile(r"[=+×÷/]|\*(?!\*)")
_ARITHMETIC_HYPHEN = re.compile(r"\d[\d,]*(?:\.\d+)?\s*[-−]\s*\d")

_APPROXIMATION_WORDS = (
    "about", "approximately", "roughly", "around", "nearly", "almost", "half", "twice",
    "double", "triple", "quarter", "third", "dozen", "times", "percent of", "probably",
    "likely", "presumably", "possibly", "might have", "should have",
)  # fmt: skip
_APPROXIMATION_SYMBOLS = ("~", "≈")
_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand",
)  # fmt: skip

_PROHIBITED_COMPARISON_PHRASES = (
    "better", "worse", "best", "prefer", "preferred", "winner", "recommend", "recommended",
    "recommendation", "ranks above", "overall winner",
)  # fmt: skip
_COMPARATIVE_WORDS = ("greater", "higher", "more", "less", "lower", "smaller", "larger")

# CLAUDE.md's prohibited claims (never grid feasibility/approval/capacity, viability,
# revenue), plus the M9 plan's land/owner/permit "confirmed as fact" examples. Narrow,
# affirmative phrasings only: "land availability is unknown" must stay allowed.
_PROHIBITED_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("feasibility_claim", r"feasib\w*"),
        ("accuracy_claim", r"\baccura(?:cy|te|tely)\b"),
        (
            "sufficient_capacity_claim",
            r"\b(?:sufficient|enough|adequate)\b[^.]{0,25}\bcapacity\b"
            r"|\bcapacity\b[^.]{0,25}\bis\s+(?:sufficient|enough|adequate)\b",
        ),
        ("approval_claim", r"\bapprov(?:ed|al|als)\b"),
        ("viability_claim", r"\bviab(?:le|ility)\b"),
        ("revenue_claim", r"\brevenue\b"),
        (
            "land_available_claim",
            r"\bland\s+is\s+available\b"
            r"|\bland\s+availability\s+(?:is|has\s+been)\s+confirmed\b",
        ),
        (
            "owner_willing_claim",
            r"\b(?:land)?owner(?:'s)?\s+(?:is\s+)?willing(?:ness)?\s*(?:is|has\s+been)?"
            r"\s*confirmed\b|\b(?:land)?owner\s+is\s+willing\b",
        ),
        (
            "permit_approved_claim",
            r"\bpermits?\s+(?:is|are|has\s+been|have\s+been)\s+(?:approved|granted)\b",
        ),
    )
)

_ABSENCE_MARKERS = (
    "unknown", "not known", "not available", "not established", "no data", "not mapped",
    "cannot be determined", "not determined", "not provided", "has not been confirmed",
    "is not confirmed",
)  # fmt: skip


def _mask(text: str) -> str:
    return _IDENTIFIER.sub(_ID_TOKEN, text)


def _numbers(text: str) -> list[str]:
    return _NUMBER.findall(text)


def number_tokens(text: str) -> list[str]:
    """The number tokens the validator sees in ``text`` (identifiers masked first).

    Public so the phase 3c scenario evaluation counts numbers exactly as the validator does.
    """
    return _numbers(_mask(text))


def _contains_word(text: str, word: str) -> bool:
    if " " in word:
        return word in text
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


# --- Validating one statement ----------------------------------------------------------------


def _validate_statement(
    statement_id: str,
    statement: Statement,
    records: dict[str, EvidenceRecord],
    comparisons: dict[str, ComparisonField],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def fail(rule: str, message: str) -> None:
        issues.append(ValidationIssue(rule=rule, statement_id=statement_id, message=message))

    text = statement.text
    lowered = text.lower()
    masked = _mask(text)

    # 1. Every cited id must exist in this session.
    cited_records = []
    for evidence_id in statement.evidence_ids:
        record = records.get(evidence_id)
        if record is None and evidence_id not in comparisons:
            fail("unknown_evidence_id", f"{evidence_id!r} is not a record from this session")
        elif record is not None:
            cited_records.append(record)

    # 2. Citation requirement by kind.
    if statement.kind == "UNKNOWN":
        if not any(_contains_word(lowered, marker) for marker in _ABSENCE_MARKERS):
            fail(
                "unknown_must_state_absence",
                "an UNKNOWN statement must say plainly that something is unavailable",
            )
    elif not statement.evidence_ids:
        fail("missing_citation", f"a {statement.kind} statement must cite at least one record")
    elif statement.kind == "CALCULATED" and not any(r.type == "CALCULATED" for r in cited_records):
        fail(
            "calculated_not_grounded",
            "a CALCULATED statement must cite a record that is itself CALCULATED: the "
            "number must already be a tool result, never worked out in the answer",
        )

    # 3. Every number in the text must be an exact token from a cited record's display.
    allowed = {token for r in cited_records for token in _numbers(_mask(r.display))}
    for token in _numbers(masked):
        if token not in allowed:
            fail(
                "number_not_grounded",
                f"{token!r} does not appear, character for character, in a cited record's "
                "display text",
            )

    # 4. No arithmetic: forbidden operator characters, or a hyphen/minus between two numbers.
    if _OPERATOR_CHAR.search(masked):
        fail("arithmetic_expression", "an arithmetic operator character is not allowed")
    if _ARITHMETIC_HYPHEN.search(masked):
        fail("arithmetic_expression", "a hyphen or minus between two numbers is not allowed")

    # 5. No approximation, hedging or spelled-out numbers.
    for word in _APPROXIMATION_WORDS:
        if _contains_word(lowered, word):
            fail("approximation_language", f"{word!r} is not allowed")
    for symbol in _APPROXIMATION_SYMBOLS:
        if symbol in text:
            fail("approximation_language", f"{symbol!r} is not allowed")
    for word in _NUMBER_WORDS:
        if _contains_word(lowered, word):
            fail("number_word", f"{word!r} must be a digit, copied from a cited record")

    # 6. Prohibited claims (CLAUDE.md), ignoring the exact grid disclaimer if present.
    without_disclaimer = text.replace(GRID_DISCLAIMER, "")
    for name, pattern in _PROHIBITED_CLAIMS:
        if pattern.search(without_disclaimer):
            fail("prohibited_claim", f"{name}: this claim is never supported by SiteScout data")

    # 7. The exact grid disclaimer whenever "grid" is mentioned.
    if _contains_word(lowered, "grid") and GRID_DISCLAIMER not in text:
        fail(
            "grid_disclaimer_missing",
            f"a statement mentioning grid must include: {GRID_DISCLAIMER!r}",
        )

    # 8. Comparisons: evaluative words are never allowed; comparative words need an agreeing
    #    compare_sites relation.
    for phrase in _PROHIBITED_COMPARISON_PHRASES:
        if _contains_word(lowered, phrase):
            fail("prohibited_comparison_language", f"{phrase!r} is not allowed")
    comparative = [word for word in _COMPARATIVE_WORDS if _contains_word(lowered, word)]
    if comparative:
        cited_comparisons = [comparisons[i] for i in statement.evidence_ids if i in comparisons]
        directional = [c for c in cited_comparisons if c.relation in ("a_greater", "b_greater")]
        if not directional:
            fail(
                "comparison_relation_mismatch",
                f"{comparative!r} needs a cited compare_sites field whose relation is "
                "a_greater or b_greater",
            )

    return issues


# --- The entry point -----------------------------------------------------------------------


def validate_answer(answer: Answer, session: Sequence[BaseModel]) -> ValidationResult:
    """Check ``answer`` against the tool results of ``session``; never raises."""
    records, comparisons = collect_session(session)
    issues: list[ValidationIssue] = []
    for section, statements in answer.sections():
        for index, statement in enumerate(statements):
            issues += _validate_statement(f"{section}[{index}]", statement, records, comparisons)
    return ValidationResult(passed=not issues, errors=tuple(issues))
