"""Milestone 4: scores for every candidate, in production and backtest mode (SPEC §5).

For each mode, the M3 feature layer is turned into scores in five steps:

1. **Percentile points** for each weighted feature (D-040). log1p is applied first to the
   skewed counts in ``scoring.log1p_features``; it keeps the order of values, so it changes
   no rank. Percentile = 100 x (candidates below + 0.5 x candidates equal) / n, over the
   candidates of the same mode whose value is present: ties share a value, a constant
   feature and a single candidate get 50. Features in ``scoring.lower_is_better`` are
   inverted as 100 - percentile. A missing value gets 0, so it never raises a score.
2. **Components**: the feature weights of ``config/weights.yaml`` applied to the percentile
   points, plus the host-type bonus (host / commercial) and the road-class bonus (access,
   exact ``road_class`` match), each component capped at 100.
3. **Grid evidence missing**: when no mapped substation or line lies within
   ``scoring.grid_evidence.missing_radius_m``, the component is
   ``missing_component_score`` (0) and its status UNKNOWN; it never gets an average.
4. **Profile** (D-039): urban within ``kigali_radius_m`` of the Kigali city centre or within
   ``town_radius_m`` of any town or city centre, inclusive; otherwise corridor. The two
   distances only assign the profile; they are never scored.
5. **Score** = the profile's component weights x the components (0 to 100). Rank 1 is the
   highest score; equal scores are ordered by candidate_id.

Confidence comes from ``sitescout.confidence``. In backtest mode no charger exists, so the
charging-gap component is the same for every candidate (SPEC §5); it is not compensated.
No model, randomness or LLM is involved.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.confidence import assess, universal_unknowns
from sitescout.config import ComponentWeights, RoadClassBonus, Settings, Weights
from sitescout.ingest.layers import (
    FEATURES_BACKTEST,
    FEATURES_PRODUCTION,
    SCORE_COMPONENTS,
    SCORES_BACKTEST,
    SCORES_PRODUCTION,
    LayerResult,
    LayerSchema,
    metadata_path,
    read_layer,
    remove_layer,
    validate_layer,
    write_layer,
)
from sitescout.ingest.metadata import read_json

logger = logging.getLogger(__name__)

MODES = ("production", "backtest")
INPUTS: dict[str, LayerSchema] = {"production": FEATURES_PRODUCTION, "backtest": FEATURES_BACKTEST}
OUTPUTS: dict[str, LayerSchema] = {"production": SCORES_PRODUCTION, "backtest": SCORES_BACKTEST}
UNKNOWN, CALCULATED = "UNKNOWN", "CALCULATED"
DECISIONS = {
    "D-039": "profile: urban within 10 km of the Kigali city centre or 3 km of a town or city "
    "centre, else corridor",
    "D-040": "percentile = 100 x (below + 0.5 x equal) / n within the mode; ties share a value; "
    "missing = 0; lower-is-better inverted; log1p on the skewed counts",
    "D-041": "confidence: grid evidence missing -> Low; else 0/1/2+ of sparse coverage, no "
    "host, remote -> High/Medium/Low",
    "D-042": "components, exact road-class bonuses, cap at 100, grid evidence missing -> 0 "
    "(UNKNOWN); backtest charging gap left constant",
}


class ScoreError(Exception):
    """Scoring cannot produce a valid result."""


def percentile_rank(values: np.ndarray) -> np.ndarray:
    """100 x (below + 0.5 x equal) / n over the values present; NaN stays NaN.

    n counts only the values present. Equal values share a percentile; a constant column
    and a single value give 50.
    """
    values = np.asarray(values, dtype=np.float64)
    result = np.full(len(values), np.nan)
    present = ~np.isnan(values)
    reference = np.sort(values[present])
    if len(reference) == 0:
        return result
    below = np.searchsorted(reference, values[present], side="left")
    up_to = np.searchsorted(reference, values[present], side="right")
    result[present] = 100.0 * (below + 0.5 * (up_to - below)) / len(reference)
    return result


def feature_points(features: pd.DataFrame, settings: Settings, weights: Weights) -> pd.DataFrame:
    """Percentile points (0-100, higher is better) for every weighted feature."""
    scoring = settings.scoring
    points = {}
    for name in weights.weighted_features():
        values = features[name].to_numpy(dtype=np.float64)
        if name in scoring.log1p_features:
            values = np.log1p(values)
        percentile = percentile_rank(values)
        if name in scoring.lower_is_better:
            percentile = 100.0 - percentile
        points[name] = np.where(np.isnan(percentile), 0.0, percentile)
    return pd.DataFrame(points, index=features.index)


def grid_evidence_missing(features: pd.DataFrame, settings: Settings) -> np.ndarray:
    """True where neither a mapped substation nor a line lies within the missing radius."""
    radius = settings.scoring.grid_evidence.missing_radius_m
    near = (features["dist_substation_m"] <= radius) | (features["dist_line_m"] <= radius)
    return ~near.fillna(False).to_numpy(dtype=bool)


def assign_profile(features: pd.DataFrame, settings: Settings) -> np.ndarray:
    """urban or corridor for each candidate (SPEC §3, D-039); the radii are inclusive."""
    rule = settings.candidates.profile
    urban = (features["dist_kigali_cbd_m"] <= rule.kigali_radius_m) | (
        features["dist_town_m"] <= rule.town_radius_m
    )
    return np.where(urban.fillna(False).to_numpy(dtype=bool), "urban", "corridor").astype(object)


def score_features(
    features: pd.DataFrame, settings: Settings, weights: Weights
) -> gpd.GeoDataFrame:
    """The score table for one mode's feature layer, following the scores schema."""
    features = features.sort_values("candidate_id", kind="mergesort", ignore_index=True)
    cap = float(settings.scoring.scale_max)
    points = feature_points(features, settings, weights)

    components: dict[str, np.ndarray] = {}
    for name in ComponentWeights.model_fields:
        group = getattr(weights.components, name)
        total = np.zeros(len(features), dtype=np.float64)
        for feature in type(group).model_fields:
            total += getattr(group, feature) * points[feature].to_numpy()
        components[name] = total

    host_table = weights.bonuses.host_commercial.host_type
    host_bonus = features["host_type"].map(lambda kind: getattr(host_table, kind)).to_numpy()
    road_table = weights.bonuses.access.road_class
    road_bonus = (
        features["road_class"]
        .map(
            lambda value: getattr(road_table, value) if value in RoadClassBonus.model_fields else 0
        )
        .to_numpy()
    )
    components["host_commercial"] = components["host_commercial"] + host_bonus
    components["access"] = components["access"] + road_bonus
    components = {name: np.minimum(values, cap) for name, values in components.items()}

    missing = grid_evidence_missing(features, settings)
    missing_score = float(settings.scoring.grid_evidence.missing_component_score)
    components["grid_evidence"] = np.where(missing, missing_score, components["grid_evidence"])

    profile = assign_profile(features, settings)
    score = np.zeros(len(features), dtype=np.float64)
    for name in SCORE_COMPONENTS:
        profile_weight = np.where(
            profile == "urban",
            getattr(weights.profiles.urban, name),
            getattr(weights.profiles.corridor, name),
        )
        score += profile_weight * components[name]

    ids = features["candidate_id"].to_numpy()
    order = np.lexsort((ids, -score))
    rank = np.empty(len(features), dtype=np.int64)
    rank[order] = np.arange(1, len(features) + 1)
    level, reasons = assess(features, missing, settings)

    table = {
        "candidate_id": ids,
        "host_type": features["host_type"].to_numpy(),
        "origin": features["origin"].to_numpy(),
        "district_id": features["district_id"].to_numpy(),
        "district": features["district"].to_numpy(),
        "province": features["province"].to_numpy(),
        "profile": profile,
        "score": score,
        "rank": rank,
        "confidence": level,
        "confidence_reasons": reasons,
        **{f"component_{name}": components[name] for name in SCORE_COMPONENTS},
        "host_bonus": host_bonus.astype(np.int64),
        "road_bonus": road_bonus.astype(np.int64),
        "grid_evidence_status": np.where(missing, UNKNOWN, CALCULATED).astype(object),
        **{f"pct_{name}": points[name].to_numpy() for name in weights.weighted_features()},
        "universal_unknowns": np.full(len(features), universal_unknowns(settings), dtype=object),
    }
    return gpd.GeoDataFrame(table, geometry=features.geometry.to_numpy(), crs=settings.crs.storage)


