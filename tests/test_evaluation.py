"""The retrospective plausibility test (SPEC §7; D-043, D-044)."""

import hashlib
import json

import numpy as np
import pytest

from sitescout.evaluation import (
    bootstrap,
    metrics,
    random_baseline,
    ranking,
    render_report,
    run_evaluation,
)
from sitescout.features import run_features
from sitescout.scoring import run_scores
from synthetic_features import feature_config, write_feature_world

KS, RECALL_AT = (10, 20, 30), 30


@pytest.fixture
def fconfig(settings_data, weights_data):
    # The SYNTHETIC world has 3 candidates: compare its Top-2 in the stability test.
    return feature_config(settings_data, weights_data, **{"settings.evaluation.stability.top_k": 2})


# --- Metrics and baselines ---------------------------------------------------------------


def test_precision_and_recall_by_hand():
    ranked = np.zeros(40, dtype=bool)
    ranked[[0, 12, 25, 35]] = True  # hits at ranks 1, 13, 26 and 36
    result = metrics(ranked, KS, RECALL_AT)
    assert result == {
        "precision_at_10": 0.1,
        "precision_at_20": 0.1,
        "precision_at_30": 0.1,
        "recall_at_30": 0.75,
    }


def test_recall_without_any_hit_is_undefined():
    assert np.isnan(metrics(np.zeros(40, dtype=bool), KS, RECALL_AT)["recall_at_30"])


def test_ranking_orders_by_score_then_id():
    order = ranking(np.array([1.0, 3.0, 3.0, 2.0]), np.array(["d", "c", "b", "a"]))
    assert order.tolist() == [2, 1, 3, 0]  # b, c (tied at 3), then a, d


def test_the_random_baseline_is_seeded_and_near_the_hit_rate(fconfig):
    hits = np.zeros(300, dtype=bool)
    hits[:15] = True  # 5% of candidates are hits
    first = random_baseline(hits, fconfig)
    assert first == random_baseline(hits, fconfig)
    assert first["precision_at_30"]["mean"] == pytest.approx(0.05, abs=0.01)
    assert first["recall_at_30"]["mean"] == pytest.approx(0.10, abs=0.02)


def test_bootstrap_is_seeded_and_brackets_the_estimate(fconfig):
    rng = np.random.default_rng(0)
    hits = rng.random(300) < 0.05
    scores = {"sitescout": hits + rng.random(300), "population_only": rng.random(300)}
    ids = np.array([f"c{i:03d}" for i in range(300)])
    first = bootstrap(scores, ids, hits, fconfig)
    assert first == bootstrap(scores, ids, hits, fconfig)
    low, high = first["sitescout"]["precision_at_30"]
    estimate = metrics(hits[ranking(scores["sitescout"], ids)], KS, RECALL_AT)["precision_at_30"]
    assert low <= estimate <= high
    assert set(first) == {"sitescout", "population_only", "sitescout_minus_population"}


# --- The whole evaluation on the SYNTHETIC world ------------------------------------------------


@pytest.fixture
def evaluated(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)
    run_features(fconfig.settings, dirs["processed"])
    run_scores(fconfig.settings, fconfig.weights, dirs["processed"])
    report = dirs["work"] / "evaluation.md"
    return run_evaluation(fconfig, dirs["processed"], report), report


def test_hits_are_candidates_within_1_km_of_a_known_site(evaluated):
    results, _ = evaluated
    a = results["backtest"]
    # Sites: the merged node/13 group, the socket-tagged fuel station and the area charger.
    assert a["known_charging_sites"] == 3
    assert a["hit_candidate_ids"] == ["cand-a"]  # 200 m from the node/13 site
    assert a["charger_sources"]["chargers_manual"]["status"].startswith("missing")


def test_stability_perturbs_every_weight_both_ways(fconfig, evaluated):
    stability = evaluated[0]["stability"]
    weights = len(fconfig.weights.weighted_features()) + 2 * 5  # features + 2 profiles x 5
    assert len(stability["runs"]) == 2 * weights == 44
    assert {run["factor"] for run in stability["runs"]} == {0.8, 1.2}
    assert all(0 <= run["overlap"] <= 1 for run in stability["runs"])


def test_the_report_is_filled_from_the_results(fconfig, evaluated):
    results, report = evaluated
    text = report.read_text(encoding="utf-8")
    assert text == render_report(results, fconfig)
    assert "retrospective plausibility test" in text
    assert "accuracy" not in text.lower()
    assert "Population-only" in text and "Random (mean of 1000 seeds)" in text


def test_repeated_runs_give_identical_files(fconfig, dirs, evaluated):
    _, report = evaluated
    json_path = dirs["processed"] / "evaluation.json"
    first = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (report, json_path)]
    run_evaluation(fconfig, dirs["processed"], report)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in (report, json_path)] == first
    json.loads(json_path.read_text(encoding="utf-8"))  # valid JSON, no NaN
