"""Validate config/settings.yaml and config/weights.yaml, then log a summary.

The summary lists every parameter that is still pending because docs/SPEC.md does not
define it. Exit code 0 means both files are valid; 1 means they are not.

Usage: uv run python scripts/check_config.py
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config, log_summary
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    try:
        config = load_config()
    except ConfigError as error:
        logging.getLogger("check_config").error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    log_summary(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
