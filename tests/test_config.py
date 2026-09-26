"""Loading and validating config/settings.yaml, and the rules shared by both config files."""

import dataclasses
import json
import logging

import pytest
from pydantic import ValidationError

from knowledge_support import EXPECTED_MILESTONE_TOPICS, EXPECTED_SOURCES
from sitescout.config import (
    PROJECT_ROOT,
    SETTINGS_FILE,
    ConfigError,
    Pending,
    PendingParameterError,
    build_config,
    load_config,
    require,
)
from support import delete_path, flatten, set_path

# Parameters docs/SPEC.md requires but does not define. Resolving one means giving it a
# value in config/settings.yaml, removing it here and recording the decision.
EXPECTED_PENDING: set[str] = set()  # every SPEC parameter is decided since Milestone 6

# The pending mechanism is still tested, on a SYNTHETIC pending value in a copy of settings.
SYNTHETIC_PENDING = {"pending": "SYNTHETIC: SPEC §7 gives no seed in this test. Decide in M5."}

# Every defined value in config/settings.yaml, as docs/SPEC.md (and CLAUDE.md for paths and
# the log level) sets it. Changing one needs a change here and a docs/decisions.md entry.
EXPECTED_SETTINGS = {
    "paths.raw_dir": "data/raw",
    "paths.processed_dir": "data/processed",
    "paths.manual_chargers_csv": "data/manual/chargers.csv",
    "paths.export_json": "data/export/sitescout.json",
    "paths.evaluation_report": "reports/evaluation.md",
    "paths.analyst_scenarios": "tests/analyst_scenarios.yaml",
    "paths.analyst_eval_report": "reports/analyst_eval.md",
    "logging.level": "INFO",
    "crs.storage": "EPSG:4326",
    "crs.metric": "EPSG:32735",
    "sources.osm.provider": "Geofabrik",
    "sources.osm.extract": "rwanda-latest.osm.pbf",
    "sources.population.provider": "WorldPop",
    "sources.population.year": 2025,
    "sources.population.resolution_m": 100,
    "sources.population.constrained": True,
    "sources.population.release": "R2025A",
    "sources.elevation.provider": "Copernicus DEM",
    "sources.elevation.product": "GLO-30",
    "sources.elevation.status": "deferred",
    "sources.boundaries.provider": "geoBoundaries",
    "sources.boundaries.iso3": "RWA",
    "sources.boundaries.master_level": "ADM2",
    "sources.boundaries.derived_levels": ["ADM1", "ADM0"],
    "sources.grid_cross_check.provider": "energydata.info",
    "sources.grid_cross_check.dataset": "Rwanda transmission network",
    "sources.grid_cross_check.data_year": 2009,
    "sources.grid_cross_check.use": "cross_check_only",
    "sources.osm_tags.charging_station": "amenity=charging_station",
    "sources.osm_tags.fuel_station_sockets": "socket:*",
    "sources.osm_tags.grid": "power=*",
    "sources.osm_tags.water": "natural=water",
    "sources.osm_tags.protected_area": "boundary=protected_area",
    "sources.charger_csv.columns": [
        "name",
        "lat",
        "lon",
        "source_url",
        "date_retrieved",
        "operator_public_name",
    ],
    "sources.charger_csv.provenance_only_columns": ["operator_public_name"],
    "candidates.target_count.min": 200,
    "candidates.target_count.max": 400,
    "candidates.host_types": ["fuel", "mall", "supermarket", "logistics", "industrial", "hotel"],
    "candidates.dedup.radius_m": 300,
    "candidates.dedup.host_priority": [
        "fuel",
        "mall",
        "supermarket",
        "logistics",
        "industrial",
        "hotel",
        "none",
    ],
    "candidates.corridor.spacing_m": 10000,
    "candidates.corridor.road_classes": ["trunk", "primary"],
    "candidates.corridor.host_snap_radius_m": 2000,
    "candidates.filters.max_road_distance_m": 500,
    "candidates.filters.exclude_areas": ["water", "protected_area"],
    "features.population_radii_m": [1000, 5000, 10000],
    "features.poi_radii_m": [1000, 3000],
    "features.charger_count_radii_m": [10000, 25000],
    "features.reported_only": [
        "dist_town_m",
        "dist_kigali_cbd_m",
        "outside_rwanda_share_10km",
        "elevation",
        "slope",
    ],
    "scoring.scale_max": 100,
    "scoring.grid_evidence.missing_radius_m": 5000,
    "scoring.grid_evidence.missing_component_score": 0,
    "confidence.grid_evidence_missing_level": "Low",
    "confidence.universal_unknowns": [
        "grid connection capacity",
        "transformer capacity",
        "land availability",
        "landowner willingness",
        "permit requirements",
    ],
    "evaluation.label": "retrospective plausibility test",
    "evaluation.backtest.hit_radius_m": 1000,
    "evaluation.backtest.precision_at": [10, 20, 30],
    "evaluation.backtest.recall_at": 30,
    "evaluation.random_baseline_seeds": 1000,
    "evaluation.stability.perturbation": 0.20,
    "evaluation.stability.renormalize": True,
    "evaluation.stability.top_k": 30,
    "evaluation.stability.min_overlap": 0.70,
    "evaluation.grounding_target": 1.0,
    "optimization.demand_h3_resolution": 7,
    "optimization.service_radius_m": 10000,
    "optimization.n_sites": 30,
    "optimization.lambda": 0.01,
    "optimization.min_spacing_m": 2000,
    "optimization.min_score_percentile": 50,
    "optimization.existing_charger_demand_factor": 0.5,
    "optimization.time_limit_s": 300,
    "export.boundary_geojson_max_kb": 300,
}

