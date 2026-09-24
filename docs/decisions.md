# Decisions

Each decision records its ID, date, decision, the alternatives considered and the reason. Questions that still need a decision are listed at the end, by milestone.

## D-001: Python 3.12, managed with uv

- **Date:** 2026-09-24
- **Decision:** Pin Python to 3.12 (`requires-python = ">=3.12,<3.13"` and `.python-version`), managed with uv.
- **Alternatives:** Python 3.11; Python 3.13 or later.
- **Reason:** CLAUDE.md requires 3.12. A resolution check on 2026-09-24 (Windows x86_64, binary wheels only, nothing installed) found:
  - On 3.12, every package the plan needs resolves from wheels: geopandas 1.1.4, pyproj 3.8.0, shapely 2.1.2, pyarrow 25.0.1, rasterio 1.5.1, h3 4.5.0, pulp 3.3.2, osmium 4.3.1, pydantic 2.13.5, PyYAML 6.0.3, pytest 9.1.1 and ruff 0.16.8.
  - On 3.11, the newest pyproj and rasterio releases cannot be installed from wheels, so 3.11 falls back to pyproj 3.7.2 and rasterio 1.4.4. CLAUDE.md's reason ("no Windows wheels for 3.11") holds for the current releases, not for all releases.
  - pyrosm cannot be installed from wheels on Windows on either version, because it and its dependency cykhash publish no Windows wheels. SPEC §2 allows pyrosm or osmium; osmium installs.

## D-002: Package layout and lockfile

- **Date:** 2026-09-24
- **Decision:** All code lives in the `sitescout` package under `src/`, built with uv's `uv_build` backend and installed in editable mode by `uv sync`. `uv.lock` is committed.
- **Alternatives:** A flat layout; `requirements.txt`.
- **Reason:** Tests and scripts import the installed package, which catches packaging mistakes early. The lockfile pins every version, so installs are reproducible.

## D-003: uv copies files instead of hard-linking them

- **Date:** 2026-09-24
- **Decision:** `[tool.uv] link-mode = "copy"` in `pyproject.toml`.
- **Alternatives:** uv's Windows default (hard links from its cache into `.venv`); moving the repository out of OneDrive; an environment variable pointing `.venv` elsewhere.
- **Reason:** The repository stays in a OneDrive-synced folder for now. With hard links, the files in `.venv` would be the same files as uv's cache, which OneDrive could then sync or offload. Copying avoids that without environment variables. The cost is a slower install and more disk space.

## D-004: Configuration design

- **Date:** 2026-09-24
- **Decision:**
  - Configuration comes only from `config/settings.yaml` and `config/weights.yaml`, parsed by a YAML loader that rejects duplicate keys.
  - Plain pydantic models validate both files. They reject unknown keys, never convert types (a quoted number or a boolean for a number is an error) and are frozen; lists become tuples, so nothing can change after loading.
  - Paths must be relative, use forward slashes and stay inside the repository; they are resolved against the repository root.
  - Overrides are passed only to `load_config(overrides=...)`. Each is logged at WARNING level and the result is validated like the files.
- **Alternatives:** pydantic-settings, which reads environment variables and `.env` files by default; dataclasses with hand-written checks; applying overrides with pydantic's `model_copy(update=...)`, which skips validation.
- **Reason:** CLAUDE.md requires YAML-only configuration, rejection of unknown keys, immutability and explicit, logged overrides. Plain PyYAML silently keeps the last of two duplicate keys, which could hide a changed weight.

## D-005: Pending parameters

- **Date:** 2026-09-24
- **Decision:**
  - A parameter that SPEC.md requires but does not define is written as `{pending: "<SPEC section and what is missing>. Decide in M<n>."}`. It never gets a default.
  - `require()` raises `PendingParameterError` for it. Using it as a boolean or iterating over it also raises.
  - Overrides can neither fill nor create one.
  - `scripts/check_config.py` logs every pending parameter, and a test pins the exact list: 16 parameters at Milestone 0.
  - Resolving one means giving it a value in YAML, updating the test and adding a decision here.
- **Alternatives:** Null values; defaults chosen by the implementer; leaving the keys out until they are needed.
- **Reason:** Approved on 2026-09-24. CLAUDE.md: "Unknown information stays unknown."

## D-006: Config values are pinned by tests

