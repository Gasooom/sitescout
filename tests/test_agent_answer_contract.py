"""The agent prompt's writing guidance and the unchanged validator agree (M11 Phase 1).

The prompt (``sitescout.agent_provider.prompt``) tells the model how to write an answer the
validator (``sitescout.agent.validate``) accepts. Each guidance here is pinned from both sides:
the compliant phrasing passes and the natural violation fails with the exact rule. Nothing in
this file relaxes a rule; the records are built by hand in the shape ``network_summary`` and
``search_knowledge`` return, and no model or network is used.
"""

import pytest

from agent_support import call, final, limits, make_context, quote_from, reactive, seen, stmt
from sitescout.agent import run_agent
from sitescout.agent.validate import AgentAnswer, validate_agent_answer
from sitescout.agent_provider.prompt import build_system_prompt
from sitescout.analyst.validate import ToolRecords
from sitescout.briefs import GRID_DISCLAIMER
from sitescout.evidence import record

CHUNK_TEXT = (
    "## 6. Confidence\n\nA level: High, Medium or Low. Never a percentage. It is based only on "
    "factors that differ between sites:\n- grid evidence missing → Low;\n\n**Universal "
    "unknowns** appear as a fixed list in every output and are not part of the confidence level."
)
KB = record(
    "kb/spec/6-confidence", "SiteScout — Method Specification > 6. Confidence", "RETRIEVED_FACT",
    "SiteScout project knowledge", "docs/SPEC.md", "0" * 64, CHUNK_TEXT,
)  # fmt: skip


def net(key, field, claim, display, type_="CALCULATED"):
    return record(f"network/{key}/{field}", claim, type_, "SiteScout network (Milestone 6)",
                  field, display, display)  # fmt: skip


RECORDS = [
    net("mclp", "population_covered_share", "Modelled population (Optimized network)", "49.8%"),
    net("top30", "population_covered_share", "Modelled population (Top-30 by score)", "24.0%"),
    net("mclp", "districts", "Districts with a site (Optimized network)", "22"),
    net("top30", "districts", "Districts with a site (Top-30 by score)", "8"),
    net("mclp", "mean_score", "Mean site score (Optimized network)", "64.6"),
    net("top30", "mean_score", "Mean site score (Top-30 by score)", "73.8"),
    net("top30", "sites", "Sites (Top-30 by score)", "30"),
    net("top30", "sites_in/city_of_kigali", "Sites in City of Kigali (Top-30 by score)", "23"),
    net("mclp", "sites_in/city_of_kigali", "Sites in City of Kigali (Optimized network)", "3"),
    record("network/parameters/service_radius_m", "Service radius", "RETRIEVED_FACT",
           "config", "service_radius_m", 10000, "10 km"),
    record("unknown/grid_capacity", "Grid connection capacity", "UNKNOWN", "SiteScout",
           "grid_capacity", None, "not available"),
    KB,
]  # fmt: skip
SESSION = ToolRecords(records=tuple(RECORDS))
M, T = "network/mclp/", "network/top30/"
RADIUS = "network/parameters/service_radius_m"


def verdict(*statements, **sections):
    answer = AgentAnswer.model_validate({"direct_answer": list(statements), **sections})
    result = validate_agent_answer(answer, SESSION, min_quote_words=3)
    return result.passed, sorted({e.rule for e in result.errors})


# --- Structured number provenance -----------------------------------------------------------


def test_a_number_copied_from_a_cited_record_is_accepted():
    ok = stmt("The optimized network covers 49.8% of the modelled population.", "CALCULATED",
              (M + "population_covered_share",))  # fmt: skip
    assert verdict(ok) == (True, [])


def test_a_number_no_cited_record_shows_is_rejected():
    bad = stmt("The optimized network covers 51.0% of the modelled population.", "CALCULATED",
               (M + "population_covered_share",))  # fmt: skip
    assert verdict(bad) == (False, ["number_not_grounded"])