# Milestone 1 values that SPEC.md does not set: verified source locations, licences and
# credits (D-016), retrieved facts (D-016, D-018) and the extraction scope (D-019).
# Changing one needs a change here and a docs/decisions.md entry.
_CC_BY = "https://creativecommons.org/licenses/by/4.0/"
_GB = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/RWA"
EXPECTED_M1_SETTINGS = {
    "sources.osm.download.url": "https://download.geofabrik.de/africa/rwanda-latest.osm.pbf",
    "sources.osm.download.versioning": "rolling",
    "sources.osm.download.licence": "ODbL-1.0",
    "sources.osm.download.licence_url": "https://www.openstreetmap.org/copyright",
    "sources.osm.download.credit": "© OpenStreetMap contributors",
    "sources.population.pixel_size_arcsec": 3,
    "sources.population.download.url": (
        "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/RWA/v1/100m/"
        "constrained/rwa_pop_2025_CN_100m_R2025A_v1.tif"
    ),
    "sources.population.download.versioning": "fixed",
    "sources.population.download.licence": "CC-BY-4.0",
    "sources.population.download.licence_url": "https://hub.worldpop.org/data/licence.txt",
    "sources.population.download.credit": (
        "WorldPop, University of Southampton (2025). Constrained population estimates, "
        "R2025A v1. DOI 10.5258/SOTON/WP00839"
    ),
    "sources.boundaries.expected_units.ADM2": 30,
    "sources.boundaries.expected_units.ADM1": 5,
    "sources.boundaries.downloads.ADM2.url": f"{_GB}/ADM2/geoBoundaries-RWA-ADM2.geojson",
    "sources.boundaries.downloads.ADM2.versioning": "fixed",
    "sources.boundaries.downloads.ADM2.licence": "CC-BY-4.0",
    "sources.boundaries.downloads.ADM2.licence_url": _CC_BY,
    "sources.boundaries.downloads.ADM2.credit": (
        "geoBoundaries (Runfola et al. 2020), gbOpen RWA ADM2; source: Open Data Rwanda (NISR)"
    ),
    "sources.boundaries.downloads.ADM1.url": f"{_GB}/ADM1/geoBoundaries-RWA-ADM1.geojson",
    "sources.boundaries.downloads.ADM1.versioning": "fixed",
    "sources.boundaries.downloads.ADM1.licence": "CC-BY-4.0",
    "sources.boundaries.downloads.ADM1.licence_url": _CC_BY,
    "sources.boundaries.downloads.ADM1.credit": (
        "geoBoundaries (Runfola et al. 2020), gbOpen RWA ADM1; source: The Rwanda Geo Portal"
    ),
    "sources.grid_cross_check.download.url": (
        "https://datacatalogfiles.worldbank.org/ddh-published/0042268/1/DR0052867/"
        "rwanda-electricity-transmission-network.zip"
    ),
    "sources.grid_cross_check.download.versioning": "fixed",
    "sources.grid_cross_check.download.licence": "CC-BY-4.0",
    "sources.grid_cross_check.download.licence_url": _CC_BY,
    "sources.grid_cross_check.download.credit": (
        "World Bank Group, Rwanda Electricity Transmission Network, via energydata.info"
    ),
    "sources.osm_tags.national_park": "boundary=national_park",
    "sources.osm_tags.roads": "highway=*",
    "sources.osm_tags.pois": [
        "amenity=*",
        "shop=*",
        "tourism=*",
        "office=*",
        "industrial=*",
        "landuse=industrial",
        "landuse=commercial",
        "landuse=retail",
        "building=warehouse",
        "building=industrial",
        "building=commercial",
        "building=retail",
        "man_made=works",
    ],
    "ingest.rwanda_bbox.min_lon": 28.85,
    "ingest.rwanda_bbox.min_lat": -2.85,
    "ingest.rwanda_bbox.max_lon": 30.91,
    "ingest.rwanda_bbox.max_lat": -1.03,
}


