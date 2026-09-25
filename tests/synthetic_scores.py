"""SYNTHETIC feature tables for Milestone 4 scoring tests. None of this is real data.

``feature_table`` builds an in-memory table with every column scoring reads. By default
every candidate is a fuel host in SYN-D1 on a trunk road, near grid evidence, in an urban
place, with a well-mapped district and POIs nearby; tests override single columns.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import shapely

WEIGHTED = (
    "pop_5km",
    "pop_1km",
    "pop_10km",
    "dist_road_m",
    "dist_trunk_m",
    "poi_1km",
    "poi_3km",
    "dist_charger_m",
    "chargers_10km",
    "chargers_25km",
    "dist_substation_m",
    "dist_line_m",
)


def feature_table(n: int = 4, **columns: Any) -> gpd.GeoDataFrame:
    """A SYNTHETIC feature table of ``n`` candidates; keyword arguments replace columns."""
    base: dict[str, Any] = {
        "candidate_id": [f"cand-{i:02d}" for i in range(n)],
        "host_type": ["fuel"] * n,
        "origin": ["host"] * n,
        "district_id": ["SYN-D1"] * n,
        "district": ["SYNTHETIC District 1"] * n,
        "province": ["SYNTHETIC West Province"] * n,
        "road_class": ["trunk"] * n,
        "grid_completeness_ratio": [1.0] * n,
        "dist_kigali_cbd_m": [50_000.0] * n,
        "dist_town_m": [1_000.0] * n,
    }
    for i, name in enumerate(WEIGHTED):
        base[name] = np.arange(n, dtype=np.float64) * 100.0 + i
    base["poi_3km"] = np.arange(1, n + 1, dtype=np.int64)
    base["dist_substation_m"] = np.full(n, 1_000.0)
    base["dist_line_m"] = np.full(n, 1_000.0)
    base.update(columns)
    points = [shapely.Point(30.0 + 0.001 * i, -1.95) for i in range(n)]
    return gpd.GeoDataFrame(base, geometry=points, crs="EPSG:4326")
