# Knowledge retrieval evaluation (Milestone 10, Phase 1)

**DRAFT GOLD SET, NOT YET REVIEWED: these measurements are not a gate.** The queries were written by the assistant from the indexed documents, before this measurement, and await review and additions by the project owner.

This report measures a **deterministic lexical knowledge retrieval layer**: Okapi BM25 over allow-listed project documents (D-056), checked against a hand-written gold set. It has no embeddings, no model and no network access, and it has no notion of meaning: a query finds only chunks that share words with it. It is a knowledge source only. Structured SiteScout facts (scores, ranks, network coverage) come from the deterministic tools, never from retrieval.

These are measurements only. No pass or fail gate has been defined for retrieval quality. Grounding and safety invariants, agent behaviour, and the answered, retry and fallback rates are different measurements, reported separately in later phases; nothing here is combined into one score.

## 1. Setup

| Item | Value |
|---|---|
| Corpus fingerprint (SHA-256) | `d0f3f8840a34cdb47d4a76616f1b5617088812a9d1aa3d17e5e34f9a5cccac82` |
| Documents | 7 |
| Chunks | 176 |
| Gold set file | `tests/knowledge_gold.yaml` |
| Gold set SHA-256 (newlines normalized) | `1fc60a636520a741d273ca76aaa39aea1ec4322affdf1772a17d4e0173adc46f` |
| Gold set review status | draft |
| Gold queries | 42 |
| Queries by category | paraphrase 11, terminology 6, definition 9, decision 10, out_of_scope 6 |
| Queries by author | assistant 36, user 6 |
| Chunker version | chunker-v1 |
| Tokenizer version | tokenizer-v1 |
| BM25 k1 | 1.2 |
| BM25 b | 0.75 |
| Retrieval fingerprint (SHA-256) | `b051a633996eab19e77beb3c8c6cf95770e65b196a78cd94b2739fdd7b72150f` |
| Largest top_k | 10 |

## 2. Corpus

| Document | doc_type | Chunks | Characters | Oversize chunks |
|---|---|---|---|---|
| `docs/scoring.md` | method | 9 | 6915 | 0 |
| `docs/features.md` | features | 21 | 17759 | 3 |
| `docs/decisions.md` | decision | 91 | 91220 | 15 |
| `docs/architecture.md` | architecture | 20 | 18264 | 1 |
| `docs/data_sources.md` | data_sources | 23 | 26447 | 2 |
| `docs/SPEC.md` | specification | 9 | 7871 | 0 |
| `README.md` | overview | 3 | 1585 | 0 |
| **All** |  | 176 | 170061 | 21 |

## 3. Retrieval quality (in-scope queries)

Hit@k counts the queries with at least one relevant chunk in the top k. Recall@k is the fraction of a query's relevant chunks found in the top k, averaged over queries. MRR is the mean of 1 divided by the rank of the first relevant chunk, and 0 when none is ranked. Vocabulary overlap is the mean share of a query's words that appear in its relevant chunks.

| Category | Queries | Hit@1 | Hit@3 | Hit@5 | Hit@10 |
|---|---|---|---|---|---|
| paraphrase | 11 | 3 of 11 | 7 of 11 | 7 of 11 | 8 of 11 |
| terminology | 6 | 2 of 6 | 2 of 6 | 6 of 6 | 6 of 6 |
| definition | 9 | 5 of 9 | 6 of 9 | 7 of 9 | 7 of 9 |
| decision | 10 | 5 of 10 | 7 of 10 | 9 of 10 | 10 of 10 |
| **all in-scope** | 36 | 15 of 36 | 22 of 36 | 29 of 36 | 31 of 36 |

| Category | Queries | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Vocabulary overlap |
|---|---|---|---|---|---|---|---|
| paraphrase | 11 | 0.136 | 0.303 | 0.333 | 0.455 | 0.451 | 0.57 |
| terminology | 6 | 0.167 | 0.250 | 0.694 | 0.917 | 0.492 | 1.00 |
| definition | 9 | 0.190 | 0.338 | 0.486 | 0.542 | 0.633 | 0.79 |
| decision | 10 | 0.350 | 0.550 | 0.750 | 0.900 | 0.661 | 0.62 |
| **all in-scope** | 36 | 0.214 | 0.372 | 0.547 | 0.677 | 0.562 | 0.71 |

