"""Milestone 10, Phase 1 (D-056): the retrieval gold set and its evaluation.

``run_evaluation`` builds the corpus and the BM25 index from the configuration, loads the
gold set (``paths.knowledge_gold``), validates it, ranks every query and reports
measurements in ``reports/knowledge_eval.md`` (``paths.knowledge_eval_report``), filled from
``templates/knowledge_eval.md``. Nothing here uses a model, the network, the clock or
randomness, so two runs on the same files give a byte-identical report.

**The gold set.** Each query names the chunks that answer it, by stable id plus a phrase that
must occur verbatim in that chunk, and its category (paraphrase, terminology, definition,
decision, out of scope), author and optional filters. Validation refuses a gold set with a
duplicate id, a chunk that does not exist, a phrase that no longer occurs in its chunk, a
relevant chunk that the query's own filters exclude, an out-of-scope query with relevant
chunks or any other query without one. An invalid gold set stops the evaluation before any
metric is computed.

**Measurements** (in-scope queries, per category and pooled, at k = 1, 3, 5 and 10):

- *Hit@k*: the query has at least one relevant chunk in the top k.
- *Recall@k*: the fraction of the query's relevant chunks in the top k, averaged over queries.
- *MRR*: the mean of 1 / (rank of the first relevant chunk), and 0 when none is ranked.

Out-of-scope queries are reported separately: how many chunks match them at all and their
top scores next to the in-scope top scores. BM25 has no relevance threshold, and Phase 1
chooses none. There is no pass or fail gate, and nothing is combined into one score.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sitescout.config import Config, ConfigError, read_yaml
from sitescout.knowledge.chunking import CHUNKER_VERSION, Chunk
from sitescout.knowledge.corpus import KnowledgeError, normalize_text, sha256_text
from sitescout.knowledge.retrieval import (
    TOKENIZER_VERSION,
    KnowledgeIndex,
    SearchError,
    SearchFilters,
    build_knowledge_index,
    tokenize,
)

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parents[1] / "templates" / "knowledge_eval.md"
KS = (1, 3, 5, 10)
Category = Literal["paraphrase", "terminology", "definition", "decision", "out_of_scope"]
CATEGORIES: tuple[Category, ...] = (
    "paraphrase",
    "terminology",
    "definition",
    "decision",
    "out_of_scope",
)
IN_SCOPE: tuple[Category, ...] = CATEGORIES[:-1]
CATEGORY_LETTER = {
    "paraphrase": "P",
    "terminology": "T",
    "definition": "D",
    "decision": "M",
    "out_of_scope": "O",
}


class EvaluationError(KnowledgeError):
    """The gold set is invalid, or cannot be checked against the corpus."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- The gold set ------------------------------------------------------------------------------


class Relevant(_Model):
    chunk: str = Field(min_length=1)
    phrase: str = Field(min_length=1)


class GoldQuery(_Model):
    id: str = Field(pattern=r"^[PTDMO]\d{2}$")
    category: Category
    author: Literal["assistant", "user"]
    query: str = Field(min_length=1)
    filters: SearchFilters = SearchFilters()
    relevant: tuple[Relevant, ...] = ()
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.id[0] != CATEGORY_LETTER[self.category]:
            raise ValueError(
                f"{self.id}: the id letter must be {CATEGORY_LETTER[self.category]!r} "
                f"for category {self.category!r}"
            )
        if self.category == "out_of_scope" and self.relevant:
            raise ValueError(f"{self.id}: an out-of-scope query has no relevant chunk")
        if self.category != "out_of_scope" and not self.relevant:
            raise ValueError(f"{self.id}: an in-scope query needs at least one relevant chunk")
        chunks = [item.chunk for item in self.relevant]
        if len(set(chunks)) != len(chunks):
            raise ValueError(f"{self.id}: a relevant chunk is listed twice")
        return self


