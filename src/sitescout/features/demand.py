"""Demand features: modelled population within 1, 5 and 10 km (SPEC §4), and the
reported-only border diagnostic ``outside_rwanda_share_10km`` (D-036).

The WorldPop raster stays in EPSG:4326, as published. For each candidate, a window of pixels
around it is chosen from the metric square that contains the largest circle; the square is
converted to degrees only to pick that window, never to measure. Each pixel centre is then
projected to EPSG:32735 and kept when its distance in metres is at most the radius.
Population is the sum of the kept pixels' values in float64: people per pixel, a modelled
estimate. A nodata pixel adds 0 (in WorldPop's constrained model it has no settlement). The
raster has no pixels outside Rwanda, so circles that cross the border count only the people
inside Rwanda; ``outside_rwanda_share_10km`` shows how much of the circle that leaves out.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyproj
import rasterio
import shapely

from sitescout.config import Settings
from sitescout.crs import check_crs, check_metric_crs

# Points on each side of the metric square when converting it to degrees, so the window
# covers the square even though its edges curve slightly in EPSG:4326.
_DENSIFY = 21
# Segments per quarter circle for the border diagnostic's circle.
_CIRCLE_SEGMENTS = 64


def population_within(
    points: gpd.GeoSeries, raster_path: Path, radii_m: tuple[int, ...], settings: Settings
) -> dict[int, np.ndarray]:
    """{radius: population within it for each point}; ``points`` are in the metric CRS.

    Sums are built ring by ring from the smallest radius outward, adding non-negative
    values, so the population within a larger radius is never below a smaller one.
    """
    metric = check_metric_crs(settings.crs.metric)
    check_crs(points.crs, settings.crs.metric, "Candidate points for population")
    radii = sorted(radii_m)
    with rasterio.open(raster_path) as dataset:
        check_crs(dataset.crs, settings.crs.storage, f"Population raster ({raster_path.name})")
        band = dataset.read(1, masked=True)
        transform = dataset.transform
    people = np.where(np.ma.getmaskarray(band), 0.0, band.data.astype(np.float64))
    height, width = people.shape
    to_degrees = pyproj.Transformer.from_crs(metric, settings.crs.storage, always_xy=True)
    to_metres = pyproj.Transformer.from_crs(settings.crs.storage, metric, always_xy=True)

    result = {radius: np.zeros(len(points), dtype=np.float64) for radius in radii}
    largest = radii[-1]
    for index, point in enumerate(points.array):
        x, y = point.x, point.y
        west, south, east, north = to_degrees.transform_bounds(
            x - largest, y - largest, x + largest, y + largest, densify_pts=_DENSIFY
        )
        # Pixel columns and rows covering the square, padded by one pixel on each side.
        col0 = max(math.floor((west - transform.c) / transform.a) - 1, 0)
        col1 = min(math.ceil((east - transform.c) / transform.a) + 1, width)
        row0 = max(math.floor((north - transform.f) / transform.e) - 1, 0)
        row1 = min(math.ceil((south - transform.f) / transform.e) + 1, height)
        if col0 >= col1 or row0 >= row1:
            continue  # the circle lies outside the raster: no modelled population in it
        lon = transform.c + (np.arange(col0, col1) + 0.5) * transform.a
        lat = transform.f + (np.arange(row0, row1) + 0.5) * transform.e
        grid_lon, grid_lat = np.meshgrid(lon, lat)
        px, py = to_metres.transform(grid_lon, grid_lat)
        distance = np.hypot(px - x, py - y)
        window = people[row0:row1, col0:col1]
        total, inner = 0.0, -1.0
        for radius in radii:
            ring = (distance <= radius) & (distance > inner)
            total += float(window[ring].sum(dtype=np.float64))
            result[radius][index] = total
            inner = radius
    return result


def outside_share(points: gpd.GeoSeries, country: shapely.Geometry, radius_m: int) -> np.ndarray:
    """Share of each point's ``radius_m`` circle whose area lies outside ``country``.

    ``points`` and ``country`` are in the metric CRS. Reported only (D-036): it never changes
    a population value.
    """
    shares = np.zeros(len(points), dtype=np.float64)
    shapely.prepare(country)
    for index, point in enumerate(points.array):
        circle = point.buffer(radius_m, quad_segs=_CIRCLE_SEGMENTS)
        if shapely.contains(country, circle):
            continue  # wholly inside: exactly 0, without area rounding
        if not shapely.intersects(country, circle):
            shares[index] = 1.0
            continue
        inside = shapely.intersection(circle, country).area
        shares[index] = min(max(1.0 - inside / circle.area, 0.0), 1.0)
    return shares
