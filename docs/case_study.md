# Case study: choosing 30 charging sites for Rwanda as a network

*An independent portfolio project built on public data. All figures come from [reports/evaluation.md](../reports/evaluation.md) and the committed export.*

## The problem

An EV charging company expanding in Rwanda has to decide where its next 30 sites go. The obvious approach, score every location and take the top 30, has a hidden flaw: the best-scoring locations sit next to each other, so the network serves the same people many times.

## The approach

SiteScout builds 300 candidates from real host locations and road corridors, scores each one on demand, access, host activity, charging gap and grid evidence from public data, and then selects the 30 sites **together**, as an exact Maximum Coverage Location Problem: the 30 sites that bring the most modelled population within 10 km of a charger.

## The result

| | Exact network | Greedy | Top-30 by score |
|---|---|---|---|
| Modelled population within 10 km | **49.8%** | 49.2% | 24.0% |
| Districts with a site | 22 | 23 | 8 |
| Provinces with a site | 5 | 5 | 4 |
| Sites in City of Kigali | 3 | 4 | 23 |
| Mean site score | 64.6 | 65.3 | 73.8 |

Choosing sites together roughly doubles the population within reach of the network. The Top-30 by score puts 23 of its 30 sites in the City of Kigali; the exact network spreads across 22 districts and all 5 provinces. The price is a lower mean site score, 64.6 against 73.8: individually weaker sites that serve people nobody else reaches. A greedy heuristic comes within 0.64% of the exact objective.

## How far to trust it

- **The ranking is plausible, not proven.** In a backtest with existing chargers removed, SiteScout's top 30 holds 4 of the 5 candidates near known charging sites (Precision@30 0.133 against 0.067 for population alone and 0.017 for random). But the 95% interval of the difference, [0.000, 0.167], includes 0: with 5 known charging sites the data cannot tell SiteScout apart from population alone.
- **The ranking is stable.** Changing any single weight by ±20% keeps on average 98% of the Top-30, and never less than 87% (target 70%).
- **Every number is grounded.** 2262 of 2262 numbers in the 30 site briefs trace to structured evidence.
- **What it cannot know.** Grid connection capacity, transformer capacity, land availability, landowner willingness and permit requirements are unknown for every site. SiteScout reports grid evidence from public maps. Actual grid connection feasibility requires utility confirmation.

## The investigation layer

A decision-maker will ask *why*. SiteScout's agent answers on the decision page, next to the site it explains, by calling the same deterministic tools and searching the project's method documents. Every statement is checked against the records the run actually fetched; an answer that fails twice is replaced by the evidence itself. Before the layer was shown, a readiness gate ran each investigation kind twice with the real provider: all 6 runs ended validated, 4 of them after the validator rejected a first draft and the agent corrected it ([reports/demo_gate.md](../reports/demo_gate.md)). That is a readiness check on one day, not a reliability claim.

## What this demonstrates

A decision system in which the numbers and the selection are deterministic and reproducible, the uncertainty is explicit, and a language model is integrated as an accountable analyst: constrained to tools, grounded in citations, bounded by budgets, validated, and replaced by evidence when it cannot be trusted.
