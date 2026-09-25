# Architecture

SiteScout is a batch pipeline in Python that writes one JSON export, plus a front end that only displays that export. This page covers the stages, what passes between them and the rules every stage follows. The method itself is in [SPEC.md](SPEC.md); the reasons behind each choice are in [decisions.md](decisions.md).

## Pipeline

| Stage | SPEC | Milestone | Planned module | Output |
|---|---|---|---|---|
| Ingest public sources | §2 | M1 | `sitescout/ingest/` | validated layers in `data/processed/` |
| Generate candidates | §3 | M2 | `sitescout/candidates.py` | 200 to 400 candidates with a generic host label, OSM ID, district, province and profile |
| Compute features | §4 | M3 | `sitescout/features/` | one feature row per candidate, in backtest and production mode |
| Score and rate confidence | §5, §6 | M4 | `sitescout/scoring.py`, `sitescout/confidence.py` | component scores, overall score, confidence level and reasons |
| Evaluate | §7 | M5 to M7 | `sitescout/evaluation.py` | `reports/evaluation.md` |
| Select the network | §8 | M6 | `sitescout/optimize.py` | exact MCLP, greedy and Top-30 selections with marginal coverage |
| Evidence and briefs | §9 | M7 | `sitescout/evidence.py`, `sitescout/briefs.py` | 30 site briefs and the grounding check |
| Export | §10 | M8 | `sitescout/export.py` | `data/export/sitescout.json` |
| Front end | §10 | M8 | `app/` | renders the export |

After Milestone 2, `sitescout/config.py`, `sitescout/logging_setup.py`, `sitescout/crs.py`, the `sitescout/ingest/` package and `sitescout/candidates.py` exist. Every other module is created in its own milestone.

```text
public sources -> data/raw/ -> ingest -> data/processed/ (GeoParquet in EPSG:4326, one GeoTIFF)
  -> candidates -> features -> scoring + confidence -> evaluation -> reports/evaluation.md
  -> network selection -> evidence + briefs -> export -> data/export/sitescout.json
  -> app/ (display only)
```

## Milestone 1: ingestion

```text
config/settings.yaml (URLs, licences, tags, envelope)
        |
  fetch (network)          data/raw/<source_id>/<file> + source.json (SHA-256, retrieval time)
        |
  process (offline)        source checks -> clean / normalise -> CRS check -> layer checks
        |
  data/processed/<layer>.parquet | population_worldpop.tif  +  <layer>.meta.json
        |
  validate / read_layer    the same checks again, plus the content fingerprint
```

| Module | Responsibility |
|---|---|
| `sitescout/crs.py` | Parse and compare CRS; reproject to storage; project to the metric CRS for distances, lengths and areas; refuse anything that would measure in degrees |
| `ingest/acquire.py` | Download each configured source into `data/raw/<source_id>/` with a manifest; reuse present files; fixed sources must not change, rolling changes are logged (D-016) |
| `ingest/metadata.py` | Raw manifests, deterministic JSON, atomic writes, content fingerprints |
| `ingest/schema.py` | Layer schemas and table checks: columns, types, required values, duplicate ids, empty tables |
| `ingest/geometry.py` | Geometry checks: missing, empty, invalid, wrong type, out-of-range or outside-Rwanda coordinates; the one explicit repair for OSM areas |
| `ingest/layers.py` | The schema of every processed vector layer; `write_layer` and `read_layer`, which validate on both sides |
| `ingest/boundaries.py` | geoBoundaries ADM2 (master), province assignment, dissolved provinces and country |
| `ingest/raster.py` | WorldPop GeoTIFF checks and the byte-identical processed copy |
| `ingest/osm.py` | pyosmium extraction of roads, POIs, charging stations, power, water and protected areas |
| `ingest/grid.py` | energydata.info transmission lines (grid-evidence cross-check) |
| `ingest/chargers.py` | The hand-filled charger CSV, or a `missing` record when it does not exist |
| `ingest/pipeline.py` | Runs fetch, process and validate per source; a failing source stops alone (D-025) |
| `scripts/ingest.py` | Parses arguments and calls `ingest/pipeline.py` |

Stage contracts in Milestone 1:

- **Deterministic.** Processing reads only `data/raw/` and config. Rows are sorted by id, column types are fixed by the schema, and metadata has no processing timestamp, so the same raw files give byte-identical outputs. Retrieval times live in the raw manifests (D-016).
- **Idempotent.** Outputs are replaced atomically, never appended to. A present raw file is not downloaded again. A source that fails, or becomes missing, leaves no stale output.
- **Validated between stages.** Each processed layer is checked when it is written and again when it is read (D-022). Later stages read processed data only through `read_layer` and `read_population`.
- **Nothing hidden.** Skipped, dropped or repaired features are counted in the layer metadata and logged (D-020). A missing source is `missing`, never an empty stand-in.
- **CRS.** Stored in EPSG:4326. Lengths and areas in the layers (`area_km2`, `length_km`, `province_overlap_share`) are computed in EPSG:32735 (D-018).

## Milestone 2: candidate generation

```text
data/processed/ (admin_districts, admin_country, osm_pois, osm_roads, osm_water,
                 osm_protected_areas; each read through read_layer)
  -> hosts (host_osm_tags, priority)          -> corridor points (trunk/primary, 10 km)
  -> snap corridor points to hosts (2 km)     -> deduplicate (300 m, host priority)
  -> filters (500 m drivable road, water, protected areas incl. national parks)
  -> district and province                    -> 595 eligible candidates
  -> budget (D-031): ADM2 quotas (largest remainder) + priority-ordered spacing -> 300
  -> data/processed/candidates_eligible.parquet (all, with `selected`)
     data/processed/candidates.parquet (the 300), or a stop with CandidateBudgetError
```