def check_scores(table: pd.DataFrame, mode: str, settings: Settings) -> None:
    """Raise ScoreError listing every value outside its documented bounds."""
    problems = []
    cap = settings.scoring.scale_max
    bounded = ["score", *(c for c in table.columns if c.startswith(("component_", "pct_")))]
    for name in bounded:
        values = table[name].to_numpy(dtype=np.float64)
        if np.isnan(values).any() or (values < 0).any() or (values > cap).any():
            problems.append(f"{name} is outside 0-{cap}")
    if sorted(table["rank"]) != list(range(1, len(table) + 1)):
        problems.append("ranks are not 1..n")
    if not table["confidence"].isin(["High", "Medium", "Low"]).all():
        problems.append("confidence is not High, Medium or Low")
    if mode == "backtest" and table["component_charging_gap"].nunique() > 1:
        problems.append("backtest charging-gap component differs between candidates")
    if problems:
        raise ScoreError(f"Scores out of bounds ({mode}): " + "; ".join(problems))


def _summary(table: pd.DataFrame) -> dict[str, Any]:
    def counts(column: str) -> dict[str, int]:
        return {str(k): int(v) for k, v in sorted(Counter(table[column]).items())}

    return {
        "candidates": len(table),
        "by_profile": counts("profile"),
        "by_confidence": counts("confidence"),
        "by_grid_evidence_status": counts("grid_evidence_status"),
        "score": {
            "min": round(float(table["score"].min()), 6),
            "median": round(float(table["score"].median()), 6),
            "max": round(float(table["score"].max()), 6),
        },
    }


