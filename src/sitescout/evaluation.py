"""Milestone 5: the retrospective plausibility test (SPEC §7; D-043, D-044).

- **A. Backtest** on ``scores_backtest``: a hit is a candidate within
  ``evaluation.backtest.hit_radius_m`` (1 km, inclusive) of a known charging site (the
  production charger set of D-034). Precision@k is the share of hits among the top k;
  Recall@30 the share of all hit candidates that are in the top 30. SiteScout's ranking is
  compared with a population-only ranking (``pop_5km``) and with random rankings (the mean
  of 1,000 seeds). Bootstrap intervals resample the candidates.
- **B. Weight stability**: each feature weight and profile weight in turn is multiplied by
  0.8 and by 1.2, its group renormalised to sum to 1, and the Top-30 compared with the
  unperturbed Top-30 (production mode).
- **D. Data quality**: missing values, invalid coordinates, duplicates and grid-layer
  completeness by district.

Every number is computed here and written to ``data/processed/evaluation.json``; the
report ``reports/evaluation.md`` is filled from a template with those values. Randomness
uses fixed seeds only. Sections C (network) and E (grounding) come with Milestones 6 and 7.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import Config, build_config, require
from sitescout.crs import to_metric
from sitescout.features.chargers import production_chargers
from sitescout.features.nearest import count_within
from sitescout.ingest.layers import metadata_path, read_layer
from sitescout.ingest.metadata import read_json, write_atomically, write_json
from sitescout.scoring import score_features

logger = logging.getLogger(__name__)

RESULTS_FILE = "evaluation.json"
TEMPLATE = Path(__file__).parent / "templates" / "evaluation.md"
POPULATION_FEATURE = "pop_5km"


class EvaluationError(Exception):
    """The evaluation cannot be computed."""


# --- A. Backtest -------------------------------------------------------------------------


def known_charging_sites(processed_dir: Path, config: Config) -> tuple[gpd.GeoSeries, dict]:
    """The known charging sites (metric points) and how they were built (D-034)."""
    settings = config.settings
    layers = {
        name: read_layer(name, processed_dir, settings)
        for name in ("osm_charging_stations", "osm_pois")
    }
    manual_meta = read_json(metadata_path(processed_dir, "chargers_manual"))
    if manual_meta.get("status") == "missing":
        manual, status = None, f"missing: {manual_meta.get('reason')}"
    else:
        manual, status = read_layer("chargers_manual", processed_dir, settings), "ok"
    chargers = production_chargers(layers, manual, status, settings)
    return chargers.sites, chargers.stats


def ranking(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Positions from best to worst: highest score first, equal scores by id."""
    return np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))


def metrics(ranked_hits: np.ndarray, ks: tuple[int, ...], recall_at: int) -> dict[str, float]:
    """Precision@k for each k and Recall@recall_at, for hits listed in ranked order."""
    ranked_hits = np.asarray(ranked_hits, dtype=bool)
    total = int(ranked_hits.sum())
    result = {f"precision_at_{k}": float(ranked_hits[:k].sum() / k) for k in ks}
    result[f"recall_at_{recall_at}"] = (
        float(ranked_hits[:recall_at].sum() / total) if total else float("nan")
    )
    return result


def random_baseline(hits: np.ndarray, config: Config) -> dict[str, dict[str, float]]:
    """Mean (and 2.5 / 97.5 percentiles) of the metrics over seeded random rankings."""
    evaluation = config.settings.evaluation
    seed = require(evaluation.random_seed, "evaluation.random_seed")
    ks, recall_at = evaluation.backtest.precision_at, evaluation.backtest.recall_at
    runs = [
        metrics(hits[np.random.default_rng(seed + i).permutation(len(hits))], ks, recall_at)
        for i in range(evaluation.random_baseline_seeds)
    ]
    table = pd.DataFrame(runs)
    return {
        name: {
            "mean": float(table[name].mean()),
            "p2_5": float(table[name].quantile(0.025)),
            "p97_5": float(table[name].quantile(0.975)),
        }
        for name in table.columns
    }


