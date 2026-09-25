"""Access, reported-only features and the written feature layers (SYNTHETIC; D-032, D-035)."""

import hashlib
import json

import numpy as np
import pandas as pd
import pyproj
import pytest

from sitescout.features import FeatureError, build_features, load_inputs, run_features
from sitescout.features.build import check_values
from sitescout.ingest.layers import (
    FEATURES_BACKTEST,
    FEATURES_PRODUCTION,
    layer_path,
    metadata_path,
    read_layer,
)
from synthetic import _poi, _road
from synthetic_features import (
    KIGALI_TEST_NODE,
    at,
    build,
    candidate,
    feature_candidates,
    feature_config,
    feature_places,
    feature_power,
    line_at,
    place,
    write_feature_world,
)

NULLABLE = {
    "host_osm_id",
    "dist_trunk_m",
    "dist_charger_m",
    "dist_substation_m",
    "dist_line_m",
    "dist_town_m",
}


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


# --- Access ------------------------------------------------------------------------------------


def test_trunk_distance(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    assert frame.loc["cand-a", "dist_trunk_m"] == pytest.approx(3000, abs=0.5)
    assert frame.loc["cand-b", "dist_trunk_m"] == pytest.approx(3000, abs=0.5)
    assert frame.loc["cand-c", "dist_trunk_m"] == pytest.approx(2000, abs=0.5)  # trunk_link


def test_trunk_distance_agrees_with_the_geodesic_distance(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    a, north = at(0, 0), at(0, 3000)
    _, _, geodesic = pyproj.Geod(ellps="WGS84").inv(a.x, a.y, north.x, north.y)
    assert frame.loc["cand-a", "dist_trunk_m"] == pytest.approx(geodesic, rel=0.0025)


def test_the_trunk_classes_come_from_config(settings_data, weights_data, dirs):
    trunk_only = feature_config(
        settings_data, weights_data, **{"settings.features.trunk_road_classes": ["trunk"]}
    )
    frame = build(dirs["processed"], trunk_only)[0]
    assert frame.loc["cand-c", "dist_trunk_m"] == pytest.approx(15000, abs=0.5)


def test_no_trunk_roads_gives_a_null_distance(fconfig, dirs):
    residential = [_road(3, "residential", line_at([(-1000, 100), (1000, 100)], step=100))]
    frame, stats = build(dirs["processed"], fconfig, roads=residential)
    assert frame["dist_trunk_m"].isna().all()
    assert stats["trunk_roads"] == 0


def test_road_distance_and_class_are_m2_values(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    expected = {row["candidate_id"]: row for row in feature_candidates()}
    for candidate_id, row in frame.iterrows():
        assert row["dist_road_m"] == expected[candidate_id]["dist_road_m"]
        assert row["road_class"] == expected[candidate_id]["nearest_road_class"]


# --- Reported only -----------------------------------------------------------------------------


def test_distance_to_the_kigali_centre(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    assert frame.loc["cand-a", "dist_kigali_cbd_m"] == pytest.approx(5000, abs=1e-6)
    assert frame.loc["cand-c", "dist_kigali_cbd_m"] == pytest.approx(13000, abs=1e-6)


def test_distance_to_the_nearest_town_or_city_inside_rwanda(fconfig, dirs):
    frame, stats = build(dirs["processed"], fconfig)
    assert frame.loc["cand-a", "dist_town_m"] == pytest.approx(5000, abs=1e-6)  # the city
    assert frame.loc["cand-c", "dist_town_m"] == pytest.approx(3000, abs=1e-6)  # a town
    # node/62 lies outside the country, 22 km from B: it is not a centre, so B's nearest is
    # the city 28 km away.
    assert frame.loc["cand-b", "dist_town_m"] == pytest.approx(28000, abs=1e-6)
    assert stats["places"]["town_centres"] == 2
    assert stats["places"]["place_nodes_outside_rwanda"] == 1


@pytest.mark.parametrize(
    "places",
    [
        [row for row in feature_places() if row["feature_id"] != KIGALI_TEST_NODE],
        [place(KIGALI_TEST_NODE, "town", at(5000, 0)), *feature_places()[1:]],
    ],
    ids=["missing", "not-a-city"],
)
def test_the_kigali_node_must_be_a_city_node_in_the_extract(fconfig, dirs, places):
    with pytest.raises(FeatureError, match="Kigali city centre as mapped in OSM"):
        build(dirs["processed"], fconfig, places=places)


def test_the_border_diagnostic(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    assert frame.loc["cand-a", "outside_rwanda_share_10km"] == 0.0
    # C is 12 km south of A, 0.108 degrees from the country's southern edge at 2.10 S.
    assert 0.1 < frame.loc["cand-c", "outside_rwanda_share_10km"] < 0.4


def test_the_border_diagnostic_never_changes_population(settings_data, weights_data, dirs):
    frame = build(dirs["processed"], feature_config(settings_data, weights_data))[0]
    # C's circle is partly outside Rwanda; the population sums are only what the raster has.
    assert frame.loc["cand-c", ["pop_1km", "pop_5km", "pop_10km"]].tolist() == [0.0, 0.0, 0.0]


# --- The written layers ------------------------------------------------------------------------


@pytest.fixture
def written(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)
    run_features(fconfig.settings, dirs["processed"])
    return {
        mode: read_layer(schema.name, dirs["processed"], fconfig.settings)
        for mode, schema in (("production", FEATURES_PRODUCTION), ("backtest", FEATURES_BACKTEST))
    }


def test_both_layers_have_every_candidate_and_the_schema(fconfig, dirs, written):
    candidates = read_layer("candidates", dirs["processed"], fconfig.settings)
    for mode, schema in (("production", FEATURES_PRODUCTION), ("backtest", FEATURES_BACKTEST)):
        frame = written[mode]
        assert len(frame) == len(candidates) == 3
        assert list(frame.columns) == [*schema.column_names, "geometry"]
        assert list(frame["candidate_id"]) == sorted(candidates["candidate_id"])
        assert frame.crs.to_epsg() == 4326
        assert (frame.geom_type == "Point").all()
        assert frame.geometry.geom_equals_exact(candidates.geometry, tolerance=0).all()
        for column in schema.columns:
            expected = {"string": "str", "int64": "int64", "float64": "float64"}[column.kind]
            assert str(frame[column.name].dtype) == expected, column.name


def test_only_documented_columns_hold_nulls(written):
    for frame in written.values():
        nulls = {name for name in frame.columns if frame[name].isna().any()}
        assert nulls <= NULLABLE


def test_values_stay_within_their_bounds(written):
    for frame in written.values():
        numeric = frame.select_dtypes("number")
        values = numeric.to_numpy(dtype=np.float64)
        assert (values[~np.isnan(values)] >= 0).all()
        assert (frame["pop_1km"] <= frame["pop_5km"]).all()
        assert (frame["pop_5km"] <= frame["pop_10km"]).all()
        assert (frame["poi_1km"] <= frame["poi_3km"]).all()
        assert (frame["chargers_10km"] <= frame["chargers_25km"]).all()
        assert frame["outside_rwanda_share_10km"].between(0, 1).all()


def test_values_out_of_bounds_are_refused(written):
    frame = written["production"].copy()
    frame.loc[0, "pop_1km"] = frame.loc[0, "pop_5km"] + 1
    with pytest.raises(FeatureError, match="pop_1km exceeds pop_5km"):
        check_values(frame, "production", list(frame["candidate_id"]))
    frame = written["backtest"].copy()
    frame.loc[0, "chargers_25km"] = 1
    with pytest.raises(FeatureError, match="backtest mode has charging-gap values"):
        check_values(frame, "backtest", list(frame["candidate_id"]))


def test_metadata(fconfig, dirs, written):
    for name, mode in (("features_production", "production"), ("features_backtest", "backtest")):
        meta = json.loads(metadata_path(dirs["processed"], name).read_text(encoding="utf-8"))
        stats = meta["stats"]
        assert stats["mode"] == mode
        assert {"candidates", "osm_pois", "osm_power", "population_worldpop"} <= set(
            stats["inputs"]
        )
        assert stats["manual_chargers"].startswith("missing")
        assert stats["settings_used"]["sources.charger_match_radius_m"] == 50
        assert set(stats["grid_completeness"]["districts"]) == {"SYN-D1", "SYN-D2"}
        assert set(stats["decisions"]) == {f"D-0{n}" for n in range(32, 39)}
        notes = " ".join(meta["notes"])
        for phrase in ("deferred", "border", "50 m", "Kigali city centre as mapped in OSM"):
            assert phrase in notes
    production = json.loads(
        metadata_path(dirs["processed"], "features_production").read_text(encoding="utf-8")
    )["stats"]["chargers"]
    assert production["sources"]["chargers_manual"]["status"].startswith("missing")


def test_repeated_runs_give_byte_identical_layers(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)

    def digests():
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ("features_production", "features_backtest")
            for path in (
                layer_path(dirs["processed"], name),
                metadata_path(dirs["processed"], name),
            )
        }

    run_features(fconfig.settings, dirs["processed"])
    first = digests()
    run_features(fconfig.settings, dirs["processed"])
    assert digests() == first


def test_input_row_order_does_not_matter(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)
    inputs = load_inputs(dirs["processed"], fconfig.settings)
    shuffled = type(inputs)(
        {
            name: frame.sample(frac=1.0, random_state=3).reset_index(drop=True)
            for name, frame in inputs.layers.items()
        },
        inputs.fingerprints,
        inputs.manual,
        inputs.manual_status,
        inputs.population,
    )
    for mode in ("production", "backtest"):
        first = build_features(inputs, fconfig.settings, mode)[0]
        second = build_features(shuffled, fconfig.settings, mode)[0]
        pd.testing.assert_frame_equal(
            pd.DataFrame(first.drop(columns="geometry")),
            pd.DataFrame(second.drop(columns="geometry")),
        )


def test_a_world_with_one_candidate(fconfig, dirs):
    frame = build(dirs["processed"], fconfig, candidates=feature_candidates()[:1])[0]
    assert list(frame.index) == ["cand-a"]
    assert frame.loc["cand-a", "poi_1km"] == 2


def test_empty_source_layers(fconfig, dirs):
    only_charger_poi = [_poi("node/13", at(200, 0), amenity="charging_station")]
    no_trunk = [_road(3, "residential", line_at([(-1000, 100), (1000, 100)], step=100))]
    no_substation = [row for row in feature_power() if row["power"] != "substation"]
    write_feature_world(
        dirs["processed"],
        fconfig.settings,
        pois=only_charger_poi,
        roads=no_trunk,
        chargers=[],
        power_rows=no_substation,
    )
    run_features(fconfig.settings, dirs["processed"])
    for name in ("features_production", "features_backtest"):
        frame = read_layer(name, dirs["processed"], fconfig.settings)
        assert frame["dist_trunk_m"].isna().all()
        assert frame["dist_charger_m"].isna().all()
        assert frame["dist_substation_m"].isna().all()
        assert (frame["poi_3km"] == 0).all() and (frame["chargers_25km"] == 0).all()


def test_a_failure_writes_nothing_and_removes_old_layers(fconfig, dirs):
    write_feature_world(dirs["processed"], fconfig.settings)
    run_features(fconfig.settings, dirs["processed"])
    no_kigali = [row for row in feature_places() if row["feature_id"] != KIGALI_TEST_NODE]
    write_feature_world(dirs["processed"], fconfig.settings, places=no_kigali)
    with pytest.raises(FeatureError):
        run_features(fconfig.settings, dirs["processed"])
    for name in ("features_production", "features_backtest"):
        assert not layer_path(dirs["processed"], name).exists()
        assert not metadata_path(dirs["processed"], name).exists()


def test_every_candidate_takes_its_districts_ratio(fconfig, dirs):
    extra = candidate("cand-d", at(40000, -5000), district="SYN-D2")
    rows = [*feature_candidates(), extra]
    frame = build(dirs["processed"], fconfig, candidates=rows)[0]
    assert frame["grid_completeness_ratio"].notna().all()
    assert (
        frame.loc["cand-d", "grid_completeness_ratio"]
        == frame.loc["cand-b", "grid_completeness_ratio"]
    )
