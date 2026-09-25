"""Milestone 8: write the export the one-page demo reads (SPEC §10).

Reads data/processed/ (never the network): the candidates, scores, network, evidence,
evaluation and grounding outputs. Writes data/export/sitescout.json and
data/export/sitescout.js (the same data for app/index.html opened from disk), after
validating them. Exit code 0 on success; 1 when an input is missing or a check fails.

Usage: uv run python scripts/export.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.export import ExportError, run_export
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("export")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        run_export(config, processed)
    except (ExportError, IngestError, CrsError, ConfigError) as error:
        log.error("Export stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
