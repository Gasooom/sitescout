"""Milestone 5: the retrospective plausibility test (SPEC §7).

Reads data/processed/ (never the network): the candidates, features and scores. Writes
data/processed/evaluation.json and reports/evaluation.md (backtest against known charging
sites with random and population-only baselines, weight stability, data quality). Exit code
0 when both are written; 1 when the evaluation stops.

Usage: uv run python scripts/evaluate.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.evaluation import EvaluationError, run_evaluation
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("evaluate")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        run_evaluation(config, processed)
    except (EvaluationError, IngestError, CrsError, ConfigError) as error:
        log.error("Evaluation stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
