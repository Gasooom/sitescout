"""Milestone 10, Phase 1 (D-056): deterministic lexical retrieval over the knowledge corpus.

This is Okapi BM25 over the chunks of ``sitescout.knowledge.chunking``: a word-overlap
ranking, nothing more. It has no embeddings, no model, no network access and no notion of
meaning, so a query only finds chunks that share words with it. It is a knowledge source
only: it returns documentation text, never structured site facts, scores, ranks or network
coverage (the deterministic tools own those).

**Tokenizer v1.** Lowercase, then every run of Unicode word characters (letters, digits,
underscore) is a token; a token that contains ``_`` also yields its ``_``-separated parts, so
``pop_5km`` is found by ``pop_5km``, ``pop`` and ``5km``. No stemming, no stop words, no
synonyms. The index reads each chunk's section titles followed by its text.

**Scoring.** For each distinct query term ``t`` (in sorted order) present in a chunk::

    idf(t)  = ln(1 + (N - n + 0.5) / (n + 0.5))
    score  += idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / avglen))

with N chunks, ``n`` chunks containing the term, ``tf`` its count in the chunk, ``len`` the
chunk's token count and ``avglen`` the mean over the whole corpus. ``k1`` and ``b`` come from
``knowledge.bm25`` (the standard defaults, never tuned). Statistics always cover the whole
corpus, so a filter never changes a score.

**Order.** Chunks with a score above 0, sorted by score rounded to 9 decimals (descending)
and then by chunk id (ascending), so ties are broken the same way on every run and platform.
A query that shares no word with any chunk returns no hits; that is a result, not an error.

**Filters** (``doc_type``, ``topic``, ``milestone``, ``decision_id``) are exact and combined
with AND. A value the corpus never takes is a ``SearchError`` that lists the valid values.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Strict

from sitescout.config import Bm25Settings, Config, RetrievalSettings
from sitescout.knowledge.chunking import Chunk, Corpus, build_corpus
from sitescout.knowledge.corpus import KnowledgeError

TOKENIZER_VERSION = "tokenizer-v1"
SCORE_DECIMALS = 9

_WORD = re.compile(r"\w+")


class SearchError(KnowledgeError):
    """A query, a filter or a ``top_k`` was invalid."""


def tokenize(text: str) -> list[str]:
    """The tokens of ``text`` under tokenizer v1 (see the module docstring)."""
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        tokens.append(word)
        if "_" in word:
            tokens.extend(part for part in word.split("_") if part)
    return tokens


class SearchFilters(BaseModel):
    """Exact metadata filters, combined with AND. ``None`` means no filter on that field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_type: str | None = None
    topic: str | None = None
    milestone: Annotated[int, Strict()] | None = None
    decision_id: str | None = None

    def active(self) -> dict[str, str | int]:
        return {name: value for name, value in self.model_dump().items() if value is not None}


class Hit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int
    score: float
    matched_terms: tuple[str, ...]
    chunk: Chunk