- **Date:** 2026-09-24
- **Decision:** Tests compare every defined value in both config files with the value in SPEC.md, or in CLAUDE.md for paths and the log level.
- **Alternatives:** Checking types and ranges only.
- **Reason:** CLAUDE.md: "Never silently change weights, radii or thresholds." A change now needs a test edit and a decision entry.

## D-007: Coordinate reference systems live in config

- **Date:** 2026-09-24
- **Decision:** `crs.storage: EPSG:4326` and `crs.metric: EPSG:32735` are in `config/settings.yaml`, pinned by tests.
- **Alternatives:** Constants in code.
- **Reason:** Every parameter SPEC.md defines goes in config (approved on 2026-09-24).

## D-008: Bonus tables live in weights.yaml under their component

- **Date:** 2026-09-24
- **Decision:** Host-type bonuses sit in `bonuses.host_commercial.host_type` and road-class bonuses in `bonuses.access.road_class`. The model allows no other placement, and the host-type table must cover exactly the host types in `settings.yaml` plus `none`.
- **Alternatives:** A separate bonus file; bonuses in `settings.yaml`.
- **Reason:** Approved on 2026-09-24. SPEC §5 gives the values but not their location.

## D-009: Dependencies for Milestone 0

- **Date:** 2026-09-24
- **Decision:** Runtime: pydantic (validating config now, and the export schema that SPEC §10 requires later) and PyYAML (reading YAML). Development: ruff (lint and format) and pytest (tests). Nothing else is installed.
- **Alternatives:** attrs or msgspec for validation; ruamel.yaml for parsing.
- **Reason:** Each dependency solves a named problem. The geospatial and solver packages arrive with the milestone that needs them.

## D-010: Logging

- **Date:** 2026-09-24
- **Decision:** Standard-library logging to stderr, configured only by entry points through `configure_logging`. Library modules call `logging.getLogger(__name__)`. Ruff rule `T20` rejects `print`.
- **Alternatives:** `print`; logging libraries such as loguru or structlog.
- **Reason:** CLAUDE.md: pipelines log instead of printing. No extra dependency is needed.

## D-011: Public repository and checks before every commit

- **Date:** 2026-09-24
- **Decision:** The repository is public during development. Before every commit, verify:
  - no secrets, credentials, API keys or tokens
  - no private data
  - no raw, processed, manual or generated datasets
  - no file over 5 MB
  - the Git author identity CLAUDE.md requires
  
  `tests/test_repo_hygiene.py` automates the file-size, ignore-rule, dataset-file and secret-pattern checks, and checks the README's mandatory statements and prohibited wording.
- **Alternatives:** A private repository until Milestone 10 (the original rule); the pre-commit framework or an external secret scanner.
- **Reason:** Approved on 2026-09-24. The checks run with the normal test suite, without extra tools.

## D-012: Company names and host labels

- **Date:** 2026-09-24
- **Decision:**
  - **Not allowed:** charging operator names, host business names, commercial brands and any company names in the product, exports, briefs or committed project data.
  - **Allowed:** public data-source names, software and tool names, attribution and licence information that a source requires, and company names that appear only as specification text in CLAUDE.md or SPEC.md.
  - Candidates and the UI use generic labels such as "Fuel station, Remera, Gasabo" plus the OSM ID. SPEC §3's `host_name` field holds that generic label, and Milestone 2 adds a `host_osm_id` field. SPEC.md is not modified.
  - Charger names and `operator_public_name` from the manual CSV are never exported or shown.
- **Alternatives:** Showing OSM names and brands; no labels at all.
- **Reason:** Approved on 2026-09-24. CLAUDE.md: never imply affiliation with any company.

## D-013: No datasets in git

- **Date:** 2026-09-24
- **Decision:** All of `data/` is gitignored: raw downloads, processed layers, the hand-filled `data/manual/chargers.csv` and the export. The repository holds the schemas and documentation needed to rebuild them ([data_sources.md](data_sources.md) and `config/`).
- **Alternatives:** Committing processed data or the charger CSV.
- **Reason:** Approved on 2026-09-24. The repository is public, the charger CSV contains operator names, and data licences differ.

## D-014: MIT licence

- **Date:** 2026-09-24
- **Decision:** Code and documentation are released under the MIT licence, copyright 2026 Abualgasim Ibrahim. Data sources keep their own licences and credits.
- **Alternatives:** No licence, which leaves the code view-only.
- **Reason:** Approved on 2026-09-24.