# --- The real files ----------------------------------------------------------------------


def test_real_config_files_load():
    config = load_config()
    assert config.settings.optimization.n_sites == 30
    assert config.weights.profiles.urban.demand == 0.30


# Milestone 2 decisions for parameters SPEC.md left pending (D-027, D-028) and the
# candidate budget Gasim set (D-031).
EXPECTED_M2_SETTINGS = {
    "candidates.budget.size": 300,
    "candidates.host_osm_tags.fuel": ["amenity=fuel"],
    "candidates.host_osm_tags.mall": ["shop=mall"],
    "candidates.host_osm_tags.supermarket": ["shop=supermarket"],
    "candidates.host_osm_tags.logistics": [
        "building=warehouse",
        "industrial=depot",
        "industrial=intermodal_freight_terminal",
    ],
    "candidates.host_osm_tags.industrial": ["landuse=industrial", "man_made=works"],
    "candidates.host_osm_tags.hotel": ["tourism=hotel"],
    "candidates.drivable_road_classes": [
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
    ],
}


# Milestone 3 decisions for parameters SPEC.md left pending (D-032 to D-036).
EXPECTED_M3_SETTINGS = {
    "sources.charger_match_radius_m": 50,
    "sources.osm_tags.places": ["place=city", "place=town"],
    "features.trunk_road_classes": ["trunk", "trunk_link"],
    "features.town_centres": ["place=city", "place=town"],
    "features.kigali_cbd.osm_id": "node/60485579",
    "features.kigali_cbd.label": "Kigali city centre as mapped in OSM",
    "features.poi_types.amenity": ["amenity=*"],
    "features.poi_types.shop": ["shop=*"],
    "features.poi_types.tourism": ["tourism=*"],
    "features.poi_types.office": ["office=*"],
    "features.poi_types.industrial": ["industrial=*"],
    "features.poi_exclude": ["amenity=charging_station"],
    "features.grid_osm_tags.substation": ["power=substation"],
    "features.grid_osm_tags.line": ["power=line", "power=minor_line", "power=cable"],
    "features.grid_osm_tags.completeness": [
        "power=line",
        "power=minor_line",
        "power=cable",
        "power=substation",
        "power=transformer",
        "power=tower",
        "power=pole",
        "power=portal",
        "power=plant",
        "power=generator",
        "power=connection",
    ],
    "features.chargers.exclude_access": ["private", "no"],
    "features.chargers.fuel_sockets_count_as_chargers": True,
}


# Milestone 4 decisions for parameters SPEC.md left pending (D-039 to D-041).
EXPECTED_M4_SETTINGS = {
    "candidates.profile.town_radius_m": 3000,
    "candidates.profile.kigali_radius_m": 10000,
    "scoring.log1p_features": ["poi_1km", "poi_3km", "chargers_10km", "chargers_25km"],
    "scoring.lower_is_better": [
        "dist_road_m",
        "dist_trunk_m",
        "dist_substation_m",
        "dist_line_m",
        "chargers_10km",
        "chargers_25km",
    ],
    "confidence.sparse_coverage_threshold": 0.5,
    "confidence.remote_location_rule.max_poi_3km": 0,
    "confidence.factor_count_levels": ["High", "Medium", "Low"],
}


# Milestone 5 decisions (D-043).
EXPECTED_M5_SETTINGS = {
    "evaluation.random_seed": 20260924,
    "evaluation.bootstrap.resamples": 1000,
    "evaluation.bootstrap.confidence_level": 0.95,
}


