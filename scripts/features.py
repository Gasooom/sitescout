"""Milestone 3: compute candidate features from the processed M1 and M2 layers (SPEC §4).

Reads data/processed/ (never the network). Writes data/processed/features_production.parquet
and data/processed/features_backtest.parquet, each with its metadata (docs/features.md).
Exit code 0 when both are written; 1 when feature engineering stops (nothing is written then).

Usage: uv run python scripts/features.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.features import FeatureError, run_features
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("features")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        results = run_features(config.settings, processed)
    except (FeatureError, IngestError, CrsError, ConfigError) as error:
        log.error("Feature engineering stopped: %s", error)
        return 1
    for result in results:
        log.info("Wrote %s: %d rows (%s)", result.name, result.rows, result.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
