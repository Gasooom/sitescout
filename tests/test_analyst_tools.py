"""The deterministic SiteScout Analyst tools (M9 phase 1; SPEC §11). SYNTHETIC and real data."""

import hashlib
import json
import subprocess
import sys
import tomllib

import pandas as pd
import pytest
from pydantic import ValidationError

from sitescout.analyst import (
    AnalystData,
    AnalystError,
    FindQuery,
    compare_sites,
    explain_score,
    find_sites,
    generate_brief,
    get_site,
    network_contribution,
)
from sitescout.analyst.tools import CompareResult, ComparisonField, established_reasons
from sitescout.briefs import NUMBER
from sitescout.config import PROJECT_ROOT, load_config
from sitescout.evidence import one_decimal

FORBIDDEN_FIELDS = (
    "winner",
    "better",
    "worse",
    "best",
    "prefer",
    "recommend",
    "total",
    "overall",
    "ranking",
)


@pytest.fixture
def world(analyst_world):
    """The SYNTHETIC world through M6 (one network site) and M7 (its brief), built once per
    module (conftest.analyst_world)."""
    return analyst_world


@pytest.fixture
def data(world):
    config, processed, _ = world
    return AnalystData.load(config, processed)


def _network(processed) -> pd.DataFrame:
    return pd.read_parquet(processed / "network.parquet").set_index("candidate_id")


# --- find_sites -------------------------------------------------------------------------------


def test_find_sites_filters_exactly_and_keeps_rank_order(data, world):
    everything = find_sites(data, FindQuery())
    assert everything.count == 3
    assert [s.rank for s in everything.sites] == sorted(s.rank for s in everything.sites)
    network = _network(world[1])
    chosen = find_sites(data, FindQuery(selected_mclp=True))
    assert [s.candidate_id for s in chosen.sites] == list(network.index[network["selected_mclp"]])
    in_d2 = find_sites(data, FindQuery(district="SYNTHETIC District 2"))
    assert [s.candidate_id for s in in_d2.sites] == ["cand-b"]
    assert find_sites(data, FindQuery(label="SYNTHETIC fuel, SYNTHETIC District 1")).count == 1
    assert find_sites(data, FindQuery(label="SYNTHETIC fuel")).count == 0  # no fuzzy match
    ranked = everything.sites[0]
    assert (
        find_sites(data, FindQuery(rank=ranked.rank)).sites[0].candidate_id == ranked.candidate_id
    )


def test_find_sites_records_resolve_to_their_sites(data):
    result = find_sites(data, FindQuery())
    ids = {r.id for r in result.records}
    for site in result.sites:
        assert set(site.record_ids) <= ids
        assert all(i.startswith(f"{site.candidate_id}/") for i in site.record_ids)


def test_find_query_accepts_only_approved_filters():
    with pytest.raises(ValidationError):
        FindQuery(similar_to="fuel")
    with pytest.raises(ValidationError):
        FindQuery(rank=0)
    with pytest.raises(ValidationError):
        FindQuery(confidence="Very high")


# --- get_site ---------------------------------------------------------------------------------


def test_get_site_values_match_the_source_layers(data, world):
    scores = pd.read_parquet(world[1] / "scores_production.parquet").set_index("candidate_id")
    for candidate_id in scores.index:
        result = get_site(data, candidate_id)
        records = {r.id: r for r in result.records}
        row = scores.loc[candidate_id]
        assert records[f"{candidate_id}/score"].display == one_decimal(row["score"])
        assert records[f"{candidate_id}/rank"].evidence.value == row["rank"]
        assert records[f"{candidate_id}/confidence"].display == row["confidence"]
        for record_id in result.component_record_ids:
            name = record_id.split("/", 1)[1]
            assert records[record_id].evidence.value == pytest.approx(row[name])


def test_network_and_other_sites(data, world):
    network = _network(world[1])
    for candidate_id, row in network.iterrows():
        result = get_site(data, candidate_id)
        ids = {r.id.split("/", 1)[1] for r in result.records}
        if row["selected_mclp"]:
            assert result.in_network and result.actions and result.note is None
            assert "marginal_coverage" in ids
        else:
            assert not result.in_network and result.actions == [] and "Not selected" in result.note
            assert "marginal_coverage" not in ids  # its M7 claim only describes network sites
        for unknown in data.config.settings.confidence.universal_unknowns:
            assert unknown.capitalize() in result.unknowns


# --- compare_sites ----------------------------------------------------------------------------


