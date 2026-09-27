"""Milestone 10, Phase 5 (D-059): what the agent's provider adapters share.

Everything genuinely provider-neutral (the question wrapper, tool-output serialization, the
retry note, unparsed-output handling, secret redaction, the error types) already lives in
``sitescout.analyst.provider_common`` and needs no agent-specific version: it is reused here
unchanged. The one thing M9's ``provider_common.check_tools`` cannot do is accept the agent's
nine tools, so ``check_agent_tools`` is the agent's own version of that one check; it does not
touch ``provider_common`` or anything M9 checks.
"""

from __future__ import annotations

from sitescout.agent import AGENT_TOOL_NAMES
from sitescout.analyst.provider import ModelContext
from sitescout.analyst.provider_common import ProviderError

__all__ = ["check_agent_tools"]


def check_agent_tools(context: ModelContext) -> None:
    """Refuse a context that offers anything but exactly the nine agent capabilities."""
    names = sorted(tool.name for tool in context.tools)
    if names != sorted(AGENT_TOOL_NAMES):
        raise ProviderError(f"expected exactly the nine agent tools, got {names}")
