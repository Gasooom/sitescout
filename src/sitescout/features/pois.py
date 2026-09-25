"""Host / commercial features: POI counts within 1 and 3 km, in total and by type (SPEC §4).

A POI is a row of ``osm_pois`` that matches any selector of ``features.poi_types`` (one type
per OSM key, D-033) and none of ``features.poi_exclude`` (charging stations, in both modes).
``poi_1km`` and ``poi_3km`` count distinct POIs, so a POI of two types counts once there and
once in each type's reported count. An area POI is represented by a point on its surface
(the M2 host method). A candidate's own host (``host_osm_id``) is never counted for it;
other hosts nearby are real places and are counted. In backtest mode the caller also removes
every charger object (fuel stations tagged ``socket:*``) before counting.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import PoiTypes, Settings
from sitescout.features.nearest import FeatureError, metric_points, pairs_within

POI_TYPES = tuple(PoiTypes.model_fields)


class PoiError(FeatureError):
    """The POI layer cannot answer a configured selector."""


def tag_mask(frame: pd.DataFrame, selector: str) -> np.ndarray:
    """Rows matching an OSM ``key=value`` or ``key=*`` selector."""
    key, _, value = selector.partition("=")
    if key not in frame.columns:
        raise PoiError(f"POI selector {selector!r}: the layer has no column {key!r}")
    column = frame[key]
    return (column.notna() if value == "*" else column == value).to_numpy(dtype=bool)


def classify_pois(pois: gpd.GeoDataFrame, settings: Settings, removed: set[str]) -> pd.DataFrame:
    """One boolean column per POI type; rows that are no POI of any type are dropped.

    ``removed`` holds feature ids taken out beforehand (backtest charger objects).
    """
    features = settings.features
    excluded = np.zeros(len(pois), dtype=bool)
    for selector in features.poi_exclude:
        excluded |= tag_mask(pois, selector)
    excluded |= pois["feature_id"].isin(removed).to_numpy(dtype=bool)
    flags = {}
    for kind in POI_TYPES:
        mask = np.zeros(len(pois), dtype=bool)
        for selector in getattr(features.poi_types, kind):
            mask |= tag_mask(pois, selector)
        flags[kind] = mask & ~excluded
    table = pd.DataFrame(flags, index=pois.index)
    return table[table.any(axis=1)]


def poi_counts(
    candidates: gpd.GeoDataFrame,
    candidate_points: gpd.GeoSeries,
    pois: gpd.GeoDataFrame,
    settings: Settings,
    removed: set[str],
) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    """POI count columns for each candidate, and how many POIs were counted by type."""
    radii = settings.features.poi_radii_m
    kinds = classify_pois(pois, settings, removed)
    selected = pois.loc[kinds.index]
    points = metric_points(selected, settings)
    flags = kinds.to_numpy(dtype=bool)
    target, source, distance = pairs_within(candidate_points, points, max(radii))
    own_host = candidates["host_osm_id"].to_numpy(dtype=object)[target]
    keep = selected["feature_id"].to_numpy(dtype=object)[source] != own_host
    target, source, distance = target[keep], source[keep], distance[keep]

    columns: dict[str, np.ndarray] = {}
    for radius in radii:
        km = _km(radius)
        near = distance <= radius
        columns[f"poi_{km}km"] = _count(target[near], len(candidates))
        for position, kind in enumerate(POI_TYPES):
            of_kind = near & flags[source, position]
            columns[f"poi_{kind}_{km}km"] = _count(target[of_kind], len(candidates))
    stats = {
        "pois_counted": len(selected),
        "by_type": {kind: int(kinds[kind].sum()) for kind in POI_TYPES},
    }
    return columns, stats


def _count(target: np.ndarray, size: int) -> np.ndarray:
    return np.bincount(target, minlength=size).astype(np.int64)


def _km(radius_m: int) -> int:
    if radius_m % 1000:
        raise PoiError(f"Radius {radius_m} m has no whole-kilometre column name")
    return radius_m // 1000
