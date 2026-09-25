"""The existing chargers each mode uses for the charging-gap features (SPEC §2, §5; D-034).

**Production mode.** The existing chargers are:
- OSM ``amenity=charging_station`` nodes and areas, except those tagged with an access value
  in ``features.chargers.exclude_access`` (not public);
- OSM fuel stations carrying ``socket:*`` tags, when
  ``features.chargers.fuel_sockets_count_as_chargers`` is true;
- rows of the manual CSV (``chargers_manual``) when it exists. When it is missing, OSM is
  the only source, and the metadata says so. Nothing stands in for it.

Records within ``sources.charger_match_radius_m`` (50 m) of each other, directly or through
a chain of such records, are one charging site. Each site takes the position of its first
record in a fixed order (OSM charging stations, then fuel stations, then CSV rows; then by
id). The 50 m is a project data-matching choice, not a definition of a charging site.

**Backtest mode.** No charger is used: the set is empty, built without reading any charger
source, so no production value is patched afterwards. ``charger_objects`` lists every OSM
object that is a charger, so backtest mode can also remove them from every other input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import Settings, require
from sitescout.features.nearest import metric_points, pairs_within
from sitescout.features.pois import tag_mask

MODES = ("production", "backtest")
_SOURCE_ORDER = ("osm_charging_stations", "osm_fuel_with_sockets", "chargers_manual")


@dataclass(frozen=True, slots=True)
class ChargerSet:
    """Existing charging sites for one mode: one metric point per site, and how it was built."""

    sites: gpd.GeoSeries
    stats: dict[str, Any]


def socket_fuel_ids(pois: gpd.GeoDataFrame, settings: Settings) -> set[str]:
    """OSM ids of fuel stations (candidates.host_osm_tags.fuel) carrying socket:* tags."""
    fuel = np.zeros(len(pois), dtype=bool)
    for selector in settings.candidates.host_osm_tags.fuel:
        fuel |= tag_mask(pois, selector)
    sockets = pois["socket_tags"].notna().to_numpy(dtype=bool)
    return set(pois.loc[fuel & sockets, "feature_id"])


def charger_objects(layers: dict[str, gpd.GeoDataFrame], settings: Settings) -> set[str]:
    """Every OSM object that is an existing charger, whatever its access tag: the charging
    stations and the fuel stations tagged socket:*. Backtest mode removes them all."""
    stations = set(layers["osm_charging_stations"]["feature_id"])
    return stations | socket_fuel_ids(layers["osm_pois"], settings)


def backtest_chargers(settings: Settings) -> ChargerSet:
    """The empty charger set of backtest mode (SPEC §5)."""
    return ChargerSet(
        gpd.GeoSeries([], crs=settings.crs.metric),
        {
            "mode": "backtest",
            "sites": 0,
            "note": "Backtest mode: existing chargers are removed from every feature (SPEC §5).",
        },
    )


def production_chargers(
    layers: dict[str, gpd.GeoDataFrame],
    manual: gpd.GeoDataFrame | None,
    manual_status: str,
    settings: Settings,
) -> ChargerSet:
    """Existing charging sites in production mode, merged within the match radius."""
    radius = require(settings.sources.charger_match_radius_m, "sources.charger_match_radius_m")
    rules = settings.features.chargers
    stations = layers["osm_charging_stations"]
    private = stations["access"].isin(rules.exclude_access).to_numpy(dtype=bool)
    parts = [_records(stations[~private], "osm_charging_stations", "feature_id")]
    sources: dict[str, Any] = {
        "osm_charging_stations": {"records": len(stations), "excluded_access": int(private.sum())}
    }
    pois = layers["osm_pois"]
    fuel_ids = socket_fuel_ids(pois, settings)
    if rules.fuel_sockets_count_as_chargers:
        parts.append(
            _records(pois[pois["feature_id"].isin(fuel_ids)], "osm_fuel_with_sockets", "feature_id")
        )
    sources["osm_fuel_with_sockets"] = {
        "records": len(fuel_ids),
        "counted": rules.fuel_sockets_count_as_chargers,
    }
    if manual is None:
        sources["chargers_manual"] = {"records": 0, "status": manual_status}
    else:
        parts.append(_records(manual, "chargers_manual", "charger_id"))
        sources["chargers_manual"] = {"records": len(manual), "status": "ok"}

    records = pd.concat(parts, ignore_index=True)
    records["_rank"] = records["source"].map(_SOURCE_ORDER.index)
    records = records.sort_values(["_rank", "record_id"], kind="mergesort", ignore_index=True)
    points = metric_points(gpd.GeoSeries(records["geometry"], crs=settings.crs.storage), settings)
    groups = _merge(points, radius)
    first = sorted({min(members) for members in groups})
    merged = [
        [str(records.loc[i, "record_id"]) for i in sorted(members)]
        for members in groups
        if len(members) > 1
    ]
    stats = {
        "mode": "production",
        "sources": sources,
        "records": len(records),
        "merge_radius_m": radius,
        "sites": len(first),
        "merged_groups": sorted(merged),
    }
    return ChargerSet(points.iloc[first].reset_index(drop=True), stats)


def _records(frame: gpd.GeoDataFrame, source: str, id_column: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "record_id": frame[id_column].to_numpy(dtype=object),
            "source": source,
            "geometry": frame.geometry.to_numpy(),
        }
    )


def _merge(points: gpd.GeoSeries, radius_m: float) -> list[set[int]]:
    """Groups of record positions linked by chains of distances of at most ``radius_m``."""
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    left, right, _ = pairs_within(points, points, radius_m)
    for a, b in zip(left.tolist(), right.tolist(), strict=True):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, set[int]] = {}
    for i in range(len(points)):
        groups.setdefault(root(i), set()).add(i)
    return list(groups.values())
