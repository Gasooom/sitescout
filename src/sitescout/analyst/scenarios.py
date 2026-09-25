"""Milestone 9, phase 3c: the SiteScout Analyst's scenario evaluation (SPEC §11).

``run_evaluation`` puts every question of the fixed scenario set
(``paths.analyst_scenarios``) to the analyst loop with the configured provider and scores
each answer with deterministic checks only: which tools were called and with which
arguments, whether every number is copied exactly from a cited record's display text,
whether any evaluative, arithmetic or approximate language appears, whether the answer
cites what it should, says what it must and avoids what it must not, and whether the exact
grid disclaimer (``briefs.GRID_DISCLAIMER``) is present. Nothing here judges whether an
answer "looks reasonable".

Each run's answer is also re-validated from the recorded transcript alone, so the log is
self-consistent: the records the model saw are rebuilt from the logged tool outputs and
``validate_answer`` is run again. Answers are never repaired: a rejected answer gets only
the loop's single retry, and every rejection is recorded.

Outputs: ``reports/analyst_eval.md`` (``paths.analyst_eval_report``) and the full log with
every tool output, ``data/processed/analyst_eval.json`` (git-ignored). With ``offline``,
only the deterministic parts run (preflight, the number-format probes and the scripted
scenario) and nothing is written.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sitescout.analyst.fake_model import FakeModel
from sitescout.analyst.provider import ModelContext, ModelStep, Provider, ToolCallRequest
from sitescout.analyst.run import FALLBACK_LABEL, RunResult, run_analyst
from sitescout.analyst.tools import (
    TOOLS,
    AnalystData,
    ComparisonField,
    FindQuery,
    find_sites,
    get_site,
    network_contribution,
)
from sitescout.analyst.validate import (
    Answer,
    Statement,
    ToolRecords,
    ValidationResult,
    number_tokens,
    validate_answer,
)
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.config import AnalystSettings, Config, read_yaml
from sitescout.evidence import EvidenceRecord

log = logging.getLogger(__name__)

LOG_FILE = "analyst_eval.json"
CATEGORIES = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L")

# The validator rejects these everywhere (D-052); the evaluation also rejects the wider list
# below in comparison scenarios, as the phase 3c brief requires.
EVALUATIVE_EVERYWHERE = (
    "better", "worse", "best", "prefer", "preferred", "winner", "recommend", "recommended",
    "recommendation", "ranks above", "overall winner",
)  # fmt: skip
EVALUATIVE_IN_COMPARISONS = (
    "superior", "inferior", "recommended over", "ranking", "top choice", "should choose",
    "should pick", "stronger candidate", "weaker candidate", "more suitable", "less suitable",
    "outperforms",
)  # fmt: skip


class EvaluationError(Exception):
    """The scenario set is invalid, or a scenario's site no longer has its stated property."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- The scenario set --------------------------------------------------------------------------


class SiteFact(_Model):
    """What a scenario relies on about one site; checked against the tools before any call."""

    selected_mclp: bool | None = None
    selected_top30: bool | None = None
    reason_status: Literal["ESTABLISHED", "UNKNOWN", "NOT_APPLICABLE"] | None = None
    grid_evidence_status: Literal["CALCULATED", "UNKNOWN"] | None = None


class ToolArguments(_Model):
    tool: str
    arguments: dict[str, Any]


class Trap(_Model):
    """A number the question tempts the model to work out. The harness computes it only to
    check that it does not appear in the answer; it is never shown."""

    kind: Literal["difference", "percent_difference", "network_mean"]
    field: str
    a: str | None = None
    b: str | None = None


class Expectation(_Model):
    status: Literal["answered", "fallback", "either"] = "answered"
    tools_include: tuple[str, ...] = ()
    tools_any: tuple[str, ...] = ()
    tool_arguments: tuple[ToolArguments, ...] = ()
    cites_prefix: tuple[str, ...] = ()
    contains_any: tuple[tuple[str, ...], ...] = ()
    not_contains: tuple[str, ...] = ()
    unknown_statement: bool = False
    grid_disclaimer: bool = False
    comparison: bool = False
    retried: bool | None = None
    traps: tuple[Trap, ...] = ()

    @model_validator(mode="after")
    def _known_tools(self) -> Expectation:
        named = {*self.tools_include, *self.tools_any, *(t.tool for t in self.tool_arguments)}
        if unknown := sorted(named - set(TOOLS)):
            raise ValueError(f"unknown tools {unknown}")
        return self


