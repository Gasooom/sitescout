"""Milestone 10, Phase 6 (D-060): the agent's bounded, unscripted LIVE evaluation.

  uv run python scripts/agent_live_eval.py
  uv run python scripts/agent_live_eval.py --provider anthropic --model claude-sonnet-5
  uv run python scripts/agent_live_eval.py --only A01 D01 --no-write

THIS SCRIPT MAKES REAL, BILLED API CALLS. It is never run by pytest and never run by CI.
Runs at most agent.live_eval.max_cases of tests/agent_live_cases.yaml (paths.agent_live_cases)
through the unmodified M10 agent loop, with the provider configured in config/settings.yaml
(agent.model_provider, D-059) unless --provider/--model override it for this run only
(nothing is written back to YAML). Needs the optional extra (`uv sync --extra analyst`), the
configured provider's key (ANTHROPIC_API_KEY or OPENAI_API_KEY, the only secret, D-050), and
the real pipeline outputs in data/processed/ (run the M1-M6 scripts first). The model chooses
every tool call and its order; nothing here scripts a trajectory or relaxes a grounding rule.

Writes reports/agent_live_eval.md (paths.agent_live_eval_report) and the full log
data/processed/agent_live_eval.json (git-ignored, never committed). With --no-write, runs
and prints the summary only.

This is an observational report, not a pass/fail gate (D-060): exit code 0 means the run
completed (regardless of how individual cases classified); 1 means it could not start (a
missing key, package or processed data); 2 for invalid usage.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime

from sitescout.agent import AgentContext
from sitescout.agent_live_eval import (
    LiveCaseError,
    RecordingAnthropicClient,
    RecordingOpenAIClient,
    evaluate_live,
    live_agent_limits,
    load_live_cases,
    render_report,
)
from sitescout.agent_live_eval.cases import LiveCaseSet
from sitescout.agent_provider import build_configured_agent_provider
from sitescout.agent_provider.anthropic_adapter import AgentAnthropicProvider
from sitescout.analyst.credentials import CredentialError
from sitescout.analyst.provider_common import ProviderError, ProviderUnavailable
from sitescout.config import ConfigError, load_config
from sitescout.logging_setup import configure_logging

RESULT_FILE = "agent_live_eval.json"


def _wrap_for_instrumentation(provider):
    """Swap the already-built provider's own client for a recording proxy that changes no
    request or response: only latency and the reply's own ``usage`` are captured. This
    touches nothing in ``sitescout.agent_provider``; see D-060's rationale."""
    recorder_cls = (
        RecordingAnthropicClient
        if isinstance(provider, AgentAnthropicProvider)
        else RecordingOpenAIClient
    )
    recorder = recorder_cls(provider._client)  # noqa: SLF001 - the documented instrumentation seam
    provider._client = recorder  # noqa: SLF001
    return recorder


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--provider", choices=["anthropic", "openai"], help="override for this run only"
    )
    parser.add_argument("--model", help="override for this run only")
    parser.add_argument("--only", nargs="+", default=(), metavar="ID", help="case ids")
    parser.add_argument("--no-write", action="store_true", help="print the summary, write nothing")
    args = parser.parse_args()
    configure_logging()
    log = logging.getLogger("agent_live_eval")
    try:
        config = load_config()
    except ConfigError as error:
        log.error("%s", error)
        return 1
    configure_logging(config.settings.logging.level)

    processed = config.resolve(config.settings.paths.processed_dir)
    if not processed.exists():
        log.error("%s does not exist; run the pipeline scripts first.", processed)
        return 1
    try:
        cases = load_live_cases(config.resolve(config.settings.paths.agent_live_cases))
    except LiveCaseError as error:
        log.error("%s", error)
        return 1
    if args.only:
        cases = LiveCaseSet(
            version=cases.version,
            world=cases.world,
            cases=tuple(c for c in cases.cases if c.id in args.only),
        )
    live = config.settings.agent.live_eval
    if len(cases.cases) > live.max_cases:
        cases = LiveCaseSet(
            version=cases.version, world=cases.world, cases=cases.cases[: live.max_cases]
        )

    provider_settings = config.settings.agent.model_provider
    if args.provider or args.model:
        provider_settings = provider_settings.model_copy(
            update={k: v for k, v in {"provider": args.provider, "model": args.model}.items() if v}
        )
    try:
        provider = build_configured_agent_provider(
            provider_settings, min_quote_words=config.settings.agent.min_quote_words
        )
    except (CredentialError, ProviderError, ProviderUnavailable) as error:
        log.error("The live evaluation cannot start: %s Nothing was run or written.", error)
        return 1

    recorder = _wrap_for_instrumentation(provider)
    context = AgentContext.load(config, processed)
    limits = live_agent_limits(config.settings.agent)
    generated_at = datetime.now(UTC).isoformat()
    evaluation = evaluate_live(
        context, cases, provider, limits, recorder=recorder, generated_at=generated_at
    )

    if not args.no_write:
        report_path = config.resolve(config.settings.paths.agent_live_eval_report)
        result_path = config.resolve(config.settings.paths.processed_dir) / RESULT_FILE
        report_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(
            render_report(evaluation, config.settings.agent.live_eval.price_table).encode("utf-8")
        )
        result_path.write_bytes(evaluation.model_dump_json(indent=2).encode("utf-8"))

    lines = [
        f"Cases: {evaluation.case_count} "
        f"(skipped {evaluation.skipped}, infra errors {evaluation.infra_errors})",
        f"By failure category: {evaluation.by_failure_category}",
        f"Total latency: {evaluation.total_latency_s:.1f}s; "
        f"tokens in={evaluation.total_input_tokens} out={evaluation.total_output_tokens}",
    ]
    if not args.no_write:
        lines.insert(0, f"Report: {config.settings.paths.agent_live_eval_report}")
    # The summary is this command's output, not a log message; it never contains a secret.
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
