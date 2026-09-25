"""The written score layers on the SYNTHETIC feature world (SPEC §5, §6; D-042)."""

import hashlib
import json

import pandas as pd
import pytest

from sitescout.features import run_features
from sitescout.ingest.layers import (
    SCORES_BACKTEST,
    SCORES_PRODUCTION,
    layer_path,
    metadata_path,
    read_layer,
)
from sitescout.scoring import run_scores
from synthetic_features import feature_config, write_feature_world

CHARGING_COLUMNS = {
    "component_charging_gap",
    "pct_dist_charger_m",
    "pct_chargers_10km",
    "pct_chargers_25km",
    "score",
    "rank",
}


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


@pytest.fixture
def world(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)
    run_features(fconfig.settings, dirs["processed"])
    return dirs["processed"]


def _run(config, processed):
    run_scores(config.settings, config.weights, processed)
    return {
        "production": read_layer("scores_production", processed, config.settings),
        "backtest": read_layer("scores_backtest", processed, config.settings),
    }


def test_both_layers_score_every_candidate_with_the_schema(fconfig, world):
    layers = _run(fconfig, world)
    features = read_layer("features_production", world, fconfig.settings)
    for mode, schema in (("production", SCORES_PRODUCTION), ("backtest", SCORES_BACKTEST)):
        table = layers[mode]
        assert len(table) == len(features) == 3
        assert list(table.columns) == [*schema.column_names, "geometry"]
        assert list(table["candidate_id"]) == list(features["candidate_id"])
        assert table.crs.to_epsg() == 4326
        assert table.geometry.geom_equals_exact(features.geometry, tolerance=0).all()
        assert not table.drop(columns="geometry").isna().any().any()


def test_backtest_charging_gap_is_25_and_the_modes_differ_only_there(fconfig, world):
    layers = _run(fconfig, world)
    production, backtest = layers["production"], layers["backtest"]
    assert (backtest["component_charging_gap"] == 25.0).all()
    differ = {
        name
        for name in production.columns
        if name != "geometry" and not production[name].equals(backtest[name])
    }
    assert differ <= CHARGING_COLUMNS
    assert "component_charging_gap" in differ


def test_metadata_holds_weights_decisions_and_unknowns(fconfig, world):
    _run(fconfig, world)
    for name in ("scores_production", "scores_backtest"):
        meta = json.loads(metadata_path(world, name).read_text(encoding="utf-8"))
        stats = meta["stats"]
        assert stats["weights"]["profiles"]["corridor"]["charging_gap"] == 0.25
        assert set(stats["decisions"]) == {"D-039", "D-040", "D-041", "D-042"}
        assert len(stats["universal_unknowns"]) == 5
        assert stats["settings_used"]["profile"] == {
            "town_radius_m": 3000,
            "kigali_radius_m": 10000,
        }
        assert sum(stats["by_confidence"].values()) == 3
        assert any("not compensated" in note for note in meta["notes"])


def test_repeated_runs_give_byte_identical_layers(fconfig, world):
    def digests():
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ("scores_production", "scores_backtest")
            for path in (layer_path(world, name), metadata_path(world, name))
        }

    _run(fconfig, world)
    first = digests()
    _run(fconfig, world)
    assert digests() == first


def test_a_failure_writes_nothing_and_removes_old_layers(fconfig, world):
    _run(fconfig, world)
    layer_path(world, "features_backtest").unlink()  # the backtest input disappears
    with pytest.raises(Exception, match="features_backtest"):
        run_scores(fconfig.settings, fconfig.weights, world)
    for name in ("scores_production", "scores_backtest"):
        assert not layer_path(world, name).exists()
        assert not metadata_path(world, name).exists()


def test_scores_explain_themselves(fconfig, world):
    table = _run(fconfig, world)["production"].set_index("candidate_id")
    weights = fconfig.weights.profiles
    for _, row in table.iterrows():
        profile = getattr(weights, row["profile"])
        rebuilt = sum(
            getattr(profile, name) * row[f"component_{name}"]
            for name in ("demand", "access", "host_commercial", "charging_gap", "grid_evidence")
        )
        assert row["score"] == pytest.approx(rebuilt)
    assert pd.Series(table["rank"]).is_unique