class Scenario(_Model):
    id: str = Field(pattern=r"^[A-L]\d+$")
    category: Literal["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]
    title: str
    question: str = Field(min_length=1)
    mode: Literal["live", "live_sabotaged_first_answer", "scripted_invalid_twice"] = "live"
    sites: dict[str, SiteFact] = {}
    probes: str | None = None  # a candidate id: run the number-format probes on its records
    expect: Expectation

    @model_validator(mode="after")
    def _id_matches_category(self) -> Scenario:
        if self.id[0] != self.category:
            raise ValueError(f"scenario {self.id} is not in category {self.category}")
        if self.mode != "live" and not self.sites:
            raise ValueError(f"scenario {self.id} needs a site for mode {self.mode}")
        return self


class ScenarioSet(_Model):
    scenarios: tuple[Scenario, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_and_complete(self) -> ScenarioSet:
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario ids must be unique")
        if missing := sorted(set(CATEGORIES) - {s.category for s in self.scenarios}):
            raise ValueError(f"no scenario covers categories {missing}")
        return self


def load_scenarios(path: Path) -> ScenarioSet:
    try:
        return ScenarioSet.model_validate(read_yaml(path))
    except ValidationError as error:
        raise EvaluationError(f"{path.name} is invalid: {error}") from None


def preflight(data: AnalystData, scenarios: ScenarioSet) -> list[str]:
    """Every stated site fact, checked against the deterministic tools. Empty when all hold."""
    problems = []
    for scenario in scenarios.scenarios:
        for candidate_id, fact in scenario.sites.items():
            found = find_sites(data, FindQuery(candidate_id=candidate_id)).sites
            if not found:
                problems.append(f"{scenario.id}: {candidate_id} is not a candidate")
                continue
            site = found[0]
            actual = {
                "selected_mclp": site.selected_mclp,
                "selected_top30": site.selected_top30,
                "grid_evidence_status": site.grid_evidence_status,
                "reason_status": network_contribution(data, candidate_id).reason_status,
            }
            for name, expected in fact.model_dump(exclude_none=True).items():
                if actual[name] != expected:
                    problems.append(
                        f"{scenario.id}: {candidate_id} {name} is {actual[name]}, not {expected}"
                    )
        if scenario.probes and not find_sites(data, FindQuery(candidate_id=scenario.probes)).sites:
            problems.append(f"{scenario.id}: probe site {scenario.probes} is not a candidate")
    return problems


# --- Running one scenario ---------------------------------------------------------------------


class FirstAnswerSabotage(Provider):
    """Wraps a provider and replaces its first final answer with a given invalid one, so the
    loop's single retry is exercised with the real model (scenario mode
    ``live_sabotaged_first_answer``). Every later step passes through unchanged."""

    def __init__(self, inner: Provider, answer_json: dict[str, Any]) -> None:
        self.inner = inner
        self.answer_json = answer_json
        self.used = False

    def next_step(self, context: ModelContext) -> ModelStep:
        step = self.inner.next_step(context)
        if step.kind == "answer" and not self.used:
            self.used = True
            return ModelStep(answer_json=self.answer_json)
        return step


def ungrounded_answer(candidate_id: str) -> dict[str, Any]:
    """An answer the validator must reject: a score no tool returned (never shown)."""
    statement = {
        "text": "The overall score is 999.9.",
        "kind": "CALCULATED",
        "evidence_ids": [f"{candidate_id}/score"],
    }
    return {"direct_answer": [statement]}


def run_scenario(scenario: Scenario, data: AnalystData, provider: Provider | None) -> RunResult:
    """One scenario through the phase 3a loop, in its mode."""
    site = next(iter(scenario.sites), None)
    if scenario.mode == "scripted_invalid_twice":
        assert site is not None
        model: Provider = FakeModel(
            [
                ModelStep(
                    tool_call=ToolCallRequest(tool="get_site", arguments={"candidate_id": site})
                ),
                ModelStep(answer_json=ungrounded_answer(site)),
                ModelStep(answer_json=ungrounded_answer(site)),
            ]
        )
        return run_analyst(data, scenario.question, model)
    if provider is None:
        raise EvaluationError(f"{scenario.id} needs the configured provider")
    if scenario.mode == "live_sabotaged_first_answer":
        assert site is not None
        provider = FirstAnswerSabotage(provider, ungrounded_answer(site))
    return run_analyst(data, scenario.question, provider)


# --- Scoring one scenario ---------------------------------------------------------------------


class Point(_Model):
    name: str
    passed: bool | None  # None: not applicable to this outcome
    detail: str = ""


class ProbeResult(_Model):
    label: str
    text: str
    evidence_ids: tuple[str, ...]
    expected_pass: bool
    actual_pass: bool
    rules: tuple[str, ...]


class ScenarioResult(_Model):
    scenario: Scenario
    run: RunResult | None  # None: not run (offline)
    tool_calls: tuple[ToolArguments, ...] = ()
    revalidation: ValidationResult | None = None
    numbers_checked: int = 0
    ungrounded_numbers: tuple[str, ...] = ()
    trap_hits: tuple[str, ...] = ()
    evaluative_hits: tuple[str, ...] = ()
    probes: tuple[ProbeResult, ...] = ()
    points: tuple[Point, ...] = ()


def session_from_transcript(run: RunResult) -> list[BaseModel]:
    """The evidence records and comparison fields the model was given, rebuilt from the
    logged tool outputs alone (so an answer can be re-validated from the log)."""
    records: dict[str, EvidenceRecord] = {}
    comparisons: dict[str, ComparisonField] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            keys = set(value)
            if keys == set(EvidenceRecord.model_fields):
                item = EvidenceRecord.model_validate(value)
                records[item.id] = item
                return
            if keys == set(ComparisonField.model_fields):
                field = ComparisonField.model_validate(value)
                comparisons[field.id] = field
                return
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    for call in run.transcript:
        walk(call.result)
    return [ToolRecords(records=tuple(records.values()), comparisons=tuple(comparisons.values()))]


def _answer_text(answer: Answer) -> str:
    return "\n".join(s.text for _, statements in answer.sections() for s in statements)


def _statements(answer: Answer) -> list[Statement]:
    return [s for _, statements in answer.sections() for s in statements]


def _contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _grounding(answer: Answer, records: dict[str, EvidenceRecord]) -> tuple[int, list[str]]:
    """How many number tokens the answer holds, and those not in a cited record's display."""
    checked, ungrounded = 0, []
    for statement in _statements(answer):
        allowed = {
            token
            for evidence_id in statement.evidence_ids
            if evidence_id in records
            for token in number_tokens(records[evidence_id].display)
        }
        for token in number_tokens(statement.text):
            checked += 1
            if token not in allowed:
                ungrounded.append(token)
    return checked, ungrounded


def _value(data: AnalystData, candidate_id: str, field: str) -> float:
    record = next(
        r for r in get_site(data, candidate_id).records if r.id == f"{candidate_id}/{field}"
    )
    return float(record.evidence.value)


def _trap_forms(value: float, percent: bool) -> set[str]:
    forms = set()
    for number in (value, abs(value)):
        for spec in (",.0f", ".0f", ",.1f", ".1f", ",.2f", ".2f"):
            text = format(number, spec)
            forms.add(text + "%" if percent else text)
    return forms


def _trap_values(trap: Trap, data: AnalystData) -> set[str]:
    if trap.kind == "network_mean":
        found = find_sites(data, FindQuery(selected_mclp=True))
        values = [float(r.evidence.value) for r in found.records if r.id.endswith(f"/{trap.field}")]
        return _trap_forms(sum(values) / len(values), percent=False)
    assert trap.a is not None and trap.b is not None
    a, b = _value(data, trap.a, trap.field), _value(data, trap.b, trap.field)
    if trap.kind == "difference":
        return _trap_forms(a - b, percent=False)
    return _trap_forms(100 * (a - b) / b, percent=True)


def _verdict(passed: bool, rules: Sequence[str] = ()) -> str:
    return "pass" if passed else "reject" + (f" ({', '.join(rules)})" if rules else "")


def evaluate(scenario: Scenario, run: RunResult | None, data: AnalystData) -> ScenarioResult:
    """The scenario's points, from the run's record and the deterministic tools only."""
    probes = tuple(format_probes(data, scenario.probes)) if scenario.probes else ()
    probe_points = [
        Point(
            name=f"probe: {p.label}",
            passed=p.actual_pass == p.expected_pass,
            detail=f"expected {_verdict(p.expected_pass)}, got {_verdict(p.actual_pass, p.rules)}",
        )
        for p in probes
    ]
    if run is None:
        return ScenarioResult(
            scenario=scenario, run=None, probes=probes, points=tuple(probe_points)
        )

    expect = scenario.expect
    calls = tuple(ToolArguments(tool=c.tool, arguments=c.arguments) for c in run.transcript)
    called = [c.tool for c in calls]
    points: list[Point] = []

    outcome_ok = expect.status in ("either", run.status)
    detail = run.status + (f": {run.fallback.reason}" if run.fallback else "")
    points.append(Point(name="outcome", passed=outcome_ok, detail=detail))

    answer = run.answer
    session = session_from_transcript(run)
    records = {r.id: r for r in session[0].records}  # type: ignore[attr-defined]
    revalidation = validate_answer(answer, session) if answer is not None else None
    checked, ungrounded, traps_hit, evaluative = 0, [], [], []
    if answer is not None:
        checked, ungrounded = _grounding(answer, records)
        text = _answer_text(answer)
        words = EVALUATIVE_EVERYWHERE + (EVALUATIVE_IN_COMPARISONS if expect.comparison else ())
        evaluative = [w for w in words if _has_word(text, w)]
        tokens = number_tokens(text)
        cited_displays = {t for r in records.values() for t in number_tokens(r.display)}
        for trap in expect.traps:
            forms = _trap_values(trap, data)
            traps_hit += [t for t in tokens if t in forms and t not in cited_displays]

    # Global points.
    if answer is None:
        points.append(Point(name="shown answer validated", passed=None, detail="no answer shown"))
    else:
        loop_ok = bool(run.attempts) and run.attempts[-1].passed
        points.append(
            Point(
                name="shown answer validated",
                passed=loop_ok and revalidation.passed,  # type: ignore[union-attr]
                detail="validated by the loop and re-validated from the log"
                if loop_ok and revalidation.passed  # type: ignore[union-attr]
                else f"re-validation errors: {[e.rule for e in revalidation.errors]}",  # type: ignore[union-attr]
            )
        )
        points.append(
            Point(
                name="numbers grounded",
                passed=not ungrounded,
                detail=f"{checked} number(s) checked; ungrounded: {ungrounded or 'none'}",
            )
        )
        points.append(
            Point(
                name="neutral language",
                passed=not evaluative,
                detail=f"evaluative words: {evaluative or 'none'}",
            )
        )
        if _has_word(text, "grid"):
            points.append(
                Point(
                    name="grid rule",
                    passed=GRID_DISCLAIMER in text,
                    detail="mentions the grid; exact disclaimer present"
                    if GRID_DISCLAIMER in text
                    else "mentions the grid without the exact disclaimer",
                )
            )
    if run.status == "fallback":
        labelled = run.fallback is not None and run.fallback.label == FALLBACK_LABEL
        points.append(
            Point(
                name="fallback labelled",
                passed=labelled,
                detail=f"{len(run.fallback.records) if run.fallback else 0} raw record(s)",
            )
        )

    # Scenario points that need no answer.
    for tool in expect.tools_include:
        points.append(
            Point(name=f"calls {tool}", passed=tool in called, detail=f"called: {called}")
        )
    if expect.tools_any:
        points.append(
            Point(
                name=f"calls one of {list(expect.tools_any)}",
                passed=any(t in called for t in expect.tools_any),
                detail=f"called: {called}",
            )
        )
    for wanted in expect.tool_arguments:
        ok = any(
            c.tool == wanted.tool and wanted.arguments.items() <= c.arguments.items() for c in calls
        )
        points.append(Point(name=f"{wanted.tool} with {wanted.arguments}", passed=ok))
    if expect.retried is not None:
        points.append(
            Point(
                name="retry", passed=run.retried == expect.retried, detail=f"retried: {run.retried}"
            )
        )
    for trap in expect.traps:
        name = f"no derived {trap.kind.replace('_', ' ')} of {trap.field}"
        points.append(
            Point(
                name=name,
                passed=None if answer is None else not traps_hit,
                detail="no answer shown"
                if answer is None
                else f"derived values found: {traps_hit or 'none'}",
            )
        )

    # Scenario points about the answer's content.
    def about_answer(name: str, check: Callable[[Answer, str], tuple[bool, str]]) -> None:
        if answer is None:
            applicable = expect.status == "answered"
            points.append(
                Point(name=name, passed=False if applicable else None, detail="no answer shown")
            )
            return
        ok, why = check(answer, _answer_text(answer))
        points.append(Point(name=name, passed=ok, detail=why))

    cited = [i for s in (_statements(answer) if answer else []) for i in s.evidence_ids]
    for prefix in expect.cites_prefix:
        about_answer(
            f"cites {prefix}…",
            lambda a, t, p=prefix: (
                any(i.startswith(p) for i in cited),
                f"{sum(i.startswith(p) for i in cited)} citation(s)",
            ),
        )
    for group in expect.contains_any:
        about_answer(
            f"says one of {list(group)}",
            lambda a, t, g=group: (
                any(_contains(t, p) for p in g),
                f"found: {[p for p in g if _contains(t, p)] or 'none'}",
            ),
        )
    if expect.not_contains:
        about_answer(
            "avoids unsupported claims",
            lambda a, t: (
                not [p for p in expect.not_contains if _contains(t, p)],
                f"found: {[p for p in expect.not_contains if _contains(t, p)] or 'none'}",
            ),
        )
    if expect.unknown_statement:
        about_answer(
            "keeps the unknown UNKNOWN",
            lambda a, t: (
                any(s.kind == "UNKNOWN" for s in _statements(a)),
                f"{sum(s.kind == 'UNKNOWN' for s in _statements(a))} UNKNOWN statement(s)",
            ),
        )
    if expect.grid_disclaimer:
        about_answer(
            "exact grid disclaimer",
            lambda a, t: (GRID_DISCLAIMER in t, "present" if GRID_DISCLAIMER in t else "absent"),
        )

    return ScenarioResult(
        scenario=scenario,
        run=run,
        tool_calls=calls,
        revalidation=revalidation,
        numbers_checked=checked,
        ungrounded_numbers=tuple(ungrounded),
        trap_hits=tuple(traps_hit),
        evaluative_hits=tuple(evaluative),
        probes=probes,
        points=tuple(points + probe_points),
    )


# --- J: deterministic number-format probes -------------------------------------------------


def format_probes(data: AnalystData, candidate_id: str) -> list[ProbeResult]:
    """Statements about one site's real records, each with the validator's expected verdict:
    exact copies (with commas, decimals, percentages, ranks, distances, ids and "Top-30")
    must pass; reformatted, rounded or rescaled numbers, or a number its citation does not
    hold, must be rejected."""
    site = get_site(data, candidate_id)
    network = network_contribution(data, candidate_id)
    session = [site, network]
    records = {r.id: r for r in [*site.records, *network.records]}

    def record(field: str) -> EvidenceRecord:
        return records[f"{candidate_id}/{field}"]

    rank, score, pop = record("rank"), record("score"), record("pop_5km")
    distance = record("nearest_network_site_distance")
    coverage = record("marginal_coverage")
    n_sites = records["context/n_sites"]
    host_osm = record("host_osm_id") if f"{candidate_id}/host_osm_id" in records else None
    score_value = float(score.evidence.value)
    distance_number, distance_unit = distance.display.split()
    top = f"Top-{n_sites.display}"  # the network size names the Top-N (30 on the real data)
    cases: list[tuple[str, str, EvidenceRecord, bool]] = [
        ("rank copied", f"Its rank is {rank.display}.", rank, True),
        ("score copied", f"Its overall score is {score.display}.", score, True),
        ("score rounded", f"Its overall score is {round(score_value)}.", score, False),
        (
            "distance copied",
            f"The nearest network site is {distance.display} away.",
            distance,
            True,
        ),
        (
            "distance reformatted",
            f"The nearest network site is {distance_number}0 {distance_unit} away.",
            distance,
            False,
        ),
        (
            "percentage copied",
            f"It would add {coverage.display} of weighted demand.",
            coverage,
            True,
        ),
        (
            "percentage reformatted",
            f"It would add {coverage.display.split('.')[0]}% of weighted demand.",
            coverage,
            False,
        ),
        (f"site id and {top}, cited", f"Site {candidate_id} is in the {top}.", n_sites, True),
    ]
    if "," in pop.display:  # a population of 1,000 or more
        pop_value = float(pop.evidence.value)
        cases += [
            (
                "population with thousands comma",
                f"Modelled population is {pop.display}.",
                pop,
                True,
            ),
            (
                "population without its comma",
                f"Modelled population is {pop.display.replace(',', '')}.",
                pop,
                False,
            ),
            (
                "population rescaled to thousands",
                f"Modelled population is {round(pop_value / 1000)}k.",
                pop,
                False,
            ),
        ]
    if rank.display != n_sites.display:
        cases.append(
            (
                f"{top} without a record holding it",
                f"Site {candidate_id} is in the {top}.",
                rank,
                False,
            )
        )
    if host_osm is not None:
        cases.append(
            ("OSM id copied", f"Its host is mapped as {host_osm.display}.", host_osm, True)
        )
    results = []
    for label, text, cited, expected in cases:
        answer = Answer(
            direct_answer=(Statement(text=text, kind=cited.type, evidence_ids=(cited.id,)),)
        )
        verdict = validate_answer(answer, session)
        results.append(
            ProbeResult(
                label=label,
                text=text,
                evidence_ids=(cited.id,),
                expected_pass=expected,
                actual_pass=verdict.passed,
                rules=tuple(sorted({e.rule for e in verdict.errors})),
            )
        )
    return results


# --- The whole evaluation ------------------------------------------------------------------


class Threshold(_Model):
    name: str
    target: str
    actual: str
    met: bool | None  # None: not evaluated (offline)


class Summary(_Model):
    scenarios: int
    run: int
    answered: int
    fallbacks: int
    retries: int
    shown_validated: int
    numbers_checked: int
    ungrounded_numbers: int
    trap_hits: int
    evaluative_hits: int
    points_passed: int
    points_total: int
    thresholds: tuple[Threshold, ...]
    passed: bool | None


def summarise(results: Sequence[ScenarioResult]) -> Summary:
    ran = [r for r in results if r.run is not None]
    answered = [r for r in ran if r.run.answer is not None]  # type: ignore[union-attr]
    validated = [r for r in answered if r.revalidation is not None and r.revalidation.passed]
    points = [p for r in results for p in r.points if p.passed is not None]
    passed_points = sum(p.passed for p in points)  # type: ignore[misc]
    live = any(r.scenario.mode != "scripted_invalid_twice" for r in ran)
    by_id = {r.scenario.id: r for r in results}

    def scenario_points_ok(ids: Sequence[str], names: Sequence[str] | None = None) -> bool | None:
        chosen = [by_id[i] for i in ids if i in by_id and by_id[i].run is not None]
        if not chosen:
            return None
        return all(
            p.passed is not False
            for r in chosen
            for p in r.points
            if names is None or any(p.name.startswith(n) for n in names)
        )

    comparison_ids = [r.scenario.id for r in results if r.scenario.expect.comparison]
    grid_ids = [r.scenario.id for r in results if r.scenario.expect.grid_disclaimer]
    grid_everywhere = all(p.passed for r in results for p in r.points if p.name == "grid rule")
    ungrounded = sum(len(r.ungrounded_numbers) for r in results)
    traps = sum(len(r.trap_hits) for r in results)
    evaluative = sum(len(r.evaluative_hits) for r in results)
    share = passed_points / len(points) if points else 0.0

    def met(value: bool | None) -> bool | None:
        return value if live else None

    def status(value: bool | None) -> str:
        return {True: "all correct", False: "see failures", None: "not run"}[value]

    comparisons_ok = scenario_points_ok(
        comparison_ids, ["neutral language", "says one of", "outcome"]
    )
    grid_ok = scenario_points_ok(grid_ids, ["exact grid disclaimer", "avoids"])
    thresholds = (
        Threshold(
            name="shown answers that pass validation",
            target="100%",
            actual=f"{len(validated)} of {len(answered)}",
            met=met(len(validated) == len(answered)),
        ),
        Threshold(
            name="derived numbers",
            target="0",
            actual=f"{ungrounded} ungrounded, {traps} trap value(s)",
            met=met(ungrounded + traps == 0),
        ),
        Threshold(
            name="winner or ranking claims",
            target="0",
            actual=str(evaluative),
            met=met(evaluative == 0),
        ),
        Threshold(
            name="comparison refusals and neutral comparisons",
            target="all correct",
            actual=status(comparisons_ok),
            met=met(comparisons_ok),
        ),
        Threshold(
            name="grid disclaimer behaviour",
            target="all correct",
            actual=status(None if grid_ok is None else grid_ok and grid_everywhere),
            met=met(None if grid_ok is None else grid_ok and grid_everywhere),
        ),
        Threshold(
            name="required evaluation points",
            target=">= 90%",
            actual=f"{passed_points} of {len(points)} ({share:.1%})",
            met=met(share >= 0.9),
        ),
    )
    # A threshold the run did not exercise (offline, or --only) leaves the verdict open.
    evaluated = [t.met for t in thresholds]
    overall = None if None in evaluated else all(evaluated)
    return Summary(
        scenarios=len(results),
        run=len(ran),
        answered=len(answered),
        fallbacks=sum(r.run.status == "fallback" for r in ran),  # type: ignore[union-attr]
        retries=sum(r.run.retried for r in ran),  # type: ignore[union-attr]
        shown_validated=len(validated),
        numbers_checked=sum(r.numbers_checked for r in results),
        ungrounded_numbers=ungrounded,
        trap_hits=traps,
        evaluative_hits=evaluative,
        points_passed=passed_points,
        points_total=len(points),
        thresholds=thresholds,
        passed=overall,
    )


class EvaluationRun(_Model):
    evaluated_at: str
    provider: str
    model: str
    sdk_version: str | None
    offline: bool
    scenario_file: str
    scenario_file_sha256: str
    data_fingerprint: str
    results: tuple[ScenarioResult, ...]
    summary: Summary


ProviderFactory = Callable[[AnalystSettings], Provider]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _sdk_version(provider: str) -> str | None:
    try:  # each provider's SDK package is named after it (anthropic, openai)
        return importlib.metadata.version(provider)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_evaluation(
    config: Config,
    factory: ProviderFactory | None,
    *,
    only: Sequence[str] = (),
    offline: bool = False,
    data: AnalystData | None = None,
    scenarios: ScenarioSet | None = None,
    write: bool = True,
) -> EvaluationRun:
    """Run the scenario set (see the module docstring). ``factory`` builds the configured
    provider once; it may raise ``CredentialError`` or ``ProviderError`` before any call."""
    paths = config.settings.paths
    scenario_file = config.resolve(paths.analyst_scenarios)
    scenarios = scenarios or load_scenarios(scenario_file)
    if unknown := sorted(set(only) - {s.id for s in scenarios.scenarios}):
        raise EvaluationError(f"unknown scenario ids {unknown}")
    processed = config.resolve(paths.processed_dir)
    data = data or AnalystData.load(config, processed)
    if problems := preflight(data, scenarios):
        raise EvaluationError("preflight failed:\n" + "\n".join(problems))
    provider = None if offline or factory is None else factory(config.settings.analyst)

    results = []
    for scenario in scenarios.scenarios:
        if only and scenario.id not in only:
            continue
        needs_model = scenario.mode != "scripted_invalid_twice"
        run = None if needs_model and provider is None else run_scenario(scenario, data, provider)
        result = evaluate(scenario, run, data)
        results.append(result)
        failed = [p.name for p in result.points if p.passed is False]
        log.info(
            "Scenario %s: %s; %d tool call(s)%s; %d of %d points%s",
            scenario.id,
            "not run" if run is None else run.status,
            0 if run is None else len(run.transcript),
            "" if run is None or not run.retried else ", retried",
            sum(p.passed is True for p in result.points),
            sum(p.passed is not None for p in result.points),
            f"; failed: {failed}" if failed else "",
        )

    evaluation = EvaluationRun(
        evaluated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider=config.settings.analyst.provider,
        model=config.settings.analyst.model,
        sdk_version=_sdk_version(config.settings.analyst.provider),
        offline=provider is None,
        scenario_file=paths.analyst_scenarios,
        scenario_file_sha256=_sha256(scenario_file),
        data_fingerprint=_sha256(processed / "evidence.json"),
        results=tuple(results),
        summary=summarise(results),
    )
    if write and not evaluation.offline:
        (processed / LOG_FILE).write_text(
            evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        report = config.resolve(paths.analyst_eval_report)
        report.write_text(render_report(evaluation), encoding="utf-8", newline="\n")
        log.info("Wrote %s and %s", report, processed / LOG_FILE)
    return evaluation


# --- The report -----------------------------------------------------------------------------


YES_NO = {True: "yes", False: "no"}
MET = {True: "yes", False: "**no**", None: "not evaluated"}
MARK = {True: "PASS", False: "**FAIL**", None: "n/a"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _render_answer(run: RunResult) -> list[str]:
    if run.answer is None:
        fallback = run.fallback
        lines = [f"**{FALLBACK_LABEL}.** Reason: {fallback.reason if fallback else 'none'}."]
        if fallback and fallback.records:
            count = len(fallback.records)
            lines.append(
                f"The fallback lists the {count} raw evidence record(s) the tools returned."
            )
        return lines
    lines = []
    for section, statements in run.answer.sections():
        for statement in statements:
            cited = ", ".join(f"`{i}`" for i in statement.evidence_ids) or "no citation"
            lines.append(
                f"- *{section.replace('_', ' ')}*, {statement.kind}: {statement.text} [{cited}]"
            )
    return lines


def render_report(evaluation: EvaluationRun) -> str:
    """reports/analyst_eval.md, from the run's record only."""
    s = evaluation.summary
    verdict = {True: "PASS", False: "FAIL", None: "NOT EVALUATED"}[s.passed]
    scenario_hash, data_hash = (
        evaluation.scenario_file_sha256[:16],
        evaluation.data_fingerprint[:16],
    )
    out = [
        "# SiteScout Analyst: scenario evaluation (Milestone 9, phase 3c)",
        "",
        "An independent portfolio project built on public data. This report records how the "
        "optional AI Site Analyst answered a fixed set of questions. Its answers are evaluation "
        "output only: they never enter the export, the evidence data, the Site Evidence Briefs "
        "or the demo page (D-053). Every point below is a deterministic check; none judges "
        "whether an answer merely looks reasonable.",
        "",
        f"**Result: {verdict}.**",
        "",
        "## 1. Run",
        "",
        f"- Evaluation date (UTC): {evaluation.evaluated_at}",
        f"- Provider and model: {evaluation.provider}, `{evaluation.model}`"
        + (
            f" ({evaluation.provider} SDK {evaluation.sdk_version})"
            if evaluation.sdk_version
            else ""
        ),
        f"- Scenario file: `{evaluation.scenario_file}` (SHA-256 `{scenario_hash}…`)",
        f"- Processed data fingerprint (`evidence.json` SHA-256): `{data_hash}…`",
        f"- Scenarios: {s.scenarios} ({s.run} run). Full log with every tool output: "
        "`data/processed/analyst_eval.json` (not committed).",
        "",
        "## 2. Thresholds",
        "",
        "| Measure | Target | Actual | Met |",
        "|---|---|---|---|",
        *[f"| {t.name} | {t.target} | {_cell(t.actual)} | {MET[t.met]} |" for t in s.thresholds],
        "",
        "## 3. Summary by scenario",
        "",
        "| Scenario | Category | Outcome | Tool calls | Retry | Points |",
        "|---|---|---|---|---|---|",
    ]
    for r in evaluation.results:
        applicable = [p for p in r.points if p.passed is not None]
        outcome = "not run" if r.run is None else r.run.status
        tools = ", ".join(c.tool for c in r.tool_calls) or "none"
        retry = "" if r.run is None else ("yes" if r.run.retried else "no")
        out.append(
            f"| {r.scenario.id}: {_cell(r.scenario.title)} | {r.scenario.category} | {outcome} | "
            f"{tools} | {retry} | {sum(bool(p.passed) for p in applicable)} of {len(applicable)} |"
        )
    out += [
        "",
        "## 4. Totals",
        "",
        f"- Answers shown: {s.answered}; validated by the loop and re-validated from the log: "
        f"{s.shown_validated}.",
        f"- Fallbacks: {s.fallbacks}. Retries: {s.retries}.",
        f"- Numerical grounding: {s.numbers_checked} number(s) in answers checked against the "
        f"display text of the records each statement cites; {s.ungrounded_numbers} not found "
        "there; "
        f"{s.trap_hits} derived trap value(s) found.",
        f"- Evaluative (winner or ranking) words in answers: {s.evaluative_hits}.",
        f"- Required evaluation points: {s.points_passed} of {s.points_total} passed.",
        "",
        "## 5. Failures",
        "",
    ]
    failures = [
        (r.scenario.id, p) for r in evaluation.results for p in r.points if p.passed is False
    ]
    out += [f"- {sid}, {p.name}: {_cell(p.detail)}" for sid, p in failures] or ["- None."]
    out += ["", "## 6. Scenarios", ""]
    for r in evaluation.results:
        sc = r.scenario
        out += [
            f"### {sc.id}: {sc.title}",
            "",
            f"- Category {sc.category}; mode `{sc.mode}`.",
            f"- Question: {sc.question}",
        ]
        if r.run is None:
            out += ["- Not run (offline).", ""]
        else:
            calls = "; ".join(
                f"`{c.tool}` {json.dumps(c.arguments, sort_keys=True)}" for c in r.tool_calls
            )
            out.append(f"- Tool calls: {calls or 'none'}")
            for index, attempt in enumerate(r.run.attempts, 1):
                errors = "; ".join(f"{e.rule} ({e.statement_id})" for e in attempt.errors)
                status = "passed" if attempt.passed else "rejected: " + _cell(errors)
                out.append(f"- Answer attempt {index}: {status}")
            out += [
                f"- Retry: {YES_NO[r.run.retried]}; "
                f"fallback: {YES_NO[r.run.status == 'fallback']}.",
                "",
                "Final answer:",
                "",
                *_render_answer(r.run),
                "",
            ]
        if r.probes:
            out += [
                "Number-format probes (the validator's verdicts on statements built from this "
                "site's records):",
                "",
                "| Probe | Expected | Actual |",
                "|---|---|---|",
            ]
            out += [
                f"| {p.label} | {_verdict(p.expected_pass)} | {_verdict(p.actual_pass, p.rules)} |"
                for p in r.probes
            ]
            out.append("")
        out += ["Points:", ""]
        out += [
            f"- {MARK[p.passed]} {p.name}" + (f": {_cell(p.detail)}" if p.detail else "")
            for p in r.points
        ]
        out.append("")
    out += [
        "## 7. Limitations",
        "",
        "- One run per scenario. Model output is not deterministic, so another run can differ; "
        "the deterministic tools, the validator and these checks do not.",
        "- Phrase checks are substring checks on fixed lists. They confirm required wording and "
        "catch listed unsupported claims; they cannot prove the absence of every possible "
        "unsupported paraphrase.",
        "- Scenario L1 replaces the model's first final answer with an ungrounded one to force the "
        "retry; the retry message tells the model its previous answer was rejected, although it "
        "did not write that answer. L2 uses a scripted model and makes no API call.",
        "- The validator's word rules are deliberately conservative (D-052): some harmless wording "
        "is rejected, which shows up as retries or fallbacks, never as an unvalidated answer.",
        "",
    ]
    return "\n".join(out)
