"""Milestone 6: select the 30-site network (SPEC §8; D-045, D-046).

**Demand.** WorldPop pixels are summed into H3 cells at ``optimization.demand_h3_resolution``
(7); each cell is a demand node at its centre. In production mode, demand within the service
radius of an existing charging site (D-034) is multiplied by
``existing_charger_demand_factor``: capacity those chargers already provide. The weights are
then normalised to sum to 1.

**Sites.** Eligible candidates are those whose production score is in the top half
(percentile, as in scoring, at least ``min_score_percentile``) and, with
``optimization.require_host``, that have a host.

**Selections**, each with ``n_sites`` sites:

- **exact MCLP** (PuLP, bundled CBC): maximise Σ wᵢ·yᵢ + λ·Σ sⱼ·xⱼ with sⱼ = score / 100,
  yᵢ ≤ Σ xⱼ over the sites covering node i (within the service radius), Σ xⱼ = n, and
  xⱼ + xₖ ≤ 1 for sites closer than ``min_spacing_m``;
- **greedy**: the same objective and constraints, adding the site with the largest gain
  (ties by candidate_id) until n are chosen;
- **Top-30 by score**: the n eligible candidates with the highest scores.

The three are compared on covered demand, the share of Rwanda's modelled population
covered, province spread and overlap. Marginal coverage says how much each candidate adds to
(or would take from) the MCLP network. Solve times vary from run to run, so they are written
to ``network_run.json`` and the log, never to the layer or the report.
"""

from __future__ import annotations

import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pulp
import rasterio

from sitescout.config import Config
from sitescout.crs import to_metric
from sitescout.evaluation import known_charging_sites
from sitescout.features.nearest import count_within, pairs_within
from sitescout.ingest.layers import NETWORK, read_layer, remove_layer, write_layer
from sitescout.ingest.metadata import write_json
from sitescout.ingest.raster import read_population
from sitescout.scoring import percentile_rank

logger = logging.getLogger(__name__)

SUMMARY_FILE = "network.json"
RUN_FILE = "network_run.json"
TEMPLATE = Path(__file__).parent / "templates" / "network.md"
SELECTIONS = ("mclp", "greedy", "top30")


class NetworkError(Exception):
    """The network cannot be selected, e.g. too few eligible sites or an infeasible model."""


# --- Demand ------------------------------------------------------------------------------


def demand_nodes(population: Path, config: Config) -> gpd.GeoDataFrame:
    """WorldPop people summed into H3 cells; one node per cell at its centre (metric CRS)."""
    settings = config.settings
    with rasterio.open(population) as dataset:
        band = dataset.read(1, masked=True)
        transform = dataset.transform
    rows, cols = np.nonzero(~np.ma.getmaskarray(band) & (band.filled(0) > 0))
    people = band.data[rows, cols].astype(np.float64)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    resolution = settings.optimization.demand_h3_resolution
    cells = [h3.latlng_to_cell(y, x, resolution) for y, x in zip(lat, lon, strict=True)]
    table = (
        pd.DataFrame({"cell": cells, "people": people})
        .groupby("cell", sort=True)["people"]
        .sum()
        .reset_index()
    )
    centres = [h3.cell_to_latlng(cell) for cell in table["cell"]]
    points = gpd.GeoSeries(
        gpd.points_from_xy([c[1] for c in centres], [c[0] for c in centres]),
        crs=settings.crs.storage,
    )
    return gpd.GeoDataFrame(
        table, geometry=to_metric(points, settings.crs).to_numpy(), crs=settings.crs.metric
    )


def demand_weights(
    nodes: gpd.GeoDataFrame, sites: gpd.GeoSeries, radius_m: float, factor: float
) -> np.ndarray:
    """Node weights summing to 1, with demand near an existing charging site down-weighted."""
    near = count_within(nodes.geometry, sites, radius_m) > 0
    weights = nodes["people"].to_numpy() * np.where(near, factor, 1.0)
    return weights / weights.sum()


def coverage(sites: gpd.GeoSeries, nodes: gpd.GeoDataFrame, radius_m: float) -> list[np.ndarray]:
    """For each site, the indices of the demand nodes within ``radius_m`` (inclusive)."""
    site_idx, node_idx, _ = pairs_within(sites, nodes.geometry.reset_index(drop=True), radius_m)
    order = np.lexsort((node_idx, site_idx))
    site_idx, node_idx = site_idx[order], node_idx[order]
    bounds = np.searchsorted(site_idx, np.arange(len(sites) + 1))
    return [node_idx[bounds[j] : bounds[j + 1]] for j in range(len(sites))]


