"""Milestone 10, Phase 2: the deterministic site investigation tools (D-057).

  uv run python scripts/investigate.py network_summary
      The network-level decision results in data/processed/network.json: the optimized,
      greedy and Top-30 selections, the exact-versus-greedy gap, overlaps, solver status,
      parameters and sensitivity rows.
  uv run python scripts/investigate.py nearby_sites <candidate_id> --radius-m 5000
      The candidates within the radius (1 metre up to the service radius) of one candidate,
      nearest first. At most investigation.nearby_sites.max_results are listed; the full
      count is always reported.

Both are read-only and print the tool's structured result as JSON. They use no model, key,
knowledge index or network access, and write nothing. Exit code 0 on success; 1 when a tool
refuses its arguments or its input (the error is printed as JSON with its code) or the
processed outputs are missing; 2 for invalid usage.
"""

import argparse
import json
import logging
import sys

from sitescout.analyst.tools import AnalystError
from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.investigation import (
    InvestigationData,
    InvestigationError,
    nearby_sites,
    network_summary,
)
from sitescout.logging_setup import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    tools = parser.add_subparsers(dest="tool", required=True)
    tools.add_parser("network_summary", help="the network-level decision results")
    nearby = tools.add_parser("nearby_sites", help="candidates within a radius of one candidate")
    nearby.add_argument("candidate_id")
    nearby.add_argument("--radius-m", type=int, required=True, help="metres, at least 1")
    return parser


def _print(payload: object) -> None:
    # The tool's structured result is this command's output, not a log message.
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    args = build_parser().parse_args()
    configure_logging()
    log = logging.getLogger("investigate")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    try:
        data = InvestigationData.load(config, config.resolve(config.settings.paths.processed_dir))
        if args.tool == "network_summary":
            result = network_summary(data)
        else:
            result = nearby_sites(data.analyst, args.candidate_id, args.radius_m)
    except InvestigationError as error:
        log.error("Tool call refused: %s", error)
        _print({"error": error.to_dict()})
        return 1
    except (AnalystError, IngestError, CrsError, ConfigError, ValueError) as error:
        log.error("The processed outputs could not be read: %s", error)
        return 1
    _print(result.model_dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
