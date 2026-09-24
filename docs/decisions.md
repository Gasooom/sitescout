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

## Open questions

These need a decision before or during the milestone named. None has a default.

### Milestone 1

- CLAUDE.md says Milestone 1 is done when "all sources [are] in GeoParquet", but WorldPop and the elevation model are rasters. Proposal: vector data goes to GeoParquet, rasters stay as clipped GeoTIFF files, and derived tables such as H3 demand nodes go to GeoParquet.
- As far as known, geoBoundaries ADM2 features carry no province code, so deriving ADM1 from ADM2 needs a district-to-province assignment.
- `rwanda-latest.osm.pbf` changes over time. Proposal: record each download's OSM timestamp and SHA-256.
- Do fuel stations tagged `socket:*` count as existing chargers in production mode? SPEC mentions them only for removal in backtest mode.
- The radius for matching manual CSV chargers to OSM chargers (`charger_match_radius_m`, pending).
- EPSG:32735 is UTM zone 35S. The part of Rwanda east of 30°E, including most of Kigali, lies in zone 36; the distance distortion there is to be measured and documented with the distance helpers.

### Milestone 2

- `dist_town_m` assigns the profile, while CLAUDE.md says it is never scored. Proposed reading: never a weighted input, but allowed for assigning the profile.
- Deduplication keeps an industrial site over a hotel, yet the hotel's bonus is 5 points and the industrial site's is 0.
- SPEC §3 says a site without a host cannot be investigated, but keeps corridor points without a host. Should those be eligible for the 30?
- If generation gives fewer than 200 or more than 400 candidates, the run stops with an error until a rule is decided.
- Fuel stations tagged `socket:*` stay candidates in backtest mode; only their charger-related attributes are removed.

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
