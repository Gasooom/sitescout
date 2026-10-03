"""D-068: compare the selection methods at several coverage radii, against a stronger baseline.

Reads data/processed/ (never the network): the production scores, the population raster and the
known charging sites. Writes data/processed/radius_robustness.json, which scripts/evaluate.py
renders as section F of reports/evaluation.md. An additional analysis: it changes no default
parameter and never touches the shipped network or the export. Run scripts/optimize.py first.
Exit code 0 on success; 1 when a selection stops.

Usage: uv run python scripts/radius_robustness.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging
from sitescout.optimize import NetworkError
from sitescout.radius_robustness import run_radius_robustness


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("radius_robustness")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        run_radius_robustness(config, processed)
    except (NetworkError, IngestError, CrsError, ConfigError) as error:
        log.error("Radius robustness stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
