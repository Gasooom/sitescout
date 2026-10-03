# SiteScout: an engineering case study

*An independent project built on public data. Figures come from [reports/evaluation.md](../reports/evaluation.md) and the committed export; design decisions are in [decisions.md](decisions.md).*

## Problem

**Where should an EV charging company expand next in Rwanda?** SiteScout proposes 30 sites, explains each one with typed evidence, states what is still unknown, and says what to investigate next.

## Why ranking alone is not enough

The obvious method is to score every location and take the top 30. But high scores cluster: the 30 best-scoring sites put 23 sites in the City of Kigali, so the network serves the same people many times. A charging network is judged as a whole, so SiteScout selects the 30 sites **jointly**, as a Maximum Coverage Location Problem (MCLP): maximize the modelled population within 10 km of a selected site, subject to eligibility (score in the top half, with a host) and at least 2 km between sites, solved exactly with PuLP and CBC.

## Architecture

I did not start with a language model. The decision system came first, and each stage validates its schema before the next one runs:

```
Public data -> Ingestion -> Features -> Candidates -> Scoring -> Optimization -> Evidence -> Evaluation -> AI investigation
```

1. **Ingestion:** OpenStreetMap, WorldPop 2025 and geoBoundaries into validated GeoParquet; distances in EPSG:32735, never in degrees.
2. **Candidates:** 300, generated in code from real host locations (fuel stations, malls, supermarkets, hotels, logistics and industrial sites) and road-corridor points; none placed by hand.
3. **Scoring:** demand, access, host activity, charging gap and grid evidence as percentile points, weighted by an urban or a corridor profile; missing data never raises a score.
4. **Optimization:** the exact MCLP, with a greedy solution and the Top-30 by score always reported next to it.
5. **Evidence:** every value is a record typed RETRIEVED_FACT, CALCULATED, INFERRED or UNKNOWN, with its source; briefs with evidence, unknowns and next actions are filled from templates for the 30 selected sites; the other 270 candidates have scores and a confidence level only.
6. **Evaluation:** a retrospective plausibility test, weight stability, the network comparison, data-quality checks and a grounding check.
7. **AI investigation:** added last, as a read-only layer over the tools above.

The rule behind the last step: **AI explains the decision; it does not make the decision.** Deterministic Python computes every score and the network selection (the parameters are documented design choices in [decisions.md](decisions.md)); the page renders the export and computes nothing.

## Key result

| | Optimized (exact MCLP) | Greedy | Top-30 by score |
|---|---|---|---|
| Modelled population within 10 km | **49.8%** | 49.2% | 24.0% |
| Provinces with a site | 5 | 5 | 4 |
| Districts with a site | 22 | 23 | 8 |
| Sites in City of Kigali | 3 | 4 | 23 |
| Mean site score | 64.6 | 65.3 | 73.8 |

The coverage figures are modelled population within the service radius under the stated assumptions, not people who will use a charger. The trade-off is explicit: the optimized network accepts a lower mean site score (64.6 against 73.8) for individually weaker sites that reach people no other site reaches. CBC reports the MCLP solution optimal; the greedy solution comes within 0.64% of its objective.

## Reliability: how SiteScout avoids making things up

- **Grounded briefs.** 2262 of 2262 numbers in the 30 briefs trace to structured evidence, checked automatically.
- **A validated agent.** The agent can call eight read-only deterministic tools and search an index of the project's own documents. Before anything is shown, a deterministic validator checks every statement: each number must be copied from a record that statement cites, each documentation claim must quote its source verbatim, candidate ids must come from fetched records, UNKNOWN stays unknown, comparative and evaluative words are refused, and any mention of the grid carries the exact disclaimer. A rejected answer gets one retry; if that fails too, the page shows the evidence the run retrieved and no generated text.
- **Contextual, not a chatbot.** The page sends a fixed investigation type and a site id; the server writes the question. The key stays on the local server, and nothing an investigation returns is saved.
- **Tested.** 73 scripted agent cases run offline ([reports/agent_eval.md](../reports/agent_eval.md)); a demo-readiness gate ran the page's own path live ([reports/demo_gate.md](../reports/demo_gate.md)).

## An engineering failure and its fix

The network-comparison investigation, the centre of the demo, sometimes ended with no answer. The model wrote plausible answers that SiteScout's validator rejected, and the single retry did not repair them (D-064).

Reproducing it live and reading the rejected attempts showed three causes:

- **Number-like language.** A statement saying selected sites cannot be closer than 2 km to "one another" was rejected: the validator treats "one" as a number word wherever it appears. The retry advice said to write digits, which cannot fix an idiom, so the model sent it back unchanged.
- **Quotations losing formatting.** The model quoted `Objective: maximize …` where the document reads `**Objective:** maximize …`. The validator requires an exact quotation, and correctly refused it.
- **Unhelpful retry feedback.** The rejection named the statement but not which of its quotations failed, so the retry repeated the same one.

The fix was **not** to relax the validator. The prompt now names every word the validator rejects (a test keeps the list identical to the validator's), and the retry note explains each rule that failed, names each quotation it could not find, and shows the exact text of the cited document when the quotation differs only in Markdown, case or spacing. The retried quotation still has to be verbatim. The same demo also exposed a transport bug: on Windows loopback, the local server's close-after-write sometimes lost the tail of the 0.6 MB export, so the page loaded without data. Letting the client close the connection (HTTP/1.1) fixed it: on a plain Python server, 38 of 60 large replies arrived whole under HTTP/1.0 and 60 of 60 under HTTP/1.1, and SiteScout's server then delivered 80 of 80.

Result, measured live on 2026-09-28: the network comparison validated in 3 of 3 runs after the fix, then at the first attempt in the final end-to-end check. That is a few runs on one day, not a reliability rate.

## What the system does not know

For every site: grid connection capacity, transformer capacity, land availability, landowner willingness and permit requirements. SiteScout reports grid evidence from public maps. Actual grid connection feasibility requires utility confirmation. It makes no claim about revenue, commercial viability or how many people would actually charge at a site.

## Limitations

- **The evaluation is weak by necessity.** The public OpenStreetMap data used maps only 5 charging sites, so the backtest cannot distinguish SiteScout from population alone (the 95% interval of the difference includes zero). With those sites removed from every feature, SiteScout's top 30 holds 4 of the 5 candidates near them (Precision@30 0.133, against 0.067 for population alone and 0.017 for random; the interval of the difference is [0.000, 0.167]). This is a retrospective plausibility test, not proof that the ranking is right.
- **The ranking is stable** under ±20% changes to each weight (mean Top-30 overlap 0.98, minimum 0.87, target 0.70), which says the result is not an artifact of one weight, not that the weights are right.
- **Public data has gaps.** Mapping density varies by district (the grid-mapping proxy ranges from 0.12 to 6.62 times the national median), and the 128 road-corridor candidates have no host.
- **Modelled assumptions.** Demand is modelled population (WorldPop) within 10 km; traffic, vehicle ownership and trip patterns are not modelled. Changing the radius to 5 or 15 km changes coverage to 26.3% or 68.9%.
- **The agent is observed, not certified.** Its live runs are few, and the model is not deterministic between runs.

## What I would build next

1. A reviewed manual list of existing chargers, which would make the backtest meaningful and the charging-gap feature sharper.
2. Traffic or trip data as a demand layer next to population.
3. A site-by-site checklist export for field verification of the unknowns above.
