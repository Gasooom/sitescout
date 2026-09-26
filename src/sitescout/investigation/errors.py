"""Milestone 10, Phase 2 (D-057): the investigation tools' deterministic, structured errors."""

from __future__ import annotations

from typing import Literal

from sitescout.analyst.tools import AnalystError

ErrorCode = Literal["invalid_arguments", "unknown_site", "network_summary_unavailable"]


class InvestigationError(AnalystError):
    """A tool refused its arguments or its authoritative input.

    ``code`` is one of ``invalid_arguments`` (a bad argument), ``unknown_site`` (no such
    candidate) and ``network_summary_unavailable`` (``network.json`` is missing, malformed or
    inconsistent with the network layer). It is an ``AnalystError``, so code that already
    handles the M9 tools' refusals handles these too. An empty ``nearby_sites`` result is not
    an error.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code: ErrorCode = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}
