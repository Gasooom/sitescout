"""Geometry checks: presence, emptiness, validity, type and coordinate bounds.

Checks report problems; they never change geometry. The one explicit repair,
``repair_polygons``, is used only for crowd-sourced OSM areas, marks every repaired row and
is counted in the layer metadata, so nothing is repaired silently.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from sitescout.config import BoundingBox

_MAX_EXAMPLES = 5

# Longitude/latitude limits of EPSG:4326 itself.
WORLD_BOUNDS = (-180.0, -90.0, 180.0, 90.0)


def _examples(labels: pd.Series, mask: np.ndarray) -> list[str]:
    return [str(label) for label in labels[mask].head(_MAX_EXAMPLES)]


def check_geometry(
    frame: gpd.GeoDataFrame,
    *,
    allowed_types: frozenset[str],
    bbox: BoundingBox,
    bounds_mode: str = "within",
    labels: pd.Series | None = None,
) -> list[str]:
    """Every geometry problem in ``frame`` (which must be in EPSG:4326 degrees).

    ``labels`` identifies rows in messages (usually the id column); defaults to the index.
    """
    problems: list[str] = []
    if frame.geometry.name != "geometry":
        problems.append(f"the active geometry column is {frame.geometry.name!r}, not 'geometry'")
    geoms = frame.geometry.array
    labels = (frame.index.to_series() if labels is None else labels).reset_index(drop=True)

    missing = np.asarray(shapely.is_missing(geoms))
    if missing.any():
        problems.append(f"{missing.sum()} missing geometries, e.g. {_examples(labels, missing)}")
    present = ~missing

    empty = present & np.asarray(shapely.is_empty(geoms))
    if empty.any():
        problems.append(f"{empty.sum()} empty geometries, e.g. {_examples(labels, empty)}")
    usable = present & ~empty

    invalid = usable & ~np.asarray(shapely.is_valid(geoms))
    if invalid.any():
        reasons = [
            f"{label}: {shapely.is_valid_reason(geom)}"
            for label, geom in zip(
                labels[invalid].head(_MAX_EXAMPLES), geoms[invalid][:_MAX_EXAMPLES], strict=True
            )
        ]
        problems.append(f"{invalid.sum()} invalid geometries, e.g. {reasons}")

    types = np.asarray(shapely.get_type_id(geoms))
    type_names = np.array([_TYPE_NAMES.get(int(code), "Unknown") for code in types])
    wrong_type = usable & ~np.isin(type_names, sorted(allowed_types))
    if wrong_type.any():
        found = sorted({str(name) for name in type_names[wrong_type]})
        problems.append(
            f"{wrong_type.sum()} geometries of type {found}; allowed {sorted(allowed_types)}, "
            f"e.g. {_examples(labels, wrong_type)}"
        )

    if usable.any():
        bounds = np.asarray(shapely.bounds(geoms[usable]))
        finite = np.isfinite(bounds).all(axis=1)
        if not finite.all():
            problems.append(f"{(~finite).sum()} geometries have non-finite coordinates")
        world_ok = (
            (bounds[:, 0] >= WORLD_BOUNDS[0])
            & (bounds[:, 1] >= WORLD_BOUNDS[1])
            & (bounds[:, 2] <= WORLD_BOUNDS[2])
            & (bounds[:, 3] <= WORLD_BOUNDS[3])
        )
        usable_labels = labels[usable].reset_index(drop=True)
        if not world_ok.all():
            problems.append(
                f"{(~world_ok).sum()} geometries have coordinates outside longitude/latitude "
                f"range, e.g. {_examples(usable_labels, ~world_ok)}"
            )
        outside = ~inside_bbox(bounds, bbox, mode=bounds_mode)
        if outside.any():
            relation = "not inside" if bounds_mode == "within" else "not touching"
            problems.append(
                f"{outside.sum()} geometries {relation} the Rwanda envelope "
                f"{bbox.as_tuple()}, e.g. {_examples(usable_labels, outside)}"
            )
    return problems


def inside_bbox(bounds: np.ndarray, bbox: BoundingBox, *, mode: str) -> np.ndarray:
    """For each row of (minx, miny, maxx, maxy), whether it is within or intersects ``bbox``."""
    min_lon, min_lat, max_lon, max_lat = bbox.as_tuple()
    if mode == "within":
        return (
            (bounds[:, 0] >= min_lon)
            & (bounds[:, 1] >= min_lat)
            & (bounds[:, 2] <= max_lon)
            & (bounds[:, 3] <= max_lat)
        )
    if mode == "intersects":
        return (
            (bounds[:, 2] >= min_lon)
            & (bounds[:, 3] >= min_lat)
            & (bounds[:, 0] <= max_lon)
            & (bounds[:, 1] <= max_lat)
        )
    raise ValueError(f"Unknown bounds mode {mode!r}")


def repair_polygons(geoms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Make invalid (multi)polygons valid, keeping only their polygonal parts.

    Returns the new geometries (as MultiPolygon, or None when nothing polygonal is left)
    and a boolean mask of the rows that were repaired. Valid rows are returned unchanged.
    """
    geoms = np.asarray(geoms, dtype=object).copy()
    repaired = ~np.asarray(shapely.is_valid(geoms))
    for index in np.flatnonzero(repaired):
        fixed = shapely.make_valid(geoms[index])
        polygons = [
            part
            for part in shapely.get_parts(fixed)
            if shapely.get_type_id(part) in (_POLYGON, _MULTIPOLYGON)
        ]
        parts = [p for poly in polygons for p in shapely.get_parts(poly)]
        geoms[index] = shapely.MultiPolygon(parts) if parts else None
    return geoms, repaired


def to_multipolygon(geoms: np.ndarray) -> np.ndarray:
    """Promote Polygons to MultiPolygons so a layer has one polygonal geometry type."""
    out = np.asarray(geoms, dtype=object).copy()
    for index, geom in enumerate(out):
        if geom is not None and shapely.get_type_id(geom) == _POLYGON:
            out[index] = shapely.MultiPolygon([geom])
    return out


_POLYGON = 3
_MULTIPOLYGON = 6
_TYPE_NAMES = {
    0: "Point",
    1: "LineString",
    2: "LinearRing",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}


def geometry_type_counts(frame: gpd.GeoDataFrame) -> dict[str, int]:
    """Number of geometries of each type, sorted by type name."""
    counts = frame.geometry.geom_type.value_counts(dropna=False)
    return {str(name): int(count) for name, count in sorted(counts.items(), key=str)}
