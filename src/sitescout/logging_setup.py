"""Logging for entry points. Library modules only call ``logging.getLogger(__name__)``."""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Send log records at ``level`` and above to stderr, replacing any earlier setup."""
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stderr, force=True)
