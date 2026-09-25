"""Build and write the Milestone 3 feature layers, one per mode (SPEC §4, §5; D-032 to D-038).

Both modes are computed from the validated input layers independently:
``features_production`` uses the existing chargers; ``features_backtest`` removes every
charger object from every input (the charger set, POIs and power features) before anything
is computed. No backtest value is derived from a production value.

Every value is raw, in natural units: metres, people, counts and ratios. No percentile
rank, log1p, inversion, bonus, weight, profile, score or confidence is applied; those
belong to Milestone 4. Distances and areas are computed in the metric CRS; the layers keep
each candidate's point in the storage CRS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import Settings
from sitescout.crs import to_metric
from sitescout.features.chargers import (
    MODES,
    backtest_chargers,
    charger_objects,
    production_chargers,
)
from sitescout.features.demand import outside_share, population_within
from sitescout.features.grid import completeness, grid_distances
from sitescout.features.nearest import FeatureError, count_within, nearest_distance
from sitescout.features.pois import POI_TYPES, poi_counts
from sitescout.ingest.layers import (
    FEATURES_BACKTEST,
    FEATURES_PRODUCTION,
    LayerResult,
    LayerSchema,
    metadata_path,
    read_layer,
    remove_layer,
    validate_layer,
    write_layer,
)
from sitescout.ingest.metadata import read_json
from sitescout.ingest.raster import POPULATION_LAYER, read_population

logger = logging.getLogger(__name__)

INPUT_LAYERS = (
    "candidates",
    "admin_districts",
    "admin_country",
    "osm_roads",
    "osm_pois",
    "osm_charging_stations",
    "osm_power",
    "osm_places",
)
SCHEMAS: dict[str, LayerSchema] = {
    "production": FEATURES_PRODUCTION,
    "backtest": FEATURES_BACKTEST,
}
IDENTITY = (
    "candidate_id",
    "host_type",
    "host_osm_id",
    "origin",
    "district_id",
    "district",
    "province_code",
    "province",
)


def _notes(settings: Settings) -> list[str]:
    centre = settings.features.kigali_cbd
    return [
        "Raw feature values only; scoring (percentile rank, log1p, inversion, bonuses, "
        "weights, profile) is Milestone 4 (SPEC §5).",
        "Grid evidence: distances to and counts of mapped OSM power features. They say "
        "nothing about grid capacity, transformer capacity or a connection.",
        "grid_completeness_ratio is a proxy for mapping completeness, not a measure of the grid.",
        "Population: WorldPop R2025A constrained, a modelled estimate. The raster ends at the "
        "border, so circles that cross it count only people inside Rwanda "
        "(outside_rwanda_share_10km shows the share of the circle outside).",
        "Existing chargers merge within sources.charger_match_radius_m "
        f"({settings.sources.charger_match_radius_m} m): a project data-matching choice, not "
        "a definition of a charging site (D-034).",
        f"{centre.label} ({centre.osm_id}), not an official CBD boundary (D-035).",
        "Elevation and slope are not computed: the Copernicus DEM is deferred (SPEC §2, D-037).",
        "OpenStreetMap data © OpenStreetMap contributors, ODbL 1.0. WorldPop, CC BY 4.0.",
    ]


DECISIONS = {
    "D-032": "dist_trunk_m uses features.trunk_road_classes (trunk, trunk_link)",
    "D-033": "POI types by OSM key; charging stations and the candidate's own host excluded; "
    "grid evidence power values are an allow-list",
    "D-034": "production chargers: OSM (not private or no access) + socket-tagged fuel "
    "stations + manual CSV if present, merged within 50 m; backtest: none",
    "D-035": "town and city centres are OSM place=city/town nodes in Rwanda; Kigali city "
    "centre is features.kigali_cbd",
    "D-036": "population sums pixel centres within the radius; outside_rwanda_share_10km is "
    "reported only",
    "D-037": "elevation and slope deferred",
    "D-038": "two layers, production and backtest, each built from its own inputs; raw values only",
}


@dataclass(frozen=True, slots=True)
class FeatureInputs:
    """The validated layers and raster feature engineering reads, with their fingerprints."""

    layers: dict[str, gpd.GeoDataFrame]
    fingerprints: dict[str, str]
    manual: gpd.GeoDataFrame | None
    manual_status: str
    population: Path


def load_inputs(processed_dir: Path, settings: Settings) -> FeatureInputs:
    """Read every input through read_layer (or read_population), so each is re-checked."""
    layers = {name: read_layer(name, processed_dir, settings) for name in INPUT_LAYERS}
    fingerprints = {
        name: read_json(metadata_path(processed_dir, name))["content_sha256"]
        for name in INPUT_LAYERS
    }
    manual_meta = read_json(metadata_path(processed_dir, "chargers_manual"))
    if manual_meta.get("status") == "missing":
        manual = None
        manual_status = f"missing: {manual_meta.get('reason')}"
    else:
        manual = read_layer("chargers_manual", processed_dir, settings)
        manual_status = "ok"
        fingerprints["chargers_manual"] = manual_meta["content_sha256"]
    population, _ = read_population(processed_dir, settings)
    fingerprints[POPULATION_LAYER] = read_json(metadata_path(processed_dir, POPULATION_LAYER))[
        "content_sha256"
    ]
    return FeatureInputs(
        layers, dict(sorted(fingerprints.items())), manual, manual_status, population
    )


def _km(radius_m: int) -> int:
    if radius_m % 1000:
        raise FeatureError(f"Radius {radius_m} m has no whole-kilometre column name")
    return radius_m // 1000


def _places(
    layers: dict[str, gpd.GeoDataFrame], points: gpd.GeoSeries, settings: Settings
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """(dist_kigali_cbd_m, dist_town_m, stats) from the OSM place nodes (D-035)."""
    features = settings.features
    places = layers["osm_places"]
    country = to_metric(layers["admin_country"].geometry, settings.crs).iloc[0]
    metric = to_metric(places.geometry, settings.crs).reset_index(drop=True)
    in_rwanda = metric.intersects(country).to_numpy(dtype=bool)
    values = {tag.partition("=")[2] for tag in features.town_centres}
    is_centre = places["place"].isin(values).to_numpy(dtype=bool) & in_rwanda
    dist_town = nearest_distance(points, metric[is_centre])

    centre = features.kigali_cbd
    match = (places["feature_id"] == centre.osm_id).to_numpy(dtype=bool)
    if match.sum() != 1 or places.loc[match, "place"].iloc[0] != "city" or not in_rwanda[match][0]:
        raise FeatureError(
            f"{centre.label} ({centre.osm_id}) must be one place=city node inside Rwanda in "
            "osm_places; re-check features.kigali_cbd against the current extract"
        )
    dist_kigali = np.asarray(points.distance(metric[match].iloc[0]), dtype=np.float64)
    stats = {
        "town_centres": int(is_centre.sum()),
        "by_place": {
            value: int((places["place"][is_centre] == value).sum()) for value in sorted(values)
        },
        "place_nodes_outside_rwanda": int((~in_rwanda).sum()),
        "kigali_centre": {"osm_id": centre.osm_id, "label": centre.label},
    }
    return dist_kigali, dist_town, stats


def build_features(
    inputs: FeatureInputs, settings: Settings, mode: str
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """The feature table for one mode, in the storage CRS, and how it was built."""
    if mode not in MODES:
        raise FeatureError(f"Unknown mode {mode!r}; expected one of {MODES}")
    layers = inputs.layers
    features = settings.features
    candidates = layers["candidates"].sort_values(
        "candidate_id", kind="mergesort", ignore_index=True
    )
    points = to_metric(candidates.geometry, settings.crs).reset_index(drop=True)
    removed = charger_objects(layers, settings) if mode == "backtest" else set()
    columns: dict[str, Any] = {name: candidates[name].to_numpy() for name in IDENTITY}
    stats: dict[str, Any] = {"mode": mode, "candidates": len(candidates)}

    # Demand.
    population = population_within(points, inputs.population, features.population_radii_m, settings)
    for radius, values in population.items():
        columns[f"pop_{_km(radius)}km"] = values

    # Access.
    columns["dist_road_m"] = candidates["dist_road_m"].to_numpy(dtype=np.float64)
    columns["road_class"] = candidates["nearest_road_class"].to_numpy()
    roads = layers["osm_roads"]
    trunk = roads[roads["highway"].isin(features.trunk_road_classes).to_numpy(dtype=bool)]
    columns["dist_trunk_m"] = nearest_distance(points, to_metric(trunk.geometry, settings.crs))
    stats["trunk_roads"] = len(trunk)

    # Host / commercial.
    counts, stats["pois"] = poi_counts(candidates, points, layers["osm_pois"], settings, removed)
    columns["poi_1km"], columns["poi_3km"] = counts["poi_1km"], counts["poi_3km"]
    for kind in POI_TYPES:
        for radius in features.poi_radii_m:
            name = f"poi_{kind}_{_km(radius)}km"
            columns[name] = counts[name]

    # Charging gap.
    if mode == "production":
        chargers = production_chargers(layers, inputs.manual, inputs.manual_status, settings)
    else:
        chargers = backtest_chargers(settings)
    columns["dist_charger_m"] = nearest_distance(points, chargers.sites)
    for radius in features.charger_count_radii_m:
        columns[f"chargers_{_km(radius)}km"] = count_within(points, chargers.sites, radius)
    stats["chargers"] = chargers.stats

    # Grid evidence.
    power = layers["osm_power"]
    to_substation, to_line, stats["grid_features"] = grid_distances(
        points, power, settings, removed
    )
    columns["dist_substation_m"], columns["dist_line_m"] = to_substation, to_line
    table, stats["grid_completeness"] = completeness(
        power, layers["admin_districts"], settings, removed
    )
    ratio = dict(zip(table["district_id"], table["ratio"], strict=True))
    columns["grid_completeness_ratio"] = (
        candidates["district_id"].map(ratio).to_numpy(dtype=np.float64)
    )

    # Reported only.
    columns["dist_kigali_cbd_m"], columns["dist_town_m"], stats["places"] = _places(
        layers, points, settings
    )
    largest = max(features.population_radii_m)
    country = to_metric(layers["admin_country"].geometry, settings.crs).iloc[0]
    columns[f"outside_rwanda_share_{_km(largest)}km"] = outside_share(points, country, largest)
    stats["removed_charger_objects"] = sorted(removed)

    frame = gpd.GeoDataFrame(
        columns, geometry=candidates.geometry.to_numpy(), crs=settings.crs.storage
    )
    return frame, stats


def check_values(frame: gpd.GeoDataFrame, mode: str, candidate_ids: list[str]) -> None:
    """Raise FeatureError listing every value outside its documented bounds."""
    problems: list[str] = []
    if sorted(frame["candidate_id"]) != sorted(candidate_ids):
        problems.append("candidate ids differ from the candidates layer")
    for name in frame.columns:
        if name == "geometry" or not pd.api.types.is_numeric_dtype(frame[name]):
            continue
        values = frame[name].to_numpy(dtype=np.float64)
        present = values[~np.isnan(values)]
        if not np.isfinite(present).all() or (present < 0).any():
            problems.append(f"{name} has negative or infinite values")
    for small, large in (
        ("pop_1km", "pop_5km"),
        ("pop_5km", "pop_10km"),
        ("poi_1km", "poi_3km"),
        ("chargers_10km", "chargers_25km"),
        *((f"poi_{kind}_1km", f"poi_{kind}_3km") for kind in POI_TYPES),
    ):
        if (frame[small] > frame[large]).any():
            problems.append(f"{small} exceeds {large} for some candidates")
    if (frame["outside_rwanda_share_10km"] > 1).any():
        problems.append("outside_rwanda_share_10km exceeds 1")
    if mode == "backtest" and (
        frame["dist_charger_m"].notna().any()
        or frame["chargers_10km"].any()
        or frame["chargers_25km"].any()
    ):
        problems.append("backtest mode has charging-gap values from existing chargers")
    if problems:
        raise FeatureError("Feature values out of bounds: " + "; ".join(problems))


def _settings_used(settings: Settings) -> dict[str, Any]:
    return {
        "crs": settings.crs.model_dump(mode="json"),
        "features": settings.features.model_dump(mode="json"),
        "sources.charger_match_radius_m": settings.sources.charger_match_radius_m,
    }


def run_features(settings: Settings, processed_dir: Path) -> list[LayerResult]:
    """Build, check and write features_production and features_backtest.

    Both layers are checked before either is written. When anything fails, both layers
    (and any earlier ones) are removed, so a stale feature layer is never left behind.
    """
    inputs = load_inputs(processed_dir, settings)
    candidate_ids = inputs.layers["candidates"]["candidate_id"].tolist()
    built: dict[str, tuple[gpd.GeoDataFrame, dict[str, Any]]] = {}
    try:
        for mode in MODES:
            frame, stats = build_features(inputs, settings, mode)
            validate_layer(frame, SCHEMAS[mode], settings)
            check_values(frame, mode, candidate_ids)
            built[mode] = (frame, stats)
    except Exception:
        for schema in SCHEMAS.values():
            remove_layer(processed_dir, schema.name)
        raise
    results = []
    for mode, (frame, stats) in built.items():
        _log(stats)
        metadata = {
            **stats,
            "inputs": inputs.fingerprints,
            "manual_chargers": inputs.manual_status,
            "settings_used": _settings_used(settings),
            "decisions": DECISIONS,
        }
        results.append(
            write_layer(
                frame,
                SCHEMAS[mode],
                processed_dir,
                settings,
                sources=[],
                stats=metadata,
                notes=_notes(settings),
            )
        )
    return results


def _log(stats: dict[str, Any]) -> None:
    chargers = stats["chargers"]
    logger.info(
        "%s: %d candidates; %d existing charging sites; %d POIs counted; %d substations, "
        "%d power lines; %d town or city centres",
        stats["mode"],
        stats["candidates"],
        chargers["sites"],
        stats["pois"]["pois_counted"],
        stats["grid_features"]["substations"],
        stats["grid_features"]["lines"],
        stats["places"]["town_centres"],
    )
    if stats["mode"] == "production":
        logger.info(
            "Charger sources %s; merged groups %s",
            chargers["sources"],
            chargers["merged_groups"],
        )
