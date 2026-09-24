"""geoBoundaries districts (ADM2, the master) and the provinces and country derived from them.

- ADM2 districts are validated as delivered: expected count, unique ids and names, valid
  polygonal geometry, EPSG:4326, inside the Rwanda envelope. Nothing is repaired.
- geoBoundaries ADM2 carries no province code, so each district is assigned to the ADM1
  province it overlaps most, measured in EPSG:32735. The assignment must be a strict
  majority of the district's area; otherwise ingestion stops (D-021).
- ADM1 geometry is never taken from the ADM1 file: provinces and the country are dissolved
  from the ADM2 districts, so every edge matches (CLAUDE.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from sitescout.config import Settings
from sitescout.crs import CrsError, area_km2, check_crs, to_metric, to_storage
from sitescout.ingest import DataValidationError
from sitescout.ingest.acquire import load_raw, raw_sources
from sitescout.ingest.geometry import check_geometry, to_multipolygon
from sitescout.ingest.layers import COUNTRY, DISTRICTS, PROVINCES, LayerResult, write_layer

logger = logging.getLogger(__name__)

SOURCE_FIELDS = ("shapeName", "shapeID", "shapeGroup", "shapeType")
POLYGONAL = frozenset({"Polygon", "MultiPolygon"})


def read_boundary_file(path: Path, level: str, settings: Settings) -> gpd.GeoDataFrame:
    """Read one geoBoundaries file and check it before anything is derived from it."""
    try:
        frame = gpd.read_file(path)
    except Exception as error:  # pyogrio raises several error types for unreadable files
        raise DataValidationError(f"geoBoundaries {level} ({path.name})", [str(error)]) from error
    boundaries = settings.sources.boundaries
    expected = getattr(boundaries.expected_units, level)
    problems: list[str] = []
    try:
        check_crs(frame.crs, settings.crs.storage, f"geoBoundaries {level}")
    except CrsError as error:
        problems.append(str(error))
    missing = [name for name in SOURCE_FIELDS if name not in frame.columns]
    if level == "ADM1" and "shapeISO" not in frame.columns:
        missing.append("shapeISO")
    if missing:
        problems.append(f"missing source fields: {missing}")
    if len(frame) != expected:
        problems.append(f"{len(frame)} units, expected {expected} (sources.boundaries)")
    if not missing:
        for field in ("shapeID", "shapeName", *(("shapeISO",) if level == "ADM1" else ())):
            values = frame[field]
            blank = values.isna() | (values.astype(str).str.strip() == "")
            if blank.any():
                problems.append(f"{blank.sum()} blank {field} value(s)")
            dupes = sorted(values[values.duplicated(keep=False)].astype(str).unique())
            if dupes:
                problems.append(f"duplicate {field} values: {dupes}")
        groups = sorted(frame["shapeGroup"].astype(str).unique())
        if groups != [boundaries.iso3]:
            problems.append(f"shapeGroup values {groups}, expected only {boundaries.iso3!r}")
        types = sorted(frame["shapeType"].astype(str).unique())
        if types != [level]:
            problems.append(f"shapeType values {types}, expected only {level!r}")
    if not problems:
        problems += check_geometry(
            frame,
            allowed_types=POLYGONAL,
            bbox=settings.ingest.rwanda_bbox,
            labels=frame["shapeName"],
        )
    if problems:
        raise DataValidationError(f"geoBoundaries {level} ({path.name})", problems)
    return to_storage(frame, settings.crs)


def assign_provinces(
    districts: gpd.GeoDataFrame, provinces: gpd.GeoDataFrame, settings: Settings
) -> pd.DataFrame:
    """For each district: the province it overlaps most, and the share of its area inside it.

    Areas are measured in the metric CRS. A district whose largest overlap is not a strict
    majority of its area, or a province that receives no district, stops ingestion.
    """
    metric_districts = to_metric(districts.geometry, settings.crs).array
    metric_provinces = to_metric(provinces.geometry, settings.crs).array
    rows = []
    problems = []
    for index, district in enumerate(metric_districts):
        overlaps = shapely.area(shapely.intersection(district, metric_provinces))
        best = int(np.argmax(overlaps))
        share = float(overlaps[best] / shapely.area(district))
        name = districts["shapeName"].iloc[index]
        if share <= 0.5:
            problems.append(f"district {name!r}: largest province overlap is {share:.1%}")
        rows.append(
            {
                "shapeID": districts["shapeID"].iloc[index],
                "province_code": provinces["shapeISO"].iloc[best],
                "province_name": provinces["shapeName"].iloc[best],
                "province_overlap_share": share,
            }
        )
    assigned = pd.DataFrame(rows)
    empty = sorted(set(provinces["shapeISO"]) - set(assigned["province_code"]))
    if empty:
        problems.append(f"provinces with no district: {empty}")
    if problems:
        raise DataValidationError("Province assignment", problems)
    return assigned


def _dissolve(geoms: list[shapely.Geometry]) -> shapely.MultiPolygon:
    merged = shapely.union_all(np.array(geoms, dtype=object))
    return to_multipolygon(np.array([merged], dtype=object))[0]


def process_boundaries(settings: Settings, raw_dir: Path, processed_dir: Path) -> list[LayerResult]:
    """Build admin_districts (ADM2), admin_provinces and admin_country from geoBoundaries."""
    sources = raw_sources(settings)
    adm2_path, adm2_manifest = load_raw(sources["boundaries_adm2"], raw_dir)
    adm1_path, adm1_manifest = load_raw(sources["boundaries_adm1"], raw_dir)
    adm2 = read_boundary_file(adm2_path, "ADM2", settings)
    adm1 = read_boundary_file(adm1_path, "ADM1", settings)
    adm2 = adm2.sort_values("shapeID", kind="mergesort", ignore_index=True)
    adm1 = adm1.sort_values("shapeISO", kind="mergesort", ignore_index=True)

    assigned = assign_provinces(adm2, adm1, settings)
    lowest = assigned.nsmallest(3, "province_overlap_share")
    logger.info(
        "Province assignment: all %d districts assigned; lowest overlap shares %s",
        len(assigned),
        ", ".join(
            f"{sid} {share:.4f}"
            for sid, share in zip(lowest["shapeID"], lowest["province_overlap_share"], strict=True)
        ),
    )

    districts = gpd.GeoDataFrame(
        {
            "district_id": adm2["shapeID"].astype(str),
            "district_name": adm2["shapeName"].astype(str),
            "province_code": assigned["province_code"].astype(str),
            "province_name": assigned["province_name"].astype(str),
            "province_overlap_share": assigned["province_overlap_share"].astype(float),
        },
        geometry=to_multipolygon(adm2.geometry.array),
        crs=settings.crs.storage,
    )
    districts["area_km2"] = area_km2(districts, settings.crs)

    province_rows = []
    for (code, name), group in districts.groupby(["province_code", "province_name"], sort=True):
        province_rows.append(
            {
                "province_code": code,
                "province_name": name,
                "district_count": len(group),
                "geometry": _dissolve(group.sort_values("district_id").geometry.tolist()),
            }
        )
    provinces = gpd.GeoDataFrame(province_rows, geometry="geometry", crs=settings.crs.storage)
    provinces["area_km2"] = area_km2(provinces, settings.crs)

    country_geometry = _dissolve(districts.sort_values("district_id").geometry.tolist())
    country = gpd.GeoDataFrame(
        {
            "country_iso3": [settings.sources.boundaries.iso3],
            "district_count": [len(districts)],
            "province_count": [len(provinces)],
        },
        geometry=[country_geometry],
        crs=settings.crs.storage,
    )
    country["area_km2"] = area_km2(country, settings.crs)
    holes = sum(len(polygon.interiors) for polygon in country_geometry.geoms)
    parts = len(country_geometry.geoms)
    notes = []
    if holes or parts > 1:
        notes.append(
            f"The dissolved country has {parts} part(s) and {holes} interior ring(s): gaps or "
            "overlaps between source districts, kept as delivered."
        )
        logger.warning(notes[-1])

    manifests = [adm2_manifest, adm1_manifest]
    share_stats = {
        "min_province_overlap_share": round(float(districts["province_overlap_share"].min()), 6)
    }
    return [
        write_layer(
            districts,
            DISTRICTS,
            processed_dir,
            settings,
            sources=manifests,
            stats={**share_stats, "district_count": len(districts)},
        ),
        write_layer(
            provinces,
            PROVINCES,
            processed_dir,
            settings,
            sources=manifests,
            stats={"province_count": len(provinces)},
            notes=["Geometry dissolved from admin_districts; ADM1 file used for names only."],
        ),
        write_layer(
            country,
            COUNTRY,
            processed_dir,
            settings,
            sources=manifests,
            stats={"parts": parts, "interior_rings": holes},
            notes=["Geometry dissolved from admin_districts.", *notes],
        ),
    ]
