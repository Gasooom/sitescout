"""Milestone 10, Phase 1: the local project-knowledge index (D-055, D-056).

A deterministic lexical retrieval layer (Okapi BM25) over the allow-listed project documents
of `knowledge:` in config/settings.yaml. It uses no model, embedding, network access or key,
reads only those documents, and is a knowledge source only: structured site facts come from
the deterministic tools.

  uv run python scripts/knowledge.py verify
      Build the corpus, check its invariants and print the chunk count and the fingerprint.
  uv run python scripts/knowledge.py list [--doc-type TYPE]
      One line per chunk: id, size, metadata.
  uv run python scripts/knowledge.py show <chunk_id>
      One chunk's metadata and exact text.
  uv run python scripts/knowledge.py search "<query>" --top-k 5 [--doc-type T] [--topic T]
                                            [--milestone N] [--decision-id D-046] [--json]
      The ranked chunks for a query. --top-k is required (1 to knowledge.retrieval.max_top_k).
  uv run python scripts/knowledge.py eval [--no-write]
      Measure retrieval on the gold set (paths.knowledge_gold) and write
      reports/knowledge_eval.md (paths.knowledge_eval_report); --no-write prints only.

Exit code 0 on success; 1 when the corpus, the gold set or a query is invalid; 2 for invalid
usage.
"""

import argparse
import logging
import sys

from sitescout.config import Config, ConfigError, load_config
from sitescout.knowledge import KnowledgeError, SearchFilters, build_knowledge_index
from sitescout.knowledge.chunking import verify_corpus
from sitescout.knowledge.evaluation import IN_SCOPE, KS, run_evaluation
from sitescout.logging_setup import configure_logging

SNIPPET_CHARS = 200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify", help="build the corpus and check its invariants")
    listing = commands.add_parser("list", help="one line per chunk")
    listing.add_argument("--doc-type")
    show = commands.add_parser("show", help="one chunk's metadata and text")
    show.add_argument("chunk_id")
    search = commands.add_parser("search", help="rank the chunks for a query")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, required=True)
    search.add_argument("--doc-type")
    search.add_argument("--topic")
    search.add_argument("--milestone", type=int)
    search.add_argument("--decision-id")
    search.add_argument("--json", action="store_true", help="print the full result as JSON")
    evaluate = commands.add_parser("eval", help="measure retrieval on the gold set")
    evaluate.add_argument("--no-write", action="store_true", help="print, do not write the report")
    return parser


def _write(text: str) -> None:
    sys.stdout.write(text)


def main() -> int:
    args = build_parser().parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # chunk text holds Unicode (λ, Σ, ×, →)
    configure_logging()
    log = logging.getLogger("knowledge")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    try:
        return run(args, config, log)
    except KnowledgeError as error:
        log.error("%s", error)
        return 1


def run(args: argparse.Namespace, config: Config, log: logging.Logger) -> int:
    if args.command == "verify":
        corpus = verify_corpus(config.settings.knowledge, config.root)
        oversize = sum(chunk.oversize for chunk in corpus.chunks)
        documents = len({chunk.source_path for chunk in corpus.chunks})
        _write(f"Chunks: {len(corpus.chunks)} in {documents} documents ({oversize} oversize)\n")
        _write(f"Corpus fingerprint: {corpus.fingerprint}\n")
        _write(f"Retrieval fingerprint: {build_knowledge_index(config).retrieval_fingerprint}\n")
        return 0

    if args.command == "eval":
        evaluation = run_evaluation(config, write=not args.no_write)
        if args.no_write:
            _write(evaluation.report)
        pooled = evaluation.pooled
        _write(f"Gold queries: {len(evaluation.results)} ({pooled.n} in scope)\n")
        _write(f"In-scope categories: {', '.join(IN_SCOPE)}\n")
        _write(f"Corpus fingerprint: {evaluation.corpus_fingerprint}\n")
        _write(f"Gold set SHA-256: {evaluation.gold_sha256}\n")
        _write(f"Gold set review status: {evaluation.gold.review.status}\n")
        for k, hits, recall in zip(KS, pooled.hits, pooled.recall, strict=True):
            _write(f"  Hit@{k}: {hits} of {pooled.n}   Recall@{k}: {recall:.3f}\n")
        _write(f"  MRR: {pooled.mrr:.3f}\n")
        if not args.no_write:
            _write(f"Report: {config.settings.paths.knowledge_eval_report}\n")
        return 0

    index = build_knowledge_index(config)
    if args.command == "list":
        for chunk in index.corpus.chunks:
            if args.doc_type and chunk.doc_type != args.doc_type:
                continue
            flags = " oversize" if chunk.oversize else ""
            _write(
                f"{chunk.chunk_id:<62} {chunk.char_count:>5}  {chunk.doc_type:<13} "
                f"{chunk.topic:<13} m={chunk.milestone} {chunk.decision_id or '-'}{flags}\n"
            )
        return 0

    if args.command == "show":
        chunk = index.corpus.by_id.get(args.chunk_id)
        if chunk is None:
            log.error("No chunk %r; use `list` to see the ids", args.chunk_id)
            return 1
        for name, value in chunk.model_dump(exclude={"text"}).items():
            _write(f"{name}: {value}\n")
        _write("\n" + chunk.text + "\n")
        return 0

    filters = SearchFilters(
        doc_type=args.doc_type,
        topic=args.topic,
        milestone=args.milestone,
        decision_id=args.decision_id,
    )
    result = index.search(args.query, top_k=args.top_k, filters=filters)
    if args.json:
        _write(result.model_dump_json(indent=2) + "\n")
        return 0
    _write(f"{result.total_matches} chunks match; showing {len(result.hits)}\n")
    for hit in result.hits:
        snippet = " ".join(hit.chunk.text.split())[:SNIPPET_CHARS]
        _write(f"\n{hit.rank}. {hit.chunk.chunk_id}  score {hit.score:.3f}\n")
        _write(f"   {' > '.join(hit.chunk.section_path)}  [{', '.join(hit.matched_terms)}]\n")
        _write(f"   {snippet}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
