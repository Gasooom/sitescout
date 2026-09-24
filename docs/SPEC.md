# SiteScout — Method Specification

This is the detailed specification. `CLAUDE.md` holds the rules and workflow; this file holds the method. If they conflict, flag it and ask Gasim.

## 1. Product

**SiteScout — EV Charging Network Intelligence for Rwanda.**

A decision-support tool for a charging-site developer. It answers:
1. Which locations deserve investigation?
2. Why is each one attractive, and what evidence supports that?
3. What is still unknown?
4. Which 30 locations form the best network when chosen together?
5. What should the developer investigate next?

**Context:** public information shows EV charging companies in East Africa planning 30+ new fast-charging sites within 12 months. Each site must clear four gates: grid-connection feasibility, a site-owner contract, installation and permits. SiteScout helps decide where to spend scarce site-development effort. It does not replace the four gates.

**The system must never claim** grid approval, transformer capacity, land availability, owner willingness, permit approval, revenue, or that the ranking is ground truth.

## 2. Data sources

| Data | Source | Use |
|---|---|---|
| Roads, POIs, charging stations, power infrastructure | Geofabrik `rwanda-latest.osm.pbf` (parse locally with pyrosm or osmium) | Candidates, access, host, grid evidence |
| Population | WorldPop Rwanda 2025, 100 m, constrained, release R2025A (a modelled estimate, not a census count) | Demand features, MCLP demand nodes |
| Elevation | Copernicus DEM GLO-30 (deferred; windowed reads from cloud-optimized tiles when needed) | Terrain (reported, not scored) |
| Boundaries | geoBoundaries RWA ADM2 as master; ADM1 and ADM0 derived from it | Clipping, provinces, districts, maps |
| Grid layers | OSM `power=*` (primary); energydata.info Rwanda transmission network (2009 data, cross-check only) | Grid evidence; completeness by district |
| Water, protected areas | OSM `natural=water`, `boundary=protected_area` (WDPA terms forbid redistribution) | Candidate filtering |
| Existing public chargers | Manual CSV from public charger maps + OSM `amenity=charging_station` | Charging gap, backtest ground truth |

Charger CSV (`data/manual/chargers.csv`, filled by hand by Gasim) columns: `name, lat, lon, source_url, date_retrieved, operator_public_name`. `operator_public_name` stays in the CSV for provenance only; it is never exported or shown. Do not copy data from PlugShare (its terms forbid it).

Every source goes in `docs/data_sources.md` with URL, license, retrieval date and known gaps. If a source can't be verified or accessed, stop and report it rather than substituting silently.

**CRS:** store in EPSG:4326; compute distances and areas in EPSG:32735 (UTM 35S).

## 3. Candidate generation

Target: **200–400 candidates, generated in code.**

1. **Host candidates:** OSM fuel stations, malls, supermarkets, hotels, logistics and industrial sites. **No restaurants** (their size cannot be measured in OSM). A site without a host cannot be investigated.
2. **Corridor candidates:** points every ~10 km along trunk and primary roads. Snap each to the nearest host within 2 km if one exists; otherwise keep it with `host_type = none`.
3. **Deduplicate:** merge candidates within 300 m and keep the highest-priority host (fuel, mall, supermarket, logistics, industrial, hotel, none).
4. **Filter:** drop points more than 500 m from a drivable road, or inside water or protected areas.
5. **Do not drop candidates near existing chargers.** The backtest needs them.

Candidate fields: `candidate_id, lat, lon, host_name, host_type, district, province, nearest_road_class, dist_road_m, profile (urban|corridor)`.

**Profile rule:** urban if within a configured radius of a town or city centre (larger for Kigali); otherwise corridor. Radii live in config and are documented.

## 4. Features

| Group | Features |
|---|---|
| Demand | Population within 1, 5 and 10 km; distance to nearest town or city centre (reported only) |
| Access | Distance to road, road class, distance to trunk corridor; distance to Kigali CBD (reported only) |
| Host / commercial | POI counts by type within 1 and 3 km; host type |
| Charging gap | Distance to nearest existing charger; charger counts within 10 and 25 km |
| Grid evidence | Distance to nearest mapped substation and line; grid-layer completeness in the district (mapped power features per km² relative to the national median, labelled as a proxy) |
| Terrain (reported only) | Elevation, slope |