# Milestone 6 decisions (D-046).
EXPECTED_M6_SETTINGS = {
    "optimization.require_host": True,
    "optimization.sensitivity.lambda": [0.0, 0.01, 0.05],
    "optimization.sensitivity.service_radius_m": [5000, 10000, 15000],
    "optimization.sensitivity.existing_charger_demand_factor": [0.25, 0.5, 0.75],
}


# Milestone 9 decisions (D-051): the optional AI Site Analyst, from YAML only.
EXPECTED_M9_SETTINGS = {
    "analyst.provider": "openai",
    "analyst.model": "gpt-5.6-luna",
    "analyst.max_tool_calls": 6,
    "analyst.max_tokens": 4096,
    "analyst.timeout_s": 60.0,
    "analyst.temperature": None,
}


# Milestone 10, Phase 1 decisions (D-055, D-056): the local project-knowledge index.
EXPECTED_M10_SETTINGS = {
    "paths.knowledge_gold": "tests/knowledge_gold.yaml",
    "paths.knowledge_eval_report": "reports/knowledge_eval.md",
    "knowledge.sources": EXPECTED_SOURCES,
    "knowledge.chunk.max_chars": 1500,
    "knowledge.chunk.min_chars": 200,
    "knowledge.bm25.k1": 1.2,
    "knowledge.bm25.b": 0.75,
    "knowledge.retrieval.max_top_k": 10,
    "knowledge.retrieval.max_query_chars": 500,
    "knowledge.milestone_topics": EXPECTED_MILESTONE_TOPICS,
}


# Milestone 10, Phase 2 decision (D-057): the investigation tools.
EXPECTED_M10_PHASE2_SETTINGS = {"investigation.nearby_sites.max_results": 10}


def test_settings_match_spec():
    assert flatten(load_config().snapshot()["settings"]) == {
        **EXPECTED_SETTINGS,
        **EXPECTED_M1_SETTINGS,
        **EXPECTED_M2_SETTINGS,
        **EXPECTED_M3_SETTINGS,
        **EXPECTED_M4_SETTINGS,
        **EXPECTED_M5_SETTINGS,
        **EXPECTED_M6_SETTINGS,
        **EXPECTED_M9_SETTINGS,
        **EXPECTED_M10_SETTINGS,
        **EXPECTED_M10_PHASE2_SETTINGS,
    }


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("knowledge.chunk.max_chars", 0, "greater than 0"),
        ("knowledge.chunk.min_chars", 1500, "min_chars must be below max_chars"),
        ("knowledge.chunk.max_chars", "1500", "Input should be a valid integer"),
        ("knowledge.bm25.k1", 0, "greater than 0"),
        ("knowledge.bm25.k1", "1.2", "must be a number"),
        ("knowledge.bm25.b", 1.5, "less than or equal to 1"),
        ("knowledge.bm25.b", True, "must be a number"),
        ("knowledge.retrieval.max_top_k", 0, "greater than 0"),
        ("knowledge.retrieval.max_query_chars", -5, "greater than 0"),
        ("knowledge.sources", [], "at least 1 item"),
        ("knowledge.milestone_topics", [], "at least 1 item"),
        (
            "knowledge.milestone_topics",
            [{"milestone": 0, "topic": "scoring"}],
            "greater than or equal to 1",
        ),
        (
            "knowledge.milestone_topics",
            [{"milestone": 11, "topic": "scoring"}],
            "less than or equal to 10",
        ),
        ("paths.knowledge_gold", "/etc/gold.yaml", "must be relative"),
        ("paths.knowledge_eval_report", "reports\\out.md", "use forward slashes"),
    ],
)
def test_knowledge_settings_are_checked(settings_data, weights_data, dotted, value, message):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


def test_the_knowledge_block_rejects_unknown_keys(settings_data, weights_data):
    settings_data["knowledge"]["embedding_model"] = "anything"
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        build_config(settings_data, weights_data)


def test_the_knowledge_manifest_cannot_list_a_document_twice(settings_data, weights_data):
    sources = settings_data["knowledge"]["sources"]
    sources.append(dict(sources[0]))
    with pytest.raises(ConfigError, match="lists a document twice"):
        build_config(settings_data, weights_data)


