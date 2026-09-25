"""Evidence records, Site Evidence Briefs and the grounding check (SPEC §9; D-047). SYNTHETIC."""

import hashlib
import json

import pandas as pd
import pytest

import sitescout.briefs as briefs_module
from sitescout.briefs import (
    GRID_DISCLAIMER,
    NOTICE,
    PLACEHOLDER,
    TEMPLATE,
    BriefError,
    grounding,
    run_briefs,
)
from sitescout.evaluation import run_evaluation
from sitescout.evidence import (
    EvidenceError,
    distance,
    people,
    percent,
    record,
    site_records,
)
from sitescout.features import run_features
from sitescout.optimize import run_network
from sitescout.scoring import run_scores
from synthetic_features import at, feature_config, line_at, power, write_feature_world

# --- Display formats and the grounding check -------------------------------------------------


def test_display_formats():
    assert distance(16.2) == "16 m"
    assert distance(999.4) == "999 m"
    assert distance(1000.0) == "1.0 km"
    assert distance(83_149.0) == "83.1 km"
    assert distance(float("nan")) == distance(None) == "none mapped"
    assert people(58_240.9) == "58,241"
    assert percent(0.49761, 1) == "49.8%"
    assert percent(0.002790025, 2) == "0.28%"


def test_the_template_holds_no_number_of_its_own():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert briefs_module.NUMBER.findall(PLACEHOLDER.sub("", text)) == []
    assert NOTICE in text and GRID_DISCLAIMER in text


def test_grounding_finds_numbers_missing_from_the_evidence():
    records = [record("pop", "Population", "CALCULATED", "WorldPop", "pop", 58240.9, "58,241")]
    assert grounding("About 58,241 people.", records) == {
        "numbers": 1,
        "grounded": 1,
        "ungrounded": [],
    }
    result = grounding("About 58,241 people and 12 chargers.", records)
    assert result["ungrounded"] == ["12"] and result["grounded"] == 1


def test_a_site_without_a_host_is_refused(settings_data, weights_data):
    config = feature_config(settings_data, weights_data)
    with pytest.raises(EvidenceError, match="has no host"):
        site_records(pd.Series({"candidate_id": "cand-x", "host_type": "none"}), config)


# --- The whole run on the SYNTHETIC world --------------------------------------------------------


def _world(settings_data, weights_data, dirs, **world):
    config = feature_config(settings_data, weights_data, **{"settings.optimization.n_sites": 1})
    write_feature_world(dirs["processed"], config.settings, **world)
    run_features(config.settings, dirs["processed"])
    run_scores(config.settings, config.weights, dirs["processed"])
    run_network(config, dirs["processed"])
    return config, dirs["processed"], dirs["work"] / "briefs"


@pytest.fixture
def world(settings_data, weights_data, dirs):
    return _world(settings_data, weights_data, dirs)


def test_one_brief_per_network_site_all_numbers_grounded(world):
    config, processed, out = world
    result = run_briefs(config, processed, out)
    names = sorted(p.name for p in out.iterdir())
    selected = pd.read_parquet(processed / "network.parquet").query("selected_mclp")
    assert names == sorted([*(f"{cid}.md" for cid in selected["candidate_id"]), "README.md"])
    assert result["briefs"] == 1 and result["share"] == 1.0
    assert result["grounded"] == result["numbers"] > 0


def test_a_brief_answers_the_four_questions_and_keeps_the_disclaimers(world):
    config, processed, out = world
    run_briefs(config, processed, out)
    text = next(p for p in out.iterdir() if p.name.startswith("cand-")).read_text(encoding="utf-8")
    for heading in (
        "## Why this site was selected",
        "## Demand",
        "## Grid evidence",
        "## Unknowns",
        "## Recommended next actions",
    ):
        assert heading in text
    for unknown in config.settings.confidence.universal_unknowns:
        assert unknown.capitalize() in text
    assert GRID_DISCLAIMER in text and NOTICE in text
    assert "feasibility" not in text.replace(GRID_DISCLAIMER, "").lower()


def test_evidence_records_follow_the_spec_shape(world):
    config, processed, out = world
    run_briefs(config, processed, out)
    evidence = json.loads((processed / "evidence.json").read_text(encoding="utf-8"))
    kinds = {r["type"] for rs in evidence["sites"].values() for r in rs}
    assert kinds <= {"RETRIEVED_FACT", "CALCULATED", "INFERRED", "UNKNOWN"}
    unknowns = [r["claim"] for r in evidence["context"] if r["type"] == "UNKNOWN"]
    assert unknowns == list(config.settings.confidence.universal_unknowns)
    for rs in evidence["sites"].values():
        for r in rs:
            assert set(r) == {"id", "claim", "type", "evidence", "display"}
            assert set(r["evidence"]) == {"source", "metric", "value", "unit"}


def test_missing_grid_evidence_is_unknown_with_its_own_action(settings_data, weights_data, dirs):
    # Only far-away power features: no substation or line within 5 km of any candidate.
    far = [
        power("way/90", "line", line_at([(60_000, 20_000), (61_000, 20_000)], step=100)),
        power("node/91", "tower", at(-9_000, 9_000)),
        power("node/92", "tower", at(40_000, 12_000)),
    ]
    config, processed, out = _world(settings_data, weights_data, dirs, power_rows=far)
    run_briefs(config, processed, out)
    text = next(p for p in out.iterdir() if p.name.startswith("cand-")).read_text(encoding="utf-8")
    assert "Grid evidence: **missing**" in text
    assert "Request the utility's network map for this area" in text
    evidence = json.loads((processed / "evidence.json").read_text(encoding="utf-8"))
    status = [r for rs in evidence["sites"].values() for r in rs if r["id"] == "grid_status"]
    assert all(r["type"] == "UNKNOWN" for r in status)


def test_an_ungrounded_number_stops_the_run(world, monkeypatch):
    config, processed, out = world
    bad = out.parent / "bad_template.md"
    bad.write_text(TEMPLATE.read_text(encoding="utf-8") + "\nAbout 12345 more.\n", "utf-8")
    monkeypatch.setattr(briefs_module, "TEMPLATE", bad)
    with pytest.raises(BriefError, match="12345"):
        run_briefs(config, processed, out)
    assert not out.exists()  # nothing is written when grounding fails


def test_repeated_runs_give_identical_files_and_stale_briefs_go(world):
    config, processed, out = world
    run_briefs(config, processed, out)
    stale = out / "cand-000000000000.md"
    stale.write_text("SYNTHETIC stale brief", "utf-8")

    def digests():
        files = [*sorted(out.iterdir()), processed / "evidence.json", processed / "grounding.json"]
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

    run_briefs(config, processed, out)
    first = digests()
    assert "cand-000000000000.md" not in first
    run_briefs(config, processed, out)
    assert digests() == first


def test_the_evaluation_report_gains_the_grounding_section(world, dirs):
    config, processed, out = world
    run_briefs(config, processed, out)
    report = dirs["work"] / "evaluation.md"
    run_evaluation(config, processed, report)
    text = report.read_text(encoding="utf-8")
    assert "Site Evidence Briefs" in text and "(100.0%; target 100%)" in text
