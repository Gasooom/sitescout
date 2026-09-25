"""Confidence levels and reasons (SPEC §6, D-041)."""

import numpy as np
import pytest

from sitescout.confidence import NO_FACTOR, assess, universal_unknowns
from sitescout.config import load_config
from sitescout.scoring import grid_evidence_missing, score_features
from synthetic_scores import feature_table


@pytest.fixture(scope="module")
def settings():
    return load_config().settings


def _levels(settings, table):
    return assess(table, grid_evidence_missing(table, settings), settings)


def test_the_number_of_factors_sets_the_level(settings):
    # Rows: none; sparse; no host; remote; sparse + no host; all three.
    table = feature_table(
        6,
        grid_completeness_ratio=[1.0, 0.3, 1.0, 1.0, 0.3, 0.3],
        host_type=["fuel", "fuel", "none", "fuel", "none", "none"],
        poi_3km=[5, 5, 5, 0, 5, 0],
    )
    levels, _ = _levels(settings, table)
    assert levels.tolist() == ["High", "Medium", "Medium", "Medium", "Low", "Low"]


def test_missing_grid_evidence_is_low_whatever_else_holds(settings):
    table = feature_table(2, dist_substation_m=[9_000.0, np.nan], dist_line_m=[8_000.0, np.nan])
    levels, reasons = _levels(settings, table)
    assert levels.tolist() == ["Low", "Low"]  # no other factor holds
    assert all(reason.startswith("Grid evidence missing") for reason in reasons)


@pytest.mark.parametrize(
    ("ratio", "sparse"), [(0.5, False), (0.4999, True), (0.0, True), (2.0, False)]
)
def test_sparse_coverage_is_strictly_below_the_threshold(settings, ratio, sparse):
    levels, _ = _levels(settings, feature_table(1, grid_completeness_ratio=[ratio]))
    assert levels[0] == ("Medium" if sparse else "High")


@pytest.mark.parametrize(("poi_3km", "remote"), [(0, True), (1, False)])
def test_remote_means_no_mapped_poi_within_3_km(settings, poi_3km, remote):
    levels, _ = _levels(settings, feature_table(1, poi_3km=[poi_3km]))
    assert levels[0] == ("Medium" if remote else "High")


def test_reasons_list_every_factor_that_holds(settings):
    table = feature_table(
        2,
        grid_completeness_ratio=[1.0, 0.1],
        host_type=["fuel", "none"],
        poi_3km=[5, 0],
        dist_substation_m=[1_000.0, 9_000.0],
        dist_line_m=[1_000.0, 9_000.0],
    )
    _, reasons = _levels(settings, table)
    assert reasons[0] == NO_FACTOR
    parts = reasons[1].split("; ")
    assert parts == [
        "Grid evidence missing: no mapped substation or power line within 5 km",
        "Sparse public-map coverage in the district (grid completeness ratio below 0.5)",
        "No identified host (a corridor point)",
        "Remote location: at most 0 mapped POIs within 3 km to cross-check",
    ]


def test_confidence_is_a_level_never_a_percentage(settings):
    scores = score_features(feature_table(5), settings, load_config().weights)
    assert set(scores["confidence"]) <= {"High", "Medium", "Low"}


def test_universal_unknowns_are_the_same_fixed_list_for_every_site(settings):
    scores = score_features(feature_table(3), settings, load_config().weights)
    expected = (
        "grid connection capacity; transformer capacity; land availability; "
        "landowner willingness; permit requirements"
    )
    assert universal_unknowns(settings) == expected
    assert (scores["universal_unknowns"] == expected).all()