def test_the_knowledge_manifest_cannot_list_a_milestone_twice(settings_data, weights_data):
    settings_data["knowledge"]["milestone_topics"].append({"milestone": 1, "topic": "scoring"})
    with pytest.raises(ConfigError, match="lists a milestone twice"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    "path", ["CLAUDE.md", "reports/evaluation.md", "tests/notes.md", "data/x.md"]
)
def test_the_knowledge_manifest_refuses_a_denied_document(settings_data, weights_data, path):
    settings_data["knowledge"]["sources"][0]["path"] = path
    with pytest.raises(ConfigError, match="may never be indexed"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("investigation.nearby_sites.max_results", 0, "greater than 0"),
        ("investigation.nearby_sites.max_results", -3, "greater than 0"),
        ("investigation.nearby_sites.max_results", "10", "valid integer"),
        ("investigation.nearby_sites.max_results", 2.5, "valid integer"),
        ("investigation.nearby_sites.max_results", True, "valid integer"),
    ],
)
def test_investigation_settings_are_checked(settings_data, weights_data, dotted, value, message):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    "dotted",
    ["investigation.max_radius_m", "investigation.nearby_sites.radius_m"],
)
def test_the_investigation_block_takes_no_radius_setting(settings_data, weights_data, dotted):
    parents, _, leaf = dotted.rpartition(".")
    set_path(settings_data, f"{parents}.{leaf}", 5000)
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize("block", ["investigation", "investigation.nearby_sites"])
def test_the_investigation_block_is_required(settings_data, weights_data, block):
    delete_path(settings_data, block)
    with pytest.raises(ConfigError, match="investigation"):
        build_config(settings_data, weights_data)


def test_the_knowledge_block_is_required(settings_data, weights_data):
    del settings_data["knowledge"]
    with pytest.raises(ConfigError, match="knowledge"):
        build_config(settings_data, weights_data)


def test_no_knowledge_setting_is_pending_and_an_override_cannot_index_a_denied_path():
    assert not any(key.startswith("settings.knowledge") for key in load_config().pending())
    denied = [{**EXPECTED_SOURCES[0], "path": "CLAUDE.md"}]
    with pytest.raises(ConfigError, match="may never be indexed"):
        load_config(overrides={"settings.knowledge.sources": denied})


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("scoring.log1p_features", ["poi_1km", "dist_town_m"], "are not weighted features"),
        ("scoring.lower_is_better", ["dist_kigali_cbd_m"], "are not weighted features"),
        ("scoring.log1p_features", ["poi_1km", "poi_1km"], "duplicates"),
        ("confidence.sparse_coverage_threshold", 0, "greater than 0"),
        ("confidence.remote_location_rule.max_poi_3km", -1, "greater than or equal to 0"),
        ("confidence.factor_count_levels", ["High", "Certain"], "Input should be"),
        ("candidates.profile.kigali_radius_m", 3000, "Kigali radius must be larger"),
    ],
    ids=[
        "log1p-reported-feature",
        "inverted-reported-feature",
        "log1p-duplicate",
        "zero-threshold",
        "negative-poi",
        "unknown-level",
        "kigali-not-larger",
    ],
)
def test_scoring_settings_are_checked(settings_data, weights_data, dotted, value, message):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("analyst.provider", "gemini", "Input should be 'anthropic' or 'openai'"),
        ("analyst.model", "", "at least 1 character"),
        ("analyst.max_tool_calls", 0, "greater than or equal to 1"),
        ("analyst.max_tool_calls", 21, "less than or equal to 20"),
        ("analyst.max_tool_calls", 6.5, "valid integer"),
        ("analyst.max_tokens", 0, "greater than 0"),
        ("analyst.timeout_s", 0, "greater than 0"),
        ("analyst.timeout_s", "60", "must be a number"),
        ("analyst.temperature", 1.5, "less than or equal to 1"),
        ("analyst.temperature", True, "must be a number"),
        ("analyst.api_key", "sk-anything", "Extra inputs are not permitted"),
    ],
    ids=[
        "unknown-provider",
        "empty-model",
        "no-tool-calls",
        "too-many-tool-calls",
        "fractional-tool-calls",
        "no-tokens",
        "zero-timeout",
        "string-timeout",
        "temperature-above-one",
        "boolean-temperature",
        "no-key-in-yaml",
    ],
)
def test_analyst_settings_are_checked(settings_data, weights_data, dotted, value, message):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("provider", "model"),
    [("openai", "claude-sonnet-5"), ("anthropic", "gpt-any")],
    ids=["openai-with-a-claude-model", "anthropic-with-a-non-claude-model"],
)
def test_analyst_provider_and_model_must_match(settings_data, weights_data, provider, model):
    # Independent of the live YAML's own provider/model (D-054 lets it name either):
    # both fields are set explicitly here to create a genuine mismatch.
    settings_data["analyst"].update(provider=provider, model=model)
    with pytest.raises(ConfigError, match=f"does not belong to provider {provider!r}"):
        build_config(settings_data, weights_data)


