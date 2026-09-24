"""The hand-filled public charger list, ``data/manual/chargers.csv`` (SPEC §2).

- A missing file is reported as missing: the layer's metadata says ``status: missing`` and
  no rows are written. Nothing is invented to stand in for it.
- The header must hold exactly the configured columns. Every row is checked; all problems
  are reported together, with their line numbers, and no row is dropped or corrected.
- ``name`` and ``operator_public_name`` stay in the CSV for provenance. They are never
  carried into the processed layer, so no later stage can export or show them.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import math
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import shapely

from sitescout.config import Settings
from sitescout.ingest import DataValidationError
from sitescout.ingest.layers import CHARGERS_MANUAL, LayerResult, write_layer, write_missing
from sitescout.ingest.metadata import RawManifest, sha256_file

logger = logging.getLogger(__name__)

REQUIRED = ("name", "lat", "lon", "source_url", "date_retrieved")
_URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def charger_id(lat: float, lon: float) -> str:
    """Stable id from the coordinates, rounded to 1e-6 degrees (about 0.1 m)."""
    return "csv-" + hashlib.sha256(f"{lat:.6f},{lon:.6f}".encode()).hexdigest()[:12]


def read_charger_csv(path: Path, settings: Settings) -> gpd.GeoDataFrame:
    """Parse and check chargers.csv; raise DataValidationError listing every problem."""
    columns = settings.sources.charger_csv.columns
    bbox = settings.ingest.rwanda_bbox
    what = f"Charger CSV ({path.name})"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise DataValidationError(what, [f"not UTF-8 text: {error}"]) from error
    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if header is None:
        raise DataValidationError(what, ["the file is empty"])
    header = [name.strip() for name in header]
    missing = [name for name in columns if name not in header]
    unexpected = [name for name in header if name not in columns]
    duplicated = sorted({name for name in header if header.count(name) > 1})
    if missing or unexpected or duplicated:
        problems = []
        if missing:
            problems.append(f"missing columns: {missing}")
        if unexpected:
            problems.append(f"unexpected columns: {unexpected}")
        if duplicated:
            problems.append(f"duplicated columns: {duplicated}")
        raise DataValidationError(what, problems)

    problems: list[str] = []
    records = []
    seen: dict[str, int] = {}
    for line_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            problems.append(f"line {line_number}: blank row")
            continue
        if len(row) != len(header):
            problems.append(f"line {line_number}: {len(row)} fields, expected {len(header)}")
            continue
        values = {name: cell.strip() for name, cell in zip(header, row, strict=True)}
        row_problems = []
        for name in REQUIRED:
            if not values[name]:
                row_problems.append(f"{name} is blank")
        lat = _number(values["lat"], "lat", -90, 90, row_problems)
        lon = _number(values["lon"], "lon", -180, 180, row_problems)
        if (
            lat is not None
            and lon is not None
            and not (bbox.min_lat <= lat <= bbox.max_lat and bbox.min_lon <= lon <= bbox.max_lon)
        ):
            row_problems.append(f"({lat}, {lon}) is outside the Rwanda envelope")
        if values["source_url"] and not _URL.match(values["source_url"]):
            row_problems.append(f"source_url {values['source_url']!r} is not an http(s) URL")
        if values["date_retrieved"] and not _valid_date(values["date_retrieved"]):
            row_problems.append(
                f"date_retrieved {values['date_retrieved']!r} is not a YYYY-MM-DD date"
            )
        if row_problems:
            problems.append(f"line {line_number}: " + "; ".join(row_problems))
            continue
        assert lat is not None and lon is not None
        identifier = charger_id(lat, lon)
        if identifier in seen:
            problems.append(
                f"line {line_number}: same coordinates as line {seen[identifier]} (duplicate)"
            )
            continue
        seen[identifier] = line_number
        records.append(
            {
                "charger_id": identifier,
                "source_row": line_number,
                "source_url": values["source_url"],
                "date_retrieved": values["date_retrieved"],
                "geometry": shapely.Point(lon, lat),
            }
        )
    if not records and not problems:
        problems.append("the file has a header but no charger rows")
    if problems:
        raise DataValidationError(what, problems)
    return gpd.GeoDataFrame(records, geometry="geometry", crs=settings.crs.storage)


def _number(text: str, name: str, low: float, high: float, problems: list[str]) -> float | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        problems.append(f"{name} {text!r} is not a number")
        return None
    if not math.isfinite(value) or not low <= value <= high:
        problems.append(f"{name} {text!r} is out of range")
        return None
    return value


def _valid_date(text: str) -> bool:
    if not _DATE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def process_chargers(settings: Settings, csv_path: Path, processed_dir: Path) -> LayerResult:
    """Validate chargers.csv into the chargers_manual layer, or record it as missing."""
    configured = settings.paths.manual_chargers_csv
    if not csv_path.is_file():
        return write_missing(
            CHARGERS_MANUAL,
            processed_dir,
            reason=f"{configured} does not exist; it is filled by hand from public charger "
            "maps (docs/data_sources.md). No charger records were created.",
            expected_path=configured,
        )
    chargers = read_charger_csv(csv_path, settings)
    digest = sha256_file(csv_path)
    manifest = RawManifest(
        source_id="chargers_manual",
        url=configured,
        resolved_url=configured,
        filename=csv_path.name,
        bytes=csv_path.stat().st_size,
        sha256=digest,
        retrieved_at="per row (date_retrieved)",
        http_last_modified=None,
        http_etag=None,
        versioning="rolling",
        licence="per row (source_url)",
        licence_url="per row (source_url)",
        credit="per row (source_url)",
    )
    return write_layer(
        chargers,
        CHARGERS_MANUAL,
        processed_dir,
        settings,
        sources=[manifest],
        stats={"charger_count": len(chargers)},
        notes=[
            "name and operator_public_name are provenance only and not in this layer.",
            "Not yet combined with OSM chargers: sources.charger_match_radius_m is pending.",
        ],
    )
