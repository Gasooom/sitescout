"""Scoring (SPEC §5; D-039, D-040, D-042): percentiles, components, bonuses, profile, score."""

import numpy as np
import pytest

from sitescout.config import load_config
from sitescout.ingest.layers import FEATURES_PRODUCTION, SCORE_COMPONENTS, WEIGHTED_FEATURES
from sitescout.scoring import (
    CALCULATED,
    UNKNOWN,
    ScoreError,
    assign_profile,
    check_scores,
    feature_points,
    percentile_rank,
    score_features,
)
from synthetic_scores import WEIGHTED, feature_table


@pytest.fixture(scope="module")
def config():
    return load_config()


def _score(config, table):
    return score_features(table, config.settings, config.weights).set_index("candidate_id")


# --- Percentile points --------------------------------------------------------------------


def test_percentile_by_hand():
    # 10: 0 below, 1 equal -> 12.5; 20: 1 below, 2 equal -> 50; 30: 3 below -> 87.5.
    assert percentile_rank(np.array([10, 20, 20, 30])).tolist() == [12.5, 50.0, 50.0, 87.5]


def test_ties_zeros_constants_and_one_candidate():
    assert percentile_rank(np.array([0.0, 0.0, 5.0])).tolist() == pytest.approx(
        [100 / 3, 100 / 3, 250 / 3]
    )
    assert percentile_rank(np.array([7.0, 7.0, 7.0])).tolist() == [50.0, 50.0, 50.0]
    assert percentile_rank(np.array([42.0])).tolist() == [50.0]


def test_missing_values_are_left_out_of_the_reference():
    result = percentile_rank(np.array([1.0, np.nan, 3.0]))
    assert result[0] == 25.0 and result[2] == 75.0 and np.isnan(result[1])
    assert np.isnan(percentile_rank(np.array([np.nan, np.nan]))).all()


def test_log1p_changes_no_percentile():
    rng = np.random.default_rng(0)
    for _ in range(20):
        counts = rng.integers(0, 2000, size=300).astype(np.float64)
        counts[rng.random(300) < 0.3] = 0  # many zeros and ties, as in the real counts
        assert np.array_equal(percentile_rank(counts), percentile_rank(np.log1p(counts)))


def test_lower_is_better_features_are_inverted(config):
    points = feature_points(
        feature_table(3, dist_road_m=[10.0, 20.0, 30.0]), config.settings, config.weights
    )
    assert points["dist_road_m"].tolist() == pytest.approx([500 / 6, 50.0, 100 / 6])
    assert set(config.settings.scoring.lower_is_better) == {
        "dist_road_m",
        "dist_trunk_m",
        "dist_substation_m",
        "dist_line_m",
        "chargers_10km",
        "chargers_25km",
    }


def test_a_larger_charging_gap_scores_higher(config):
    table = feature_table(3, dist_charger_m=[1_000.0, 5_000.0, 20_000.0])
    points = feature_points(table, config.settings, config.weights)
    assert points["dist_charger_m"].is_monotonic_increasing


def test_a_missing_value_never_raises_a_score(config):
    # dist_substation_m is lower-is-better: inverting must not turn a missing value into 100.
    table = feature_table(
        3, dist_substation_m=[np.nan, 1_000.0, 2_000.0], dist_charger_m=[np.nan] * 3
    )
    points = feature_points(table, config.settings, config.weights)
    assert points.loc[0, "dist_substation_m"] == 0.0
    assert (points["dist_charger_m"] == 0.0).all()
    assert (points.to_numpy() >= 0).all() and (points.to_numpy() <= 100).all()


# --- Components, bonuses and grid evidence ---------------------------------------------------


def test_components_are_the_weighted_percentiles(config):
    table = feature_table(4)
    scores = _score(config, table)
    points = feature_points(table, config.settings, config.weights)
    row = 2
    expected = (
        0.5 * points.loc[row, "pop_5km"]
        + 0.25 * points.loc[row, "pop_1km"]
        + 0.25 * points.loc[row, "pop_10km"]
    )
    assert scores.iloc[row]["component_demand"] == pytest.approx(expected)
    expected_gap = (
        0.5 * points.loc[row, "dist_charger_m"]
        + 0.25 * points.loc[row, "chargers_10km"]
        + 0.25 * points.loc[row, "chargers_25km"]
    )
    assert scores.iloc[row]["component_charging_gap"] == pytest.approx(expected_gap)


@pytest.mark.parametrize(
    ("road_class", "bonus"),
    [("trunk", 10), ("primary", 5), ("trunk_link", 0), ("primary_link", 0), ("residential", 0)],
)
def test_road_bonus_uses_the_exact_class(config, road_class, bonus):
    scores = _score(config, feature_table(2, road_class=[road_class, "service"]))
    assert scores.iloc[0]["road_bonus"] == bonus
    assert scores.iloc[1]["road_bonus"] == 0


def test_host_bonus_follows_the_spec_table(config):
    kinds = ["fuel", "mall", "supermarket", "logistics", "hotel", "industrial", "none"]
    scores = _score(config, feature_table(7, host_type=kinds))
    assert scores["host_bonus"].tolist() == [10, 10, 5, 5, 5, 0, 0]


