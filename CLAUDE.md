# SiteScout — Claude Code Rules

Read this file at the start of every session. The full method specification is in `docs/SPEC.md`; read it before any milestone that touches data, scoring, evaluation or optimization.

## Project identity

SiteScout is an independent portfolio project for EV charging network intelligence in Rwanda. It proposes the best network of 30 charging sites, selected jointly, and explains each site with evidence, unknowns and next actions.

- Never imply affiliation with any company. No company logos or colours in the product or repo.
- No company names in the product, exports, briefs or committed project data: no charging operator names, host business names or commercial brands. Allowed: public data-source names, software and tool names, attribution and licence information a source requires, and company names that appear only as specification text in `CLAUDE.md` or `docs/SPEC.md`.
- Candidates and the UI use generic host labels such as "Fuel station, Remera, Gasabo", plus the OSM ID. Never remove attribution that a licence requires.
- Public data only. Never fabricate company operational data or claim access to private data.
- Synthetic data is allowed only where labeled synthetic in code, UI and docs.

## Core engineering principles

- No machine learning without a legitimate labeled modeling problem. No fake ML.
- Deterministic code makes every numerical decision. LLMs never produce numbers.
- Every important factual claim is grounded in evidence.
- Unknown information stays unknown. Missing data never raises a score or a confidence level.
- Use "grid evidence", never "grid feasibility". The only exception is the exact disclaimer sentence: "Actual grid connection feasibility requires utility confirmation."
- Never claim grid approval, transformer capacity, land availability, permit approval, owner willingness, revenue, or business viability.
- Evaluation is a "retrospective plausibility test", never "accuracy".

## Environment and repository

- Python **3.12** (not 3.11: rasterio and pyproj lack Windows wheels for 3.11). Managed with uv.
- Branch `main`.
- `.gitattributes` with `* text=auto eol=lf` from the first commit.
- The GitHub repo is **public** during development. Do not make it private.
- All of `data/` (raw, processed, manual and generated) and `.venv/` are gitignored. The repository holds the schemas and documentation needed to reproduce the pipeline, not the datasets.
- One explicit exception (Milestone 8, D-048): the small demo export `data/export/sitescout.json` and `data/export/sitescout.js`, written only by `scripts/export.py`, is committed so the demo runs from the public repository. It is the only exception to the generated-datasets rule below: it must contain no raw data, secrets, personal data or business names, and every other rule still applies. Nothing else under `data/` is committed.
- Never commit a file larger than 5 MB.

## Git authorship and milestone delivery

All Git commits must represent Gasim as the author.

- Git author name: `Gasooom`
- Git author email: `aboelgasimibrahim999@gmail.com`
- Claude Code may execute Git commands, but must never change the configured Git author identity.
- Never configure Claude, Anthropic, OpenAI, or any AI system as the Git author.
- Never add a `Co-authored-by` trailer for Claude, Anthropic, OpenAI, or any AI system.
- Never add an AI attribution trailer to commit messages.
- Before every milestone commit, verify:
  - `git config user.name`
  - `git config user.email`
- The expected identity is:
  - `Gasooom`
  - `aboelgasimibrahim999@gmail.com`
- If the identity is incorrect, STOP and ask Gasim to correct it. Do not change it automatically.
- Only commit after the milestone has been explicitly approved and all milestone requirements are complete.
- One commit per approved milestone deliverable unless a correction commit is explicitly required.
- Commit messages must clearly identify the milestone and what was delivered.
- Example:
  `feat(milestone-0): establish SiteScout repository architecture`
- Push each approved milestone commit to `origin main`.
- Never push unapproved work.
- Never force-push.
- Never rewrite published Git history.
- Never commit secrets, credentials, `.env` files, generated datasets, or files over 5 MB.
- Before every commit, verify:
  - no secrets, credentials, API keys or tokens
  - no private data
  - no raw, processed, manual or generated datasets
  - no file over 5 MB
  - the Git author identity above
- After committing, verify the commit author with:
  `git log -1 --format=fuller`
