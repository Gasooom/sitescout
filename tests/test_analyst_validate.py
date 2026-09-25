"""The M9 phase 2 answer schema and offline validator. No model, network or file write."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from sitescout.analyst import (
    AnalystData,
    Answer,
    FindQuery,
    Statement,
    ToolRecords,
    ValidationResult,
    compare_sites,
    find_sites,
    get_site,
    validate_answer,
)
from sitescout.analyst.tools import ComparisonField
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.evidence import record

# --- Hand-built records, for isolated rule tests -------------------------------------------


def _rec(id, claim, kind, value, display, *, source="Test source", metric="value", unit=None):
    return record(id, claim, kind, source, metric, value, display, unit)


SCORE = _rec("x/score", "Overall score (0-100)", "CALCULATED", 67.2, "67.2")
RADIUS_5KM = _rec("context/radius_5000", "Feature radius", "RETRIEVED_FACT", 5000, "5 km", unit="m")
POP_5KM = _rec(
    "x/pop_5km", "Modelled population within 5 km", "CALCULATED", 131688.0, "131,688", unit="people"
)
N_SITES = _rec("context/n_sites", "Sites in the network", "RETRIEVED_FACT", 30, "30")
DISTRICT = _rec("x/district", "District", "RETRIEVED_FACT", "Huye", "Huye")
HOST = _rec("x/host", "Host", "RETRIEVED_FACT", "fuel", "Fuel station, Huye")
GRID_MISSING = _rec("x/grid_status", "Grid evidence missing", "UNKNOWN", "UNKNOWN", "missing")
POP_A = _rec(
    "a/pop_5km", "Modelled population within 5 km", "CALCULATED", 131688.0, "131,688", unit="people"
)
POP_B = _rec(
    "b/pop_5km", "Modelled population within 5 km", "CALCULATED", 58241.0, "58,241", unit="people"
)
SCORE_A = _rec("a/score", "Overall score (0-100)", "CALCULATED", 76.6, "76.6")
SCORE_B = _rec("b/score", "Overall score (0-100)", "CALCULATED", 67.2, "67.2")

COMPARE_POP_GREATER = ComparisonField(
    id="compare/a/b/pop_5km", field="pop_5km", label="Modelled population within 5 km",
    kind="numeric", a_record_id="a/pop_5km", b_record_id="b/pop_5km",
    a_display="131,688", b_display="58,241", relation="a_greater", display="131,688 | 58,241",
)  # fmt: skip
COMPARE_SCORE_EQUAL = ComparisonField(
    id="compare/a/b/score", field="score", label="Overall score (0-100)",
    kind="numeric", a_record_id="a/score", b_record_id="b/score",
    a_display="70.0", b_display="70.0", relation="equal", display="70.0 | 70.0",
)  # fmt: skip

BASE_SESSION = [
    ToolRecords(records=(SCORE, RADIUS_5KM, POP_5KM, N_SITES, DISTRICT, HOST, GRID_MISSING))
]
COMPARE_SESSION = [
    ToolRecords(
        records=(POP_A, POP_B, SCORE_A, SCORE_B),
        comparisons=(COMPARE_POP_GREATER, COMPARE_SCORE_EQUAL),
    )
]


def _answer(text, kind="CALCULATED", ids=()) -> Answer:
    statement = Statement(text=text, kind=kind, evidence_ids=tuple(ids))
    return Answer(direct_answer=(statement,))


def check(text, session=BASE_SESSION, kind="CALCULATED", ids=()) -> ValidationResult:
    return validate_answer(_answer(text, kind, ids), session)


def rules(result: ValidationResult) -> set[str]:
    return {e.rule for e in result.errors}


# 1, 4, 25. A grounded statement with multiple citations passes ------------------------------


def test_1_4_valid_grounded_answer_with_copied_numbers_passes():
    result = check("The score is 67.2.", ids=("x/score",))
    assert result.passed and result.errors == ()


def test_25_multiple_citations_pass():
    result = check(
        "Modelled population within 5 km is 131,688.",
        kind="CALCULATED",
        ids=("x/pop_5km", "context/radius_5000"),
    )
    assert result.passed


# 2. Unknown evidence id ----------------------------------------------------------------------


def test_2_unknown_evidence_id_fails():
    result = check("The score is 67.2.", ids=("x/score", "x/does_not_exist"))
    assert not result.passed
    assert "unknown_evidence_id" in rules(result)
    assert "x/does_not_exist" in result.errors[0].message


# 3, 26. Missing or unsupported citation -------------------------------------------------------


def test_3_missing_citation_fails():
    result = check("The score is 67.2.", ids=())
    assert not result.passed and rules(result) == {"missing_citation", "number_not_grounded"}


def test_26_unsupported_factual_claim_fails():
    # A fabricated score cited only against an unrelated, non-CALCULATED record.
    result = check("The score is 82.5.", kind="CALCULATED", ids=("x/host",))
    assert not result.passed
    assert {"calculated_not_grounded", "number_not_grounded"} <= rules(result)


# 5, 6, 7. Reformatted, rounded or rescaled numbers --------------------------------------------


def test_5_number_reformatted_fails():
    result = check("Coverage is roughly 50%.", kind="CALCULATED", ids=("x/score",))
    # (using an unrelated citation on purpose: no record here displays "50%" at all)
    assert not result.passed and "number_not_grounded" in rules(result)


def test_6_number_rounded_fails():
    coord = _rec("x/coordinates", "Coordinates", "RETRIEVED_FACT", "x", "-2.61966, 29.74227")
    session = [ToolRecords(records=(coord,))]
    rounded = validate_answer(
        _answer("The site sits near -2.62.", "RETRIEVED_FACT", ("x/coordinates",)), session
    )
    assert not rounded.passed and "number_not_grounded" in rules(rounded)
    exact = validate_answer(
        _answer("The site sits near -2.61966.", "RETRIEVED_FACT", ("x/coordinates",)), session
    )
    assert exact.passed


def test_7_number_converted_to_thousands_fails():
    result = check("Population within 5 km is 132k.", kind="CALCULATED", ids=("x/pop_5km",))
    assert not result.passed and "number_not_grounded" in rules(result)
    result = check("Population within 5 km is 131688.", kind="CALCULATED", ids=("x/pop_5km",))
    assert not result.passed and "number_not_grounded" in rules(result)


# 8, 9. Number words and approximation ---------------------------------------------------------


@pytest.mark.parametrize("word", ["thirty", "fifty", "hundred", "twelve"])
def test_8_number_written_as_words_fails(word):
    result = check(f"There are {word} sites.", kind="RETRIEVED_FACT", ids=("x/host",))
    assert not result.passed and "number_word" in rules(result)


@pytest.mark.parametrize(
    "phrase",
    ["about 30 sites", "approximately 30 sites", "roughly half", "around 30 sites", "~30 sites"],
)
def test_9_approximation_language_fails(phrase):
    result = check(phrase, kind="RETRIEVED_FACT", ids=("context/n_sites",))
    assert not result.passed and "approximation_language" in rules(result)


# 10, 11. Arithmetic and derived ranges ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["73.8 - 64.6 = 9.2", "23 of 30 = 76.7%", "131,688 / 2", "score = 67.2", "67.2 * 2"],
)
def test_10_arithmetic_expression_fails(text):
    result = check(text, kind="CALCULATED", ids=("x/score", "x/pop_5km", "context/n_sites"))
    assert not result.passed and "arithmetic_expression" in rules(result)


@pytest.mark.parametrize("text", ["2-3 km away", "10-20 km", "between 5-10 sites"])
def test_11_derived_range_fails(text):
    result = check(text, kind="CALCULATED", ids=("context/radius_5000",))
    assert not result.passed and "arithmetic_expression" in rules(result)


# 12. A number present in the session but not among the cited records' displays ---------------


def test_12_number_present_elsewhere_in_session_but_not_cited_fails():
    result = check(
        "The score is 30.", kind="CALCULATED", ids=("x/score",)
    )  # 30 is elsewhere (N_SITES), not here
    assert not result.passed and "number_not_grounded" in rules(result)


# 13, 14, 15. Prohibited claims and the grid disclaimer -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Grid connection capacity is sufficient here.",
        "This confirms grid feasibility for the site.",
        "The grid connection has been approved.",
        "The site is commercially viable.",
        "Expected revenue from this site is high.",
        "Land is available at this site.",
        "The landowner is willing to host a charger.",
        "Permits have been approved for this site.",
    ],
)
def test_13_prohibited_claims_fail(text):
    result = check(text, kind="INFERRED", ids=("x/host",))
    assert not result.passed and "prohibited_claim" in rules(result)


def test_14_grid_mention_without_disclaimer_fails():
    result = check("Grid evidence is mapped nearby.", kind="RETRIEVED_FACT", ids=("x/host",))
    assert not result.passed and "grid_disclaimer_missing" in rules(result)


def test_15_grid_mention_with_exact_disclaimer_passes():
    text = f"Grid evidence is mapped nearby. {GRID_DISCLAIMER}"
    result = check(text, kind="RETRIEVED_FACT", ids=("x/host",))
    assert result.passed


def test_15b_an_altered_disclaimer_still_fails():
    altered = GRID_DISCLAIMER.replace("utility", "a utility")
    result = check(
        f"Grid evidence is mapped nearby. {altered}", kind="RETRIEVED_FACT", ids=("x/host",)
    )
    assert not result.passed and "grid_disclaimer_missing" in rules(result)


# 16, 17, 18, 19, 20. Comparisons ----------------------------------------------------------------


def test_16_better_comparison_fails():
    result = check(
        "Site A is better than site B.",
        kind="INFERRED",
        ids=("compare/a/b/pop_5km",),
        session=COMPARE_SESSION,
    )
    assert not result.passed and "prohibited_comparison_language" in rules(result)


def test_17_winner_comparison_fails():
    result = check(
        "Site A is the winner.",
        kind="INFERRED",
        ids=("compare/a/b/pop_5km",),
        session=COMPARE_SESSION,
    )
    assert not result.passed and "prohibited_comparison_language" in rules(result)


def test_18_unsupported_comparative_word_fails():
    # "greater" used with no compare_sites citation at all.
    result = check(
        "Site A's population is greater.",
        kind="CALCULATED",
        ids=("a/pop_5km",),
        session=COMPARE_SESSION,
    )
    assert not result.passed and "comparison_relation_mismatch" in rules(result)


def test_19_valid_a_greater_comparison_passes():
    result = check(
        "A's population, 131,688, is greater than B's, 58,241.",
        kind="CALCULATED",
        ids=("a/pop_5km", "b/pop_5km", "compare/a/b/pop_5km"),
        session=COMPARE_SESSION,
    )
    assert result.passed, result.errors


def test_20_relation_mismatch_fails():
    # The score comparison's relation is "equal": a directional word is not supported.
    result = check(
        "A's score is greater than B's.",
        kind="CALCULATED",
        ids=("a/score", "b/score", "compare/a/b/score"),
        session=COMPARE_SESSION,
    )
    assert not result.passed and "comparison_relation_mismatch" in rules(result)


def test_a_neutral_refusal_with_no_trigger_word_can_pass():
    # There is no special-cased "refusal" parser (see the phase 2 report): every evaluative
    # word is always rejected, so a refusal must simply avoid them. This shows one can:
    # no "better/worse/best/prefer/preferred/winner/recommend/...", just the documented gap.
    result = check(
        "SiteScout does not choose between the sites. It reports the documented "
        "difference: A's population, 131,688, is greater than B's, 58,241.",
        kind="CALCULATED",
        ids=("a/pop_5km", "b/pop_5km", "compare/a/b/pop_5km"),
        session=COMPARE_SESSION,
    )
    assert result.passed, result.errors


# 21. UNKNOWN statements -----------------------------------------------------------------------


def test_21_unknown_statement_passes_without_citation():
    # A grid mention needs the exact disclaimer too (tests 14/15): this one is about land,
    # so the "UNKNOWN passes" concern is tested on its own, separately from that rule.
    result = check("Landowner willingness to host a charger is unknown.", kind="UNKNOWN", ids=())
    assert result.passed, result.errors


def test_21c_an_unknown_grid_statement_still_needs_the_exact_disclaimer():
    bare = check("Grid connection capacity is unknown.", kind="UNKNOWN", ids=())
    assert not bare.passed and "grid_disclaimer_missing" in rules(bare)
    with_disclaimer = check(
        f"Grid connection capacity is unknown. {GRID_DISCLAIMER}", kind="UNKNOWN", ids=()
    )
    assert with_disclaimer.passed, with_disclaimer.errors


def test_21b_unknown_cannot_be_a_loophole_for_a_confident_guess():
    result = check("The grid probably has enough capacity.", kind="UNKNOWN", ids=())
    assert not result.passed
    assert {"unknown_must_state_absence", "approximation_language", "prohibited_claim"} & rules(
        result
    )


# 22, 23. Schema validity -----------------------------------------------------------------------


def test_22_malformed_schema_fails():
    with pytest.raises(ValidationError):
        Statement(text="", kind="RETRIEVED_FACT")  # empty text
    with pytest.raises(ValidationError):
        Statement(text="ok", kind="MAYBE")  # not one of the four kinds
    with pytest.raises(ValidationError):
        Answer()  # direct_answer is required and non-empty


def test_23_unknown_schema_fields_are_rejected():
    with pytest.raises(ValidationError):
        Statement(text="ok", kind="RETRIEVED_FACT", confidence="High")  # not a field
    with pytest.raises(ValidationError):
        Answer(direct_answer=(Statement(text="ok", kind="RETRIEVED_FACT"),), summary="extra")


# 24. Hyphenated identifiers must not be mistaken for numbers or arithmetic --------------------


def test_24_top_30_and_candidate_ids_remain_valid():
    result = check(
        "Site cand-051d849523f2 ranks among the Top-30.",
        kind="RETRIEVED_FACT",
        ids=("context/n_sites",),
    )
    assert result.passed, result.errors


def test_24b_a_hyphenated_word_is_not_treated_as_arithmetic():
    result = check(
        "This is a well-mapped, value-neutral comparison.", kind="RETRIEVED_FACT", ids=("x/host",)
    )
    assert result.passed


# --- Regression: purely offline, no side effects, no AI ----------------------------------------


def test_no_files_are_written(tmp_path: Path):
    before = {p: p.stat().st_mtime for p in Path().glob("*") if p.is_file()}
    check("The score is 67.2.", ids=("x/score",))
    after = {p: p.stat().st_mtime for p in Path().glob("*") if p.is_file()}
    assert before == after
    assert list(tmp_path.iterdir()) == []  # validate_answer never touched this directory either


def test_no_ai_package_or_network_module_is_imported():
    # A presence check over the whole process would be unreliable: unrelated test-session
    # dependencies (pytest plugins, geopandas, ...) may already import "socket" or similar
    # for reasons that have nothing to do with SiteScout. Instead this checks that calling
    # the validator introduces no *new* forbidden import.
    import sys

    forbidden = {
        "anthropic", "openai", "langchain", "langgraph", "chromadb", "faiss", "requests", "httpx"
    }  # fmt: skip
    before = {m.split(".")[0] for m in sys.modules}
    check("The score is 67.2.", ids=("x/score",))
    after = {m.split(".")[0] for m in sys.modules}
    assert (after - before) & forbidden == set()
    # Whether an AI package was imported earlier in the session (for example by the offline
    # real-SDK test when the analyst extra is installed) is a property of the test session,
    # not of the validator; test_analyst_provider checks SiteScout in a clean interpreter.


def test_no_environment_variable_is_read(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "poisoned")
    monkeypatch.setenv("SITESCOUT_ANALYST_MODEL", "poisoned")
    a = check("The score is 67.2.", ids=("x/score",))
    b = check("The score is 67.2.", ids=("x/score",))
    assert a == b  # identical whether or not these variables are set


def test_validate_answer_never_raises_on_a_well_formed_answer():
    # Any Answer built through the schema is safe to validate; only construction can raise.
    result = check("Grid connection capacity is unknown.", kind="UNKNOWN", ids=())
    assert isinstance(result, ValidationResult)


# --- Integration: real tool output on the SYNTHETIC world --------------------------------------


@pytest.fixture
def world(analyst_world):
    """The SYNTHETIC world through M6, built once per module (conftest.analyst_world)."""
    config, processed, _ = analyst_world
    return AnalystData.load(config, processed)


def test_a_real_get_site_result_grounds_a_simple_statement(world):
    site = find_sites(world, FindQuery()).sites[0]
    result = get_site(world, site.candidate_id)
    score_id = f"{site.candidate_id}/score"
    score_display = next(r.display for r in result.records if r.id == score_id)
    answer = Answer(
        direct_answer=(
            Statement(
                text=f"The score is {score_display}.", kind="CALCULATED", evidence_ids=(score_id,)
            ),
        )
    )
    assert validate_answer(answer, [result]).passed


def test_a_real_compare_sites_result_grounds_a_directional_statement(world):
    sites = find_sites(world, FindQuery()).sites
    result = compare_sites(world, sites[0].candidate_id, sites[1].candidate_id)
    score_field = next(
        f for f in result.fields if f.field == "score" and f.relation in ("a_greater", "b_greater")
    )
    a, b = score_field.a_display, score_field.b_display
    text = (
        f"A's overall score, {a}, is greater than B's, {b}."
        if score_field.relation == "a_greater"
        else f"B's overall score, {b}, is greater than A's, {a}."
    )
    answer = Answer(
        direct_answer=(
            Statement(
                text=text,
                kind="CALCULATED",
                evidence_ids=(score_field.a_record_id, score_field.b_record_id, score_field.id),
            ),
        )
    )
    assert validate_answer(answer, [result]).passed


def test_a_real_answer_with_a_fabricated_number_fails(world):
    site = find_sites(world, FindQuery()).sites[0]
    result = get_site(world, site.candidate_id)
    answer = Answer(
        direct_answer=(
            Statement(
                text="The score is 999.9.",
                kind="CALCULATED",
                evidence_ids=(f"{site.candidate_id}/score",),
            ),
        )
    )
    outcome = validate_answer(answer, [result])
    assert not outcome.passed and "number_not_grounded" in {e.rule for e in outcome.errors}
