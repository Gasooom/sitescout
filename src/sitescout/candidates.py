"""Candidate generation (SPEC §3, Milestone 2).

Every candidate comes from public data in code; nothing is placed by hand:

1. **Hosts.** OSM POIs whose tags match ``candidates.host_osm_tags`` (D-027). A POI that
   matches several host types takes the highest in ``dedup.host_priority``. Area hosts are
   represented by a point on their surface. Hosts outside Rwanda are left out.
2. **Corridor points.** Every ``corridor.spacing_m`` along the trunk and primary network
   inside Rwanda, starting half a spacing from each junction or end, so neighbouring points
   are one spacing apart along the road (D-029).
3. **Snapping.** A corridor point moves to the nearest host within
   ``corridor.host_snap_radius_m``; with none in reach it stays on the road as
   ``host_type = none``.
4. **Deduplication.** Candidates are taken in host-priority order; a candidate is dropped if
   an already-kept one lies within ``dedup.radius_m`` (D-029).
5. **Filters.** Candidates more than ``filters.max_road_distance_m`` from a drivable road
   (D-028), or inside water or a protected area (D-026), are dropped.
6. **Budget.** The eligible candidates are reduced to exactly ``budget.size`` (D-031):
   seats are shared among ADM2 districts in proportion to their eligible candidates
   (largest remainder), and each district keeps its quota by priority-ordered spacing,
   using the largest spacing radius that still leaves the quota. Fewer eligible candidates
   than the budget stops the run with CandidateBudgetError and writes nothing.

Both the eligible universe (``candidates_eligible``, with a ``selected`` flag) and the
selection (``candidates``) are written. The selection uses only candidate positions,
districts, host types, origins and ids: no population, chargers, grid evidence, feature or
score, and no randomness.

Existing chargers are never read here, so no candidate is dropped for being near one
(CLAUDE.md). Distances and areas are computed in the metric CRS; candidates are stored in
the storage CRS. The profile (urban or corridor) stays null until its pending parameters
are decided (D-030).
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.strtree import STRtree

from sitescout.config import Settings
from sitescout.crs import to_metric, to_storage
from sitescout.ingest.layers import (
    CANDIDATES,
    CANDIDATES_ELIGIBLE,
    LayerResult,
    metadata_path,
    read_layer,
    remove_layer,
    validate_layer,
    write_layer,
)
from sitescout.ingest.metadata import read_json

logger = logging.getLogger(__name__)

INPUT_LAYERS = (
    "admin_districts",
    "admin_country",
    "osm_pois",
    "osm_roads",
    "osm_water",
    "osm_protected_areas",
)
EXCLUSION_LAYERS = {"water": "osm_water", "protected_area": "osm_protected_areas"}

# Generic labels for host_name (D-012): never a business or brand name.
HOST_LABELS = {
    "fuel": "Fuel station",
    "mall": "Mall",
    "supermarket": "Supermarket",
    "logistics": "Logistics site",
    "industrial": "Industrial site",
    "hotel": "Hotel",
    "none": "Corridor point",
}
# At equal host priority a host's own candidate wins over a corridor point snapped to it.
ORIGIN_ORDER = ("host", "corridor_snapped", "corridor")


class CandidateError(Exception):
    """Candidate generation cannot produce a valid result."""


class CandidateCountError(CandidateError):
    """The number of candidates is outside candidates.target_count (SPEC §3)."""

    def __init__(self, message: str, stats: dict[str, Any]) -> None:
        super().__init__(message)
        self.stats = stats


class CandidateBudgetError(CandidateError):
    """There are fewer eligible candidates than candidates.budget.size (D-031)."""


BUDGET_METHOD = (
    "ADM2 district quotas proportional to eligible candidates (largest remainder, ties by "
    "district_id), then per district greedy spacing in priority order (host priority, "
    "origin, candidate_id) at the largest pairwise-distance radius that keeps the quota"
)


@dataclass(frozen=True, slots=True)
class Inputs:
    """The validated M1 layers candidate generation reads, with their fingerprints."""

    layers: dict[str, gpd.GeoDataFrame]
    fingerprints: dict[str, str]


def load_inputs(processed_dir: Path, settings: Settings) -> Inputs:
    """Read every input through read_layer, so each is checked against its schema."""
    layers = {name: read_layer(name, processed_dir, settings) for name in INPUT_LAYERS}
    fingerprints = {
        name: read_json(metadata_path(processed_dir, name))["content_sha256"]
        for name in INPUT_LAYERS
    }
    return Inputs(layers, fingerprints)


def _digest(text: str) -> str:
    return "cand-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _point_id(point: shapely.Point) -> str:
    return _digest(f"{point.y:.6f},{point.x:.6f}")


# --- 1. Hosts ---------------------------------------------------------------------------------


def _selector_mask(pois: pd.DataFrame, selector: str) -> np.ndarray:
    key, _, value = selector.partition("=")
    if key not in pois.columns:
        raise CandidateError(f"Host tag {selector!r}: osm_pois has no column {key!r}")
    column = pois[key]
    return (column.notna() if value == "*" else column == value).to_numpy(dtype=bool)


def classify_hosts(pois: gpd.GeoDataFrame, settings: Settings) -> gpd.GeoDataFrame:
    """POIs that are hosts, with their host type, in the storage CRS.

    Returns ``host_osm_id``, ``host_type`` and a point geometry (the POI itself, or a point
    on the surface of an area POI, computed in the metric CRS).
    """
    candidates = settings.candidates
    host_type = np.full(len(pois), None, dtype=object)
    # Walk from the lowest priority up, so the highest-priority match is written last.
    for name in reversed(candidates.dedup.host_priority):
        if name == "none":
            continue
        mask = np.zeros(len(pois), dtype=bool)
        for selector in getattr(candidates.host_osm_tags, name):
            mask |= _selector_mask(pois, selector)
        host_type[mask] = name
    is_host = pd.notna(host_type)
    hosts = pois.loc[is_host, ["feature_id", "geometry"]].copy()
    hosts["host_type"] = host_type[is_host]
    if hosts.empty:
        return _empty_hosts(settings)

    points = hosts.geometry.copy()
    areas = ~points.geom_type.eq("Point")
    if areas.any():
        surface = to_metric(points[areas], settings.crs).representative_point()
        points.loc[areas] = to_storage(surface, settings.crs).to_numpy()
    return gpd.GeoDataFrame(
        {"host_osm_id": hosts["feature_id"].to_numpy(), "host_type": hosts["host_type"]},
        geometry=points.to_numpy(),
        crs=settings.crs.storage,
    ).reset_index(drop=True)


def _empty_hosts(settings: Settings) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"host_osm_id": pd.Series([], dtype=object), "host_type": pd.Series([], dtype=object)},
        geometry=gpd.GeoSeries([], crs=settings.crs.storage),
        crs=settings.crs.storage,
    )


# --- 2. Corridor points --------------------------------------------------------------------


def corridor_points(
    roads: gpd.GeoDataFrame, country: gpd.GeoDataFrame, settings: Settings
) -> gpd.GeoSeries:
    """Points every ``spacing_m`` along the trunk/primary network inside Rwanda (metric CRS).

    The network is noded and merged into chains between junctions. On each chain points
    sit at spacing/2, 3·spacing/2, ...: consecutive points are one spacing apart along a
    chain and across a junction. A chain shorter than spacing/2 gets no point.
    """
    corridor = settings.candidates.corridor
    metric_crs = settings.crs.metric
    lines = roads[roads["highway"].isin(corridor.road_classes)]
    if lines.empty:
        return gpd.GeoSeries([], crs=metric_crs)
    lines = lines.sort_values(["osm_type", "osm_id"], kind="mergesort")
    network = shapely.union_all(to_metric(lines.geometry, settings.crs).array)
    inside = _linear_parts(
        shapely.intersection(network, to_metric(country.geometry, settings.crs).iloc[0])
    )
    if not inside:
        return gpd.GeoSeries([], crs=metric_crs)
    merged = shapely.line_merge(shapely.union_all(inside))
    chains = sorted(
        (part for part in _linear_parts(merged) if part.length > 0),
        key=lambda line: (line.coords[0], line.coords[-1], line.length),
    )
    spacing = float(corridor.spacing_m)
    points = []
    for chain in chains:
        distance = spacing / 2
        while distance <= chain.length:
            points.append(chain.interpolate(distance))
            distance += spacing
    return gpd.GeoSeries(points, crs=metric_crs)


def _linear_parts(geometry: shapely.Geometry) -> list[shapely.LineString]:
    return [
        part
        for part in shapely.get_parts(geometry)
        if shapely.get_type_id(part) == 1  # LineString
    ]


# --- 3. Snapping -------------------------------------------------------------------------------


def snap_to_hosts(
    points: gpd.GeoSeries, hosts_metric: gpd.GeoDataFrame, radius_m: float
) -> np.ndarray:
    """For each corridor point, the index of the nearest host within ``radius_m``, or -1.

    Ties are broken by the lower host index; hosts are sorted by OSM id beforehand.
    """
    chosen = np.full(len(points), -1, dtype=np.int64)
    if len(points) == 0 or hosts_metric.empty:
        return chosen
    tree = STRtree(hosts_metric.geometry.array)
    (point_idx, host_idx), _ = tree.query_nearest(
        points.array, max_distance=radius_m, return_distance=True, all_matches=True
    )
    for point, host in zip(point_idx, host_idx, strict=True):
        if chosen[point] == -1 or host < chosen[point]:
            chosen[point] = host
    return chosen


# --- 4. Deduplication --------------------------------------------------------------------------


def deduplicate(
    table: gpd.GeoDataFrame, radius_m: float, priority: tuple[str, ...]
) -> gpd.GeoDataFrame:
    """Keep candidates in priority order, dropping any within ``radius_m`` of a kept one.

    ``table`` is in the metric CRS. Order: host priority, then origin (a host before a
    corridor point snapped to it), then candidate_id. Adds ``merged_count``: how many
    candidates each kept one absorbed. Greedy, not transitive: a chain of hosts 250 m apart
    does not collapse into one candidate.
    """
    if table.empty:
        return table.assign(merged_count=pd.Series([], dtype="int64"))
    rank = table.assign(
        _priority=table["host_type"].map(priority.index),
        _origin=table["origin"].map(ORIGIN_ORDER.index),
    ).sort_values(["_priority", "_origin", "candidate_id"], kind="mergesort")
    order = rank.index.to_numpy()
    geoms = table.geometry.array
    tree = STRtree(geoms)
    removed = np.zeros(len(table), dtype=bool)
    merged = np.zeros(len(table), dtype=np.int64)
    kept = []
    position = {label: i for i, label in enumerate(table.index)}
    for label in order:
        i = position[label]
        if removed[i]:
            continue
        kept.append(i)
        near = tree.query(geoms[i], predicate="dwithin", distance=radius_m)
        near = near[(near != i) & ~removed[near]]
        removed[near] = True
        merged[i] = len(near)
    result = table.iloc[sorted(kept)].copy()
    result["merged_count"] = merged[sorted(kept)]
    return result


# --- 5. Filters and districts ------------------------------------------------------------------


def nearest_road(
    points: gpd.GeoSeries, roads_metric: gpd.GeoDataFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Distance in metres to, and highway class of, the nearest road for each point."""
    distance = np.full(len(points), np.inf)
    road_class = np.full(len(points), None, dtype=object)
    if len(points) == 0 or roads_metric.empty:
        return distance, road_class
    tree = STRtree(roads_metric.geometry.array)
    (point_idx, road_idx), dist = tree.query_nearest(
        points.array, return_distance=True, all_matches=True
    )
    best = np.full(len(points), np.iinfo(np.int64).max, dtype=np.int64)
    for point, road, d in zip(point_idx, road_idx, dist, strict=True):
        if road < best[point]:
            best[point] = road
            distance[point] = d
    classes = roads_metric["highway"].to_numpy()
    road_class[:] = classes[best]
    return distance, road_class


