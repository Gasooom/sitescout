"""Milestone 10, Phase 2 (D-057): ``network_summary``, the network-level decision results.

Reads ``network.json`` in the processed directory, the one authoritative source (written by
``optimize.run_network``, M6, D-046; the M8 export headline and section C of
``reports/evaluation.md`` are built from it too). Nothing is recomputed, the optimization is
never run, and no value is typed in here: every number is a value in that file, formatted
with the display functions the export and the report use, so it reads the same everywhere.

Every value is an ``EvidenceRecord`` with a stable id (``network/<method>/<field>`` and so
on), so a later answer can cite it exactly. ``population_total`` and ``spacing_conflicts``
are in the file but shown by neither the export nor the report, so they are not exposed.

The file is validated strictly: an unknown or missing key (schema drift), a mode other than
production, a selection whose size is not ``n_sites``, or an MCLP, greedy or Top-30 selection
that disagrees with the network layer raises ``network_summary_unavailable``. Nothing is
repaired and no other source is tried.

**Limit.** This validates the internal consistency of the processed network artifact and its
agreement with the network layer. It does not determine whether the artifact is out of date
relative to a changed configuration: the parameters recorded in ``network.json`` are not
compared with the current configuration, and the optimization is never run (D-057).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sitescout.evidence import (
    NETWORK,
    EvidenceRecord,
    kilometres,
    one_decimal,
    percent,
    record,
)
from sitescout.investigation.data import InvestigationData
from sitescout.investigation.errors import InvestigationError

NETWORK_FILE = "network.json"
METHODS = ("mclp", "greedy", "top30")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- The authoritative file, as optimize.run_network writes it ----------------------------------


class Selection(_Strict):
    by_province: dict[str, int]
    candidate_ids: list[str]
    covered_demand: float
    districts: int
    mean_score: float
    objective: float
    population_covered_share: float
    provinces: int
    sites: int


class Selections(_Strict):
    mclp: Selection
    greedy: Selection
    top30: Selection


class Overlap(_Strict):
    greedy_top30: int
    mclp_greedy: int
    mclp_top30: int


class Parameters(_Strict):
    existing_charger_demand_factor: float
    lambda_: float = Field(alias="lambda")
    min_score_percentile: int
    min_spacing_m: int
    n_sites: int
    require_host: bool
    service_radius_m: int


class Solver(_Strict):
    solution: str
    status: str


class SensitivityRow(_Strict):
    covered_demand: float
    overlap_with_base: int
    parameter: str
    population_covered_share: float
    provinces: int
    status: str
    value: float


class NetworkFile(_Strict):
    demand_nodes: int
    eligible_sites: int
    exact_vs_greedy_gap: float
    h3_resolution: int
    known_charging_sites: int
    known_sites_by_province: dict[str, int]
    mode: str
    overlap: Overlap
    parameters: Parameters
    population_total: float
    selections: Selections
    sensitivity: list[SensitivityRow]
    solver: Solver
    spacing_conflicts: int


# --- The tool's result --------------------------------------------------------------------------


class MethodEntry(_Strict):
    """One selection. ``record_ids`` maps a field name to the id of its record."""

    key: Literal["mclp", "greedy", "top30"]
    label: str
    candidate_ids: list[str]
    record_ids: dict[str, str]


class SensitivityEntry(_Strict):
    parameter: str
    value: str
    record_ids: dict[str, str]


class NetworkSummary(_Strict):
    tool: Literal["network_summary"] = "network_summary"
    source: str
    mode: Literal["production"]
    methods: list[MethodEntry]
    sensitivity: list[SensitivityEntry]
    overlap_record_ids: dict[str, str]
    gap_record_id: str
    other_record_ids: dict[str, str]
    parameter_record_ids: dict[str, str]
    records: list[EvidenceRecord]


def _unavailable(message: str) -> InvestigationError:
    return InvestigationError("network_summary_unavailable", message)


def load_network(processed_dir: Path) -> NetworkFile:
    """``network.json``, validated strictly; ``network_summary_unavailable`` if it cannot be."""
    path = processed_dir / NETWORK_FILE
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _unavailable(f"{NETWORK_FILE} does not exist; run scripts/optimize.py") from None
    except (OSError, ValueError) as error:
        raise _unavailable(f"{NETWORK_FILE} cannot be read: {type(error).__name__}") from None
    try:
        network = NetworkFile.model_validate(raw)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['type']}"
            for item in error.errors()[:5]
        )
        raise _unavailable(f"{NETWORK_FILE} does not match its schema ({problems})") from None
    if network.mode != "production":
        raise _unavailable(f"{NETWORK_FILE} has mode {network.mode!r}, not 'production'")
    return network


def check_consistency(network: NetworkFile, data: InvestigationData) -> None:
    """The file must agree with the network layer and with itself; raise, never repair."""
    n_sites = network.parameters.n_sites
    flags = {
        "mclp": "selected_mclp",
        "greedy": "selected_greedy",
        "top30": "selected_top30",
    }
    sites = data.analyst.sites
    for key in METHODS:
        selection = getattr(network.selections, key)
        if selection.sites != n_sites or len(selection.candidate_ids) != n_sites:
            raise _unavailable(
                f"the {key} selection has {selection.sites} sites "
                f"({len(selection.candidate_ids)} ids) but n_sites is {n_sites}"
            )
        in_layer = set(sites.index[sites[flags[key]].astype(bool)])
        if set(selection.candidate_ids) != in_layer:
            raise _unavailable(f"the {key} candidate ids disagree with the network layer")


# --- Records ------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _labels(n_sites: int) -> dict[str, str]:
    # The names the M8 export gives the three selections.
    return {
        "mclp": "Optimized network",
        "greedy": "Greedy network",
        "top30": f"Top-{n_sites} by score",
    }


def network_summary(data: InvestigationData) -> NetworkSummary:
    """The network-level decision results in ``network.json``; see the module docstring."""
    network = load_network(data.processed_dir)
    check_consistency(network, data)
    p = network.parameters
    labels = _labels(p.n_sites)
    records: list[EvidenceRecord] = []

    def add(
        id: str,
        claim: str,
        kind: Literal["RETRIEVED_FACT", "CALCULATED", "INFERRED", "UNKNOWN"],
        metric: str,
        value: Any,
        display: str,
        unit: str | None = None,
    ) -> str:
        records.append(record(id, claim, kind, NETWORK, metric, value, display, unit))
        return id

    provinces = sorted(
        {name for key in METHODS for name in getattr(network.selections, key).by_province}
    )
    methods = []
    for key in METHODS:
        s: Selection = getattr(network.selections, key)
        label, base = labels[key], f"network/{key}"
        ids = {
            "population_covered_share": add(
                f"{base}/population_covered_share",
                f"Modelled population within the service radius ({label})",
                "CALCULATED",
                "population_covered_share",
                s.population_covered_share,
                percent(s.population_covered_share, 1),
            ),
            "covered_demand": add(
                f"{base}/covered_demand",
                f"Covered demand, the weighted objective term ({label})",
                "CALCULATED",
                "covered_demand",
                s.covered_demand,
                f"{s.covered_demand:.3f}",
            ),
            "objective": add(
                f"{base}/objective",
                f"Optimization objective value, which includes the score term ({label})",
                "CALCULATED",
                "objective",
                s.objective,
                f"{s.objective:.4f}",
            ),
            "sites": add(
                f"{base}/sites", f"Sites ({label})", "CALCULATED", "sites", s.sites, str(s.sites)
            ),
            "provinces": add(
                f"{base}/provinces",
                f"Provinces with a site ({label})",
                "CALCULATED",
                "provinces",
                s.provinces,
                str(s.provinces),
            ),
            "districts": add(
                f"{base}/districts",
                f"Districts with a site ({label})",
                "CALCULATED",
                "districts",
                s.districts,
                str(s.districts),
            ),
            "mean_score": add(
                f"{base}/mean_score",
                f"Mean site score ({label})",
                "CALCULATED",
                "mean_score",
                s.mean_score,
                one_decimal(s.mean_score),
            ),
        }
        for name in provinces:
            count = s.by_province.get(name, 0)
            ids[f"sites_in_{_slug(name)}"] = add(
                f"{base}/sites_in/{_slug(name)}",
                f"Sites in {name} ({label})",
                "CALCULATED",
                "sites_by_province",
                count,
                str(count),
            )
        methods.append(
            MethodEntry(key=key, label=label, candidate_ids=sorted(s.candidate_ids), record_ids=ids)
        )

    gap_id = add(
        "network/exact_vs_greedy_gap",
        "Exact-versus-greedy gap on the optimization objective (not a difference between the "
        "population-coverage percentages)",
        "CALCULATED",
        "exact_vs_greedy_gap",
        network.exact_vs_greedy_gap,
        percent(network.exact_vs_greedy_gap, 2),
    )
    overlap_ids = {}
    for pair, claim in (
        ("mclp_greedy", "Sites shared by the optimized and the greedy networks"),
        ("mclp_top30", f"Sites shared by the optimized network and the Top-{p.n_sites} selection"),
        ("greedy_top30", f"Sites shared by the greedy network and the Top-{p.n_sites} selection"),
    ):
        value = getattr(network.overlap, pair)
        overlap_ids[pair] = add(
            f"network/overlap/{pair}", claim, "CALCULATED", pair, value, str(value)
        )
    other = {
        "solver_status": add(
            "network/solver_status",
            "Exact MCLP solver status",
            "CALCULATED",
            "solver.status",
            network.solver.status,
            network.solver.status,
        ),
        "solver_solution": add(
            "network/solver_solution",
            "Exact MCLP solver solution status",
            "CALCULATED",
            "solver.solution",
            network.solver.solution,
            network.solver.solution,
        ),
        "eligible_sites": add(
            "network/eligible_sites",
            "Candidates eligible for the network",
            "CALCULATED",
            "eligible_sites",
            network.eligible_sites,
            str(network.eligible_sites),
        ),
        "demand_nodes": add(
            "network/demand_nodes",
            "Demand nodes (H3 cells with modelled population)",
            "CALCULATED",
            "demand_nodes",
            network.demand_nodes,
            str(network.demand_nodes),
        ),
        "h3_resolution": add(
            "network/h3_resolution",
            "H3 resolution of the demand nodes",
            "RETRIEVED_FACT",
            "h3_resolution",
            network.h3_resolution,
            str(network.h3_resolution),
        ),
        "known_charging_sites": add(
            "network/known_charging_sites",
            "Known charging sites used to down-weight demand",
            "CALCULATED",
            "known_charging_sites",
            network.known_charging_sites,
            str(network.known_charging_sites),
        ),
    }
    parameter_ids = {
        name: add(f"network/parameters/{name}", claim, "RETRIEVED_FACT", name, value, display, unit)
        for name, claim, value, display, unit in (
            ("n_sites", "Sites in the network", p.n_sites, str(p.n_sites), None),
            (
                "service_radius_m",
                "Service radius",
                p.service_radius_m,
                kilometres(p.service_radius_m),
                "m",
            ),
            (
                "min_spacing_m",
                "Minimum spacing between selected sites",
                p.min_spacing_m,
                kilometres(p.min_spacing_m),
                "m",
            ),
            (
                "lambda",
                "Weight of the score term in the objective (lambda)",
                p.lambda_,
                f"{p.lambda_:g}",
                None,
            ),
            (
                "min_score_percentile",
                "Minimum score percentile for eligibility",
                p.min_score_percentile,
                str(p.min_score_percentile),
                None,
            ),
            (
                "existing_charger_demand_factor",
                "Factor applied to demand near known charging sites",
                p.existing_charger_demand_factor,
                f"{p.existing_charger_demand_factor:g}",
                None,
            ),
            (
                "require_host",
                "Sites without a host are never selected",
                p.require_host,
                "yes" if p.require_host else "no",
                None,
            ),
        )
    }
    sensitivity = []
    for row in network.sensitivity:
        base = f"network/sensitivity/{row.parameter}={row.value:g}"
        what = f"({row.parameter} = {row.value:g})"
        sensitivity.append(
            SensitivityEntry(
                parameter=row.parameter,
                value=f"{row.value:g}",
                record_ids={
                    "population_covered_share": add(
                        f"{base}/population_covered_share",
                        f"Modelled population within the service radius {what}",
                        "CALCULATED",
                        "population_covered_share",
                        row.population_covered_share,
                        percent(row.population_covered_share, 1),
                    ),
                    "covered_demand": add(
                        f"{base}/covered_demand",
                        f"Covered demand, the weighted objective term {what}",
                        "CALCULATED",
                        "covered_demand",
                        row.covered_demand,
                        f"{row.covered_demand:.3f}",
                    ),
                    "provinces": add(
                        f"{base}/provinces",
                        f"Provinces with a site {what}",
                        "CALCULATED",
                        "provinces",
                        row.provinces,
                        str(row.provinces),
                    ),
                    "overlap_with_base": add(
                        f"{base}/overlap_with_base",
                        f"Sites shared with the base network {what}",
                        "CALCULATED",
                        "overlap_with_base",
                        row.overlap_with_base,
                        str(row.overlap_with_base),
                    ),
                    "status": add(
                        f"{base}/status",
                        f"Solver status {what}",
                        "CALCULATED",
                        "status",
                        row.status,
                        row.status,
                    ),
                },
            )
        )
    return NetworkSummary(
        source=f"{data.analyst.config.settings.paths.processed_dir}/{NETWORK_FILE}",
        mode="production",
        methods=methods,
        sensitivity=sensitivity,
        overlap_record_ids=overlap_ids,
        gap_record_id=gap_id,
        other_record_ids=other,
        parameter_record_ids=parameter_ids,
        records=records,
    )