# --- Selections --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Problem:
    """One MCLP instance over the eligible sites (positions into the candidate table)."""

    eligible: np.ndarray
    cover: list[np.ndarray]
    weights: np.ndarray
    site_score: np.ndarray  # s_j in [0, 1], for every candidate
    lam: float
    n_sites: int
    conflicts: list[tuple[int, int]]  # eligible positions closer than the minimum spacing


def covered_demand(selected: list[int], problem: Problem) -> float:
    covered = np.zeros(len(problem.weights), dtype=bool)
    for j in selected:
        covered[problem.cover[j]] = True
    return float(problem.weights[covered].sum())


def objective(selected: list[int], problem: Problem) -> float:
    return covered_demand(selected, problem) + problem.lam * float(
        problem.site_score[selected].sum()
    )


def solve_mclp(problem: Problem, time_limit_s: int) -> tuple[list[int], dict[str, Any]]:
    """The exact MCLP with PuLP and its bundled CBC solver (single thread, deterministic)."""
    model = pulp.LpProblem("sitescout_mclp", pulp.LpMaximize)
    x = {j: model.add_variable(f"x_{j}", cat="Binary") for j in problem.eligible}
    covering: dict[int, list[int]] = {}
    for j in problem.eligible:
        for i in problem.cover[j]:
            covering.setdefault(int(i), []).append(int(j))
    y = {i: model.add_variable(f"y_{i}", lowBound=0, upBound=1) for i in sorted(covering)}
    model += pulp.lpSum(problem.weights[i] * y[i] for i in y) + problem.lam * pulp.lpSum(
        problem.site_score[j] * x[j] for j in x
    )
    for i, sites in sorted(covering.items()):
        model += y[i] <= pulp.lpSum(x[j] for j in sites)
    model += pulp.lpSum(x.values()) == problem.n_sites
    for j, k in problem.conflicts:
        model += x[j] + x[k] <= 1
    started = time.perf_counter()
    with warnings.catch_warnings():
        # SPEC §8 requires PuLP's bundled CBC. PuLP 3.3 marks PULP_CBC_CMD deprecated
        # because PuLP 4 drops the bundled binary; pyproject pins pulp < 4 (D-045).
        warnings.filterwarnings(
            "ignore", message="PULP_CBC_CMD is deprecated", category=DeprecationWarning
        )
        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s, threads=1)
    model.solve(solver)
    seconds = time.perf_counter() - started
    status = pulp.LpStatus[model.status]
    solution = pulp.LpSolution[model.sol_status]
    if status in ("Infeasible", "Unbounded", "Undefined") or model.sol_status <= 0:
        raise NetworkError(f"The MCLP has no solution: status {status}, solution {solution}")
    selected = sorted(j for j, var in x.items() if var.value() is not None and var.value() > 0.5)
    if len(selected) != problem.n_sites:
        raise NetworkError(f"The MCLP selected {len(selected)} sites, not {problem.n_sites}")
    return selected, {"status": status, "solution": solution, "seconds": round(seconds, 3)}


def solve_greedy(problem: Problem) -> list[int]:
    """Add the eligible site with the largest objective gain, respecting the spacing."""
    blocked: dict[int, set[int]] = {}
    for j, k in problem.conflicts:
        blocked.setdefault(j, set()).add(k)
        blocked.setdefault(k, set()).add(j)
    covered = np.zeros(len(problem.weights), dtype=bool)
    selected: list[int] = []
    available = [int(j) for j in problem.eligible]  # already in candidate_id order
    while len(selected) < problem.n_sites:
        best, best_gain = None, -1.0
        for j in available:
            gain = float(problem.weights[problem.cover[j][~covered[problem.cover[j]]]].sum())
            gain += problem.lam * float(problem.site_score[j])
            if gain > best_gain:
                best, best_gain = j, gain
        if best is None:
            raise NetworkError(
                f"Greedy found only {len(selected)} sites that respect the spacing, not "
                f"{problem.n_sites}"
            )
        selected.append(best)
        covered[problem.cover[best]] = True
        removed = {best} | blocked.get(best, set())
        available = [j for j in available if j not in removed]
    return sorted(selected)


