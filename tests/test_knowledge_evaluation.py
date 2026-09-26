"""Milestone 10, Phase 1: the gold set, the retrieval measurements and the report."""

import copy

import pytest
from pydantic import ValidationError

from knowledge_support import settings, synthetic_root, write_docs
from sitescout.config import load_config
from sitescout.knowledge import KnowledgeIndex, build_corpus
from sitescout.knowledge.evaluation import (
    CATEGORIES,
    KS,
    EvaluationError,
    GoldSet,
    evaluate,
    evaluate_query,
    load_gold,
    run_evaluation,
    validate_gold,
)

TOY = """# Notes

## Alpha

alpha alpha gamma

## Beta

alpha gamma delta

## Gamma

gamma gamma gamma epsilon
"""
TOY_SOURCE = {"path": "docs/notes.md", "doc_type": "method", "topic": "scoring", "preamble": False}
ALPHA, BETA, GAMMA = "kb/notes/alpha", "kb/notes/beta", "kb/notes/gamma"
PHRASE = {ALPHA: "alpha alpha gamma", BETA: "alpha gamma delta", GAMMA: "gamma gamma gamma epsilon"}


def _relevant(*chunks):
    return [{"chunk": chunk, "phrase": PHRASE[chunk]} for chunk in chunks]


def _query(id, category, query, chunks=(), author="assistant", **extra):
    return {
        "id": id,
        "category": category,
        "author": author,
        "query": query,
        "relevant": _relevant(*chunks),
        "note": "a note",
        **extra,
    }


def _gold(queries, status="draft", reviewer=None, on=None):
    return {
        "version": 1,
        "review": {"status": status, "reviewed_by": reviewer, "reviewed_on": on},
        "queries": queries,
    }


TOY_QUERIES = [
    _query("P01", "paraphrase", "alpha", [BETA]),
    _query("T01", "terminology", "alpha alpha", [ALPHA, GAMMA]),
    _query("D01", "definition", "gamma", [GAMMA]),
    _query("M01", "decision", "delta", [ALPHA]),
    _query("O01", "out_of_scope", "quokka"),
    _query("O02", "out_of_scope", "alpha alpha alpha"),
]


@pytest.fixture
def toy_index(temp_dir):
    write_docs(temp_dir, {"docs/notes.md": TOY})
    config = settings([TOY_SOURCE])
    return KnowledgeIndex(build_corpus(config, temp_dir), config.bm25, config.retrieval)


@pytest.fixture
def toy_run(toy_index):
    return evaluate(toy_index, GoldSet.model_validate(_gold(TOY_QUERIES)), "f" * 64)


# --- Measuring: hand-computed on a toy corpus ---------------------------------------------------


def test_the_toy_ranking_is_what_the_arithmetic_below_assumes(toy_index):
    ids = lambda q: [toy_index.corpus.chunks[m.index].chunk_id for m in toy_index.rank(q)]  # noqa: E731
    assert ids("alpha") == [ALPHA, BETA]
    assert ids("gamma")[0] == GAMMA
    assert ids("delta") == [BETA] and ids("quokka") == []


def test_one_query_measured_by_hand(toy_run):
    p01 = next(r for r in toy_run.results if r.query.id == "P01")  # relevant: Beta, ranked 2nd
    assert (p01.relevant_ranks, p01.first_rank, p01.matches) == ((2,), 2, 2)
    assert dict(zip(KS, p01.hit, strict=True)) == {1: False, 3: True, 5: True, 10: True}
    assert dict(zip(KS, p01.recall, strict=True)) == {1: 0.0, 3: 1.0, 5: 1.0, 10: 1.0}
    assert p01.reciprocal_rank == 0.5
    assert (p01.top1_chunk, p01.vocabulary_overlap) == (ALPHA, 1.0)


def test_recall_counts_the_share_of_relevant_chunks_found(toy_run):
    t01 = next(r for r in toy_run.results if r.query.id == "T01")  # Alpha ranked 1st; Gamma never
    assert t01.relevant_ranks == (1, None)
    assert dict(zip(KS, t01.hit, strict=True)) == {1: True, 3: True, 5: True, 10: True}
    assert dict(zip(KS, t01.recall, strict=True)) == {1: 0.5, 3: 0.5, 5: 0.5, 10: 0.5}
    assert t01.reciprocal_rank == 1.0


def test_a_query_that_finds_nothing_relevant_scores_zero_everywhere(toy_run):
    m01 = next(r for r in toy_run.results if r.query.id == "M01")  # 'delta' finds only Beta
    assert m01.relevant_ranks == (None,) and m01.first_rank is None
    assert not any(m01.hit) and m01.recall == (0.0,) * 4 and m01.reciprocal_rank == 0.0
    assert m01.vocabulary_overlap == 0.0


