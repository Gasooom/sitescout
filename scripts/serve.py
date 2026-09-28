"""Milestone 11 (D-062): serve the decision page locally, with the optional investigation layer.

  uv run python scripts/serve.py
  uv run --env-file .env python scripts/serve.py     # with the provider key from a local .env

Serves app/index.html, the committed export and the reports on http://127.0.0.1:<server.port>/
(config/settings.yaml). The page is complete without a provider: when the configured
provider's key (OPENAI_API_KEY or ANTHROPIC_API_KEY, D-050), the optional extra
(`uv sync --extra analyst`) or data/processed/ is missing, investigation is reported as
unavailable and nothing else changes. SiteScout itself never reads .env; `uv --env-file` puts
the key into this process's environment. An investigation makes real, billed API calls, one
at a time, and nothing it returns is written anywhere. Stop with Ctrl+C.
"""

import argparse
import logging
import sys

from sitescout.config import ConfigError, load_config
from sitescout.logging_setup import configure_logging
from sitescout.server import InvestigationService, build_server


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()
    log = logging.getLogger("serve")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    service = InvestigationService.start(config)
    try:
        server = build_server(config, service)
    except OSError as error:
        log.error("Cannot listen on port %s: %s", config.settings.server.port, error.strerror)
        return 1
    log.info("SiteScout is served at %s (Ctrl+C to stop)", server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
