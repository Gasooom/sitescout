"""Backtest leakage: existing chargers never reach a backtest feature (SPEC §5; SYNTHETIC).

Backtest mode must give exactly the features of a world in which no charger was ever
mapped: adding, removing or moving any charger, charging-station POI or socket-tagged fuel
station must leave every backtest value unchanged. Production mode must see them.
"""

import pandas as pd
import pytest

from synthetic import _poi
from synthetic_features import (
    at,
    build,
    charger,
    feature_chargers,
    feature_config,
    feature_pois,
    feature_power,
    power,
)

WITHOUT_CHARGERS = {
    "chargers": [],
    # The charging station and the socket-tagged fuel station are charger objects.
    "pois": [row for row in feature_pois() if row["feature_id"] not in {"node/13", "node/17"}],
}


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


def _table(frame):
    return pd.DataFrame(frame.drop(columns="geometry"))


def _backtest(dirs, config, **world):
    return _table(build(dirs["processed"], config, "backtest", **world)[0])


def test_backtest_features_equal_a_world_without_chargers(fconfig, dirs):
    with_chargers = _backtest(dirs, fconfig)
    never_mapped = _backtest(dirs, fconfig, **WITHOUT_CHARGERS)
    pd.testing.assert_frame_equal(with_chargers, never_mapped)


@pytest.mark.parametrize(
    "world",
    [
        {"chargers": []},
        {"chargers": [charger("node/13", at(-4000, 900)), *feature_chargers()[1:]]},
        {"chargers": [*feature_chargers(), charger("node/45", at(0, 0))]},
        {"manual": [at(100, 100), at(-6000, 0)]},
    ],
    ids=["removed", "moved", "added", "csv-added"],
)
def test_changing_chargers_does_not_change_backtest_features(fconfig, dirs, world):
    pd.testing.assert_frame_equal(_backtest(dirs, fconfig), _backtest(dirs, fconfig, **world))


def test_charging_station_pois_do_not_change_backtest_poi_counts(fconfig, dirs):
    extra = [
        _poi("node/96", at(100, 0), amenity="charging_station"),
        _poi("node/97", at(0, 2500), amenity="charging_station", socket__type2="1"),
    ]
    pd.testing.assert_frame_equal(
        _backtest(dirs, fconfig), _backtest(dirs, fconfig, pois=[*feature_pois(), *extra])
    )


def test_socket_tagged_fuel_stations_do_not_reach_backtest_features(fconfig, dirs):
    # A fuel station with socket:* tags near A: in backtest mode, the same as no station.
    socket_fuel = _poi("node/98", at(400, 400), amenity="fuel", socket__type2="2")
    with_it = _backtest(dirs, fconfig, pois=[*feature_pois(), socket_fuel])
    pd.testing.assert_frame_equal(with_it, _backtest(dirs, fconfig))
    # Without sockets it is an ordinary fuel POI and is counted.
    plain_fuel = _poi("node/98", at(400, 400), amenity="fuel")
    plain = _backtest(dirs, fconfig, pois=[*feature_pois(), plain_fuel])
    assert plain.loc["cand-a", "poi_1km"] == with_it.loc["cand-a", "poi_1km"] + 1


def test_a_charger_object_tagged_as_power_does_not_reach_backtest_grid_evidence(fconfig, dirs):
    # node/13 is a charging station; here it is also mapped as power=transformer.
    tagged = [*feature_power(), power("node/13", "transformer", at(200, 0))]
    backtest = _backtest(dirs, fconfig, power_rows=tagged)
    pd.testing.assert_frame_equal(backtest, _backtest(dirs, fconfig))
    production = build(dirs["processed"], fconfig, power_rows=tagged)[1]
    assert production["grid_completeness"]["districts"]["SYN-D1"]["power_features"] == 4


def test_backtest_charging_gap_is_empty(fconfig, dirs):
    frame = _backtest(dirs, fconfig)
    assert frame["dist_charger_m"].isna().all()
    assert (frame["chargers_10km"] == 0).all()
    assert (frame["chargers_25km"] == 0).all()


def test_production_features_do_see_charger_changes(fconfig, dirs):
    before = build(dirs["processed"], fconfig)[0]
    moved = [charger("node/13", at(-4000, 900)), *feature_chargers()[1:]]
    after = build(dirs["processed"], fconfig, chargers=moved)[0]
    # node/40 (220 m) now leads the site that node/13 has left.
    assert before.loc["cand-a", "dist_charger_m"] == pytest.approx(200, abs=1e-6)
    assert after.loc["cand-a", "dist_charger_m"] == pytest.approx(220, abs=1e-6)
    removed = build(dirs["processed"], fconfig, **WITHOUT_CHARGERS)[0]
    assert removed["dist_charger_m"].isna().all()
