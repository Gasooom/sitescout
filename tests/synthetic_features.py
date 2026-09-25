"""SYNTHETIC test fixtures for Milestone 3 feature engineering. None of this is real data.

The world is laid out in metres around ORIGIN in EPSG:32735, so expected distances can be
read off the layout: ``at(dx, dy)`` is the point ``dx`` metres east and ``dy`` metres north
of ORIGIN. Candidate A sits at ORIGIN (a fuel host, SYN-D1), B 33 km east (no host,
SYN-D2), C 12 km south (a hotel host, near the country's southern edge).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyproj
import shapely

from sitescout.config import Config, build_config
from synthetic import ARCSEC, _poi, _road, population_tif, synthetic_config, write_candidate_world

ORIGIN = (30.10, -1.95)
KIGALI_TEST_NODE = "node/60"
_TO_METRIC = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
_TO_DEGREES = pyproj.Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True)
_OX, _OY = _TO_METRIC.transform(*ORIGIN)


def at(dx: float, dy: float) -> shapely.Point:
    """The point ``dx`` m east and ``dy`` m north of ORIGIN, in EPSG:4326."""
    return shapely.Point(*_TO_DEGREES.transform(_OX + dx, _OY + dy))


def metric_xy(point: shapely.Point) -> tuple[float, float]:
    """(x, y) in metres from ORIGIN, measured in EPSG:32735."""
    x, y = _TO_METRIC.transform(point.x, point.y)
    return x - _OX, y - _OY


def box_at(dx: float, dy: float, half: float) -> shapely.Polygon:
    """A square of side 2*half metres centred at(dx, dy), in EPSG:4326."""
    corners = [(-half, -half), (half, -half), (half, half), (-half, half)]
    return shapely.Polygon([at(dx + x, dy + y) for x, y in corners])


def line_at(points: list[tuple[float, float]], step: float = 1000.0) -> shapely.LineString:
    """A line through metric waypoints, with a vertex every ``step`` m."""
    vertices = []
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        parts = max(int(np.hypot(x1 - x0, y1 - y0) // step), 1)
        vertices += [
            at(x0 + (x1 - x0) * i / parts, y0 + (y1 - y0) * i / parts) for i in range(parts)
        ]
    vertices.append(at(*points[-1]))
    return shapely.LineString(vertices)


def feature_config(
    settings_data: dict[str, Any], weights_data: dict[str, Any], **overrides: Any
) -> Config:
    """The real settings for the SYNTHETIC feature world, whose Kigali node is node/60."""
    base = synthetic_config(settings_data, weights_data)
    return build_config(
        base.snapshot()["settings"],
        weights_data,
        overrides={"settings.features.kigali_cbd.osm_id": KIGALI_TEST_NODE, **overrides},
    )


_DISTRICTS = {
    "SYN-D1": ("SYNTHETIC District 1", "RW-91", "SYNTHETIC West Province"),
    "SYN-D2": ("SYNTHETIC District 2", "RW-92", "SYNTHETIC East Province"),
}


def candidate(
    candidate_id: str,
    point: shapely.Point,
    host_type: str = "none",
    host_osm_id: str | None = None,
    district: str = "SYN-D1",
    road_class: str = "residential",
    dist_road: float = 10.0,
) -> dict[str, Any]:
    name, code, province = _DISTRICTS[district]
    return {
        "candidate_id": candidate_id,
        "lat": point.y,
        "lon": point.x,
        "host_name": f"SYNTHETIC {host_type}, {name}",
        "host_type": host_type,
        "host_osm_id": host_osm_id,
        "origin": "host" if host_osm_id else "corridor",
        "merged_count": 0,
        "district_id": district,
        "district": name,
        "province_code": code,
        "province": province,
        "nearest_road_class": road_class,
        "dist_road_m": dist_road,
        "profile": None,
        "geometry": point,
    }


def feature_candidates() -> list[dict[str, Any]]:
    return [
        candidate("cand-a", at(0, 0), "fuel", "node/10", "SYN-D1", "trunk", 5.0),
        candidate("cand-b", at(33000, 0), "none", None, "SYN-D2", "primary", 12.0),
        candidate("cand-c", at(0, -12000), "hotel", "node/30", "SYN-D1", "residential", 40.0),
    ]


def feature_pois() -> list[dict[str, Any]]:
    """POIs around candidate A, and C's own host."""
    return [
        _poi("node/10", at(0, 0), amenity="fuel"),  # A's own host: never counted for A
        _poi("node/11", at(999, 0), amenity="restaurant"),  # within 1 km
        _poi("node/12", at(1001, 0), shop="supermarket"),  # another host, 1-3 km: counted
        _poi("node/13", at(200, 0), amenity="charging_station"),  # never a POI
        _poi("node/14", at(0, 500), amenity="cafe", shop="bakery"),  # two types, one POI
        _poi("way/15", box_at(0, 2000, 100), tourism="hotel"),  # an area POI, 1-3 km
        _poi("node/16", at(300, 0), landuse="industrial"),  # not a POI type
        _poi("node/17", at(-2000, 0), amenity="fuel", socket__type2="1"),  # also a charger
        _poi("node/30", at(0, -12000), tourism="hotel"),  # C's own host
    ]