class Review(_Model):
    status: Literal["draft", "reviewed"]
    reviewed_by: str | None
    reviewed_on: str | None

    @model_validator(mode="after")
    def _reviewed_is_signed(self) -> Self:
        if self.status == "reviewed" and not (self.reviewed_by and self.reviewed_on):
            raise ValueError("a reviewed gold set names who reviewed it and when")
        return self


class GoldSet(_Model):
    version: Literal[1]
    review: Review
    queries: tuple[GoldQuery, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [query.id for query in self.queries]
        texts = [query.query for query in self.queries]
        if len(set(ids)) != len(ids):
            duplicated = sorted(item for item, count in Counter(ids).items() if count > 1)
            raise ValueError(f"duplicate query ids: {duplicated}")
        if len(set(texts)) != len(texts):
            raise ValueError("two queries have the same text")
        return self


def load_gold(path: Path) -> tuple[GoldSet, str]:
    """The gold set at ``path`` and the SHA-256 of its newline-normalized text."""
    try:
        data = read_yaml(path)
        digest = sha256_text(normalize_text(path.read_bytes()))
    except (ConfigError, OSError, KnowledgeError) as error:
        raise EvaluationError(f"cannot read the gold set {path}: {error}") from error
    try:
        return GoldSet.model_validate(data), digest
    except ValidationError as error:
        raise EvaluationError(f"invalid gold set {path}:\n{error}") from error


def validate_gold(gold: GoldSet, index: KnowledgeIndex) -> None:
    """Check every query against the corpus; raise ``EvaluationError`` listing every problem."""
    problems: list[str] = []
    for query in gold.queries:
        try:
            index.query_terms(query.query)
            index.check_filters(query.filters)
        except SearchError as error:
            problems.append(f"{query.id}: {error}")
        wanted = query.filters.active()
        for item in query.relevant:
            chunk = index.corpus.by_id.get(item.chunk)
            if chunk is None:
                problems.append(f"{query.id}: the relevant chunk {item.chunk!r} does not exist")
                continue
            if item.phrase not in chunk.text:
                problems.append(f"{query.id}: {item.chunk!r} does not contain {item.phrase!r}")
            if any(getattr(chunk, name) != value for name, value in wanted.items()):
                problems.append(f"{query.id}: {item.chunk!r} is excluded by the query's filters")
    if problems:
        raise EvaluationError("the gold set does not match the corpus:\n- " + "\n- ".join(problems))


# --- Measuring ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryResult:
    query: GoldQuery
    matches: int  # chunks with a score above 0 within the query's filters
    relevant_ranks: tuple[int | None, ...]  # rank of each relevant chunk, None if not ranked
    first_rank: int | None
    hit: tuple[bool, ...]  # Hit@k for each k in KS
    recall: tuple[float, ...]  # Recall@k for each k in KS
    reciprocal_rank: float
    top1_chunk: str | None
    top1_score: float | None
    vocabulary_overlap: float | None  # share of query terms found in the relevant chunks


def evaluate_query(index: KnowledgeIndex, query: GoldQuery) -> QueryResult:
    ranking = index.rank(query.query, query.filters)
    chunks = index.corpus.chunks
    position = {chunks[match.index].chunk_id: rank for rank, match in enumerate(ranking, start=1)}
    ranks = tuple(position.get(item.chunk) for item in query.relevant)
    found = [rank for rank in ranks if rank is not None]
    first = min(found) if found else None
    overlap = None
    if query.relevant:
        terms = set(tokenize(query.query))
        seen: set[str] = set()
        for item in query.relevant:
            seen |= set(tokenize(index.corpus.by_id[item.chunk].search_text()))
        overlap = len(terms & seen) / len(terms)
    return QueryResult(
        query=query,
        matches=len(ranking),
        relevant_ranks=ranks,
        first_rank=first,
        hit=tuple(first is not None and first <= k for k in KS),
        recall=tuple(
            sum(1 for rank in found if rank <= k) / len(ranks) if ranks else 0.0 for k in KS
        ),
        reciprocal_rank=1 / first if first else 0.0,
        top1_chunk=chunks[ranking[0].index].chunk_id if ranking else None,
        top1_score=ranking[0].score if ranking else None,
        vocabulary_overlap=overlap,
    )


@dataclass(frozen=True, slots=True)
class Metrics:
    """Measurements over a group of in-scope queries."""

    n: int
    hits: tuple[int, ...]  # queries with Hit@k, for each k in KS
    recall: tuple[float, ...]  # mean Recall@k
    mrr: float
    overlap: float  # mean vocabulary overlap


def aggregate(results: Sequence[QueryResult]) -> Metrics:
    n = len(results)
    return Metrics(
        n=n,
        hits=tuple(sum(1 for result in results if result.hit[i]) for i in range(len(KS))),
        recall=tuple(statistics.fmean(r.recall[i] for r in results) for i in range(len(KS))),
        mrr=statistics.fmean(result.reciprocal_rank for result in results),
        overlap=statistics.fmean(r.vocabulary_overlap or 0.0 for r in results),
    )


@dataclass
class EvaluationRun:
    """Everything one evaluation measured, and the report text built from it."""

    corpus_fingerprint: str
    retrieval_fingerprint: str
    gold_sha256: str
    gold: GoldSet
    results: tuple[QueryResult, ...]
    by_category: dict[str, Metrics]
    pooled: Metrics
    report: str = ""

    def in_scope(self) -> tuple[QueryResult, ...]:
        return tuple(r for r in self.results if r.query.category != "out_of_scope")


# --- The report --------------------------------------------------------------------------------


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(item) for item in row) + " |" for row in rows]
    return "\n".join(lines)


