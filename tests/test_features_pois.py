"""Host / commercial features: POI counts within 1 and 3 km (SYNTHETIC; D-033).

Around candidate A (tests/synthetic_features.py): its own fuel host at 0 m, a restaurant at
999 m, a cafe-and-bakery at 500 m, a charging station at 200 m, industrial land use at
300 m, a socket-tagged fuel station at 2 km, a hotel area about 2 km north and a
supermarket (another host) at 1,001 m.
"""

import geopandas as gpd
import numpy as np
import pytest
import shapely

from sitescout.features.nearest import count_within, metric_points
from synthetic import _poi
from synthetic_features import at, box_at, build, feature_config, feature_pois


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


@pytest.fixture
def production(fconfig, dirs):
    return build(dirs["processed"], fconfig)[0]


def test_radii_are_inclusive(fconfig):
    crs = fconfig.settings.crs.metric
    targets = gpd.GeoSeries([shapely.Point(0, 0)], crs=crs)
    sources = gpd.GeoSeries(
        [shapely.Point(1000, 0), shapely.Point(0, -1000), shapely.Point(1000.001, 0)], crs=crs
    )
    assert count_within(targets, sources, 1000).tolist() == [2]
    assert count_within(targets, sources, 3000).tolist() == [3]


def test_counts_at_1_and_3_km(production):
    a = production.loc["cand-a"]
    # 1 km: restaurant (999 m) and cafe-bakery (500 m). 3 km adds the supermarket (1,001 m),
    # the socket-tagged fuel station (2 km) and the hotel area (about 2 km).
    assert (a["poi_1km"], a["poi_3km"]) == (2, 5)
    assert (a["poi_amenity_1km"], a["poi_amenity_3km"]) == (2, 3)
    assert (a["poi_shop_1km"], a["poi_shop_3km"]) == (1, 2)
    assert (a["poi_tourism_1km"], a["poi_tourism_3km"]) == (0, 1)
    assert (a["poi_office_3km"], a["poi_industrial_3km"]) == (0, 0)


def test_an_area_poi_counts_by_a_point_on_its_surface(fconfig, production):
    hotel = gpd.GeoDataFrame(
        [_poi("way/15", box_at(0, 2000, 100), tourism="hotel")], crs="EPSG:4326"
    )
    point = metric_points(hotel, fconfig.settings).iloc[0]
    from sitescout.crs import to_metric

    assert to_metric(hotel.geometry, fconfig.settings.crs).iloc[0].contains(point)
    # The box's edge is 1.9 km away, its surface point about 2 km: in 3 km, not in 1 km.
    assert production.loc["cand-a", "poi_tourism_1km"] == 0
    assert production.loc["cand-a", "poi_tourism_3km"] == 1


def test_the_candidates_own_host_is_not_counted(production):
    # A's fuel host sits at 0 m; C's hotel host sits at C. Neither counts for its candidate.
    assert production.loc["cand-a", "poi_amenity_1km"] == 2  # restaurant and cafe only
    assert production.loc["cand-c", ["poi_1km", "poi_3km"]].tolist() == [0, 0]


def test_other_hosts_nearby_are_counted(fconfig, dirs):
    # The supermarket at 1,001 m is a host of no candidate here; it is still a POI.
    without = [row for row in feature_pois() if row["feature_id"] != "node/12"]
    fewer = build(dirs["processed"], fconfig, pois=without)[0]
    full = build(dirs["processed"], fconfig)[0]
    assert full.loc["cand-a", "poi_shop_3km"] - fewer.loc["cand-a", "poi_shop_3km"] == 1


def test_a_poi_of_two_types_counts_once_in_the_total(production):
    a = production.loc["cand-a"]
    # The cafe-and-bakery (amenity and shop) at 500 m, and the restaurant.
    assert a["poi_1km"] == 2
    assert a["poi_amenity_1km"] + a["poi_shop_1km"] == 3


def test_charging_stations_and_other_keys_are_never_pois(fconfig, dirs):
    extra = [
        _poi("node/90", at(100, 0), amenity="charging_station"),
        _poi("node/91", at(100, 100), amenity="charging_station", shop="yes"),
        _poi("node/92", at(150, 0), landuse="commercial"),
        _poi("node/93", at(150, 50), building="retail"),
    ]
    for mode in ("production", "backtest"):
        more = build(dirs["processed"], fconfig, mode, pois=[*feature_pois(), *extra])[0]
        full = build(dirs["processed"], fconfig, mode)[0]
        assert more.loc["cand-a", "poi_1km"] == full.loc["cand-a", "poi_1km"] == 2


def test_zero_pois(fconfig, dirs):
    only_charger = [_poi("node/13", at(200, 0), amenity="charging_station")]
    frame, stats = build(dirs["processed"], fconfig, pois=only_charger)
    columns = [name for name in frame.columns if name.startswith("poi_")]
    assert len(columns) == 12
    assert (frame[columns].to_numpy() == 0).all()
    assert frame[columns].dtypes.map(lambda d: d == np.int64).all()
    assert stats["pois"]["pois_counted"] == 0
