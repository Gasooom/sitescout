"""D-068: the spaced Top-by-score baseline and the radius robustness analysis.
SYNTHETIC data only."""

import hashlib

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from sitescout.evaluation import run_evaluation
from sitescout.features import run_features
from sitescout.optimize import run_network
from sitescout.radius_robustness import (
    RESULTS_FILE,
    analyse,
    ranked_eligible,
    render_radius_section,
    run_radius_robustness,
    select_spaced_top,
)
from sitescout.scoring import run_scores
from synthetic_features import feature_config, write_feature_world

CRS = "EPSG:32735"


def line(*km):
    """x coordinates in metres of points on a line, and y = 0."""
    x = np.array(km, dtype=np.float64) * 1000
    return x, np.zeros_like(x)


# --- The spaced selector ------------------------------------------------------------------------


def test_every_accepted_pair_is_at_least_the_spacing_apart():
    x, y = line(0, 3, 6, 9, 12, 15)
    chosen = select_spaced_top(range(6), x, y, 5000, 6)
    assert chosen == [0, 2, 4]  # 3 km and 9 km are too close to an accepted site, 15 km to 12 km
    for a in chosen:
        for b in chosen:
            assert a == b or abs(x[a] - x[b]) >= 5000


def test_a_site_exactly_the_spacing_away_is_accepted():
    x, y = line(0, 5)
    assert select_spaced_top([0, 1], x, y, 5000, 2) == [0, 1]
    assert select_spaced_top([0, 1], x, y, 5000.5, 2) == [0]


def test_the_ranking_is_walked_in_order_and_a_close_site_is_skipped_not_swapped():
    x, y = line(0, 1, 9)
    assert select_spaced_top([0, 1, 2], x, y, 5000, 3) == [0, 2]
    assert select_spaced_top([1, 0, 2], x, y, 5000, 3) == [1, 2]  # the higher-ranked site wins


def test_it_uses_straight_line_distance_in_both_axes():
    x = np.array([0.0, 3000.0])
    y = np.array([0.0, 4000.0])  # 5 km apart on the diagonal
    assert select_spaced_top([0, 1], x, y, 5000, 2) == [0, 1]
    assert select_spaced_top([0, 1], x, y, 5001, 2) == [0]


def test_it_stops_at_n_sites_even_when_more_would_fit():
    x, y = line(0, 10, 20, 30)
    assert select_spaced_top(range(4), x, y, 5000, 2) == [0, 1]


def test_it_returns_fewer_sites_when_the_ranking_runs_out_first():
    x, y = line(0, 1, 2, 3)
    chosen = select_spaced_top(range(4), x, y, 5000, 3)
    assert chosen == [0]  # stops cleanly with what it could place, never raises or pads
    assert select_spaced_top([], x, y, 5000, 3) == []


def test_the_same_inputs_always_give_the_same_selection():
    rng = np.random.default_rng(7)
    x, y = rng.uniform(0, 50_000, 40), rng.uniform(0, 50_000, 40)
    ranking = list(rng.permutation(40))
    first = select_spaced_top(ranking, x, y, 8000, 10)
    assert select_spaced_top(ranking, x, y, 8000, 10) == first
    assert select_spaced_top(list(ranking), x.copy(), y.copy(), 8000, 10) == first


def test_the_ranking_is_by_score_then_candidate_id_over_eligible_sites_only():
    table = pd.DataFrame({"candidate_id": ["c", "a", "b", "d"], "score": [50.0, 80.0, 80.0, 99.0]})
    assert ranked_eligible(table, np.array([0, 1, 2])) == [1, 2, 0]  # the 99 is not eligible


# --- The analysis --------------------------------------------------------------------------------


@pytest.fixture
def tiny(settings_data, weights_data):
    """Eight sites 6 km apart on a line, each with a demand node of its own, and no chargers."""
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 3})
    count = 8
    points = gpd.GeoSeries([shapely.Point(i * 6000, 0) for i in range(count)], crs=CRS)
    table = pd.DataFrame(
        {
            "candidate_id": [f"cand-{i}" for i in range(count)],
            "score": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0],
            "host_type": ["fuel"] * count,
            "province": ["North", "North", "South", "South", "East", "East", "West", "West"],
            "district": [f"d{i}" for i in range(count)],
        }
    )
    nodes = gpd.GeoDataFrame({"people": [10.0] * count}, geometry=list(points), crs=CRS)
    return config, table, points, nodes, gpd.GeoSeries([], crs=CRS)


def run(tiny, radii=(5000, 10000, 15000), spacings=(5000, 20000)):
    config, table, points, nodes, chargers = tiny
    return analyse(table, points, nodes, chargers, config, radii_m=radii, spacings_m=spacings)


def test_the_sensitivity_is_deterministic(tiny):
    assert run(tiny) == run(tiny)


def test_every_method_is_reported_at_every_radius(tiny):
    result = run(tiny)
    methods = {"mclp", "greedy", "top", "spaced_5000", "spaced_20000"}
    assert {(r["method"], r["radius_m"]) for r in result["rows"]} == {
        (m, radius) for m in methods for radius in (5000, 10000, 15000)
    }
    assert all(r["status"] == "Optimal" for r in result["rows"] if r["method"] == "mclp")