def marginal_coverage(selected: list[int], problem: Problem, n: int) -> np.ndarray:
    """Weighted demand each candidate would take from (selected) or add to the network."""
    count = np.zeros(len(problem.weights), dtype=np.int64)
    for j in selected:
        count[problem.cover[j]] += 1
    chosen = set(selected)
    result = np.zeros(n, dtype=np.float64)
    for j in range(n):
        nodes = problem.cover[j]
        if j in chosen:
            result[j] = problem.weights[nodes[count[nodes] == 1]].sum()
        else:
            result[j] = problem.weights[nodes[count[nodes] == 0]].sum()
    return result


# --- The whole run -----------------------------------------------------------------------


def build_problem(
    table: pd.DataFrame,
    points: gpd.GeoSeries,
    nodes: gpd.GeoDataFrame,
    chargers: gpd.GeoSeries,
    config: Config,
    *,
    lam: float,
    radius_m: float,
    factor: float,
) -> Problem:
    """The MCLP instance for the candidate table (sorted by candidate_id)."""
    rules = config.settings.optimization
    percentile = percentile_rank(table["score"].to_numpy(dtype=np.float64))
    eligible_mask = percentile >= rules.min_score_percentile
    if rules.require_host:
        eligible_mask &= (table["host_type"] != "none").to_numpy()
    eligible = np.flatnonzero(eligible_mask)
    if len(eligible) < rules.n_sites:
        raise NetworkError(
            f"Only {len(eligible)} eligible sites for a network of {rules.n_sites}; the "
            "threshold is never lowered (SPEC §8)"
        )
    close_a, close_b, distance = pairs_within(points, points, rules.min_spacing_m)
    conflicts = sorted(
        {
            (int(a), int(b))
            for a, b, d in zip(close_a, close_b, distance, strict=True)
            if a < b and d < rules.min_spacing_m and eligible_mask[a] and eligible_mask[b]
        }
    )
    return Problem(
        eligible=eligible,
        cover=coverage(points, nodes, radius_m),
        weights=demand_weights(nodes, chargers, radius_m, factor),
        site_score=table["score"].to_numpy(dtype=np.float64) / config.settings.scoring.scale_max,
        lam=lam,
        n_sites=rules.n_sites,
        conflicts=conflicts,
    )


def sites_by_province(sites: gpd.GeoSeries, processed_dir: Path, config: Config) -> dict[str, int]:
    """How many known charging sites lie in each province (descriptive, for the report)."""
    districts = read_layer("admin_districts", processed_dir, config.settings)
    metric = to_metric(districts.geometry, config.settings.crs).reset_index(drop=True)
    counts: dict[str, int] = {}
    for site in sites.array:
        inside = districts.loc[metric.intersects(site).to_numpy(), "province_name"]
        name = str(sorted(inside)[0]) if len(inside) else "outside the districts"
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def summarise(
    selected: list[int], problem: Problem, table: pd.DataFrame, nodes: gpd.GeoDataFrame
) -> dict[str, Any]:
    """Covered demand, population share, spread and objective for one selection."""
    covered = np.zeros(len(nodes), dtype=bool)
    for j in selected:
        covered[problem.cover[j]] = True
    people = nodes["people"].to_numpy()
    chosen = table.iloc[selected]
    return {
        "sites": len(selected),
        "objective": round(objective(selected, problem), 6),
        "covered_demand": round(covered_demand(selected, problem), 6),
        "population_covered_share": round(float(people[covered].sum() / people.sum()), 6),
        "provinces": int(chosen["province"].nunique()),
        "districts": int(chosen["district"].nunique()),
        "by_province": {k: int(v) for k, v in sorted(chosen["province"].value_counts().items())},
        "mean_score": round(float(chosen["score"].mean()), 3),
        "candidate_ids": sorted(chosen["candidate_id"]),
    }


