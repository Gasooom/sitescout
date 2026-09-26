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

## D-026: National parks are protected areas

- **Date:** 2026-09-24 (Milestone 2)
- **Decision:** `osm_protected_areas` selects `boundary=national_park` as well as `boundary=protected_area` (`sources.osm_tags.national_park`), with a `boundary` column recording which tag each area has. The candidate filter's protected-area exclusion therefore covers Nyungwe and Volcanoes National Parks. Features tagged only `leisure=nature_reserve` are not added.
- **Alternatives:** Keeping `boundary=protected_area` only; adding every `leisure=nature_reserve`; WDPA (its terms forbid redistribution, SPEC §2).
- **Reason:** SPEC §3's rule is to drop candidates "inside water or protected areas", and SPEC §2 names OpenStreetMap as the protected-area source. In OSM, `boundary=national_park` is an alternative tag for the same category: both Rwandan national parks carry `protect_class` 2 and 4, the IUCN protected-area categories that `boundary=protected_area` uses. Including them applies the existing rule to the existing source; it adds no new rule and does not change SPEC.md. In the 2026-09-23 extract, the two parks cover 1,028.7 km² (Nyungwe) and 159.5 km² (Volcanoes) inside Rwanda, and a trunk road runs through Nyungwe. `leisure=nature_reserve` alone is a different OSM concept with no protect_class, covering about 0.1 km² inside Rwanda, so it is left out. Resolves the Milestone 1 open question.

## D-027: OSM tags for each host type

- **Date:** 2026-09-24 (Milestone 2, chosen by Gasim from the options presented)
- **Decision:** `candidates.host_osm_tags`:
  - fuel: `amenity=fuel`
  - mall: `shop=mall`
  - supermarket: `shop=supermarket`
  - logistics: `building=warehouse`, `industrial=depot`, `industrial=intermodal_freight_terminal`
  - industrial: `landuse=industrial`, `man_made=works`
  - hotel: `tourism=hotel`

  A POI matching several host types takes the highest in `dedup.host_priority`. Config checks that every tag appears under one host type only and falls inside the M1 POI extraction scope. Restaurants, charging stations and other amenities are never hosts.
- **Alternatives:** Adding `building=industrial` (449 individual buildings, which gives more candidates still); adding motels, guest houses, department stores or convenience shops.
- **Reason:** SPEC §3 names the six host types but not their tags. These are the standard OSM tags for each type. On the 2026-09-23 extract they select 689 hosts: fuel 173, hotel 280, industrial 137, supermarket 80, mall 14, logistics 5. 17 of them lie outside Rwanda.

## D-028: Drivable road classes

- **Date:** 2026-09-24 (Milestone 2, chosen by Gasim)
- **Decision:** `candidates.drivable_road_classes` are the OSM car-road values: motorway, trunk, primary, secondary and tertiary (with their `_link` roads), unclassified, residential, living_street, service and road. Tracks, paths, footways, steps, pedestrian ways, cycleways and roads under construction are not drivable. Config checks that the corridor road classes are drivable.
- **Alternatives:** Adding `highway=track` (3,076 km of mostly unpaved rural tracks).
- **Reason:** SPEC §3 drops points more than 500 m from a drivable road without listing the classes. A charging site needs access by car.

## D-029: Candidate generation method

- **Date:** 2026-09-24 (Milestone 2)
- **Decision:** `sitescout/candidates.py` follows SPEC §3 in order: hosts, corridor points, snapping, deduplication, filters. Where SPEC is silent:
  - **Host location:** a node's own point, or for an area a point on its surface (computed in EPSG:32735, so it is inside the area). Hosts outside the Rwanda outline (`admin_country`) are left out before snapping, so no candidate can move abroad.
  - **Corridor points:** the trunk and primary network is clipped to Rwanda, noded and merged into chains between junctions. Points sit at 5 km, 15 km, 25 km, … along each chain (half a spacing from each junction), so consecutive points are 10 km apart along a chain and across a junction. A chain shorter than 5 km gets no point. Dual carriageways give near-duplicate points, which deduplication merges.
  - **Snapping:** to the nearest host within 2 km, of any host type; ties go to the lower OSM id. The candidate then sits at the host's point.
  - **Deduplication:** greedy and not transitive. Candidates are taken in host-priority order (SPEC §3), then host before snapped corridor point, then candidate id; each is kept unless a kept candidate lies within 300 m. `merged_count` records how many it absorbed. A clustering alternative would collapse a street of hotels 250 m apart into one candidate.
  - **Filters** run after deduplication, as SPEC §3 orders them. The number of candidates absorbed by a candidate that was later filtered out is recorded (`merged_into_dropped`; 0 on real data).
  - **Output:** `candidate_id` is derived from the host's OSM id, or from the coordinates of a corridor point without a host. `host_name` is a generic label with the district, e.g. "Fuel station, Gasabo" (D-012); the sector name in D-012's example is not in any ingested source. District and province come from `admin_districts`; a point on a shared edge takes the lowest district id. No randomness is used.
  - Existing chargers are not read, so no candidate is dropped for being near one (CLAUDE.md).
- **Alternatives:** Points from each chain's start (0 km, 10 km, …); spreading points evenly per chain; clustering deduplication; filtering before deduplication.
- **Reason:** Each choice keeps SPEC §3's numbers (10 km, 2 km, 300 m, 500 m) and order, is deterministic and is tested.

## D-030: The count stops the run; the profile is deferred to M4

- **Date:** 2026-09-24 (Milestone 2, chosen by Gasim)
- **Decision:**
  - When the number of candidates is outside `candidates.target_count` (200 to 400), the run logs the full breakdown, removes any old candidates layer, writes nothing and exits with CandidateCountError. Since D-031 this check applies to the budget's selection, and the budget itself stops the run when too few candidates are eligible.
  - The profile rule's parameters (`candidates.profile.town_radius_m`, `candidates.profile.kigali_radius_m`, `features.town_centres`) stay pending and move to M4, where the profile is first used for scoring. Until then `profile` is null in the candidates layer.
- **Alternatives:** Keeping all candidates and warning; a selection rule chosen by the implementer; radii and town centres proposed without a source.
- **Reason:** SPEC §3's own rules give 595 candidates on the 2026-09-23 extract. SPEC and CLAUDE.md require 200 to 400 but give no rule for choosing among them. Limiting hosts to those within 2 km of a trunk or primary road still gave about 511 in an exploratory run. CLAUDE.md: flag an inconsistency in the specification rather than force the numbers. The profile radii have no value in SPEC, and inventing them is not allowed.

## D-031: Candidate budget: 300 of the eligible candidates, by district quota and spacing

- **Date:** 2026-09-24 (Milestone 2, specified and approved by Gasim)
- **Decision:** After every SPEC §3 step (generation, snapping, deduplication, road, water and protected-area filters), a budget stage selects exactly `candidates.budget.size` = 300 candidates. Config checks that the budget lies within `target_count` (200 to 400).
  1. **Quotas.** The 300 seats are shared among the ADM2 districts in proportion to each district's eligible candidates: floor(300 × n / N) each, with the seats left over going to the largest remainders and equal remainders to the lower `district_id`. The arithmetic is exact integers. The allocation unit (ADM2) is fixed, not a parameter.
  2. **Order within a district:** host priority (SPEC §3), then origin (host, corridor_snapped, corridor), then `candidate_id`.
  3. **Spacing.** For each district, the district's own pairwise distances (EPSG:32735) are tried from largest to smallest. The first radius at which greedy spacing keeps at least the quota is used; greedy spacing walks the order and keeps a candidate unless an already-kept one is within the radius. If that keeps more than the quota, the first ones in order are taken. No binary search over a continuous range is used.
  4. Fewer eligible candidates than the budget stops the run with CandidateBudgetError and writes nothing.
  5. `candidates_eligible` keeps every eligible candidate with a `selected` flag; `candidates` holds the 300. Both metadata files record the eligible and selected counts, the target, each district's quota, selected count and radius, and the method.

  Spacing is not enforced across district borders. Hostless candidates are kept; no host requirement is added. The budget reads no chargers, population, demand, grid evidence, feature or score, and uses no randomness. That it can incidentally remove candidates near existing chargers is accepted.