def feature_roads() -> list[dict[str, Any]]:
    return [
        _road(1, "trunk", line_at([(-20000, 3000), (60000, 3000)])),
        _road(2, "trunk_link", line_at([(-500, -10000), (500, -10000)], step=100)),
        _road(3, "residential", line_at([(-1000, -12040), (1000, -12040)], step=100)),
    ]


def charger(
    feature_id: str, geometry: shapely.Geometry, access: str | None = None
) -> dict[str, Any]:
    osm_type, osm_id = feature_id.split("/")
    polygonal = geometry.geom_type == "Polygon"
    return {
        "feature_id": feature_id,
        "osm_type": osm_type,
        "osm_id": int(osm_id),
        "socket_tags": None,
        "capacity": None,
        "access": access,
        "geometry_repaired": False,
        "geometry": shapely.MultiPolygon([geometry]) if polygonal else geometry,
    }


def feature_chargers() -> list[dict[str, Any]]:
    return [
        charger("node/13", at(200, 0)),
        charger("node/40", at(220, 0)),  # 20 m from node/13: the same site
        charger("node/41", at(260, 0)),  # 40 m from node/40, 60 m from node/13: chained
        charger("way/42", box_at(20000, 0, 50)),  # an area charger
        charger("node/43", at(0, -11000), access="private"),  # not public
        charger("node/44", at(33000, 500), access="no"),  # not public
    ]


def power(feature_id: str, value: str, geometry: shapely.Geometry) -> dict[str, Any]:
    osm_type, osm_id = feature_id.split("/")
    polygonal = geometry.geom_type == "Polygon"
    return {
        "feature_id": feature_id,
        "osm_type": osm_type,
        "osm_id": int(osm_id),
        "power": value,
        "voltage": None,
        "substation": None,
        "geometry_repaired": False,
        "geometry": shapely.MultiPolygon([geometry]) if polygonal else geometry,
    }


def feature_power() -> list[dict[str, Any]]:
    """SYN-D1 holds 3 counted features, SYN-D2 holds 4 (the line way/51 crosses both)."""
    return [
        power("way/50", "substation", box_at(0, -12000, 100)),  # contains candidate C
        power("way/51", "line", line_at([(-5000, 4000), (60000, 4000)])),
        power("node/53", "150kWh", at(0, 0)),  # not a power type: never counted
        power("node/54", "tower", at(-5000, 4000)),
        power("node/55", "tower", at(40000, 4000)),
        power("node/56", "pole", at(40000, 5000)),
        power("node/57", "pole", at(40000, 6000)),
    ]


def place(feature_id: str, value: str, point: shapely.Point) -> dict[str, Any]:
    osm_type, osm_id = feature_id.split("/")
    return {
        "feature_id": feature_id,
        "osm_type": osm_type,
        "osm_id": int(osm_id),
        "place": value,
        "geometry": point,
    }


def feature_places() -> list[dict[str, Any]]:
    return [
        place(KIGALI_TEST_NODE, "city", at(5000, 0)),
        place("node/61", "town", at(0, -9000)),
        place("node/62", "town", shapely.Point(30.60, -1.95)),  # outside the country
    ]


# People placed near candidate A: 10 at 0.5 km, 20 at 3 km, 40 at 7 km, 80 at 20 km.
FEATURE_POPULATION = {(500, 0): 10.0, (3000, 0): 20.0, (7000, 0): 40.0, (20000, 0): 80.0}
RASTER_WEST, RASTER_NORTH = 29.95, -1.80
PIXEL = 3 * ARCSEC


