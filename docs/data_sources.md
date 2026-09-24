# Data sources

Every external source SiteScout uses, with its URL, licence, retrieval date and known gaps, and the schema of every processed layer built from them. No dataset is committed to this repository. This page and `config/settings.yaml` are what you need to rebuild `data/`. If a source cannot be verified or accessed, the pipeline stops that source and reports it; it never substitutes another source silently.

**Status (Milestone 1):** every source below was verified and retrieved on 2026-09-24, except the manual charger list, which does not exist yet (see [below](#manual-charger-list-datamanualchargerscsv)).

## Rebuilding `data/`

```bash
uv sync
uv run python scripts/ingest.py all        # fetch, process, validate
uv run python scripts/ingest.py validate   # re-check existing outputs only
```

- `fetch` downloads each file into `data/raw/<source_id>/` with a `source.json` manifest (URL, resolved URL, size, SHA-256, HTTP headers, licence, credit, UTC retrieval time). Files already present are not downloaded again; `--refresh` downloads again.
- `process` builds `data/processed/` from `data/raw/` only, without the network. The same raw files always give byte-identical outputs.
- `validate` re-reads every processed output through the checks later stages use.

## Sources

| Source | Used for | Selection in `config/settings.yaml` | Licence | Retrieved | Status |
|---|---|---|---|---|---|
| OpenStreetMap extract for Rwanda, from Geofabrik | Roads, POIs, charging stations, power infrastructure, water, protected areas | `sources.osm`, `sources.osm_tags` | ODbL 1.0 | 2026-09-24 (OSM data up to 2026-09-23T20:22:04Z) | ingested |
| WorldPop population, 2025, 100 m, constrained, R2025A | Demand features, MCLP demand nodes | `sources.population` | CC BY 4.0 | 2026-09-24 | ingested |
| geoBoundaries gbOpen, Rwanda ADM2 (master) and ADM1 (province names only) | Districts; provinces and country derived from them | `sources.boundaries` | CC BY 4.0 | 2026-09-24 | ingested |
| energydata.info Rwanda Electricity Transmission Network (World Bank) | Cross-check of the OSM grid layers only | `sources.grid_cross_check` | CC BY 4.0 | 2026-09-24 | ingested |
| Copernicus DEM GLO-30 | Elevation and slope, reported only | `sources.elevation` | to verify when used | — | deferred (SPEC §2) |
| Existing public chargers (manual CSV) | Charging gap, backtest ground truth | `paths.manual_chargers_csv`, `sources.charger_csv` | per row (`source_url`) | per row (`date_retrieved`) | **missing**: the file does not exist yet |

### OpenStreetMap (Geofabrik)

- **URL:** https://download.geofabrik.de/africa/rwanda-latest.osm.pbf. On 2026-09-24 it redirected to `rwanda-260923.osm.pbf` (64.3 MB, SHA-256 `52a8bcfa6881e531…`); the manifest records the resolved URL.
- **Versioning:** rolling. `rwanda-latest` is replaced daily. Each download records the extract's replication timestamp (read from the PBF header) and SHA-256; a changed file is logged, never hidden.
- **Licence and credit:** Open Database License 1.0, https://www.openstreetmap.org/copyright. Required credit: **"© OpenStreetMap contributors"**, shown wherever OSM-derived data appears. Derived databases that are shared must stay under ODbL.
- **Read with:** pyosmium (D-001: pyrosm has no Windows wheels).
- **Known gaps:**
  - Mapping completeness varies by district; grid-layer completeness is measured by district in M3 (SPEC §4).
  - Only 7 `amenity=charging_station` objects are mapped (6 nodes, 1 way); 4 carry `socket:*` tags. No fuel station carries a `socket:*` tag.
  - Nyungwe and Volcanoes National Parks are tagged `boundary=national_park`, not the `boundary=protected_area` that SPEC §2 names, so they are **not** in `osm_protected_areas`. Akagera National Park is tagged `boundary=protected_area` and is included. Whether to add `boundary=national_park` is an open question for M2 ([decisions.md](decisions.md)).
  - Two `power` values are not OSM power types (`150kWh`, `11 kWh`). They are kept as mapped; which values count as grid evidence is decided in M3 (`features.grid_osm_tags`).
  - The extract includes features that cross the border. Features wholly outside the Rwanda envelope (253 `power` towers and portals, 1 water area) are dropped and counted (D-020).
  - Two `boundary=protected_area` relations outside Rwanda (in Uganda and Tanzania) are cut by the extract and cannot be assembled into areas; they are counted in the layer metadata.

### WorldPop

- **URL:** https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/RWA/v1/100m/constrained/rwa_pop_2025_CN_100m_R2025A_v1.tif (9.4 MB, SHA-256 `93bb39bbd91e2bd6…`, Last-Modified 2025-08-04). Catalogue entry: https://hub.worldpop.org/geodata/summary?id=75119.
- **Versioning:** fixed. A re-download that returns different bytes stops the fetch.
- **Licence:** CC BY 4.0, https://hub.worldpop.org/data/licence.txt.
- **Required citation:** Bondarenko M., Priyatikanto R., Tejedor-Garavito N., Zhang W., McKeen T., Cunningham A., Woods T., Hilton J., Cihan D., Nosatiuk B., Brinkhoff T., Tatem A., Sorichetta A. (2025). Constrained estimates of 2015-2030 total number of people per grid square at a resolution of 3 arc (approximately 100m at the equator) R2025A version v1. Global Demographic Data Project, funded by The Bill and Melinda Gates Foundation (INV-045237). WorldPop, School of Geography and Environmental Science, University of Southampton. DOI: 10.5258/SOTON/WP00839.
- **Known gaps:**
  - A modelled estimate (random-forest dasymetric redistribution), not a census count (SPEC §2).
  - Constrained: people are placed only in cells classified as built settlement. Every other cell is nodata, and nodata is not evidence of zero people.
  - R2025A is an alpha release; WorldPop states it may change over the coming year.
  - The "100 m" grid is 3 arc-seconds: about 92.7 m east-west by 92.2 m north-south at Rwanda's latitude (geodesic, WGS 84).

### geoBoundaries

- **URLs** (pinned to geoBoundaries commit `9469f09`; GitHub serves them from `media.githubusercontent.com`):
  - ADM2: https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/RWA/ADM2/geoBoundaries-RWA-ADM2.geojson (6.0 MB, SHA-256 `efe97dd43536b7de…`)
  - ADM1: https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/RWA/ADM1/geoBoundaries-RWA-ADM1.geojson (2.4 MB, SHA-256 `60a4fe6a0ff35e3d…`)
  - Discovered through the API: https://www.geoboundaries.org/api/current/gbOpen/RWA/ADM2/.
- **Versioning:** fixed (a commit-pinned URL).
- **Licence:** CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.
- **Required credit:** geoBoundaries: Runfola, D. et al. (2020), "geoBoundaries: A global database of political administrative boundaries", PLoS ONE 15(4): e0231866. ADM2 source: Open Data Rwanda (National Institute of Statistics of Rwanda), year represented 2012. ADM1 source: The Rwanda Geo Portal, year represented 2020.
- **Known gaps:**
  - ADM2 carries no province code (`shapeISO` is empty), so provinces are assigned from ADM1 by overlap (D-021). Every district's largest overlap is at least 99.12%.
  - ADM1 and ADM2 come from different sources and years, so their edges do not match exactly. Only ADM1's names and ISO codes are used; its geometry is not (CLAUDE.md).

### energydata.info transmission network

- **URL:** https://datacatalogfiles.worldbank.org/ddh-published/0042268/1/DR0052867/rwanda-electricity-transmission-network.zip (9.3 KB, SHA-256 `42905043bea56e49…`). Catalogue entry: https://energydata.info/dataset/rwanda-electricity-transmission-network.
- **Versioning:** fixed.
- **Licence:** CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Credit: World Bank Group, Rwanda Electricity Transmission Network, via energydata.info.
- **Use:** a cross-check for OSM `power=*` only (SPEC §2). It is **grid evidence**: it shows where lines were mapped, not capacity, transformer headroom or whether any site can connect.
- **Known gaps:**
  - Compiled around 2009 for the World Bank's AICD study from regional power-pool documents and project maps. Lines built since are missing.
  - 2 of the 38 lines were marked Planned at the time.
  - The source's `SOURCES` column names utilities and is not carried into the processed layer; neither is `PROJECT_NM`.

## Manual charger list: `data/manual/chargers.csv`

**Status on 2026-09-24: missing.** The file has not been created. The pipeline reports the source as `missing`, writes `data/processed/chargers_manual.meta.json` with `status: missing` and no data file, and creates no charger records. Later stages must treat existing public chargers from this source as unknown until it exists.

Filled by hand from public charger maps. The file is gitignored and never committed.

| Column | Type | Meaning | In the processed layer |
|---|---|---|---|
| `name` | text | Name of the charging site on the public source | No. Provenance only. |
| `lat` | decimal degrees | Latitude, WGS 84 (EPSG:4326) | As the point geometry |
| `lon` | decimal degrees | Longitude, WGS 84 (EPSG:4326) | As the point geometry |
| `source_url` | http(s) URL | Public page the row was taken from | Yes |
| `date_retrieved` | date, `YYYY-MM-DD` | When the row was checked | Yes |
| `operator_public_name` | text, may be blank | Operator as publicly listed | **Never** (SPEC §2) |

Rules checked on every run (all problems are reported together, with line numbers; no row is dropped or corrected):

- The header holds exactly the six columns above, each once. UTF-8, with or without a byte-order mark.
- `name`, `lat`, `lon`, `source_url` and `date_retrieved` are not blank; `operator_public_name` may be blank (unknown stays unknown).
- `lat` and `lon` are finite numbers inside the Rwanda envelope; `source_url` is an http(s) URL; `date_retrieved` is a real `YYYY-MM-DD` date.
- No two rows have the same coordinates (to 1e-6 degrees).
- One row per charging site, from public sources only. Do not copy data from charger-map services whose terms forbid reuse (SPEC §2 names one).

## Processed layers

All vector layers are GeoParquet (schema version 1.1.0, zstd compression) in **EPSG:4326**, sorted by their id, with a `<layer>.meta.json` file beside them that records the schema, row count, geometry types, bounds, a content fingerprint (`content_sha256`), the raw sources (with retrieval times, licences and credits) and extraction statistics. Metadata files contain no processing timestamp, so reprocessing the same raw files gives identical files. Distances, lengths and areas in these layers are computed in **EPSG:32735**.

Counts below are from the run of 2026-09-24.

### `admin_districts`

geoBoundaries ADM2, the master boundary: 30 districts, `MultiPolygon`. Validated as delivered (count, unique ids and names, `shapeGroup` RWA, `shapeType` ADM2, valid polygons, inside the Rwanda envelope); nothing is repaired.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `district_id` | string | yes | geoBoundaries `shapeID` |
| `district_name` | string | yes | geoBoundaries `shapeName` |
| `province_code` | string | yes | ISO 3166-2 province code (RW-01 to RW-05) |
| `province_name` | string | yes | Province name from geoBoundaries ADM1 |
| `province_overlap_share` | float64 | yes | Share of the district's area inside its province, EPSG:32735 (minimum 0.9912) |
| `area_km2` | float64 | yes | District area in km², EPSG:32735 |

Districts per province: City of Kigali 3, Eastern 7, Northern 5, Western 7, Southern 8. Total area 25,364.5 km².

### `admin_provinces`

5 provinces, `MultiPolygon`, dissolved from `admin_districts` so every edge matches.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `province_code` | string | yes | ISO 3166-2 code |
| `province_name` | string | yes | Name from geoBoundaries ADM1 |
| `district_count` | int64 | yes | Districts dissolved into the province |
| `area_km2` | float64 | yes | Area in km², EPSG:32735 |

### `admin_country`

Rwanda, one `MultiPolygon` dissolved from `admin_districts` (1 part, no interior rings).

| Column | Type | Required | Meaning |
|---|---|---|---|
| `country_iso3` | string | yes | `RWA` |
| `district_count` | int64 | yes | 30 |
| `province_count` | int64 | yes | 5 |
| `area_km2` | float64 | yes | Area in km², EPSG:32735 |

### `population_worldpop`

The WorldPop GeoTIFF, validated and copied **byte for byte** (it is not converted to points or polygons): EPSG:4326, 3 arc-seconds (0.000833333°), 2444 × 2150 pixels, one float32 band, nodata -99999, bounds 28.8617–30.8983 E, 2.8400–1.0483 S. 1,940,061 pixels hold a value and 3,314,539 are nodata. The sum of all values is 14,406,786 people (a modelled estimate). Its metadata file records the grid, nodata, pixel statistics, source and the limitations listed above.

### `osm_roads`

173,787 OSM ways tagged `highway=*`, as `LineString`, every highway value kept (the drivable classes are chosen in M2). Closed ways, such as roundabouts, stay lines. Trunk 473, primary 586, secondary 1,509, tertiary 626, unclassified 17,147, residential 56,647, plus tracks, paths and others.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | `way/<id>`, unique |
| `osm_type` | string | yes | `way` |
| `osm_id` | int64 | yes | OSM way id |
| `highway` | string | yes | OSM `highway` value |
| `ref` | string | no | Road number, e.g. RN1 |
| `surface` | string | no | OSM `surface` |
| `access` | string | no | OSM `access` |
| `motor_vehicle` | string | no | OSM `motor_vehicle` |

### `osm_pois`

7,632 nodes (`Point`) and areas (`MultiPolygon`) matching any tag in `sources.osm_tags.pois`. This is a broad superset of tagged places, **not** host types: which tags identify fuel stations, malls, supermarkets, hotels, logistics and industrial sites is decided in M2 (`candidates.host_osm_tags`). Restaurants are in this layer because they are tagged `amenity=*`, and they are never a host type. Open (non-closed) ways are not POIs and are counted, not kept.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | `node/<id>`, `way/<id>` or `relation/<id>` |
| `osm_type` | string | yes | node, way or relation |
| `osm_id` | int64 | yes | OSM id |
| `amenity` | string | no | OSM `amenity` |
| `shop` | string | no | OSM `shop` |
| `tourism` | string | no | OSM `tourism` |
| `office` | string | no | OSM `office` |
| `industrial` | string | no | OSM `industrial` |
| `landuse` | string | no | OSM `landuse` |
| `building` | string | no | OSM `building` |
| `man_made` | string | no | OSM `man_made` |
| `socket_tags` | string | no | JSON object of the feature's `socket:*` tags, keys sorted; null if none. Used to remove charger leakage in backtest mode. |
| `geometry_repaired` | bool | yes | True if the OSM area was invalid and made valid (D-020); none were on 2026-09-24 |

### `osm_charging_stations`

7 `amenity=charging_station` features: 6 `Point`, 1 `MultiPolygon`. An empty layer is allowed: "none mapped" is a fact about OSM, not an error.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | OSM object as type/id |
| `osm_type` | string | yes | node, way or relation |
| `osm_id` | int64 | yes | OSM id |
| `socket_tags` | string | no | JSON object of `socket:*` tags |
| `capacity` | string | no | OSM `capacity`, as mapped |
| `access` | string | no | OSM `access` |
| `geometry_repaired` | bool | yes | See `osm_pois` |

### `osm_power`

5,208 `power=*` features: 4,592 nodes (`Point`; 4,186 towers, 451 generators, 280 poles, 80 portals, …), 122 lines (`LineString`; `line`, `minor_line` and `cable` ways stay lines even when closed) and 494 areas (`MultiPolygon`; other closed ways and multipolygons, e.g. 43 substations, 37 plants). Route relations are not areas and are counted, not kept. Which values count as a substation or a line is decided in M3.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | OSM object as type/id |
| `osm_type` | string | yes | node, way or relation |
| `osm_id` | int64 | yes | OSM id |
| `power` | string | yes | OSM `power` value |
| `voltage` | string | no | OSM `voltage`, as mapped (volts, text) |
| `substation` | string | no | OSM `substation` |
| `geometry_repaired` | bool | yes | See `osm_pois` |

### `osm_water`

653 `natural=water` areas (`MultiPolygon`): 564 closed ways and 89 multipolygon relations.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | OSM object as type/id |
| `osm_type` | string | yes | way or relation |
| `osm_id` | int64 | yes | OSM id |
| `water` | string | no | OSM `water`, e.g. lake, river, reservoir |
| `geometry_repaired` | bool | yes | See `osm_pois` |

### `osm_protected_areas`

7 `boundary=protected_area` areas (`MultiPolygon`), including Akagera National Park and cross-border areas that touch Rwanda. Nyungwe and Volcanoes National Parks are **not** included (see Known gaps above). WDPA is not used (its terms forbid redistribution).

| Column | Type | Required | Meaning |
|---|---|---|---|
| `feature_id` | string | yes | OSM object as type/id |
| `osm_type` | string | yes | way or relation |
| `osm_id` | int64 | yes | OSM id |
| `protect_class` | string | no | OSM `protect_class` |
| `geometry_repaired` | bool | yes | See `osm_pois` |

### `grid_transmission_lines`

38 lines (`LineString`) from the energydata.info transmission network, 1,091.0 km in total. Voltages: 30 kV (22 lines), 70 kV (5), 110 kV (9), 220 kV (2). Grid evidence and a cross-check only.

| Column | Type | Required | Meaning |
|---|---|---|---|
| `line_id` | string | yes | `tx-` + first 12 hex digits of the SHA-256 of the line's WKB |
| `voltage_kv` | float64 | yes | Source `VOLTAGE_KV` |
| `status` | string | yes | Source `STATUS`: Existing (36) or Planned (2) |
| `from_name` | string | no | Source `FROM_NM`, a place name |
| `to_name` | string | no | Source `TO_NM`, a place name |
| `length_km` | float64 | yes | Length in km, EPSG:32735 |

### `chargers_manual`

Built from `data/manual/chargers.csv` when it exists. **Missing on 2026-09-24.**

| Column | Type | Required | Meaning |
|---|---|---|---|
| `charger_id` | string | yes | `csv-` + first 12 hex digits of the SHA-256 of `lat,lon` (6 decimals) |
| `source_row` | int64 | yes | Line number of the row in the CSV, for provenance |
| `source_url` | string | yes | Public page the row was taken from |
| `date_retrieved` | string | yes | `YYYY-MM-DD` |

Geometry: `Point`. `name` and `operator_public_name` are not in this layer.

## Checks every processed layer passes

On write and again on every read:

1. Required columns present and no unexpected columns.
2. Column types as declared; nothing is converted to fit.
3. Geometry present, not empty, valid, and of the declared types.
4. CRS is EPSG:4326.
5. Coordinates finite and within longitude/latitude range, and inside the Rwanda envelope (`ingest.rwanda_bbox`). OSM layers must touch it; the rest must lie inside it.
6. No duplicate ids; no nulls in required columns.
7. Not empty, unless the layer allows it (`osm_charging_stations` only).
8. Row count and content fingerprint match the metadata file, so a stale or edited layer is refused.

Source-level checks happen before a layer is built: SHA-256 against the raw manifest, expected unit counts (geoBoundaries), expected fields, raster grid, CRS, nodata and pixel values, PBF signature and header timestamp.

## Sources not used

- **WDPA protected areas:** its terms forbid redistribution, so SiteScout uses OSM `boundary=protected_area` instead (SPEC §2).
- **Charger-map services whose terms forbid copying:** not used (SPEC §2).
- **geoBoundaries ADM0:** not downloaded. The country outline is dissolved from ADM2 so that edges match.

## Where files go

All paths come from `config/settings.yaml`.

| Path | Contents |
|---|---|
| `data/raw/<source_id>/` | Downloaded source files, unchanged, each with `source.json` |
| `data/manual/chargers.csv` | The hand-filled charger list |
| `data/processed/` | Validated layers (GeoParquet in EPSG:4326, one GeoTIFF) with `.meta.json` files |
| `data/export/sitescout.json` | The export the front end reads (Milestone 8) |

Credits that a licence requires are shown wherever the data appears: in the README, the briefs and the front end.
