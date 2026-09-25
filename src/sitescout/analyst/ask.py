"""Milestone 9, phase 3b: answer one question with the configured provider, and render it.

``ask`` builds the provider named in ``config/settings.yaml``, loads the processed outputs
and runs the phase 3a loop. A missing key (``ANTHROPIC_API_KEY`` or ``OPENAI_API_KEY``) or
a missing optional SDK never raises and never switches to another provider: it becomes the
deterministic fallback ("no AI summary") with the reason, before any data is read.
``render_result`` prints a validated answer with its citations, or the fallback with the raw
evidence records; it never prints prompts, provider state or secrets.
"""

from __future__ import annotations

from collections.abc import Callable

from sitescout.analyst.credentials import CredentialError
from sitescout.analyst.provider import Provider
from sitescout.analyst.run import FALLBACK_LABEL, FallbackAnswer, RunResult, run_analyst
from sitescout.analyst.tools import AnalystData
from sitescout.config import AnalystSettings, Config

ProviderFactory = Callable[[AnalystSettings], Provider]


def _default_factory(settings: AnalystSettings) -> Provider:
    from sitescout.analyst.factory import build_configured_provider

    return build_configured_provider(settings)


def ask(config: Config, question: str, factory: ProviderFactory = _default_factory) -> RunResult:
    """Run the analyst loop on ``question``; see the module docstring."""
    from sitescout.analyst.provider_common import ProviderError

    try:
        provider = factory(config.settings.analyst)
    except (CredentialError, ProviderError) as error:
        return RunResult(
            question=question,
            status="fallback",
            fallback=FallbackAnswer(reason=f"no model was called: {error}"),
        )
    data = AnalystData.load(config, config.resolve(config.settings.paths.processed_dir))
    return run_analyst(data, question, provider)


def render_result(result: RunResult) -> str:
    """Plain text for the terminal: the validated answer, or the labelled fallback."""
    if result.status == "answered" and result.answer is not None:
        lines = ["SiteScout Analyst answer (validated against this session's tool records)"]
        for section, statements in result.answer.sections():
            if not statements:
                continue
            lines += ["", section.replace("_", " ").capitalize() + ":"]
            for statement in statements:
                cited = ", ".join(statement.evidence_ids) or "no citation"
                lines.append(f"- {statement.text} [{statement.kind}; {cited}]")
        return "\n".join(lines) + "\n"
    fallback = result.fallback or FallbackAnswer(reason="the run produced no answer")
    lines = [FALLBACK_LABEL, f"Reason: {fallback.reason}"]
    if fallback.records:
        lines += ["", "Evidence records returned by the tools this session:"]
        lines += [f"- {r.id}: {r.claim}: {r.display}" for r in fallback.records]
    return "\n".join(lines) + "\n"