def test_compare_sites_has_no_winner_ranking_or_preference_field():
    names = set(CompareResult.model_fields) | set(ComparisonField.model_fields)
    assert not [n for n in names for word in FORBIDDEN_FIELDS if word in n]


def test_compare_sites_relations_are_neutral_and_exact(data):
    result = compare_sites(data, "cand-a", "cand-c")
    records = {r.id: r for r in result.records}
    for field in result.fields:
        a, b = records[field.a_record_id], records[field.b_record_id]
        assert (field.a_display, field.b_display) == (a.display, b.display)
        va, vb = a.evidence.value, b.evidence.value
        if field.kind == "numeric":
            expected = (
                None
                if va is None or vb is None
                else ("equal" if va == vb else "a_greater" if va > vb else "b_greater")
            )
            assert field.relation == expected, field.field
        else:
            assert field.relation == ("same" if va == vb else "different"), field.field
        # No new number: the row's display holds only the two cited displays.
        assert set(NUMBER.findall(field.display)) <= set(
            NUMBER.findall(a.display + " " + b.display)
        )


def test_compare_sites_refuses_one_site_or_unknown_ids(data):
    with pytest.raises(AnalystError, match="two different"):
        compare_sites(data, "cand-a", "cand-a")
    with pytest.raises(AnalystError, match="Unknown site id"):
        compare_sites(data, "cand-a", "cand-zzz")


# --- explain_score ----------------------------------------------------------------------------


def test_explain_score_exposes_the_stored_parts(data, world):
    config, processed, _ = world
    scores = pd.read_parquet(processed / "scores_production.parquet").set_index("candidate_id")
    result = explain_score(data, "cand-a")
    records = {r.id: r for r in result.records}
    profile = scores.loc["cand-a", "profile"]
    for component in result.components:
        assert records[component.value_record_id].evidence.value == pytest.approx(
            scores.loc["cand-a", f"component_{component.component}"]
        )
        weight = getattr(getattr(config.weights.profiles, profile), component.component)
        assert records[component.profile_weight_record_id].evidence.value == weight
        for feature in component.features:
            expected = getattr(
                getattr(config.weights.components, component.component), feature.feature
            )
            assert records[feature.weight_record_id].evidence.value == expected
            points = scores.loc["cand-a", f"pct_{feature.feature}"]
            assert records[feature.points_record_id].evidence.value == pytest.approx(points)
    bonuses = {c.component: c.bonus_record_id for c in result.components if c.bonus_record_id}
    assert set(bonuses) == {"host_commercial", "access"}
    assert str(config.settings.scoring.scale_max) in records[result.method_record_id].display


# --- network_contribution ---------------------------------------------------------------------


def test_established_reasons_are_only_the_m6_rules():
    base = {"hostless": False, "require_host": True, "percentile": 80.0, "threshold": 50.0}
    assert established_reasons(**base, conflicts=False) == []
    assert established_reasons(**{**base, "hostless": True}, conflicts=False) == ["no_host"]
    assert established_reasons(**{**base, "percentile": 49.9}, conflicts=False) == [
        "score_below_eligibility_percentile"
    ]
    assert established_reasons(**base, conflicts=True) == ["spacing_conflict"]
    assert (
        established_reasons(**{**base, "hostless": True, "require_host": False}, conflicts=False)
        == []
    )


def test_network_contribution_by_hand(data, world):
    network = _network(world[1])
    selected = network.index[network["selected_mclp"]][0]
    chosen = network_contribution(data, selected)
    assert chosen.status == "selected" and chosen.reason_status == "NOT_APPLICABLE"
    # cand-b is a corridor point without a host: the M6 rule excludes it.
    hostless = network_contribution(data, "cand-b")
    assert hostless.status == "not_selected" and "no_host" in hostless.reasons
    assert hostless.reason_status == "ESTABLISHED"
    for candidate_id in network.index:
        result = network_contribution(data, candidate_id)
        records = {r.id.split("/", 1)[-1]: r for r in result.records}
        if candidate_id != selected:
            assert result.nearest_network_site == selected
        if not result.in_network and not result.reasons:
            assert result.reason_status == "UNKNOWN"  # no invented reason
        assert (result.spacing_conflicts != []) == ("spacing_conflict" in result.reasons)
        assert records["marginal_coverage"].evidence.value == pytest.approx(
            network.loc[candidate_id, "marginal_coverage"]
        )


