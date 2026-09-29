# Demo script (about 7 minutes)

**Before you start.** Run `uv run --env-file .env python scripts/serve.py` and open http://127.0.0.1:8765/. The investigation needs the pipeline outputs in `data/processed/` and a provider key ([README](../README.md#run-the-demo)); everything else also works from `app/index.html` opened from disk. Run the network investigation once beforehand: it takes under a minute. No model output is recorded or committed; the investigation is always shown live.

## 00:00–00:45 · Problem

"An EV charging company expanding in Rwanda has to decide where its next 30 sites go. SiteScout answers that from public data only: OpenStreetMap, WorldPop population and geoBoundaries. It proposes 30 sites chosen as a network, explains each one, says what is still unknown, and says what to check next. It is an independent project; no company data is used."

## 00:45–01:30 · Architecture

Show the diagram in the README.

"I didn't start with a language model. First the data pipeline: ingestion with schema checks, geospatial features in a metric projection, 300 candidates generated in code from real host locations, deterministic scoring, and an exact optimization. Then evidence and evaluation. The AI came last, as a read-only layer. The rule: AI explains the decision; it does not make it. The page you'll see renders one exported file and computes nothing."

## 01:30–03:00 · Map and the optimized network

On the network workspace, with *Optimized network* selected in the network switch above the map.

- "Each dot is one of the 300 candidates; the dark blue points are the 30 selected sites, with their 10 km service radius shaded."
- "They were selected together as a maximum coverage problem: the 30 sites that bring the most modelled population within 10 km, at least 2 km apart, among sites in the top half by score that have a host. CBC solves it exactly and reports the solution optimal."
- Point at 49.8%: "That is modelled population within 10 km under these assumptions, not people who will charge there."

## 03:00–04:00 · Top-30 versus optimized

Click *Top-30 by score*, then *Greedy network*, then back to *Optimized network*. Scroll to the *Network* section: the comparison table and its *Observation* show the same trade-off side by side.

- "The obvious method, taking the 30 best-scoring sites, piles 23 of them into the City of Kigali and reaches 24.0%. The optimized network reaches 49.8% across 22 districts and all 5 provinces."
- "The trade-off is shown, not hidden: mean site score 64.6 against 73.8. The network accepts individually weaker sites because they serve people nobody else reaches."
- "Greedy is the heuristic baseline: within 0.64% of the exact objective, so the exact solver is a check more than a large gain."

## 04:00–05:00 · Site evidence

Open the rank-2 site, Fuel station, Musanze (`#cand-3a8fa00f876a`).

- "Score, rank of 300 and a confidence level, never a percentage. Under *Network role*, its place in each of the three selections, and how much coverage the network would lose without it."
- "*Evidence* shows one key value per group; *View all evidence* opens the full table. Every value says how SiteScout knows it: retrieved fact, calculated, inferred or unknown. Grid evidence means mapped infrastructure nearby, not a connection decision."
- Scroll to *What we don't know yet* (each item marked VERIFY) and *Next checks*: "Grid capacity, land, landowner willingness and permits are unknown for every site. SiteScout says so rather than guessing, and turns them into next steps."

## 05:00–06:00 · AI-assisted investigation

Back to the *Network* section. Under *Network investigation*, click **Investigate network difference**.

- "There is no chat box. The page sends a fixed investigation type; the server writes the question: how does the optimized network differ from the Top-30 by score, and why?"
- When the report appears: "It opens with the key finding. Each statement carries a provenance label and cites the records it came from; open one to see the value and its source. Documentation claims quote the document verbatim."
- Open *Investigation record*: "These are the tools the agent actually called, and whether the validator accepted the first draft. If no answer passes validation after one retry, the page shows the retrieved evidence instead, and the decision above never changes."

## 06:00–07:00 · Reliability and limitations

- "Every number in the 30 briefs is checked against structured data: 2262 of 2262."
- "A debugging story: the network investigation used to fail validation. The model wrote 'one another', which the validator counts as a number word, and quoted a document without its Markdown. I didn't loosen the validator; I made its feedback precise: which rule, which quotation, and the exact text it should have quoted. After the fix it validated 3 of 3 live runs."
- "The honest limit: only 5 known charging sites exist in public data. In a backtest SiteScout's top 30 finds 4 of the 5 candidates near them, but the interval against population alone includes zero. It's a retrospective plausibility test, not proof. The ranking is stable under ±20% weight changes."

## If the provider is unavailable

Say so and continue. The investigation block reads "Investigation unavailable in this view. The SiteScout decision above is complete without it.", and every other step works as shown.
