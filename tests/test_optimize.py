"""Network selection: exact MCLP, greedy and Top-30 (SPEC §8; D-046). SYNTHETIC data only."""

import hashlib
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from sitescout.evaluation import run_evaluation
from sitescout.features import run_features
from sitescout.ingest.layers import layer_path, metadata_path, read_layer
from sitescout.optimize import (
    NetworkError,
    Problem,
    build_problem,
    coverage,
    demand_weights,
    marginal_coverage,
    objective,
    run_network,
    solve_greedy,
    solve_mclp,
)
from sitescout.scoring import run_scores
from synthetic_features import feature_config, write_feature_world

CRS = "EPSG:32735"


def _problem(cover, weights, n, conflicts=(), scores=None, lam=0.0):
    count = len(cover)
    return Problem(
        eligible=np.arange(count),
        cover=[np.array(nodes, dtype=np.int64) for nodes in cover],
        weights=np.asarray(weights, dtype=np.float64),
        site_score=np.asarray(scores if scores is not None else [0.5] * count, dtype=np.float64),
        lam=lam,
        n_sites=n,
        conflicts=list(conflicts),
    )


# Six nodes of equal weight. X covers 4 of them, A and B 3 each and do not overlap: greedy
# takes X first and ends at 5/6; the exact MCLP takes A and B and covers everything.
TRAP = _problem(cover=[[0, 1, 2], [3, 4, 5], [1, 2, 3, 4]], weights=[1 / 6] * 6, n=2)


def test_the_exact_mclp_beats_greedy_where_greedy_is_trapped():
    exact, run = solve_mclp(TRAP, 60)
    assert exact == [0, 1]
    assert run["status"] == "Optimal"
    assert objective(exact, TRAP) == pytest.approx(1.0)
    greedy = solve_greedy(TRAP)
    assert 2 in greedy
    assert objective(greedy, TRAP) == pytest.approx(5 / 6)


def test_exactly_n_sites_are_selected():
    problem = _problem(cover=[[i] for i in range(8)], weights=[1 / 8] * 8, n=5)
    assert len(solve_mclp(problem, 60)[0]) == 5
    assert len(solve_greedy(problem)) == 5


def test_sites_closer_than_the_spacing_are_never_both_selected():
    # Sites 0 and 1 cover the heaviest nodes but conflict: only one of them may be chosen.
    problem = _problem(cover=[[0], [1], [2]], weights=[0.45, 0.45, 0.1], n=2, conflicts=[(0, 1)])
    for chosen in (solve_mclp(problem, 60)[0], solve_greedy(problem)):
        assert not {0, 1} <= set(chosen)
        assert 2 in chosen


def test_an_infeasible_network_stops_clearly():
    problem = _problem(cover=[[0], [1]], weights=[0.5, 0.5], n=2, conflicts=[(0, 1)])
    with pytest.raises(NetworkError, match="no solution"):
        solve_mclp(problem, 60)
    with pytest.raises(NetworkError, match="respect the spacing"):
        solve_greedy(problem)


def test_lambda_breaks_ties_by_score():
    problem = _problem(cover=[[0], [0]], weights=[1.0], n=1, scores=[0.4, 0.9], lam=0.01)
    assert solve_mclp(problem, 60)[0] == [1]
    assert solve_greedy(problem) == [1]


def test_marginal_coverage():
    # Selected A (nodes 0-2) and B (3-5); X (1-4) is not selected and would add nothing.
    marginal = marginal_coverage([0, 1], TRAP, 3)
    assert marginal.tolist() == pytest.approx([0.5, 0.5, 0.0])
    alone = marginal_coverage([2], TRAP, 3)
    assert alone.tolist() == pytest.approx([1 / 6, 1 / 6, 4 / 6])