def test_network_contribution_distance_by_hand(data):
    # The layout puts C 12 km south of A (tests/synthetic_features.py). Whichever of them is
    # the network site, the other one's nearest network site is 12 km away.
    for a, b in (("cand-a", "cand-c"), ("cand-c", "cand-a")):
        result = network_contribution(data, a)
        if result.nearest_network_site == b:
            distances = [
                r for r in result.records if r.id.endswith("nearest_network_site_distance")
            ]
            assert distances[0].display == "12.0 km"
            assert distances[0].evidence.value == pytest.approx(12_000, abs=1)


# --- generate_brief ---------------------------------------------------------------------------


def test_generate_brief_matches_the_m7_brief(data, world):
    _, processed, briefs = world
    network = _network(processed)
    for candidate_id, row in network.iterrows():
        result = generate_brief(data, candidate_id)
        if row["selected_mclp"]:
            assert result.markdown == (briefs / f"{candidate_id}.md").read_text(encoding="utf-8")
            assert result.sections["actions"] and result.brief_path.endswith(f"{candidate_id}.md")
        else:
            assert result.markdown is None and result.sections is None
            assert "not selected" in result.note


# --- Shared guarantees ------------------------------------------------------------------------


@pytest.mark.parametrize("tool", [get_site, explain_score, network_contribution, generate_brief])
def test_unknown_site_ids_are_refused(data, tool):
    with pytest.raises(AnalystError, match="Unknown site id"):
        tool(data, "cand-does-not-exist")


def _every_call(data):
    per_site = (get_site, explain_score, network_contribution, generate_brief)
    calls = [find_sites(data, FindQuery())]
    calls += [tool(data, cid) for cid in ("cand-a", "cand-b", "cand-c") for tool in per_site]
    calls.append(compare_sites(data, "cand-a", "cand-b"))
    return calls


def test_repeated_calls_give_identical_results(world):
    config, processed, _ = world
    first = [r.model_dump() for r in _every_call(AnalystData.load(config, processed))]
    second = [r.model_dump() for r in _every_call(AnalystData.load(config, processed))]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_no_tool_writes_anything(world):
    config, processed, _ = world

    def snapshot():
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(processed.iterdir())
        }

    before = snapshot()
    _every_call(AnalystData.load(config, processed))
    assert snapshot() == before


def test_the_tools_need_no_ai_package():
    code = (
        "import sys, sitescout.analyst, sitescout.analyst.tools; "
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'openai', 'langchain', 'langgraph', 'chromadb', 'faiss', 'transformers')]; "
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
    # Phase 3b (D-051) adds the Anthropic SDK as an optional extra only: the base install,
    # which every tool runs on, stays AI-free.
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = " ".join(project["project"]["dependencies"]).lower()
    for package in ("anthropic", "openai", "langchain", "chromadb", "faiss"):
        assert package not in base


# --- Real processed data ----------------------------------------------------------------------

REAL = PROJECT_ROOT / "data" / "processed"
needs_real = pytest.mark.skipif(
    not (REAL / "evidence.json").is_file(), reason="real processed outputs are not present"
)


@pytest.fixture(scope="module")
def real():
    config = load_config()
    return AnalystData.load(config, config.resolve(config.settings.paths.processed_dir))


@needs_real
def test_real_counts(real):
    assert find_sites(real, FindQuery()).count == 300
    assert find_sites(real, FindQuery(selected_mclp=True)).count == 30
    assert find_sites(real, FindQuery(selected_top30=True)).count == 30


@needs_real
def test_real_network_sites_carry_exactly_the_m7_evidence(real):
    evidence = json.loads((REAL / "evidence.json").read_text(encoding="utf-8"))
    for candidate_id, rows in evidence["sites"].items():
        records = get_site(real, candidate_id).records
        m7 = {f"{candidate_id}/{r['id']}": r for r in rows}
        mine = {r.id: r.model_dump() for r in records if r.id in m7}
        assert set(mine) == set(m7)
        for record_id, row in m7.items():
            assert mine[record_id] == {**row, "id": record_id}


@needs_real
def test_real_briefs_are_reproduced_byte_for_byte(real):
    for candidate_id in json.loads((REAL / "evidence.json").read_text(encoding="utf-8"))["order"]:
        brief = (PROJECT_ROOT / "reports" / "briefs" / f"{candidate_id}.md").read_bytes()
        assert generate_brief(real, candidate_id).markdown.encode("utf-8") == brief


@needs_real
def test_real_network_contribution_is_consistent_for_every_candidate(real):
    for summary in find_sites(real, FindQuery()).sites:
        result = network_contribution(real, summary.candidate_id)  # raises if M6 disagrees
        assert (result.spacing_conflicts != []) == ("spacing_conflict" in result.reasons)
        if not result.in_network and not result.reasons:
            assert result.reason_status == "UNKNOWN"