def bootstrap(
    rankings: dict[str, np.ndarray], ids: np.ndarray, hits: np.ndarray, config: Config
) -> dict[str, dict[str, list[float]]]:
    """Bootstrap intervals of each method's metrics, and of SiteScout minus population-only.

    Each resample draws the candidates with replacement; every method is re-ranked on the
    same resample, so the difference between methods is measured on identical data.
    ``rankings`` maps a method to its scores (higher is better).
    """
    evaluation = config.settings.evaluation
    seed = require(evaluation.random_seed, "evaluation.random_seed")
    resamples = require(evaluation.bootstrap.resamples, "evaluation.bootstrap.resamples")
    level = require(evaluation.bootstrap.confidence_level, "evaluation.bootstrap.confidence_level")
    ks, recall_at = evaluation.backtest.precision_at, evaluation.backtest.recall_at
    rng = np.random.default_rng(seed)
    samples: dict[str, list[dict[str, float]]] = {name: [] for name in rankings}
    for _ in range(resamples):
        draw = rng.integers(0, len(hits), len(hits))
        for name, scores in rankings.items():
            order = np.lexsort((np.arange(len(draw)), ids[draw], -scores[draw]))
            samples[name].append(metrics(hits[draw][order], ks, recall_at))
    low, high = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    tables = {name: pd.DataFrame(rows) for name, rows in samples.items()}
    tables["sitescout_minus_population"] = tables["sitescout"] - tables["population_only"]
    return {
        name: {
            column: [
                float(np.nanpercentile(table[column], low)),
                float(np.nanpercentile(table[column], high)),
            ]
            for column in table.columns
        }
        for name, table in tables.items()
    }


def backtest(processed_dir: Path, config: Config) -> dict[str, Any]:
    """Section A: hits, metrics, baselines and bootstrap intervals."""
    settings = config.settings
    scores = read_layer("scores_backtest", processed_dir, settings)
    features = read_layer("features_backtest", processed_dir, settings)
    features = features.set_index("candidate_id").loc[scores["candidate_id"]].reset_index()
    sites, charger_stats = known_charging_sites(processed_dir, config)
    points = to_metric(scores.geometry, settings.crs).reset_index(drop=True)
    radius = settings.evaluation.backtest.hit_radius_m
    hits = count_within(points, sites, radius) > 0
    ids = scores["candidate_id"].to_numpy()
    ks, recall_at = (
        settings.evaluation.backtest.precision_at,
        settings.evaluation.backtest.recall_at,
    )
    rankings = {
        "sitescout": scores["score"].to_numpy(dtype=np.float64),
        "population_only": features[POPULATION_FEATURE].to_numpy(dtype=np.float64),
    }
    point = {
        name: metrics(hits[ranking(values, ids)], ks, recall_at)
        for name, values in rankings.items()
    }
    top = ranking(rankings["sitescout"], ids)[: max(ks)]
    return {
        "known_charging_sites": int(len(sites)),
        "charger_sources": charger_stats["sources"],
        "hit_radius_m": radius,
        "candidates": len(ids),
        "hit_candidates": int(hits.sum()),
        "hit_candidate_ids": sorted(ids[hits].tolist()),
        "hits_in_sitescout_top": sorted(ids[top][hits[top]].tolist()),
        "metrics": point,
        "random": random_baseline(hits, config),
        "bootstrap": bootstrap(rankings, ids, hits, config),
    }


# --- B. Weight stability -----------------------------------------------------------------


def weight_paths(config: Config) -> list[tuple[str, list[str]]]:
    """(group path, weight names) for every group of weights that sums to 1."""
    weights = config.snapshot()["weights"]
    groups = [(f"components.{name}", list(group)) for name, group in weights["components"].items()]
    groups += [(f"profiles.{name}", list(group)) for name, group in weights["profiles"].items()]
    return groups


def stability(processed_dir: Path, config: Config) -> dict[str, Any]:
    """Section B: Top-k overlap after perturbing each weight by +/- the configured share."""
    rules = config.settings.evaluation.stability
    features = read_layer("features_production", processed_dir, config.settings)
    snapshot = config.snapshot()

    def top(weights_config: Config) -> set[str]:
        table = score_features(features, weights_config.settings, weights_config.weights)
        return set(table.nsmallest(rules.top_k, "rank")["candidate_id"])

    baseline = top(config)
    runs = []
    for path, names in weight_paths(config):
        group = snapshot["weights"]
        for part in path.split("."):
            group = group[part]
        for name in names:
            for factor in (1 - rules.perturbation, 1 + rules.perturbation):
                changed = {key: float(value) for key, value in group.items()}
                changed[name] *= factor
                if rules.renormalize:
                    total = sum(changed.values())
                    changed = {key: value / total for key, value in changed.items()}
                overrides = {f"weights.{path}.{key}": value for key, value in changed.items()}
                perturbed = build_config(
                    snapshot["settings"], snapshot["weights"], overrides=overrides
                )
                overlap = len(baseline & top(perturbed)) / rules.top_k
                runs.append(
                    {"weight": f"{path}.{name}", "factor": round(factor, 6), "overlap": overlap}
                )
    overlaps = np.array([run["overlap"] for run in runs])
    return {
        "perturbation": rules.perturbation,
        "top_k": rules.top_k,
        "target": rules.min_overlap,
        "runs": runs,
        "min_overlap": float(overlaps.min()),
        "mean_overlap": float(overlaps.mean()),
        "runs_below_target": int((overlaps < rules.min_overlap).sum()),
    }


