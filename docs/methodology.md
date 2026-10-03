# Methodology

SiteScout answers one question: **where should an EV charging company expand next in Rwanda?** It proposes 30 sites chosen together as a network, and explains each one with evidence, unknowns and next actions. The full specification is [SPEC.md](SPEC.md); every design choice is recorded in [decisions.md](decisions.md). This page is the short version.

## 1. Public data only

OpenStreetMap (roads, points of interest, fuel stations, mapped power infrastructure and charging stations), WorldPop 2025 population, geoBoundaries districts and a World Bank transmission network used as a cross-check. Every source, licence and retrieval date is in [data_sources.md](data_sources.md). No private or company data is used, and synthetic data appears only in tests, labelled as such.

## 2. Candidates

Candidates come from real host locations (fuel stations, malls, supermarkets, hotels, logistics and industrial sites) and from points along trunk and primary roads, never placed by hand. The rules give 595 eligible locations; a candidate budget keeps 300 (D-031).

## 3. Features and score

Each candidate gets features for demand (modelled population within 1, 5 and 10 km), access, host activity, charging gap and grid evidence ([features.md](features.md)). Features become percentile points, skewed counts through `log1p`, never min-max. Two profiles, urban and corridor, weight five components into a score from 0 to 100 ([scoring.md](scoring.md)). Missing data never raises a score.

**Grid evidence** means mapped infrastructure nearby, never a grid connection decision. Actual grid connection feasibility requires utility confirmation.

## 4. Confidence

A level, High, Medium or Low, never a percentage, built only from factors that differ between sites: missing grid evidence, sparse public mapping in the district, no identified host, and a remote location with few points to cross-check. Grid connection capacity, transformer capacity, land availability, landowner willingness and permit requirements stay unknown for every site and are listed in every output.

## 5. Retrospective plausibility test

The ranking is checked in backtest mode, with every existing charger removed from every feature, against random (1,000 seeds) and population-only baselines with bootstrap intervals, and for stability under ±20% changes to each weight ([reports/evaluation.md](../reports/evaluation.md)). With only 5 charging sites mapped in the OpenStreetMap data, the test is weak, and the report says so.

## 6. The network: an exact maximum coverage problem

Scoring sites one by one favours a cluster of similar sites. SiteScout instead selects 30 sites **jointly** as a Maximum Coverage Location Problem, solved exactly with PuLP and CBC: maximize the modelled population (normalized to 1) within 10 km of a selected site, with a small score term (λ = 0.01), among eligible sites (score in the top half, with a host), at least 2 km apart. A greedy solution and the Top-30 by score are always reported alongside it.

## 7. Evidence and briefs

Every value a brief shows is an evidence record typed RETRIEVED_FACT, CALCULATED, INFERRED or UNKNOWN, with its source. The 30 briefs are filled from templates, and an automated check confirms every number in them traces to structured data. Briefs with evidence, unknowns and next actions exist for the 30 selected sites; the other 270 candidates have scores and a confidence level only.

## 8. The investigation layer

Scores and network selection are computed by deterministic Python; the parameters (weights, radius, candidate rules) are documented design choices in [decisions.md](decisions.md). On top of it sits an agent that can question the results, never change them:

- **Tools, not free generation.** It can call eight read-only deterministic tools (site records, score explanations, comparisons, network contribution and summary, nearby sites, briefs) and search a small, allow-listed index of the project documents (BM25, no embeddings). The index never holds site data.
- **Grounding.** Every statement must cite records the run fetched. Numbers must be copied from those records, knowledge claims must quote the cited document verbatim, candidate ids must come from fetched records, and UNKNOWN never becomes a fact. One retry is allowed, with a note that explains each rule broken and names each quotation that was not found verbatim (D-064); otherwise the answer is replaced by the evidence the run gathered, with no generated text.
- **Budgets.** Iterations, tool calls, searches and recoverable errors are capped in configuration; the model never sees the limits.
- **Evaluation.** 73 scripted trajectories test the validator and the loop offline ([reports/agent_eval.md](../reports/agent_eval.md)); one bounded live run is reported as an observation, not a reliability claim ([reports/agent_live_eval.md](../reports/agent_live_eval.md)); and a demo-readiness gate checked the page's own path before the demo ([reports/demo_gate.md](../reports/demo_gate.md)).

On the decision page the investigation is a separate, labelled layer, served locally with the key kept on the server, and nothing it returns is saved (D-062).