## D-015: README scope

- **Date:** 2026-09-24
- **Decision:** The README presents SiteScout as an independent portfolio project using public data. It contains no unsourced company-specific claims, which leaves out SPEC §1's context sentence about sites planned within 12 months. Prohibited wording appears only inside the exact required disclaimer, and a test checks this.
- **Alternatives:** Copying SPEC §1's context paragraph.
- **Reason:** Approved on 2026-09-24.

## D-016: Source locations, versioning and raw manifests

- **Date:** 2026-09-24 (Milestone 1)
- **Decision:**
  - Each download's URL, licence, licence URL and required credit live in `config/settings.yaml` (`sources.*.download`), verified on 2026-09-24 and pinned by `tests/test_config.py`.
  - Each source is `fixed` (the URL names one release: WorldPop R2025A, geoBoundaries at commit `9469f09`, the World Bank zip) or `rolling` (Geofabrik `rwanda-latest`).
  - `fetch` writes `data/raw/<source_id>/source.json`: URL, resolved URL, size, SHA-256, `Last-Modified`, `ETag`, licence, credit and the UTC retrieval time. A present file that matches its manifest is not downloaded again.
  - On a re-download, a changed `fixed` source raises `SourceChangedError` and the recorded file is kept; a changed `rolling` source is logged at WARNING with both hashes and replaces the old file. The OSM data timestamp comes from the PBF header.
  - Retrieval times are metadata. They are copied into layer metadata but never affect layer content, and layer metadata has no processing timestamp.
  - Downloads use the standard library (`urllib`), with a User-Agent that names the project.
- **Alternatives:** URLs as constants in code; downloading on every run; the `requests` package; recording only the file name.
- **Reason:** Resolves the Milestone 1 question about `rwanda-latest.osm.pbf` changing over time. SPEC §2 requires every source to be verified or reported, and CLAUDE.md requires reproducible, idempotent pipelines. Pinning a commit for geoBoundaries turns "current" into a fixed release.

## D-017: Dependencies for Milestone 1

- **Date:** 2026-09-24
- **Decision:** Runtime dependencies added, each for a named Milestone 1 problem:
  - **geopandas** (1.1.4): reading GeoJSON and shapefiles, reprojection, writing and reading GeoParquet.
  - **shapely** (2.1.2): geometry validity, types, bounds, dissolving districts into provinces and country.
  - **pyproj** (3.8.0): parsing and comparing CRS, refusing non-metric CRS, geodesic reference distances in tests.
  - **pyarrow** (25.0.1): the Parquet engine behind GeoParquet.
  - **rasterio** (1.5.1): reading and validating the WorldPop GeoTIFF (CRS, grid, nodata, pixel values).
  - **osmium** (pyosmium 4.3.1): reading the OSM PBF, building line and area geometries, filtering by tag in C++ (D-001: pyrosm has no Windows wheels).
  - **numpy** and **pandas**: used directly for raster statistics and table checks; both were already installed with geopandas and rasterio.
  
  pyogrio, the GeoJSON and shapefile reader, comes with geopandas.