def run_network(config: Config, processed_dir: Path) -> dict[str, Any]:
    """Select the MCLP, greedy and Top-30 networks, write the layer and the summaries."""
    settings, rules = config.settings, config.settings.optimization
    scores = read_layer("scores_production", processed_dir, settings)
    table = scores.sort_values("candidate_id", kind="mergesort", ignore_index=True)
    points = to_metric(table.geometry, settings.crs).reset_index(drop=True)
    population, _ = read_population(processed_dir, settings)
    nodes = demand_nodes(population, config)
    chargers, _ = known_charging_sites(processed_dir, config)
    problem = build_problem(
        table,
        points,
        nodes,
        chargers,
        config,
        lam=rules.lambda_,
        radius_m=rules.service_radius_m,
        factor=rules.existing_charger_demand_factor,
    )
    try:
        mclp, solver = solve_mclp(problem, rules.time_limit_s)
        greedy = solve_greedy(problem)
    except NetworkError:
        remove_layer(processed_dir, NETWORK.name)
        raise
    order = np.lexsort((table["candidate_id"].to_numpy(), -table["score"].to_numpy()))
    eligible_set = set(problem.eligible.tolist())
    top30 = sorted([int(j) for j in order if int(j) in eligible_set][: rules.n_sites])
    selections = {"mclp": mclp, "greedy": greedy, "top30": top30}
    summaries = {
        name: summarise(chosen, problem, table, nodes) for name, chosen in selections.items()
    }
    exact, heuristic = summaries["mclp"]["objective"], summaries["greedy"]["objective"]
    overlap = {
        f"{a}_{b}": len(set(selections[a]) & set(selections[b]))
        for a, b in (("mclp", "greedy"), ("mclp", "top30"), ("greedy", "top30"))
    }

    sensitivity, timings = [], {"base": solver}
    base = set(mclp)
    varied = (
        ("lambda", rules.sensitivity.lambda_, rules.lambda_),
        ("service_radius_m", rules.sensitivity.service_radius_m, rules.service_radius_m),
        (
            "existing_charger_demand_factor",
            rules.sensitivity.existing_charger_demand_factor,
            rules.existing_charger_demand_factor,
        ),
    )
    for name, values, default in varied:
        for value in values:
            if value == default:
                continue
            params = {
                "lam": rules.lambda_,
                "radius_m": rules.service_radius_m,
                "factor": rules.existing_charger_demand_factor,
            }
            params[{"lambda": "lam", "service_radius_m": "radius_m"}.get(name, "factor")] = value
            variant = build_problem(table, points, nodes, chargers, config, **params)
            chosen, run = solve_mclp(variant, rules.time_limit_s)
            timings[f"{name}={value}"] = run
            summary = summarise(chosen, variant, table, nodes)
            sensitivity.append(
                {
                    "parameter": name,
                    "value": value,
                    "status": run["status"],
                    "covered_demand": summary["covered_demand"],
                    "population_covered_share": summary["population_covered_share"],
                    "provinces": summary["provinces"],
                    "overlap_with_base": len(set(chosen) & base),
                }
            )

    marginal = marginal_coverage(mclp, problem, len(table))
    flags = {name: np.isin(np.arange(len(table)), chosen) for name, chosen in selections.items()}
    layer = gpd.GeoDataFrame(
        {
            "candidate_id": table["candidate_id"].to_numpy(),
            "host_type": table["host_type"].to_numpy(),
            "district": table["district"].to_numpy(),
            "province": table["province"].to_numpy(),
            "score": table["score"].to_numpy(dtype=np.float64),
            "rank": table["rank"].to_numpy(dtype=np.int64),
            "eligible": np.isin(np.arange(len(table)), problem.eligible),
            "selected_mclp": flags["mclp"],
            "selected_greedy": flags["greedy"],
            "selected_top30": flags["top30"],
            "marginal_coverage": np.round(marginal, 9),
        },
        geometry=table.geometry.to_numpy(),
        crs=settings.crs.storage,
    )
    summary = {
        "mode": "production",
        "demand_nodes": len(nodes),
        "h3_resolution": rules.demand_h3_resolution,
        "population_total": round(float(nodes["people"].sum()), 3),
        "known_charging_sites": int(len(chargers)),
        "known_sites_by_province": sites_by_province(chargers, processed_dir, config),
        "eligible_sites": int(len(problem.eligible)),
        "spacing_conflicts": len(problem.conflicts),
        "parameters": {
            "n_sites": rules.n_sites,
            "lambda": rules.lambda_,
            "service_radius_m": rules.service_radius_m,
            "min_spacing_m": rules.min_spacing_m,
            "min_score_percentile": rules.min_score_percentile,
            "existing_charger_demand_factor": rules.existing_charger_demand_factor,
            "require_host": rules.require_host,
        },
        "solver": {"status": solver["status"], "solution": solver["solution"]},
        "selections": summaries,
        "exact_vs_greedy_gap": round((exact - heuristic) / exact, 6) if exact else 0.0,
        "overlap": overlap,
        "sensitivity": sensitivity,
    }
    write_layer(
        layer,
        NETWORK,
        processed_dir,
        settings,
        sources=[],
        stats={key: value for key, value in summary.items() if key != "sensitivity"},
        notes=[
            "Production mode. Coverage is modelled population within the service radius; it "
            "says nothing about grid capacity, land or demand for charging.",
            "Solve times are in network_run.json; they vary between runs.",
        ],
    )
    write_json(processed_dir / SUMMARY_FILE, summary)
    write_json(processed_dir / RUN_FILE, timings)
    logger.info(
        "MCLP %s (%s) in %.2f s: covered demand %.4f; greedy %.4f (gap %.4f%%); Top-30 %.4f",
        solver["status"],
        solver["solution"],
        solver["seconds"],
        summaries["mclp"]["covered_demand"],
        summaries["greedy"]["covered_demand"],
        summary["exact_vs_greedy_gap"] * 100,
        summaries["top30"]["covered_demand"],
    )
    return summary