def test_components_are_capped_at_100(config):
    # Among 200 candidates the best percentile is 100 x 199.5 / 200 = 99.75; with a
    # bonus of 10 the access and host components would be 109.75, and are capped at 100.
    order = np.arange(200, dtype=np.float64)
    table = feature_table(
        200, dist_road_m=order, dist_trunk_m=order, poi_1km=200 - order, poi_3km=200 - order
    )
    scores = _score(config, table)
    assert scores.iloc[0]["pct_dist_road_m"] == 99.75
    assert scores.iloc[0]["component_access"] == 100.0
    assert scores.iloc[0]["component_host_commercial"] == 100.0


def test_missing_grid_evidence_scores_zero_and_is_unknown(config):
    table = feature_table(
        4,
        dist_substation_m=[5_000.0, 5_000.01, np.nan, 9_000.0],
        dist_line_m=[9_000.0, 9_000.0, np.nan, 5_000.0],
    )
    scores = _score(config, table)
    # Within 5 km (inclusive) of a substation or a line: present. Neither: missing.
    assert scores["grid_evidence_status"].tolist() == [CALCULATED, UNKNOWN, UNKNOWN, CALCULATED]
    assert scores.iloc[1]["component_grid_evidence"] == 0.0
    assert scores.iloc[2]["component_grid_evidence"] == 0.0


# --- Profile, score and rank -----------------------------------------------------------------


def test_profile_boundaries_are_inclusive(config):
    table = feature_table(
        5,
        dist_kigali_cbd_m=[10_000.0, 10_000.01, 50_000.0, 50_000.0, np.nan],
        dist_town_m=[9_000.0, 9_000.0, 3_000.0, 3_000.01, np.nan],
    )
    profile = assign_profile(table, config.settings).tolist()
    assert profile == ["urban", "corridor", "urban", "corridor", "corridor"]


def test_profile_distances_are_never_scored(config):
    near = _score(config, feature_table(3, dist_town_m=[100.0, 200.0, 300.0]))
    far = _score(config, feature_table(3, dist_town_m=[2_900.0, 2_000.0, 1_000.0]))
    assert near["score"].tolist() == far["score"].tolist()  # same profile, same score


def test_score_uses_the_profiles_component_weights(config):
    table = feature_table(2, dist_town_m=[1_000.0, 8_000.0])  # urban, corridor
    scores = _score(config, table)
    profiles = config.weights.profiles
    for row, weights in ((0, profiles.urban), (1, profiles.corridor)):
        expected = sum(
            getattr(weights, name) * scores.iloc[row][f"component_{name}"]
            for name in SCORE_COMPONENTS
        )
        assert scores.iloc[row]["score"] == pytest.approx(expected)
    assert scores["profile"].tolist() == ["urban", "corridor"]


def test_rank_orders_by_score_then_candidate_id(config):
    table = feature_table(3)
    for name in WEIGHTED:
        table[name] = 1.0  # every candidate identical
    scores = _score(config, table)
    assert scores["score"].nunique() == 1
    assert scores["rank"].tolist() == [1, 2, 3]  # cand-00, cand-01, cand-02


def test_one_candidate(config):
    scores = _score(config, feature_table(1))
    for name in WEIGHTED:
        assert scores.iloc[0][f"pct_{name}"] == 50.0
    assert scores.iloc[0]["rank"] == 1


def test_scores_stay_between_0_and_100(config):
    rng = np.random.default_rng(1)
    table = feature_table(200)
    for name in WEIGHTED:
        values = rng.gamma(0.5, 1_000.0, 200)
        values[rng.random(200) < 0.1] = np.nan
        table[name] = values
    table["poi_3km"] = rng.integers(0, 5, 200)
    scores = _score(config, table)
    check_scores(scores.reset_index(), "production", config.settings)
    columns = ["score", *(c for c in scores.columns if c.startswith(("component_", "pct_")))]
    assert (scores[columns].to_numpy() >= 0).all() and (scores[columns].to_numpy() <= 100).all()


def test_backtest_charging_gap_is_25_for_every_candidate(config):
    # No chargers: dist_charger_m missing -> 0; both counts constant 0 -> 50, inverted 50.
    table = feature_table(
        4,
        dist_charger_m=[np.nan] * 4,
        chargers_10km=[0] * 4,
        chargers_25km=[0] * 4,
        dist_town_m=[1_000.0, 1_000.0, 8_000.0, 8_000.0],
    )
    scores = _score(config, table)
    assert (scores["component_charging_gap"] == 25.0).all()
    urban, corridor = config.weights.profiles.urban, config.weights.profiles.corridor
    assert urban.charging_gap * 25 == pytest.approx(3.75)
    assert corridor.charging_gap * 25 == pytest.approx(6.25)
    check_scores(scores.reset_index(), "backtest", config.settings)


def test_a_backtest_charging_gap_that_varies_is_refused(config):
    table = feature_table(3, dist_charger_m=[3_000.0, 1_000.0, 2_000.0])
    scores = _score(config, table).reset_index()
    assert scores["component_charging_gap"].nunique() > 1
    with pytest.raises(ScoreError, match="backtest charging-gap component differs"):
        check_scores(scores, "backtest", config.settings)


def test_the_schema_covers_every_weighted_feature_and_component(config):
    assert set(WEIGHTED_FEATURES) == set(config.weights.weighted_features()) == set(WEIGHTED)
    assert tuple(type(config.weights.components).model_fields) == SCORE_COMPONENTS
    assert set(WEIGHTED_FEATURES) <= set(FEATURES_PRODUCTION.column_names)
