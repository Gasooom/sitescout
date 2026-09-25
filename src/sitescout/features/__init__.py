"""Milestone 3: feature engineering (SPEC §4; docs/features.md).

``run_features`` writes ``features_production`` and ``features_backtest``: raw values for
every candidate, in natural units. Scoring is Milestone 4.
"""

from sitescout.features.build import (
    FeatureInputs,
    build_features,
    load_inputs,
    run_features,
)
from sitescout.features.chargers import MODES
from sitescout.features.nearest import FeatureError

__all__ = [
    "MODES",
    "FeatureError",
    "FeatureInputs",
    "build_features",
    "load_inputs",
    "run_features",
]
