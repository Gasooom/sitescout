"""Confidence levels and reasons for each scored candidate (SPEC §6, D-041).

A level is High, Medium or Low, never a percentage. It uses only factors that differ
between sites:

- **grid evidence missing** (no mapped substation or line within the missing radius): the
  level is ``confidence.grid_evidence_missing_level`` (Low), whatever else holds;
- **sparse public-map coverage**: the district's ``grid_completeness_ratio`` is below
  ``confidence.sparse_coverage_threshold``;
- **no identified host**: ``host_type`` is ``none``;
- **remote location**: at most ``confidence.remote_location_rule.max_poi_3km`` mapped POIs
  within 3 km to cross-check against.

Without missing grid evidence, the number of the other three factors picks the level from
``confidence.factor_count_levels`` (0 -> High, 1 -> Medium, 2 or more -> Low). Every factor
that holds is written into the reasons, including those that do not change the level. The
universal unknowns are the same fixed list for every site and are not part of the level.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sitescout.config import Settings

NO_FACTOR = "No confidence-lowering factor found"


def assess(
    features: pd.DataFrame, grid_missing: np.ndarray, settings: Settings
) -> tuple[np.ndarray, np.ndarray]:
    """(level, reasons) for each row of a feature table."""
    rules = settings.confidence
    radius_km = settings.scoring.grid_evidence.missing_radius_m / 1000
    factors = {
        "sparse": (
            features["grid_completeness_ratio"].to_numpy(dtype=np.float64)
            < rules.sparse_coverage_threshold,
            "Sparse public-map coverage in the district (grid completeness ratio below "
            f"{rules.sparse_coverage_threshold:g})",
        ),
        "no_host": (
            (features["host_type"] == "none").to_numpy(dtype=bool),
            "No identified host (a corridor point)",
        ),
        "remote": (
            features["poi_3km"].to_numpy() <= rules.remote_location_rule.max_poi_3km,
            "Remote location: at most "
            f"{rules.remote_location_rule.max_poi_3km} mapped POIs within 3 km to cross-check",
        ),
    }
    grid_reason = (
        f"Grid evidence missing: no mapped substation or power line within {radius_km:g} km"
    )
    count = sum(mask.astype(np.int64) for mask, _ in factors.values())
    levels_by_count = rules.factor_count_levels
    levels = np.array(
        [levels_by_count[min(int(n), len(levels_by_count) - 1)] for n in count], dtype=object
    )
    levels[grid_missing] = rules.grid_evidence_missing_level
    reasons = []
    for row in range(len(features)):
        found = [grid_reason] if grid_missing[row] else []
        found += [text for mask, text in factors.values() if mask[row]]
        reasons.append("; ".join(found) if found else NO_FACTOR)
    return levels, np.array(reasons, dtype=object)


def universal_unknowns(settings: Settings) -> str:
    """The fixed list of unknowns every site shares (SPEC §6), as one string."""
    return "; ".join(settings.confidence.universal_unknowns)
