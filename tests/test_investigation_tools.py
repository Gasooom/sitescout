"""Milestone 10, Phase 2 (D-057): network_summary and nearby_sites (SYNTHETIC and real data)."""

import ast
import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from sitescout.analyst import REGISTRY, TOOLS, AnalystData, FindQuery, network_contribution
from sitescout.analyst.tools import find_sites
from sitescout.config import PROJECT_ROOT, SETTINGS_FILE, WEIGHTS_FILE, load_config, read_yaml
from sitescout.evidence import distance, kilometres, one_decimal, percent
from sitescout.investigation import (
    INVESTIGATION_TOOLS,
    InvestigationData,
    InvestigationError,
    NearbyArgs,
    nearby_sites,
    network_summary,
)
from sitescout.investigation.nearby import SITE_FIELDS
from sitescout.optimize import render_network_section
from synthetic_features import feature_config

METHODS = ("mclp", "greedy", "top30")


@pytest.fixture
def world(analyst_world):
    return analyst_world


@pytest.fixture
def idata(world):
    config, processed, _ = world
    return InvestigationData.load(config, processed)


def _file(processed: Path) -> dict:
    return json.loads((processed / "network.json").read_text(encoding="utf-8"))


def _layer(processed: Path) -> pd.DataFrame:
    return pd.read_parquet(processed / "network.parquet").set_index("candidate_id")


def _records(result) -> dict:
    return {r.id: r for r in result.records}


def _snapshot(*roots: Path, hashed: bool = True) -> dict[str, object]:
    """Every file under ``roots``: its SHA-256, or (``hashed=False``, for the large real data)
    its size and modification time."""
    files: dict[str, object] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                stat = path.stat()
                files[path.as_posix()] = (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    if hashed
                    else (stat.st_size, stat.st_mtime_ns)
                )
    return files


# --- network_summary: values, displays and structure ---------------------------------------------


def test_every_method_value_is_the_value_in_network_json(idata, world):
    _, processed, _ = world
    raw, records = _file(processed), _records(network_summary(idata))
    for key in METHODS:
        s = raw["selections"][key]
        for field in ("population_covered_share", "covered_demand", "objective", "mean_score"):
            assert records[f"network/{key}/{field}"].evidence.value == s[field]
        for field in ("sites", "provinces", "districts"):
            assert records[f"network/{key}/{field}"].evidence.value == s[field]


def test_displays_use_the_export_and_report_conventions(idata, world):
    _, processed, _ = world
    raw, records = _file(processed), _records(network_summary(idata))
    for key in METHODS:
        s = raw["selections"][key]
        shown = lambda field, key=key: records[f"network/{key}/{field}"].display  # noqa: E731
        assert shown("population_covered_share") == percent(s["population_covered_share"], 1)
        assert shown("covered_demand") == f"{s['covered_demand']:.3f}"
        assert shown("objective") == f"{s['objective']:.4f}"
        assert shown("mean_score") == one_decimal(s["mean_score"])
        assert shown("sites") == str(s["sites"])
        assert shown("provinces") == str(s["provinces"])
        assert shown("districts") == str(s["districts"])
    assert records["network/exact_vs_greedy_gap"].display == percent(raw["exact_vs_greedy_gap"], 2)


def test_the_displayed_numbers_match_the_evaluation_report_section(idata, world):
    _, processed, _ = world
    raw, summary = _file(processed), network_summary(idata)
    section, records = render_network_section(raw), _records(summary)
    for key in METHODS:
        assert records[f"network/{key}/population_covered_share"].display in section
    assert f"**{records['network/exact_vs_greedy_gap'].display}**" in section
    n = raw["parameters"]["n_sites"]
    for entry in summary.sensitivity:
        share = records[entry.record_ids["population_covered_share"]].display
        provinces = records[entry.record_ids["provinces"]].display
        overlap = records[entry.record_ids["overlap_with_base"]].display
        row = f"| {entry.parameter} = {entry.value} | {share} | {provinces} | {overlap} of {n} |"
        assert row in section


def test_the_headline_shares_equal_the_m9_context_records(idata):
    context = {r.id: r.display for r in idata.analyst.context}
    records = _records(network_summary(idata))
    assert (
        records["network/mclp/population_covered_share"].display
        == context["context/network_population"]
    )
    assert (
        records["network/top30/population_covered_share"].display
        == context["context/top30_population"]
    )


