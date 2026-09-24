"""Milestone 2: generate candidate charging sites from the processed M1 layers (SPEC §3).

Reads data/processed/ (never the network). Writes data/processed/candidates_eligible.parquet
(every eligible candidate, with a `selected` flag) and data/processed/candidates.parquet (the
candidate budget's selection, D-031), each with its metadata. Exit code 0 when both are
written; 1 when generation stops, for example because fewer candidates are eligible than the
budget (nothing is written then).

Usage: uv run python scripts/candidates.py
"""

import argparse
import logging
import sys

from sitescout.candidates import CandidateError, run_candidates
from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("candidates")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        results = run_candidates(config.settings, processed)
    except (CandidateError, IngestError, CrsError) as error:
        log.error("Candidate generation stopped: %s", error)
        return 1
    for result in results:
        log.info("Wrote %s: %d rows (%s)", result.name, result.rows, result.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
