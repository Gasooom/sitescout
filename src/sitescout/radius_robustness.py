"""D-068: how the coverage radius changes the comparison, against a stronger baseline.

An *additional analysis* for section F of ``reports/evaluation.md``. It never changes the
shipped network, a default parameter or the export.

Site scores stay fixed. Only the coverage radius used to select and to evaluate varies; the
radius of the zone around known chargers where demand is down-weighted stays at the shipped
``optimization.service_radius_m`` (the cross-check below also lets it follow the radius). Every
method chooses from the same eligible sites, with the same score tie-break as the shipped
Top-30:

- **Optimized**: the exact MCLP (same formulation, lambda and spacing as the shipped one);
- **Greedy coverage**: the same objective, one site at a time;
- **Top-30 by score**: the highest scores with no spacing (the shipped naive baseline);
- **Top by score, spaced** (new): walk the score ranking and accept a site only when it is at
  least D from every accepted site, for each D in ``optimization.radius_robustness.spacing_m``,
  until n sites are chosen or the ranking runs out.

Coverage is the share of Rwanda's modelled population within the radius of a selected site.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from sitescout.config import Config
from sitescout.crs import to_metric
from sitescout.evaluation import known_charging_sites
from sitescout.ingest.layers import read_layer
from sitescout.ingest.metadata import read_json, write_json
from sitescout.ingest.raster import read_population
from sitescout.optimize import (
    NetworkError,
    build_problem,
    demand_nodes,
    demand_weights,
    solve_greedy,
    solve_mclp,
    summarise,
)

logger = logging.getLogger(__name__)

RESULTS_FILE = "radius_robustness.json"
KIGALI = "City of Kigali"  # the province name in the boundary data
METHODS = ("mclp", "greedy", "top")


def spaced_method(spacing_m: int) -> str:
    return f"spaced_{spacing_m}"


def ranked_eligible(table: pd.DataFrame, eligible: np.ndarray) -> list[int]:
    """Eligible positions from the highest score down, ties by candidate_id (as the Top-30)."""
    order = np.lexsort((table["candidate_id"].to_numpy(), -table["score"].to_numpy()))
    allowed = set(eligible.tolist())
    return [int(j) for j in order if int(j) in allowed]


def select_spaced_top(
    ranked: Sequence[int], x: np.ndarray, y: np.ndarray, min_distance_m: float, n: int
) -> list[int]:
    """Walk ``ranked`` and accept a site only if it is at least ``min_distance_m`` from every
    accepted site (straight-line, metric CRS; exactly the distance is accepted), until ``n``
    are accepted. Fewer than ``n`` come back when the ranking runs out first. Deterministic."""
    chosen: list[int] = []
    for j in ranked:
        if len(chosen) == n:
            break
        if chosen and bool((np.hypot(x[chosen] - x[j], y[chosen] - y[j]) < min_distance_m).any()):
            continue
        chosen.append(int(j))
    return chosen


def analyse(
    table: pd.DataFrame,
    points: gpd.GeoSeries,
    nodes: gpd.GeoDataFrame,
    chargers: gpd.GeoSeries,
    config: Config,
    *,
    radii_m: Sequence[int],
    spacings_m: Sequence[int],
) -> dict[str, Any]:
    """Every method at every radius, plus the cross-check on the down-weighting zone."""
    rules = config.settings.optimization
    base = rules.service_radius_m
    x, y = points.x.to_numpy(dtype=np.float64), points.y.to_numpy(dtype=np.float64)
    rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    eligible = 0
    for radius in radii_m:
        at_radius = build_problem(
            table,
            points,
            nodes,
            chargers,
            config,
            lam=rules.lambda_,
            radius_m=radius,
            factor=rules.existing_charger_demand_factor,
        )
        # Only the coverage radius varies: the down-weighting zone keeps the shipped radius.
        problem = replace(
            at_radius,
            weights=demand_weights(nodes, chargers, base, rules.existing_charger_demand_factor),
        )
        eligible = len(problem.eligible)
        ranked = ranked_eligible(table, problem.eligible)
        mclp, run = solve_mclp(problem, rules.time_limit_s)
        selections: dict[str, list[int]] = {
            "mclp": mclp,
            "greedy": solve_greedy(problem),
            "top": ranked[: rules.n_sites],
        }
        for spacing in spacings_m:
            selections[spaced_method(spacing)] = select_spaced_top(
                ranked, x, y, spacing, rules.n_sites
            )
        for method, chosen in selections.items():
            summary = summarise(sorted(chosen), problem, table, nodes)
            rows.append(
                {
                    "method": method,
                    "radius_m": int(radius),
                    "sites": summary["sites"],
                    "population_covered_share": summary["population_covered_share"],
                    "provinces": summary["provinces"],
                    "districts": summary["districts"],
                    "kigali_sites": summary["by_province"].get(KIGALI, 0),
                    "mean_score": summary["mean_score"],
                    "status": run["status"] if method == "mclp" else None,
                    "candidate_ids": summary["candidate_ids"],
                }
            )
        if radius != base:
            follows, _ = solve_mclp(at_radius, rules.time_limit_s)
            moved = summarise(follows, at_radius, table, nodes)
            checks.append(
                {
                    "radius_m": int(radius),
                    "sites_shared": len(set(follows) & set(mclp)),
                    "share_zone_fixed": next(
                        r["population_covered_share"]
                        for r in rows
                        if r["method"] == "mclp" and r["radius_m"] == radius
                    ),
                    "share_zone_follows": moved["population_covered_share"],
                }
            )
        logger.info("Radius %d m: MCLP %s in %.2f s", radius, run["status"], run["seconds"])
    return {
        "parameters": {
            "n_sites": rules.n_sites,
            "lambda": rules.lambda_,
            "service_radius_m": base,
            "min_spacing_m": rules.min_spacing_m,
            "existing_charger_demand_factor": rules.existing_charger_demand_factor,
            "eligible_sites": eligible,
            "known_charging_sites": int(len(chargers)),
            "radii_m": [int(r) for r in radii_m],
            "spacing_m": [int(s) for s in spacings_m],
        },
        "rows": rows,
        "weight_zone_check": checks,
        "reconciles_with_network": None,
    }


def run_radius_robustness(config: Config, processed_dir: Path) -> dict[str, Any]:
    """Run the analysis on the processed data and write ``radius_robustness.json``."""
    settings = config.settings
    scores = read_layer("scores_production", processed_dir, settings)
    table = scores.sort_values("candidate_id", kind="mergesort", ignore_index=True)
    points = to_metric(table.geometry, settings.crs).reset_index(drop=True)
    population, _ = read_population(processed_dir, settings)
    nodes = demand_nodes(population, config)
    chargers, _ = known_charging_sites(processed_dir, config)
    plan = settings.optimization.radius_robustness
    started = time.perf_counter()
    result = analyse(
        table, points, nodes, chargers, config, radii_m=plan.radii_m, spacings_m=plan.spacing_m
    )
    result["reconciles_with_network"] = _reconcile(result, processed_dir)
    write_json(processed_dir / RESULTS_FILE, result)
    logger.info(
        "Radius robustness: %.1f s, written to %s", time.perf_counter() - started, RESULTS_FILE
    )
    return result


def _reconcile(result: dict[str, Any], processed_dir: Path) -> bool | None:
    """At the shipped radius the three shipped selections must be reproduced exactly."""
    path = processed_dir / "network.json"
    base = result["parameters"]["service_radius_m"]
    if not path.is_file() or base not in result["parameters"]["radii_m"]:
        return None
    shipped = read_json(path)["selections"]
    here = {r["method"]: r["candidate_ids"] for r in result["rows"] if r["radius_m"] == base}
    same = all(
        here[method] == shipped[name]["candidate_ids"]
        for method, name in (("mclp", "mclp"), ("greedy", "greedy"), ("top", "top30"))
    )
    if not same:
        raise NetworkError(
            f"At {base} m the analysis does not reproduce network.json; re-run scripts/optimize.py"
        )
    return True


# --- Section F of reports/evaluation.md ---------------------------------------------------------


def _label(method: str, n: int) -> str:
    if method == "mclp":
        return "Optimized (exact MCLP)"
    if method == "greedy":
        return "Greedy coverage"
    if method == "top":
        return f"Top-{n} by score, no spacing"
    return f"Top by score, at least {int(method.split('_')[1]) / 1000:g} km apart"


def _pp(value: float) -> str:
    return f"{value * 100:.1f}"


def _km(radius_m: int) -> str:
    return f"{radius_m / 1000:g} km"


def render_radius_section(result: dict[str, Any] | None) -> str:
    """Section F: one table and a few plain sentences, all filled from the results."""
    if result is None:
        return "Not available: run `scripts/radius_robustness.py` before `scripts/evaluate.py`."
    p, rows = result["parameters"], result["rows"]
    n, base, radii = p["n_sites"], p["service_radius_m"], p["radii_m"]
    by = {(r["method"], r["radius_m"]): r for r in rows}
    methods = list(dict.fromkeys(r["method"] for r in rows))
    baselines = [m for m in methods if m not in ("mclp", "greedy")]

    def share(method: str, radius: int) -> float:
        # To the 0.1 point the table shows, so every gap equals the difference a reader sees.
        return round(by[(method, radius)]["population_covered_share"], 3)

    def strongest(radius: int, *, full: bool) -> str | None:
        pool = [m for m in baselines if not full or by[(m, radius)]["sites"] == n]
        return max(pool, key=lambda m: share(m, radius), default=None)

    lines = [
        "| Method | Radius | Population within radius | Versus Optimized (points) | Sites "
        "| Provinces | Districts | Sites in Kigali | Mean site score |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for radius in radii:
        for method in methods:
            r = by[(method, radius)]
            points = round((share(method, radius) - share("mclp", radius)) * 100, 1)
            delta = "—" if method == "mclp" else ("0.0" if points == 0 else f"{points:+.1f}")
            sites = str(r["sites"]) if r["sites"] == n else f"{r['sites']} of {n}"
            lines.append(
                f"| {_label(method, n)} | {_km(radius)} | {_pp(share(method, radius))}% "
                f"| {delta} | {sites} | {r['provinces']} | {r['districts']} | {r['kigali_sites']} "
                f"| {r['mean_score']:.1f} |"
            )

    def gap(radius: int, *, full: bool) -> float | None:
        best = strongest(radius, full=full)
        return None if best is None else share("mclp", radius) - share(best, radius)

    def describe(method: str, radius: int) -> str:
        r = by[(method, radius)]
        reached = "" if r["sites"] == n else f" but reaches only {r['sites']} of {n} sites"
        return f"{_label(method, n).lower()}, covers {_pp(share(method, radius))}%{reached}"

    sentences = [
        f"All methods choose from the same {p['eligible_sites']} eligible sites with site scores "
        "fixed and only the coverage radius changing; the spaced baselines take the highest "
        "scores while keeping every pair of sites at least their spacing apart."
    ]
    if base in radii:
        best, best_full = strongest(base, full=False), strongest(base, full=True)
        text = (
            f"At {_km(base)} Optimized covers {_pp(share('mclp', base))}% of modelled population; "
            f"the strongest baseline, {describe(best, base)}, a gap of "
            f"{_pp(gap(base, full=False))} percentage points"
        )
        if best_full is not None and best_full != best:
            text += (
                f", while the strongest baseline with all {n} sites, "
                f"{_label(best_full, n).lower()}, covers {_pp(share(best_full, base))}% "
                f"({_pp(gap(base, full=True))} points behind)"
            )
        text += f"; the unspaced Top-{n} covers {_pp(share('top', base))}%."
        sentences.append(text)
    gaps = {radius: gap(radius, full=False) for radius in radii}
    low, high = min(gaps, key=gaps.get), max(gaps, key=gaps.get)
    behind = [radius for radius in radii if gaps[radius] <= 0]
    full_gaps = [g for g in (gap(radius, full=True) for radius in radii) if g is not None]
    full_range = (
        f" (against the best baseline that reaches all {n} sites: {_pp(min(full_gaps))} to "
        f"{_pp(max(full_gaps))} points)"
        if full_gaps and len(full_gaps) == len(radii)
        else ""
    )
    sentences.append(
        f"Across {_km(radii[0])} to {_km(radii[-1])} the gap to the strongest baseline runs from "
        f"{_pp(gaps[low])} points at {_km(low)} to {_pp(gaps[high])} points at {_km(high)}"
        f"{full_range}, and "
        + (
            "Optimized covers more than every baseline at every radius."
            if not behind
            else "Optimized does not cover more than the strongest baseline at "
            + ", ".join(_km(r) for r in behind)
            + "."
        )
    )
    check = result["weight_zone_check"]
    if check:
        changed = [c for c in check if c["sites_shared"] < n]
        if not changed:
            sentences.append(
                f"Letting the zone around the {p['known_charging_sites']} known chargers, where "
                f"demand is down-weighted, follow the radius instead of staying at {_km(base)}, "
                "as the "
                "sensitivity table in section C does, leaves the selected sites unchanged at every "
                "radius."
            )
        else:
            worst = max(check, key=lambda c: abs(c["share_zone_follows"] - c["share_zone_fixed"]))
            sentences.append(
                f"Letting the zone around the {p['known_charging_sites']} known chargers, where "
                f"demand is down-weighted, follow the radius instead of staying at {_km(base)}, "
                "as the "
                "sensitivity table in section C does, changes the selected sites at "
                + ", ".join(
                    f"{_km(c['radius_m'])} ({n - c['sites_shared']} of {n})" for c in changed
                )
                + f" and moves Optimized's coverage by at most "
                f"{abs(worst['share_zone_follows'] - worst['share_zone_fixed']) * 100:.1f} points "
                f"({_pp(worst['share_zone_follows'])}% against {_pp(worst['share_zone_fixed'])}% "
                f"at {_km(worst['radius_m'])}), which is why the two tables differ slightly."
            )
    if result.get("reconciles_with_network"):
        sentences.append(
            f"At {_km(base)} the three shipped selections are reproduced exactly (checked "
            "against network.json)."
        )
    return "\n\n".join([" ".join(sentences), "\n".join(lines)])
