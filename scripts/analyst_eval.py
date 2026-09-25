"""Milestone 9, phase 3c: evaluate the SiteScout Analyst on the fixed scenario set.

  uv sync --extra analyst
  uv run python scripts/analyst_eval.py            # live: needs the provider's key (D-050)
  uv run python scripts/analyst_eval.py --only C2 E1
  uv run python scripts/analyst_eval.py --offline  # no model, no key, nothing written

Live, it puts every scenario of tests/analyst_scenarios.yaml (paths.analyst_scenarios) to
the configured provider, one after another, scores each answer with deterministic checks
and writes reports/analyst_eval.md (paths.analyst_eval_report) and the full log
data/processed/analyst_eval.json. Offline, it runs only the deterministic parts (preflight,
the number-format probes and the scripted scenario) and writes nothing.

Exit code 0 when the thresholds are met (or, offline, when every deterministic point
passes); 1 when they are not, when the key or the optional SDK is missing, or when a
scenario's site no longer has its stated property; 2 for invalid usage.
"""

import argparse
import logging
import sys

from sitescout.analyst.credentials import CredentialError
from sitescout.analyst.scenarios import EvaluationError, run_evaluation
from sitescout.config import ConfigError, load_config
from sitescout.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--offline", action="store_true", help="deterministic parts only")
    parser.add_argument("--only", nargs="+", default=(), metavar="ID", help="scenario ids")
    args = parser.parse_args()
    configure_logging()
    log = logging.getLogger("analyst_eval")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)

    from sitescout.analyst.factory import build_configured_provider
    from sitescout.analyst.provider_common import ProviderError

    factory = None if args.offline else build_configured_provider
    try:
        evaluation = run_evaluation(config, factory, only=args.only, offline=args.offline)
    except (CredentialError, ProviderError) as error:
        log.error("The live evaluation cannot start: %s Nothing was run or written.", error)
        return 1
    except EvaluationError as error:
        log.error("%s", error)
        return 1

    summary = evaluation.summary
    lines = [
        f"Scenarios: {summary.scenarios} ({summary.run} run); answered {summary.answered}, "
        f"fallbacks {summary.fallbacks}, retries {summary.retries}",
        f"Points: {summary.points_passed} of {summary.points_total}",
        *(f"- {t.name}: {t.actual} (target {t.target})" for t in summary.thresholds),
    ]
    if evaluation.offline:
        lines.insert(0, "Offline: deterministic parts only; no model was called, nothing written.")
    else:
        verdict = "PASS" if summary.passed else "FAIL"
        lines.insert(0, f"Result: {verdict} (report: {config.settings.paths.analyst_eval_report})")
    # The summary is this command's output, not a log message.
    sys.stdout.write("\n".join(lines) + "\n")
    if evaluation.offline:
        return 0 if summary.points_passed == summary.points_total else 1
    return 0 if summary.passed else 1


if __name__ == "__main__":
    sys.exit(main())
