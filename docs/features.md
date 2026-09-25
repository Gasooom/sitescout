# Features

Milestone 3 computes the features SPEC §4 lists for every candidate. This page defines each one: what it measures, where it comes from, how it is calculated, its unit, what happens when data is missing, its known limitations and how it behaves in backtest mode.

## Scope

- **Candidates.** The 300 candidates in `candidates` (Milestone 2, D-031).
- **Outputs.** Two layers in `data/processed/`, one row per candidate, sorted by `candidate_id`, with the candidate's point in EPSG:4326:
  - `features_production`: existing chargers included (SPEC §5).
  - `features_backtest`: every existing charger removed from every input first (SPEC §5).

  Each mode is built from the validated input layers on its own; no backtest value is copied or patched from a production value (D-038). Column types and descriptions: [data_sources.md](data_sources.md#features_production).
- **Coordinates.** Every distance and area is computed in EPSG:32735 (metres). The WorldPop raster stays in EPSG:4326; its pixel centres are projected to EPSG:32735 before any distance is measured. Nothing is measured in degrees.
- **Radii** are inclusive: something exactly 1,000 m away is within 1 km.
- **Raw values only.** Values are in natural units: metres, people, counts and ratios. Milestone 3 applies **no** percentile rank, log1p, inversion of "lower is better", bonus, weight, profile, score or confidence. SPEC §5 puts all of them in scoring (Milestone 4). The only derived value is `grid_completeness_ratio`, which SPEC §4 defines as a ratio.
- **Run:** `uv run python scripts/features.py`. It reads `data/processed/` only, never the network. When anything fails, it writes neither layer and removes old ones.

## Summary

| Feature | Group | Unit | Type | Use | Source |
|---|---|---|---|---|---|
| `pop_1km`, `pop_5km`, `pop_10km` | Demand | people | float64 | Weighted | WorldPop R2025A |
| `dist_road_m` | Access | m | float64 | Weighted | OSM roads, via `candidates` |
| `road_class` | Access | OSM `highway` value | string | Road-class bonus (M4) | OSM roads, via `candidates` |
| `dist_trunk_m` | Access | m | float64 | Weighted | OSM roads |
| `poi_1km`, `poi_3km` | Host / commercial | count | int64 | Weighted | OSM POIs |
| `poi_<type>_1km`, `poi_<type>_3km` | Host / commercial | count | int64 | Reported only | OSM POIs |
| `host_type` | Host / commercial | category | string | Host-type bonus (M4) | `candidates` |
| `dist_charger_m` | Charging gap | m | float64 | Weighted | OSM chargers (+ manual CSV) |
| `chargers_10km`, `chargers_25km` | Charging gap | count | int64 | Weighted | OSM chargers (+ manual CSV) |
| `dist_substation_m`, `dist_line_m` | Grid evidence | m | float64 | Weighted | OSM `power=*` |
| `grid_completeness_ratio` | Grid evidence | ratio | float64 | Proxy for confidence and data quality (M4, M5) | OSM `power=*`, ADM2 districts |
| `dist_kigali_cbd_m` | Access | m | float64 | Reported only | OSM place node |
| `dist_town_m` | Demand | m | float64 | Reported only | OSM place nodes |
| `outside_rwanda_share_10km` | Demand (diagnostic) | share, 0 to 1 | float64 | Reported only | ADM0 outline |
| Elevation, slope | Terrain | — | — | **Not computed** (D-037) | Copernicus DEM, deferred |

Each row also carries the candidate's identity: `candidate_id`, `host_type`, `host_osm_id`, `origin`, `district_id`, `district`, `province_code` and `province`.

## Demand

### `pop_1km`, `pop_5km`, `pop_10km`

- **Definition.** Modelled population within 1, 5 and 10 km of the candidate.
- **Source.** `population_worldpop` (WorldPop 2025, R2025A, 100 m constrained): people per pixel, a modelled estimate, not a census count. 3 arc-second pixels.
- **Geometry.** The candidate's point; each pixel's centre.
- **Calculation.**
  1. Choose the pixels around the candidate that could lie within 10 km. The metric square around the candidate is converted to degrees only to choose this window, never to measure.
  2. Project each pixel centre to EPSG:32735 and measure its distance to the candidate in metres.
  3. Sum, in float64, the values of the pixels whose centre is within the radius. The sums are built ring by ring from 1 km outward, so `pop_1km ≤ pop_5km ≤ pop_10km` always holds.
- **Unit and type.** People, float64 (modelled values are fractional).
- **Missing data.** A nodata pixel adds 0: in WorldPop's constrained model, a nodata pixel has no modelled settlement. A circle with no people is 0.0, never null.
- **Limitations.**
  - The pixel-centre rule includes or leaves out a whole edge pixel. A 1 km circle holds about 370 pixels, so the effect is small and has no direction (D-036).
  - **Border.** The raster ends at Rwanda's border. A circle that crosses it counts only the people inside Rwanda, and population outside is never estimated. See `outside_rwanda_share_10km`.
  - R2025A is a release WorldPop says may still change.
- **Backtest.** Identical in both modes: population has no charger input.
- **Leakage.** None.

### `dist_town_m` (reported only)

- **Definition.** Distance to the nearest town or city centre.
- **Source.** `osm_places`: every OSM `place=city` and `place=town` **node** inside Rwanda (`features.town_centres`, D-035). On the 2026-09-23 extract: 11 cities and 98 towns; one town node outside Rwanda is not used.
- **Calculation.** Distance in EPSG:32735 from the candidate to the nearest of those nodes. Kigali's city node counts as a centre.
- **Unit and type.** Metres, float64.
- **Missing data.** Null only if no centre exists (never on real data).
- **Limitations.**
  - A node marks where OSM places the settlement's label, not an administrative centre.
  - OSM's `town` and `city` classes follow mappers' judgement.
  - Two unnamed `place=city` nodes near Musanze are counted, because the rule counts every city node.
- **Use.** Reported only; never scored (CLAUDE.md). SPEC §5 uses it to assign the profile, whose radii stay pending until M4.
- **Backtest.** Identical in both modes.

### `outside_rwanda_share_10km` (reported only)

- **Definition.** The share of the candidate's 10 km circle whose area lies outside Rwanda, where the population raster has no data.
- **Source.** `admin_country` (dissolved from geoBoundaries ADM2).
- **Calculation.** 1 − area(circle ∩ Rwanda) ÷ area(circle), in EPSG:32735, with the circle drawn as a 256-segment polygon. It is exactly 0 when the circle lies wholly inside Rwanda.
- **Unit and type.** Share from 0 to 1, float64.
- **Use.** A diagnostic that shows where `pop_10km` leaves out people across the border (D-036). It never changes a population value, a score, a confidence level or a selection.
- **Real data.** 69 of 300 candidates have part of their circle outside Rwanda, 31 of them a quarter or more. The maximum is 0.73, in Nyagatare.

## Access

### `dist_road_m` and `road_class`

- **Definition.** The distance to, and the OSM `highway` value of, the nearest drivable road.
- **Source.** Copied from `candidates` (`dist_road_m`, `nearest_road_class`), which M2 computed from `osm_roads` with the drivable classes of D-028. They are not recomputed, so the same number means the same thing everywhere.
- **Unit and type.** Metres, float64; `road_class` is a string.
- **Missing data.** Never null: every candidate is within 500 m of a drivable road (SPEC §3). The real maximum is 227 m.
- **Use.** `road_class` feeds the road-class bonus in M4 (trunk 10, primary 5); Milestone 3 applies no bonus.
- **Backtest.** Identical in both modes.

### `dist_trunk_m`

- **Definition.** Distance to the trunk corridor: the nearest OSM road whose `highway` value is in `features.trunk_road_classes` (`trunk`, `trunk_link`; D-032).
- **Source.** `osm_roads.highway`, line geometry.
- **Calculation.** Distance in EPSG:32735 from the candidate to the nearest such road. Roads are not clipped to Rwanda, as for `dist_road_m`.
- **Unit and type.** Metres, float64.
- **Missing data.** Null if no trunk road is mapped (never on real data).
- **Limitations.** It relies on OSM's road classification. `primary` roads are not part of it; the road-class bonus rewards them in M4.
- **Backtest.** Identical in both modes.

### `dist_kigali_cbd_m` (reported only)

- **Definition.** Distance to the **Kigali city centre as mapped in OSM**.
- **Source.** `osm_places`, the node `features.kigali_cbd.osm_id` = `node/60485579` (`place=city`, `capital=yes`; D-035).
- **Calculation.** Distance in EPSG:32735.
- **Unit and type.** Metres, float64.
- **Missing data.** The run stops if the node is missing from the extract, or is not a `place=city` node inside Rwanda.
- **Limitations.** It is where OSM places the city, not an official CBD boundary. By the geoBoundaries ADM2 outlines the node lies in Gasabo district, not Nyarugenge.
- **Use.** Reported only; never scored (CLAUDE.md).
- **Backtest.** Identical in both modes.

## Host / commercial

### `poi_1km`, `poi_3km`

- **Definition.** Distinct points of interest within 1 and 3 km.
- **Source.** `osm_pois`. A POI is a row matching any `features.poi_types` selector: `amenity=*`, `shop=*`, `tourism=*`, `office=*` or `industrial=*` (one type per OSM key; D-033). On the 2026-09-23 extract, 6,664 rows are POIs.
- **Geometry.** A node's point, or a point on the surface of an area POI, computed in EPSG:32735 (the method M2 uses for area hosts).
- **Calculation.** The number of distinct POIs within the radius. A POI with two keys (say, `amenity` and `shop`) counts once.
- **Excluded:**
  - `amenity=charging_station`, in **both** modes (`features.poi_exclude`). Chargers are measured by the charging gap, and counting them here would count them twice.
  - The candidate's **own host** (`host_osm_id`), which the host-type bonus already rewards. Other hosts nearby are counted.
  - Land-use areas, `building=*` rows and `man_made=works`: they are in `osm_pois` for host classification but are not POI types.
- **Unit and type.** Count, int64.
- **Missing data.** 0 is a real zero: no mapped POI.
- **Limitations.**
  - The count reflects how densely OSM is mapped as well as how much activity there is. Mapping is densest in Kigali.
  - Every POI counts the same: a pharmacy and a mall each count 1.
  - The counts are skewed (real median 1 within 1 km, maximum 284). Whether to apply log1p is decided in M4 (`scoring.log1p_features`, pending).
- **Backtest.** Fuel stations tagged `socket:*` are removed as well (SPEC §5). None exist on the 2026-09-23 extract, so the real counts are the same in both modes.
- **Leakage.** Charging stations never count, and socket-tagged fuel stations are removed in backtest mode. Tested (`tests/test_features_leakage.py`).

### `poi_<type>_1km`, `poi_<type>_3km` (reported only)

For each type (`amenity`, `shop`, `tourism`, `office`, `industrial`), the POIs of that type within the radius, under the same rules. They answer SPEC §4's "POI counts by type" and are reported, not weighted. A POI with two types counts once in each.

### `host_type`

The candidate's host type from Milestone 2: fuel, mall, supermarket, logistics, industrial, hotel or none. It feeds the host-type bonus in M4. A candidate hosted by a socket-tagged fuel station keeps `host_type = fuel` in both modes; only its charger role is removed (docs/decisions.md, open question).

## Charging gap

### Existing chargers

Which records are existing chargers (D-034):

- **Production mode:**
  - OSM `amenity=charging_station` nodes and areas, except those tagged `access=private` or `access=no`;
  - OSM fuel stations tagged `socket:*`;
  - the rows of `data/manual/chargers.csv` when it exists.
- **Merging.** Records within **50 m** of each other (`sources.charger_match_radius_m`), directly or through a chain, are one charging site. A site sits at its first record (OSM charging stations, then fuel stations, then CSV rows, then by id). An area charger is represented by a point on its surface. The 50 m is a project data-matching choice, not a definition of a charging site.
- **Backtest mode:** none. The set is built empty, without reading a charger source.

On the 2026-09-23 extract: 7 OSM charging stations (none private), no socket-tagged fuel stations, and **no manual CSV** (`data/manual/chargers.csv` does not exist). The 7 records become **5 sites**: three nodes 10 m apart are one site. Every feature layer's metadata records the sources and their counts.

### `dist_charger_m`

- **Definition.** Distance to the nearest existing charging site.
- **Calculation.** Distance in EPSG:32735 from the candidate to the nearest site's point.
- **Unit and type.** Metres, float64.
- **Missing data.** **Null** when there is no existing charger. That is always the case in backtest mode; it is never filled with a stand-in distance.
- **Limitations.**
  - 5 sites, most of them in Kigali: the real median is 47 km, and the maximum is 150 km.
  - Until the manual CSV is filled, the inventory is OSM's alone.
  - A larger distance means a larger gap. M4 must invert this feature's direction relative to the counts, explicitly (SPEC §5).
- **Backtest.** Null for every candidate.

### `chargers_10km`, `chargers_25km`

- **Definition.** Existing charging sites within 10 and 25 km.
- **Unit and type.** Count, int64.
- **Missing data.** 0 when there are none.
- **Real data.** 72 candidates have a site within 10 km, 100 within 25 km.
- **Backtest.** 0 for every candidate. As SPEC §5 notes, the charging-gap component is then constant, so the backtest tests the other four components.

## Grid evidence

These are distances to, and counts of, **mapped** OpenStreetMap power infrastructure. They are evidence that grid infrastructure is mapped nearby. They say nothing about grid capacity, transformer capacity, available voltage, a connection, or the utility's view. Voltage tags are not interpreted. The 2009 energydata.info transmission lines are a cross-check only (SPEC §2) and are not used.

### `dist_substation_m`

- **Definition.** Distance to the nearest mapped substation.
- **Source.** `osm_power` rows with `power=substation` (`features.grid_osm_tags.substation`, D-033): 43 areas on the 2026-09-23 extract.
- **Calculation.** Distance in EPSG:32735 to the substation's mapped geometry, so a point inside a substation area is at 0.
- **Unit and type.** Metres, float64.
- **Missing data.** Null if no substation is mapped (never on real data).
- **Backtest.** Every charger object is removed from `osm_power` first, in case one is also tagged with a power value. No power feature on the real extract is one.

### `dist_line_m`

- **Definition.** Distance to the nearest mapped power line.
- **Source.** `osm_power` rows with `power=line`, `power=minor_line` or `power=cable`: 122 features, about 1,648 km.
- **Calculation.** Distance in EPSG:32735 to the line geometry.
- **Unit and type.** Metres, float64.
- **Missing data.** Null if no line is mapped.

SPEC §5 treats grid evidence as missing when no mapped substation or line lies within 5 km. That rule belongs to scoring (M4). On the real candidates, 186 of 300 have a substation or a line within 5 km.

### `grid_completeness_ratio`

- **Definition.** A **proxy** for how completely each district's grid is mapped (SPEC §4): mapped power features per km² in the candidate's ADM2 district, divided by the national median of that density across the 30 districts.
- **Source.** `osm_power` rows whose `power` value is in the allow-list: `line`, `minor_line`, `cable`, `substation`, `transformer`, `tower`, `pole`, `portal`, `plant`, `generator` and `connection` (D-033). No other value is ever counted. That leaves out `150kWh` and `11 kWh`, which are tags on two charging-station nodes.
- **Calculation.**
  1. Count the features that intersect each district; a feature crossing several districts counts in each.
  2. Divide by the district's `area_km2`.
  3. Divide by the median over the 30 districts (with an even count, the mean of the two middle values).
  4. Each candidate takes its district's ratio.
- **Unit and type.** Ratio, float64. 1 means the national median.
- **Missing data.** A district with no counted feature gets 0. A national median of 0 stops the run, because the ratio would be undefined.
- **Real data.** 5,206 features are counted, and the national median is 0.188 per km². Ratios run from 0.12 (Nyaruguru) to 6.62 (Gasabo).
- **Limitations.** Every feature counts the same. Densely mapped towers (379 of Gasabo's 536) or individually mapped generating units (334 in Rwamagana) can dominate a district. It measures how much is mapped, not the grid itself.
- **Use.** Not weighted. It is meant for the "sparse public-map coverage" confidence factor (M4) and the data-quality report (M5).
- **Metadata.** The layer metadata holds the full table by district.

## Production and backtest

| | Production | Backtest |
|---|---|---|
| Existing chargers | OSM (public) + socket-tagged fuel + manual CSV, merged within 50 m | None |
| `dist_charger_m` | Distance to the nearest site | Null |
| `chargers_10km`, `chargers_25km` | Sites within the radius | 0 |
| POIs | Charging stations and the own host excluded | Also socket-tagged fuel stations |
| Power features | As mapped | Charger objects removed first |
| Every other feature | Same inputs | Same inputs |

On the 2026-09-23 extract, the two layers differ only in the three charging-gap columns.

The leakage tests (`tests/test_features_leakage.py`) check that backtest mode equals a world where no charger was ever mapped. Adding, removing or moving a charger, a charging-station POI, a CSV row or a socket-tagged fuel station changes no backtest value. They also check that production mode does see those changes.

**Not removable.** The OSM data is dated 2026-09-23, after the chargers were mapped. People who mapped a charger may also have mapped the places around it. Backtest mode cannot undo that; the M5 evaluation must say so.

## Data status on 2026-09-25

- **Manual charger CSV:** missing. Production chargers come from OSM only.
- **Elevation and slope:** not computed. SPEC §2 defers the Copernicus DEM (D-037).
- **Population:** undercounted across the border. See `outside_rwanda_share_10km`.
- **Profile:** the town and Kigali radii stay pending until M4, so `profile` is still null in `candidates`.