def test_pooled_metrics_are_means_over_the_in_scope_queries(toy_run):
    pooled = toy_run.pooled
    assert pooled.n == 4
    assert pooled.hits == (2, 3, 3, 3)  # P01 misses @1; M01 never hits
    assert pooled.recall == pytest.approx((0.375, 0.625, 0.625, 0.625))
    assert pooled.mrr == pytest.approx(0.625)


def test_metrics_are_kept_per_category_and_never_combined(toy_run):
    assert set(toy_run.by_category) == set(CATEGORIES[:-1])
    assert [toy_run.by_category[c].n for c in CATEGORIES[:-1]] == [1, 1, 1, 1]
    assert toy_run.by_category["decision"].hits == (0, 0, 0, 0)
    assert toy_run.by_category["definition"].mrr == 1.0
    assert not hasattr(toy_run.pooled, "score") and not hasattr(toy_run, "overall")


def test_out_of_scope_queries_report_matches_not_relevance(toy_run):
    quokka = next(r for r in toy_run.results if r.query.id == "O01")
    assert (quokka.matches, quokka.top1_chunk, quokka.top1_score) == (0, None, None)
    assert quokka.vocabulary_overlap is None and quokka.first_rank is None
    alpha = next(r for r in toy_run.results if r.query.id == "O02")
    assert alpha.matches == 2 and alpha.top1_chunk == ALPHA and alpha.top1_score > 0


def test_a_category_with_no_query_is_reported_as_empty(toy_index):
    gold = GoldSet.model_validate(_gold(TOY_QUERIES[:1]))
    run = evaluate(toy_index, gold, "0" * 64)
    assert run.by_category["terminology"].n == 0 and run.by_category["paraphrase"].n == 1
    assert "0 of 0" in run.report


def test_evaluating_twice_gives_the_same_measurements_and_report(toy_index):
    gold = GoldSet.model_validate(_gold(TOY_QUERIES))
    first, second = evaluate(toy_index, gold, "a" * 64), evaluate(toy_index, gold, "a" * 64)
    assert first == second and first.report == second.report


def test_filters_shape_the_ranking_a_query_is_measured_on(temp_dir):
    index = KnowledgeIndex(
        build_corpus(settings(), synthetic_root(temp_dir)), settings().bm25, settings().retrieval
    )
    query = {
        "id": "T01",
        "category": "terminology",
        "author": "assistant",
        "query": "thing",
        "filters": {"decision_id": "D-003"},
        "relevant": [{"chunk": "kb/decisions/d-003", "phrase": "the third thing"}],
        "note": "n",
    }
    result = evaluate_query(index, GoldSet.model_validate(_gold([query])).queries[0])
    assert result.matches == 1 and result.first_rank == 1


# --- The gold set is validated before anything is measured --------------------------------------


def _invalid(queries, **kwargs):
    return GoldSet.model_validate(_gold(queries, **kwargs))


@pytest.mark.parametrize(
    ("queries", "message"),
    [
        (
            [_query("P01", "paraphrase", "a", [BETA]), _query("P01", "paraphrase", "b", [BETA])],
            "duplicate query ids",
        ),
        (
            [_query("P01", "paraphrase", "a", [BETA]), _query("P02", "paraphrase", "a", [BETA])],
            "same text",
        ),
        ([_query("T01", "paraphrase", "a", [BETA])], "id letter must be 'P'"),
        ([_query("O01", "out_of_scope", "a", [BETA])], "no relevant chunk"),
        ([_query("P01", "paraphrase", "a")], "at least one relevant chunk"),
        ([_query("P01", "paraphrase", "a", [BETA, BETA])], "listed twice"),
        ([_query("P1", "paraphrase", "a", [BETA])], "String should match"),
        ([_query("P01", "paraphrase", "", [BETA])], "at least 1 character"),
        ([_query("P01", "paraphrase", "a", [BETA], author="robot")], "author"),
        ([_query("P01", "paraphrase", "a", [BETA], extra_key=1)], "Extra inputs"),
        ([], "at least 1 item"),
    ],
)
def test_a_malformed_gold_set_is_refused(queries, message):
    with pytest.raises(ValidationError, match=message):
        _invalid(queries)


def test_a_reviewed_gold_set_names_its_reviewer():
    with pytest.raises(ValidationError, match="who reviewed it and when"):
        _invalid(TOY_QUERIES, status="reviewed")
    assert _invalid(TOY_QUERIES, status="reviewed", reviewer="Gasim", on="2026-09-30")


