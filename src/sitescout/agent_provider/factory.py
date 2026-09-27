"""Milestone 10, Phase 5 (D-059): the one place that turns ``agent.model_provider`` into a
provider for the agent loop.

Structurally this is ``sitescout.analyst.factory`` again, one level up: it reads only the
validated YAML setting and builds that provider, never another one, and each adapter module
(and its SDK) is imported only when it is the configured one.
"""

from __future__ import annotations

from sitescout.analyst.provider import Provider
from sitescout.analyst.provider_common import ProviderError
from sitescout.config import AgentProviderSettings


def build_configured_agent_provider(
    settings: AgentProviderSettings, *, min_quote_words: int
) -> Provider:
    """The agent provider named by ``settings.provider``; see the module docstring."""
    if settings.provider == "anthropic":
        from sitescout.agent_provider.anthropic_adapter import build_provider as build_anthropic

        return build_anthropic(settings, min_quote_words=min_quote_words)
    if settings.provider == "openai":
        from sitescout.agent_provider.openai_adapter import build_provider as build_openai

        return build_openai(settings, min_quote_words=min_quote_words)
    raise ProviderError(f"unsupported agent provider {settings.provider!r}")
