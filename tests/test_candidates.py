"""Candidate generation (SPEC §3, Milestone 2) on a SYNTHETIC world (tests/synthetic.py)."""

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely

from sitescout.candidates import (
    CandidateBudgetError,
    classify_hosts,
    corridor_points,
    deduplicate,
    generate_candidates,
    load_inputs,
    run_candidates,
    snap_to_hosts,
)
from sitescout.config import build_config
from sitescout.ingest.layers import CANDIDATES, layer_path, metadata_path, read_layer
from synthetic import (
    INDUSTRIAL_BOX,
    MALL_BOX,
    PARK_BOX,
    TRUNK,
    WATER_BOX,
    candidate_pois,
    candidate_roads,
    synthetic_config,
    write_candidate_world,
)


@pytest.fixture
def config(settings_data, weights_data):
    """Real settings with a target range and budget that the SYNTHETIC world can meet.

    The budget equals the 8 eligible candidates, so these tests see every candidate; the
    budget itself is tested in test_candidate_budget.py.
    """
    base = synthetic_config(settings_data, weights_data)
    return build_config(
        base.snapshot()["settings"],
        weights_data,
        overrides={
            "settings.candidates.target_count.min": 1,
            "settings.candidates.target_count.max": 1000,
            "settings.candidates.budget.size": 8,
        },
    )


@pytest.fixture
def world(config, dirs) -> Path:
    write_candidate_world(dirs["processed"], config.settings)
    return dirs["processed"]


def _generate(config, processed):
    return generate_candidates(load_inputs(processed, config.settings), config.settings)


@pytest.fixture
def result(config, world):
    return _generate(config, world)


def _metric(frame):
    return frame.to_crs("EPSG:32735")


# --- Schema, CRS, determinism ---------------------------------------------------------------


def test_the_written_layer_matches_its_schema_in_4326(config, world):
    run_candidates(config.settings, world)
    candidates = read_layer("candidates", world, config.settings)
    assert list(candidates.columns) == [*CANDIDATES.column_names, "geometry"]
    assert candidates.crs.to_epsg() == 4326
    assert (candidates.geom_type == "Point").all()
    assert np.allclose(candidates["lat"], candidates.geometry.y)
    assert np.allclose(candidates["lon"], candidates.geometry.x)
    assert candidates["profile"].isna().all(), "profile stays unknown until it is decided"


def test_the_synthetic_world_gives_the_expected_candidates(result):
    candidates, stats = result
    by_host = candidates.set_index("host_osm_id", drop=False)
    assert sorted(candidates["host_osm_id"].dropna()) == [
        "node/10",
        "node/12",
        "node/15",
        "node/20",
        "way/16",
        "way/18",
    ]
    assert (candidates["host_type"] == "none").sum() == 2
    assert by_host.loc["node/10", "host_type"] == "fuel"
    assert stats["corridor_points"] == 6
    assert stats["final"] == len(candidates) == 8


def test_processing_twice_gives_byte_identical_outputs(config, world):
    run_candidates(config.settings, world)
    first = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (layer_path(world, "candidates"), metadata_path(world, "candidates"))
    }
    run_candidates(config.settings, world)
    second = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (layer_path(world, "candidates"), metadata_path(world, "candidates"))
    }
    assert first == second


def test_candidate_ids_are_stable_and_unique(result):
    candidates, _ = result
    assert candidates["candidate_id"].is_unique
    fuel = candidates.set_index("host_osm_id").loc["node/10", "candidate_id"]
    assert fuel == "cand-" + hashlib.sha256(b"node/10").hexdigest()[:12]
    assert list(candidates["candidate_id"]) == sorted(candidates["candidate_id"])


# --- Hosts and priority ---------------------------------------------------------------------


def test_host_priority_keeps_the_fuel_station_over_a_nearby_hotel(result):
    candidates, _ = result
    assert "node/11" not in set(candidates["host_osm_id"]), "hotel 115 m from a fuel station"
    fuel = candidates.set_index("host_osm_id").loc["node/10"]
    assert fuel["merged_count"] == 1