def inside_any(points: gpd.GeoSeries, areas_metric: gpd.GeoDataFrame) -> np.ndarray:
    """Whether each point lies in or on any of the areas."""
    inside = np.zeros(len(points), dtype=bool)
    if len(points) == 0 or areas_metric.empty:
        return inside
    point_idx, _ = STRtree(areas_metric.geometry.array).query(points.array, predicate="intersects")
    inside[np.unique(point_idx)] = True
    return inside


def assign_districts(
    points: gpd.GeoSeries, districts_metric: gpd.GeoDataFrame
) -> tuple[np.ndarray, gpd.GeoDataFrame]:
    """Row labels (into the returned, id-sorted districts) of the district containing each
    point; a point on a shared edge takes the lowest district_id."""
    ordered = districts_metric.sort_values("district_id", kind="mergesort").reset_index(drop=True)
    chosen = np.full(len(points), -1, dtype=np.int64)
    if len(points):
        point_idx, district_idx = STRtree(ordered.geometry.array).query(
            points.array, predicate="intersects"
        )
        for point, district in zip(point_idx, district_idx, strict=True):
            if chosen[point] == -1 or district < chosen[point]:
                chosen[point] = district
    missing = int((chosen == -1).sum())
    if missing:
        raise CandidateError(f"{missing} candidates are not inside any district")
    return ordered.index.to_numpy()[chosen], ordered


