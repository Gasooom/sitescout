"""Run the Milestone 1 stages for each source: fetch, process and validate.

- **fetch** downloads raw files (network); already-downloaded files are reused.
- **process** builds processed layers from ``data/raw`` only (no network), so the same raw
  files always give the same outputs.
- **validate** re-reads every processed output through the same checks later stages use.

A source that fails stops only that source: its stale outputs from earlier runs are removed,
the exact error is reported and the other sources continue. Nothing is substituted.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sitescout.config import Config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError, SourceMissingError
from sitescout.ingest.acquire import fetch, raw_sources
from sitescout.ingest.boundaries import process_boundaries
from sitescout.ingest.chargers import process_chargers
from sitescout.ingest.grid import process_grid
from sitescout.ingest.layers import LayerResult, layer_path, metadata_path, read_layer, remove_layer
from sitescout.ingest.metadata import read_json
from sitescout.ingest.osm import process_osm
from sitescout.ingest.raster import (
    POPULATION_LAYER,
    population_path,
    process_population,
    read_population,
)

logger = logging.getLogger(__name__)

SOURCES = ("boundaries", "population", "grid", "osm", "chargers")

RAW_IDS = {
    "boundaries": ("boundaries_adm2", "boundaries_adm1"),
    "population": ("worldpop",),
    "grid": ("grid_transmission",),
    "osm": ("osm",),
    "chargers": (),  # filled by hand; nothing to download
}

OUTPUTS = {
    "boundaries": ("admin_districts", "admin_provinces", "admin_country"),
    "population": (POPULATION_LAYER,),
    "grid": ("grid_transmission_lines",),
    "osm": (
        "osm_roads",
        "osm_pois",
        "osm_charging_stations",
        "osm_power",
        "osm_water",
        "osm_protected_areas",
    ),
    "chargers": ("chargers_manual",),
}


@dataclass
class SourceReport:
    source: str
    status: str  # ok, missing or failed
    seconds: float
    layers: list[LayerResult] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Dirs:
    """Where a run reads and writes; normally the configured data/ paths."""

    raw: Path
    processed: Path
    chargers_csv: Path

    @classmethod
    def from_config(cls, config: Config) -> Dirs:
        paths = config.settings.paths
        return cls(
            raw=config.resolve(paths.raw_dir),
            processed=config.resolve(paths.processed_dir),
            chargers_csv=config.resolve(paths.manual_chargers_csv),
        )


def _run(source: str, action: Callable[[], tuple[str, list[LayerResult]]]) -> SourceReport:
    start = time.perf_counter()
    try:
        status, layers = action()
    except (IngestError, CrsError) as error:
        seconds = time.perf_counter() - start
        logger.error("%s: FAILED after %.1f s\n%s", source, seconds, error)
        return SourceReport(source, "failed", seconds, error=str(error))
    return SourceReport(source, status, time.perf_counter() - start, layers)


def fetch_sources(
    config: Config, sources: tuple[str, ...], *, refresh: bool = False, dirs: Dirs | None = None
) -> list[SourceReport]:
    dirs = dirs or Dirs.from_config(config)
    available = raw_sources(config.settings)
    reports = []
    for source in sources:
        if not RAW_IDS[source]:
            logger.info("%s: nothing to download (filled by hand)", source)
            continue

        def action(source: str = source) -> tuple[str, list[LayerResult]]:
            for raw_id in RAW_IDS[source]:
                fetch(available[raw_id], dirs.raw, refresh=refresh)
            return "ok", []

        reports.append(_run(source, action))
    return reports


def process_sources(
    config: Config, sources: tuple[str, ...], *, dirs: Dirs | None = None
) -> list[SourceReport]:
    dirs = dirs or Dirs.from_config(config)
    settings = config.settings
    steps = {
        "boundaries": lambda: process_boundaries(settings, dirs.raw, dirs.processed),
        "population": lambda: [process_population(settings, dirs.raw, dirs.processed)],
        "grid": lambda: [process_grid(settings, dirs.raw, dirs.processed)],
        "osm": lambda: process_osm(settings, dirs.raw, dirs.processed),
        "chargers": lambda: [process_chargers(settings, dirs.chargers_csv, dirs.processed)],
    }
    reports = []
    for source in sources:
        logger.info("%s: processing", source)

        def action(source: str = source) -> tuple[str, list[LayerResult]]:
            layers = steps[source]()
            missing = any(layer.status == "missing" for layer in layers)
            return ("missing" if missing else "ok"), layers

        report = _run(source, action)
        if report.status == "failed":
            for name in OUTPUTS[source]:
                _remove_output(dirs.processed, name)
        reports.append(report)
    return reports


def _remove_output(processed_dir: Path, name: str) -> None:
    if name == POPULATION_LAYER:
        for path in (population_path(processed_dir), metadata_path(processed_dir, name)):
            path.unlink(missing_ok=True)
    else:
        remove_layer(processed_dir, name)


def validate_outputs(
    config: Config, sources: tuple[str, ...], *, dirs: Dirs | None = None
) -> list[SourceReport]:
    """Re-read every processed output of ``sources`` through its schema and fingerprint."""
    dirs = dirs or Dirs.from_config(config)
    reports = []
    for source in sources:

        def action(source: str = source) -> tuple[str, list[LayerResult]]:
            layers = []
            status = "ok"
            for name in OUTPUTS[source]:
                if name == POPULATION_LAYER:
                    path, info = read_population(dirs.processed, config.settings)
                    layers.append(
                        LayerResult(name, "ok", info.valid_pixels, path, path.stat().st_size, None)
                    )
                    continue
                try:
                    frame = read_layer(name, dirs.processed, config.settings)
                except SourceMissingError as error:
                    metadata = read_json(metadata_path(dirs.processed, name))
                    if metadata.get("status") != "missing":
                        raise
                    logger.warning("%s: %s", name, error)
                    layers.append(LayerResult(name, "missing", 0, None, 0, None))
                    status = "missing"
                    continue
                path = layer_path(dirs.processed, name)
                layers.append(LayerResult(name, "ok", len(frame), path, path.stat().st_size, None))
                logger.info("%s: valid, %d rows", name, len(frame))
            return status, layers

        reports.append(_run(source, action))
    return reports


def log_reports(stage: str, reports: list[SourceReport]) -> None:
    logger.info("=== %s summary ===", stage)
    for report in reports:
        logger.log(
            logging.ERROR if report.status == "failed" else logging.INFO,
            "%-11s %-8s %6.1f s",
            report.source,
            report.status.upper(),
            report.seconds,
        )
        for layer in report.layers:
            logger.info(
                "    %-24s %-8s rows=%-8d %.2f MB",
                layer.name,
                layer.status,
                layer.rows,
                layer.bytes / 1_048_576,
            )
        if report.error:
            logger.error("    %s", report.error.splitlines()[0])