@pytest.mark.parametrize(
    ("text", "ids", "expected"),
    [
        # "within 10 km" holds the number 10: it needs the service-radius record.
        ("The optimized network covers 49.8% of the modelled population within 10 km.",
         [M + "population_covered_share"], (False, ["number_not_grounded"])),
        ("The optimized network covers 49.8% of the modelled population within 10 km.",
         [M + "population_covered_share", RADIUS], (True, [])),
        # "Top-30" holds the number 30: it needs a record showing 30.
        ("The Top-30 by score covers 24.0% of the modelled population.",
         [T + "population_covered_share"], (False, ["number_not_grounded"])),
        ("The Top-30 by score covers 24.0% of the modelled population.",
         [T + "population_covered_share", T + "sites"], (True, [])),
    ],
)  # fmt: skip
def test_digits_in_names_and_units_need_their_own_record(text, ids, expected):
    assert verdict(stmt(text, "CALCULATED", tuple(ids))) == expected


# --- Number and approximation words, even in idioms -----------------------------------------


@pytest.mark.parametrize(
    ("bad", "good", "rule"),
    [
        # The live network_comparison run that ended validation_failed (M11): "one another"
        # survived the retry, because nothing said the word itself is the problem.
        ("Selected sites may not sit closer than the spacing rule to one another.",
         "Selected sites may not sit closer than the spacing rule to each other.", "number_word"),
        ("The Top-30 by score is concentrated around Kigali.",
         "The Top-30 by score is concentrated in the City of Kigali.", "approximation_language"),
    ],
)  # fmt: skip
def test_rejected_words_are_rejected_even_when_they_are_not_numbers(bad, good, rule):
    ids = (T + "sites_in/city_of_kigali", T + "sites")
    assert verdict(stmt(bad, "INFERRED", ids)) == (False, [rule])
    assert verdict(stmt(good, "INFERRED", ids)) == (True, [])


def test_the_prompt_lists_exactly_the_words_the_validator_rejects():
    from sitescout.agent_provider import prompt
    from sitescout.analyst.validate import _APPROXIMATION_WORDS, _NUMBER_WORDS

    def words(listed):
        return tuple(w.strip().strip('"') for w in listed.split(","))

    assert words(prompt._NUMBER_WORDS) == _NUMBER_WORDS
    assert words(prompt._APPROXIMATION_WORDS) == _APPROXIMATION_WORDS
    text = build_system_prompt(3)
    assert prompt._NUMBER_WORDS in text and prompt._APPROXIMATION_WORDS in text


# --- Network comparison: values side by side, never a comparative word ----------------------


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("The Top-30 by score has a higher mean site score, 73.8, than the optimized network, "
         "64.6.", "comparison_relation_mismatch"),
        ("The optimized network reaches twice the modelled population of the Top-30 by score, "
         "49.8% versus 24.0%.", "approximation_language"),
        ("The optimized network is better: 49.8% versus 24.0% for the Top-30 by score.",
         "prohibited_comparison_language"),
    ],
)  # fmt: skip
def test_network_comparisons_with_comparative_or_evaluative_words_are_rejected(text, rule):
    ids = (M + "population_covered_share", T + "population_covered_share", M + "mean_score",
           T + "mean_score", T + "sites")  # fmt: skip
    passed, rules = verdict(stmt(text, "CALCULATED", ids))
    assert not passed and rule in rules


def test_a_side_by_side_network_comparison_answer_is_accepted():
    answer = [
        stmt("Modelled population within 10 km: 49.8% for the optimized network versus 24.0% "
             "for the Top-30 by score.", "CALCULATED",
             (M + "population_covered_share", T + "population_covered_share", T + "sites",
              RADIUS)),
        stmt("Districts with a site: 22 for the optimized network and 8 for the Top-30 by "
             "score, which places 23 of its 30 sites in the City of Kigali against 3 for the "
             "optimized network.", "CALCULATED",
             (M + "districts", T + "districts", T + "sites", T + "sites_in/city_of_kigali",
              M + "sites_in/city_of_kigali")),
        stmt("Mean site score: 64.6 for the optimized network and 73.8 for the Top-30 by "
             "score.", "CALCULATED", (M + "mean_score", T + "mean_score", T + "sites")),
    ]  # fmt: skip
    assert verdict(*answer) == (True, [])


# --- Knowledge citations and quotations -----------------------------------------------------