def _rank(value: int | None) -> str:
    return "not retrieved" if value is None else str(value)


def _banner(gold: GoldSet) -> str:
    if gold.review.status == "reviewed":
        return f"Gold set reviewed by {gold.review.reviewed_by} on {gold.review.reviewed_on}."
    return (
        "**DRAFT GOLD SET, NOT YET REVIEWED: these measurements are not a gate.** "
        "The queries were written by the assistant from the indexed documents, before this "
        "measurement, and await review and additions by the project owner."
    )


def _setup_table(index: KnowledgeIndex, run: EvaluationRun) -> str:
    settings = index.retrieval
    authors = Counter(result.query.author for result in run.results)
    categories = Counter(result.query.category for result in run.results)
    rows = [
        ("Corpus fingerprint (SHA-256)", f"`{run.corpus_fingerprint}`"),
        ("Documents", len({chunk.source_path for chunk in index.corpus.chunks})),
        ("Chunks", len(index.corpus.chunks)),
        ("Gold set file", "`tests/knowledge_gold.yaml`"),
        ("Gold set SHA-256 (newlines normalized)", f"`{run.gold_sha256}`"),
        ("Gold set review status", run.gold.review.status),
        ("Gold queries", len(run.results)),
        ("Queries by category", ", ".join(f"{c} {categories[c]}" for c in CATEGORIES)),
        ("Queries by author", ", ".join(f"{a} {authors[a]}" for a in sorted(authors))),
        ("Chunker version", CHUNKER_VERSION),
        ("Tokenizer version", TOKENIZER_VERSION),
        ("BM25 k1", index.bm25.k1),
        ("BM25 b", index.bm25.b),
        ("Retrieval fingerprint (SHA-256)", f"`{run.retrieval_fingerprint}`"),
        ("Largest top_k", settings.max_top_k),
    ]
    return _table(("Item", "Value"), rows)


