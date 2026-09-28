# Two-minute demo script

Two parts: the **static demo** anyone can open from the repository, and the **local investigation**, which needs the pipeline outputs and a provider key ([README](../README.md#run-the-demo)). No recorded model output is committed; the investigation part is always shown live.

## Part 1: the decision (about 90 seconds)

Open `app/index.html` from disk.

1. **The question (10 s).** "SiteScout answers one question: where should an EV charging company put its next 30 sites in Rwanda? Everything here comes from public data."
2. **The headline (20 s).** Point at the two figures. "Taking the 30 best-scoring sites covers 24.0% of the modelled population within 10 km. Choosing 30 sites together, as an exact maximum-coverage problem, covers 49.8%. Same budget, about twice the reach."
3. **The map (20 s).** Toggle *Optimized network*, *Greedy network*, *Top-30 by score*. "The Top-30 piles into Kigali: 23 of 30 sites. The optimized network reaches 22 districts. Greedy is within 0.64% of the exact answer."
4. **One site (30 s).** Click the rank-2 site (Fuel station, Musanze). "Score, rank and confidence; why it was selected, its unique contribution to the network, and every piece of evidence typed as retrieved, calculated, inferred or unknown, with the grid evidence stated as evidence, not approval. And what we don't know: grid capacity, land, permits."
5. **Trust (10 s).** Scroll to the footer. "The ranking is checked with a retrospective plausibility test against random and population-only baselines, and it reports honestly that 5 known chargers are too few to separate them."

## Part 2: the investigation (about 30 seconds)

Run `uv run --env-file .env python scripts/serve.py` beforehand and open http://127.0.0.1:8765/#cand-3a8fa00f876a.

1. **Ask (10 s).** Under *Unknown*, click *What would we need to verify?* "The page sends a fixed investigation type and the site id, never free text. The server writes the question; the key never reaches the browser."
2. **Read the answer (15 s).** "Each statement is typed and cites the records it came from; open a citation to see the value and its source. *How this was investigated* lists the tools the agent actually called and whether the validator accepted the first draft."
3. **The boundary (5 s).** "If no answer passes validation, the page shows the retrieved evidence instead of generated text. The decision above never changes."

## If the provider is unavailable

Say so and show the static demo: the investigation block reads "AI investigation unavailable in this view", and the decision view is complete without it.
