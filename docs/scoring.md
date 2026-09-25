# Scoring and confidence

Milestone 4 turns the raw features of each candidate ([features.md](features.md)) into an explainable score and a confidence level (SPEC §5, §6). Every number comes from deterministic code in `sitescout/scoring.py` and `sitescout/confidence.py`; no model, randomness or LLM is involved.

A score ranks candidates for **investigation**. It is not a measure of demand, revenue or grid capacity. Actual grid connection feasibility requires utility confirmation.

## Outputs

`uv run python scripts/score.py` reads `features_production` and `features_backtest` and writes `scores_production` and `scores_backtest` in `data/processed/`: 300 rows each, sorted by `candidate_id`. When anything fails, neither layer is written. Columns: [data_sources.md](data_sources.md#scores_production).

Each row answers "why did this site get this score?":

| Column | What it shows |
|---|---|
| `profile` | urban or corridor, which picks the component weights |
| `pct_<feature>` | Percentile points of each weighted feature, 0 to 100, higher is better |
| `component_<name>` | The five component scores, after bonuses and the cap at 100 |
| `host_bonus`, `road_bonus` | Points added to host / commercial and to access |
| `grid_evidence_status` | CALCULATED, or UNKNOWN when grid evidence is missing |
| `score`, `rank` | The overall score and its rank in this mode |
| `confidence`, `confidence_reasons` | High, Medium or Low, and why |
| `universal_unknowns` | What no site's data can answer |

The weights are in the layer metadata and in `config/weights.yaml`; the raw feature values are in the feature layers.

## Method

### 1. Percentile points (D-040)

For each of the 12 weighted features:

1. `poi_1km`, `poi_3km`, `chargers_10km` and `chargers_25km` get log1p first (SPEC §5: skewed counts). log1p keeps the order of values, so it changes no percentile; it is kept because SPEC asks for it, and a test proves it has no effect.
2. **Percentile** = 100 × (candidates below + 0.5 × candidates equal) / n, where n counts the candidates of the same mode whose value is present. Urban and corridor candidates are ranked together. Ties share a value; a feature with one value for everyone gets 50, and so does a single candidate. With 300 candidates the points run from 0.17 to 99.83.
3. **Direction.** Lower is better for `dist_road_m`, `dist_trunk_m`, `dist_substation_m`, `dist_line_m`, `chargers_10km` and `chargers_25km`: they become 100 − percentile. A larger `dist_charger_m` is better: a larger charging gap.
4. **Missing values** get 0, after inversion, so a missing value never raises a score.

No min-max normalisation is used anywhere.

### 2. Components (SPEC §5, D-042)

| Component | Features and weights | Bonus |
|---|---|---|
| Demand | `pop_5km` 0.5, `pop_1km` 0.25, `pop_10km` 0.25 | — |
| Access | `dist_road_m` 0.5, `dist_trunk_m` 0.5 | road class: exact `trunk` +10, exact `primary` +5, every other class (including `trunk_link` and `primary_link`) +0 |
| Host / commercial | `poi_1km` 0.5, `poi_3km` 0.5 | host type: fuel +10, mall +10, supermarket +5, logistics +5, hotel +5, industrial 0, none 0 |
| Charging gap | `dist_charger_m` 0.5, `chargers_10km` 0.25, `chargers_25km` 0.25 | — |
| Grid evidence | `dist_substation_m` 0.5, `dist_line_m` 0.5 | — |

Each component is capped at 100.

**Missing grid evidence.** When neither a mapped substation nor a mapped power line lies within 5 km (inclusive), the grid-evidence component is 0 and `grid_evidence_status` is UNKNOWN. It never gets an average or an imputed value.

### 3. Profile (SPEC §3, D-039)

**Urban** if the candidate lies within 10 km of the Kigali city centre as mapped in OSM (`node/60485579`) **or** within 3 km of any OSM town or city centre (both inclusive); otherwise **corridor**. The two distances only assign the profile; they are never scored.

### 4. Score

Score = Σ profile weight × component, on a 0 to 100 scale.

| Component | Urban | Corridor |
|---|---|---|
| Demand | 0.30 | 0.15 |
| Host / commercial | 0.25 | 0.15 |
| Access | 0.20 | 0.30 |
| Charging gap | 0.15 | 0.25 |
| Grid evidence | 0.10 | 0.15 |

Rank 1 is the highest score in the mode; equal scores are ordered by `candidate_id`.

## Confidence (SPEC §6, D-041)

A level, never a percentage, built only from factors that differ between sites:

1. **Grid evidence missing** (no mapped substation or line within 5 km) → **Low**, whatever else holds.
2. Otherwise count:
   - **sparse public-map coverage**: the district's `grid_completeness_ratio` is below 0.5;
   - **no identified host**: a corridor point (`host_type` none);
   - **remote location**: no mapped POI within 3 km to cross-check (`poi_3km` = 0).

   0 factors → **High**, 1 → **Medium**, 2 or more → **Low**.

`confidence_reasons` lists every factor that holds, including those that do not change the level; "No confidence-lowering factor found" when none does.

**Universal unknowns**, the same for every site and not part of the level: grid connection capacity, transformer capacity, land availability, landowner willingness, permit requirements.

## Production and backtest

Both modes use the same method on their own feature layer. In backtest mode no existing charger is used (SPEC §5), so:

- `dist_charger_m` is missing for every candidate → 0 points;
- `chargers_10km` and `chargers_25km` are 0 for every candidate → 50 points each after inversion;
- the charging-gap component is **25** for every candidate.

That adds **3.75** points to every urban score (weight 0.15) and **6.25** to every corridor score (weight 0.25). SPEC §5 says to document this, not work around it, so it is not compensated. Within a profile the ranking is unaffected; between profiles, corridor candidates gain 2.5 points relative to urban ones compared with a component of 0.

## Results on 2026-09-25

On the 300 real candidates (OSM extract of 2026-09-23):

- **Profiles:** 107 urban, 193 corridor.
- **Confidence:** High 113, Medium 59, Low 128 (114 because grid evidence is missing). Corridor candidates carry most of the Low ratings (109 of 128).
- **Grid evidence:** CALCULATED for 186, UNKNOWN for 114.
- **Scores, production:** 21.6 to 77.6, median 52.3; mean 63.4 urban and 47.2 corridor.
- **Scores, backtest:** 14.2 to 78.7, median 45.3; mean 62.5 urban and 38.3 corridor. 21 of the production top 30 are also in the backtest top 30.

## Known limitations

- **Corridor points without a host can rank high.** In production the top-ranked candidate is a corridor point in Rubavu, and 3 of the top 10 are corridor points. Corridor weights favour access and the charging gap, and SPEC §3 says a site without a host cannot be investigated. Whether such points may be among the 30 is decided in Milestone 6 (docs/decisions.md).
- **The charging gap mostly measures distance from Kigali.** The 5 known charging sites are in or near Kigali, so production charging-gap scores are lowest there (5 to 9 for the top Kigali sites). Filling `data/manual/chargers.csv` would change this.
- **Percentiles are relative.** A high score means "better than most of these 300 candidates", not good in any absolute sense. Adding or removing candidates changes every percentile.
- **Grid completeness is a proxy** for how much is mapped (docs/features.md). It only lowers confidence; it never enters the score.
- **Population across the border is not counted** (docs/features.md, `outside_rwanda_share_10km`), which lowers demand for border candidates such as those in Rubavu and Rusizi.