def test_methods_carry_their_labels_and_candidate_ids(idata, world):
    _, processed, _ = world
    raw, summary = _file(processed), network_summary(idata)
    n = raw["parameters"]["n_sites"]
    assert [(m.key, m.label) for m in summary.methods] == [
        ("mclp", "Optimized network"),
        ("greedy", "Greedy network"),
        ("top30", f"Top-{n} by score"),
    ]
    for method in summary.methods:
        assert method.candidate_ids == sorted(raw["selections"][method.key]["candidate_ids"])
        assert (
            len(method.candidate_ids)
            == n
            == int(_records(summary)[f"network/{method.key}/sites"].display)
        )


def test_selected_counts_equal_the_network_layer(idata, world):
    _, processed, _ = world
    layer, summary = _layer(processed), network_summary(idata)
    for method, flag in zip(
        summary.methods, ("selected_mclp", "selected_greedy", "selected_top30"), strict=True
    ):
        assert method.candidate_ids == sorted(layer.index[layer[flag]])


def test_sites_by_province_lists_every_province_with_zeros_as_the_report_does(idata, world):
    _, processed, _ = world
    raw, records = _file(processed), _records(network_summary(idata))
    names = sorted({p for k in METHODS for p in raw["selections"][k]["by_province"]})
    for key in METHODS:
        for name in names:
            slug = "_".join(name.lower().split())
            got = records[f"network/{key}/sites_in/{slug}"]
            assert got.evidence.value == raw["selections"][key]["by_province"].get(name, 0)


def test_gap_overlap_solver_counts_and_parameters(idata, world):
    _, processed, _ = world
    raw, summary = _file(processed), network_summary(idata)
    records, p = _records(summary), raw["parameters"]
    gap = records[summary.gap_record_id]
    assert "objective" in gap.claim and "not a difference" in gap.claim and gap.type == "CALCULATED"
    for pair, value in raw["overlap"].items():
        assert records[summary.overlap_record_ids[pair]].evidence.value == value
    assert records["network/solver_status"].display == raw["solver"]["status"]
    assert records["network/solver_solution"].display == raw["solver"]["solution"]
    for name, field in (
        ("eligible_sites", "eligible_sites"),
        ("demand_nodes", "demand_nodes"),
        ("h3_resolution", "h3_resolution"),
        ("known_charging_sites", "known_charging_sites"),
    ):
        assert records[summary.other_record_ids[name]].evidence.value == raw[field]
    shown = {name: records[rid].display for name, rid in summary.parameter_record_ids.items()}
    assert shown == {
        "n_sites": str(p["n_sites"]),
        "service_radius_m": kilometres(p["service_radius_m"]),
        "min_spacing_m": kilometres(p["min_spacing_m"]),
        "lambda": f"{p['lambda']:g}",
        "min_score_percentile": str(p["min_score_percentile"]),
        "existing_charger_demand_factor": f"{p['existing_charger_demand_factor']:g}",
        "require_host": "yes" if p["require_host"] else "no",
    }


def test_sensitivity_rows_are_the_file_rows(idata, world):
    _, processed, _ = world
    raw, summary = _file(processed), network_summary(idata)
    records = _records(summary)
    assert [(e.parameter, e.value) for e in summary.sensitivity] == [
        (row["parameter"], f"{row['value']:g}") for row in raw["sensitivity"]
    ]
    for entry, row in zip(summary.sensitivity, raw["sensitivity"], strict=True):
        assert records[entry.record_ids["status"]].display == row["status"]
        assert records[entry.record_ids["covered_demand"]].evidence.value == row["covered_demand"]


def test_the_fields_the_export_and_report_do_not_show_are_not_exposed(idata):
    summary = network_summary(idata)
    dump = summary.model_dump_json()
    assert "population_total" not in dump and "spacing_conflicts" not in dump
    assert all("population_total" not in r.evidence.metric for r in summary.records)