def test_a_poi_matching_two_host_types_takes_the_higher_priority(config):
    pois = gpd.GeoDataFrame(
        [
            {**row, "tourism": "hotel"} if row["feature_id"] == "node/10" else row
            for row in candidate_pois()
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    hosts = classify_hosts(pois, config.settings).set_index("host_osm_id")
    assert hosts.loc["node/10", "host_type"] == "fuel"


def test_restaurants_and_charging_stations_are_never_hosts(config):
    pois = gpd.GeoDataFrame(candidate_pois(), geometry="geometry", crs="EPSG:4326")
    hosts = classify_hosts(pois, config.settings)
    assert "node/13" not in set(hosts["host_osm_id"])  # restaurant
    assert "node/14" not in set(hosts["host_osm_id"])  # charging station


def test_area_hosts_are_represented_by_a_point_inside_the_area(config):
    pois = gpd.GeoDataFrame(candidate_pois(), geometry="geometry", crs="EPSG:4326")
    hosts = classify_hosts(pois, config.settings).set_index("host_osm_id")
    assert hosts.loc["way/16"].geometry.within(MALL_BOX)
    assert hosts.loc["way/18"].geometry.within(INDUSTRIAL_BOX)


def test_hosts_outside_rwanda_are_left_out(result):
    candidates, stats = result
    assert "node/19" not in set(candidates["host_osm_id"])
    assert stats["hosts_outside_rwanda"] == 1


# --- Corridor points and snapping -----------------------------------------------------------


def test_corridor_points_are_spaced_along_the_road(config):
    roads = gpd.GeoDataFrame(candidate_roads(), geometry="geometry", crs="EPSG:4326")
    country = gpd.GeoDataFrame(geometry=[shapely.box(29.95, -2.10, 30.55, -1.80)], crs="EPSG:4326")
    points = corridor_points(roads, country, config.settings)
    trunk = gpd.GeoSeries([TRUNK], crs="EPSG:4326").to_crs("EPSG:32735").iloc[0]
    along = sorted(trunk.project(point) for point in points)
    assert len(points) == 6
    assert along[0] == pytest.approx(5_000, abs=1)
    assert np.diff(along) == pytest.approx([10_000] * 5, abs=1)
    assert all(trunk.distance(point) < 1e-6 for point in points)


def test_residential_roads_and_tracks_get_no_corridor_points(config):
    roads = gpd.GeoDataFrame(candidate_roads()[1:], geometry="geometry", crs="EPSG:4326")
    country = gpd.GeoDataFrame(geometry=[shapely.box(29.95, -2.10, 30.55, -1.80)], crs="EPSG:4326")
    assert len(corridor_points(roads, country, config.settings)) == 0


def test_snapping_takes_the_nearest_host_within_the_radius():
    points = gpd.GeoSeries([shapely.Point(0, 0), shapely.Point(10_000, 0)], crs="EPSG:32735")
    hosts = gpd.GeoDataFrame(
        geometry=[shapely.Point(1_500, 0), shapely.Point(800, 0), shapely.Point(12_500, 0)],
        crs="EPSG:32735",
    )
    assert snap_to_hosts(points, hosts, 2_000).tolist() == [1, -1]


def test_snapping_breaks_ties_by_the_lower_host_index():
    points = gpd.GeoSeries([shapely.Point(0, 0)], crs="EPSG:32735")
    hosts = gpd.GeoDataFrame(
        geometry=[shapely.Point(0, 500), shapely.Point(500, 0)], crs="EPSG:32735"
    )
    assert snap_to_hosts(points, hosts, 2_000).tolist() == [0]


def test_a_snapped_corridor_point_becomes_its_host(result):
    candidates, stats = result
    assert stats["corridor_snapped"] == 2  # to node/20 and to the industrial area
    snapped_host = candidates.set_index("host_osm_id").loc["node/20"]
    assert snapped_host["origin"] == "host"
    assert snapped_host["merged_count"] == 1


# --- Deduplication ------------------------------------------------------------------------------


def _table(points, host_types, origins=None):
    return gpd.GeoDataFrame(
        {
            "candidate_id": [f"c{i}" for i in range(len(points))],
            "host_type": host_types,
            "origin": origins or ["host"] * len(points),
        },
        geometry=[shapely.Point(x, 0) for x in points],
        crs="EPSG:32735",
    )


PRIORITY = ("fuel", "mall", "supermarket", "logistics", "industrial", "hotel", "none")


def test_dedup_is_greedy_not_transitive():
    kept = deduplicate(_table([0, 250, 500], ["hotel"] * 3), 300, PRIORITY)
    assert kept["candidate_id"].tolist() == ["c0", "c2"]


def test_dedup_keeps_the_highest_priority_host_type():
    types = ["hotel", "industrial", "fuel", "none"]
    kept = deduplicate(_table([0, 100, 200, 290], types), 300, PRIORITY)
    assert kept["host_type"].tolist() == ["fuel"]
    assert kept["merged_count"].tolist() == [3]


def test_dedup_merges_exactly_at_the_radius_and_keeps_beyond_it():
    assert len(deduplicate(_table([0, 300], ["fuel", "fuel"]), 300, PRIORITY)) == 1
    assert len(deduplicate(_table([0, 300.5], ["fuel", "fuel"]), 300, PRIORITY)) == 2


# --- Filters ------------------------------------------------------------------------------------


def test_candidates_far_from_a_drivable_road_are_dropped(result):
    candidates, stats = result
    assert "node/17" not in set(candidates["host_osm_id"]), "near a track only"
    assert stats["dropped_far_from_road"] == 1
    assert (candidates["dist_road_m"] <= 500).all()
    assert "track" not in set(candidates["nearest_road_class"])


def test_candidates_in_water_are_dropped(result):
    candidates, stats = result
    assert stats["dropped_in_water"] == 1
    assert not candidates.intersects(WATER_BOX).any()


def test_candidates_in_protected_areas_including_national_parks_are_dropped(result):
    candidates, stats = result
    assert stats["dropped_in_protected_area"] == 1
    assert not candidates.intersects(PARK_BOX).any()


def test_candidates_near_existing_chargers_are_kept(result):
    candidates, _ = result
    hosts = set(candidates["host_osm_id"])
    assert "node/15" in hosts, "a fuel station tagged socket:* stays a candidate"
    assert "node/12" in hosts, "a supermarket 110 m from a charging station stays"


# --- Coordinates, districts and labels -----------------------------------------------------------


def test_no_candidate_is_placed_by_hand(result, config, world):
    """Every candidate sits on a host's point or on a trunk/primary road."""
    candidates, _ = result
    pois = gpd.GeoDataFrame(candidate_pois(), geometry="geometry", crs="EPSG:4326")
    hosts = classify_hosts(pois, config.settings).set_index("host_osm_id")
    trunk = gpd.GeoSeries([TRUNK], crs="EPSG:4326").to_crs("EPSG:32735").iloc[0]
    metric = _metric(candidates)
    for row, point in zip(candidates.itertuples(), metric.geometry, strict=True):
        if row.host_type == "none":
            assert trunk.distance(point) < 1e-3
        else:
            assert row.geometry.equals(hosts.loc[row.host_osm_id].geometry)


def test_districts_provinces_and_generic_labels(result):
    candidates, _ = result
    row = candidates.set_index("host_osm_id").loc["node/10"]
    assert row["district"] == "SYNTHETIC District 1"
    assert row["province_code"] == "RW-91"
    assert row["host_name"] == "Fuel station, SYNTHETIC District 1"
    industrial = candidates.set_index("host_osm_id").loc["way/18"]
    assert industrial["district_id"] == "SYN-D2"
    assert set(candidates["host_name"].str.split(",").str[0]) <= {
        "Fuel station",
        "Mall",
        "Supermarket",
        "Logistics site",
        "Industrial site",
        "Hotel",
        "Corridor point",
    }


# --- Count range and edge cases -------------------------------------------------------------------


def test_fewer_eligible_than_the_budget_stops_and_writes_nothing(settings_data, weights_data, dirs):
    real_budget = synthetic_config(settings_data, weights_data)  # budget 300
    write_candidate_world(dirs["processed"], real_budget.settings)
    stale = [layer_path(dirs["processed"], name) for name in ("candidates", "candidates_eligible")]
    for path in stale:
        path.write_bytes(b"SYNTHETIC stale output")
    with pytest.raises(CandidateBudgetError, match="8 eligible candidates, fewer than the budget"):
        run_candidates(real_budget.settings, dirs["processed"])
    assert not any(path.exists() for path in stale)
    assert not metadata_path(dirs["processed"], "candidates").exists()
    assert not metadata_path(dirs["processed"], "candidates_eligible").exists()


def test_no_trunk_roads_gives_host_candidates_only(config, dirs):
    write_candidate_world(dirs["processed"], config.settings, roads=candidate_roads()[1:])
    candidates, stats = _generate(config, dirs["processed"])
    assert stats["corridor_points"] == 0
    assert (candidates["origin"] == "host").all()


def test_no_hosts_gives_corridor_candidates_only(config, dirs):
    restaurant_only = [row for row in candidate_pois() if row["feature_id"] == "node/13"]
    write_candidate_world(dirs["processed"], config.settings, pois=restaurant_only)
    candidates, stats = _generate(config, dirs["processed"])
    assert stats["hosts_found"] == {}
    assert (candidates["host_type"] == "none").all()
    assert len(candidates) == 4  # six corridor points, minus water and park


def test_no_hosts_and_no_corridor_roads_is_a_budget_error(config, dirs):
    restaurant_only = [row for row in candidate_pois() if row["feature_id"] == "node/13"]
    write_candidate_world(
        dirs["processed"], config.settings, pois=restaurant_only, roads=candidate_roads()[1:]
    )
    with pytest.raises(CandidateBudgetError, match="0 eligible candidates"):
        run_candidates(config.settings, dirs["processed"])


def test_generation_uses_no_randomness(config, world):
    first, _ = _generate(config, world)
    second, _ = _generate(config, world)
    assert first.equals(second)


def test_metadata_records_the_input_fingerprints(config, world):
    run_candidates(config.settings, world)
    metadata = json.loads(metadata_path(world, "candidates").read_text(encoding="utf-8"))
    assert set(metadata["stats"]["generation"]["inputs"]) == {
        "admin_country",
        "admin_districts",
        "osm_pois",
        "osm_protected_areas",
        "osm_roads",
        "osm_water",
    }
    assert metadata["stats"]["generation"]["final"] == 8
