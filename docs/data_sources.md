# Data sources

Every external source SiteScout uses, with its URL, licence, retrieval date and known gaps. No dataset is committed to this repository: this page and `config/settings.yaml` are what you need to rebuild `data/`. If a source cannot be verified or accessed, the pipeline stops and reports it; it never substitutes another source silently.

**Status (Milestone 0):** the sources below are planned. URLs, licences, required credits and retrieval dates are verified and filled in during Milestone 1.

## Sources

| Source | Used for | Selection in `config/settings.yaml` | URL | Licence and required credit | Retrieved | Known gaps | Status |
|---|---|---|---|---|---|---|---|
| OpenStreetMap extract for Rwanda, from Geofabrik | Roads, POIs, host sites, charging stations, power infrastructure, water, protected areas | `sources.osm`, `sources.osm_tags` | to verify in M1 | to verify in M1 | — | Mapping completeness varies; grid-layer completeness is measured by district (SPEC §4, §7) | planned (M1) |
| WorldPop population | Demand features, MCLP demand nodes | `sources.population`: 2025, 100 m, constrained, release R2025A | to verify in M1 | to verify in M1 | — | A modelled estimate, not a census count (SPEC §2) | planned (M1) |
| geoBoundaries, Rwanda | Districts (ADM2, the master); provinces (ADM1) and country (ADM0) derived from them | `sources.boundaries` | to verify in M1 | to verify in M1 | — | Province assignment needs a method (see [decisions.md](decisions.md)) | planned (M1) |
| energydata.info Rwanda transmission network | Cross-check of the OSM grid layers only | `sources.grid_cross_check` | to verify in M1 | to verify in M1 | — | 2009 data (SPEC §2) | planned (M1) |
| Copernicus DEM GLO-30 | Elevation and slope, reported only | `sources.elevation` | to verify when used | to verify when used | — | — | deferred |
| Existing public chargers (manual CSV) | Charging gap, backtest ground truth | `paths.manual_chargers_csv`, `sources.charger_csv` | per row (`source_url`) | per source page | per row (`date_retrieved`) | Small sample: a few dozen chargers (SPEC §7) | filled by hand, never committed |

Credits that a licence requires are shown wherever the data appears: in the README, the briefs and the front end.

## Manual charger list: `data/manual/chargers.csv`

Filled by hand from public charger maps. The file is gitignored and never committed.

| Column | Type | Meaning | Exported or shown |
|---|---|---|---|
| `name` | text | Name of the charging site on the public source | No. Used only to match duplicates and trace provenance; outputs use generic labels. |
| `lat` | decimal degrees | Latitude, WGS 84 (EPSG:4326) | Yes, as a location |
| `lon` | decimal degrees | Longitude, WGS 84 (EPSG:4326) | Yes, as a location |
| `source_url` | URL | Public page the row was taken from | Kept for provenance; whether it is shown is decided in Milestone 8 under the company-name rule |
| `date_retrieved` | date, `YYYY-MM-DD` | When the row was checked | Kept for provenance |
| `operator_public_name` | text | Operator as publicly listed | Never (SPEC §2) |

Rules:

- Use public sources only, one row per charging site.
- Do not copy data from charger-map services whose terms forbid reuse (SPEC §2 names one).

## Sources not used

- **WDPA protected areas:** its terms forbid redistribution, so SiteScout uses OSM `boundary=protected_area` instead (SPEC §2).
- **Charger-map services whose terms forbid copying:** not used (SPEC §2).

## Where files go

All paths come from `config/settings.yaml` and are created by the pipeline.

| Path | Contents |
|---|---|
| `data/raw/` | Downloaded source files, unchanged |
| `data/manual/chargers.csv` | The hand-filled charger list |
| `data/processed/` | Cleaned layers (GeoParquet, EPSG:4326) |
| `data/export/sitescout.json` | The export the front end reads |
