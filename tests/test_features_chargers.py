"""Charging-gap features and the existing-charger set (SYNTHETIC; D-034).

Production chargers in the SYNTHETIC world: node/13, node/40 and node/41 (200, 220 and
260 m east of candidate A, one site after merging), the area charger way/42 20 km east, the
socket-tagged fuel station node/17 2 km west, and two chargers that are not public
(node/43 private, 1 km north of C; node/44 access=no).
"""

import geopandas as gpd
import numpy as np
import pytest
import shapely

from sitescout.config import PendingParameterError
from sitescout.crs import to_metric
from sitescout.features.chargers import _merge
from sitescout.features.nearest import count_within, nearest_distance
from synthetic import _poi
from synthetic_features import (
    at,
    box_at,
    build,
    charger,
    feature_chargers,
    feature_config,
    feature_pois,
)


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


def test_distance_and_counts(fconfig, dirs):
    frame, stats = build(dirs["processed"], fconfig)
    a = frame.loc["cand-a"]
    assert a["dist_charger_m"] == pytest.approx(200, abs=1e-6)  # the merged site's node/13
    # Within 10 km: the merged site and the fuel station; 25 km adds the area charger.
    assert (a["chargers_10km"], a["chargers_25km"]) == (2, 3)
    assert stats["chargers"]["sites"] == 3


def test_an_area_charger_is_measured_to_a_point_on_its_surface(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    area = gpd.GeoSeries([box_at(20000, 0, 50)], crs="EPSG:4326")
    surface = to_metric(area, fconfig.settings.crs).representative_point().iloc[0]
    b = to_metric(gpd.GeoSeries([at(33000, 0)], crs="EPSG:4326"), fconfig.settings.crs).iloc[0]
    assert frame.loc["cand-b", "dist_charger_m"] == pytest.approx(b.distance(surface), abs=1e-6)
    assert frame.loc["cand-b", "chargers_25km"] == 1


def test_no_chargers_gives_null_distance_and_zero_counts(fconfig, dirs):
    no_socket_fuel = [row for row in feature_pois() if row["feature_id"] != "node/17"]
    frame, stats = build(dirs["processed"], fconfig, chargers=[], pois=no_socket_fuel)
    assert frame["dist_charger_m"].isna().all()
    assert (frame["chargers_10km"] == 0).all() and (frame["chargers_25km"] == 0).all()
    assert stats["chargers"]["sites"] == 0


def test_private_and_no_access_chargers_are_not_existing_chargers(fconfig, dirs):
    frame, stats = build(dirs["processed"], fconfig)
    # node/43 (private) is 1 km from C; C's nearest counted site is node/13, 12,001.7 m away.
    assert frame.loc["cand-c", "dist_charger_m"] == pytest.approx(np.hypot(200, 12000), abs=1e-3)
    assert stats["chargers"]["sources"]["osm_charging_stations"]["excluded_access"] == 2
    public = [
        charger(row["feature_id"], at(0, -11000)) if row["feature_id"] == "node/43" else row
        for row in feature_chargers()
    ]
    frame = build(dirs["processed"], fconfig, chargers=public)[0]
    assert frame.loc["cand-c", "dist_charger_m"] == pytest.approx(1000, abs=1e-6)


def test_records_within_50_m_merge_into_one_site(fconfig, dirs):
    stats = build(dirs["processed"], fconfig)[1]["chargers"]
    # node/41 is 60 m from node/13 but 40 m from node/40: merged through the chain.
    assert stats["merged_groups"] == [["node/13", "node/40", "node/41"]]
    assert stats["merge_radius_m"] == 50
    assert stats["records"] == 5 and stats["sites"] == 3


def test_the_merge_radius_is_inclusive(fconfig):
    crs = fconfig.settings.crs.metric
    exactly = gpd.GeoSeries([shapely.Point(0, 0), shapely.Point(50, 0)], crs=crs)
    beyond = gpd.GeoSeries([shapely.Point(0, 0), shapely.Point(50.01, 0)], crs=crs)
    assert _merge(exactly, 50) == [{0, 1}]
    assert sorted(map(sorted, _merge(beyond, 50))) == [[0], [1]]


def test_socket_tagged_fuel_stations_are_chargers_in_production(
    fconfig, dirs, settings_data, weights_data
):
    frame, stats = build(dirs["processed"], fconfig)
    assert stats["chargers"]["sources"]["osm_fuel_with_sockets"] == {"records": 1, "counted": True}
    off = feature_config(
        settings_data,
        weights_data,
        **{"settings.features.chargers.fuel_sockets_count_as_chargers": False},
    )
    frame_off = build(dirs["processed"], off)[0]
    assert frame.loc["cand-a", "chargers_10km"] - frame_off.loc["cand-a", "chargers_10km"] == 1


def test_a_missing_csv_leaves_osm_as_the_only_source(fconfig, dirs):
    stats = build(dirs["processed"], fconfig)[1]["chargers"]
    manual = stats["sources"]["chargers_manual"]
    assert manual["records"] == 0 and manual["status"].startswith("missing")


def test_csv_chargers_join_and_merge_with_osm(fconfig, dirs):
    # One CSV row 10 m from node/13 (the same site) and one new site 8 km west.
    frame, stats = build(dirs["processed"], fconfig, manual=[at(210, 5), at(-8000, 0)])
    assert stats["chargers"]["sources"]["chargers_manual"] == {"records": 2, "status": "ok"}
    assert stats["chargers"]["sites"] == 4
    assert ["node/13", "node/40", "node/41", "csv-000000000000"] in stats["chargers"][
        "merged_groups"
    ]
    assert frame.loc["cand-a", "chargers_10km"] == 3


def test_a_pending_match_radius_stops_production(settings_data, weights_data, dirs):
    settings_data["sources"]["charger_match_radius_m"] = {
        "pending": "SPEC §2 gives no radius (SYNTHETIC test). Decide in M3."
    }
    config = feature_config(settings_data, weights_data)
    with pytest.raises(PendingParameterError, match="charger_match_radius_m is pending"):
        build(dirs["processed"], config)


def test_equally_distant_chargers(fconfig):
    crs = fconfig.settings.crs.metric
    target = gpd.GeoSeries([shapely.Point(0, 0)], crs=crs)
    tied = gpd.GeoSeries([shapely.Point(300, 0), shapely.Point(0, -300)], crs=crs)
    assert nearest_distance(target, tied).tolist() == [300.0]
    assert count_within(target, tied, 300).tolist() == [2]


def test_a_charging_station_poi_is_not_a_charger_by_itself(fconfig, dirs):
    # Chargers come from osm_charging_stations; a charging_station POI adds nothing.
    extra = _poi("node/95", at(50, 50), amenity="charging_station")
    frame = build(dirs["processed"], fconfig, pois=[*feature_pois(), extra])[0]
    assert frame.loc["cand-a", "dist_charger_m"] == pytest.approx(200, abs=1e-6)
