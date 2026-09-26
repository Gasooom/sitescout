"""Milestone 10, Phase 1: deterministic BM25 retrieval, its filters and its limits."""

import ast
import hashlib
import math
from collections import Counter
from pathlib import Path

import pytest

from knowledge_support import settings, synthetic_root, write_docs
from sitescout.config import PROJECT_ROOT, load_config
from sitescout.knowledge import (
    KnowledgeIndex,
    KnowledgeSearchResult,
    SearchError,
    SearchFilters,
    build_corpus,
    build_knowledge_index,
    search_knowledge,
)
from sitescout.knowledge.retrieval import tokenize

NOTES = "# Notes\n\n## One\n\nzeta xray\n\n## Two\n\nyankee xray\n"
NOTES_SOURCE = {
    "path": "docs/notes.md",
    "doc_type": "method",
    "topic": "scoring",
    "preamble": False,
}


def _index(root, sources=None, **kwargs) -> KnowledgeIndex:
    config = settings(sources, **kwargs)
    return KnowledgeIndex(build_corpus(config, root), config.bm25, config.retrieval)


@pytest.fixture
def notes(temp_dir):
    write_docs(temp_dir, {"docs/notes.md": NOTES})
    return _index(temp_dir, [NOTES_SOURCE])


@pytest.fixture
def index(temp_dir):
    return _index(synthetic_root(temp_dir))


# --- The tokenizer -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("Percentile Points", ["percentile", "points"]),
        ("pop_5km", ["pop_5km", "pop", "5km"]),
        (
            "dist_road_m, dist_trunk_m",
            ["dist_road_m", "dist", "road", "m", "dist_trunk_m", "dist", "trunk", "m"],
        ),
        ("D-046: Network selection", ["d", "046", "network", "selection"]),
        ("EPSG:32735", ["epsg", "32735"]),
        ("log1p", ["log1p"]),
        ("λ = 0.01 and Σ", ["λ", "0", "01", "and", "σ"]),
        ("__x__", ["__x__", "x"]),
        ("!!! ???", []),
        ("", []),
    ],
)
def test_tokenizer_v1(text, tokens):
    assert tokenize(text) == tokens


# --- Scoring ------------------------------------------------------------------------------------


def test_a_single_rare_term_scores_its_idf_exactly(notes):
    # Two chunks of equal length: the length factor is 1, so score = idf = ln(1 + 1.5 / 1.5).
    result = search_knowledge(notes, "zeta", top_k=5)
    assert [hit.chunk.chunk_id for hit in result.hits] == ["kb/notes/one"]
    assert result.hits[0].score == round(math.log(2), 9)
    assert result.hits[0].matched_terms == ("zeta",)
    assert result.total_matches == 1


def test_equal_scores_are_ordered_by_chunk_id(notes):
    result = search_knowledge(notes, "xray", top_k=5)
    assert [(hit.rank, hit.chunk.chunk_id) for hit in result.hits] == [
        (1, "kb/notes/one"),
        (2, "kb/notes/two"),
    ]
    assert result.hits[0].score == result.hits[1].score == round(math.log(1.2), 9)


def test_ties_do_not_depend_on_the_order_of_the_sections(temp_dir):
    reversed_notes = "# Notes\n\n## Two\n\nyankee xray\n\n## One\n\nzeta xray\n"
    write_docs(temp_dir, {"docs/notes.md": reversed_notes})
    result = search_knowledge(_index(temp_dir, [NOTES_SOURCE]), "xray", top_k=5)
    assert [hit.chunk.chunk_id for hit in result.hits] == ["kb/notes/one", "kb/notes/two"]


def _reference_ranking(index: KnowledgeIndex, query: str, k1=1.2, b=0.75):
    """BM25 written out independently of the index: the same formula, plain loops."""
    chunks = index.corpus.chunks
    counts = [Counter(tokenize(chunk.search_text())) for chunk in chunks]
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / len(chunks)
    scores = {}
    for term in sorted(set(tokenize(query))):
        containing = sum(1 for count in counts if term in count)
        if not containing:
            continue
        idf = math.log(1 + (len(chunks) - containing + 0.5) / (containing + 0.5))
        for position, count in enumerate(counts):
            if term in count:
                tf = count[term]
                factor = tf * (k1 + 1) / (tf + k1 * (1 - b + b * lengths[position] / average))
                scores[position] = scores.get(position, 0.0) + idf * factor
    ranked = sorted(
        ((round(score, 9), chunks[position].chunk_id) for position, score in scores.items()),
        key=lambda item: (-item[0], item[1]),
    )
    return [(chunk_id, score) for score, chunk_id in ranked]