# --- The whole run ------------------------------------------------------------------------------


def generate_candidates(inputs: Inputs, settings: Settings) -> tuple[gpd.GeoDataFrame, dict]:
    """Candidates in the storage CRS, following the CANDIDATES schema, and step statistics."""
    layers = inputs.layers
    candidates = settings.candidates
    priority = candidates.dedup.host_priority
    country_metric = to_metric(layers["admin_country"].geometry, settings.crs).iloc[0]
    stats: dict[str, Any] = {"inputs": dict(sorted(inputs.fingerprints.items()))}

    # 1. Hosts inside Rwanda, sorted by OSM id for deterministic tie-breaks.
    hosts = classify_hosts(layers["osm_pois"], settings)
    hosts = hosts.sort_values("host_osm_id", kind="mergesort", ignore_index=True)
    hosts_metric = to_metric(hosts, settings.crs)
    in_rwanda = hosts_metric.intersects(country_metric).to_numpy(dtype=bool)
    stats["hosts_found"] = _counts(hosts["host_type"])
    stats["hosts_outside_rwanda"] = int((~in_rwanda).sum())
    hosts, hosts_metric = hosts[in_rwanda].reset_index(drop=True), hosts_metric[in_rwanda]
    hosts_metric = hosts_metric.reset_index(drop=True)

    # 2-3. Corridor points, snapped to the nearest host within the radius.
    points = corridor_points(layers["osm_roads"], layers["admin_country"], settings)
    snapped = snap_to_hosts(points, hosts_metric, candidates.corridor.host_snap_radius_m)
    stats["corridor_points"] = len(points)
    stats["corridor_snapped"] = int((snapped >= 0).sum())
    stats["corridor_without_host"] = int((snapped < 0).sum())

    rows = [
        {
            "candidate_id": _digest(host.host_osm_id),
            "host_type": host.host_type,
            "host_osm_id": host.host_osm_id,
            "origin": "host",
            "point": host.geometry,
            "metric": metric,
        }
        for host, metric in zip(hosts.itertuples(), hosts_metric.geometry, strict=True)
    ]
    storage_points = to_storage(points, settings.crs) if len(points) else points
    for point, point_storage, host_index in zip(points, storage_points, snapped, strict=True):
        if host_index >= 0:
            host = hosts.iloc[host_index]
            rows.append(
                {
                    "candidate_id": _digest(host.host_osm_id),
                    "host_type": host.host_type,
                    "host_osm_id": host.host_osm_id,
                    "origin": "corridor_snapped",
                    "point": host.geometry,
                    "metric": hosts_metric.geometry.iloc[host_index],
                }
            )
        else:
            rows.append(
                {
                    "candidate_id": _point_id(point_storage),
                    "host_type": "none",
                    "host_osm_id": None,
                    "origin": "corridor",
                    "point": point_storage,
                    "metric": point,
                }
            )
    table = gpd.GeoDataFrame(
        pd.DataFrame(
            rows, columns=["candidate_id", "host_type", "host_osm_id", "origin", "point", "metric"]
        ),
        geometry="metric",
        crs=settings.crs.metric,
    )
    stats["before_dedup"] = len(table)

    # 4. Deduplicate.
    table = deduplicate(table, candidates.dedup.radius_m, priority)
    stats["after_dedup"] = len(table)
    stats["removed_by_dedup"] = stats["before_dedup"] - stats["after_dedup"]

    # 5. Filters: drivable road distance, water, protected areas.
    roads = layers["osm_roads"]
    drivable = roads[roads["highway"].isin(candidates.drivable_road_classes)]
    drivable = drivable.sort_values(["osm_type", "osm_id"], kind="mergesort")
    distance, road_class = nearest_road(table.geometry, to_metric(drivable, settings.crs))
    too_far = distance > candidates.filters.max_road_distance_m
    dropped = too_far.copy()
    stats["dropped_far_from_road"] = int(too_far.sum())
    for area in candidates.filters.exclude_areas:
        layer = layers[EXCLUSION_LAYERS[area]]
        inside = inside_any(table.geometry, to_metric(layer, settings.crs))
        stats[f"dropped_in_{area}"] = int(inside.sum())
        dropped |= inside
    stats["dropped_total"] = int(dropped.sum())
    stats["merged_into_dropped"] = int(table["merged_count"].to_numpy()[dropped].sum())
    table = table.assign(dist_road_m=distance, nearest_road_class=road_class)[~dropped]

    # District and province of each remaining candidate.
    districts_metric = to_metric(layers["admin_districts"], settings.crs)
    rows_idx, ordered = assign_districts(table.geometry, districts_metric)
    districts = ordered.loc[rows_idx]

    storage = gpd.GeoSeries(table["point"].to_numpy(), crs=settings.crs.storage)
    district_names = districts["district_name"].to_numpy()
    result = gpd.GeoDataFrame(
        {
            "candidate_id": table["candidate_id"].to_numpy(),
            "lat": storage.y.to_numpy(),
            "lon": storage.x.to_numpy(),
            "host_name": [
                f"{HOST_LABELS[kind]}, {district}"
                for kind, district in zip(table["host_type"], district_names, strict=True)
            ],
            "host_type": table["host_type"].to_numpy(),
            "host_osm_id": table["host_osm_id"].to_numpy(),
            "origin": table["origin"].to_numpy(),
            "merged_count": table["merged_count"].to_numpy(dtype=np.int64),
            "district_id": districts["district_id"].to_numpy(),
            "district": district_names,
            "province_code": districts["province_code"].to_numpy(),
            "province": districts["province_name"].to_numpy(),
            "nearest_road_class": table["nearest_road_class"].to_numpy(),
            "dist_road_m": table["dist_road_m"].to_numpy(dtype=np.float64),
            "profile": np.full(len(table), None, dtype=object),
        },
        geometry=storage.to_numpy(),
        crs=settings.crs.storage,
    )
    result = result.sort_values("candidate_id", kind="mergesort", ignore_index=True)
    stats["final"] = len(result)
    stats["by_host_type"] = _counts(result["host_type"])
    stats["by_origin"] = _counts(result["origin"])
    stats["by_province"] = _counts(result["province"])
    return result, stats


