"""SYNTHETIC test fixtures for Milestone 1. None of this is real data.

Every fixture is built in a temporary directory at test time: a tiny OSM file, four square
"districts" in two "provinces", a small population GeoTIFF, a two-line transmission
shapefile and charger CSVs. Coordinates sit inside the Rwanda envelope only so that the
bounds checks can pass; the features do not describe anything real. Names are prefixed
"SYNTHETIC" so they can never be mistaken for source data.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmium
import rasterio
import shapely
from rasterio.transform import Affine

from sitescout.config import Config, build_config
from sitescout.ingest.acquire import fetch, raw_sources

SYNTHETIC = "SYNTHETIC"
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
OSM_TIMESTAMP = "2026-01-01T00:00:00Z"

# A 2x2 grid of 0.1-degree squares near 30.0 E, 1.9 S.
DISTRICT_CELLS = {
    "SYN-D1": ("SYNTHETIC District 1", 30.0, -1.9),
    "SYN-D2": ("SYNTHETIC District 2", 30.1, -1.9),
    "SYN-D3": ("SYNTHETIC District 3", 30.0, -2.0),
    "SYN-D4": ("SYNTHETIC District 4", 30.1, -2.0),
}


def synthetic_config(settings_data: dict[str, Any], weights_data: dict[str, Any]) -> Config:
    """The real settings, with boundary counts matching the synthetic districts."""
    settings = {**settings_data}
    boundaries = {**settings["sources"]["boundaries"]}
    boundaries["expected_units"] = {"ADM2": 4, "ADM1": 2}
    settings["sources"] = {**settings["sources"], "boundaries": boundaries}
    return build_config(settings, weights_data)


def square(west: float, north: float, size: float = 0.1) -> shapely.Polygon:
    return shapely.box(west, north - size, west + size, north)


# --- Boundaries -----------------------------------------------------------------------------


def districts_geojson(**changes: Any) -> bytes:
    rows = [
        {
            "shapeName": name,
            "shapeISO": "",
            "shapeID": shape_id,
            "shapeGroup": "RWA",
            "shapeType": "ADM2",
            "geometry": square(west, north),
        }
        for shape_id, (name, west, north) in DISTRICT_CELLS.items()
    ]
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    for column, value in changes.items():
        frame[column] = value
    return _geojson(frame)


def provinces_geojson(west_edge: float = 30.102, east_edge: float = 30.102) -> bytes:
    # West province covers districts 1 and 3; east covers 2 and 4. The edge between them
    # is shifted slightly into districts 2 and 4 so overlap shares are below 1, as they are
    # with real data. west_edge/east_edge move the two provinces' shared edge apart.
    rows = [
        {
            "shapeName": "SYNTHETIC West Province",
            "shapeISO": "RW-91",
            "shapeID": "SYN-P1",
            "shapeGroup": "RWA",
            "shapeType": "ADM1",
            "geometry": shapely.box(30.0, -2.1, west_edge, -1.8),
        },
        {
            "shapeName": "SYNTHETIC East Province",
            "shapeISO": "RW-92",
            "shapeID": "SYN-P2",
            "shapeGroup": "RWA",
            "shapeType": "ADM1",
            "geometry": shapely.box(east_edge, -2.1, 30.2, -1.8),
        },
    ]
    return _geojson(gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326"))


def _geojson(frame: gpd.GeoDataFrame) -> bytes:
    return frame.to_json(drop_id=True).encode("utf-8")


# --- Population raster -----------------------------------------------------------------------

ARCSEC = 1 / 3600


def population_tif(
    path: Path,
    *,
    crs: str | None = "EPSG:4326",
    pixel_arcsec: float = 3.0,
    nodata: float | None = -99999.0,
    values: np.ndarray | None = None,
    west: float = 30.0,
    north: float = -1.9,
) -> Path:
    """A 10x20 float32 grid; the left half holds people, the right half is nodata."""
    if values is None:
        values = np.full((10, 20), -99999.0, dtype=np.float32)
        values[:, :10] = np.arange(100, dtype=np.float32).reshape(10, 10) / 10
    size = pixel_arcsec * ARCSEC
    profile = {
        "driver": "GTiff",
        "width": values.shape[1],
        "height": values.shape[0],
        "count": 1,
        "dtype": "float32",
        "transform": Affine(size, 0.0, west, 0.0, -size, north),
    }
    if crs is not None:
        profile["crs"] = crs
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values.astype(np.float32), 1)
    return path


# --- Transmission lines ----------------------------------------------------------------------


def transmission_zip(
    directory: Path, *, lines: list[shapely.LineString] | None = None, crs: str = "EPSG:4326"
) -> bytes:
    lines = lines or [
        shapely.LineString([(30.01, -1.91), (30.09, -1.99)]),
        shapely.LineString([(30.11, -1.91), (30.19, -1.95), (30.15, -2.05)]),
    ]
    frame = gpd.GeoDataFrame(
        {
            "COUNTRY": ["RWA"] * len(lines),
            "VOLTAGE_KV": [110] * len(lines),
            "FROM_NM": ["SYNTHETIC A"] * len(lines),
            "TO_NM": [None] + ["SYNTHETIC B"] * (len(lines) - 1),
            "STATUS": ["Existing"] * len(lines),
            "SOURCES": ["SYNTHETIC source list"] * len(lines),
            "PROJECT_NM": [None] * len(lines),
        },
        geometry=lines,
        crs="EPSG:4326",
    ).to_crs(crs)
    shp_dir = directory / "synthetic-transmission"
    shp_dir.mkdir(parents=True, exist_ok=True)
    frame.to_file(shp_dir / "lines.shp", driver="ESRI Shapefile")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for part in sorted(shp_dir.iterdir()):
            archive.write(part, part.name)
    return buffer.getvalue()


# --- OSM ---------------------------------------------------------------------------------------


def osm_pbf(path: Path) -> Path:
    """A tiny SYNTHETIC OSM extract that exercises every OSM layer rule."""
    Node, Way, Relation = (
        osmium.osm.mutable.Node,
        osmium.osm.mutable.Way,
        osmium.osm.mutable.Relation,
    )
    header = osmium.io.Header()
    header.set("osmosis_replication_timestamp", OSM_TIMESTAMP)
    nodes: dict[int, tuple[float, float]] = {}

    def ring(start: int, west: float, north: float, size: float = 0.004) -> list[int]:
        corners = [
            (west, north),
            (west + size, north),
            (west + size, north - size),
            (west, north - size),
        ]
        for offset, corner in enumerate(corners):
            nodes[start + offset] = corner
        return [start, start + 1, start + 2, start + 3, start]

    tagged_nodes = {
        1: (
            (30.02, -1.92),
            {
                "amenity": "fuel",
                "name": "SYNTHETIC Brand",
                "brand": "SYNTHETIC",
                "socket:type2": "2",
            },
        ),
        2: ((30.03, -1.92), {"amenity": "restaurant", "name": "SYNTHETIC Diner"}),
        3: (
            (30.04, -1.92),
            {
                "amenity": "charging_station",
                "socket:type2": "2",
                "capacity": "2",
                "operator": "SYNTHETIC Operator",
            },
        ),
        4: ((30.05, -1.92), {"power": "tower"}),
        5: ((29.50, -3.30), {"power": "tower"}),  # wholly outside the Rwanda envelope
        6: ((30.06, -1.92), {"shop": "supermarket"}),
        7: ((30.07, -1.92), {"place": "city", "name": "SYNTHETIC City", "capital": "yes"}),
        8: ((30.08, -1.92), {"place": "town", "name": "SYNTHETIC Town"}),
        9: ((30.09, -1.92), {"place": "village"}),  # not a town or city
    }
    ways: list[tuple[int, list[int], dict[str, str]]] = []
    nodes.update({10: (30.00, -1.95), 11: (30.05, -1.95), 12: (30.10, -1.96)})
    ways.append((100, [10, 11, 12], {"highway": "trunk", "ref": "SYN1", "name": "SYNTHETIC Rd"}))
    ways.append((101, ring(20, 30.02, -1.97), {"highway": "primary"}))  # closed road stays a line
    ways.append((102, ring(30, 30.03, -1.97), {"shop": "mall", "name": "SYNTHETIC Mall"}))
    ways.append((103, ring(40, 30.04, -1.97), {"building": "commercial"}))
    ways.append((104, ring(50, 30.05, -1.97), {"building": "yes"}))  # not a POI
    nodes.update({60: (30.06, -1.97), 61: (30.09, -1.99)})
    ways.append((105, [60, 61], {"power": "line", "voltage": "110000"}))
    ways.append((106, ring(70, 30.07, -1.97), {"power": "substation", "voltage": "110000"}))
    ways.append((107, ring(80, 30.08, -1.97), {"power": "minor_line"}))  # closed, still a line
    ways.append((108, ring(90, 30.02, -2.01), {"natural": "water", "water": "lake"}))
    ways.append((109, ring(110, 30.03, -2.01), {}))  # outer ring of relation 301
    ways.append((111, ring(120, 30.04, -2.01), {}))  # outer ring of relation 302
    ways.append((114, ring(150, 30.05, -2.01), {"boundary": "national_park", "protect_class": "2"}))
    ways.append((115, ring(160, 30.06, -2.03), {"place": "town"}))  # places are nodes only
    nodes.update({130: (30.06, -2.01), 131: (30.07, -2.02)})
    ways.append((112, [130, 131], {"amenity": "parking"}))  # open way: POIs take nodes/areas
    # A self-intersecting "bow-tie" ring tagged as water.
    nodes.update(
        {140: (30.08, -2.01), 141: (30.09, -2.02), 142: (30.09, -2.01), 143: (30.08, -2.02)}
    )
    ways.append((113, [140, 141, 142, 143, 140], {"natural": "water"}))
    relations = [
        (300, [("w", 105, "")], {"type": "route", "route": "power", "power": "line"}),
        (301, [("w", 109, "outer")], {"type": "multipolygon", "natural": "water"}),
        (
            302,
            [("w", 111, "outer")],
            {
                "type": "boundary",
                "boundary": "protected_area",
                "protect_class": "2",
                "name": "SYNTHETIC Park",
            },
        ),
    ]

    writer = osmium.SimpleWriter(str(path), header=header, overwrite=True)
    try:
        all_nodes = {**{i: loc for i, (loc, _) in tagged_nodes.items()}, **nodes}
        for node_id in sorted(all_nodes):
            tags = tagged_nodes[node_id][1] if node_id in tagged_nodes else {}
            writer.add_node(Node(id=node_id, location=all_nodes[node_id], tags=tags))
        for way_id, refs, tags in sorted(ways):
            writer.add_way(Way(id=way_id, nodes=refs, tags=tags))
        for relation_id, members, tags in relations:
            writer.add_relation(Relation(id=relation_id, members=members, tags=tags))
    finally:
        writer.close()
    return path


# --- Chargers ----------------------------------------------------------------------------------

CHARGER_HEADER = "name,lat,lon,source_url,date_retrieved,operator_public_name"


def charger_csv(path: Path, rows: list[str], header: str = CHARGER_HEADER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


VALID_CHARGER_ROWS = [
    "SYNTHETIC Charger A,-1.9500,30.0500,https://example.org/synthetic-a,2026-01-01,SYNTHETIC Op",
    "SYNTHETIC Charger B,-1.9600,30.0600,https://example.org/synthetic-b,2026-01-02,",
]


# --- Installing raw files -----------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    """Stands in for an HTTP response so tests never touch the network."""

    def __init__(self, data: bytes, url: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Length": str(len(data)), **(headers or {})}

    def geturl(self) -> str:
        return self._url


def serve(data: bytes, headers: dict[str, str] | None = None) -> Callable[[Any], FakeResponse]:
    return lambda request: FakeResponse(data, request.full_url, headers)


def install_raw(config: Config, raw_dir: Path, source_id: str, data: bytes) -> None:
    """Put SYNTHETIC bytes in data/raw/<source_id>/ through the real fetch code."""
    fetch(
        raw_sources(config.settings)[source_id], raw_dir, opener=serve(data), now=lambda: FIXED_TIME
    )


def install_all_raw(config: Config, raw_dir: Path, work: Path) -> None:
    install_raw(config, raw_dir, "boundaries_adm2", districts_geojson())
    install_raw(config, raw_dir, "boundaries_adm1", provinces_geojson())
    install_raw(config, raw_dir, "worldpop", population_tif(work / "pop.tif").read_bytes())
    install_raw(config, raw_dir, "grid_transmission", transmission_zip(work))
    install_raw(config, raw_dir, "osm", osm_pbf(work / "synthetic.osm.pbf").read_bytes())


# --- Candidate generation (Milestone 2) ---------------------------------------------------------
# A SYNTHETIC world of two districts crossed by one straight trunk road along 1.95 S.
# Along the trunk, corridor points fall about 5, 15, 25, 35, 45 and 55 km from its west end.

TRUNK = shapely.LineString([(30.00, -1.95), (30.50, -1.95)])
WATER_BOX = shapely.box(30.40, -1.96, 30.41, -1.94)  # covers the 5th corridor point
PARK_BOX = shapely.box(30.48, -1.97, 30.52, -1.93)  # covers the 6th corridor point
MALL_BOX = shapely.box(30.07, -1.903, 30.072, -1.902)
INDUSTRIAL_BOX = shapely.box(30.30, -1.954, 30.305, -1.946)  # straddles the trunk

_POI_TAGS = (
    "amenity",
    "shop",
    "tourism",
    "office",
    "industrial",
    "landuse",
    "building",
    "man_made",
)


def _poi(feature_id: str, geometry: shapely.Geometry, **tags: str) -> dict[str, Any]:
    osm_type, osm_id = feature_id.split("/")
    sockets = {k.replace("__", ":"): v for k, v in tags.items() if k.startswith("socket")}
    row: dict[str, Any] = {"feature_id": feature_id, "osm_type": osm_type, "osm_id": int(osm_id)}
    row.update({key: tags.get(key) for key in _POI_TAGS})
    row["socket_tags"] = json.dumps(sockets, sort_keys=True) if sockets else None
    row["geometry_repaired"] = False
    polygonal = geometry.geom_type == "Polygon"
    row["geometry"] = shapely.MultiPolygon([geometry]) if polygonal else geometry
    return row


def candidate_pois() -> list[dict[str, Any]]:
    """SYNTHETIC POIs: hosts, non-hosts and chargers."""
    point = shapely.Point
    return [
        _poi("node/10", point(30.080, -1.9010), amenity="fuel"),
        _poi("node/11", point(30.081, -1.9012), tourism="hotel"),  # 115 m from node/10
        _poi("node/12", point(30.020, -1.9010), shop="supermarket"),
        _poi("node/13", point(30.030, -1.9010), amenity="restaurant"),  # never a host
        _poi("node/14", point(30.021, -1.9010), amenity="charging_station", socket__type2="2"),
        _poi("node/15", point(30.050, -1.9005), amenity="fuel", socket__type2="1"),
        _poi("way/16", MALL_BOX, shop="mall"),
        _poi("node/17", point(30.350, -2.0490), tourism="hotel"),  # only a track nearby
        _poi("way/18", INDUSTRIAL_BOX, landuse="industrial"),
        _poi("node/19", point(30.600, -1.9500), amenity="fuel"),  # outside the country
        _poi("node/20", point(30.137, -1.9535), amenity="fuel"),  # 390 m off the trunk
    ]


def _road(osm_id: int, highway: str, line: shapely.LineString) -> dict[str, Any]:
    return {
        "feature_id": f"way/{osm_id}",
        "osm_type": "way",
        "osm_id": osm_id,
        "highway": highway,
        "ref": None,
        "surface": None,
        "access": None,
        "motor_vehicle": None,
        "geometry": line,
    }


def candidate_roads() -> list[dict[str, Any]]:
    return [
        _road(1, "trunk", TRUNK),
        _road(2, "residential", shapely.LineString([(30.00, -1.90), (30.10, -1.90)])),
        _road(3, "track", shapely.LineString([(30.30, -2.05), (30.40, -2.05)])),
    ]


def write_candidate_world(
    processed: Path,
    settings: Any,
    *,
    pois: list[dict[str, Any]] | None = None,
    roads: list[dict[str, Any]] | None = None,
) -> None:
    """Write the SYNTHETIC M1 layers candidate generation reads into ``processed``."""
    from sitescout.ingest.layers import LAYERS, empty_frame, write_layer

    def frame(rows: list[dict[str, Any]], name: str) -> gpd.GeoDataFrame:
        if not rows:
            return empty_frame(LAYERS[name], "EPSG:4326")
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    districts = [
        {
            "district_id": "SYN-D1",
            "district_name": "SYNTHETIC District 1",
            "province_code": "RW-91",
            "province_name": "SYNTHETIC West Province",
            "province_overlap_share": 1.0,
            "area_km2": 1.0,
            "geometry": shapely.MultiPolygon([shapely.box(29.95, -2.10, 30.25, -1.80)]),
        },
        {
            "district_id": "SYN-D2",
            "district_name": "SYNTHETIC District 2",
            "province_code": "RW-92",
            "province_name": "SYNTHETIC East Province",
            "province_overlap_share": 1.0,
            "area_km2": 1.0,
            "geometry": shapely.MultiPolygon([shapely.box(30.25, -2.10, 30.55, -1.80)]),
        },
    ]
    country = [
        {
            "country_iso3": "RWA",
            "district_count": 2,
            "province_count": 2,
            "area_km2": 2.0,
            "geometry": shapely.MultiPolygon([shapely.box(29.95, -2.10, 30.55, -1.80)]),
        }
    ]
    water = [
        {
            "feature_id": "way/40",
            "osm_type": "way",
            "osm_id": 40,
            "water": "lake",
            "geometry_repaired": False,
            "geometry": shapely.MultiPolygon([WATER_BOX]),
        }
    ]
    parks = [
        {
            "feature_id": "relation/50",
            "osm_type": "relation",
            "osm_id": 50,
            "boundary": "national_park",
            "protect_class": "2",
            "geometry_repaired": False,
            "geometry": shapely.MultiPolygon([PARK_BOX]),
        }
    ]
    layers = {
        "admin_districts": districts,
        "admin_country": country,
        "osm_pois": candidate_pois() if pois is None else pois,
        "osm_roads": candidate_roads() if roads is None else roads,
        "osm_water": water,
        "osm_protected_areas": parks,
    }
    for name, rows in layers.items():
        write_layer(frame(rows, name), LAYERS[name], processed, settings, sources=[])