def test_only_version_1_is_understood():
    data = _gold(TOY_QUERIES)
    data["version"] = 2
    with pytest.raises(ValidationError, match="version"):
        GoldSet.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda q: q["relevant"][0].update(chunk="kb/notes/nope"), "does not exist"),
        (lambda q: q["relevant"][0].update(phrase="never written"), "does not contain"),
        (lambda q: q.update(query="!!!"), "no searchable terms"),
        (lambda q: q.update(filters={"topic": "gossip"}), "valid values"),
        (lambda q: q.update(query="x" * 201), "limit is 200"),
    ],
)
def test_validation_names_every_problem_and_stops_before_measuring(toy_index, change, message):
    queries = copy.deepcopy(TOY_QUERIES)
    change(queries[0])
    with pytest.raises(EvaluationError, match=message):
        evaluate(toy_index, GoldSet.model_validate(_gold(queries)), "0" * 64)


def test_a_relevant_chunk_that_the_queries_own_filters_exclude_is_refused(temp_dir):
    config = settings()
    index = KnowledgeIndex(
        build_corpus(config, synthetic_root(temp_dir)), config.bm25, config.retrieval
    )
    query = {
        "id": "T01",
        "category": "terminology",
        "author": "assistant",
        "query": "thing",
        "filters": {"doc_type": "method"},
        "relevant": [{"chunk": "kb/decisions/d-001", "phrase": "the first thing"}],
        "note": "n",
    }
    with pytest.raises(EvaluationError, match="excluded by the query's filters"):
        validate_gold(GoldSet.model_validate(_gold([query])), index)


def test_all_problems_are_listed_together(toy_index):
    queries = copy.deepcopy(TOY_QUERIES)
    queries[0]["relevant"][0]["chunk"] = "kb/notes/nope"
    queries[1]["relevant"][0]["phrase"] = "never written"
    with pytest.raises(EvaluationError) as error:
        validate_gold(GoldSet.model_validate(_gold(queries)), toy_index)
    assert "P01" in str(error.value) and "T01" in str(error.value)


# --- Loading the gold file -----------------------------------------------------------------------

GOLD_YAML = """version: 1
review: {status: draft, reviewed_by: null, reviewed_on: null}
queries:
  - id: P01
    category: paraphrase
    author: assistant
    query: "alpha"
    relevant:
      - {chunk: "kb/notes/beta", phrase: "alpha gamma delta"}
    note: n
"""


def test_the_gold_hash_ignores_line_endings(temp_dir):
    lf, crlf = temp_dir / "lf.yaml", temp_dir / "crlf.yaml"
    lf.write_bytes(GOLD_YAML.encode())
    crlf.write_bytes(GOLD_YAML.replace("\n", "\r\n").encode())
    (a, hash_a), (b, hash_b) = load_gold(lf), load_gold(crlf)
    assert a == b and hash_a == hash_b and len(hash_a) == 64
    lf.write_bytes(GOLD_YAML.replace("alpha", "omega").encode())
    assert load_gold(lf)[1] != hash_a


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("queries: [", "cannot read the gold set"),
        (GOLD_YAML.replace("version: 1", "version: 1\nversion: 1"), "cannot read the gold set"),
        (GOLD_YAML.replace("note: n", "note: n\n    surprise: 1"), "Extra inputs"),
        ("- just\n- a list\n", "cannot read the gold set"),
    ],
)
def test_an_unreadable_or_invalid_gold_file_is_an_evaluation_error(temp_dir, text, message):
    path = temp_dir / "gold.yaml"
    path.write_bytes(text.encode())
    with pytest.raises(EvaluationError, match=message):
        load_gold(path)


def test_a_missing_gold_file_is_an_evaluation_error(temp_dir):
    with pytest.raises(EvaluationError, match="cannot read the gold set"):
        load_gold(temp_dir / "absent.yaml")


# --- The report ----------------------------------------------------------------------------------


def test_the_report_states_its_setup_and_that_it_is_a_draft(toy_index, toy_run):
    report = toy_run.report
    assert toy_run.corpus_fingerprint in report and "f" * 64 in report
    assert toy_run.retrieval_fingerprint in report
    for text in (
        "chunker-v1",
        "tokenizer-v1",
        "BM25 k1 | 1.2",
        "BM25 b | 0.75",
        "Largest top_k | 5",
    ):
        assert text in report
    assert "DRAFT GOLD SET, NOT YET REVIEWED" in report and "not a gate" in report
    assert "Gold set review status | draft" in report
    assert "assistant 6" in report and "paraphrase 1, terminology 1" in report


