"""Milestone 16 (D-071): serve SiteScout's read-only tools and its validator over MCP (stdio).

  uv sync --extra mcp
  uv run python scripts/mcp_serve.py

An MCP client starts this command and talks to it over stdin and stdout; logs go to stderr.
The tools read the committed processed outputs in data/processed/ (D-066), so a fresh clone
serves without running the pipeline. No API key is needed, no model is called, no network port
is opened and nothing is written. One process serves one client, and the evidence that
validate_answer accepts is only what that client fetched through this process.
"""

import argparse
import asyncio
import logging

from sitescout.config import ConfigError, load_config
from sitescout.logging_setup import configure_logging


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    configure_logging()  # stderr: stdout carries the protocol
    log = logging.getLogger("mcp_serve")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    try:
        import mcp  # noqa: F401 - the optional extra; sitescout.mcp_server imports it lazily
    except ImportError:
        log.error("The MCP extra is not installed; run: uv sync --extra mcp")
        return 1
    from sitescout.mcp_server import SiteScoutTools, build_server, serve_stdio

    processed = config.resolve(config.settings.paths.processed_dir)
    try:
        tools = SiteScoutTools.load(config, processed)
    except Exception as error:  # noqa: BLE001 - say what is missing instead of a traceback
        log.error("Cannot load the processed outputs from %s: %s", processed, error)
        return 1
    log.info("SiteScout MCP server: %d tools over stdio", len(tools.list_tools()))
    asyncio.run(serve_stdio(build_server(tools)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