# --- 6. Candidate budget (D-031) -----------------------------------------------------------


def allocate_quotas(counts: dict[str, int], size: int) -> dict[str, int]:
    """Share ``size`` seats among districts in proportion to their eligible candidates.

    Largest-remainder method in exact integer arithmetic: each district gets
    floor(size * n / total); the seats left go to the largest remainders, equal remainders
    by district id ascending. Every quota is at most the district's count.
    """
    total = sum(counts.values())
    if size > total:
        raise CandidateBudgetError(
            f"{total} eligible candidates, fewer than the budget of {size}; nothing was written"
        )
    quotas = {district: size * n // total for district, n in counts.items()}
    remainders = {district: size * n % total for district, n in counts.items()}
    left = size - sum(quotas.values())
    for district in sorted(counts, key=lambda d: (-remainders[d], d))[:left]:
        quotas[district] += 1
    return quotas


def greedy_spacing(distances: np.ndarray, radius: float) -> list[int]:
    """Rows kept in order, dropping any within ``radius`` of an already-kept row."""
    kept: list[int] = []
    for row in range(len(distances)):
        if not kept or distances[row, kept].min() > radius:
            kept.append(row)
    return kept


def spacing_select(xy: np.ndarray, quota: int) -> tuple[list[int], float | None]:
    """Pick ``quota`` rows of ``xy`` (metres, already in priority order) spread apart.

    Tries the district's own pairwise distances from largest to smallest, and uses the
    first (largest) radius at which greedy spacing keeps at least ``quota`` rows; if it
    keeps more, the first ``quota`` in priority order are taken. Radius 0 (keep every
    distinct point) is the last resort. Returns the chosen row positions and the radius.
    """
    count = len(xy)
    if quota <= 0:
        return [], None
    if quota >= count:
        return list(range(count)), 0.0
    distances = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=-1))
    radii = np.unique(distances[np.triu_indices(count, k=1)])[::-1]
    for radius in [*radii.tolist(), 0.0]:
        kept = greedy_spacing(distances, radius)
        if len(kept) >= quota:
            return kept[:quota], float(radius)
    raise CandidateError("Candidates share a location; deduplication should have merged them")


