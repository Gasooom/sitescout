"""The processed vector layers: their schemas, and validated GeoParquet writing and reading.

Every layer is written through ``write_layer`` and read through ``read_layer``, so the same
checks run when a stage produces a layer and when the next stage consumes it (CLAUDE.md:
pipelines validate schemas between stages). Each layer ``<name>.parquet`` has a metadata
file ``<name>.meta.json`` with its schema, sources, row count, bounds and a content
fingerprint. A source that is missing gets a metadata file with ``status: missing`` and no
data file, so later stages see "unknown", never an empty layer standing in for real data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import Settings
from sitescout.crs import CrsError, check_crs
from sitescout.ingest import DataValidationError, SourceMissingError
from sitescout.ingest.geometry import check_geometry, geometry_type_counts
from sitescout.ingest.metadata import (
    RawManifest,
    content_fingerprint,
    read_json,
    source_record,
    write_atomically,
    write_json,
)
from sitescout.ingest.schema import Column, LayerSchema, check_table

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
POINT = "Point"
LINE = "LineString"
MULTILINE = "MultiLineString"
MULTIPOLYGON = "MultiPolygon"

# --- Shared columns ---------------------------------------------------------------------

_OSM_ID_COLUMNS = (
    Column("feature_id", "string", True, "OSM object as type/id, e.g. 'way/123'. Unique."),
    Column("osm_type", "string", True, "OSM object type: node, way or relation."),
    Column("osm_id", "int64", True, "OSM object id."),
)
_REPAIRED = Column(
    "geometry_repaired",
    "bool",
    True,
    "True if the OSM area was invalid and made valid with shapely.make_valid (D-020).",
)
_SOCKETS = Column(
    "socket_tags",
    "string",
    False,
    "JSON object of the feature's socket:* tags, keys sorted; null when it has none.",
)


def _osm(
    name: str, description: str, *columns: Column, types: set[str], allow_empty: bool = False
) -> LayerSchema:
    return LayerSchema(
        name=name,
        description=description,
        id_column="feature_id",
        columns=(*_OSM_ID_COLUMNS, *columns),
        geometry_types=frozenset(types),
        sort_by=("osm_type", "osm_id"),
        allow_empty=allow_empty,
        bounds_mode="intersects",
    )


# --- The layers ---------------------------------------------------------------------------

DISTRICTS = LayerSchema(
    name="admin_districts",
    description="geoBoundaries ADM2 districts: the master administrative boundary.",
    id_column="district_id",
    columns=(
        Column("district_id", "string", True, "geoBoundaries shapeID of the district."),
        Column("district_name", "string", True, "District name (geoBoundaries shapeName)."),
        Column("province_code", "string", True, "ISO 3166-2 code of the province (RW-01..05)."),
        Column("province_name", "string", True, "Province name from geoBoundaries ADM1."),
        Column(
            "province_overlap_share",
            "float64",
            True,
            "Share of the district's area inside its assigned ADM1 province (EPSG:32735).",
        ),
        Column("area_km2", "float64", True, "District area in km², computed in EPSG:32735."),
    ),
    geometry_types=frozenset({MULTIPOLYGON}),
    sort_by=("district_id",),
)

PROVINCES = LayerSchema(
    name="admin_provinces",
    description="Provinces (ADM1), dissolved from the ADM2 districts so edges match.",
    id_column="province_code",
    columns=(
        Column("province_code", "string", True, "ISO 3166-2 code of the province."),
        Column("province_name", "string", True, "Province name from geoBoundaries ADM1."),
        Column("district_count", "int64", True, "Number of ADM2 districts dissolved."),
        Column("area_km2", "float64", True, "Province area in km², computed in EPSG:32735."),
    ),
    geometry_types=frozenset({MULTIPOLYGON}),
    sort_by=("province_code",),
)

COUNTRY = LayerSchema(
    name="admin_country",
    description="Rwanda (ADM0), dissolved from the ADM2 districts so edges match.",
    id_column="country_iso3",
    columns=(
        Column("country_iso3", "string", True, "ISO 3166-1 alpha-3 code."),
        Column("district_count", "int64", True, "Number of ADM2 districts dissolved."),
        Column("province_count", "int64", True, "Number of provinces."),
        Column("area_km2", "float64", True, "Country area in km², computed in EPSG:32735."),
    ),
    geometry_types=frozenset({MULTIPOLYGON}),
    sort_by=("country_iso3",),
)

OSM_ROADS = _osm(
    "osm_roads",
    "OSM ways tagged highway=*, every value kept; drivable classes are chosen in M2.",
    Column("highway", "string", True, "OSM highway value, e.g. trunk, primary, track."),
    Column("ref", "string", False, "Road reference number, e.g. RN1."),
    Column("surface", "string", False, "OSM surface tag."),
    Column("access", "string", False, "OSM access tag."),
    Column("motor_vehicle", "string", False, "OSM motor_vehicle tag."),
    types={LINE},
)

OSM_POIS = _osm(
    "osm_pois",
    "OSM places matching sources.osm_tags.pois: a broad superset, not yet host types (M2).",
    Column("amenity", "string", False, "OSM amenity tag."),
    Column("shop", "string", False, "OSM shop tag."),
    Column("tourism", "string", False, "OSM tourism tag."),
    Column("office", "string", False, "OSM office tag."),
    Column("industrial", "string", False, "OSM industrial tag."),
    Column("landuse", "string", False, "OSM landuse tag."),
    Column("building", "string", False, "OSM building tag."),
    Column("man_made", "string", False, "OSM man_made tag."),
    _SOCKETS,
    _REPAIRED,
    types={POINT, MULTIPOLYGON},
)

OSM_CHARGING_STATIONS = _osm(
    "osm_charging_stations",
    "OSM amenity=charging_station nodes and areas.",
    _SOCKETS,
    Column("capacity", "string", False, "OSM capacity tag, as mapped (text)."),
    Column("access", "string", False, "OSM access tag."),
    _REPAIRED,
    types={POINT, MULTIPOLYGON},
    # Few chargers are mapped in OSM; none at all is possible and stays "none mapped".
    allow_empty=True,
)

OSM_POWER = _osm(
    "osm_power",
    "OSM power=* features: nodes, lines and areas; which values count as grid evidence is "
    "decided in M3 (features.grid_osm_tags).",
    Column("power", "string", True, "OSM power value, e.g. line, substation, tower."),
    Column("voltage", "string", False, "OSM voltage tag, as mapped (text, volts)."),
    Column("substation", "string", False, "OSM substation tag."),
    _REPAIRED,
    types={POINT, LINE, MULTIPOLYGON},
)

OSM_WATER = _osm(
    "osm_water",
    "OSM natural=water areas.",
    Column("water", "string", False, "OSM water tag, e.g. lake, river, reservoir."),
    _REPAIRED,
    types={MULTIPOLYGON},
)

OSM_PROTECTED_AREAS = _osm(
    "osm_protected_areas",
    "OSM boundary=protected_area and boundary=national_park areas (SPEC §2, D-026; WDPA is "
    "not used).",
    Column("boundary", "string", True, "OSM boundary value: protected_area or national_park."),
    Column("protect_class", "string", False, "OSM protect_class tag."),
    _REPAIRED,
    types={MULTIPOLYGON},
)

GRID_TRANSMISSION = LayerSchema(
    name="grid_transmission_lines",
    description="energydata.info Rwanda transmission network (2009): grid-evidence "
    "cross-check only. It says nothing about capacity or connection decisions.",
    id_column="line_id",
    columns=(
        Column("line_id", "string", True, "'tx-' + first 12 hex of SHA-256 of the line's WKB."),
        Column("voltage_kv", "float64", True, "VOLTAGE_KV from the source."),
        Column("status", "string", True, "STATUS from the source: Existing or Planned."),
        Column("from_name", "string", False, "FROM_NM from the source (a place name)."),
        Column("to_name", "string", False, "TO_NM from the source (a place name)."),
        Column("length_km", "float64", True, "Line length in km, computed in EPSG:32735."),
    ),
    geometry_types=frozenset({LINE, MULTILINE}),
    sort_by=("line_id",),
)

CHARGERS_MANUAL = LayerSchema(
    name="chargers_manual",
    description="Public chargers from the hand-filled data/manual/chargers.csv. Names and "
    "operator_public_name are provenance only and are not carried into this layer.",
    id_column="charger_id",
    columns=(
        Column("charger_id", "string", True, "'csv-' + first 12 hex of SHA-256 of 'lat,lon'."),
        Column("source_row", "int64", True, "Line number of the row in chargers.csv."),
        Column("source_url", "string", True, "Public page the row was taken from."),
        Column("date_retrieved", "string", True, "Date the row was checked, YYYY-MM-DD."),
    ),
    geometry_types=frozenset({POINT}),
    sort_by=("charger_id",),
)

CANDIDATES = LayerSchema(
    name="candidates",
    description="Candidate charging sites generated in code from OSM hosts and trunk/primary "
    "road corridors (SPEC §3, Milestone 2). Generic labels only; no business names.",
    id_column="candidate_id",
    columns=(
        Column(
            "candidate_id",
            "string",
            True,
            "'cand-' + first 12 hex of SHA-256 of the host's OSM id, or of 'lat,lon' for a "
            "corridor point without a host.",
        ),
        Column("lat", "float64", True, "Latitude, EPSG:4326."),
        Column("lon", "float64", True, "Longitude, EPSG:4326."),
        Column("host_name", "string", True, "Generic label: host type and district."),
        Column(
            "host_type",
            "string",
            True,
            "fuel, mall, supermarket, logistics, industrial, hotel or none.",
        ),
        Column("host_osm_id", "string", False, "OSM id of the host as type/id; null if none."),
        Column("origin", "string", True, "host, corridor_snapped or corridor."),
        Column("merged_count", "int64", True, "Candidates within 300 m merged into this one."),
        Column("district_id", "string", True, "admin_districts.district_id."),
        Column("district", "string", True, "District name."),
        Column("province_code", "string", True, "ISO 3166-2 province code."),
        Column("province", "string", True, "Province name."),
        Column("nearest_road_class", "string", True, "highway value of the nearest drivable road."),
        Column(
            "dist_road_m",
            "float64",
            True,
            "Distance to the nearest drivable road, metres, EPSG:32735.",
        ),
        Column(
            "profile",
            "string",
            False,
            "urban or corridor; null until the profile rule is decided (pending, D-030).",
        ),
    ),
    geometry_types=frozenset({POINT}),
    sort_by=("candidate_id",),
)

CANDIDATES_ELIGIBLE = replace(
    CANDIDATES,
    name="candidates_eligible",
    description="Every eligible candidate before the candidate budget (SPEC §3 rules, D-029), "
    "with the budget's choice in `selected` (D-031). Kept so the full universe stays auditable.",
    columns=(
        *CANDIDATES.columns,
        Column("selected", "bool", True, "True if the candidate budget kept it (D-031)."),
    ),
)

LAYERS: dict[str, LayerSchema] = {
    schema.name: schema
    for schema in (
        DISTRICTS,
        PROVINCES,
        COUNTRY,
        OSM_ROADS,
        OSM_POIS,
        OSM_CHARGING_STATIONS,
        OSM_POWER,
        OSM_WATER,
        OSM_PROTECTED_AREAS,
        GRID_TRANSMISSION,
        CHARGERS_MANUAL,
        CANDIDATES,
        CANDIDATES_ELIGIBLE,
    )
}


# --- Writing and reading ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerResult:
    name: str
    status: str
    rows: int
    path: Path | None
    bytes: int
    fingerprint: str | None


def layer_path(processed_dir: Path, name: str) -> Path:
    return processed_dir / f"{name}.parquet"


def metadata_path(processed_dir: Path, name: str) -> Path:
    return processed_dir / f"{name}.meta.json"


def validate_layer(frame: gpd.GeoDataFrame, schema: LayerSchema, settings: Settings) -> None:
    """Raise DataValidationError listing every CRS, schema and geometry problem."""
    problems: list[str] = []
    try:
        check_crs(frame.crs, settings.crs.storage, f"Layer {schema.name!r}")
    except CrsError as error:
        problems.append(str(error))
    problems += check_table(frame, schema)
    if "geometry" in frame.columns and not problems:
        labels = frame[schema.id_column] if schema.id_column in frame.columns else None
        problems += check_geometry(
            frame,
            allowed_types=schema.geometry_types,
            bbox=settings.ingest.rwanda_bbox,
            bounds_mode=schema.bounds_mode,
            labels=labels,
        )
    if problems:
        raise DataValidationError(f"Layer {schema.name!r}", problems)


# pandas 3 "str" dtype keeps missing values missing (pandas 2 would write the text "None").
_DTYPES = {"string": "str", "int64": "int64", "float64": "float64", "bool": "bool"}


def empty_frame(schema: LayerSchema, crs: str) -> gpd.GeoDataFrame:
    """A layer with no rows but the schema's columns and types, e.g. no mapped chargers."""
    columns = {column.name: pd.Series([], dtype=_DTYPES[column.kind]) for column in schema.columns}
    return gpd.GeoDataFrame(columns, geometry=gpd.GeoSeries([], crs=crs), crs=crs)