def _notes(settings: Settings) -> list[str]:
    return [
        "Scores rank candidates for investigation; they are not a measure of demand, revenue "
        "or grid capacity. Actual grid connection feasibility requires utility confirmation.",
        "Percentiles are within the candidates of this mode, not a national reference.",
        "Backtest mode: with no existing charger, the charging-gap component is the same for "
        "every candidate (SPEC §5); it is not compensated, so it adds more to corridor scores "
        "(weight 0.25) than to urban ones (0.15).",
        f"Universal unknowns: {universal_unknowns(settings)}.",
    ]


def run_scores(settings: Settings, weights: Weights, processed_dir: Path) -> list[LayerResult]:
    """Score both modes, check both, then write scores_production and scores_backtest.

    When anything fails, neither layer is written and old ones are removed.
    """
    built = {}
    try:
        for mode in MODES:
            features = read_layer(INPUTS[mode].name, processed_dir, settings)
            table = score_features(features, settings, weights)
            validate_layer(table, OUTPUTS[mode], settings)
            check_scores(table, mode, settings)
            fingerprint = read_json(metadata_path(processed_dir, INPUTS[mode].name))[
                "content_sha256"
            ]
            built[mode] = (table, {INPUTS[mode].name: fingerprint})
    except Exception:
        for schema in OUTPUTS.values():
            remove_layer(processed_dir, schema.name)
        raise
    results = []
    for mode, (table, inputs) in built.items():
        summary = _summary(table)
        logger.info(
            "%s: %d candidates; profiles %s; confidence %s; score %s",
            mode,
            summary["candidates"],
            summary["by_profile"],
            summary["by_confidence"],
            summary["score"],
        )
        stats = {
            "mode": mode,
            **summary,
            "inputs": inputs,
            "weights": weights.model_dump(mode="json", by_alias=True),
            "settings_used": {
                "profile": settings.candidates.profile.model_dump(mode="json"),
                "scoring": settings.scoring.model_dump(mode="json"),
                "confidence": settings.confidence.model_dump(mode="json"),
            },
            "universal_unknowns": list(settings.confidence.universal_unknowns),
            "decisions": DECISIONS,
        }
        results.append(
            write_layer(
                table,
                OUTPUTS[mode],
                processed_dir,
                settings,
                sources=[],
                stats=stats,
                notes=_notes(settings),
            )
        )
    return results
