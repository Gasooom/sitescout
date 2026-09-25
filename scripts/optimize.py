"""Milestone 6: select the 30-site charging network (SPEC §8).

Reads data/processed/ (never the network): the production scores, the population raster and
the known charging sites. Writes data/processed/network.parquet (+ .meta.json), network.json
(the comparison of the exact MCLP, greedy and Top-30 networks, with sensitivity runs) and
network_run.json (solver times). Exit code 0 on success; 1 when selection stops, e.g. with
too few eligible sites or an infeasible model.

Usage: uv run python scripts/optimize.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging
from sitescout.optimize import NetworkError, run_network


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("optimize")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        run_network(config, processed)
    except (NetworkError, IngestError, CrsError, ConfigError) as error:
        log.error("Network selection stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
