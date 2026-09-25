"""The demo export (SPEC §10; D-048), on the SYNTHETIC world."""

import json

import pytest

import sitescout.export as export_module
from sitescout.briefs import GRID_DISCLAIMER, NOTICE, run_briefs
from sitescout.evaluation import run_evaluation
from sitescout.evidence import percent
from sitescout.export import INDEPENDENT, Export, ExportError, run_export
from sitescout.features import run_features
from sitescout.optimize import run_network
from sitescout.scoring import run_scores
from synthetic_features import feature_config, write_feature_world


@pytest.fixture
def world(settings_data, weights_data, dirs):
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 1})
    processed, work = dirs["processed"], dirs["work"]
    write_feature_world(processed, config.settings)
    run_features(config.settings, processed)
    run_scores(config.settings, config.weights, processed)
    run_network(config, processed)
    run_briefs(config, processed, work / "briefs")
    run_evaluation(config, processed, work / "evaluation.md")
    return config, processed, work / "export" / "sitescout.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_export_validates_and_holds_every_candidate(world):
    config, processed, out = world
    run_export(config, processed, out)
    data = _load(out)
    Export.model_validate(data)
    sites = data["sites"]
    assert len(sites) == 3
    for flag in ("selected_mclp", "selected_greedy", "selected_top30"):
        assert sum(site[flag] for site in sites) == 1
    assert [site["rank"] for site in sites] == sorted(site["rank"] for site in sites)


def test_headline_values_come_from_network_json(world):
    config, processed, out = world
    run_export(config, processed, out)
    network = _load(processed / "network.json")
    for selection in _load(out)["headline"]["selections"]:
        summary = network["selections"][selection["key"]]
        assert selection["population_display"] == percent(summary["population_covered_share"], 1)
        assert selection["population_share"] == summary["population_covered_share"]
        assert selection["districts_display"] == str(summary["districts"])


def test_network_sites_carry_evidence_unknowns_and_actions_others_do_not(world):
    config, processed, out = world
    run_export(config, processed, out)
    evidence = _load(processed / "evidence.json")
    for site in _load(out)["sites"]:
        if site["selected_mclp"]:
            assert site["evidence"] == evidence["sites"][site["candidate_id"]]
            unknowns = [u.lower() for u in site["brief"]["unknowns"]]
            for unknown in config.settings.confidence.universal_unknowns:
                assert unknown in unknowns
            assert site["brief"]["actions"] and site["unique_coverage"].endswith("%")
            assert "**" not in site["brief"]["opportunity"]
        else:
            assert site["evidence"] is None and site["brief"] is None
            assert site["unique_coverage"] is None


def test_chargers_are_positions_only_and_outlines_are_small(world):
    config, processed, out = world
    run_export(config, processed, out)
    data = _load(out)
    assert data["existing_chargers"] and all(
        set(c) == {"lat", "lon"} for c in data["existing_chargers"]
    )
    assert (
        len(json.dumps(data["districts"]).encode()) / 1024
        <= config.settings.export.boundary_geojson_max_kb
    )
    assert {f["properties"]["district"] for f in data["districts"]["features"]} == {
        "SYNTHETIC District 1",
        "SYNTHETIC District 2",
    }
    assert data["meta"]["disclaimers"] == [INDEPENDENT, GRID_DISCLAIMER, NOTICE]
    assert data["meta"]["data_status"] == "pipeline"


def test_the_js_file_holds_the_same_data(world):
    config, processed, out = world
    run_export(config, processed, out)
    js = out.with_suffix(".js").read_text(encoding="utf-8")
    prefix, suffix = "window.SITESCOUT = ", ";\n"
    assert js.startswith(prefix) and js.endswith(suffix)
    assert json.loads(js[len(prefix) : -len(suffix)]) == _load(out)


def test_a_second_export_differs_only_in_generated_at(world):
    config, processed, out = world
    run_export(config, processed, out)
    first = _load(out)
    run_export(config, processed, out)
    second = _load(out)
    first["meta"].pop("generated_at"), second["meta"].pop("generated_at")
    assert first == second


def test_no_business_names_reach_the_export(world):
    config, processed, out = world
    run_export(config, processed, out)
    text = out.read_text(encoding="utf-8")
    for name in ("SYNTHETIC Brand", "SYNTHETIC Operator", "SYNTHETIC Mall", "SYNTHETIC Diner"):
        assert name not in text


def test_an_oversized_export_writes_nothing(world, monkeypatch):
    config, processed, out = world
    monkeypatch.setattr(export_module, "MAX_BYTES", 1_000)
    with pytest.raises(ExportError, match="above 1000"):
        run_export(config, processed, out)
    assert not out.exists() and not out.with_suffix(".js").exists()


def test_evidence_out_of_step_with_the_network_is_refused(world):
    config, processed, out = world
    evidence = _load(processed / "evidence.json")
    evidence["sites"] = {}
    (processed / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ExportError, match="does not match the network"):
        run_export(config, processed, out)