- **Alternatives:** Loosening the 200 to 400 range, removing hotels or corridor points, or corridor points on trunk roads only (all rejected by Gasim); a 5 km or 10 km grid round-robin (in a simulation the City of Kigali fell from 23% of candidates to 6% or 4%); district quotas with pure priority inside each district (no spread within a district); random sampling.
- **Reason:** SPEC §3's rules give 595 eligible candidates on the 2026-09-23 extract, and SPEC and CLAUDE.md require 200 to 400. 300 sits well inside the range. Proportional district quotas keep the eligible universe's geographic distribution; spacing spreads each district's candidates; host priority decides when candidates compete, as in deduplication. The 595 remain auditable. SPEC.md is not changed.

## D-032: The trunk corridor for `dist_trunk_m`

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:** `features.trunk_road_classes` is `[trunk, trunk_link]`. `dist_trunk_m` is the distance in EPSG:32735 to the nearest OSM road of those classes. Roads are not clipped to Rwanda, as for `dist_road_m`. Config checks that the classes are drivable (D-028).
- **Alternatives:** `trunk` only; `trunk` and `primary` (the roads M2 places corridor points on).
- **Reason:** SPEC §4 says "distance to trunk corridor" without listing classes. The feature's name and SPEC §5's separate trunk (10) and primary (5) road-class bonuses both point to trunk roads; a trunk link is part of the trunk road. Rwanda has no motorway in OSM.

## D-033: POI types and grid-evidence power values

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:**
  - **POI types** (`features.poi_types`): one type per OSM key: `amenity=*`, `shop=*`, `tourism=*`, `office=*` and `industrial=*`. `poi_1km` and `poi_3km` count the distinct POIs of any type within the radius (inclusive), so a POI with two keys counts once there; `poi_<type>_1km` and `poi_<type>_3km` give the reported breakdown. Land-use areas, `building=*` rows and `man_made=works` in `osm_pois` are not POIs. An area POI is represented by a point on its surface, as M2 represents area hosts.
  - `amenity=charging_station` is excluded from POI counts in both modes (`features.poi_exclude`). Config requires it there.
  - A candidate's own host (`host_osm_id`) is never counted for that candidate. Other hosts nearby are counted.
  - **Grid evidence** (`features.grid_osm_tags`): substations are `power=substation`; lines are `power=line`, `power=minor_line` and `power=cable`. The completeness proxy counts only `line`, `minor_line`, `cable`, `substation`, `transformer`, `tower`, `pole`, `portal`, `plant`, `generator` and `connection`. No other `power` value is ever counted. Config accepts only exact `power=<value>` tags and requires the substation and line values to be in the completeness list.
