"""Coordinate reference systems: store in EPSG:4326, measure in EPSG:32735.

Every stored layer is in the storage CRS (longitude/latitude degrees). Distances and areas
are only ever computed after projecting to the metric CRS, whose unit must be the metre;
these helpers refuse to measure anything in degrees (CLAUDE.md, SPEC §2).
"""

from __future__ import annotations

import geopandas as gpd
import pyproj
from pyproj.exceptions import CRSError as PyprojCRSError
from shapely.geometry.base import BaseGeometry

from sitescout.config import CrsSettings


class CrsError(ValueError):
    """A CRS is missing, unreadable, different from the one required, or unfit for metres."""


def parse_crs(value: object) -> pyproj.CRS:
    """Parse a CRS such as ``"EPSG:4326"``; raise CrsError if it is missing or unknown."""
    if value is None:
        raise CrsError("CRS is missing")
    try:
        return pyproj.CRS.from_user_input(value)
    except PyprojCRSError as error:
        raise CrsError(f"Unknown CRS {value!r}: {error}") from error


def same_crs(actual: object, expected: object) -> bool:
    """True when both describe the same CRS, ignoring axis order.

    GeoJSON's OGC:CRS84 and EPSG:4326 differ only in axis order, and geopandas always
    handles coordinates as (longitude, latitude), so they count as the same here.
    """
    return parse_crs(actual).equals(parse_crs(expected), ignore_axis_order=True)


def check_crs(actual: object, expected: str, what: str) -> None:
    """Raise CrsError unless ``actual`` is the ``expected`` CRS."""
    if actual is None:
        raise CrsError(f"{what} has no CRS; expected {expected}")
    if not same_crs(actual, expected):
        raise CrsError(f"{what} is in {parse_crs(actual).to_string()}, expected {expected}")


def check_metric_crs(crs: object) -> pyproj.CRS:
    """Return ``crs`` if it is projected with metre axes, else raise CrsError."""
    parsed = parse_crs(crs)
    if not parsed.is_projected:
        raise CrsError(f"{parsed.to_string()} is not projected; distances need metres")
    units = {axis.unit_name for axis in parsed.axis_info}
    if units != {"metre"}:
        raise CrsError(f"{parsed.to_string()} has axis units {sorted(units)}, not metres")
    return parsed


def to_storage[FrameT: (gpd.GeoDataFrame, gpd.GeoSeries)](
    frame: FrameT, crs: CrsSettings
) -> FrameT:
    """Reproject ``frame`` to the storage CRS; a frame without a CRS is refused."""
    if frame.crs is None:
        raise CrsError("Cannot reproject data that has no CRS")
    if same_crs(frame.crs, crs.storage):
        return frame.set_crs(crs.storage, allow_override=True)
    return frame.to_crs(crs.storage)


def to_metric[FrameT: (gpd.GeoDataFrame, gpd.GeoSeries)](frame: FrameT, crs: CrsSettings) -> FrameT:
    """Project stored data (which must be in the storage CRS) to the metric CRS."""
    check_crs(frame.crs, crs.storage, "Input to a metric calculation")
    check_metric_crs(crs.metric)
    return frame.to_crs(crs.metric)


def distance_m(a: gpd.GeoSeries, b: gpd.GeoSeries | BaseGeometry, crs: CrsSettings) -> list[float]:
    """Distance in metres from each geometry in ``a`` to ``b``, computed in the metric CRS.

    ``b`` is a GeoSeries aligned with ``a``, or one geometry in the storage CRS.
    """
    projected = to_metric(a, crs)
    if isinstance(b, BaseGeometry):
        other: gpd.GeoSeries | BaseGeometry = to_metric(
            gpd.GeoSeries([b], crs=crs.storage), crs
        ).iloc[0]
    else:
        other = to_metric(b, crs)
    return projected.distance(other, align=False).tolist()


def area_km2(frame: gpd.GeoDataFrame | gpd.GeoSeries, crs: CrsSettings) -> list[float]:
    """Area of each geometry in square kilometres, computed in the metric CRS."""
    return (to_metric(frame, crs).area / 1_000_000).tolist()


def length_km(frame: gpd.GeoDataFrame | gpd.GeoSeries, crs: CrsSettings) -> list[float]:
    """Length of each geometry in kilometres, computed in the metric CRS."""
    return (to_metric(frame, crs).length / 1_000).tolist()