- **Alternatives:** pyrosm (no Windows wheels); fiona (pyogrio is geopandas' default reader); GDAL command-line tools (not installable from wheels).
- **Reason:** CLAUDE.md: a new dependency must solve a named problem. All were resolved in D-001 as installable from wheels on Windows with Python 3.12. On the development machine, uv's first install of pyarrow failed several times because another process (probably a file scanner) locked files while they were being copied. Retrying `uv sync` fixed it; `uv add --no-sync` then `uv sync` avoids rolling back `pyproject.toml` if that happens again.

## D-018: Coordinate checks and the Rwanda envelope

- **Date:** 2026-09-24
- **Decision:**
  - `ingest.rwanda_bbox` (28.85 to 30.91 E, 2.85 to 1.03 S) is the union of the geoBoundaries ADM2 extent and the Geofabrik extract box, rounded outward to 0.01°. Boundaries, the population raster, transmission lines and CSV chargers must lie inside it; OSM features must touch it (OSM keeps ways that cross the border).
  - Every coordinate is also checked to be finite and within longitude/latitude range, which catches swapped or projected coordinates.
  - `sitescout.crs` holds the CRS helpers. Metric helpers accept only data in the storage CRS and refuse a metric CRS that is not projected in metres, so a distance can never be taken in degrees.
  - EPSG:32735 stays the metric CRS. Measured against WGS 84 geodesic distances, its scale error over Rwanda is +0.016% at the western border (28.86 E), +0.093% at 29.9 E and +0.199% at the eastern border (30.9 E), about 20 m per 10 km. A test holds it below 0.25%.
- **Alternatives:** A buffer around the country in metres (a number without a source); EPSG:32736 for the east (two metric CRS in one pipeline); a custom transverse Mercator centred on Rwanda (not what CLAUDE.md names).
- **Reason:** Resolves the Milestone 1 question about UTM zones 35 and 36: the distortion is small, measured and documented. The envelope values come from retrieved sources, not from a chosen tolerance.

## D-019: OSM extraction scope

- **Date:** 2026-09-24
- **Decision:**
  - Layers are selected by `sources.osm_tags` in settings.yaml. `roads: "highway=*"` keeps every highway value, because the drivable classes are pending until M2. `pois` is a broad superset of tagged places (`amenity`, `shop`, `tourism`, `office`, `industrial` with any value, plus industrial, commercial and retail land use and buildings, and `man_made=works`). It is not a list of host types; host classification is pending until M2 (`candidates.host_osm_tags`).
  - The PBF is read in two passes: one C++ key filter for "any value" selectors and one tag filter for exact key=value selectors. This keeps the 1.45 million plain buildings out of Python (a single pass through them took 44 s, the two filtered passes about 4 s each).
  - Nodes become points. Ways become lines for roads; for other layers, closed ways and multipolygon or boundary relations become areas. `power=line`, `minor_line` and `cable` ways stay lines even when closed, following the OSM convention; other closed `power` ways are areas.
  - Only the tags each layer needs are copied, as columns. `name`, `brand` and `operator` are never read (D-012). `socket:*` tags are kept as a JSON column for backtest leakage removal.
- **Alternatives:** Only the six host types' tags (would decide M2's pending parameter now); every tag as JSON (carries names and brands into processed data); pyrosm.
- **Reason:** SPEC §2 lists the OSM information SiteScout needs; the milestone brief forbids inventing host categories. A superset keeps M2's decision open without re-reading the PBF.

## D-020: OSM geometry problems are counted, never hidden

- **Date:** 2026-09-24
- **Decision:**
  - Invalid OSM areas are made valid with `shapely.make_valid`, keeping only polygonal parts, and flagged with `geometry_repaired = True`. The count goes into the layer metadata and the log. An area with nothing polygonal left is dropped and counted.
  - Areas libosmium cannot assemble, geometries it cannot build (missing node locations, invalid rings), open ways in area-only layers and non-area relations are counted per layer and logged.
  - Features wholly outside the Rwanda envelope are dropped and counted: on 2026-09-24, 253 power towers and portals (in Burundi, down to 3.37 S) and 1 water area. They come into the extract as nodes of cross-border ways and members of cross-border relations. Features that cross the envelope are kept whole, never clipped.
  - Boundaries, the population raster and the transmission lines are never repaired: a problem there stops that source.
- **Alternatives:** Dropping invalid areas silently; failing the whole OSM source on one invalid area; clipping every layer to the country outline.
- **Reason:** CLAUDE.md and the milestone brief: never repair important source problems silently. Crowd-sourced areas are sometimes invalid, and dropping a lake or a park would be worse than an explicit, flagged repair. On 2026-09-24 no area needed a repair.

## D-021: Provinces from ADM2 by majority overlap

- **Date:** 2026-09-24
- **Decision:**
  - geoBoundaries ADM2 has no province field. Each district is assigned the gbOpen ADM1 province it overlaps most, measured in EPSG:32735. The overlap must be a strict majority (more than 50%) of the district's area, and every province must receive a district, or ingestion stops.
  - From ADM1 only the name and ISO 3166-2 code (`shapeISO`, RW-01 to RW-05) are used. Province and country geometry is dissolved from ADM2, so every edge matches.
  - On 2026-09-24 the smallest overlap share was 99.12%. It is stored per district (`province_overlap_share`).
  - geoBoundaries ADM0 is not downloaded.
