"""Milestone 4: score the candidates and rate confidence (SPEC §5, §6).

Reads data/processed/features_production and features_backtest (never the network). Writes
data/processed/scores_production.parquet and scores_backtest.parquet, each with its metadata
(docs/scoring.md). Exit code 0 when both are written; 1 when scoring stops (nothing is
written then).

Usage: uv run python scripts/score.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging
from sitescout.scoring import ScoreError, run_scores


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("score")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        results = run_scores(config.settings, config.weights, processed)
    except (ScoreError, IngestError, CrsError, ConfigError) as error:
        log.error("Scoring stopped: %s", error)
        return 1
    for result in results:
        log.info("Wrote %s: %d rows (%s)", result.name, result.rows, result.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