# --- D. Data quality ---------------------------------------------------------------------


def data_quality(processed_dir: Path, config: Config) -> dict[str, Any]:
    """Section D: missing values, invalid coordinates, duplicates, grid completeness."""
    settings = config.settings
    candidates = read_layer("candidates", processed_dir, settings)
    features = read_layer("features_production", processed_dir, settings)
    bbox = settings.ingest.rwanda_bbox
    outside = ~(
        candidates["lon"].between(bbox.min_lon, bbox.max_lon)
        & candidates["lat"].between(bbox.min_lat, bbox.max_lat)
    )
    coordinates = (
        candidates["lat"].round(6).astype(str) + "," + candidates["lon"].round(6).astype(str)
    )
    missing = {
        name: int(count)
        for name, count in features.drop(columns="geometry").isna().sum().items()
        if count
    }
    meta = read_json(metadata_path(processed_dir, "features_production"))["stats"]
    completeness = meta["grid_completeness"]
    return {
        "candidates": len(candidates),
        "missing_values": missing,
        "invalid_coordinates": int(outside.sum()),
        "duplicate_ids": int(candidates["candidate_id"].duplicated().sum()),
        "duplicate_coordinates": int(coordinates.duplicated().sum()),
        "grid_completeness": {
            "national_median_per_km2": completeness["national_median_per_km2"],
            "districts": completeness["districts"],
        },
        "manual_chargers": meta["manual_chargers"],
    }


def _json_ready(value: Any) -> Any:
    """NaN (an undefined metric, e.g. recall without hits) becomes null in JSON."""
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and value != value:
        return None
    return value


# --- The report --------------------------------------------------------------------------


def _pct(value: float) -> str:
    return "n/a" if value != value else f"{value:.3f}"  # NaN check without numpy


def _interval(values: list[float]) -> str:
    return f"[{_pct(values[0])}, {_pct(values[1])}]"


def network_section(summary: dict[str, Any] | None) -> str:
    """Section C: the network comparison from Milestone 6, when network.json exists."""
    if summary is None:
        return "Not available: run `scripts/optimize.py` before `scripts/evaluate.py`."
    from sitescout.optimize import render_network_section  # optimize imports this module

    return render_network_section(summary)