def _share(value: float) -> str:
    return f"{value * 100:.1f}%"


def factor_note(summary: dict[str, Any]) -> str:
    """Explain the existing-charger factor rows of the sensitivity table."""
    n = summary["parameters"]["n_sites"]
    runs = [r for r in summary["sensitivity"] if r["parameter"] == "existing_charger_demand_factor"]
    where = ", ".join(
        f"{count} in {province}" for province, count in summary["known_sites_by_province"].items()
    )
    if runs and all(r["overlap_with_base"] == n for r in runs):
        return (
            "Changing the existing-charger factor leaves the network unchanged: the "
            f"{summary['known_charging_sites']} known charging sites are few and concentrated "
            f"({where}), so down-weighting the demand near them does not change which sites "
            "are chosen. It will matter once more chargers are known."
        )
    changed = n - min((r["overlap_with_base"] for r in runs), default=n)
    return (
        f"Changing the existing-charger factor changes up to {changed} of the {n} sites; "
        f"the known charging sites are {where}."
    )


def render_network_section(summary: dict[str, Any]) -> str:
    """Section C of reports/evaluation.md, filled from network.json only."""
    s, p = summary["selections"], summary["parameters"]
    columns = (
        ("mclp", "Exact MCLP"),
        ("greedy", "Greedy"),
        ("top30", f"Top-{p['n_sites']} by score"),
    )
    head = "| | " + " | ".join(title for _, title in columns) + " |\n|---|---|---|---|"
    rows = [
        ("Covered demand (weighted objective term)", lambda v: f"{v['covered_demand']:.3f}"),
        (
            f"Modelled population within {p['service_radius_m'] // 1000} km",
            lambda v: _share(v["population_covered_share"]),
        ),
        ("Provinces with a site", lambda v: str(v["provinces"])),
        ("Districts with a site", lambda v: str(v["districts"])),
        ("Mean site score", lambda v: f"{v['mean_score']:.1f}"),
    ]
    provinces = sorted({name for v in s.values() for name in v["by_province"]})
    rows += [
        (f"Sites in {name}", lambda v, name=name: str(v["by_province"].get(name, 0)))
        for name in provinces
    ]
    table = (
        head
        + "\n"
        + "\n".join(
            f"| {label} | " + " | ".join(fmt(s[key]) for key, _ in columns) + " |"
            for label, fmt in rows
        )
    )
    sensitivity = "\n".join(
        f"| {r['parameter']} = {r['value']:g} | {_share(r['population_covered_share'])} "
        f"| {r['provinces']} | {r['overlap_with_base']} of {p['n_sites']} |"
        for r in summary["sensitivity"]
    )
    overlap = summary["overlap"]
    values = {
        "eligible": summary["eligible_sites"],
        "min_percentile": p["min_score_percentile"],
        "host_rule": " and a host" if p["require_host"] else "",
        "nodes": summary["demand_nodes"],
        "resolution": summary["h3_resolution"],
        "radius_km": p["service_radius_m"] // 1000,
        "known_sites": summary["known_charging_sites"],
        "factor": f"{p['existing_charger_demand_factor']:g}",
        "spacing_km": p["min_spacing_m"] // 1000,
        "n": p["n_sites"],
        "lam": f"{p['lambda']:g}",
        "table": table,
        "status": summary["solver"]["status"],
        "solution": summary["solver"]["solution"],
        "gap": f"{summary['exact_vs_greedy_gap'] * 100:.2f}%",
        "mclp_greedy": overlap["mclp_greedy"],
        "mclp_top30": overlap["mclp_top30"],
        "greedy_top30": overlap["greedy_top30"],
        "sensitivity": sensitivity,
        "exact_objective": f"{s['mclp']['objective']:.4f}",
        "greedy_objective": f"{s['greedy']['objective']:.4f}",
        "factor_note": factor_note(summary),
    }
    return TEMPLATE.read_text(encoding="utf-8").format(**values)