def test_the_analyst_can_select_openai_in_yaml(settings_data, weights_data):
    # D-054: provider and model are set together in YAML; nothing else changes.
    settings_data["analyst"].update(provider="openai", model="gpt-any")
    analyst = build_config(settings_data, weights_data).settings.analyst
    assert (analyst.provider, analyst.model) == ("openai", "gpt-any")


def test_analyst_temperature_may_be_left_out_of_requests(settings_data, weights_data):
    settings_data["analyst"]["temperature"] = None
    assert build_config(settings_data, weights_data).settings.analyst.temperature is None


def test_the_analyst_block_is_required(settings_data, weights_data):
    del settings_data["analyst"]
    with pytest.raises(ConfigError, match="analyst"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("features.trunk_road_classes", ["trunk", "track"], "are not drivable"),
        ("features.poi_types.office", ["craft=*"], "not extracted into osm_pois"),
        ("features.poi_exclude", ["amenity=restaurant"], "must list amenity=charging_station"),
        ("features.town_centres", ["place=village"], "not in sources.osm_tags.places"),
        ("features.grid_osm_tags.line", ["power=*"], "must name one power value"),
        ("features.grid_osm_tags.substation", ["man_made=works"], "must name one power value"),
        ("features.grid_osm_tags.substation", ["power=plant_x"], "also be in the completeness"),
        ("features.kigali_cbd.osm_id", "way/1", "String should match pattern"),
        ("features.chargers.exclude_access", ["private", "private"], "duplicates"),
        ("sources.charger_match_radius_m", 0, "greater than 0"),
    ],
    ids=[
        "trunk-not-drivable",
        "poi-outside-extraction",
        "charging-stations-not-excluded",
        "town-not-extracted",
        "power-wildcard",
        "power-other-key",
        "distance-value-not-counted",
        "kigali-not-a-node",
        "duplicate-access",
        "zero-match-radius",
    ],
)
def test_feature_settings_are_checked(settings_data, weights_data, dotted, value, message):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


def test_restaurants_and_tracks_are_never_selected():
    candidates = load_config().settings.candidates
    tags = [
        tag for host in candidates.host_types for tag in getattr(candidates.host_osm_tags, host)
    ]
    assert not any("restaurant" in tag for tag in tags)
    assert "track" not in candidates.drivable_road_classes


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("candidates.host_osm_tags.hotel", ["tourism=hotel", "amenity=fuel"], "both fuel and"),
        ("candidates.host_osm_tags.mall", ["craft=brewery"], "not extracted into osm_pois"),
        ("candidates.drivable_road_classes", ["residential", "primary"], "not drivable"),
        ("candidates.budget.size", 450, "must lie within target_count 200-400"),
        ("candidates.budget.size", 150, "must lie within target_count 200-400"),
    ],
    ids=[
        "tag-in-two-host-types",
        "tag-outside-extraction",
        "corridor-not-drivable",
        "budget-above-target",
        "budget-below-target",
    ],
)
def test_candidate_selection_is_checked_across_settings(
    settings_data, weights_data, dotted, value, message
):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError, match=message):
        build_config(settings_data, weights_data)


def test_pending_parameters_are_exactly_the_documented_list():
    pending = load_config().pending()
    assert set(pending) == EXPECTED_PENDING
    for key, reason in pending.items():
        assert "SPEC" in reason, key
        assert "Decide in M" in reason, key


def test_snapshot_is_json_and_rebuilds_the_same_config():
    config = load_config()
    snapshot = json.loads(json.dumps(config.snapshot()))
    assert build_config(snapshot["settings"], snapshot["weights"]) == config


# --- Pending parameters ------------------------------------------------------------------


def _with_pending_seed(settings_data, weights_data):
    settings_data["evaluation"]["random_seed"] = dict(SYNTHETIC_PENDING)
    return build_config(settings_data, weights_data)


def test_reading_a_pending_parameter_raises(settings_data, weights_data):
    seed = _with_pending_seed(settings_data, weights_data).settings.evaluation.random_seed
    assert isinstance(seed, Pending)
    with pytest.raises(PendingParameterError, match=r"random_seed is pending: SYNTHETIC"):
        require(seed, "evaluation.random_seed")