## 4. Queries

| Query | Category | Author | First relevant rank | Relevant in top 10 | Top-1 chunk | Vocabulary overlap |
|---|---|---|---|---|---|---|
| P01 | paraphrase | assistant | 1 | 2 of 2 | `kb/scoring/method/1-percentile-points-d-040` | 0.56 |
| P02 | paraphrase | assistant | 28 | 0 of 2 | `kb/features/grid-evidence` | 0.50 |
| P03 | paraphrase | assistant | 25 | 0 of 2 | `kb/readme/what-it-answers` | 0.35 |
| P04 | paraphrase | assistant | 2 | 1 of 2 | `kb/decisions/d-029#1` | 0.58 |
| P05 | paraphrase | assistant | 2 | 1 of 2 | `kb/features/intro` | 0.71 |
| P06 | paraphrase | assistant | 3 | 1 of 2 | `kb/decisions/d-034#1` | 0.59 |
| P07 | paraphrase | assistant | 20 | 0 of 2 | `kb/decisions/d-055#2` | 0.53 |
| P08 | paraphrase | assistant | 1 | 1 of 2 | `kb/features/grid-evidence/grid-completeness-ratio` | 0.71 |
| P09 | paraphrase | assistant | 6 | 1 of 2 | `kb/readme/what-it-answers` | 0.53 |
| P10 | paraphrase | assistant | 1 | 1 of 2 | `kb/decisions/d-044#1` | 0.57 |
| T01 | terminology | assistant | 4 | 3 of 3 | `kb/data-sources/derived-layers/network` | 1.00 |
| T02 | terminology | assistant | 1 | 2 of 2 | `kb/decisions/d-040#1` | 1.00 |
| T03 | terminology | assistant | 4 | 2 of 2 | `kb/architecture/milestone-6-network-optimization` | 1.00 |
| T04 | terminology | assistant | 5 | 2 of 2 | `kb/features/host-commercial/poi-1km-poi-3km#2` | 1.00 |
| T05 | terminology | assistant | 4 | 1 of 2 | `kb/decisions/d-007` | 1.00 |
| T06 | terminology | assistant | 1 | 2 of 2 | `kb/features/grid-evidence/grid-completeness-ratio` | 1.00 |
| D01 | definition | assistant | 11 | 0 of 2 | `kb/decisions/d-047#1` | 0.60 |
| D02 | definition | assistant | 1 | 1 of 2 | `kb/readme/what-it-does-not-claim` | 0.86 |
| D03 | definition | assistant | 3 | 2 of 2 | `kb/readme/what-it-does-not-claim` | 0.83 |
| D04 | definition | assistant | 5 | 1 of 2 | `kb/scoring/outputs` | 0.80 |
| D05 | definition | assistant | 1 | 3 of 3 | `kb/spec/8-network-optimization-mclp#2` | 1.00 |
| D06 | definition | assistant | 13 | 0 of 2 | `kb/scoring/known-limitations` | 0.67 |
| M01 | decision | assistant | 1 | 1 of 2 | `kb/decisions/d-046#1` | 0.73 |
| M02 | decision | assistant | 9 | 1 of 1 | `kb/decisions/d-017#1` | 0.27 |
| M03 | decision | assistant | 1 | 2 of 2 | `kb/decisions/d-034#2` | 0.71 |
| M04 | decision | assistant | 1 | 1 of 1 | `kb/spec/3-candidate-generation` | 0.56 |
| M05 | decision | assistant | 1 | 2 of 2 | `kb/decisions/d-047#1` | 0.83 |
| M06 | decision | assistant | 1 | 1 of 1 | `kb/decisions/d-053` | 0.73 |
| M07 | decision | assistant | 4 | 2 of 2 | `kb/features/production-and-backtest` | 0.69 |
| M08 | decision | assistant | 2 | 1 of 1 | `kb/decisions/d-049#2` | 0.56 |
| P11 | paraphrase | user | 3 | 3 of 3 | `kb/decisions/d-044#1` | 0.67 |
| D07 | definition | user | 1 | 5 of 8 | `kb/readme/what-it-does-not-claim` | 0.91 |
| D08 | definition | user | 1 | 1 of 2 | `kb/readme/what-it-does-not-claim` | 0.67 |
| M09 | decision | user | 2 | 2 of 4 | `kb/architecture/milestone-6-network-optimization` | 0.60 |
| D09 | definition | user | 1 | 3 of 4 | `kb/scoring/confidence-spec-6-d-041` | 0.82 |
| M10 | decision | user | 4 | 1 of 1 | `kb/spec/9-evidence-and-reports` | 0.50 |

