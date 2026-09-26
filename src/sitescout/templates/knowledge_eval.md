# Knowledge retrieval evaluation (Milestone 10, Phase 1)

{banner}

This report measures a **deterministic lexical knowledge retrieval layer**: Okapi BM25 over allow-listed project documents (D-056), checked against a hand-written gold set. It has no embeddings, no model and no network access, and it has no notion of meaning: a query finds only chunks that share words with it. It is a knowledge source only. Structured SiteScout facts (scores, ranks, network coverage) come from the deterministic tools, never from retrieval.

These are measurements only. No pass or fail gate has been defined for retrieval quality. Grounding and safety invariants, agent behaviour, and the answered, retry and fallback rates are different measurements, reported separately in later phases; nothing here is combined into one score.

## 1. Setup

{setup}

## 2. Corpus

{corpus}

## 3. Retrieval quality (in-scope queries)

Hit@k counts the queries with at least one relevant chunk in the top k. Recall@k is the fraction of a query's relevant chunks found in the top k, averaged over queries. MRR is the mean of 1 divided by the rank of the first relevant chunk, and 0 when none is ranked. Vocabulary overlap is the mean share of a query's words that appear in its relevant chunks.

{hits}

{recall}

## 4. Queries

{queries}

## 5. Misses

{misses}

## 6. Out-of-scope queries

{out_of_scope}

## 7. How to read this report

{caveats}
