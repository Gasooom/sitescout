"""Milestone 9 (D-050, D-054): the AI Site Analyst's secrets, one per provider.

``_read_secret`` is the single place SiteScout reads an environment variable, and only
through the two provider functions below: ``read_api_key`` reads ``ANTHROPIC_API_KEY`` and
``read_openai_api_key`` reads ``OPENAI_API_KEY``. Both are secret credentials only: the
provider, model, limits, timeout, temperature and every analytical parameter come from
``config/settings.yaml`` (D-051), never from the environment, and no ``.env`` file is read.

A key is returned as a ``SecretStr``, whose ``str`` and ``repr`` are masked, so logging or
formatting it by mistake prints ``**********``. Error messages name the variable, never its
value.
"""

from __future__ import annotations

import os

from pydantic import SecretStr

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
OPENAI_API_KEY_VARIABLE = "OPENAI_API_KEY"


class CredentialError(Exception):
    """The analyst's API key is not available. The message never contains a secret."""


def _read_secret(variable: str) -> SecretStr:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise CredentialError(
            f"{variable} is not set, so no model can be called. Set it in the "
            "environment of this command only; never write it to a file in the repository."
        )
    return SecretStr(value)


def read_api_key() -> SecretStr:
    """The Anthropic API key from ``ANTHROPIC_API_KEY``, or ``CredentialError`` if unset."""
    return _read_secret(API_KEY_VARIABLE)


def read_openai_api_key() -> SecretStr:
    """The OpenAI API key from ``OPENAI_API_KEY``, or ``CredentialError`` if unset."""
    return _read_secret(OPENAI_API_KEY_VARIABLE)
