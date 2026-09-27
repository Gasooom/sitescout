"""Milestone 10, Phase 5 (D-059): drive the agent loop with the configured real provider.

Structurally this is ``sitescout.analyst.ask`` again, one level up: ``ask`` builds the
provider named in ``config/settings.yaml`` (``agent.model_provider``), runs
``sitescout.agent.run_agent`` on the given ``AgentContext``, and never raises for a missing
key or SDK — a missing ``ANTHROPIC_API_KEY``/``OPENAI_API_KEY`` or optional package becomes
the loop's own fallback shape (a ``termination`` of ``provider_error``, no answer, no
records: nothing was run, so there is nothing to show), before any tool is called. This is
the seam a manual smoke test uses (``scripts/agent_ask.py``); no test here calls a real model.
"""

from __future__ import annotations

from collections.abc import Callable

from sitescout.agent import AgentContext, AgentLimits, AgentResult, AgentState, run_agent
from sitescout.analyst.credentials import CredentialError
from sitescout.analyst.provider import Provider
from sitescout.config import AgentSettings

AgentProviderFactory = Callable[..., Provider]


def _default_factory(settings: AgentSettings) -> Provider:
    from sitescout.agent_provider.factory import build_configured_agent_provider

    return build_configured_agent_provider(
        settings.model_provider, min_quote_words=settings.min_quote_words
    )


def ask(
    context: AgentContext,
    question: str,
    settings: AgentSettings,
    factory: AgentProviderFactory = _default_factory,
) -> AgentResult:
    """Run the agent loop on ``question`` with the configured real provider; see the module
    docstring for what happens when no provider can be built."""
    from sitescout.analyst.provider_common import ProviderError
    from sitescout.analyst.run import FallbackAnswer

    try:
        provider = factory(settings)
    except (CredentialError, ProviderError) as error:
        fallback = FallbackAnswer(reason=f"no model was called: {error}")
        return AgentResult(
            question=question,
            status="fallback",
            termination="provider_error",
            fallback=fallback,
            state=AgentState(question=question, termination="provider_error", fallback=fallback),
        )
    return run_agent(context, question, provider, AgentLimits.from_settings(settings))