- **Alternatives:** A hand-picked list of commercial values; every `osm_pois` row; counting charging stations in production and removing them in backtest (SPEC §5's literal reading); counting the candidate's own host; counting every `power=*` value; `power=line` only for lines.
- **Reason:** SPEC §4 counts POIs "by type" and measures distance to "the nearest mapped substation and line" without listing either. Keys are the plainest types OSM offers. Charging stations are measured by the charging-gap component; counting them as POIs would count them twice and open a leakage path. The host-type bonus already rewards a candidate's own host. On the 2026-09-23 extract, two charging-station nodes also carry `power=150kWh` and `power=11 kWh` (charger capacities), so counting every `power` value would put chargers into grid evidence.

## D-034: Existing chargers in production and backtest mode

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:**
  - **Production:** OSM charging stations, except those tagged `access=private` or `access=no` (`features.chargers.exclude_access`); fuel stations tagged `socket:*` (`features.chargers.fuel_sockets_count_as_chargers: true`); and the rows of `data/manual/chargers.csv` when it exists. While the CSV is missing, OSM is the only source and every feature layer's metadata says so. Nothing stands in for it.
  - `sources.charger_match_radius_m` = **50 m**. Records within 50 m of each other, directly or through a chain of such records, are one charging site, within one source and between sources. A site takes the position of its first record in a fixed order: OSM charging stations, then fuel stations, then CSV rows, then by id. An area charger is represented by a point on its surface. The 50 m is a project data-matching choice, not a claim that 50 m defines a charging site.
  - **Backtest:** the charger set is empty and is built without reading any charger source: `dist_charger_m` is null and `chargers_10km` and `chargers_25km` are 0 for every candidate. Every charger object (all charging stations, whatever their access, and socket-tagged fuel stations) is also removed from the POI and power inputs before anything is computed. No backtest value is copied or patched from a production value.
  - `dist_charger_m` is null, never a stand-in distance, when there is no charger.
- **Alternatives:** Counting OSM objects without merging; greedy, non-transitive merging as in D-029; a centroid for merged records; counting private chargers; keeping `charger_match_radius_m` pending.
- **Reason:** SPEC §2 combines the manual CSV with OSM chargers but gives no matching radius (D-024). On the 2026-09-23 extract, OSM maps one site as three nodes 10 m apart (`node/8816058906`, `node/8816058907`, `node/8816068253`); without merging, that site would count three times in `chargers_10km`. The next-nearest pair of chargers is 407 m apart, so 50 m merges the one cluster and nothing else: 7 records become 5 sites. SPEC §2 names public chargers. SPEC §5 removes chargers from every feature in backtest mode.

## D-035: Town and city centres, and the Kigali city centre

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:**
  - A new OSM layer, `osm_places`, holds `place=city` and `place=town` nodes (`sources.osm_tags.places`). It is extracted from the same PBF as every other OSM layer; no other source is downloaded. Ways and relations tagged `place` are not taken, and names are not read.
  - **Town and city centres** (`features.town_centres`): every `place=city` and `place=town` node inside Rwanda. `dist_town_m` is the distance to the nearest one. On the 2026-09-23 extract: 11 cities and 98 towns; 1 town node lies outside Rwanda and is not used.
  - **Kigali** (`features.kigali_cbd`): `node/60485579`, the `place=city` node with `capital=yes`, labelled "Kigali city centre as mapped in OSM". `dist_kigali_cbd_m` is the distance to it. The run stops if that node is missing from the extract or is not a `place=city` node inside Rwanda. By the geoBoundaries ADM2 outlines the node lies in **Gasabo** district, not in Nyarugenge. It is where OSM places the city, not an official CBD boundary.
  - The profile radii (`candidates.profile.town_radius_m`, `kigali_radius_m`) stay pending until M4. `dist_town_m` and `dist_kigali_cbd_m` are reported only and never scored.
- **Alternatives:** A coordinate from another public source; the centroid of Nyarugenge district; keeping `features.town_centres` deferred to M4 (D-030).
- **Reason:** SPEC §4 lists both distances as features without defining the places. OSM is SPEC §2's source for places; one node pinned by id is reproducible and can be checked on every run. The Kigali node was identified once by its tags during M3 (a one-off check that read its name); the pipeline itself never reads names (D-019). The extract also has two unnamed `place=city` nodes near Musanze; they are kept, because the rule counts every city node.

## D-036: Population within a radius, and the border diagnostic

- **Date:** 2026-09-25 (Milestone 3; the diagnostic chosen by Gasim)
- **Decision:**
  - `pop_1km`, `pop_5km` and `pop_10km` sum the WorldPop pixel values whose pixel **centre** lies within the radius (inclusive). Pixel centres are computed from the raster's grid in EPSG:4326 and projected to EPSG:32735; the distance is measured in metres. Values are summed in float64, ring by ring from the smallest radius outward, so a larger radius never holds fewer people. A nodata pixel adds 0. The raster is not clipped or resampled.
  - `outside_rwanda_share_10km`: the share of the candidate's 10 km circle (a 256-segment polygon in EPSG:32735) that lies outside `admin_country`. Exactly 0 when the circle is inside Rwanda. Reported only: it never changes a population value, a score, a confidence level or a selection.
- **Alternatives:** Weighting each pixel by the share of its area inside the circle (it needs a new dependency such as exactextract); treating nodata as unknown; estimating population outside Rwanda.
- **Reason:** SPEC §4 asks for population within 1, 5 and 10 km. With 3 arc-second pixels a 1 km circle holds about 370 of them, so the edge effect of the pixel-centre rule is small and has no direction. In WorldPop's constrained model a nodata pixel has no modelled settlement. The raster ends at the border, so a circle that crosses it counts only people inside Rwanda: on the real candidates, 69 of 300 have part of their 10 km circle outside Rwanda, 31 of them a quarter or more. The diagnostic makes that visible instead of silently understating demand.

## D-037: Terrain is deferred

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:** Elevation and slope, SPEC §4's reported-only terrain features, are not computed in M3. They stay in `features.reported_only`, and no column holds them.
- **Alternatives:** Reading Copernicus DEM tiles now.
- **Reason:** SPEC §2 defers the Copernicus DEM, and SPEC §4 reports terrain without scoring it.

## D-038: Feature layers, modes and the boundary with scoring

- **Date:** 2026-09-25 (Milestone 3, chosen by Gasim)
- **Decision:** `sitescout/features/` writes two layers, `features_production` and `features_backtest`, with one row per candidate (300), sorted by `candidate_id`, each candidate's point in EPSG:4326. Each is computed from the validated inputs independently (D-034). Both are checked before either is written; if anything fails, both are removed. Every value is raw, in natural units: metres, people, counts and ratios. No percentile rank, log1p, inversion, bonus, weight, profile, score or confidence is applied; SPEC §5 puts all of them in scoring (Milestone 4). Metadata records the mode, the fingerprint of every input, the charger sources, the grid-completeness table, the settings used, these decisions and the known limitations.
- **Alternatives:** One layer with a `mode` column; computing backtest values by blanking production ones; applying log1p in M3.
- **Reason:** Two layers keep each `read_layer` contract simple, and building each mode from its own inputs is what makes the leakage tests meaningful. The normalisation SPEC §5 describes is part of scoring.

## D-039: The profile rule

- **Date:** 2026-09-25 (Milestone 4, chosen by Gasim)
- **Decision:** `candidates.profile.town_radius_m` = 3,000 m and `kigali_radius_m` = 10,000 m. A candidate is **urban** when it lies within 10 km of the Kigali city centre as mapped in OSM (`node/60485579`, D-035) or within 3 km of any town or city centre (`features.town_centres`), both inclusive; otherwise it is **corridor**. The profile is assigned from `dist_kigali_cbd_m` and `dist_town_m`, which are never scored. It is a column of the score layers; the M2 `candidates` layer keeps `profile` null.
- **Alternatives:** A 5 km town radius (135 of 300 candidates within it instead of 74); a 15 km Kigali radius (71 candidates, some outside the City of Kigali).
- **Reason:** SPEC §3 puts both radii in config without values. 3 km covers a town core; 10 km from the Kigali node covers 59 of the 69 City-of-Kigali candidates without reaching far beyond it. On the real candidates: 107 urban, 193 corridor.

## D-040: Percentile points, direction, missing values and log1p

- **Date:** 2026-09-25 (Milestone 4, chosen by Gasim)
- **Decision:**
  - Each weighted feature becomes percentile points: 100 × (candidates below + 0.5 × candidates equal) / n, where n counts the candidates of the **same mode** whose value is present. Urban and corridor candidates share one reference. Ties get the same value; a feature whose values are all equal, and a single candidate, get 50. With 300 candidates the points run from 0.17 to 99.83.
  - `scoring.lower_is_better` (`dist_road_m`, `dist_trunk_m`, `dist_substation_m`, `dist_line_m`, `chargers_10km`, `chargers_25km`) are inverted as 100 − percentile. A larger `dist_charger_m` is better (a larger charging gap).
  - A missing value gets 0, after inversion, so it never raises a score.
  - `scoring.log1p_features` = `poi_1km`, `poi_3km`, `chargers_10km`, `chargers_25km`: log1p is applied before ranking. It keeps the order of values, so it changes no percentile; a test proves it. It is kept because SPEC §5 asks for it.
  - Equal scores are ranked by `candidate_id`.
- **Alternatives:** pandas' `rank(pct=True)` (a constant feature gets 50.2, one candidate 100); ranking missing values last instead of 0; a national reference outside the candidates; dropping log1p.
- **Reason:** SPEC §5 asks for percentile ranks within Rwanda without a formula. This is the standard percentile rank: symmetric, defined for ties and for one candidate. CLAUDE.md: missing data never raises a score. SPEC §5's grid rule also gives missing evidence 0.

## D-041: Confidence levels

- **Date:** 2026-09-25 (Milestone 4, chosen by Gasim)
- **Decision:**
  - Grid evidence missing (no mapped substation or line within 5 km) → **Low**, whatever else holds.
  - Otherwise count three factors: sparse public-map coverage (the district's `grid_completeness_ratio` below `confidence.sparse_coverage_threshold` = 0.5); no identified host (`host_type` none); remote location (`poi_3km` at most `confidence.remote_location_rule.max_poi_3km` = 0). 0 → **High**, 1 → **Medium**, 2 or more → **Low** (`confidence.factor_count_levels`).
  - The reasons list every factor that holds, including those that do not change the level. They contain no site-specific numbers, only the configured thresholds.
  - The universal unknowns (SPEC §6) are the same fixed list for every site and are not part of the level.
- **Alternatives:** Thresholds of 0.25 or 1.0 for sparse coverage (47 or 116 candidates); `poi_3km` ≤ 2 for remote (105 candidates); a weighted confidence number (SPEC and CLAUDE.md forbid a percentage).
- **Reason:** SPEC §6 names the factors but no thresholds and no way to combine them. 0.5 is half the national median of the proxy; a cut at 1.0 would flag half the districts by construction. On the real candidates: High 113, Medium 59, Low 128 (114 of them because grid evidence is missing).

## D-042: Components, bonuses, grid evidence and the score layers

- **Date:** 2026-09-25 (Milestone 4, chosen by Gasim)
- **Decision:**
  - Each component is the `config/weights.yaml` feature weights applied to the percentile points. The host-type bonus goes to host / commercial and the road-class bonus to access; each component is capped at 100 (SPEC §5).
  - The road-class bonus needs an exact `road_class` match: `trunk` +10, `primary` +5; `trunk_link`, `primary_link` and every other class +0.
  - Grid evidence is missing when neither a mapped substation nor a line lies within 5 km (inclusive). Its component is then 0 and its status UNKNOWN; otherwise CALCULATED.
  - Score = the profile's component weights × the components, 0 to 100.
  - **Backtest:** with no existing charger, `dist_charger_m` is missing (0 points) and both counts are 0 for every candidate (50 points after inversion), so the charging-gap component is **25** for every candidate. It adds 3.75 points to every urban score and 6.25 to every corridor score. This is an intentional consequence of SPEC §5's backtest definition and is not compensated.
  - `sitescout/scoring.py` writes `scores_production` and `scores_backtest`: 300 rows each, with the profile, score, rank, confidence and reasons, the five components, the bonuses, the grid-evidence status, the percentile points of every weighted feature and the universal unknowns. Raw feature values stay in the feature layers. Both layers are checked before either is written.
- **Alternatives:** Applying the trunk bonus to `trunk_link`; renormalising the profile weights in backtest mode (a workaround SPEC §5 rules out); repeating the raw features in the score layers.
- **Reason:** SPEC §5 lists the bonus classes as trunk and primary. Keeping raw values in one place (M3) and the reasoning in another (M4) keeps both layers small and each number in one source.

## D-043: Seeds and bootstrap settings

- **Date:** 2026-09-25 (Milestone 5, approved by Gasim in the demo plan)
- **Decision:** `evaluation.random_seed` = 20260924 (Day 0, the M0 commit date). The random baseline's 1,000 rankings use seeds 20260924, 20260925, …, one per ranking; the bootstrap uses 20260924. `evaluation.bootstrap.resamples` = 1,000; `confidence_level` = 0.95 (percentile intervals, 2.5th to 97.5th).
- **Alternatives:** Other seeds (any fixed value would do); 10,000 resamples.
- **Reason:** SPEC §7 and CLAUDE.md require fixed seeds and bootstrap intervals without values. 1,000 resamples keeps a run to a few seconds; 95% is the usual level.

## D-044: How the plausibility test is measured

- **Date:** 2026-09-25 (Milestone 5)
- **Decision:**
  - **Hits:** a candidate within 1 km (inclusive) of a known charging site: the production charger set of D-034 (OSM charging stations merged within 50 m, plus the manual CSV when it exists). Ranking uses `scores_backtest`, where no charger reaches any feature.
  - **Precision@k** = hits among the top k ÷ k. **Recall@30** = hits in the top 30 ÷ all hit candidates; undefined (null) with no hit candidate.
  - **Population-only baseline:** candidates ranked by `pop_5km`, the most heavily weighted demand feature. **Random baseline:** the mean over 1,000 seeded random rankings.
  - **Bootstrap:** each resample draws the 300 candidates with replacement; SiteScout and population-only are re-ranked on the same resample, which also gives an interval for their difference.
  - **Weight stability:** every feature weight (12) and profile weight (10) is multiplied by 0.8 and by 1.2 in turn (44 runs), its group renormalised to 1, and the production Top-30 compared with the unperturbed Top-30. The report gives the mean, the minimum and the number of runs below the 70% target. Bonuses are points, not weights, and are not perturbed. Each run passes its weights as logged config overrides.
  - Results go to `data/processed/evaluation.json`; `reports/evaluation.md` is filled from them by a template, with no hand-written numbers.
- **Alternatives:** Recall over known sites instead of hit candidates; `pop_10km` or a sum of the population features as the baseline; resampling the known sites instead of the candidates; judging stability on the average only.
- **Reason:** SPEC §7 names the metrics and baselines but not their definitions. Candidate-level hits keep precision and recall on the same footing and let the bootstrap resample one population. With 5 known sites and 5 hit candidates on the 2026-09-23 data, the intervals are wide; the report says so and draws no strong claim.

## D-045: Dependencies for Milestone 6

- **Date:** 2026-09-25
- **Decision:** Two runtime dependencies, each for a named SPEC §8 problem:
  - **pulp** (3.3.2, pinned `>=3.3.2,<4`): the exact MCLP with its **bundled CBC** solver, as SPEC §8 and CLAUDE.md require. PuLP 4.0 no longer ships CBC (it found no solver on this machine), so the pin keeps the bundled binary. PuLP 3.3 marks `PULP_CBC_CMD` as deprecated for that reason; `optimize.py` silences that one warning at the call site and nowhere else.
  - **h3** (4.5.0): the demand nodes, WorldPop aggregated to H3 resolution 7 (SPEC §8).
- **Alternatives:** PuLP 4 with a separately installed CBC (`pulp[cbc]`), which is no longer "bundled"; OR-Tools CP-SAT (SPEC §8: needs integer scaling); a square grid instead of H3 (SPEC names H3).
- **Reason:** CLAUDE.md: a new dependency must solve a named problem. Both resolved as Windows wheels for Python 3.12 (D-001). As in D-017, `uv sync` needed retries because another process briefly locked files.

## D-046: Network selection

- **Date:** 2026-09-25 (Milestone 6; eligibility of hostless points and the sensitivity values chosen by Gasim)
- **Decision:**
  - **Mode:** production scores and production demand; the network is what SiteScout recommends.
  - **Demand nodes:** every WorldPop pixel with people, summed into its H3 resolution-7 cell (4,094 cells on the real data); each node sits at its cell's centre, in EPSG:32735. Demand within the service radius of a known charging site (D-034) is multiplied by `existing_charger_demand_factor` (0.5), then all weights are **renormalised to sum to 1**.
  - **Eligible sites:** production score percentile (D-040) at least `min_score_percentile` (50) across all candidates, not within each profile. With `optimization.require_host: true`, corridor points without a host are never selected (SPEC §3); they stay scored and visible. With fewer than 30 eligible sites the run stops; the threshold is never lowered. On the real data: 108 eligible.
  - **sⱼ** = score / 100, a score in [0, 1] without min-max normalisation.
  - **Exact MCLP:** SPEC §8's objective and constraints, solved by CBC on one thread with the 300 s limit. The status and CBC's solution status are reported; a result is only called exact when CBC reports it optimal. Sites strictly closer than 2 km cannot both be selected. A node is covered within the service radius, inclusive.
  - **Greedy:** the same objective and constraints; each step adds the eligible site with the largest objective gain that respects the spacing, ties by candidate_id.
  - **Top-30 by score:** the 30 highest-scoring eligible sites, with no spacing rule: the naive baseline.
  - **Marginal coverage:** for a selected MCLP site, the weighted demand only it covers; for any other candidate, the weighted demand it would add.
  - **Sensitivity** (`optimization.sensitivity`): λ ∈ {0, 0.01, 0.05}, service radius ∈ {5, 10, 15} km, existing-charger factor ∈ {0.25, 0.5, 0.75}, each varied with the others at their defaults (6 extra solves).
  - **Outputs:** the `network` layer, `data/processed/network.json` (the comparison, deterministic) and `network_run.json` (solve times, which vary between runs and are kept out of the layer and the report). Section C of `reports/evaluation.md` is rendered from `network.json`.
- **Alternatives:** Allowing hostless points (one ranked first in production); eligibility within each profile; a population-weighted node position; applying the spacing rule to Top-30.
- **Reason:** Resolves the Milestone 6 open questions. On the real data the exact MCLP is optimal and covers 49.8% of Rwanda's modelled population within 10 km, against 24.0% for the Top-30 by score (23 of whose 30 sites are in the City of Kigali); greedy comes within 0.64% of it.

## D-047: Site Evidence Briefs and the grounding check

- **Date:** 2026-09-25 (Milestone 7)
- **Decision:**
  - **What:** one brief for each of the 30 sites the exact MCLP selects (D-046), plus an index, written to `reports/briefs/` by `scripts/briefs.py`. Production-mode data throughout.
  - **Name:** "Site Evidence Brief". SPEC §9's name contains a word CLAUDE.md allows only inside the exact grid disclaimer; SPEC.md is not changed.
  - **Content:** SPEC §9's sections, answering four questions: why the site was selected (the network, its unique coverage, rank, score, strongest components), what evidence supports it (demand, access, charging gap, grid evidence, score breakdown), what is unknown (the five universal unknowns, plus missing grid evidence), and what to investigate next. Both mandatory texts appear verbatim: the grid disclaimer and the public-data notice. Licences and data dates are credited.
  - **Evidence records** (`sitescout/evidence.py`, `data/processed/evidence.json`): SPEC §9's `{claim, type, evidence: {source, metric, value}}` plus an `id`, a `unit` and the exact `display` text. Types: RETRIEVED_FACT (OSM tags, boundaries, dates, config), CALCULATED (pipeline values), INFERRED (the confidence level and reasons), UNKNOWN (missing grid evidence and the universal unknowns). Built only from the M2-M6 layers, their metadata and the configuration.
  - **No LLM.** The Opportunity paragraph, risks and next actions come from `templates/brief.md` and fixed rules over the evidence records: risks for missing grid evidence, sparse grid mapping, a circle crossing the border, the few known charging sites and the weakest component; actions for the universal unknowns (utility, owner, permits), plus missing grid evidence, sparse mapping, a known charging site within the service radius and cross-border demand. No new threshold is introduced; every rule uses an existing flag or configured radius.
  - **Grounding check:** the template contains no number of its own (a test enforces it), and every number in every brief and in the index must appear in the display text of that site's evidence records or the shared context records. The share must reach `evaluation.grounding_target` (1.0) or nothing is written. The result goes to `data/processed/grounding.json` and section E of `reports/evaluation.md`.
- **Alternatives:** An LLM-written Opportunity paragraph (SPEC §9 allows it, but no approved API key source exists and the grounding check would then carry the whole burden); briefs for the Top-30 by score instead of the network; keeping briefs under `data/` only.
- **Reason:** Resolves the Milestone 7 open questions. CLAUDE.md: reports are built from templates with injected values, every displayed number comes from structured data, and grounding targets 100%. On the real data: 30 briefs, 2,262 numbers, all grounded.

## D-048: The demo export and the one-page decision view

- **Date:** 2026-09-25 (Milestone 8; the committed export, `generated_at` and the SVG map chosen by Gasim)
- **Decision:**
  - **Export** (`sitescout/export.py`, `scripts/export.py`): `data/export/sitescout.json`, validated by pydantic models before anything is written. It holds `meta` (`generated_at`, `data_status`, sources, licences, the ODbL notice, the three disclaimers, the settings snapshot), `headline` (the three networks: population share and its display, districts, provinces, mean score, sites by province; the exact-versus-greedy gap), `weights`, the evidence `context`, all 300 `sites` (the 30 network sites with their evidence records and brief sections), `existing_chargers` (positions only), an `evaluation` summary, a `map` frame, and district and province outlines simplified by 250 m in EPSG:32735 (69 KB, below the 300 KB limit). Every number the page prints is a display string formatted in Python. `data/export/sitescout.js` holds the same object as `window.SITESCOUT = …;`, so the page also opens from disk.
  - **Brief sections are shared, not copied.** The rules that write a brief's Opportunity paragraph, risks, unknowns and next actions moved into `briefs.brief_sections()`. The Markdown briefs and the export both call it; the 30 briefs and the index stayed byte-identical.
  - **Determinism:** the export is identical from run to run except `meta.generated_at`, which records when it was written and is intentionally variable. Re-export (and commit) only when the pipeline outputs change.
  - **Committed demo export:** an explicit exception to "nothing under `data/` is committed", so the demo runs from the public repository. Only `data/export/sitescout.json` and `data/export/sitescout.js` are committed (`.gitignore` exceptions, a hygiene test, and a line in CLAUDE.md). Conditions: written only by `scripts/export.py`, schema-validated, never edited by hand, each file below 1 MB, no raw data, no personal data, no secrets, no business or operator names (generic host labels and OSM ids only). Everything else under `data/` stays ignored and untracked.
  - **Licences:** the export contains data derived from OpenStreetMap, so it carries the ODbL notice and OpenStreetMap credit, plus the WorldPop and geoBoundaries credits (CC BY 4.0), in the export and in the page footer.
  - **Page** (`app/index.html`): one file, inline CSS and plain JavaScript, no framework, no library, no network request. A read-only view: it draws the export and computes no score, coverage, rank, selection, confidence or recommendation. The map is inline SVG (district outlines, province outlines and labels; candidates subtle, the selected 30 dominant with very subtle service rings, known charging sites as outlined squares) with an "Optimized network | Top-30 by score" toggle. A network site's detail shows why it was selected, its components, evidence, grid evidence (missing stated plainly, with the exact disclaimer), confidence, unknowns, risks and next actions, and links to its brief. A Top-30 site outside the network shows score, rank, confidence and components with "Not selected for the optimized network." and nothing more. `#top30` and `#<candidate_id>` open those views directly.
- **Alternatives:** Leaflet with OpenStreetMap tiles (a CDN and a tile service, rejected by Gasim); `fetch()` of the JSON (blocked from `file://`); keeping the export uncommitted (the public repository could not show the demo); a timestamp-free export (SPEC §10 lists `generated_at`).
- **Reason:** SPEC §10 and CLAUDE.md: the pipeline exports one JSON file, and the front end renders it without computing. A single static page is the smallest thing that makes the result visible: optimizing 30 sites together covers 49.8% of the modelled population, against 24.0% for the Top-30 by individual score.

## D-049: M9 is the AI Site Analyst, built on deterministic tools first

- **Date:** 2026-09-25 (Milestone 9, approved by Gasim)
- **Decision:**
  - Of SPEC §11's two stretch options, M9 builds the AI Site Analyst. Station sizing is not built: Erlang C needs arrival and service rates that no project dataset holds.
  - **Phase 1 (no AI):** six deterministic, read-only tools in `sitescout/analyst/tools.py` over `data/processed/`, and `scripts/ask.py --tools-only <tool> …`, which prints a tool's structured result as JSON.
    - `find_sites`: exact filters only (id, generic label, district, province, rank, profile, confidence, network and Top-30 flags, grid-evidence status), combined with AND, in rank order. No fuzzy or semantic search.
    - `get_site`: identity, score, rank, profile, confidence, components, the M7 evidence records, selection flags, the universal unknowns and, for network sites, the M7 next actions.
    - `compare_sites`: two sites side by side, field by field, with a value-neutral `relation` (`equal`, `a_greater`, `b_greater`; `same`, `different`). No winner, ranking, preference, total, difference or ratio.
    - `explain_score`: the stored score, components, the profile's component weights, feature weights, percentile points, bonuses and confidence reasons. Nothing is recomputed.
    - `network_contribution`: the M6 facts about a site's place in the network (eligibility, host, flags, the demand it covers or would add, the nearest other network site, spacing conflicts). A non-selected site's reason is ESTABLISHED only when an M6 rule excludes it (no host, score below the eligibility percentile, a network site closer than the minimum spacing); otherwise it is UNKNOWN.
    - `generate_brief`: the M7 brief sections and Markdown of a network site, from `briefs.brief_sections()` and `render_brief()`.
  - Every value is an evidence record with a stable id (`<candidate_id>/<field>`, `context/<field>`, `weights/<component>/<feature>`, `compare/<a>/<b>/<field>`), its display text, its type and its source, so a later validator can check each statement against exactly the text it cites. `evidence.all_sites()` joins every candidate, and `site_records(…, network_site=False)` describes any candidate; the M7 outputs are unchanged.
  - Later phases, each reviewed separately: the answer schema and validator (D-052), the provider interface and tool-calling loop, and the optional Anthropic provider with its configuration (D-051) and credential (D-050). What AI output may reach is fixed by D-053.
- **Alternatives:** Station sizing; an LLM first; embeddings or a vector store (300 keyed records need exact lookup, not semantic search); a free-text parser without a model.
- **Reason:** The gaps M8 leaves (why a high-scoring site is not in the network, side-by-side comparison, questions across sites) are deterministic information gaps. The tools close them without AI and are what an optional model would call.

## D-050: The analyst's API key is the one environment input, and only a secret

- **Date:** 2026-09-25 (Milestone 9, phase 3b; approved by Gasim)
- **Decision:**
  - `sitescout/analyst/credentials.py::read_api_key` is the only code in SiteScout that reads the environment. It reads `ANTHROPIC_API_KEY` and nothing else, returns it as a masked `SecretStr`, and raises `CredentialError` (naming the variable, never a value) when it is unset or blank.
  - The key is a secret, not configuration. Provider, model, limits, timeout, temperature and every analytical parameter come from `config/settings.yaml` (D-051); no environment variable or `.env` file overrides any of them, and `load_config` is unchanged. CLAUDE.md carries this as the single exception to "environment-variable and `.env` overrides are disabled".
  - The key never appears in logs, tool results, the model's context, `RunResult`, error messages or files. It reaches only the SDK client's constructor, which sends it as an HTTP header. Provider errors report the error type and HTTP status only.
  - The client is built with the key and the API endpoint passed explicitly, so the SDK consults neither its own credential variables (`ANTHROPIC_AUTH_TOKEN`, profiles, federation) nor `ANTHROPIC_BASE_URL`.
  - A missing key never raises from `scripts/ask.py` and never switches provider: the answer is the deterministic fallback ("no AI summary") with the reason, and no data is read.
- **Alternatives:** A git-ignored key file referenced from config (a secret on disk next to the repository); a general environment loader (would reopen environment configuration); a key field in YAML (would put a secret in a committed file).
- **Reason:** The Anthropic API needs a secret, and the secret must never be committed. The standard variable keeps it out of the repository and out of YAML, while the rule that configuration comes from YAML only stays intact.

## D-051: The analyst's provider interface, configuration and optional SDK

- **Date:** 2026-09-25 (Milestone 9, phases 3a and 3b; approved by Gasim)
- **Decision:**
  - **Provider interface** (`sitescout/analyst/provider.py`): one method, `Provider.next_step(ModelContext) -> ModelStep`. The context holds the question, the six tool definitions built from `REGISTRY`, the transcript of executed tool calls and, on the retry turn only, the validator's errors. A step is either one tool call or the raw final answer. A provider returns data only; tool execution, validation, the retry and the fallback stay in the loop (`sitescout/analyst/run.py`). `FakeModel` is the scripted implementation for tests.
  - **Anthropic provider** (`sitescout/analyst/anthropic_provider.py`): stateless; each turn rebuilds the conversation from the context. It declares exactly the six tools (no server, code, shell, file or web tool) with parallel tool use off, asks for the final answer as a JSON object matching the `Answer` schema, and translates the reply into a `ModelStep` without checking or fixing it: output that is not a JSON object becomes `{"unparsed_output": ...}` and fails the schema in the loop. The SDK's own retries are off (`max_retries=0`); the loop's single answer retry is the only retry.
  - **Configuration** (`analyst:` in `config/settings.yaml`, `AnalystSettings` in `config.py`): `provider` (only `anthropic`), `model` (`claude-sonnet-5`), `max_tool_calls` (6, 1 to 20; used by the loop through `RunLimits.from_settings`), `max_tokens` (4096), `timeout_s` (60) and `temperature` (`null`). The Anthropic SDK 1.8 has no temperature parameter; `null` leaves it out, and a number is sent as a raw request field for a model that still accepts it.
  - **Optional dependency:** `anthropic` is the only package in the `analyst` extra (`[project.optional-dependencies]`). A plain `uv sync` installs no AI package; `uv sync --extra analyst` installs the SDK. The SDK is imported only inside `build_provider`, so SiteScout, the tools, the validator, the loop and every automated test run without it; a missing SDK is reported, not raised.
  - **Command line:** `scripts/ask.py "<question>"` runs the loop with the configured provider and prints the validated answer with its citations, or the fallback labelled "no AI summary" with the raw evidence records (exit 0 or 1). `--tools-only` keeps the deterministic path. Prompts and provider state are never printed.
  - No automated test calls the API. One test runs the real SDK, when the extra is installed, through an in-memory HTTP transport.
- **Alternatives:** A framework (LangChain, LangGraph) or an agent library (unnecessary for one tool loop, CLAUDE.md); a local model (weaker at tool calling and structured output; possible later behind the same interface); the SDK in the base dependencies (would make the demo pipeline depend on an AI package); the SDK's structured-output option (its schema support could not be checked offline; the loop's parser and validator are authoritative either way).
- **Reason:** A new dependency must solve a named problem (CLAUDE.md): the SDK is the supported way to call the Messages API. Keeping it optional and behind one small interface keeps the pipeline, the demo and the tests AI-free, and lets another provider replace it without touching the loop.

## D-052: The answer schema, the validator, one retry and the fallback

- **Date:** 2026-09-25 (Milestone 9, phases 2 and 3a; approved by Gasim)
- **Decision:**
  - **Answer schema** (`sitescout/analyst/validate.py`): five sections (`direct_answer`, `evidence`, `interpretation`, `unknowns`, `next_investigation`), each a list of statements with `text`, `kind` (RETRIEVED_FACT, CALCULATED, INFERRED, UNKNOWN) and `evidence_ids`. Unknown fields are rejected.
  - **Validator** (`validate_answer(answer, session)`): offline and deterministic; it trusts only the records returned by this session's tools. A statement fails when it cites an id not in the session; is not UNKNOWN and cites nothing; is CALCULATED without citing a CALCULATED record; is UNKNOWN without saying that something is unavailable; contains a number that is not, character for character, a number in the display text of a record it cites (so rounding, rescaling and reformatting fail); contains an operator or a hyphen between numbers; uses an approximation word or a number written as a word; makes one of CLAUDE.md's prohibited claims; mentions the grid without the exact sentence "Actual grid connection feasibility requires utility confirmation." (`briefs.GRID_DISCLAIMER`, the only grid disclaimer); uses an evaluative comparison word (better, worse, best, prefer, preferred, winner, recommend, ranks above); or uses a comparative word (greater, higher, more, less, lower, smaller, larger) without citing a `compare_sites` field whose relation is directional. The word rules are deliberately conservative: a false rejection falls back, a false acceptance would publish an ungrounded claim.
  - **One retry:** a final answer that fails the schema or the validator gets exactly one retry, with the structured errors in the model's context. Both kinds of failure share it.
  - **Fallback:** a second failure, an unknown tool, invalid arguments, a tool's refusal, the tool-call limit, a malformed step, a provider error or a missing key returns `FallbackAnswer`, labelled "no AI summary": the reason and the raw evidence records the session's tools returned, with no prose and no new number. An unvalidated answer is never returned.
- **Alternatives:** A special-cased parser for "refusing to choose" (not reliably deterministic); checking a number anywhere in the session rather than in the cited records (would let a statement borrow an unrelated number); retrying tool-call errors (the retry is for answers; a bad tool call stops the run).
- **Reason:** CLAUDE.md: deterministic code makes every numerical decision, LLMs never produce numbers, and every claim is grounded. The validator enforces that on the model's text as the M7 grounding check does on the briefs.

## D-053: What AI output may reach

- **Date:** 2026-09-25 (Milestone 9; approved by Gasim)
- **Decision:**
  - AI Analyst output must never affect SiteScout's decision outputs: it is never written into `data/export/`, the deterministic pipeline's own outputs under `data/processed/` (including `evidence.json`), the Site Evidence Briefs or `app/index.html`. The M8 demo stays AI-free and runs without the analyst.
  - The one documented exception is the evaluation harness itself (phase 3c): `scripts/analyst_eval.py` writes model answers, as evaluation artifacts, to `reports/analyst_eval.md` and to its own log file, `data/processed/analyst_eval.json` (not a pipeline output; not committed). These record test evidence of how the analyst answered a fixed scenario set — never a SiteScout claim. Nothing reads either file back into the pipeline, the export, the briefs or the demo, and this exception does not widen beyond these two evaluation artifacts.
  - Deterministic tools make every numerical decision. The model only chooses tools and phrases their returned values; it cannot select, rank, score, reorder or calculate.
  - `compare_sites` never produces a winner, ranking, preference, total, difference or ratio, and the validator rejects an answer that turns a comparison into one.
- **Alternatives:** An "AI Analyst preview" in the page (SPEC §10), which SPEC §11 allows only after a scenario set passes; storing answers next to the briefs.
- **Reason:** The briefs and the export are grounded, reproducible outputs. Model output is neither, so it stays outside them.

## D-054: OpenAI as a second provider behind the same interface

- **Date:** 2026-09-25 (Milestone 9; requested by Gasim)
- **Decision:**
  - `analyst.provider` accepts `anthropic` or `openai`; `analyst.model` is set with it in YAML, and a model id that does not belong to the provider (Anthropic ids start with `claude-`) is rejected at load. No provider, model or setting comes from the environment, and no provider is ever substituted for another.
  - `sitescout/analyst/openai_provider.py` implements `Provider.next_step` on the OpenAI Responses API (`client.responses.create`, SDK 3.19): stateless, `store=False`, the shared rules as `instructions`, the question as delimited data, each executed tool call replayed as a `function_call` with its `function_call_output`, the validator's errors as a final user item on the retry turn only, exactly the six tools as non-strict function tools (their parameters are the loop's own pydantic argument schemas; strict mode would need every optional `find_sites` filter to be required), parallel tool calls off, `max_output_tokens` from `max_tokens`, `temperature` only when set. A reply becomes a tool call (a built-in tool call, never declared, is passed on under its own type name for the loop to refuse), or raw answer data from the message text (a refusal or non-JSON text becomes `unparsed_output`). The loop keeps tool allow-listing, argument validation, execution, the schema, `validate_answer`, the single retry and the fallback; the SDK's retries are off.
  - The rules both providers send live once in `provider_common.py`; the Anthropic provider now imports them from there, with its requests and reply translations byte-identical to before. `factory.py` is the only provider selection; `ask` and the scenario evaluation use it.
  - The D-050 credential rule extends to `OPENAI_API_KEY`: `credentials.read_openai_api_key` reads it as a masked secret through the same single environment read. Errors report the error type, HTTP status and the API's error code; the API's own message is logged with keys and bearer tokens redacted.
  - Both SDKs are the optional `analyst` extra; a plain `uv sync` installs neither.
