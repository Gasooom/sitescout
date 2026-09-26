"""Milestone 10, Phase 1 (D-055, D-056): the local project-knowledge index.

A deterministic lexical retrieval layer (Okapi BM25) over allow-listed project documents.
It returns documentation text with stable chunk ids and metadata. It is a knowledge source
only and never holds or replaces structured SiteScout site data. It uses no model,
embedding, network access or credential.
"""

from sitescout.knowledge.chunking import CHUNKER_VERSION, Chunk, Corpus, build_corpus
from sitescout.knowledge.corpus import CorpusError, KnowledgeError
from sitescout.knowledge.retrieval import (
    TOKENIZER_VERSION,
    Hit,
    KnowledgeIndex,
    KnowledgeSearchResult,
    SearchError,
    SearchFilters,
    build_knowledge_index,
    search_knowledge,
)

__all__ = [
    "CHUNKER_VERSION",
    "TOKENIZER_VERSION",
    "Chunk",
    "Corpus",
    "CorpusError",
    "Hit",
    "KnowledgeError",
    "KnowledgeIndex",
    "KnowledgeSearchResult",
    "SearchError",
    "SearchFilters",
    "build_corpus",
    "build_knowledge_index",
    "search_knowledge",
]
