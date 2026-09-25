"""Milestone 9 (D-054): the one place that turns ``analyst.provider`` into a provider.

``build_configured_provider`` reads only the validated YAML setting and builds that
provider, never another one: a missing key or SDK raises for the caller (``ask`` and the
scenario evaluation turn it into the deterministic fallback or a clear error). Each
provider module, and its SDK, is imported only when it is the configured one.
"""

from __future__ import annotations

from sitescout.analyst.provider import Provider
from sitescout.analyst.provider_common import ProviderError
from sitescout.config import AnalystSettings


def build_configured_provider(settings: AnalystSettings) -> Provider:
    """The provider named by ``settings.provider``; see the module docstring."""
    if settings.provider == "anthropic":
        from sitescout.analyst.anthropic_provider import build_provider as build_anthropic

        return build_anthropic(settings)
    if settings.provider == "openai":
        from sitescout.analyst.openai_provider import build_provider as build_openai

        return build_openai(settings)
    raise ProviderError(f"unsupported analyst provider {settings.provider!r}")
