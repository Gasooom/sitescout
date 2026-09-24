"""energydata.info Rwanda transmission network (2009): a cross-check for OSM grid evidence.

This layer is grid *evidence* only. It shows where lines were mapped around 2009; it says
nothing about capacity, transformer headroom or whether a site can connect. The source's
SOURCES column (which names utilities) and PROJECT_NM are not carried over.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import geopandas as gpd
import shapely

from sitescout.config import Settings
from sitescout.crs import CrsError, check_crs, length_km, to_storage
from sitescout.ingest import DataValidationError
from sitescout.ingest.acquire import load_raw, raw_sources
from sitescout.ingest.geometry import check_geometry
from sitescout.ingest.layers import GRID_TRANSMISSION, LayerResult, write_layer

logger = logging.getLogger(__name__)

SOURCE_FIELDS = ("COUNTRY", "VOLTAGE_KV", "FROM_NM", "TO_NM", "STATUS")
LINEAR = frozenset({"LineString", "MultiLineString"})


def line_id(geometry: shapely.Geometry) -> str:
    wkb = shapely.to_wkb(geometry, output_dimension=2, byte_order=1, flavor="iso")
    return "tx-" + hashlib.sha256(wkb).hexdigest()[:12]


def process_grid(settings: Settings, raw_dir: Path, processed_dir: Path) -> LayerResult:
    path, manifest = load_raw(raw_sources(settings)["grid_transmission"], raw_dir)
    what = f"energydata.info transmission network ({path.name})"
    try:
        raw = gpd.read_file(path)
    except Exception as error:  # pyogrio raises several error types for unreadable files
        raise DataValidationError(what, [str(error)]) from error

    problems: list[str] = []
    try:
        check_crs(raw.crs, settings.crs.storage, what)
    except CrsError as error:
        problems.append(str(error))
    missing = [name for name in SOURCE_FIELDS if name not in raw.columns]
    if missing:
        problems.append(f"missing source fields: {missing}")
    if raw.empty:
        problems.append("the source has no features")
    if not missing and not raw.empty:
        countries = sorted(raw["COUNTRY"].astype(str).unique())
        if countries != [settings.sources.boundaries.iso3]:
            problems.append(f"COUNTRY values {countries}, expected only 'RWA'")
        if raw["VOLTAGE_KV"].isna().any() or raw["STATUS"].isna().any():
            problems.append("VOLTAGE_KV or STATUS has null values")
        problems += check_geometry(
            raw,
            allowed_types=LINEAR,
            bbox=settings.ingest.rwanda_bbox,
            labels=raw.index.to_series().map(lambda i: f"row {i}"),
        )
    if problems:
        raise DataValidationError(what, problems)

    raw = to_storage(raw, settings.crs)
    lines = gpd.GeoDataFrame(
        {
            "line_id": [line_id(geom) for geom in raw.geometry],
            "voltage_kv": raw["VOLTAGE_KV"].astype(float),
            "status": raw["STATUS"].astype(str),
            "from_name": raw["FROM_NM"].astype(object).where(raw["FROM_NM"].notna(), None),
            "to_name": raw["TO_NM"].astype(object).where(raw["TO_NM"].notna(), None),
        },
        geometry=raw.geometry.array,
        crs=settings.crs.storage,
    )
    lines["length_km"] = length_km(lines, settings.crs)
    status_counts = {str(k): int(v) for k, v in sorted(lines["status"].value_counts().items())}
    voltage_counts = {
        f"{k:g}": int(v) for k, v in sorted(lines["voltage_kv"].value_counts().items())
    }
    return write_layer(
        lines,
        GRID_TRANSMISSION,
        processed_dir,
        settings,
        sources=[manifest],
        stats={
            "status_counts": status_counts,
            "voltage_kv_counts": voltage_counts,
            "total_length_km": round(float(lines["length_km"].sum()), 3),
            "data_year": settings.sources.grid_cross_check.data_year,
        },
        notes=[
            "Cross-check only (SPEC §2): OSM power=* is the primary grid-evidence layer.",
            "Mapped around 2009; includes lines marked Planned at the time.",
            "Grid evidence only: no capacity, transformer or connection information.",
        ],
    )
