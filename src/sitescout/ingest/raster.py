"""WorldPop population raster: validate it and keep it as a raster.

The processed raster is a byte-for-byte copy of the validated source GeoTIFF, so its CRS,
grid, resolution, nodata value and every pixel are exactly as WorldPop published them. It
is never converted to points or polygons. Its metadata records the grid, nodata handling,
pixel statistics, source and known limitations.
"""

from __future__ import annotations

import logging
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import RasterioError

from sitescout.config import Settings
from sitescout.crs import CrsError, check_crs
from sitescout.ingest import DataValidationError, SourceMissingError
from sitescout.ingest.acquire import load_raw, raw_sources
from sitescout.ingest.layers import LayerResult, metadata_path
from sitescout.ingest.metadata import (
    read_json,
    sha256_file,
    source_record,
    write_atomically,
    write_json,
)

logger = logging.getLogger(__name__)

POPULATION_LAYER = "population_worldpop"
PIXEL_SIZE_TOLERANCE_ARCSEC = 1e-6  # float rounding of 3/3600 in the GeoTIFF header

LIMITATIONS = (
    "A modelled estimate (random-forest dasymetric redistribution), not a census count.",
    "Constrained: people are placed only in cells WorldPop classifies as built settlement; "
    "every other cell is nodata, so the absence of a value is not evidence of zero people.",
    "R2025A is an alpha release that WorldPop states may change over the coming year.",
    "3 arc-seconds is about 92.7 m east-west and 92.2 m north-south at Rwanda's latitude, "
    "not exactly 100 m; areas and distances must be computed in EPSG:32735.",
)


@dataclass(frozen=True, slots=True)
class RasterInfo:
    driver: str
    crs: str
    band_count: int
    dtype: str
    width: int
    height: int
    pixel_size_deg: tuple[float, float]
    pixel_size_arcsec: tuple[float, float]
    transform: tuple[float, float, float, float, float, float]
    bounds: tuple[float, float, float, float]
    nodata: float
    valid_pixels: int
    nodata_pixels: int
    zero_pixels: int
    population_sum: float
    population_max: float


def population_path(processed_dir: Path) -> Path:
    return processed_dir / f"{POPULATION_LAYER}.tif"


def inspect_population_raster(path: Path, settings: Settings) -> RasterInfo:
    """Read and check the population GeoTIFF; raise DataValidationError listing problems."""
    what = f"WorldPop raster ({path.name})"
    try:
        with rasterio.open(path) as dataset:
            profile = dataset.profile
            crs = dataset.crs
            transform = dataset.transform
            bounds = tuple(float(value) for value in dataset.bounds)
            band = dataset.read(1, masked=True) if dataset.count >= 1 else None
    except RasterioError as error:
        raise DataValidationError(what, [f"cannot be read as a raster: {error}"]) from error

    problems: list[str] = []
    expected_arcsec = settings.sources.population.pixel_size_arcsec
    try:
        check_crs(crs, settings.crs.storage, what)
    except CrsError as error:
        problems.append(str(error))
    if profile["driver"] != "GTiff":
        problems.append(f"driver {profile['driver']}, expected GTiff")
    if profile["count"] != 1:
        problems.append(f"{profile['count']} bands, expected 1")
    if not np.issubdtype(np.dtype(profile["dtype"]), np.floating):
        problems.append(f"data type {profile['dtype']}, expected floating point")
    if profile["width"] <= 0 or profile["height"] <= 0:
        problems.append(f"empty grid {profile['width']}x{profile['height']}")
    if transform.b != 0 or transform.d != 0:
        problems.append("the grid is rotated; expected a north-up grid")
    if transform.e >= 0:
        problems.append("rows do not run north to south")
    size_x, size_y = abs(transform.a), abs(transform.e)
    arcsec = (size_x * 3600, size_y * 3600)
    if any(abs(value - expected_arcsec) > PIXEL_SIZE_TOLERANCE_ARCSEC for value in arcsec):
        problems.append(f"pixel size {arcsec} arc-seconds, expected {expected_arcsec}")
    nodata = profile.get("nodata")
    if nodata is None:
        problems.append("no nodata value is declared")
    bbox = settings.ingest.rwanda_bbox
    if not (
        bounds[0] >= bbox.min_lon
        and bounds[1] >= bbox.min_lat
        and bounds[2] <= bbox.max_lon
        and bounds[3] <= bbox.max_lat
    ):
        problems.append(f"bounds {bounds} are not inside the Rwanda envelope {bbox.as_tuple()}")

    valid = nodata_count = zeros = 0
    total = maximum = 0.0
    if band is not None:
        values = band.compressed().astype(np.float64)
        valid = int(values.size)
        nodata_count = int(band.size - valid)
        if valid == 0:
            problems.append("every pixel is nodata: the raster is empty")
        else:
            if not np.isfinite(values).all():
                problems.append(f"{(~np.isfinite(values)).sum()} valid pixels are not finite")
            negative = int((values < 0).sum())
            if negative:
                problems.append(f"{negative} pixels have negative population")
            zeros = int((values == 0).sum())
            total = float(values.sum(dtype=np.float64))
            maximum = float(values.max())
            if not total > 0:
                problems.append("total population is not positive")
    if problems:
        raise DataValidationError(what, problems)
    return RasterInfo(
        driver=profile["driver"],
        crs=settings.crs.storage,
        band_count=int(profile["count"]),
        dtype=str(profile["dtype"]),
        width=int(profile["width"]),
        height=int(profile["height"]),
        pixel_size_deg=(size_x, size_y),
        pixel_size_arcsec=(round(arcsec[0], 6), round(arcsec[1], 6)),
        transform=tuple(float(value) for value in transform[:6]),  # type: ignore[arg-type]
        bounds=bounds,  # type: ignore[arg-type]
        nodata=float(nodata),  # type: ignore[arg-type]
        valid_pixels=valid,
        nodata_pixels=nodata_count,
        zero_pixels=zeros,
        population_sum=total,
        population_max=maximum,
    )