def test_the_selections_follow_the_definitions(tiny):
    rows = {(r["method"], r["radius_m"]): r for r in run(tiny)["rows"]}
    assert rows[("top", 10000)]["candidate_ids"] == ["cand-0", "cand-1", "cand-2"]
    # The four eligible sites are 6 km apart: 5 km spacing keeps the top three, 20 km only one.
    assert rows[("spaced_5000", 10000)]["candidate_ids"] == ["cand-0", "cand-1", "cand-2"]
    assert rows[("spaced_20000", 10000)]["sites"] == 1
    # Spacing leaves the score ranking intact: the baselines do not depend on the radius.
    assert rows[("top", 5000)]["candidate_ids"] == rows[("top", 15000)]["candidate_ids"]


def test_a_larger_radius_never_lets_the_optimum_cover_less(tiny):
    shares = [r["population_covered_share"] for r in run(tiny)["rows"] if r["method"] == "mclp"]
    assert shares == sorted(shares)


def test_without_chargers_the_down_weighting_zone_changes_nothing(tiny):
    check = run(tiny)["weight_zone_check"]
    assert {c["radius_m"] for c in check} == {5000, 15000}  # not the shipped 10 km
    assert all(c["sites_shared"] == 3 for c in check)
    assert all(c["share_zone_fixed"] == c["share_zone_follows"] for c in check)


# --- The report text -----------------------------------------------------------------------------


def row(method, radius, share, sites=30):
    return {
        "method": method,
        "radius_m": radius,
        "sites": sites,
        "population_covered_share": share,
        "provinces": 5,
        "districts": 20,
        "kigali_sites": 3,
        "mean_score": 65.0,
        "status": "Optimal" if method == "mclp" else None,
        "candidate_ids": [],
    }


def result(spaced_share):
    radii = [5000, 10000]
    rows = [
        row(m, r, share)
        for r in radii
        for m, share in (
            ("mclp", 0.5),
            ("greedy", 0.49),
            ("top", 0.25),
            ("spaced_5000", spaced_share),
        )
    ]
    return {
        "parameters": {
            "n_sites": 30, "service_radius_m": 10000, "eligible_sites": 108,
            "known_charging_sites": 5, "radii_m": radii, "spacing_m": [5000],
        },
        "rows": rows,
        "weight_zone_check": [],
        "reconciles_with_network": None,
    }  # fmt: skip


def test_the_section_says_so_when_optimized_stays_ahead_and_gives_the_gap():
    text = render_radius_section(result(0.46))
    assert "a gap of 4.0 percentage points" in text
    assert "Optimized covers more than every baseline at every radius" in text


def test_the_section_says_so_when_a_baseline_catches_up():
    text = render_radius_section(result(0.52))  # the spaced baseline beats Optimized
    assert "does not cover more than the strongest baseline at 5 km, 10 km" in text
    assert "Optimized covers more than every baseline" not in text


def test_a_baseline_with_fewer_sites_is_flagged_and_a_full_one_is_also_named():
    data = result(0.46)
    data["parameters"]["spacing_m"] = [5000, 10000]
    for radius in (5000, 10000):
        data["rows"].append(row("spaced_10000", radius, 0.48, sites=27))
    text = render_radius_section(data)
    assert "reaches only 27 of 30 sites" in text
    assert "strongest baseline with all 30 sites" in text
    assert "27 of 30" in text.split("\n\n")[1]  # the table says it too


def test_without_results_the_section_says_how_to_produce_them():
    assert "scripts/radius_robustness.py" in render_radius_section(None)


# --- The whole run on the SYNTHETIC world --------------------------------------------------------


@pytest.fixture
def world(settings_data, weights_data, dirs):
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 1})
    write_feature_world(dirs["processed"], config.settings)
    run_features(config.settings, dirs["processed"])
    run_scores(config.settings, config.weights, dirs["processed"])
    run_network(config, dirs["processed"])
    return config, dirs["processed"]


def test_the_run_reproduces_the_shipped_selections_and_is_repeatable(world):
    config, processed = world
    first = run_radius_robustness(config, processed)
    assert first["reconciles_with_network"] is True  # checked against network.json at 10 km
    digest = hashlib.sha256((processed / RESULTS_FILE).read_bytes()).hexdigest()
    run_radius_robustness(config, processed)
    assert hashlib.sha256((processed / RESULTS_FILE).read_bytes()).hexdigest() == digest


def test_the_evaluation_report_gains_section_f(world, dirs):
    config, processed = world
    run_radius_robustness(config, processed)
    report = dirs["work"] / "evaluation.md"
    run_evaluation(config, processed, report)
    text = report.read_text(encoding="utf-8")
    assert "## F. Radius robustness and a stronger baseline" in text
    assert "| Method | Radius | Population within radius |" in text
    assert "Top by score, at least 5 km apart" in text


def test_the_report_without_the_analysis_says_how_to_run_it(world, dirs):
    config, processed = world
    report = dirs["work"] / "evaluation.md"
    run_evaluation(config, processed, report)
    assert "scripts/radius_robustness.py" in report.read_text(encoding="utf-8")