def test_a_pending_parameter_is_listed(settings_data, weights_data):
    config = _with_pending_seed(settings_data, weights_data)
    assert config.pending() == {"settings.evaluation.random_seed": SYNTHETIC_PENDING["pending"]}


def test_a_pending_parameter_cannot_pass_for_a_value(settings_data, weights_data):
    pending = _with_pending_seed(settings_data, weights_data).settings.evaluation.random_seed
    with pytest.raises(PendingParameterError):
        bool(pending)
    with pytest.raises(PendingParameterError):
        iter(pending)


def test_require_returns_decided_values():
    assert require(load_config().settings.optimization.n_sites, "optimization.n_sites") == 30


@pytest.mark.parametrize("dotted", ["evaluation.random_seed", "optimization.sensitivity"])
def test_pending_parameters_are_required_keys(settings_data, weights_data, dotted):
    delete_path(settings_data, dotted)
    with pytest.raises(ConfigError, match="Field required"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(("town", "kigali", "valid"), [(1, 2, True), (2, 2, False), (2, 1, False)])
def test_kigali_radius_must_be_larger_once_both_are_decided(
    settings_data, weights_data, town, kigali, valid
):
    settings_data["candidates"]["profile"] = {"town_radius_m": town, "kigali_radius_m": kigali}
    if valid:
        build_config(settings_data, weights_data)
    else:
        with pytest.raises(ConfigError, match="Kigali radius must be larger"):
            build_config(settings_data, weights_data)


# --- Strict validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "dotted"),
    [
        ("settings", "unexpected"),
        ("settings", "optimization.unexpected"),
        ("settings", "candidates.profile.unexpected"),
        ("weights", "unexpected"),
        ("weights", "components.demand.pop_2km"),
        ("weights", "bonuses.access.unexpected"),
    ],
)
def test_unknown_keys_are_rejected(settings_data, weights_data, document, dotted):
    set_path(settings_data if document == "settings" else weights_data, dotted, 1)
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("document", "dotted"),
    [
        ("settings", "paths"),
        ("settings", "optimization.n_sites"),
        ("settings", "sources.population.release"),
        ("weights", "profiles.corridor.access"),
        ("weights", "bonuses.host_commercial.host_type.hotel"),
    ],
)
def test_missing_keys_are_rejected(settings_data, weights_data, document, dotted):
    delete_path(settings_data if document == "settings" else weights_data, dotted)
    with pytest.raises(ConfigError, match="Field required"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("optimization.n_sites", "30"),
        ("optimization.n_sites", 30.0),
        ("optimization.n_sites", True),
        ("optimization.lambda", "0.01"),
        ("optimization.lambda", True),
        ("sources.population.constrained", "true"),
        ("sources.population.constrained", 1),
        ("crs.metric", 32735),
        ("candidates.host_types", "fuel"),
    ],
)
def test_wrong_types_are_rejected_not_converted(settings_data, weights_data, dotted, value):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("optimization.lambda", -0.01),
        ("optimization.min_score_percentile", 0),
        ("optimization.min_score_percentile", 100),
        ("optimization.existing_charger_demand_factor", 1.5),
        ("optimization.demand_h3_resolution", 16),
        ("candidates.dedup.radius_m", 0),
        ("candidates.target_count.min", 500),
        ("features.population_radii_m", [5000, 1000, 10000]),
        ("evaluation.stability.min_overlap", 1.2),
        ("crs.metric", "UTM 35S"),
        ("sources.osm_tags.water", "natural water"),
    ],
)
def test_out_of_range_values_are_rejected(settings_data, weights_data, dotted, value):
    set_path(settings_data, dotted, value)
    with pytest.raises(ConfigError):
        build_config(settings_data, weights_data)


def test_restaurants_are_not_a_host_type(settings_data, weights_data):
    settings_data["candidates"]["host_types"].append("restaurant")
    with pytest.raises(ConfigError, match="restaurant"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    "priority",
    [
        ["fuel", "mall", "supermarket", "logistics", "industrial", "hotel"],
        ["fuel", "fuel", "mall", "supermarket", "logistics", "industrial", "hotel", "none"],
    ],
    ids=["missing-none", "duplicate"],
)
def test_host_priority_lists_each_host_type_once(settings_data, weights_data, priority):
    settings_data["candidates"]["dedup"]["host_priority"] = priority
    with pytest.raises(ConfigError, match="host_priority"):
        build_config(settings_data, weights_data)


