"""Milestone 10, Phase 4: evaluate the agent's grounding rules and loop, offline.

  uv run python scripts/agent_eval.py             # run, write the report and the JSON result
  uv run python scripts/agent_eval.py --no-write  # run and print the summary only

Runs every case of tests/agent_cases.yaml (paths.agent_cases) through the production agent loop
with a scripted provider, on the SYNTHETIC test world and the real project-knowledge index. No
model, API key or network is used, and nothing needs data/. Writes reports/agent_eval.md
(paths.agent_eval_report) and the machine-readable data/processed/agent_eval.json (git-ignored).

Exit code 0 when every case matches its expectations (pinned known limits included); 1 when a
case does not, or the case file is invalid; 2 for invalid usage.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))  # the SYNTHETIC world

from agent_world import agent_world  # noqa: E402
from sitescout.agent import AgentLimits  # noqa: E402
from sitescout.agent_eval import CaseError, evaluate, load_cases, render_report  # noqa: E402
from sitescout.config import ConfigError, load_config  # noqa: E402
from sitescout.logging_setup import configure_logging  # noqa: E402

RESULT_FILE = "agent_eval.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-write", action="store_true", help="print the summary, write nothing")
    args = parser.parse_args()
    configure_logging()
    log = logging.getLogger("agent_eval")
    try:
        config = load_config()
        configure_logging(config.settings.logging.level)
        cases = load_cases(config.resolve(config.settings.paths.agent_cases))
    except (ConfigError, CaseError) as error:
        log.error("%s", error)
        return 1

    with agent_world() as (_, context):
        evaluation = evaluate(context, cases, AgentLimits.from_settings(config.settings.agent))

    if not args.no_write:
        report = config.resolve(config.settings.paths.agent_eval_report)
        result = config.resolve(config.settings.paths.processed_dir) / RESULT_FILE
        report.parent.mkdir(parents=True, exist_ok=True)
        result.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(render_report(evaluation).encode("utf-8"))
        result.write_bytes(evaluation.model_dump_json(indent=2).encode("utf-8"))

    t = evaluation.total
    lines = [
        f"Cases: {t.cases}; pass {t.passed}, fail {t.failed}",
        f"Decisions: true accept {t.true_accept}, true reject {t.true_reject}, "
        f"false accept {t.false_accept}, false reject {t.false_reject}, "
        f"not applicable {t.not_applicable}",
        *(f"FAIL {r.id}: {'; '.join(r.failures)}" for r in evaluation.cases if not r.passed),
    ]
    # The summary is this command's output, not a log message.
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if t.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
