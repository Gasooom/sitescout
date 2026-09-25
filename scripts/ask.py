"""Milestone 9: ask the SiteScout Analyst (SPEC §11).

Two modes:

  uv run python scripts/ask.py "Why is cand-8366165e2a19 not in the network?"
      Runs the analyst loop with the provider configured in config/settings.yaml
      (analyst:, D-051, D-054). Needs the optional extra (`uv sync --extra analyst`) and
      the configured provider's key, ANTHROPIC_API_KEY or OPENAI_API_KEY, the only
      secret (D-050). Prints the
      validated answer, or the deterministic fallback labelled "no AI summary" with the
      evidence records the tools returned. Exit code 0 for a validated answer, 1 for the
      fallback (including a missing key or package), 2 for invalid usage.

  uv run python scripts/ask.py --tools-only <tool> [arguments]
      Calls one deterministic tool directly and prints its structured result as JSON.
      Never uses a model, a key or the network. Examples:

  uv run python scripts/ask.py --tools-only find_sites --selected-top30 yes --selected-mclp no
  uv run python scripts/ask.py --tools-only find_sites --district Huye --rank 67
  uv run python scripts/ask.py --tools-only get_site cand-0bd87a61afe8
  uv run python scripts/ask.py --tools-only compare_sites cand-0bd87a61afe8 cand-3a8fa00f876a
  uv run python scripts/ask.py --tools-only explain_score cand-0bd87a61afe8
  uv run python scripts/ask.py --tools-only network_contribution cand-8366165e2a19
  uv run python scripts/ask.py --tools-only generate_brief cand-0bd87a61afe8

      Exit code 0 on success; 1 when a tool refuses its arguments or the outputs are
      missing; 2 for invalid usage.
"""

import argparse
import json
import logging
import sys

from sitescout.analyst import (
    AnalystData,
    AnalystError,
    FindQuery,
    compare_sites,
    explain_score,
    find_sites,
    generate_brief,
    get_site,
    network_contribution,
)
from sitescout.analyst.ask import ask, render_result
from sitescout.config import ConfigError, load_config
from sitescout.crs import CrsError
from sitescout.ingest import IngestError
from sitescout.logging_setup import configure_logging

YES_NO = {"yes": True, "no": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--tools-only",
        action="store_true",
        help="call one deterministic tool directly, with no model",
    )
    tools = parser.add_subparsers(dest="tool", required=True)
    find = tools.add_parser("find_sites", help="exact filters, combined with AND")
    find.add_argument("--candidate-id")
    find.add_argument("--label", help='exact generic label, e.g. "Fuel station, Huye"')
    find.add_argument("--district")
    find.add_argument("--province")
    find.add_argument("--rank", type=int)
    find.add_argument("--profile", choices=["urban", "corridor"])
    find.add_argument("--confidence", choices=["High", "Medium", "Low"])
    find.add_argument("--selected-mclp", choices=YES_NO)
    find.add_argument("--selected-top30", choices=YES_NO)
    find.add_argument("--grid-evidence-status", choices=["CALCULATED", "UNKNOWN"])
    for name in ("get_site", "explain_score", "network_contribution", "generate_brief"):
        tools.add_parser(name).add_argument("candidate_id")
    compare = tools.add_parser("compare_sites")
    compare.add_argument("a")
    compare.add_argument("b")
    return parser


def run_tool(args: argparse.Namespace, data: AnalystData):
    if args.tool == "find_sites":
        query = FindQuery(
            candidate_id=args.candidate_id,
            label=args.label,
            district=args.district,
            province=args.province,
            rank=args.rank,
            profile=args.profile,
            confidence=args.confidence,
            selected_mclp=YES_NO.get(args.selected_mclp),
            selected_top30=YES_NO.get(args.selected_top30),
            grid_evidence_status=args.grid_evidence_status,
        )
        return find_sites(data, query)
    if args.tool == "compare_sites":
        return compare_sites(data, args.a, args.b)
    tool = {
        "get_site": get_site,
        "explain_score": explain_score,
        "network_contribution": network_contribution,
        "generate_brief": generate_brief,
    }[args.tool]
    return tool(data, args.candidate_id)


def build_question_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("question", help="the question, in quotes")
    return parser


def main() -> int:
    tools_only = "--tools-only" in sys.argv[1:]
    args = (build_parser() if tools_only else build_question_parser()).parse_args()
    configure_logging()
    log = logging.getLogger("ask")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    if not tools_only:
        return run_question(args.question, config, log)
    try:
        data = AnalystData.load(config, config.resolve(config.settings.paths.processed_dir))
        result = run_tool(args, data)
    except (AnalystError, IngestError, CrsError, ConfigError, ValueError) as error:
        log.error("Tool call refused: %s", error)
        return 1
    # The tool's structured result is this command's output, not a log message.
    sys.stdout.write(json.dumps(result.model_dump(), indent=2, ensure_ascii=False) + "\n")
    return 0


def run_question(question: str, config, log: logging.Logger) -> int:
    try:
        result = ask(config, question)
    except (IngestError, CrsError, ConfigError) as error:
        log.error("The processed outputs could not be read: %s", error)
        return 1
    log.info(
        "Analyst run: %s after %d tool call(s)%s",
        result.status,
        len(result.transcript),
        ", with the one retry" if result.retried else "",
    )
    # The answer (or the labelled fallback) is this command's output, not a log message.
    sys.stdout.write(render_result(result))
    return 0 if result.status == "answered" else 1


if __name__ == "__main__":
    sys.exit(main())
