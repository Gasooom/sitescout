"""Milestone 10, Phase 2 (D-057): what the investigation tools read.

``AnalystData`` (M9) holds the candidates, their scores and their geometry but not where the
processed outputs live, and ``network_summary`` must read ``network.json`` from there. This
small wrapper pairs the two. It is read-only: nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sitescout.analyst.tools import AnalystData
from sitescout.config import Config


@dataclass(frozen=True)
class InvestigationData:
    analyst: AnalystData
    processed_dir: Path

    @classmethod
    def load(cls, config: Config, processed_dir: Path) -> InvestigationData:
        return cls(analyst=AnalystData.load(config, processed_dir), processed_dir=processed_dir)