def _corpus_table(chunks: Sequence[Chunk]) -> str:
    rows = []
    for path in dict.fromkeys(chunk.source_path for chunk in chunks):
        own = [chunk for chunk in chunks if chunk.source_path == path]
        rows.append(
            (
                f"`{path}`",
                own[0].doc_type,
                len(own),
                sum(chunk.char_count for chunk in own),
                sum(chunk.oversize for chunk in own),
            )
        )
    rows.append(
        (
            "**All**",
            "",
            len(chunks),
            sum(chunk.char_count for chunk in chunks),
            sum(chunk.oversize for chunk in chunks),
        )
    )
    return _table(("Document", "doc_type", "Chunks", "Characters", "Oversize chunks"), rows)


def _metric_rows(run: EvaluationRun) -> list[tuple[str, Metrics]]:
    rows = [(category, run.by_category[category]) for category in IN_SCOPE]
    rows.append(("**all in-scope**", run.pooled))
    return rows


def _hit_table(run: EvaluationRun) -> str:
    rows = [(name, m.n, *(f"{hits} of {m.n}" for hits in m.hits)) for name, m in _metric_rows(run)]
    return _table(("Category", "Queries", *(f"Hit@{k}" for k in KS)), rows)


def _recall_table(run: EvaluationRun) -> str:
    rows = [
        (name, m.n, *(f"{value:.3f}" for value in m.recall), f"{m.mrr:.3f}", f"{m.overlap:.2f}")
        for name, m in _metric_rows(run)
    ]
    headers = ("Category", "Queries", *(f"Recall@{k}" for k in KS), "MRR", "Vocabulary overlap")
    return _table(headers, rows)


def _query_table(run: EvaluationRun) -> str:
    rows = []
    for result in run.in_scope():
        found = sum(1 for rank in result.relevant_ranks if rank is not None and rank <= KS[-1])
        rows.append(
            (
                result.query.id,
                result.query.category,
                result.query.author,
                _rank(result.first_rank),
                f"{found} of {len(result.relevant_ranks)}",
                f"`{result.top1_chunk}`" if result.top1_chunk else "none",
                f"{result.vocabulary_overlap:.2f}" if result.vocabulary_overlap is not None else "",
            )
        )
    headers = (
        "Query",
        "Category",
        "Author",
        "First relevant rank",
        f"Relevant in top {KS[-1]}",
        "Top-1 chunk",
        "Vocabulary overlap",
    )
    return _table(headers, rows)


def _misses(run: EvaluationRun) -> str:
    missed = [result for result in run.in_scope() if not result.hit[-1]]
    if not missed:
        return f"No in-scope query missed: every one has a relevant chunk in the top {KS[-1]}."
    lines = [
        f"{len(missed)} in-scope quer{'y' if len(missed) == 1 else 'ies'} with no relevant chunk "
        f"in the top {KS[-1]}:",
        "",
    ]
    for result in missed:
        ranks = ", ".join(
            f"`{item.chunk}` (rank {_rank(rank)})"
            for item, rank in zip(result.query.relevant, result.relevant_ranks, strict=True)
        )
        lines.append(
            f"- **{result.query.id}**, {result.query.category}: {result.query.query!r}. {ranks}"
        )
    return "\n".join(lines)


def _out_of_scope(run: EvaluationRun) -> str:
    outside = [r for r in run.results if r.query.category == "out_of_scope"]
    if not outside:
        return "The gold set has no out-of-scope query."
    rows = [
        (
            r.query.id,
            r.query.query,
            r.matches,
            f"{r.top1_score:.3f}" if r.top1_score is not None else "none",
            f"`{r.top1_chunk}`" if r.top1_chunk else "none",
        )
        for r in outside
    ]
    table = _table(("Query", "Text", "Chunks matching", "Top-1 score", "Top-1 chunk"), rows)
    empty = sum(1 for r in outside if r.matches == 0)
    outside_scores = [r.top1_score for r in outside if r.top1_score is not None]
    inside_scores = [r.top1_score for r in run.in_scope() if r.top1_score is not None]
    lines = [
        table,
        "",
        f"{empty} of {len(outside)} out-of-scope queries matched no chunk at all; "
        f"{len(outside) - empty} shared a word with at least one chunk and returned it.",
    ]
    if outside_scores and inside_scores:
        highest, lowest = max(outside_scores), min(inside_scores)
        median = statistics.median(inside_scores)
        lines.append(
            f"Top-1 scores: out-of-scope {min(outside_scores):.3f} to {highest:.3f}; in-scope "
            f"{lowest:.3f} to {max(inside_scores):.3f} (median {median:.3f}). "
            + (
                "The highest out-of-scope score is below the lowest in-scope score."
                if highest < lowest
                else "The ranges overlap: a score cut-off alone would not separate them."
            )
        )
    lines.append(
        "BM25 scores depend on the query's length and on how rare its words are, so they are "
        "not comparable across queries; this is a diagnostic, and no threshold is chosen."
    )
    return "\n".join(lines)