Every feature has a definition, unit and source in `docs/features.md`.

## 5. Scoring

**Normalization:** percentile rank within Rwanda. Apply log1p to skewed counts before ranking. Invert "lower is better" features explicitly. No min-max normalization.

**Within-component feature weights (in `config/weights.yaml`, each group sums to 1):**

| Component | Features and weights |
|---|---|
| Demand | pop_5km 0.5, pop_1km 0.25, pop_10km 0.25 |
| Access | dist_road_m 0.5, dist_trunk_m 0.5 |
| Host / commercial | poi_1km 0.5, poi_3km 0.5 |
| Charging gap | dist_charger_m 0.5, chargers_10km 0.25, chargers_25km 0.25 |
| Grid evidence | dist_substation_m 0.5, dist_line_m 0.5 |

`dist_town_m` and `dist_kigali_cbd_m` are reported only. `dist_town_m` is used to assign the profile.

**Component rules:**
- Grid evidence is **missing** when no mapped substation or line lies within 5 km. Missing grid evidence scores 0 for that component and is flagged UNKNOWN. It never gets an imputed or average value.
- Bonuses (points added to the component, capped at 100): host type fuel 10, mall 10, supermarket 5, logistics 5, hotel 5, industrial 0, none 0; road class trunk 10, primary 5.

**Profiles (in `config/weights.yaml`):**

| Component | Urban | Corridor |
|---|---|---|
| Demand | 0.30 | 0.15 |
| Host / commercial | 0.25 | 0.15 |
| Access | 0.20 | 0.30 |
| Charging gap | 0.15 | 0.25 |
| Grid evidence | 0.10 | 0.15 |

Score = Σ weight × component, on a 0–100 scale. Each site exposes its overall score, profile, component scores and weights.

**Modes:**
- **Backtest:** existing chargers are removed from every feature, including the charging gap, and from POI and host features (`amenity=charging_station` POIs; fuel stations tagged `socket:*`). With no chargers, the charging-gap component is constant across candidates, so the backtest effectively tests the other four components. Document this; do not work around it.
- **Production:** existing chargers are included.

## 6. Confidence

A level: High, Medium or Low. Never a percentage. It is based only on factors that differ between sites:
- grid evidence missing → Low;
- sparse public-map coverage in the district;
- no identified host;
- remote location with few data points to cross-check.

Each site stores its confidence reasons as text.

**Universal unknowns** appear as a fixed list in every output and are not part of the confidence level: grid connection capacity, transformer capacity, land availability, landowner willingness, permit requirements.

## 7. Evaluation

All results are labeled **"retrospective plausibility test"**. `scripts/evaluate.py` writes `reports/evaluation.md`.

**A. Backtest** (backtest mode)
- A hit is a candidate within 1 km of a known public charger.
- Report Precision@10, @20, @30 and Recall@30.
- Baselines: random ranking (mean of 1,000 seeds) and population-only ranking. Report bootstrap confidence intervals: the ground truth is small (a few dozen chargers), so point estimates are noisy.
- If SiteScout does not beat population-only, report it and analyze why.

**B. Weight stability:** perturb each weight by ±20% and renormalize. Report the Top-30 overlap. Target ≥ 70%.

**C. Network effect:** covered demand and province spread for Top-30-by-score, greedy-30 and exact-MCLP-30.

**D. Data quality:** missing values, invalid coordinates, duplicates, and grid-layer completeness by district.

**E. Grounding:** automated check that every number in every brief exists in structured data. Target 100%.

## 8. Network optimization (MCLP)

