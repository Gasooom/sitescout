"""Milestone 10, Phase 5 (D-059): a real provider behind the M10 agent loop.

``sitescout.agent`` (Phase 3) drives any :class:`sitescout.analyst.provider.Provider` and
imports no SDK; ``sitescout.agent_provider`` is the provider-side half of that seam, for the
agent's nine capabilities specifically. It reuses M9's provider infrastructure
(``sitescout.analyst.provider_common``, and each SDK's reply-to-``ModelStep`` translation,
``to_step``) unchanged, and adds only what the agent needs and M9 cannot supply as-is: a
check for the agent's nine tools instead of M9's six (:mod:`sitescout.agent_provider.common`)
and the agent's own system prompt (:mod:`sitescout.agent_provider.prompt`). Building the
request for each real API and picking the configured one are one level up, exactly as in
``sitescout.analyst``: :mod:`anthropic_adapter`, :mod:`openai_adapter`, :func:`factory.
build_configured_agent_provider`. :mod:`ask` is the manual smoke-test seam
(``scripts/agent_ask.py``); no test here calls a real model, and FakeModel-driven agent runs
and the M10 Phase 4 evaluation are unaffected.
"""

from sitescout.agent_provider.ask import AgentProviderFactory, ask
from sitescout.agent_provider.common import check_agent_tools
from sitescout.agent_provider.factory import build_configured_agent_provider
from sitescout.agent_provider.prompt import build_system_prompt

__all__ = [
    "AgentProviderFactory",
    "ask",
    "build_configured_agent_provider",
    "build_system_prompt",
    "check_agent_tools",
]