- After pushing, verify:
  - the current branch
  - the latest commit
  - working tree status
- The milestone is not considered delivered until the approved commit has been successfully pushed to `origin main`.

## Geospatial rules

- Storage CRS: `EPSG:4326`. Distance and area calculations: `EPSG:32735`.
- Never compute distances in latitude/longitude degrees.
- Boundaries: geoBoundaries **ADM2 is the master**; derive ADM1 and ADM0 from it so edges match.

## Architecture rules

- Business logic lives in `src/sitescout/`. Scripts in `scripts/` only parse arguments and call `src/`.
- Python is the only place numbers are computed. The pipeline exports `data/export/sitescout.json`.
- The front end in `app/` renders that export. It may filter, sort and display; it never computes scores, coverage or selection.
- The design reference is `app/prototype.html`.
- Configuration-driven paths only (`config/settings.yaml`, `config/weights.yaml`). No absolute paths.
- Config comes from YAML only.
- **Environment-variable and `.env` overrides are disabled.**
  - Single exception (Milestone 9, D-050, extended by D-054): the optional AI Site Analyst reads its API key from `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, whichever matches the configured provider, as a secret credential only. Provider, model, limits, timeout, temperature and every analytical parameter still come from YAML; no environment variable or `.env` file overrides configuration.
- Models reject unknown keys and are immutable after loading.
- Sensitivity runs pass explicit, logged overrides; they never read the environment.
- Parameters that `docs/SPEC.md` requires but does not define stay marked `pending` in config, with the reason. They never get invented defaults: code that reads one stops with a clear error, and overrides cannot fill them.
- Pipelines are reproducible and idempotent.
- Pipelines validate schemas between stages.
- Pipelines log instead of printing.
- Document every external source in `docs/data_sources.md` (URL, license, retrieval date, known gaps).
- Record design decisions in `docs/decisions.md` (ID, date, decision, alternatives, reason).

## Evidence rules

- Evidence types:
  - `RETRIEVED_FACT`
  - `CALCULATED`
  - `INFERRED`
  - `UNKNOWN`
- Every displayed number comes from structured data.
- Reports are built from templates with injected values.
- An automated check confirms every number in every brief exists in structured data.
- Target: 100% grounding.
- Confidence is a level:
  - `High`
  - `Medium`
  - `Low`
- Confidence is never a percentage.
- Confidence uses only factors that differ between sites.
- Universal unknowns are a fixed list in every output.

## Candidate and scoring rules

- Generate 200–400 candidates in code.
- Candidates come from real host locations:
  - fuel stations
  - malls
  - supermarkets
  - hotels
  - logistics sites
  - industrial sites
- Restaurants are excluded.
- Corridor points are also allowed.
- Never place candidates by hand.
- Do not drop candidates near existing chargers; the backtest depends on them.
- Normalize with percentile ranks.
- Use `log1p` for skewed counts where appropriate.
- Do not use min-max normalization.
- Two profiles:
  - urban
  - corridor
- Profile weights live in `config/weights.yaml`.
- Expose the overall score, profile, component scores and weights for every site.
- `dist_kigali_cbd_m` and `dist_town_m` are reported features only and are never scored.
- Never hide scoring logic behind an LLM.
- Never silently change weights, radii or thresholds.

## Backtest rules

### Backtest mode

Existing charger locations are removed from every feature, including the charging gap.

Remove charger-related leakage from:

- charging-gap features
- POI features
- host features
- `amenity=charging_station` POIs
- fuel stations tagged with `socket:*`

Any leakage is a test failure.

### Production mode

Existing chargers are included when selecting new sites.

### Evaluation

Compare SiteScout against:

1. Random baseline
2. Population-only baseline

Random baseline requirements:

- 1,000 seeds
- report the mean
- use bootstrap confidence intervals

If SiteScout does not beat the population-only baseline, report that honestly.

Test weight stability using ±20% per weight.

Target:

- Top-30 overlap of at least 70%

Do not fabricate evaluation results.

## Optimization rules

- The network problem is the **Maximum Coverage Location Problem (MCLP)**.
- Solve the exact optimization problem with **PuLP and its bundled CBC solver**.
- Demand is normalized to sum to 1.
- λ defaults to `0.01`.
- Eligibility is a **percentile**.
- Eligible sites are those in the top 50% of scores.
- If the problem is infeasible, stop with a clear error.
- Never lower thresholds silently.
- Keep a greedy solver as a baseline.
- Report the exact-versus-greedy gap.
- Always compare three results:
  1. Top-30 by score
  2. Greedy 30
  3. Exact MCLP 30
- Compare:
  - covered demand
  - province spread
  - overlap
- No hardcoded or demo results anywhere in the pipeline.

## Testing

Use pytest for:

- config validation
- CRS helpers
- distance helpers
- candidate deduplication
- every feature
- normalization edge cases:
  - zeros
  - ties
  - NaN
  - one candidate
- weights summing to 1
- component bounds
- backtest leakage
- MCLP constraints
- exactly N selected sites
- spacing constraints
- export schema
- brief grounding

Use fixed random seeds wherever randomness is used.

Standard validation commands:

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest -q
```