- **Alternatives:** A hand-typed district-to-province table (data written into code); ADM1 geometry (edges would not match ADM2); taking the province of each district's centroid (no measure of doubt).
- **Reason:** Resolves the Milestone 1 question about deriving ADM1. CLAUDE.md: ADM2 is the master; derive ADM1 and ADM0 from it.

## D-022: Processed data contract

- **Date:** 2026-09-24
- **Decision:**
  - Vector layers are GeoParquet 1.1.0 (zstd) in EPSG:4326, with a declared schema per layer in `sitescout/ingest/layers.py`: column names, types, required columns, geometry types, the id column and sort order.
  - `write_layer` validates before and after normalising types and order, writes to a temporary file and moves it into place. `read_layer` validates again and compares the row count and content fingerprint with the metadata file. Later stages read layers only through `read_layer`.
  - Each layer's `.meta.json` records its schema, sources, row count, geometry types, bounds, extraction statistics and a content fingerprint (SHA-256 over the columns in order and the geometry as WKB).
  - The WorldPop raster stays a GeoTIFF, copied byte for byte after validation, with its grid, nodata, statistics and limitations in `.meta.json`.
  - A missing source gets a metadata file with `status: missing` and no data file; reading it raises `SourceMissingError`.
- **Alternatives:** Converting the raster to points or polygons; clipping the raster to the ADM2 outline (changes edge pixels WorldPop published); GeoPackage; validating only on write.
- **Reason:** Resolves the Milestone 1 question that CLAUDE.md's "all sources in GeoParquet" does not fit rasters: vectors go to GeoParquet, the raster keeps its representation. CLAUDE.md: validate schemas between stages; pipelines are idempotent. Two runs on the same raw files give byte-identical files (tested).

## D-023: The manual charger list

- **Date:** 2026-09-24
- **Decision:**
  - `data/manual/chargers.csv` is checked strictly: exact header, required values, numeric coordinates inside the Rwanda envelope, http(s) URLs, real `YYYY-MM-DD` dates and no duplicate coordinates. Every problem is reported with its line number, and nothing is dropped or corrected.
  - `name` and `operator_public_name` stay in the CSV for provenance and are not carried into `chargers_manual`. The CSV line number (`source_row`) keeps the link back to them.
  - `charger_id` is derived from the coordinates rounded to 1e-6 degrees, so ids do not depend on row order.
  - A missing file is reported as `missing`, with no records created and any stale output removed. On 2026-09-24 the file does not exist.
- **Alternatives:** Keeping names in the processed layer and filtering them at export (a later stage could leak them); synthetic placeholder chargers (forbidden).
- **Reason:** SPEC §2: `operator_public_name` is never exported or shown. CLAUDE.md: no company names in outputs; unknown stays unknown.

## D-024: `charger_match_radius_m` stays pending until Milestone 3

- **Date:** 2026-09-24
- **Decision:** `sources.charger_match_radius_m` stays `pending`. Its reason now says to decide it in M3.
- **Alternatives:** Reusing SPEC's 1 km backtest hit radius (SPEC §7) or the 300 m candidate deduplication radius (SPEC §3).
- **Reason:** SPEC gives no value and no method for it. The two SPEC radii serve other purposes, and using either would be an invented default. Milestone 1 ingests the CSV and OSM chargers as separate layers and never combines them. The radius is first needed when the set of existing chargers is built for the charging-gap features and the backtest ground truth, in M3. The CSV does not exist yet, so the positional differences between the sources cannot yet be measured either.

## D-025: A failing source stops alone

- **Date:** 2026-09-24
- **Decision:** `scripts/ingest.py` runs fetch, process and validate for each source. A source that fails is reported with its exact error, its stale outputs from earlier runs are removed, and the other sources continue. Nothing is substituted. The exit code is 1 when any source failed and 0 when every source is ok or reported missing. Transmission lines get ids from the SHA-256 of their geometry; the source's `SOURCES` (which names utilities) and `PROJECT_NM` columns are not carried over.
- **Alternatives:** Stopping the whole run at the first failure; keeping old outputs after a failure.
- **Reason:** SPEC §2: stop and report rather than substitute. Keeping an old output after its source failed would let a later stage read data that no longer matches the raw files.

## Open questions

These need a decision before or during the milestone named. None has a default.

### Resolved in Milestone 1

