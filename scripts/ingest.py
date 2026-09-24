"""Milestone 1 ingestion: fetch public sources, process them, validate the outputs.

Stages:
  fetch     download raw files into data/raw/ (skips files already downloaded)
  process   build data/processed/ from data/raw/ only (no network)
  validate  re-read every processed output through its schema and fingerprint
  all       fetch, process, then validate

Exit code 0 when every requested source is ok or reported missing (the hand-filled charger
CSV may not exist yet); 1 when any source failed.

Usage: uv run python scripts/ingest.py all [--source osm --source boundaries] [--refresh]
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.ingest.pipeline import (
    SOURCES,
    fetch_sources,
    log_reports,
    process_sources,
    validate_outputs,
)
from sitescout.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("stage", choices=["fetch", "process", "validate", "all"])
    parser.add_argument(
        "--source",
        action="append",
        choices=SOURCES,
        help="limit to one source; repeat for several (default: all sources)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download again even if a file is present (fixed releases must not change)",
    )
    args = parser.parse_args()
    configure_logging()
    try:
        config = load_config()
    except ConfigError as error:
        logging.getLogger("ingest").error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    sources = tuple(source for source in SOURCES if source in (args.source or SOURCES))

    reports = []
    if args.stage in ("fetch", "all"):
        fetched = fetch_sources(config, sources, refresh=args.refresh)
        log_reports("fetch", fetched)
        reports += fetched
        failed = {report.source for report in fetched if report.status == "failed"}
        sources = tuple(source for source in sources if source not in failed)
    if args.stage in ("process", "all"):
        processed = process_sources(config, sources)
        log_reports("process", processed)
        reports += processed
        failed = {report.source for report in processed if report.status == "failed"}
        sources = tuple(source for source in sources if source not in failed)
    if args.stage in ("validate", "all"):
        validated = validate_outputs(config, sources)
        log_reports("validate", validated)
        reports += validated
    return 1 if any(report.status == "failed" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