def test_duplicate_yaml_keys_are_rejected(temp_dir):
    text = SETTINGS_FILE.read_text(encoding="utf-8")
    text = text.replace("  n_sites: 30\n", "  n_sites: 30\n  n_sites: 25\n")
    assert text.count("n_sites:") == 2
    duplicated = temp_dir / "settings.yaml"
    duplicated.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="Duplicate key 'n_sites'"):
        load_config(settings_file=duplicated)


def test_loaded_config_cannot_be_changed():
    config = load_config()
    with pytest.raises(ValidationError, match="frozen"):
        config.settings.optimization.n_sites = 10
    with pytest.raises(ValidationError, match="frozen"):
        config.weights.profiles.urban.demand = 0.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.settings = config.settings  # type: ignore[misc]
    assert isinstance(config.settings.features.population_radii_m, tuple)
    assert isinstance(config.settings.candidates.host_types, tuple)


# --- Environment and paths ----------------------------------------------------------------


def test_environment_variables_and_dotenv_files_are_ignored(monkeypatch, temp_dir):
    baseline = load_config().snapshot()
    (temp_dir / ".env").write_text("N_SITES=5\nLAMBDA=0.5\n", encoding="utf-8")
    with monkeypatch.context() as patch:  # undone before temp_dir is removed
        for name in (
            "N_SITES",
            "LAMBDA",
            "OPTIMIZATION__N_SITES",
            "SITESCOUT_N_SITES",
            "SITESCOUT_OPTIMIZATION__N_SITES",
            "SETTINGS__OPTIMIZATION__LAMBDA",
            "ANALYST__MODEL",
            "SITESCOUT_ANALYST_MODEL",
            "SITESCOUT_ANALYST__MAX_TOOL_CALLS",
            "ANTHROPIC_MODEL",
        ):
            patch.setenv(name, "5")
        patch.chdir(temp_dir)
        assert load_config().snapshot() == baseline


@pytest.mark.parametrize(
    "bad_path",
    ["/data/raw", "C:/data/raw", "C:\\data\\raw", "data\\raw", "../outside", "data/../../x", ""],
)
def test_paths_must_be_relative_and_inside_the_repository(settings_data, weights_data, bad_path):
    settings_data["paths"]["raw_dir"] = bad_path
    with pytest.raises(ConfigError):
        build_config(settings_data, weights_data)


def test_paths_resolve_against_the_repository_not_the_working_directory(monkeypatch, temp_dir):
    with monkeypatch.context() as patch:  # undone before temp_dir is removed
        patch.chdir(temp_dir)
        config = load_config()
        resolved = config.resolve(config.settings.paths.raw_dir)
    assert resolved == PROJECT_ROOT / "data" / "raw"


# --- Overrides (sensitivity runs) ---------------------------------------------------------


def test_overrides_are_applied_and_logged(caplog, settings_data, weights_data):
    with caplog.at_level(logging.WARNING, logger="sitescout.config"):
        config = build_config(
            settings_data, weights_data, overrides={"settings.optimization.lambda": 0.02}
        )
    assert config.settings.optimization.lambda_ == 0.02
    assert "settings.optimization.lambda: 0.01 -> 0.02" in caplog.text


def test_overrides_are_validated_like_the_files(settings_data, weights_data):
    with pytest.raises(ConfigError, match="must sum to 1"):
        build_config(settings_data, weights_data, overrides={"weights.profiles.urban.demand": 0.5})


@pytest.mark.parametrize(
    "key",
    [
        "optimization.lambda",
        "settings",
        "other.optimization.lambda",
        "settings.optimization.unknown",
        "settings.unknown.lambda",
    ],
)
def test_unknown_override_keys_are_rejected(settings_data, weights_data, key):
    with pytest.raises(ConfigError, match="Override"):
        build_config(settings_data, weights_data, overrides={key: 0.02})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("settings.evaluation.random_seed", 1),
        ("settings.evaluation", {"label": "replaced"}),
        ("settings.optimization.n_sites", {"pending": "created by an override"}),
    ],
    ids=["fill-pending", "replace-block-with-pending", "create-pending"],
)
def test_overrides_cannot_fill_or_create_pending_parameters(
    settings_data, weights_data, key, value
):
    settings_data["evaluation"]["random_seed"] = dict(SYNTHETIC_PENDING)
    with pytest.raises(ConfigError, match="pending parameter"):
        build_config(settings_data, weights_data, overrides={key: value})