def apply_budget(frame: gpd.GeoDataFrame, settings: Settings) -> tuple[np.ndarray, dict[str, Any]]:
    """Which eligible candidates the budget keeps, and a record of how it chose them."""
    size = settings.candidates.budget.size
    priority = settings.candidates.dedup.host_priority
    if len(frame) < size:
        raise CandidateBudgetError(
            f"{len(frame)} eligible candidates, fewer than the budget of {size}; nothing was "
            "written"
        )
    metric = to_metric(frame.geometry, settings.crs)
    order = pd.DataFrame(
        {
            "district_id": frame["district_id"].to_numpy(),
            "priority": frame["host_type"].map(priority.index).to_numpy(),
            "origin": frame["origin"].map(ORIGIN_ORDER.index).to_numpy(),
            "candidate_id": frame["candidate_id"].to_numpy(),
            "x": metric.x.to_numpy(),
            "y": metric.y.to_numpy(),
        }
    ).sort_values(["district_id", "priority", "origin", "candidate_id"], kind="mergesort")
    counts = {str(d): int(n) for d, n in order.groupby("district_id").size().items()}
    quotas = allocate_quotas(counts, size)
    names = dict(zip(frame["district_id"], frame["district"], strict=True))

    selected = np.zeros(len(frame), dtype=bool)
    districts: dict[str, Any] = {}
    for district_id, rows in order.groupby("district_id", sort=True):
        picks, radius = spacing_select(rows[["x", "y"]].to_numpy(), quotas[district_id])
        selected[rows.index.to_numpy()[picks]] = True
        districts[district_id] = {
            "district": names[district_id],
            "eligible": counts[district_id],
            "quota": quotas[district_id],
            "selected": len(picks),
            "radius_m": None if radius is None else round(radius, 3),
        }
    if int(selected.sum()) != size:
        raise CandidateError(f"The budget selected {int(selected.sum())}, expected {size}")
    record = {
        "method": BUDGET_METHOD,
        "target_budget": size,
        "eligible_count": len(frame),
        "selected_count": int(selected.sum()),
        "districts": districts,
    }
    return selected, record