def test_a_reviewed_gold_set_changes_the_banner(toy_index):
    gold = _invalid(TOY_QUERIES, status="reviewed", reviewer="Gasim", on="2026-09-30")
    report = evaluate(toy_index, gold, "0" * 64).report
    assert "reviewed by Gasim on 2026-09-30" in report and "DRAFT" not in report


def test_the_report_gives_hit_recall_mrr_per_category_and_pooled(toy_run):
    report = toy_run.report
    for k in KS:
        assert f"Hit@{k}" in report and f"Recall@{k}" in report
    assert "MRR" in report and "**all in-scope**" in report
    for category in CATEGORIES[:-1]:
        assert f"| {category} |" in report
    assert "| **all in-scope** | 4 | 2 of 4 | 3 of 4 | 3 of 4 | 3 of 4 |" in report
    assert "0.375 | 0.625 | 0.625 | 0.625 | 0.625" in report


def test_the_report_lists_the_misses_with_the_rank_of_each_relevant_chunk(toy_run):
    report = toy_run.report
    assert "1 in-scope query with no relevant chunk in the top 10" in report
    assert "**M01**, decision" in report and "`kb/notes/alpha` (rank not retrieved)" in report


def test_the_report_says_when_nothing_was_missed(toy_index):
    gold = GoldSet.model_validate(_gold(TOY_QUERIES[:1] + TOY_QUERIES[2:3]))
    assert "No in-scope query missed" in evaluate(toy_index, gold, "0" * 64).report


def test_the_report_describes_out_of_scope_matches_without_choosing_a_threshold(toy_run):
    report = toy_run.report
    assert "1 of 2 out-of-scope queries matched no chunk at all" in report
    assert "no threshold is chosen" in report
    assert "Top-1 scores: out-of-scope" in report


def test_the_report_is_measurements_only(toy_run):
    report = toy_run.report
    assert "No pass or fail gate has been defined" in report
    assert "nothing here is combined into one score" in report
    assert "no embeddings" in report and "deterministic lexical knowledge retrieval layer" in report
    assert "accuracy" not in report.lower() and "semantic" not in report.replace(
        "no notion of meaning", ""
    )


def test_every_table_row_has_the_same_number_of_cells_as_its_header(toy_run):
    block = []
    for line in [*toy_run.report.splitlines(), ""]:
        if line.startswith("|"):
            block.append(line.replace("\\|", "").count("|"))
        elif block:
            assert len(set(block)) == 1, block
            block = []


# --- Running on the real corpus and gold set -----------------------------------------------------


@pytest.fixture(scope="module")
def real_run():
    return run_evaluation(load_config(), write=False)


def test_the_real_gold_set_is_valid_and_has_the_approved_shape(real_run):
    counts = {c: sum(r.query.category == c for r in real_run.results) for c in CATEGORIES}
    assert counts == {
        "paraphrase": 11,
        "terminology": 6,
        "definition": 9,
        "decision": 10,
        "out_of_scope": 6,
    }
    assert real_run.pooled.n == 36 and len(real_run.results) == 42
    authors = [r.query.author for r in real_run.results]
    assert (authors.count("assistant"), authors.count("user")) == (36, 6)


def test_real_measurements_obey_their_own_definitions(real_run):
    for metrics in (*real_run.by_category.values(), real_run.pooled):
        assert all(0 <= hits <= metrics.n for hits in metrics.hits)
        assert list(metrics.hits) == sorted(metrics.hits)  # more chunks never lose a hit
        assert list(metrics.recall) == sorted(metrics.recall)
        assert all(0.0 <= value <= 1.0 for value in (*metrics.recall, metrics.mrr, metrics.overlap))
    assert real_run.pooled.n == sum(m.n for m in real_run.by_category.values())
    for result in real_run.results:
        if result.query.category != "out_of_scope":
            ranks = [rank for rank in result.relevant_ranks if rank is not None]
            assert result.first_rank == (min(ranks) if ranks else None)


def test_the_real_report_is_reproducible_and_labelled_a_draft(real_run):
    again = run_evaluation(load_config(), write=False)
    assert again.report == real_run.report and again.gold_sha256 == real_run.gold_sha256
    assert "DRAFT GOLD SET, NOT YET REVIEWED" in real_run.report
    assert (
        real_run.corpus_fingerprint in real_run.report and real_run.gold_sha256 in real_run.report
    )


def test_the_report_is_written_only_when_asked_and_with_unix_line_endings(temp_dir):
    config = load_config()
    target = temp_dir / "out" / "knowledge_eval.md"
    run_evaluation(config, write=False, report_path=target)
    assert not target.exists()
    run = run_evaluation(config, write=True, report_path=target)
    written = target.read_bytes()
    assert written == run.report.encode("utf-8") and b"\r" not in written