def _caveats(run: EvaluationRun) -> str:
    sizes = sorted({run.by_category[c].n for c in IN_SCOPE if run.by_category[c].n})
    granularity = ", ".join(f"about {100 / n:.0f} points for {n} queries" for n in sizes)
    return "\n".join(
        [
            f"- The gold set is small. One query moves a category's Hit@k by {granularity}, "
            "so differences of a query or two are not meaningful.",
            "- Relevance is binary and judged by the phrase anchored in each chunk. A relevant "
            "chunk other than the listed ones may also answer a query; it is counted as not "
            "relevant.",
            "- The queries were written from the indexed documents. Vocabulary overlap shows "
            "how much of each query's wording appears in its relevant chunks: a query with "
            "low overlap cannot be found by word matching.",
            "- Nothing was tuned on these results. BM25 parameters, the tokenizer and the "
            "chunking were fixed before the first run; a later change needs a recorded "
            "decision and a fresh review of the gold set.",
        ]
    )


def render_report(index: KnowledgeIndex, run: EvaluationRun) -> str:
    return TEMPLATE.read_text(encoding="utf-8").format(
        banner=_banner(run.gold),
        setup=_setup_table(index, run),
        corpus=_corpus_table(index.corpus.chunks),
        hits=_hit_table(run),
        recall=_recall_table(run),
        queries=_query_table(run),
        misses=_misses(run),
        out_of_scope=_out_of_scope(run),
        caveats=_caveats(run),
    )


# --- Running -----------------------------------------------------------------------------------


def _metrics(results: Sequence[QueryResult]) -> Metrics:
    if not results:
        return Metrics(0, (0,) * len(KS), (0.0,) * len(KS), 0.0, 0.0)
    return aggregate(results)


def evaluate(index: KnowledgeIndex, gold: GoldSet, gold_sha256: str) -> EvaluationRun:
    """Validate ``gold`` against ``index``, measure every query and build the report."""
    validate_gold(gold, index)
    results = tuple(evaluate_query(index, query) for query in gold.queries)
    in_scope = [result for result in results if result.query.category != "out_of_scope"]
    run = EvaluationRun(
        corpus_fingerprint=index.corpus.fingerprint,
        retrieval_fingerprint=index.retrieval_fingerprint,
        gold_sha256=gold_sha256,
        gold=gold,
        results=results,
        by_category={
            category: _metrics([r for r in in_scope if r.query.category == category])
            for category in IN_SCOPE
        },
        pooled=_metrics(in_scope),
    )
    run.report = render_report(index, run)
    return run


def run_evaluation(
    config: Config, *, write: bool = True, report_path: Path | None = None
) -> EvaluationRun:
    """Build the index, evaluate the configured gold set and (unless ``write`` is false)
    write the report to ``report_path`` or ``paths.knowledge_eval_report``."""
    index = build_knowledge_index(config)
    gold, digest = load_gold(config.resolve(config.settings.paths.knowledge_gold))
    run = evaluate(index, gold, digest)
    if write:
        path = report_path or config.resolve(config.settings.paths.knowledge_eval_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(run.report.encode("utf-8"))
        log.info("Wrote %s", path)
    return run