- Rasters and "all sources in GeoParquet": D-022. Vectors go to GeoParquet. The raster stays a GeoTIFF, unclipped: the M0 proposal to clip it was dropped because clipping to the ADM2 outline would change edge pixels that WorldPop published.
- Deriving ADM1 from ADM2 without a province code: D-021.
- `rwanda-latest.osm.pbf` changing over time: D-016.
- The distortion of EPSG:32735 east of 30°E: D-018 (at most +0.199%).
- `charger_match_radius_m`: still pending, moved to M3 (D-024).

### Milestone 2

- `boundary=protected_area` misses Nyungwe and Volcanoes National Parks, which OSM tags `boundary=national_park` (checked on the 2026-09-23 extract). SPEC §2 names only `boundary=protected_area`. Should the candidate filter also use `boundary=national_park`? That would be a change to the SPEC tag list, so it needs Gasim's decision.
- `osm_protected_areas` also holds cross-border areas that only touch Rwanda. The candidate filter should test against the area geometry, so this matters only for reporting.
- Which OSM tags identify each host type (`candidates.host_osm_tags`, pending) and which road classes are drivable (`candidates.drivable_road_classes`, pending). `osm_pois` and `osm_roads` hold the superset these choices select from (D-019).
- `dist_town_m` assigns the profile, while CLAUDE.md says it is never scored. Proposed reading: never a weighted input, but allowed for assigning the profile.
- Deduplication keeps an industrial site over a hotel, yet the hotel's bonus is 5 points and the industrial site's is 0.
- SPEC §3 says a site without a host cannot be investigated, but keeps corridor points without a host. Should those be eligible for the 30?
- If generation gives fewer than 200 or more than 400 candidates, the run stops with an error until a rule is decided.
- Fuel stations tagged `socket:*` stay candidates in backtest mode; only their charger-related attributes are removed.

### Milestone 3

- The radius for matching manual CSV chargers to OSM chargers (`charger_match_radius_m`, pending; D-024).
- Do fuel stations tagged `socket:*` count as existing chargers in production mode? SPEC mentions them only for removal in backtest mode. On the 2026-09-23 extract no fuel station has a `socket:*` tag, but the rule is still needed.
- Two OSM `power` values are not power types (`150kWh`, `11 kWh`). `features.grid_osm_tags` (pending) should list the values that count, so these are ignored rather than repaired.
- The backtest ground truth is small. OSM maps 7 charging stations, and `data/manual/chargers.csv` does not exist yet. SPEC §7 expects "a few dozen". Until the CSV is filled, the M5 backtest has 7 known chargers at most, and its confidence intervals will be very wide.

### Milestone 4

- Applying log1p before a percentile rank does not change any rank, because log1p preserves order. Keep the step, drop it, or use it somewhere else?
- Does "percentile rank within Rwanda" rank a candidate among the candidates or against a national reference?
- In backtest mode the charging-gap component is the same for every candidate. Its value depends on tie handling and on how "no charger" distances are stored. Because urban weights this component at 0.15 and corridor at 0.25, that value shifts corridor scores against urban ones by up to 10 points.
- How the confidence factors combine into Medium or Low.

### Milestone 5

- The definition of Recall@30.
- What the bootstrap resamples.
- Which weights the ±20% stability test changes (profile weights, feature weights, bonuses), and whether the 70% target applies to each change or to the average.
- The network section of `reports/evaluation.md` is added in Milestone 6 and the grounding section in Milestone 7.

### Milestone 6

- Is the top-50% eligibility cut taken across all candidates or within each profile?
- SPEC §8 calls sⱼ a "normalized site score" without defining it. Proposal: score / 100, since min-max normalization is not allowed.
- In production mode, is demand renormalized to sum to 1 after demand near existing chargers is down-weighted?
- If the solver reaches the 300 s limit, the run reports the solver status and does not call the result exact.

### Milestone 7

- SPEC §9's name for the brief uses a word CLAUDE.md prohibits outside the disclaimer. Proposal: "Site Evidence Brief".
- SPEC §9 allows an LLM to write the Opportunity paragraph, while CLAUDE.md says reports are built from templates. Environment variables and `.env` files are disabled, so an API key would also need an approved source.

### Milestone 8

- Synthetic exports exist only as labelled test fixtures, never as pipeline output.
- `app/prototype.html`, the design reference, is not in the repository yet.