- **Demand nodes:** WorldPop aggregated to H3 resolution 7, weighted by population.
- **Coverage:** a site covers a demand node within the service radius (default 10 km; configurable, sensitivity tested).
- **Objective:** maximize Σ wᵢ·yᵢ + λ·Σ sⱼ·xⱼ, with demand weights wᵢ normalized to sum to 1, sⱼ in [0, 1] and λ = 0.01 by default (so the score term adds at most 0.30), where
  - xⱼ = 1 if site j is opened; yᵢ = 1 if demand node i is covered; sⱼ = normalized site score;
  - yᵢ ≤ Σ xⱼ over the sites j that cover i;
  - Σ xⱼ = N (default 30);
  - xⱼ + xₖ ≤ 1 for any pair of sites closer than d_min (default 2 km);
  - eligibility: sites in the top 50% of scores (`min_score_percentile: 50`). If the problem is infeasible, stop with a clear error; never lower the threshold silently.
- **Production mode:** demand already within the radius of an existing charger is down-weighted by a configured factor (default 0.5), representing capacity those chargers already provide. Document this assumption.
- **Solver:** PuLP with its bundled CBC solver (accepts float coefficients; CP-SAT would need integer scaling). Report solve time and optimality status, with a time limit of 300 s.
- **Baselines:** greedy selection with the same objective and constraints, and Top-30 by score. Report the exact-versus-greedy gap.
- **Per-site output:** `selected`, and `marginal_coverage`. For a selected site this is coverage lost if it is removed; for any other site, coverage gained if it is added.
- **Sensitivity:** λ, radius and existing-charger weight, each varied and reported.

## 9. Evidence and reports

**Evidence record:**
```json
{"claim": "Strong corridor access", "type": "CALCULATED",
 "evidence": {"source": "OpenStreetMap", "metric": "dist_trunk_m", "value": 420, "road_class": "trunk"}}
```
Types: `RETRIEVED_FACT`, `CALCULATED`, `INFERRED`, `UNKNOWN`.

**One-page Site Feasibility Brief (generated from a template):**
1. Site: name, host, coordinates, district, province
2. Opportunity: why investigate this site
3. Demand evidence
4. Access evidence
5. Charging gap
6. Grid evidence, with the mandatory line: *"Actual grid connection feasibility requires utility confirmation."*
7. Score breakdown
8. Confidence level and reasons
9. Risks
10. Unknowns
11. Recommended next actions
12. Important notice: *"This analysis uses public data. It does not establish grid approval, land availability, permitting approval, or commercial viability."*

An LLM may optionally write the Opportunity paragraph from structured fields only. The grounding check then validates it.

## 10. Export and front end

**Export:** `data/export/sitescout.json`, validated by a pydantic schema. It contains:
- `meta`: generated_at, `data_status` (`pipeline` or `synthetic`), sources, config snapshot;
- `weights`;
- `sites`: all fields, components, score, rank, confidence and reasons, evidence, unknowns, next actions, selected, marginal_coverage;
- `existing_chargers`;
- `evaluation` summary;
- `network` summaries for Top-30, greedy and MCLP;
- a simplified boundary GeoJSON (under 300 KB).

**Front end:** start from the existing SiteScout prototype (Overview, Network, Sites, Site Detail, Compare, Reports, AI Analyst preview). Replace its mock model with the export.
- JavaScript only renders, filters and sorts. It never computes scores, coverage or selection.
- The demo-data banner shows only when `data_status` is not `pipeline`.
- Keep the disclaimers: independent project, grid-feasibility line, public data only.

## 11. Stretch (only after Milestone 8 is complete)

- **Station sizing:** Erlang C (M/M/c) estimate of ports per site from proxy demand, labeled as an estimate.
- **AI Site Analyst:** tool-calling over deterministic tools only (`get_site`, `compare_sites`, `explain_score`, `network_contribution`, `generate_brief`). It is read-only and never produces numbers itself. It must be tested with a scenario set before it appears in the demo.

## 12. Deliverables for the demo

- A ranked and optimized top-30 map of Rwanda.
- A comparison of Top-30-by-score vs optimized network (the headline result).
- `reports/evaluation.md` with baselines and stability.
- 30 site briefs.
- README, methodology, one-page case study, and a 2-minute demo script.