@pytest.mark.parametrize(
    "query",
    ["alpha beta", "gamma", "thing", "decision first", "pop_5km", "detail", "the", "no such word"],
)
def test_the_index_agrees_with_an_independent_bm25(index, query):
    expected = _reference_ranking(index, query)
    got = [(match.index, match.score) for match in index.rank(query)]
    assert [(index.corpus.chunks[i].chunk_id, score) for i, score in got] == expected


def test_a_query_word_repeated_or_recased_counts_once(index):
    assert index.rank("Gamma gamma GAMMA") == index.rank("gamma")


def test_a_query_with_no_shared_word_returns_no_hits_and_no_error(index):
    result = search_knowledge(index, "quokka", top_k=3)
    assert result.hits == () and result.total_matches == 0


def test_matched_terms_are_the_sorted_query_terms_the_chunk_holds(index):
    hit = search_knowledge(index, "beta zzz alpha", top_k=1).hits[0]
    assert hit.matched_terms == ("alpha", "beta")


def test_identifiers_are_found_whole_and_by_part(index):
    whole = search_knowledge(index, "pop_5km", top_k=1).hits[0].chunk.chunk_id
    part = search_knowledge(index, "5km", top_k=1).hits[0].chunk.chunk_id
    assert whole == part == "kb/guide/gamma-milestone-4"


# --- Determinism -------------------------------------------------------------------------------


def test_the_same_query_gives_the_same_result_twice_and_from_a_fresh_index(temp_dir, index):
    first = search_knowledge(index, "alpha decision", top_k=5)
    assert first == search_knowledge(index, "alpha decision", top_k=5)
    fresh = _index(synthetic_root(temp_dir / "again"))
    assert first == search_knowledge(fresh, "alpha decision", top_k=5)


def test_a_result_carries_the_fingerprints_that_reproduce_it(index):
    result = search_knowledge(index, "alpha", top_k=2)
    assert result.corpus_fingerprint == index.corpus.fingerprint
    assert result.retrieval_fingerprint == index.retrieval_fingerprint
    assert len(result.retrieval_fingerprint) == 64


def test_the_retrieval_fingerprint_follows_the_bm25_parameters(temp_dir):
    root = synthetic_root(temp_dir)
    base = _index(root)
    corpus = base.corpus
    other = KnowledgeIndex(
        corpus, settings().bm25.model_copy(update={"k1": 1.5}), settings().retrieval
    )
    assert other.retrieval_fingerprint != base.retrieval_fingerprint


def test_a_result_survives_a_json_round_trip(index):
    result = search_knowledge(index, "alpha", top_k=3, filters=SearchFilters(doc_type="method"))
    assert KnowledgeSearchResult.model_validate_json(result.model_dump_json()) == result


# --- Filters -----------------------------------------------------------------------------------


def test_each_filter_keeps_only_matching_chunks(index):
    def ids(**filters):
        result = search_knowledge(index, "thing", top_k=5, filters=SearchFilters(**filters))
        return sorted(hit.chunk.chunk_id for hit in result.hits)

    assert ids() == ["kb/decisions/d-001", "kb/decisions/d-002", "kb/decisions/d-003"]
    assert ids(decision_id="D-001") == ["kb/decisions/d-001"]
    assert ids(milestone=6) == ["kb/decisions/d-003"]
    assert ids(topic="project") == ["kb/decisions/d-002"]
    assert ids(doc_type="decision") == ids()
    assert ids(doc_type="method") == []


def test_filters_combine_with_and(index):
    result = search_knowledge(
        index, "thing", top_k=5, filters=SearchFilters(decision_id="D-001", milestone=2)
    )
    assert [hit.chunk.chunk_id for hit in result.hits] == ["kb/decisions/d-001"]
    empty = search_knowledge(
        index, "thing", top_k=5, filters=SearchFilters(decision_id="D-001", milestone=6)
    )
    assert empty.hits == () and empty.total_matches == 0  # valid values, no chunk has both


def test_a_filter_never_changes_a_score(index):
    everything = {h.chunk.chunk_id: h.score for h in search_knowledge(index, "thing", top_k=5).hits}
    only = search_knowledge(index, "thing", top_k=5, filters=SearchFilters(decision_id="D-003"))
    assert only.hits[0].score == everything["kb/decisions/d-003"]


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (
            {"doc_type": "report"},
            "no chunk has doc_type = 'report'; valid values: \\['decision', 'method'\\]",
        ),
        ({"topic": "nope"}, "valid values"),
        ({"milestone": 5}, "valid values: \\[2, 4, 6\\]"),
        ({"decision_id": "D-0000"}, "valid values: \\['D-001', 'D-003'\\]"),
    ],
)
def test_a_filter_value_the_corpus_never_takes_is_an_error_listing_the_valid_ones(
    index, filters, message
):
    with pytest.raises(SearchError, match=message):
        search_knowledge(index, "thing", top_k=3, filters=SearchFilters(**filters))


