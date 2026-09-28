"""Milestone 11 (D-063): run the demo-readiness gate's live part.

  uv run --env-file .env python scripts/demo_gate.py
  uv run --env-file .env python scripts/demo_gate.py --no-write

THIS SCRIPT MAKES REAL, BILLED API CALLS: demo_gate.runs_per_kind runs of each of the three gated
investigation kinds, through the same path the page's server uses. It is never run by pytest.
Writes reports/demo_gate.md (paths.demo_gate_report) with metadata only, never answer text;
--no-write prints the verdict only.

Exit code 0 when the gate passes, 1 when it fails or cannot start (no key, package or processed
data; nothing is run or written then), 2 for invalid usage.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime

from sitescout.config import ConfigError, load_config
from sitescout.demo_gate import render_report, run_gate
from sitescout.logging_setup import configure_logging
from sitescout.server import InvestigationService


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-write", action="store_true", help="print the verdict, write nothing")
    args = parser.parse_args()
    configure_logging()
    log = logging.getLogger("demo_gate")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)
    service = InvestigationService.start(config)
    if not service.available:
        log.error("Investigation is unavailable, so the gate cannot start; nothing was run.")
        return 1
    result = run_gate(
        service,
        config,
        generated_at=datetime.now(UTC).isoformat(),
        on_run=lambda r: log.info("%s: %s (%s)", r.kind, r.status, r.termination),
    )
    if not args.no_write:
        path = config.resolve(config.settings.paths.demo_gate_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_report(result).encode("utf-8"))
    lines = [f"Demo-readiness gate: {'PASS' if result.passed else 'FAIL'}"]
    lines += [f"  {v.kind}: {v.validated} of {v.runs} validated" for v in result.verdicts]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