| Module | Responsibility |
|---|---|
| `sitescout/candidates.py` | Every step above (D-029, D-031). Metric work in EPSG:32735 through `sitescout.crs`; output in EPSG:4326 through `write_layer`. |
| `scripts/candidates.py` | Loads the config and calls `run_candidates`; exit code 1 when generation stops |

Candidate generation reads processed layers only, uses no randomness and never reads the existing chargers. The same processed layers give byte-identical output (tested). The `candidates` schema lives with the other layer schemas in `ingest/layers.py`, so later stages read it through `read_layer`.

## Milestone 3: feature engineering

```text
data/processed/ (candidates, admin_districts, admin_country, osm_roads, osm_pois,
                 osm_charging_stations, osm_power, osm_places, chargers_manual or its
                 `missing` record, population_worldpop.tif; each re-checked on read)
  for each mode, from the inputs alone (D-038):
    production: existing chargers = public OSM stations + socket-tagged fuel + CSV,
                merged within 50 m (D-034)
    backtest:   no chargers; every charger object removed from POIs and power first
  -> demand: population within 1/5/10 km (pixel centres, metres; D-036)
  -> access: dist_road_m and road_class from M2; dist_trunk_m (D-032)
  -> host / commercial: POI counts by OSM key within 1/3 km (D-033)
  -> charging gap: dist_charger_m, chargers within 10/25 km
  -> grid evidence: substation and line distances; district completeness proxy (D-033)
  -> reported only: Kigali city centre and town distances (D-035); border share (D-036)
  -> bounds checks for both modes
  -> data/processed/features_production.parquet and features_backtest.parquet
     (+ .meta.json), or a stop that writes neither
```

| Module | Responsibility |
|---|---|
| `features/nearest.py` | Nearest distance, pairs and counts within an inclusive radius, points on the surface of areas; metric geometries only |
| `features/demand.py` | Population sums from the WorldPop raster; the border diagnostic |
| `features/pois.py` | POI types, exclusions and counts |
| `features/chargers.py` | The existing chargers for each mode, and merging within the match radius |
| `features/grid.py` | Substation and line distances; the grid-completeness proxy |
| `features/build.py` | Loads the inputs, builds both modes, checks bounds, writes both layers with their metadata |
| `scripts/features.py` | Loads the config and calls `run_features`; exit code 1 when feature engineering stops |

Values are raw, in natural units; normalisation, bonuses, weights and profiles belong to scoring (Milestone 4). OSM place nodes (`osm_places`) are a Milestone 1 layer, added in Milestone 3 from the same PBF.

## Rules every stage follows

- **Configuration.** Stages receive a loaded `Config` object. They never read `config/` files or environment variables themselves.
- **Pending parameters.** A parameter that SPEC.md requires but does not define is marked `pending` in `config/settings.yaml`. A stage that needs it calls `require(...)`, which stops with an error naming the parameter until the value is decided and recorded.
- **Coordinates.** Data is stored in EPSG:4326. Distances and areas are computed in EPSG:32735, never in degrees.
- **Stage contracts.** Each stage validates its inputs, writes outputs with a declared schema and is idempotent: the same inputs and config give the same outputs.
- **Modes.** Backtest and production mode are an explicit argument, never a global switch. Backtest mode removes existing chargers from every feature, including POI and host features.
- **Numbers.** Python computes every number. The front end filters, sorts and displays; it never computes scores, coverage or selection. LLMs never produce numbers.
- **Logging.** Library modules call `logging.getLogger(__name__)`. Only entry points configure logging, to stderr. Ruff rule `T20` rejects `print`.
- **Scripts.** Files in `scripts/` only parse arguments and call `src/sitescout/`.
- **Synthetic data.** Allowed only when labelled synthetic in code, UI and docs. The export records `data_status` as `pipeline` or `synthetic`.
- **Names.** Outputs use generic host labels plus OSM IDs, never business, brand or operator names.
- **Data in git.** Nothing under `data/` is committed. [data_sources.md](data_sources.md) and `config/` describe how to rebuild it.

## Configuration

`config/settings.yaml` holds every parameter SPEC.md defines, grouped by stage, plus the paths and log level. `config/weights.yaml` holds the feature weights, profile weights and bonus tables. Both are loaded by `sitescout.config.load_config()`:

- YAML is parsed with a loader that rejects duplicate keys.
- Pydantic models reject unknown keys, refuse to convert types (a quoted `"30"` stays an error) and are frozen once loaded. Lists become tuples.
- Paths must be relative and are resolved against the repository root, not the working directory (`Config.resolve`).
- Sensitivity runs pass explicit overrides such as `{"settings.optimization.lambda": 0.02}`. Each is logged, the result is validated like the files, and overrides can never fill a pending parameter.
- `Config.pending()` lists every pending parameter. `Config.snapshot()` returns both files as JSON-ready data for the export's config snapshot.
- `scripts/check_config.py` validates both files and logs a summary with every pending parameter.

## Repository layout

```text
config/                 settings.yaml, weights.yaml
src/sitescout/          config.py, logging_setup.py, crs.py, ingest/, candidates.py (later: one module per stage)
scripts/                check_config.py, ingest.py, candidates.py (later: one entry point per stage)
tests/                  pytest suite; SYNTHETIC fixtures are built at test time by tests/synthetic.py
docs/                   SPEC.md, architecture.md, decisions.md, data_sources.md
data/                   raw/, manual/, processed/, export/ (gitignored, created by the pipeline)
reports/                evaluation.md and site briefs (from Milestone 5)
app/                    front end (Milestone 8)
```