- **Alternatives:** The Chat Completions API (the Responses API is the SDK's current tool-calling interface); strict function schemas (would loosen `find_sites` into all-required fields); a second prompt or tool registry for OpenAI (would drift from the Anthropic rules); choosing the provider by which key is present (would let the environment select the provider).
- **Reason:** A second provider makes the analyst independent of one vendor's availability, while every guarantee stays in SiteScout's own code: the same tools, evidence, schema, validator, retry, fallback and credential rule apply whichever model answers.

## D-055: M10 is Agentic Site Investigation, with a local knowledge index as its RAG layer

- **Date:** 2026-09-26 (Milestone 10; approved by Gasim)
- **Decision:**
  - The milestones are renumbered: M10 is Agentic Site Investigation and M11 is Packaging + demo. M9 is closed; iteration itself is not new in M10, because the M9 loop already lets the model choose tools one call at a time. M10 adds a broader action space, retrieval of project knowledge, an explicit AgentState, separate tool, retrieval and error budgets, bounded recovery from tool errors, trajectory evaluation and investigations that combine tools with retrieval.
  - RAG is in scope only as a local project-knowledge index (CLAUDE.md, Technology discipline). It never contains or replaces structured SiteScout site data. Structured facts (site ids, scores, ranks, components, confidence, network coverage, comparisons) come from the deterministic tools; methodology and project knowledge come from retrieval; the agent orchestrates and synthesizes; the validator checks the final answer; the deterministic decision engine stays the source of truth.
  - The agent is read-only with respect to the decision engine. It never modifies scores, features, weights, candidate generation, the MCLP, the selected sites, coverage or evaluation results.
  - Phase 1 builds the knowledge layer only, offline: the corpus manifest, chunking, metadata, fingerprint, BM25 retrieval, a retrieval gold set and its evaluation (D-056). It does not touch the M9 analyst, the providers or any model. The provider seam, the new tools (`network_summary`, `nearby_sites`, `search_knowledge`), the agent loop and its evaluation come in later phases, each reviewed separately.
  - Dense embeddings are a possible later experiment, decided only from a measured comparison against BM25 on a reviewed gold set. No embedding dependency is added now.
  - The 30 Site Evidence Briefs are not indexed. They stay available through the deterministic tools such as `generate_brief`.
- **Alternatives:** Embeddings and a vector database from the start; indexing the briefs and the evaluation reports; putting the whole documentation into every prompt; a separate agent framework.
- **Reason:** M9's tools return site facts only, so a methodology question such as why the network is chosen by an exact maximum-coverage model has no citable source today. A small, deterministic, offline knowledge layer closes that gap without adding a dependency, an API key or a second source of numbers, and it can be measured before any model uses it.

## D-056: The knowledge corpus, chunking and retrieval rules

- **Date:** 2026-09-26 (Milestone 10, Phase 1; approved by Gasim)
- **Decision:**
  - **Corpus:** an explicit allow-list in `knowledge.sources` of `config/settings.yaml`: `docs/scoring.md`, `docs/features.md`, `docs/decisions.md`, `docs/architecture.md`, `docs/data_sources.md`, sections 3 to 9 of `docs/SPEC.md` and three sections of `README.md`, each with the `##` headings it includes or excludes. Left out on purpose: the dated-results sections (they are data snapshots, which the tools own), the repository-layout section of the architecture page (out of date), the command and ingestion-layer sections of the data-sources page, and the parts of SPEC and README that are not method. CLAUDE.md, `config/`, `tests/`, `data/`, `app/`, `scripts/`, `src/` and everything under `reports/` (the evaluation reports, the analyst evaluation and the 30 briefs) can never be indexed: the configuration model refuses those paths. A test pins the exact list.
  - **Normalization:** UTF-8, CRLF and CR line endings turned into LF, nothing else, so a Windows and a Linux checkout give the same fingerprint.
  - **Chunks:** sections by Markdown heading (levels 1 to 3), cut into atomic blocks (paragraph, table, code fence, top-level list item with everything under it), packed up to `chunk.max_chars` = 1500, a part below `chunk.min_chars` = 200 joined to its neighbour, no overlap. A block longer than the limit stays whole and its chunk is flagged `oversize`. A chunk's text is an exact contiguous slice of the normalized document.
  - **Ids and metadata:** ids are `kb/<document>/<section>[#part]` from heading slugs, and a duplicate stops the build. Metadata (`doc_type`, `topic`, `section_path`, `decision_id`, `milestone`, hashes) comes only from the text and the manifest; `milestone` is set only where the text states it and is otherwise null. The corpus fingerprint is a SHA-256 over the chunker version, the manifest and every chunk's id, metadata and content hash.
  - **Retrieval:** Okapi BM25 with `k1` = 1.2 and `b` = 0.75 (the standard defaults, never tuned), a tokenizer that lowercases and splits on word characters, `top_k` a required argument up to `retrieval.max_top_k` = 10, queries up to `retrieval.max_query_chars` = 500, exact AND filters on `doc_type`, `topic`, `milestone` and `decision_id`, ties broken by chunk id. It is a word-overlap ranking with no embeddings, no model, no network access and no notion of meaning. The index is built in memory from the current files, so it cannot be out of date.
  - **Gold set and evaluation:** `tests/knowledge_gold.yaml` holds queries in five categories (paraphrase, terminology, definition, decision, out of scope), each relevant chunk anchored by its id and a verbatim phrase. `scripts/knowledge.py eval` reports Recall@k, Hit@k (k = 1, 3, 5, 10), MRR, per-category results, the misses and diagnostics for out-of-scope queries into `reports/knowledge_eval.md`, with the corpus fingerprint, the gold-set hash and the review status. The queries are written before the first run, and BM25, the tokenizer and the chunking are not tuned on the results; a later change needs a recorded decision and a fresh review. Phase 1 defines no pass or fail gate: that follows the review of the gold set and the results.
- **Alternatives:** Indexing the whole repository; splitting long blocks at nested list items; overlapping chunks; a persisted index file (unneeded for a corpus this small); tuning `k1`, `b` or the tokenizer on the gold set; a relevance threshold chosen before it is measured.
- **Reason:** The corpus is small, human-written and deterministic, so an in-memory lexical index over allow-listed sections is enough to measure retrieval honestly before anything larger is considered. Exact slices and stable ids make every retrieved text checkable against its document, and refusing generated data and reports keeps structured facts with the tools.

## D-057: Two deterministic investigation tools: network_summary and nearby_sites

- **Date:** 2026-09-26 (Milestone 10, Phase 2; approved by Gasim)
- **Decision:**
  - **Why:** the M9 tools describe one site at a time and return no network-level record, and none answers which candidates lie near another. Structured network and spatial facts stay with deterministic tools, never with retrieval, so the future agent needs these two as the structured counterpart of `search_knowledge` (D-055, D-056). Neither tool imports the other, the knowledge package, a model, a provider or a network module.
  - **`network_summary`** reads `data/processed/network.json`, the single authoritative source that `optimize.run_network` writes and that the M8 export headline and section C of `reports/evaluation.md` are built from. Nothing is recomputed or typed in, and the optimization is never run. Every value is an evidence record (`network/<method>/<field>` and similar) formatted with the export's and the report's own display conventions: per selection (optimized, greedy, Top-30) the population coverage, covered demand, objective, sites, provinces, districts, mean score and sites by province; the exact-versus-greedy gap, marked as a gap on the objective and not on population share; the overlaps; the solver status; the eligible-site, demand-node, H3-resolution and known-charging-site counts; the optimization parameters; and the sensitivity rows. `population_total` and `spacing_conflicts` are in the file but neither the export nor the report shows them, so they are not exposed. The file is checked strictly: a missing file, an unknown or missing key, a mode other than production, a selection whose size is not `n_sites`, or a selection that disagrees with the network layer raises `network_summary_unavailable`. Nothing is repaired and no other source is tried.
  - **`nearby_sites(data, candidate_id, radius_m)`** lists the candidates within `radius_m` metres of one candidate, from the candidate points in EPSG:32735 and the same distance call `network_contribution` uses, so both give the same distance. `radius_m` is a required integer from 1 up to `optimization.service_radius_m`; no separate radius setting exists. The radius is inclusive, the target is not listed, all 300 candidates can be found with their stored eligibility and selection flags, and neighbours are ordered by distance and then `candidate_id`, which is an ordering and not a ranking method. It performs no score comparison, has no filter and takes no coordinates: comparing a neighbour with the target stays with `compare_sites`, so the agent must call that tool on an id it discovered.
  - **`investigation.nearby_sites.max_results` = 10** only caps how many neighbours one result lists, to keep it small. It is not a geographic, business or optimization assumption. The full count within the radius is always returned with `returned_count` and `truncated`.
  - **Consistency and its limit:** besides the schema and mode checks, the MCLP, greedy and Top-30 candidate ids must equal the network layer's selection flags and every selection must have `n_sites` sites. `network_summary` therefore validates the internal consistency of the processed network artifact, but does not determine whether it is out of date relative to a changed configuration: it does not compare the parameters recorded in `network.json` with the current configuration, and it never runs the optimization. That comparison is left as a future decision.
  - **Errors** are `InvestigationError`, a subclass of the M9 `AnalystError`, with the codes `invalid_arguments`, `unknown_site` and `network_summary_unavailable`. A result with no neighbour is a normal result with a count of 0.
  - **Data:** `InvestigationData(analyst, processed_dir)` pairs the M9 `AnalystData` with the processed directory, because `AnalystData` does not hold it and `network_summary` reads `network.json` from there; `nearby_sites` needs only the `AnalystData`.
  - **Registration:** the two tools are not in the M9 six-tool registry, which stays unchanged together with the provider tool check. `sitescout.investigation.INVESTIGATION_TOOLS` lists them with the same fields as the M9 `ToolSpec`, defined locally because `ToolSpec` lives in the M9 loop module, which imports the provider interface. A later phase composes M9's tools, these two and `search_knowledge` into one registry.
  - Both tools are read-only. `scripts/investigate.py` prints their results as JSON and makes no model or network call.
- **Alternatives:** Adding the tools to the M9 registry (would change the provider tool check and M9 behaviour); recomputing the network figures (a second calculation that could drift); putting network figures into the knowledge index (retrieval must never hold decision data); a `higher_ranked` filter or a score relation in `nearby_sites` (a second comparison path); coordinate queries (need their own validation); a separate, larger radius setting (a new number for no new fact).
- **Reason:** The network results are the project's headline decision and must be read from where they are decided. A bounded, deterministic neighbourhood query is the one spatial fact the agent could not get before, and keeping comparison in `compare_sites` keeps every comparison neutral.

## Open questions

These need a decision before or during the milestone named. None has a default.

### Resolved in Milestone 1

- Rasters and "all sources in GeoParquet": D-022. Vectors go to GeoParquet. The raster stays a GeoTIFF, unclipped: the M0 proposal to clip it was dropped because clipping to the ADM2 outline would change edge pixels that WorldPop published.
- Deriving ADM1 from ADM2 without a province code: D-021.
- `rwanda-latest.osm.pbf` changing over time: D-016.
- The distortion of EPSG:32735 east of 30°E: D-018 (at most +0.199%).
- `charger_match_radius_m`: moved to M3 (D-024) and set to 50 m there (D-034).
- National parks tagged `boundary=national_park`: D-026 (Milestone 2).

### Milestone 2

- Resolved: the candidate count. SPEC §3's rules give 595 eligible candidates, above the 200 to 400 target; the candidate budget selects 300 (D-031).
- Deduplication keeps an industrial site over a hotel (SPEC §3 order), yet the hotel's bonus is 5 points and the industrial site's is 0. The SPEC order is implemented as written.
- Fuel stations tagged `socket:*` stay candidates in backtest mode; only their charger-related attributes are removed. No fuel station carries a `socket:*` tag on the 2026-09-23 extract.
- Resolved in Milestone 2: national parks (D-026), host tags (D-027), drivable classes (D-028). `osm_protected_areas` also holds cross-border areas that only touch Rwanda; the filter tests the area geometry, so they matter only where they overlap Rwanda.

### Milestone 3

- Resolved in Milestone 3: the charger match radius and which records are chargers (D-034); `socket:*` fuel stations count as chargers in production (D-034); the `power` values that count, which leaves out `150kWh` and `11 kWh` (D-033); POI types (D-033); the trunk classes (D-032); town and city centres and the Kigali centre (D-035); terrain deferred (D-037).
- The backtest ground truth is small. OSM maps 7 charging stations (5 sites after merging within 50 m), and `data/manual/chargers.csv` does not exist yet. SPEC §7 expects "a few dozen". Until the CSV is filled, the M5 backtest has 5 known charging sites at most, and its confidence intervals will be very wide.
- The OSM data is dated 2026-09-23, after the chargers were mapped. People who mapped a charger may also have mapped the places around it, which backtest mode cannot remove. Report this with the M5 results.
- `grid_completeness_ratio` counts every approved feature equally, so densely mapped towers or individually mapped generating units can dominate a district. Rwamagana holds 334 of the 488 `generator` and `plant` features (ratio 3.57), and Gasabo's highest ratio (6.62) comes mostly from 379 towers. It measures how much is mapped, not the grid, and stays a labelled proxy.

### Milestone 4

- Resolved in Milestone 4: the profile rule (D-039); `dist_town_m` assigns the profile but is never scored (D-039); log1p changes no rank and is kept (D-040); percentiles are within the candidates of each mode (D-040); the backtest charging-gap constant is 25 (D-042); how the confidence factors combine (D-041).
- In production mode a corridor point without a host ranks first (Rubavu), and 3 of the top 10 are corridor points. Corridor weights favour access (0.30) and the charging gap (0.25), and a corridor point far from the 5 known charging sites scores high on both. SPEC §3 says a site without a host cannot be investigated; whether such points may enter the 30 is the Milestone 6 question below.
- The 5 known charging sites are in or near Kigali, so production charging-gap scores are lowest there (5 to 9 for the top Kigali sites). Until `data/manual/chargers.csv` is filled, the charging gap mostly measures distance from Kigali.

### Milestone 5

- Resolved in Milestone 5: Recall@30, what the bootstrap resamples, which weights the stability test changes and how the 70% target is reported (D-044); seeds and bootstrap settings (D-043).
- The network section of `reports/evaluation.md` is added in Milestone 6 and the grounding section in Milestone 7.

### Milestone 6

- Resolved in Milestone 6 (D-046): hostless points are not eligible; eligibility is across all candidates; sⱼ = score / 100; demand is renormalised after the down-weighting; a time-limited result is reported with CBC's status and not called exact.
- Changing the existing-charger factor (0.25 to 0.75) leaves the network unchanged on the real data: only 5 charging sites exist, all in or near Kigali. It will matter once `data/manual/chargers.csv` is filled.

### Milestone 7

- Resolved in Milestone 7 (D-047): the brief is a "Site Evidence Brief"; no LLM writes any of it.

### Milestone 8

- Resolved in Milestone 8 (D-048): the export, the committed demo export, `generated_at`, the SVG map. Synthetic exports exist only inside tests; the pipeline writes `data_status: pipeline`.
- `app/prototype.html`, which CLAUDE.md names as the design reference, was never added; `app/index.html` was designed from scratch as a single page rather than SPEC §10's multi-view prototype.

### Milestone 9

- Resolved in Milestone 9: the M7 note that an API key "would also need an approved source" (D-050); the provider, configuration and optional SDK (D-051); the answer rules (D-052); what AI output may reach (D-053); OpenAI as a second provider (D-054).
- The SDK still reads a few variables of its own that SiteScout cannot switch off through its public API: `ANTHROPIC_CUSTOM_HEADERS` (extra request headers), `ANTHROPIC_LOG` (its log level) and the standard proxy variables of its HTTP client. None can change the provider, model, limits, timeout, temperature or any analytical value, which are passed explicitly on every request.
- **The phase 3c live evaluation ran with the OpenAI provider (`gpt-5.6-luna`) on 2026-09-25 and passed.** 18 of 20 scenarios were answered (2 ended in the deterministic fallback); 153 of 156 required evaluation points passed (98.1%, target ≥90%). All 18 shown answers passed validation; 0 ungrounded derived numbers; 0 trap values; 0 winner or ranking violations; comparison refusals and neutral comparisons all correct; grid disclaimer behaviour all correct. The full record is `reports/analyst_eval.md`; the per-scenario tool calls and answers are logged in `data/processed/analyst_eval.json` (not committed, per D-053).
- The Anthropic provider's live run has not passed: it returned HTTP 400 before any tool call. Per Gasim's instruction this was not diagnosed or rerun as part of adding OpenAI (D-054); it remains open. The committed `config/settings.yaml` default is a separate question from whether the code works — as of this evaluation, only the OpenAI path is evidenced end to end.
- `find_sites` with no filter returns all 300 candidates with their records, which is large for a model's context; every scenario in the fixed set calls it with a filter, so this stays untested.
- The next re-export will carry the `analyst:` settings in `meta.config`, like every other setting (the committed export predates them). They are configuration, not AI output.
- The OpenAI SDK also reads variables of its own that SiteScout does not switch off: `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` (sent as organization and project headers), `OPENAI_CUSTOM_HEADERS`, `OPENAI_LOG` and the proxy variables. The key and the endpoint are passed explicitly, so `OPENAI_BASE_URL` and the SDK's own key lookup are never used; no variable can change the provider, model, limits or any analytical value.
- CLAUDE.md's D-050 line names only `ANTHROPIC_API_KEY`; extending it to `OPENAI_API_KEY` (D-054) still needs Gasim's approval of the wording.

### Milestone 10

- Phase 1 (D-055, D-056): the gold set `tests/knowledge_gold.yaml` is a draft. Gasim adds at least 6 queries before the final retrieval evaluation, and the retrieval gate is defined only after that review.
- BM25 has no relevance threshold, so an out-of-scope query still returns chunks that share a word with it. `reports/knowledge_eval.md` shows how the top scores of out-of-scope queries compare with in-scope ones; no threshold is chosen from it in Phase 1.
- Some indexed documents describe an earlier state: the Pipeline section of `docs/architecture.md` still lists only the early modules. Correcting them is a documentation task for the last M10 phase.