class KnowledgeSearchResult(BaseModel):
    """What ``search_knowledge`` returns: the hits and everything needed to reproduce them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    top_k: int
    filters: SearchFilters
    corpus_fingerprint: str
    retrieval_fingerprint: str
    total_matches: int  # chunks with a score above 0 within the filters, before top_k
    hits: tuple[Hit, ...]


@dataclass(frozen=True, slots=True)
class Match:
    """One ranked chunk: its position in the corpus, its score and the query terms it has."""

    index: int
    score: float
    terms: tuple[str, ...]


def retrieval_fingerprint(bm25: Bm25Settings) -> str:
    """SHA-256 of what defines the ranking besides the corpus: tokenizer and BM25 parameters."""
    document = {"tokenizer": TOKENIZER_VERSION, "k1": bm25.k1, "b": bm25.b}
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class KnowledgeIndex:
    """The BM25 index of one corpus. Immutable after construction; built in memory."""

    def __init__(self, corpus: Corpus, bm25: Bm25Settings, retrieval: RetrievalSettings):
        self.corpus = corpus
        self.bm25 = bm25
        self.retrieval = retrieval
        self.retrieval_fingerprint = retrieval_fingerprint(bm25)
        self._vocabulary = corpus.vocabulary()
        self._lengths: list[int] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for index, chunk in enumerate(corpus.chunks):
            tokens = tokenize(chunk.search_text())
            self._lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                self._postings.setdefault(term, []).append((index, count))
        total = len(corpus.chunks)
        self._average_length = sum(self._lengths) / total
        self._idf = {
            term: math.log(1 + (total - len(posting) + 0.5) / (len(posting) + 0.5))
            for term, posting in self._postings.items()
        }

    # --- validation -------------------------------------------------------------------------

    def query_terms(self, query: str) -> list[str]:
        """The sorted distinct terms of a valid query; ``SearchError`` for an invalid one."""
        if not isinstance(query, str) or not query.strip():
            raise SearchError("the query must be a non-empty string")
        if len(query) > self.retrieval.max_query_chars:
            raise SearchError(
                f"the query has {len(query)} characters; the limit is "
                f"{self.retrieval.max_query_chars}"
            )
        terms = sorted(set(tokenize(query)))
        if not terms:
            raise SearchError("the query has no searchable terms (letters or digits)")
        return terms

    def check_filters(self, filters: SearchFilters) -> None:
        for name, value in filters.active().items():
            valid = self._vocabulary[name]
            if value not in valid:
                raise SearchError(f"no chunk has {name} = {value!r}; valid values: {list(valid)}")

    def check_top_k(self, top_k: int) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise SearchError("top_k must be an integer")
        if not 1 <= top_k <= self.retrieval.max_top_k:
            raise SearchError(f"top_k must be between 1 and {self.retrieval.max_top_k}")

    # --- ranking ----------------------------------------------------------------------------

    def rank(self, query: str, filters: SearchFilters | None = None) -> list[Match]:
        """Every chunk with a score above 0 within ``filters``, best first (module docstring)."""
        filters = filters or SearchFilters()
        terms = self.query_terms(query)
        self.check_filters(filters)
        wanted = filters.active()
        chunks = self.corpus.chunks
        allowed = {
            index
            for index, chunk in enumerate(chunks)
            if all(getattr(chunk, name) == value for name, value in wanted.items())
        }
        k1, b = self.bm25.k1, self.bm25.b
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        for term in terms:
            posting = self._postings.get(term)
            if posting is None:
                continue
            idf = self._idf[term]
            for index, count in posting:
                if index not in allowed:
                    continue
                norm = k1 * (1 - b + b * self._lengths[index] / self._average_length)
                scores[index] = scores.get(index, 0.0) + idf * count * (k1 + 1) / (count + norm)
                matched.setdefault(index, []).append(term)
        ranked = sorted(
            (
                (round(score, SCORE_DECIMALS), chunks[index].chunk_id, index)
                for index, score in scores.items()
            ),
            key=lambda item: (-item[0], item[1]),
        )
        return [
            Match(index=index, score=score, terms=tuple(matched[index]))
            for score, _, index in ranked
            if score > 0
        ]

    def search(
        self, query: str, *, top_k: int, filters: SearchFilters | None = None
    ) -> KnowledgeSearchResult:
        self.check_top_k(top_k)
        filters = filters or SearchFilters()
        ranking = self.rank(query, filters)
        hits = tuple(
            Hit(
                rank=position,
                score=match.score,
                matched_terms=match.terms,
                chunk=self.corpus.chunks[match.index],
            )
            for position, match in enumerate(ranking[:top_k], start=1)
        )
        return KnowledgeSearchResult(
            query=query,
            top_k=top_k,
            filters=filters,
            corpus_fingerprint=self.corpus.fingerprint,
            retrieval_fingerprint=self.retrieval_fingerprint,
            total_matches=len(ranking),
            hits=hits,
        )


def search_knowledge(
    index: KnowledgeIndex, query: str, *, top_k: int, filters: SearchFilters | None = None
) -> KnowledgeSearchResult:
    """Search the knowledge corpus; ``top_k`` is required. See the module docstring."""
    return index.search(query, top_k=top_k, filters=filters)


def build_knowledge_index(config: Config) -> KnowledgeIndex:
    """Read the allow-listed documents of ``config`` and build the index, in memory."""
    knowledge = config.settings.knowledge
    corpus = build_corpus(knowledge, config.root)
    return KnowledgeIndex(corpus, knowledge.bm25, knowledge.retrieval)