def feature_population(path: Path, cells: dict[tuple[float, float], float] | None = None) -> Path:
    """A 3 arc-second SYNTHETIC raster over the world: 0 people everywhere, nodata in a
    block at the south-west corner, and people only in the pixels containing ``cells``."""
    width, height = round(0.6 / PIXEL), round(0.3 / PIXEL)
    values = np.zeros((height, width), dtype=np.float32)
    values[-40:, :40] = -99999.0
    for (dx, dy), people in (FEATURE_POPULATION if cells is None else cells).items():
        point = at(dx, dy)
        row = int((RASTER_NORTH - point.y) // PIXEL)
        col = int((point.x - RASTER_WEST) // PIXEL)
        values[row, col] = people
    return population_tif(path, values=values, west=RASTER_WEST, north=RASTER_NORTH)


def write_feature_world(
    processed: Path,
    settings: Any,
    *,
    candidates: list[dict[str, Any]] | None = None,
    pois: list[dict[str, Any]] | None = None,
    roads: list[dict[str, Any]] | None = None,
    chargers: list[dict[str, Any]] | None = None,
    power_rows: list[dict[str, Any]] | None = None,
    places: list[dict[str, Any]] | None = None,
    manual: list[shapely.Point] | None = None,
    population: dict[tuple[float, float], float] | None = None,
) -> None:
    """Write every SYNTHETIC input of feature engineering into ``processed``.

    ``manual`` gives the chargers_manual rows; None records the CSV as missing.
    """
    from sitescout.ingest.layers import (
        CHARGERS_MANUAL,
        LAYERS,
        empty_frame,
        write_layer,
        write_missing,
    )
    from sitescout.ingest.metadata import sha256_file, write_json
    from sitescout.ingest.raster import POPULATION_LAYER, population_path

    write_candidate_world(
        processed,
        settings,
        pois=feature_pois() if pois is None else pois,
        roads=feature_roads() if roads is None else roads,
    )
    provinces = [
        {
            "province_code": code,
            "province_name": name,
            "district_count": 1,
            "area_km2": 1.0,
            "geometry": shapely.MultiPolygon([shapely.box(west, -2.10, west + 0.30, -1.80)]),
        }
        for code, name, west in (
            ("RW-91", "SYNTHETIC West Province", 29.95),
            ("RW-92", "SYNTHETIC East Province", 30.25),
        )
    ]
    layers = {
        "admin_provinces": provinces,
        "candidates": feature_candidates() if candidates is None else candidates,
        "osm_charging_stations": feature_chargers() if chargers is None else chargers,
        "osm_power": feature_power() if power_rows is None else power_rows,
        "osm_places": feature_places() if places is None else places,
    }
    for name, rows in layers.items():
        frame = (
            gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
            if rows
            else empty_frame(LAYERS[name], "EPSG:4326")
        )
        write_layer(frame, LAYERS[name], processed, settings, sources=[])
    if manual is None:
        write_missing(
            CHARGERS_MANUAL, processed, reason="SYNTHETIC: no CSV", expected_path="chargers.csv"
        )
    else:
        rows = [
            {
                "charger_id": f"csv-{i:012d}",
                "source_row": i + 2,
                "source_url": "https://example.org/synthetic",
                "date_retrieved": "2026-01-01",
                "geometry": point,
            }
            for i, point in enumerate(manual)
        ]
        write_layer(
            gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326"),
            CHARGERS_MANUAL,
            processed,
            settings,
            sources=[],
        )
    raster = feature_population(population_path(processed), population)
    write_json(
        processed / f"{POPULATION_LAYER}.meta.json",
        {"layer": POPULATION_LAYER, "status": "ok", "content_sha256": sha256_file(raster)},
    )


def build(processed: Path, config: Config, mode: str = "production", **world: Any):
    """Write a SYNTHETIC world (``world`` as in write_feature_world) and build one mode.

    Returns the feature table indexed by candidate_id, and the build statistics.
    """
    from sitescout.features import build_features, load_inputs

    write_feature_world(processed, config.settings, **world)
    frame, stats = build_features(load_inputs(processed, config.settings), config.settings, mode)
    return frame.set_index("candidate_id"), stats
