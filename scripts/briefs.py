"""Milestone 7: write a Site Evidence Brief for every network site (SPEC §9).

Reads data/processed/ (never the network): the network, scores, features and candidates.
Writes data/processed/evidence.json (the structured evidence), reports/briefs/<candidate>.md
(one brief per selected site), reports/briefs/README.md (the index) and
data/processed/grounding.json (the grounding check). Exit code 0 when every number in every
brief is grounded; 1 otherwise, or when the inputs are missing.

Usage: uv run python scripts/briefs.py
"""

import argparse
import logging
import sys

from sitescout.briefs import BriefError, run_briefs
from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.evidence import EvidenceError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("briefs")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        run_briefs(config, processed)
    except (BriefError, EvidenceError, IngestError, CrsError, ConfigError) as error:
        log.error("Brief generation stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
