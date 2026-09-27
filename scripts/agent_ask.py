"""Milestone 10, Phase 5 (D-059): ask the SiteScout Agent with a real provider.

  uv run python scripts/agent_ask.py "What does SiteScout mean by grid evidence?"
  uv run python scripts/agent_ask.py "How does the optimized network compare with Top-30?"

Runs the M10 agent loop (nine capabilities: the six M9 tools, network_summary, nearby_sites
and search_knowledge) with the provider configured in config/settings.yaml
(agent.model_provider, D-059). Needs the optional extra (`uv sync --extra analyst`) and the
configured provider's key, ANTHROPIC_API_KEY or OPENAI_API_KEY (the only secret, D-050).
Prints the validated answer with its quotations, or the deterministic fallback labelled
"no AI summary" with the evidence records gathered before the run stopped.

This is a manual smoke test only: it is not run by pytest, needs the real pipeline outputs
in data/processed/ (run the M1-M6 scripts first, or point --processed-dir elsewhere), and
makes a real network call. It proves the seam, not model quality: the M10 Phase 4 evaluation
(scripts/agent_eval.py) is the deterministic, offline measurement and is unaffected by this
script's existence.

Exit code 0 for a validated answer, 1 for the fallback (including a missing key or package),
2 for invalid usage.
"""

import argparse
import logging
import sys

from sitescout.agent import AgentContext
from sitescout.agent_provider import ask
from sitescout.config import ConfigError, load_config
from sitescout.logging_setup import configure_logging


def render(result) -> str:
    """Plain text for the terminal: the validated answer with its quotes, or the fallback."""
    if result.status == "answered" and result.answer is not None:
        lines = ["SiteScout Agent answer (validated against this session's tool records)"]
        for section, statements in result.answer.sections():
            if not statements:
                continue
            lines += ["", section.replace("_", " ").capitalize() + ":"]
            for statement in statements:
                cited = ", ".join(statement.evidence_ids) or "no citation"
                lines.append(f"- {statement.text} [{statement.kind}; {cited}]")
                for quote in statement.quotes:
                    lines.append(f'    quote: "{quote}"')
        return "\n".join(lines) + "\n"
    fallback = result.fallback
    lines = ["FALLBACK: no AI summary", f"Reason: {result.termination}"]
    if fallback is not None:
        lines[-1] += f" ({fallback.reason})"
        if fallback.records:
            lines += ["", "Evidence records gathered before the run stopped:"]
            lines += [f"- {r.id}: {r.claim}: {r.display}" for r in fallback.records]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("question")
    args = parser.parse_args()
    configure_logging()
    log = logging.getLogger("agent_ask")
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
    context = AgentContext.load(config, processed)
    result = ask(context, args.question, config.settings.agent)
    # The answer is this command's output, not a log message.
    sys.stdout.write(render(result))
    return 0 if result.status == "answered" else 1


if __name__ == "__main__":
    sys.exit(main())
