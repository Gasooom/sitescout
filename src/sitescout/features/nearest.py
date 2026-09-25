"""Nearest-distance and count-within helpers, in metres, for the feature modules.

Every function takes geometries already projected to the metric CRS (EPSG:32735, through
``sitescout.crs.to_metric``); nothing here is ever given degrees. Radii are inclusive: a
feature exactly ``radius`` metres away is within it.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import shapely
from shapely.strtree import STRtree

from sitescout.config import Settings
from sitescout.crs import to_metric


class FeatureError(Exception):
    """Feature engineering cannot produce a valid result."""


def metric_points(frame: gpd.GeoDataFrame | gpd.GeoSeries, settings: Settings) -> gpd.GeoSeries:
    """One point per feature in the metric CRS: a point stays itself; a line or area becomes
    a point on its surface (the M2 host method, D-029)."""
    geometry = to_metric(
        frame if isinstance(frame, gpd.GeoSeries) else frame.geometry, settings.crs
    )
    geometry = geometry.reset_index(drop=True)
    others = ~geometry.geom_type.eq("Point")
    if others.any():
        geometry = geometry.copy()
        geometry.loc[others] = geometry[others].representative_point()
    return geometry


def nearest_distance(targets: gpd.GeoSeries, sources: gpd.GeoSeries) -> np.ndarray:
    """Distance in metres from each target to the nearest source geometry (NaN if none).

    Distances are to the source geometry itself, so a point inside an area is at 0.
    """
    distance = np.full(len(targets), np.nan, dtype=np.float64)
    if len(targets) == 0 or len(sources) == 0:
        return distance
    tree = STRtree(sources.array)
    (target_idx, _), found = tree.query_nearest(
        targets.array, return_distance=True, all_matches=True
    )
    # all_matches returns every source tied for nearest; the distance is the same for each.
    distance[target_idx] = found
    return distance


def pairs_within(
    targets: gpd.GeoSeries, sources: gpd.GeoSeries, radius_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(target index, source index, distance in metres) for every pair within ``radius_m``.

    The spatial index only proposes pairs; the inclusive test ``distance <= radius_m`` is
    applied here explicitly, so the boundary rule does not depend on the index.
    """
    empty = np.array([], dtype=np.int64)
    if len(targets) == 0 or len(sources) == 0:
        return empty, empty, np.array([], dtype=np.float64)
    tree = STRtree(sources.array)
    target_idx, source_idx = tree.query(targets.array, predicate="dwithin", distance=radius_m)
    distance = shapely.distance(
        np.asarray(targets.array)[target_idx], np.asarray(sources.array)[source_idx]
    )
    keep = distance <= radius_m
    return target_idx[keep], source_idx[keep], np.asarray(distance[keep], dtype=np.float64)


def count_within(targets: gpd.GeoSeries, sources: gpd.GeoSeries, radius_m: float) -> np.ndarray:
    """How many source geometries lie within ``radius_m`` of each target (inclusive)."""
    target_idx, _, _ = pairs_within(targets, sources, radius_m)
    return np.bincount(target_idx, minlength=len(targets)).astype(np.int64)