def process_population(settings: Settings, raw_dir: Path, processed_dir: Path) -> LayerResult:
    """Validate the WorldPop GeoTIFF and copy it unchanged to data/processed/."""
    source_path, manifest = load_raw(raw_sources(settings)["worldpop"], raw_dir)
    info = inspect_population_raster(source_path, settings)
    target = population_path(processed_dir)
    write_atomically(target, lambda temp: shutil.copyfile(source_path, temp))
    digest = sha256_file(target)
    if digest != manifest.sha256:
        raise DataValidationError(POPULATION_LAYER, ["the processed copy differs from the source"])
    population = settings.sources.population
    metadata: dict[str, Any] = {
        "layer": POPULATION_LAYER,
        "status": "ok",
        "schema_version": 1,
        "description": (
            f"{population.provider} {population.year} population, {population.resolution_m} m "
            f"(3 arc-second) grid, {'constrained' if population.constrained else 'unconstrained'}"
            f", release {population.release}. Unit: people per pixel."
        ),
        "format": "GeoTIFF",
        "file": target.name,
        "content_sha256": digest,
        "raster": _json_ready(asdict(info)),
        "sources": [source_record(manifest)],
        "limitations": list(LIMITATIONS),
    }
    write_json(metadata_path(processed_dir, POPULATION_LAYER), metadata)
    logger.info(
        "Wrote %s: %dx%d pixels, %s arc-seconds, nodata %s, %d valid pixels, population sum %.0f",
        POPULATION_LAYER,
        info.width,
        info.height,
        info.pixel_size_arcsec,
        info.nodata,
        info.valid_pixels,
        info.population_sum,
    )
    return LayerResult(
        POPULATION_LAYER, "ok", info.valid_pixels, target, target.stat().st_size, digest
    )


def read_population(processed_dir: Path, settings: Settings) -> tuple[Path, RasterInfo]:
    """The processed raster's path, after re-checking it against its metadata."""
    metadata = read_json(metadata_path(processed_dir, POPULATION_LAYER))
    path = population_path(processed_dir)
    if not path.is_file():
        raise SourceMissingError(f"{path} does not exist")
    if sha256_file(path) != metadata["content_sha256"]:
        raise DataValidationError(POPULATION_LAYER, ["file does not match its metadata"])
    return path, inspect_population_raster(path, settings)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value