def render_report(results: dict[str, Any], config: Config) -> str:
    """reports/evaluation.md, filled from the computed results only."""
    settings = config.settings
    label = settings.evaluation.label
    a, b, d = results["backtest"], results["stability"], results["data_quality"]
    level = int(round(settings.evaluation.bootstrap.confidence_level * 100))
    names = [*(f"precision_at_{k}" for k in settings.evaluation.backtest.precision_at)]
    names.append(f"recall_at_{settings.evaluation.backtest.recall_at}")
    titles = {
        name: name.replace("_at_", "@")
        .replace("precision", "Precision")
        .replace("recall", "Recall")
        for name in names
    }

    rows = []
    for name in names:
        rows.append(
            f"| {titles[name]} | {_pct(a['metrics']['sitescout'][name])} "
            f"{_interval(a['bootstrap']['sitescout'][name])} | "
            f"{_pct(a['metrics']['population_only'][name])} "
            f"{_interval(a['bootstrap']['population_only'][name])} | "
            f"{_pct(a['random'][name]['mean'])} | "
            f"{_interval(a['bootstrap']['sitescout_minus_population'][name])} |"
        )
    key = f"precision_at_{max(settings.evaluation.backtest.precision_at)}"
    ours, pop = a["metrics"]["sitescout"][key], a["metrics"]["population_only"][key]
    diff = a["bootstrap"]["sitescout_minus_population"][key]
    verdict = (
        "beats" if ours > pop else "does not beat" if ours < pop else "ties with"
    ) + f" the population-only baseline on {titles[key]} ({_pct(ours)} against {_pct(pop)})"
    certainty = f"The {level}% bootstrap interval of the difference, {_interval(diff)}, " + (
        "includes 0, so the data cannot tell the two apart."
        if diff[0] <= 0 <= diff[1]
        else "excludes 0."
    )
    stab_rows = "\n".join(
        f"| {run['weight']} | {run['factor']:g} | {run['overlap']:.2f} |"
        for run in sorted(
            b["runs"], key=lambda run: (run["overlap"], run["weight"], run["factor"])
        )[:10]
    )
    missing = ", ".join(f"`{k}` {v}" for k, v in d["missing_values"].items()) or "none"
    districts = sorted(d["grid_completeness"]["districts"].values(), key=lambda e: e["ratio"])
    grid_rows = "\n".join(
        f"| {e['district']} | {e['power_features']} | {e['features_per_km2']:.3f} "
        f"| {e['ratio']:.2f} |"
        for e in districts
    )
    values = {
        "label": label,
        "hit_radius_m": a["hit_radius_m"],
        "known_sites": a["known_charging_sites"],
        "merge_radius_m": settings.sources.charger_match_radius_m,
        "manual_status": a["charger_sources"]["chargers_manual"]["status"].split(":")[0],
        "hit_candidates": a["hit_candidates"],
        "candidates": a["candidates"],
        "top_k": max(settings.evaluation.backtest.precision_at),
        "recall_at": settings.evaluation.backtest.recall_at,
        "hits_in_top": len(a["hits_in_sitescout_top"]),
        "level": level,
        "population_feature": POPULATION_FEATURE,
        "random_seeds": settings.evaluation.random_baseline_seeds,
        "metric_rows": "\n".join(rows),
        "verdict": verdict,
        "certainty": certainty,
        "top_metric": titles[key],
        "one_hit": f"{1 / max(settings.evaluation.backtest.precision_at):.3f}",
        "weights": len(b["runs"]) // 2,
        "low_factor": f"{1 - b['perturbation']:g}",
        "high_factor": f"{1 + b['perturbation']:g}",
        "stability_k": b["top_k"],
        "mean_overlap": f"{b['mean_overlap']:.2f}",
        "min_overlap": f"{b['min_overlap']:.2f}",
        "target": f"{b['target']:.2f}",
        "below_target": b["runs_below_target"],
        "runs": len(b["runs"]),
        "stability_rows": stab_rows,
        "dq_candidates": d["candidates"],
        "invalid_coordinates": d["invalid_coordinates"],
        "duplicate_ids": d["duplicate_ids"],
        "duplicate_coordinates": d["duplicate_coordinates"],
        "missing": missing,
        "national_median": f"{d['grid_completeness']['national_median_per_km2']:.3f}",
        "grid_rows": grid_rows,
        "network_section": network_section(results.get("network")),
    }
    return TEMPLATE.read_text(encoding="utf-8").format(**values)


def run_evaluation(
    config: Config, processed_dir: Path, report_path: Path | None = None
) -> dict[str, Any]:
    """Compute sections A, B and D, write evaluation.json and the report.

    The report goes to ``paths.evaluation_report`` unless ``report_path`` is given (tests).
    """
    results = {
        "label": config.settings.evaluation.label,
        "backtest": backtest(processed_dir, config),
        "stability": stability(processed_dir, config),
        "data_quality": data_quality(processed_dir, config),
        "network": read_json(network)
        if (network := processed_dir / "network.json").is_file()
        else None,
    }
    a = results["backtest"]
    logger.info(
        "Backtest: %d known sites, %d hit candidates; SiteScout %s; population-only %s",
        a["known_charging_sites"],
        a["hit_candidates"],
        a["metrics"]["sitescout"],
        a["metrics"]["population_only"],
    )
    logger.info(
        "Stability: mean overlap %.3f, min %.3f, %d runs below target",
        results["stability"]["mean_overlap"],
        results["stability"]["min_overlap"],
        results["stability"]["runs_below_target"],
    )
    write_json(processed_dir / RESULTS_FILE, _json_ready(results))
    report = report_path or config.resolve(config.settings.paths.evaluation_report)
    text = render_report(results, config)
    write_atomically(report, lambda temp: Path(temp).write_bytes(text.encode("utf-8")))
    logger.info("Wrote %s and %s", processed_dir / RESULTS_FILE, report)
    return results