## Development workflow

Work on one milestone at a time. For every milestone:

1. Read this file and inspect the repository.
2. State the implementation plan and wait for approval before large changes.
3. Implement only the approved milestone.
4. Run the tests and report pass/fail with the numbers.
5. Report what was implemented and explain the important engineering decisions.
6. Ask Gasim five difficult interview-style questions about the code just written. If he can't answer, explain before moving on.
7. Wait for explicit approval before starting the next milestone.

- Never rewrite working code without a stated reason.
- If you disagree with a requirement, say: "I disagree with X because Y." Never silently change the specification.
- If specification numbers are internally inconsistent, flag the inconsistency instead of forcing the numbers.
- One commit per milestone deliverable on `main`, with a message stating what was delivered and what the tests show. Push after each milestone.

## Milestones

| # | Milestone | Done when |
|---|---|---|
| 0 | Repository setup + architecture | Structure, config, tooling, README skeleton; plan approved |
| 1 | Data ingestion + geospatial pipeline | All sources in GeoParquet, validated and documented |
| 2 | Candidate generation | 200–400 candidates, checked on a map |
| 3 | Feature engineering | All features tested; `docs/features.md` written |
| 4 | Scoring + confidence | Both profiles and modes; edge-case tests pass |
| 5 | Evaluation | `reports/evaluation.md` with baselines, stability and data quality |
| 6 | Network optimization | Exact MCLP vs greedy vs Top-30 comparison |
| 7 | Evidence + reports | 30 briefs; grounding check at 100% |
| 8 | Export + front end | Front end runs on the real export |
| 9 | Stretch (optional) | Station sizing or AI Site Analyst |
| 10 | Agentic site investigation | Agent over the deterministic tools plus retrieval of project knowledge (RAG); its own evaluation suite passes its defined acceptance criteria; decision outputs unchanged |
| 11 | Packaging + demo | README, methodology, top-30 map, case study, 2-minute demo script |

## Stop rule

Day 0 is the day Milestone 0 is committed. If Milestone 5 (evaluation) is not complete by Day 5: no AI agent, no RAG, no stretch features. Focus on data → scoring → evaluation → optimization.

## Technology discipline

- No microservices, extra databases, extra frameworks or unnecessary abstractions.
- Out of scope for v1: LangGraph, PostGIS, Kenya, contract extraction, a site-pipeline tracker, authentication.
- RAG is in scope only as the Milestone 10 local project-knowledge index. It must never contain or replace structured SiteScout site data.
- A new dependency must solve a named problem; record it in `docs/decisions.md`.

## Current State

Never use CLAUDE.md to determine the current milestone or repository state.

At the beginning of every session, determine the actual state from:
- git status
- git log
- repository contents
- milestone artifacts/tests
- docs/SPEC.md

Never assume a milestone is complete based only on CLAUDE.md.