def test_every_record_id_is_unique_typed_and_referenced_ids_exist(idata):
    summary = network_summary(idata)
    ids = [r.id for r in summary.records]
    assert len(ids) == len(set(ids)) and all(i.startswith("network/") for i in ids)
    referenced = [
        *(i for m in summary.methods for i in m.record_ids.values()),
        *(i for e in summary.sensitivity for i in e.record_ids.values()),
        *summary.overlap_record_ids.values(),
        *summary.other_record_ids.values(),
        *summary.parameter_record_ids.values(),
        summary.gap_record_id,
    ]
    assert set(referenced) <= set(ids)
    assert {r.type for r in summary.records} <= {"CALCULATED", "RETRIEVED_FACT"}


def test_the_summary_is_read_from_the_file_on_every_call(idata, world, temp_dir):
    _, processed, _ = world
    raw = _file(processed)
    raw["selections"]["mclp"]["population_covered_share"] = 0.123456
    (temp_dir / "network.json").write_text(json.dumps(raw), encoding="utf-8")
    changed = network_summary(dataclasses.replace(idata, processed_dir=temp_dir))
    assert _records(changed)["network/mclp/population_covered_share"].display == "12.3%"


def test_network_summary_is_deterministic_and_writes_nothing(idata, world):
    _, processed, _ = world
    before = _snapshot(processed)
    first, second = network_summary(idata), network_summary(idata)
    assert first == second and first.model_dump_json() == second.model_dump_json()
    assert _snapshot(processed) == before


# --- network_summary: strict validation ----------------------------------------------------------


def _with_network(idata, temp_dir, mutate=None, text=None):
    raw = _file(idata.processed_dir)
    if mutate:
        mutate(raw)
    (temp_dir / "network.json").write_text(
        text if text is not None else json.dumps(raw), encoding="utf-8"
    )
    return dataclasses.replace(idata, processed_dir=temp_dir)


