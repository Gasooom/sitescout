# SiteScout

**EV charging network intelligence for Rwanda.** An independent portfolio project built on public data.

SiteScout proposes a network of 30 charging sites, chosen together rather than one at a time, and explains each site with evidence, unknowns and next actions.

> **Status:** Milestones 1 (data ingestion) and 2 (candidate generation: 300 candidates selected from 595 eligible ones) are complete. Milestone 3 (feature engineering: raw demand, access, host, charging-gap and grid-evidence features, [docs/features.md](docs/features.md)) is complete. Milestone 4 (scoring and confidence, [docs/scoring.md](docs/scoring.md)) is complete. Milestone 5 (evaluation) is implemented and in review: a retrospective plausibility test against the known public chargers, with random and population-only baselines and weight stability ([reports/evaluation.md](reports/evaluation.md)).

## What it answers

1. Which locations deserve investigation?
2. Why is each one attractive, and what evidence supports that?
3. What is still unknown?
4. Which 30 locations form the best network when chosen together?
5. What should be investigated next?

## What it does not claim

SiteScout never claims grid approval, transformer capacity, land availability, permit approval, owner willingness, revenue or business viability, and its ranking is not ground truth. It reports **grid evidence** from public maps, never a grid connection decision.

Every site brief carries these two statements:

> Actual grid connection feasibility requires utility confirmation.

> This analysis uses public data. It does not establish grid approval, land availability, permitting approval, or commercial viability.

Some things stay unknown for every site and are listed in every output: grid connection capacity, transformer capacity, land availability, landowner willingness and permit requirements.

Evaluation results are reported as a **retrospective plausibility test**.

## Independence and data

- SiteScout is an independent project and is not affiliated with any company. Sites carry generic labels such as "Fuel station, Remera, Gasabo", never a business or brand name.
- It uses public data only, such as OpenStreetMap, WorldPop and geoBoundaries, and never private or company operational data.
- Synthetic data, where used, is labelled synthetic.
- No datasets are stored in this repository. [docs/data_sources.md](docs/data_sources.md) lists every source, its licence and how to obtain it.

## How it works (planned)

1. **Ingest** public data into GeoParquet (Milestone 1).
2. **Generate 200 to 400 candidates** in code from real host sites (fuel stations, malls, supermarkets, hotels, logistics and industrial sites) and from points along trunk and primary roads (Milestone 2).
3. **Compute features** for demand, access, host activity, charging gap and grid evidence (Milestone 3).
4. **Score** each candidate with an urban or corridor profile and give it a High, Medium or Low confidence level (Milestone 4).
5. **Evaluate** the ranking against random and population-only baselines (Milestone 5).
6. **Select the network** of 30 sites exactly, as a maximum coverage location problem, and compare it with a greedy selection and the Top-30 by score (Milestone 6).
7. **Write site briefs** in which every number traces back to structured data (Milestone 7).
8. **Export** one JSON file that the front end displays without computing anything (Milestone 8).

The method is specified in [docs/SPEC.md](docs/SPEC.md). The architecture is in [docs/architecture.md](docs/architecture.md), and design decisions and open questions are in [docs/decisions.md](docs/decisions.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/), which installs Python 3.12 if needed.

```bash
uv sync
uv run python scripts/check_config.py
uv run python scripts/ingest.py all
uv run python scripts/candidates.py
uv run python scripts/features.py
uv run python scripts/score.py
uv run python scripts/evaluate.py
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

`check_config.py` validates `config/settings.yaml` and `config/weights.yaml` and lists every parameter that is still pending because the specification does not define it. `ingest.py all` downloads the public sources into `data/raw/`, builds the validated layers in `data/processed/` and checks them again; see [docs/data_sources.md](docs/data_sources.md).

## Repository layout

```text
config/    settings.yaml and weights.yaml: every parameter, taken from docs/SPEC.md
src/       the sitescout package, where all computation lives
scripts/   entry points that parse arguments and call src/
tests/     pytest suite
docs/      specification, architecture, decisions, data sources, features and scoring
data/      local data, never committed (raw, manual, processed, export)
```

## Milestones

| # | Milestone | Status |
|---|---|---|
| 0 | Repository setup and architecture | done |
| 1 | Data ingestion and geospatial pipeline | done |
| 2 | Candidate generation | done |
| 3 | Feature engineering | done |
| 4 | Scoring and confidence | done |
| 5 | Evaluation | in review |
| 6 | Network optimization | not started |
| 7 | Evidence and reports | not started |
| 8 | Export and front end | not started |
| 9 | Stretch (optional) | not started |
| 10 | Packaging and demo | not started |

## Data credits

- Map data © OpenStreetMap contributors, available under the [Open Database License](https://www.openstreetmap.org/copyright).
- Population: WorldPop, University of Southampton (2025), constrained estimates R2025A v1, DOI 10.5258/SOTON/WP00839, [CC BY 4.0](https://hub.worldpop.org/data/licence.txt).
- Boundaries: geoBoundaries (Runfola et al. 2020), gbOpen Rwanda ADM2 and ADM1, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Transmission network (grid-evidence cross-check): World Bank Group via energydata.info, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Licence

Code and documentation: [MIT](LICENSE). Data sources keep their own licences and credits; see [docs/data_sources.md](docs/data_sources.md).