def test_demand_weights_sum_to_1_and_down_weight_near_chargers():
    nodes = gpd.GeoDataFrame(
        {"people": [100.0, 100.0]},
        geometry=[shapely.Point(0, 0), shapely.Point(50_000, 0)],
        crs=CRS,
    )
    charger = gpd.GeoSeries([shapely.Point(1_000, 0)], crs=CRS)
    weights = demand_weights(nodes, charger, 10_000, 0.5)
    assert weights.sum() == pytest.approx(1.0)
    assert weights.tolist() == pytest.approx([50 / 150, 100 / 150])


def test_coverage_radius_is_inclusive():
    nodes = gpd.GeoDataFrame(
        {"people": [1.0, 1.0]},
        geometry=[shapely.Point(10_000, 0), shapely.Point(10_000.5, 0)],
        crs=CRS,
    )
    sites = gpd.GeoSeries([shapely.Point(0, 0)], crs=CRS)
    assert coverage(sites, nodes, 10_000)[0].tolist() == [0]


def test_eligibility_keeps_the_top_half_with_a_host(settings_data, weights_data):
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 2})
    table = pd.DataFrame(
        {
            "score": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0],
            "host_type": ["fuel", "none", "hotel", "mall", "fuel", "fuel"],
        }
    )
    points = gpd.GeoSeries([shapely.Point(i * 10_000, 0) for i in range(6)], crs=CRS)
    nodes = gpd.GeoDataFrame({"people": [1.0]}, geometry=[shapely.Point(0, 0)], crs=CRS)
    none = gpd.GeoSeries([], crs=CRS)
    problem = build_problem(table, points, nodes, none, config, lam=0.0, radius_m=1, factor=1)
    # Top half by score: 90, 80, 70; the corridor point (80) has no host and is dropped.
    assert problem.eligible.tolist() == [0, 2]
    three = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 3})
    with pytest.raises(NetworkError, match="Only 2 eligible sites"):
        build_problem(table, points, nodes, none, three, lam=0.0, radius_m=1, factor=1)


# --- The whole run on the SYNTHETIC world --------------------------------------------------------


@pytest.fixture
def world(settings_data, weights_data, dirs):
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 1})
    write_feature_world(dirs["processed"], config.settings)
    run_features(config.settings, dirs["processed"])
    run_scores(config.settings, config.weights, dirs["processed"])
    return config, dirs["processed"]


def test_the_network_layer_and_summary(world):
    config, processed = world
    summary = run_network(config, processed)
    layer = read_layer("network", processed, config.settings)
    assert len(layer) == 3
    for name in ("selected_mclp", "selected_greedy", "selected_top30"):
        assert layer[name].sum() == 1
        assert not (layer[name] & ~layer["eligible"]).any()
    assert not (layer["eligible"] & (layer["host_type"] == "none")).any()
    assert summary["solver"]["status"] == "Optimal"
    assert set(summary["selections"]) == {"mclp", "greedy", "top30"}
    assert len(summary["sensitivity"]) == 6  # two non-default values per parameter
    run = json.loads((processed / "network_run.json").read_text(encoding="utf-8"))
    assert "seconds" in run["base"]


def test_repeated_runs_give_identical_layer_and_summary(world):
    config, processed = world

    def digests():
        paths = (layer_path(processed, "network"), metadata_path(processed, "network"))
        return [
            hashlib.sha256(p.read_bytes()).hexdigest() for p in (*paths, processed / "network.json")
        ]

    run_network(config, processed)
    first = digests()
    run_network(config, processed)
    assert digests() == first


def test_the_evaluation_report_gains_the_network_section(world, dirs):
    config, processed = world
    run_network(config, processed)
    report = dirs["work"] / "evaluation.md"
    run_evaluation(config, processed, report)
    text = report.read_text(encoding="utf-8")
    assert "| | Exact MCLP | Greedy | Top-1 by score |" in text
    assert "Exact-versus-greedy gap" in text
    assert "which includes the λ score term" in text
    assert "not the difference between the population-coverage percentages" in text
    assert "Changing the existing-charger factor" in text