def _counts(values: pd.Series) -> dict[str, int]:
    return {str(key): int(count) for key, count in sorted(Counter(values).items())}


def check_count(frame: gpd.GeoDataFrame, settings: Settings, stats: dict[str, Any]) -> None:
    """Raise CandidateCountError unless the selected count is within target_count."""
    target = settings.candidates.target_count
    count = len(frame)
    if not target.min <= count <= target.max:
        raise CandidateCountError(
            f"{count} selected candidates, outside the SPEC §3 target of "
            f"{target.min}-{target.max}; nothing was written (D-030, D-031).",
            stats,
        )


def log_stats(stats: dict[str, Any]) -> None:
    logger.info(
        "Hosts found: %s (%d outside Rwanda)", stats["hosts_found"], stats["hosts_outside_rwanda"]
    )
    logger.info(
        "Corridor points: %d (%d snapped to a host, %d without a host)",
        stats["corridor_points"],
        stats["corridor_snapped"],
        stats["corridor_without_host"],
    )
    logger.info(
        "Before dedup %d, after dedup %d (%d merged)",
        stats["before_dedup"],
        stats["after_dedup"],
        stats["removed_by_dedup"],
    )
    logger.info(
        "Dropped: %d far from a drivable road, %s in water, %s in protected areas "
        "(%d in total; %d candidates had been merged into dropped ones)",
        stats["dropped_far_from_road"],
        stats.get("dropped_in_water", "n/a"),
        stats.get("dropped_in_protected_area", "n/a"),
        stats["dropped_total"],
        stats["merged_into_dropped"],
    )
    logger.info("Candidates: %d; by host type %s", stats["final"], stats["by_host_type"])
    logger.info("By origin %s", stats["by_origin"])
    logger.info("By province %s", stats["by_province"])