def _remove(*path):
    def mutate(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


def _set(*path, value):
    def mutate(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _refused(idata, temp_dir, message, **kwargs):
    with pytest.raises(InvestigationError, match=message) as error:
        network_summary(_with_network(idata, temp_dir, **kwargs))
    assert error.value.code == "network_summary_unavailable"


def test_a_missing_network_json_is_unavailable_and_nothing_falls_back(idata, temp_dir):
    with pytest.raises(InvestigationError, match="does not exist") as error:
        network_summary(dataclasses.replace(idata, processed_dir=temp_dir))
    assert error.value.code == "network_summary_unavailable"


def test_an_unreadable_network_json_is_unavailable(idata, temp_dir):
    _refused(idata, temp_dir, "cannot be read", text="{not json")


@pytest.mark.parametrize(
    "mutate",
    [
        _set("surprise", value=1),
        _set("selections", "mclp", "extra", value=1),
        _set("parameters", "extra", value=1),
        _set("solver", "extra", value=1),
        _remove("solver"),
        _remove("selections", "top30", "mean_score"),
        _remove("exact_vs_greedy_gap"),
        _remove("population_total"),
        _set("demand_nodes", value="many"),
        _set("sensitivity", 0, "surprise", value=1),
    ],
    ids=[
        "unknown-top-level-key",
        "unknown-selection-key",
        "unknown-parameter-key",
        "unknown-solver-key",
        "missing-solver",
        "missing-selection-field",
        "missing-gap",
        "missing-population-total",
        "wrong-type",
        "unknown-sensitivity-key",
    ],
)
def test_schema_drift_is_unavailable(idata, temp_dir, mutate):
    _refused(idata, temp_dir, "does not match its schema", mutate=mutate)


def test_a_mode_other_than_production_is_unavailable(idata, temp_dir):
    _refused(idata, temp_dir, "not 'production'", mutate=_set("mode", value="backtest"))


def test_an_mclp_selection_that_disagrees_with_the_layer_is_unavailable(idata, temp_dir):
    _refused(
        idata,
        temp_dir,
        "mclp candidate ids disagree",
        mutate=_set("selections", "mclp", "candidate_ids", value=["cand-x"]),
    )


def test_greedy_and_top30_must_agree_with_the_layer_too(idata, temp_dir):
    _refused(
        idata,
        temp_dir,
        "greedy candidate ids disagree",
        mutate=_set("selections", "greedy", "candidate_ids", value=["cand-x"]),
    )
    _refused(
        idata,
        temp_dir,
        "top30 candidate ids disagree",
        mutate=_set("selections", "top30", "candidate_ids", value=["cand-x"]),
    )


@pytest.mark.parametrize("method", METHODS)
def test_a_selection_whose_size_is_not_n_sites_is_unavailable(idata, temp_dir, method):
    _refused(
        idata,
        temp_dir,
        f"the {method} selection has 5 sites",
        mutate=_set("selections", method, "sites", value=5),
    )


def test_n_sites_must_match_every_selection(idata, temp_dir):
    _refused(idata, temp_dir, "n_sites is 2", mutate=_set("parameters", "n_sites", value=2))


def test_the_error_is_structured_and_an_analyst_error(idata, temp_dir):
    from sitescout.analyst import AnalystError

    with pytest.raises(AnalystError) as error:
        network_summary(dataclasses.replace(idata, processed_dir=temp_dir))
    assert isinstance(error.value, InvestigationError)
    assert set(error.value.to_dict()) == {"code", "message"}
    assert str(error.value).startswith("network_summary_unavailable: ")


# --- nearby_sites ---------------------------------------------------------------------------------

SERVICE = 10_000


def _points(data: AnalystData, coords: dict[str, tuple[float, float]]) -> gpd.GeoSeries:
    return gpd.GeoSeries(
        [Point(*coords[cid]) for cid in data.points.index],
        index=data.points.index,
        crs=data.points.crs,
    )


def _laid_out(idata, coords, **overrides):
    """The SYNTHETIC world with its three candidates moved to exact metric positions."""
    changes = {"points": _points(idata.analyst, coords)}
    if overrides:
        changes["config"] = feature_config(
            read_yaml(SETTINGS_FILE),
            read_yaml(WEIGHTS_FILE),
            **{"settings.optimization.n_sites": 1, **overrides},
        )
    return dataclasses.replace(idata.analyst, **changes)


# cand-c at the origin; cand-b 5 km away (a 3-4-5 triangle); cand-a 10 km away (6-8-10).
LINE = {"cand-c": (0.0, 0.0), "cand-b": (3000.0, 4000.0), "cand-a": (6000.0, 8000.0)}
# cand-a at the origin with cand-b and cand-c both exactly 5 km away, on opposite sides.
TIE = {"cand-a": (0.0, 0.0), "cand-b": (3000.0, 4000.0), "cand-c": (-3000.0, -4000.0)}


def test_the_layout_below_assumes_the_configured_service_radius(idata):
    assert idata.analyst.config.settings.optimization.service_radius_m == SERVICE


def _neighbours(result):
    return [n.site.candidate_id for n in result.neighbours]


def _distance(result, cid):
    return _records(result)[f"nearby/{result.candidate_id}/{cid}/distance"]


def test_a_known_site_lists_its_neighbours_nearest_first(idata):
    result = nearby_sites(_laid_out(idata, LINE), "cand-c", SERVICE)
    assert _neighbours(result) == ["cand-b", "cand-a"]  # by distance, although cand-a sorts first
    assert [_distance(result, c).evidence.value for c in _neighbours(result)] == [5000.0, 10000.0]
    assert result.candidate_id == "cand-c" and result.target.candidate_id == "cand-c"
    assert (result.count_within_radius, result.returned_count, result.truncated) == (2, 2, False)


def test_the_target_is_never_listed(idata):
    for target in ("cand-a", "cand-b", "cand-c"):
        assert target not in _neighbours(nearby_sites(_laid_out(idata, TIE), target, SERVICE))


def test_a_site_exactly_at_the_radius_is_inside_and_one_metre_less_leaves_it_out(idata):
    data = _laid_out(idata, TIE)
    assert _neighbours(nearby_sites(data, "cand-a", 5000)) == ["cand-b", "cand-c"]
    inside = nearby_sites(data, "cand-a", 5000)
    assert inside.count_within_radius == 2 and _distance(inside, "cand-b").evidence.value == 5000.0
    outside = nearby_sites(data, "cand-a", 4999)
    assert outside.count_within_radius == 0 and outside.neighbours == []


def test_the_largest_allowed_radius_is_inclusive_too(idata):
    assert _neighbours(nearby_sites(_laid_out(idata, LINE), "cand-c", SERVICE)) == [
        "cand-b",
        "cand-a",
    ]
    assert _neighbours(nearby_sites(_laid_out(idata, LINE), "cand-c", SERVICE - 1)) == ["cand-b"]


def test_equal_distances_are_ordered_by_candidate_id(idata):
    result = nearby_sites(_laid_out(idata, TIE), "cand-a", 5000)
    assert _distance(result, "cand-b").evidence.value == _distance(result, "cand-c").evidence.value
    assert _neighbours(result) == ["cand-b", "cand-c"]


def test_a_result_with_no_neighbour_is_a_normal_result(idata):
    result = nearby_sites(idata.analyst, "cand-a", 1)  # the real synthetic sites are km apart
    assert (result.count_within_radius, result.returned_count, result.truncated) == (0, 0, False)
    assert result.neighbours == []
    records = _records(result)
    assert records["nearby/cand-a/count_within_radius"].display == "0"
    assert records["nearby/cand-a/returned_count"].display == "0"


def test_the_result_is_capped_but_the_full_count_is_reported(idata):
    data = _laid_out(idata, LINE, **{"settings.investigation.nearby_sites.max_results": 1})
    result = nearby_sites(data, "cand-c", SERVICE)
    assert (result.count_within_radius, result.returned_count, result.truncated) == (2, 1, True)
    assert _neighbours(result) == ["cand-b"] and result.max_results == 1
    records = _records(result)
    assert records["nearby/cand-c/count_within_radius"].display == "2"
    assert records["nearby/cand-c/returned_count"].display == "1"


def test_the_default_cap_comes_from_the_configuration(idata):
    assert (
        nearby_sites(idata.analyst, "cand-a", 1).max_results
        == load_config().settings.investigation.nearby_sites.max_results
    )


def test_distance_records_are_metric_exact_and_labelled(idata):
    result = nearby_sites(_laid_out(idata, LINE), "cand-c", SERVICE)
    record = _distance(result, "cand-b")
    assert (record.type, record.evidence.unit, record.display) == (
        "CALCULATED",
        "m",
        distance(5000.0),
    )
    assert result.neighbours[0].distance_record_id == record.id
    assert _records(result)["nearby/cand-c/radius"].display == kilometres(SERVICE)


def test_neighbours_carry_the_stored_site_facts_and_flags(idata, world):
    _, processed, _ = world
    layer = _layer(processed)
    result = nearby_sites(_laid_out(idata, TIE), "cand-a", 5000)
    records = _records(result)
    for cid in ("cand-b", "cand-c"):
        neighbour = next(n for n in result.neighbours if n.site.candidate_id == cid)
        assert [r.removeprefix(f"{cid}/") for r in neighbour.record_ids] == list(SITE_FIELDS)
        for flag in ("selected_mclp", "selected_greedy", "selected_top30", "eligible"):
            assert records[f"{cid}/{flag}"].evidence.value == bool(layer.loc[cid, flag])
        assert neighbour.site == find_sites(idata.analyst, FindQuery(candidate_id=cid)).sites[0]
    host_none = [c for c in ("cand-b", "cand-c") if idata.analyst.site(c)["host_type"] == "none"]
    assert all(records[f"{c}/host"].evidence.value == "none" for c in host_none)


def test_distances_equal_the_ones_network_contribution_reports(idata):
    config = feature_config(
        read_yaml(SETTINGS_FILE),
        read_yaml(WEIGHTS_FILE),
        **{"settings.optimization.n_sites": 1, "settings.optimization.service_radius_m": 40_000},
    )
    data = dataclasses.replace(idata.analyst, config=config)
    network = data.sites.index[data.sites["selected_mclp"]]
    checked = 0
    for target in data.sites.index:
        if target in network:
            continue
        contribution = network_contribution(data, target)
        nearest = contribution.nearest_network_site
        expected = next(
            r for r in contribution.records if r.id.endswith("nearest_network_site_distance")
        )
        result = nearby_sites(data, target, 40_000)
        got = _distance(result, nearest)
        assert got.evidence.value == expected.evidence.value and got.display == expected.display
        checked += 1
    assert checked >= 1


def test_nearby_sites_is_deterministic_and_writes_nothing(idata, world):
    _, processed, _ = world
    before = _snapshot(processed)
    data = _laid_out(idata, LINE)
    assert (
        nearby_sites(data, "cand-c", SERVICE).model_dump_json()
        == nearby_sites(data, "cand-c", SERVICE).model_dump_json()
    )
    assert _snapshot(processed) == before


def test_nearby_sites_reports_no_ranking_or_comparison_field(idata):
    dump = json.dumps(nearby_sites(_laid_out(idata, LINE), "cand-c", SERVICE).model_dump()).lower()
    for word in ("winner", "better", "worse", "best", "prefer", "recommend", "higher", "lower"):
        assert f'"{word}' not in dump


# --- nearby_sites: refusals -----------------------------------------------------------------


@pytest.mark.parametrize("radius", [0, -1, SERVICE + 1, 10**9, True, 5000.0, "5000", None])
def test_an_invalid_radius_is_refused(idata, radius):
    with pytest.raises(InvestigationError) as error:
        nearby_sites(idata.analyst, "cand-a", radius)
    assert error.value.code == "invalid_arguments" and "radius_m" in error.value.message


@pytest.mark.parametrize("candidate_id", ["", None, 7])
def test_an_invalid_candidate_id_is_refused(idata, candidate_id):
    with pytest.raises(InvestigationError) as error:
        nearby_sites(idata.analyst, candidate_id, 1000)
    assert error.value.code == "invalid_arguments"


def test_an_unknown_site_is_a_structured_error(idata):
    with pytest.raises(InvestigationError, match="unknown_site") as error:
        nearby_sites(idata.analyst, "cand-does-not-exist", 1000)
    assert error.value.code == "unknown_site"
    assert error.value.to_dict()["code"] == "unknown_site"


def test_the_radius_limit_is_the_service_radius_from_config(idata):
    limit = idata.analyst.config.settings.optimization.service_radius_m
    nearby_sites(idata.analyst, "cand-a", limit)
    with pytest.raises(InvestigationError, match=f"between 1 and {limit}"):
        nearby_sites(idata.analyst, "cand-a", limit + 1)


def test_the_argument_model_is_strict():
    assert NearbyArgs(candidate_id="cand-a", radius_m=5).radius_m == 5
    for bad in (
        {"candidate_id": "cand-a", "radius_m": 5.0},
        {"candidate_id": "cand-a", "radius_m": "5"},
        {"candidate_id": "cand-a", "radius_m": 0},
        {"candidate_id": "", "radius_m": 5},
        {"candidate_id": "cand-a"},
        {"candidate_id": "cand-a", "radius_m": 5, "lat": 1.0},
    ):
        with pytest.raises(ValidationError):
            NearbyArgs(**bad)


# --- Registration and isolation -------------------------------------------------------------------


def test_the_two_tools_are_listed_but_not_in_the_m9_registry(idata):
    assert [t.name for t in INVESTIGATION_TOOLS] == ["network_summary", "nearby_sites"]
    assert sorted(REGISTRY) == sorted(TOOLS) and len(TOOLS) == 6
    assert not {"network_summary", "nearby_sites"} & set(TOOLS)
    summary_tool, nearby_tool = INVESTIGATION_TOOLS
    assert summary_tool.call(idata, summary_tool.args_model()) == network_summary(idata)
    args = nearby_tool.args_model(candidate_id="cand-a", radius_m=1)
    assert nearby_tool.call(idata, args) == nearby_sites(idata.analyst, "cand-a", 1)


PACKAGE = sorted((PROJECT_ROOT / "src" / "sitescout" / "investigation").glob("*.py"))
FORBIDDEN_TOP = {
    "openai",
    "anthropic",
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "http",
    "socket",
    "ssl",
    "subprocess",
}
FORBIDDEN_ANALYST = {
    "run", "provider", "provider_common", "openai_provider", "anthropic_provider", "factory",
    "fake_model", "credentials", "validate", "ask", "scenarios",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


@pytest.mark.parametrize(
    "path", [*PACKAGE, PROJECT_ROOT / "scripts" / "investigate.py"], ids=lambda p: p.name
)
def test_the_tools_import_no_model_provider_knowledge_or_network_code(path):
    for module in _imports(path):
        parts = module.split(".")
        assert parts[0] not in FORBIDDEN_TOP, module
        assert not module.startswith("sitescout.knowledge"), module
        if module.startswith("sitescout.analyst."):
            assert parts[2] not in FORBIDDEN_ANALYST, module


@pytest.mark.parametrize("path", PACKAGE, ids=lambda p: p.name)
def test_the_tools_never_write_or_read_the_environment(path):
    source = path.read_text(encoding="utf-8")
    for call in (
        "write_bytes",
        "write_text",
        "open(",
        "mkdir(",
        "os.environ",
        "getenv",
        "to_parquet",
        "write_layer",
        "write_json",
    ):
        assert call not in source, f"{path.name} uses {call}"


def test_importing_the_package_loads_no_sdk_provider_or_knowledge_module():
    code = (
        "import sys, sitescout.investigation\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('openai', 'anthropic', 'httpx', 'httpx2') "
        "or m.startswith(('sitescout.knowledge', 'sitescout.analyst.openai_provider', "
        "'sitescout.analyst.anthropic_provider', 'sitescout.analyst.provider_common', "
        "'sitescout.analyst.factory', 'sitescout.analyst.credentials'))]\n"
        "print(bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


# --- Real processed data --------------------------------------------------------------------

REAL = PROJECT_ROOT / "data" / "processed"
needs_real = pytest.mark.skipif(
    not (REAL / "network.json").is_file() or not (REAL / "evidence.json").is_file(),
    reason="real processed outputs are not present",
)


@pytest.fixture(scope="module")
def real():
    config = load_config()
    return InvestigationData.load(config, config.resolve(config.settings.paths.processed_dir))


@needs_real
def test_real_network_summary_reads_network_json_and_agrees_with_the_m9_context(real):
    raw = json.loads((REAL / "network.json").read_text(encoding="utf-8"))
    summary = network_summary(real)
    records = _records(summary)
    for key in METHODS:
        share = raw["selections"][key]["population_covered_share"]
        assert records[f"network/{key}/population_covered_share"].display == percent(share, 1)
        assert records[f"network/{key}/sites"].evidence.value == raw["parameters"]["n_sites"]
    context = {r.id: r.display for r in real.analyst.context}
    assert (
        records["network/mclp/population_covered_share"].display
        == context["context/network_population"]
    )
    assert (
        records["network/top30/population_covered_share"].display
        == context["context/top30_population"]
    )
    assert records["network/eligible_sites"].evidence.value == raw["eligible_sites"]
    assert len(summary.sensitivity) == len(raw["sensitivity"]) == 6
    assert network_summary(real).model_dump_json() == summary.model_dump_json()


@needs_real
def test_real_nearby_sites_agrees_with_an_independent_count_and_with_network_contribution(real):
    data, limit = real.analyst, real.analyst.config.settings.optimization.service_radius_m
    cap = data.config.settings.investigation.nearby_sites.max_results
    rich = max(
        data.sites.index,
        key=lambda c: sum(
            1
            for o in data.points.index
            if o != c and data.points[c].distance(data.points[o]) <= limit
        ),
    )
    result = nearby_sites(data, rich, limit)
    expected = sorted(
        (float(data.points[rich].distance(data.points[o])), o)
        for o in data.points.index
        if o != rich
    )
    within = [(d, o) for d, o in expected if d <= limit]
    assert result.count_within_radius == len(within) and len(within) > cap
    assert result.returned_count == cap and result.truncated is True
    assert _neighbours(result) == [o for _, o in within[:cap]]
    for cid in _neighbours(result):
        assert _distance(result, cid).evidence.value == pytest.approx(
            dict((o, d) for d, o in within)[cid]
        )
    for target in data.sites.index:
        contribution = network_contribution(data, target)
        if contribution.in_network or contribution.nearest_network_site is None:
            continue
        gap = next(
            r for r in contribution.records if r.id.endswith("nearest_network_site_distance")
        )
        if gap.evidence.value <= limit:
            got = _distance(nearby_sites(data, target, limit), contribution.nearest_network_site)
            assert got.evidence.value == gap.evidence.value and got.display == gap.display
            break
    else:
        pytest.fail("no candidate has a network site within the service radius")


@needs_real
def test_the_real_tools_write_nothing_and_leave_the_export_untouched(real):
    export = PROJECT_ROOT / "data" / "export"
    before_export, before_processed = _snapshot(export), _snapshot(REAL, hashed=False)
    network_summary(real)
    nearby_sites(real.analyst, real.analyst.sites.index[0], 1000)
    assert _snapshot(export) == before_export and _snapshot(REAL, hashed=False) == before_processed