def _normalise(frame: gpd.GeoDataFrame, schema: LayerSchema) -> gpd.GeoDataFrame:
    """Column order, dtypes and row order fixed by the schema, so output is deterministic."""
    ordered = frame[[*schema.column_names, "geometry"]].copy()
    for column in schema.columns:
        ordered[column.name] = ordered[column.name].astype(_DTYPES[column.kind])
    ordered = ordered.sort_values(list(schema.sort_by), kind="mergesort", ignore_index=True)
    return gpd.GeoDataFrame(ordered, geometry="geometry", crs=frame.crs)


def write_layer(
    frame: gpd.GeoDataFrame,
    schema: LayerSchema,
    processed_dir: Path,
    settings: Settings,
    *,
    sources: list[RawManifest],
    stats: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> LayerResult:
    """Validate ``frame`` against ``schema``, then write GeoParquet and its metadata."""
    validate_layer(frame, schema, settings)
    frame = _normalise(frame, schema)
    validate_layer(frame, schema, settings)
    path = layer_path(processed_dir, schema.name)
    write_atomically(
        path,
        lambda temp: frame.to_parquet(
            temp,
            index=False,
            compression="zstd",
            schema_version="1.1.0",
            write_covering_bbox=False,
        ),
    )
    fingerprint = content_fingerprint(frame, schema.column_names)
    bounds = [round(float(value), 7) for value in frame.total_bounds] if len(frame) else None
    metadata = {
        "layer": schema.name,
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "description": schema.description,
        "format": "GeoParquet",
        "file": path.name,
        "crs": settings.crs.storage,
        "row_count": len(frame),
        "id_column": schema.id_column,
        "columns": [
            {
                "name": column.name,
                "type": column.kind,
                "required": column.required,
                "description": column.description,
            }
            for column in schema.columns
        ],
        "geometry_types": geometry_type_counts(frame),
        "bounds": bounds,
        "content_sha256": fingerprint,
        "sources": [source_record(manifest) for manifest in sources],
        "stats": stats or {},
        "notes": notes or [],
    }
    write_json(metadata_path(processed_dir, schema.name), metadata)
    size = path.stat().st_size
    logger.info(
        "Wrote %s: %d rows, %s, %.1f MB",
        schema.name,
        len(frame),
        metadata["geometry_types"],
        size / 1_048_576,
    )
    return LayerResult(schema.name, "ok", len(frame), path, size, fingerprint)


def write_missing(
    schema: LayerSchema, processed_dir: Path, *, reason: str, expected_path: str
) -> LayerResult:
    """Record that a layer's source is missing, and remove any stale output from an old run."""
    remove_layer(processed_dir, schema.name)
    write_json(
        metadata_path(processed_dir, schema.name),
        {
            "layer": schema.name,
            "status": "missing",
            "schema_version": SCHEMA_VERSION,
            "description": schema.description,
            "expected_source": expected_path,
            "reason": reason,
        },
    )
    logger.warning("%s: source missing (%s); no layer written", schema.name, reason)
    return LayerResult(schema.name, "missing", 0, None, 0, None)


def remove_layer(processed_dir: Path, name: str) -> None:
    """Delete a layer's data and metadata files, e.g. after its source failed validation."""
    for path in (layer_path(processed_dir, name), metadata_path(processed_dir, name)):
        if path.exists():
            path.unlink()
            logger.info("Removed stale output %s", path.name)


def read_layer(name: str, processed_dir: Path, settings: Settings) -> gpd.GeoDataFrame:
    """Read a processed layer and check it against its schema and recorded fingerprint."""
    schema = LAYERS[name]
    metadata = read_json(metadata_path(processed_dir, name))
    if metadata.get("status") == "missing":
        raise SourceMissingError(f"Layer {name!r} is unavailable: {metadata.get('reason')}")
    if metadata.get("status") != "ok" or metadata.get("schema_version") != SCHEMA_VERSION:
        raise DataValidationError(
            f"Layer {name!r}", [f"unexpected metadata status/version: {metadata}"]
        )
    path = layer_path(processed_dir, name)
    if not path.is_file():
        raise SourceMissingError(f"{path} does not exist")
    frame = gpd.read_parquet(path)
    validate_layer(frame, schema, settings)
    fingerprint = content_fingerprint(frame, schema.column_names)
    if fingerprint != metadata["content_sha256"] or len(frame) != metadata["row_count"]:
        raise DataValidationError(
            f"Layer {name!r}",
            ["content does not match its metadata; re-run ingestion (it may be stale)"],
        )
    return frame


def string_column(values: list[str | None]) -> np.ndarray:
    """An object array of optional strings, ready for a 'string' schema column."""
    return np.array(values, dtype=object)