def test_an_exact_contiguous_quote_is_accepted():
    ok = stmt("SiteScout's confidence is a level, not a percentage.", "RETRIEVED_FACT",
              (KB.id,), ("A level: High, Medium or Low. Never a percentage.",))  # fmt: skip
    assert verdict(ok) == (True, [])


@pytest.mark.parametrize(
    "quote",
    [
        "Universal unknowns appear as a fixed list",  # Markdown ** dropped
        "grid evidence missing -> Low",  # arrow rewritten
        "a level: High, Medium or Low",  # capitalization changed
        "A level: High, Medium or Low ... not part of the confidence level",  # joined passages
        "Confidence is expressed as a level and never as a percentage",  # paraphrase
    ],
)
def test_an_inexact_or_invented_quote_is_rejected(quote):
    bad = stmt("SiteScout documents how confidence is expressed.", "RETRIEVED_FACT", (KB.id,),
               (quote,))  # fmt: skip
    assert verdict(bad) == (False, ["quote_not_in_cited_chunk"])


def test_the_markdown_as_shown_is_quotable():
    ok = stmt("Universal unknowns sit outside the confidence level.", "RETRIEVED_FACT",
              (KB.id,), ("**Universal unknowns** appear as a fixed list",))  # fmt: skip
    assert verdict(ok) == (True, [])


def test_a_mixed_statement_takes_its_numbers_from_the_structured_record():
    ok = stmt("The optimized network covers 49.8% of the modelled population, and confidence "
              "is a separate level.", "CALCULATED", (M + "population_covered_share", KB.id),
              ("A level: High, Medium or Low.",))  # fmt: skip
    assert verdict(ok) == (True, [])


# --- Grid disclaimer and UNKNOWN ------------------------------------------------------------


def test_a_statement_mentioning_the_grid_needs_the_exact_disclaimer():
    bare = stmt("Grid connection capacity is unknown.", "UNKNOWN")
    with_it = stmt(f"Grid connection capacity is unknown. {GRID_DISCLAIMER}", "UNKNOWN")
    assert verdict(bare) == (False, ["grid_disclaimer_missing"])
    assert verdict(with_it) == (True, [])


def test_unknown_stays_unknown():
    as_fact = stmt("Grid connection capacity is sufficient here. " + GRID_DISCLAIMER,
                   "RETRIEVED_FACT", ("unknown/grid_capacity",))  # fmt: skip
    as_unknown = stmt("Land availability is not known.", "UNKNOWN")
    passed, rules = verdict(as_fact)
    assert not passed and "unknown_stated_as_fact" in rules
    assert verdict(as_unknown) == (True, [])


# --- The prompt states each of these rules --------------------------------------------------


def test_the_prompt_teaches_each_rule_pinned_here():
    prompt = build_system_prompt(3)
    for needle in (
        '"Top-30" contains 30',
        '"10 km" contains 10',
        "put the values side by side",
        'never "twice"',
        "one contiguous span copied exactly",
        "Markdown characters",
        "checked per statement",
        "3 to 6 statements",
        "check every statement silently",
        '"each other", not "one another"',
        "do not repeat a quotation",
    ):
        assert needle in prompt, needle


# --- A retry after an inexact quotation -----------------------------------------------------


@pytest.fixture(scope="module")
def context(analyst_world):
    return make_context(analyst_world)


def test_a_retry_after_an_inexact_quote_can_validate(context):
    def model(ctx):
        if not ctx.transcript:
            return call("search_knowledge", query="how confidence is determined")
        chunk = next(r for r in seen(ctx).values() if r.id.startswith("kb/"))
        exact = quote_from(chunk)
        quote = exact if ctx.validation_errors else exact.upper() + " (paraphrased)"
        return final(stmt("The documentation describes this.", "RETRIEVED_FACT", (chunk.id,),
                          (quote,)))  # fmt: skip

    result = run_agent(context, "How is confidence determined?", reactive(model), limits())
    assert result.status == "answered" and result.state.retried
    assert [e.rule for e in result.state.validation_attempts[0].errors] == [
        "quote_not_in_cited_chunk"
    ]
    assert result.state.validation_attempts[1].passed