## 5. Misses

5 in-scope queries with no relevant chunk in the top 10:

- **P02**, paraphrase: 'What score does a site get for the power-infrastructure part when nothing is mapped nearby?'. `kb/scoring/method/2-components-spec-5-d-042` (rank 34), `kb/decisions/d-042#1` (rank 28)
- **P03**, paraphrase: "Why can't two chosen locations be placed very close together in the recommended set of 30?". `kb/spec/8-network-optimization-mclp#1` (rank 42), `kb/decisions/d-046#1` (rank 25)
- **P07**, paraphrase: 'How does SiteScout express how sure it is about a site, and can that be shown as a percent?'. `kb/scoring/confidence-spec-6-d-041` (rank 47), `kb/spec/6-confidence` (rank 20)
- **D01**, definition: 'What is a universal unknown?'. `kb/spec/6-confidence` (rank 11), `kb/scoring/confidence-spec-6-d-041` (rank 19)
- **D06**, definition: 'Which kinds of places can host a charging site?'. `kb/spec/3-candidate-generation` (rank 26), `kb/decisions/d-027` (rank 13)

## 6. Out-of-scope queries

| Query | Text | Chunks matching | Top-1 score | Top-1 chunk |
|---|---|---|---|---|
| O01 | What is the capital city of Kenya? | 174 | 9.774 | `kb/features/access/dist-kigali-cbd-m-reported-only` |
| O02 | How much monthly revenue would a fast charger in Kigali generate? | 168 | 11.490 | `kb/scoring/known-limitations` |
| O03 | Which brand of charger hardware should be installed at a fuel station? | 166 | 12.053 | `kb/readme/what-it-answers` |
| O04 | How do I deploy this application to a cloud provider? | 160 | 8.496 | `kb/spec/8-network-optimization-mclp#1` |
| O05 | What is the current electricity tariff per kilowatt-hour in Rwanda? | 176 | 9.838 | `kb/data-sources/sources/energydata-info-transmission-network` |
| O06 | How many electric cars are registered in Rwanda today? | 155 | 7.722 | `kb/decisions/d-029#1` |

0 of 6 out-of-scope queries matched no chunk at all; 6 shared a word with at least one chunk and returned it.
Top-1 scores: out-of-scope 7.722 to 12.053; in-scope 4.300 to 25.191 (median 12.375). The ranges overlap: a score cut-off alone would not separate them.
BM25 scores depend on the query's length and on how rare its words are, so they are not comparable across queries; this is a diagnostic, and no threshold is chosen.

## 7. How to read this report

- The gold set is small. One query moves a category's Hit@k by about 17 points for 6 queries, about 11 points for 9 queries, about 10 points for 10 queries, about 9 points for 11 queries, so differences of a query or two are not meaningful.
- Relevance is binary and judged by the phrase anchored in each chunk. A relevant chunk other than the listed ones may also answer a query; it is counted as not relevant.
- The queries were written from the indexed documents. Vocabulary overlap shows how much of each query's wording appears in its relevant chunks: a query with low overlap cannot be found by word matching.
- Nothing was tuned on these results. BM25 parameters, the tokenizer and the chunking were fixed before the first run; a later change needs a recorded decision and a fresh review of the gold set.