def log_budget(record: dict[str, Any]) -> None:
    logger.info(
        "Budget: %d eligible -> %d selected (target %d)",
        record["eligible_count"],
        record["selected_count"],
        record["target_budget"],
    )
    for district_id, entry in record["districts"].items():
        logger.info(
            "  %-24s %-12s eligible %3d quota %3d selected %3d radius %s m",
            district_id,
            entry["district"],
            entry["eligible"],
            entry["quota"],
            entry["selected"],
            entry["radius_m"],
        )


NOTES = [
    "Generated in code from OpenStreetMap data, © OpenStreetMap contributors, ODbL 1.0.",
    "Existing chargers are not used by generation or by the budget (D-029, D-031).",
    "profile is null until the profile rule's pending parameters are decided (D-030).",
]


def run_candidates(settings: Settings, processed_dir: Path) -> list[LayerResult]:
    """Generate, budget and write candidates_eligible and candidates.

    Writes nothing, and removes any earlier candidate layers, when generation or the
    budget stops.
    """
    eligible, stats = generate_candidates(load_inputs(processed_dir, settings), settings)
    log_stats(stats)
    try:
        selected, record = apply_budget(eligible, settings)
        chosen = eligible.loc[selected].reset_index(drop=True)
        check_count(chosen, settings, stats)
        universe = eligible.assign(selected=selected)
        validate_layer(universe, CANDIDATES_ELIGIBLE, settings)
        validate_layer(chosen, CANDIDATES, settings)
    except CandidateError:
        for name in (CANDIDATES.name, CANDIDATES_ELIGIBLE.name):
            remove_layer(processed_dir, name)
        raise
    log_budget(record)
    chosen_stats = {
        "by_host_type": _counts(chosen["host_type"]),
        "by_origin": _counts(chosen["origin"]),
        "by_province": _counts(chosen["province"]),
    }
    logger.info("Selected by host type %s", chosen_stats["by_host_type"])
    logger.info("Selected by origin %s", chosen_stats["by_origin"])
    logger.info("Selected by province %s", chosen_stats["by_province"])
    common = {"generation": stats, "budget": record}
    return [
        write_layer(
            universe,
            CANDIDATES_ELIGIBLE,
            processed_dir,
            settings,
            sources=[],
            stats=common,
            notes=NOTES,
        ),
        write_layer(
            chosen,
            CANDIDATES,
            processed_dir,
            settings,
            sources=[],
            stats={**common, "selected": chosen_stats},
            notes=NOTES,
        ),
    ]