def test_filters_are_strict_about_their_types_and_names():
    with pytest.raises(ValueError, match="Extra inputs"):
        SearchFilters(colour="red")
    with pytest.raises(ValueError, match="valid integer"):
        SearchFilters(milestone="6")
    with pytest.raises(ValueError, match="valid integer"):
        SearchFilters(milestone=True)


# --- Limits ------------------------------------------------------------------------------------


@pytest.mark.parametrize("top_k", [0, -1, 6, 100, True, 2.0, "3", None])
def test_top_k_must_be_an_integer_between_1_and_the_configured_maximum(index, top_k):
    with pytest.raises(SearchError, match="top_k"):
        search_knowledge(index, "alpha", top_k=top_k)


def test_top_k_caps_the_hits_but_not_the_match_count(index):
    result = search_knowledge(index, "the", top_k=2)
    assert len(result.hits) == 2 and result.total_matches >= 2
    assert [hit.rank for hit in result.hits] == [1, 2]
    scores = [hit.score for hit in result.hits]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("query", ["", "   ", "\n", "!!! ???", None, 5])
def test_a_query_needs_searchable_text(index, query):
    with pytest.raises(SearchError, match="query"):
        search_knowledge(index, query, top_k=2)


def test_a_query_over_the_length_limit_is_refused(index):
    with pytest.raises(SearchError, match="limit is 200"):
        search_knowledge(index, "a" * 201, top_k=2)
    search_knowledge(index, "a" * 200, top_k=2)


# --- The real corpus ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_index():
    return build_knowledge_index(load_config())


@pytest.mark.parametrize(
    "query", ["percentile", "grid evidence", "spacing", "D 046", "socket", "quokka zebra"]
)
def test_real_results_are_ordered_capped_and_reproducible(real_index, query):
    result = search_knowledge(real_index, query, top_k=10)
    assert result == search_knowledge(real_index, query, top_k=10)
    assert len(result.hits) == min(10, result.total_matches)
    keys = [(-hit.score, hit.chunk.chunk_id) for hit in result.hits]
    assert keys == sorted(keys)
    assert all(hit.chunk == real_index.corpus.by_id[hit.chunk.chunk_id] for hit in result.hits)
    assert result.corpus_fingerprint == real_index.corpus.fingerprint


# --- Boundaries --------------------------------------------------------------------------------

KNOWLEDGE_FILES = sorted((PROJECT_ROOT / "src" / "sitescout" / "knowledge").glob("*.py"))
FORBIDDEN_MODULES = {
    "openai",
    "anthropic",
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "http",
    "socket",
    "ssl",
    "subprocess",
}


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize(
    "path", [*KNOWLEDGE_FILES, PROJECT_ROOT / "scripts" / "knowledge.py"], ids=lambda p: p.name
)
def test_the_knowledge_layer_imports_no_model_network_or_analyst_code(path):
    for module in _imports(path):
        assert module.split(".")[0] not in FORBIDDEN_MODULES, module
        assert not module.startswith("sitescout.analyst"), module


@pytest.mark.parametrize(
    "path", [p for p in KNOWLEDGE_FILES if p.name != "evaluation.py"], ids=lambda p: p.name
)
def test_only_the_evaluation_module_can_write_a_file_and_none_reads_the_environment(path):
    source = path.read_text(encoding="utf-8")
    for call in ("write_bytes", "write_text", "open(", "mkdir(", "os.environ", "getenv"):
        assert call not in source, f"{path.name} uses {call}"


def _snapshot(*roots: str) -> dict[str, str]:
    files = {}
    for root in roots:
        for path in sorted((PROJECT_ROOT / root).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(PROJECT_ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return files


def test_building_searching_and_evaluating_write_nothing():
    from sitescout.knowledge.evaluation import run_evaluation

    roots = ("docs", "config", "reports", "data/export", "src/sitescout/analyst")
    before = _snapshot(*roots)
    config = load_config()
    search_knowledge(build_knowledge_index(config), "score", top_k=3)
    run_evaluation(config, write=False)
    assert _snapshot(*roots) == before
