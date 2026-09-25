"""Grid-evidence features from mapped OSM power infrastructure (SPEC §4, D-033).

- ``dist_substation_m``: to the nearest ``features.grid_osm_tags.substation`` feature (its
  mapped geometry, so a point inside a substation area is at 0).
- ``dist_line_m``: to the nearest ``features.grid_osm_tags.line`` feature.
- ``grid_completeness_ratio``: a proxy for how completely the district's grid is mapped:
  the number of ``completeness`` features that intersect the district, per km² of district
  area, divided by the national median of that density over all districts. A feature that
  crosses several districts counts in each. A district with none gets 0; a national median
  of 0 stops the run, because the ratio is then undefined.

Only the listed ``power`` values ever count; values such as ``150kWh`` are ignored. These are
distances to, and counts of, mapped features: evidence that grid infrastructure is mapped
nearby, never a statement about capacity, voltage available or a connection. The 2009
transmission-line layer is a cross-check only (SPEC §2) and is not used here.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.strtree import STRtree

from sitescout.config import Settings
from sitescout.crs import to_metric
from sitescout.features.nearest import FeatureError, nearest_distance


class GridError(FeatureError):
    """The grid-completeness proxy cannot be computed."""


def power_values(tags: tuple[str, ...]) -> set[str]:
    """The power values named by ``power=<value>`` selectors (validated in config)."""
    return {tag.partition("=")[2] for tag in tags}


def select_power(
    power: gpd.GeoDataFrame, tags: tuple[str, ...], removed: set[str]
) -> gpd.GeoDataFrame:
    """Power features with one of the listed values, minus the ``removed`` feature ids."""
    keep = power["power"].isin(power_values(tags)) & ~power["feature_id"].isin(removed)
    return power[keep.to_numpy(dtype=bool)]


def grid_distances(
    candidate_points: gpd.GeoSeries,
    power: gpd.GeoDataFrame,
    settings: Settings,
    removed: set[str],
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """(dist_substation_m, dist_line_m, feature counts), in metres."""
    tags = settings.features.grid_osm_tags
    substations = select_power(power, tags.substation, removed)
    lines = select_power(power, tags.line, removed)
    to_substation = nearest_distance(
        candidate_points, to_metric(substations.geometry, settings.crs)
    )
    to_line = nearest_distance(candidate_points, to_metric(lines.geometry, settings.crs))
    return to_substation, to_line, {"substations": len(substations), "lines": len(lines)}


def completeness(
    power: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    settings: Settings,
    removed: set[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per district: counted features, area, density and ratio to the national median."""
    counted = select_power(power, settings.features.grid_osm_tags.completeness, removed)
    ordered = districts.sort_values("district_id", kind="mergesort", ignore_index=True)
    counts = np.zeros(len(ordered), dtype=np.int64)
    if len(counted):
        tree = STRtree(to_metric(counted.geometry, settings.crs).array)
        district_idx, _ = tree.query(
            to_metric(ordered.geometry, settings.crs).array, predicate="intersects"
        )
        counts = np.bincount(district_idx, minlength=len(ordered)).astype(np.int64)
    area = ordered["area_km2"].to_numpy(dtype=np.float64)
    density = counts / area
    median = float(np.median(density))
    if not median > 0:
        raise GridError(
            "The national median of mapped power features per km² is 0, so "
            "grid_completeness_ratio is undefined; nothing was written"
        )
    table = pd.DataFrame(
        {
            "district_id": ordered["district_id"].to_numpy(),
            "district": ordered["district_name"].to_numpy(),
            "power_features": counts,
            "area_km2": area,
            "features_per_km2": density,
            "ratio": density / median,
        }
    )
    summary = {
        "counted_values": sorted(power_values(settings.features.grid_osm_tags.completeness)),
        "features_counted": len(counted),
        "national_median_per_km2": median,
        "districts": {
            row.district_id: {
                "district": row.district,
                "power_features": int(row.power_features),
                "area_km2": round(float(row.area_km2), 3),
                "features_per_km2": round(float(row.features_per_km2), 6),
                "ratio": round(float(row.ratio), 6),
            }
            for row in table.itertuples()
        },
    }
    return table, summary
