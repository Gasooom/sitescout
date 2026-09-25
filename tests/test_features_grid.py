"""Grid-evidence features from mapped OSM power features (SYNTHETIC; D-033).

The SYNTHETIC world: a substation area around candidate C, a power line 4 km north of A and
B that crosses both districts, towers and poles, and a node tagged power=150kWh at A (a
charger capacity tag, not a power type). Both districts have an area_km2 of 1.
"""

import pytest

from sitescout.features.grid import GridError
from synthetic_features import at, box_at, build, feature_config, feature_power, line_at, power


@pytest.fixture
def fconfig(settings_data, weights_data):
    return feature_config(settings_data, weights_data)


def test_a_candidate_inside_a_substation_area_is_at_zero(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    assert frame.loc["cand-c", "dist_substation_m"] == 0.0
    # A is 11.9 km from the substation's northern edge (the box is 100 m around C).
    assert frame.loc["cand-a", "dist_substation_m"] == pytest.approx(11900, abs=0.5)


def test_line_distance(fconfig, dirs):
    frame = build(dirs["processed"], fconfig)[0]
    assert frame.loc["cand-a", "dist_line_m"] == pytest.approx(4000, abs=0.5)
    assert frame.loc["cand-b", "dist_line_m"] == pytest.approx(4000, abs=0.5)
    assert frame.loc["cand-c", "dist_line_m"] == pytest.approx(16000, abs=0.5)


@pytest.mark.parametrize("value", ["minor_line", "cable"])
def test_minor_lines_and_cables_are_lines(fconfig, dirs, value):
    rows = [
        *feature_power(),
        power("way/70", value, line_at([(-1000, -300), (1000, -300)], step=100)),
    ]
    frame = build(dirs["processed"], fconfig, power_rows=rows)[0]
    assert frame.loc["cand-a", "dist_line_m"] == pytest.approx(300, abs=0.5)


def test_only_the_approved_power_values_count(fconfig, dirs):
    # "11 kWh" and "substation_planned" are not approved values: never counted, and not a
    # substation. A node tagged power=line has an approved value and is counted.
    rows = [
        *feature_power(),
        power("node/71", "11 kWh", at(10, 0)),
        power("node/72", "substation_planned", at(20, 0)),
        power("node/73", "line", at(30, 0)),  # a line value on a node still counts
    ]
    frame, stats = build(dirs["processed"], fconfig, power_rows=rows)
    table = stats["grid_completeness"]["districts"]
    assert table["SYN-D1"]["power_features"] == 3 + 1  # node/73 only
    assert frame.loc["cand-a", "dist_substation_m"] == pytest.approx(11900, abs=0.5)


def test_charger_capacity_tags_are_ignored(fconfig, dirs):
    # node/53 is power=150kWh at A: it is neither a substation, a line nor counted.
    frame, stats = build(dirs["processed"], fconfig)
    assert frame.loc["cand-a", "dist_substation_m"] > 0
    assert stats["grid_completeness"]["districts"]["SYN-D1"]["power_features"] == 3


def test_completeness_by_hand(fconfig, dirs):
    # SYN-D1: substation way/50, line way/51, tower node/54 = 3 per km².
    # SYN-D2: line way/51, tower node/55, poles node/56 and node/57 = 4 per km².
    # Two districts: the median is the mean of the middle two, (3 + 4) / 2 = 3.5.
    frame, stats = build(dirs["processed"], fconfig)
    summary = stats["grid_completeness"]
    assert summary["national_median_per_km2"] == 3.5
    assert summary["districts"]["SYN-D1"]["ratio"] == pytest.approx(3 / 3.5, abs=1e-6)
    assert summary["districts"]["SYN-D2"]["ratio"] == pytest.approx(4 / 3.5, abs=1e-6)
    assert frame.loc["cand-a", "grid_completeness_ratio"] == pytest.approx(3 / 3.5)
    assert frame.loc["cand-c", "grid_completeness_ratio"] == pytest.approx(3 / 3.5)
    assert frame.loc["cand-b", "grid_completeness_ratio"] == pytest.approx(4 / 3.5)


def test_a_line_crossing_two_districts_counts_in_each(fconfig, dirs):
    only_line = [power("way/51", "line", line_at([(-5000, 4000), (60000, 4000)]))]
    stats = build(dirs["processed"], fconfig, power_rows=only_line)[1]
    districts = stats["grid_completeness"]["districts"]
    assert districts["SYN-D1"]["power_features"] == 1
    assert districts["SYN-D2"]["power_features"] == 1


def test_a_district_without_features_gets_zero(fconfig, dirs):
    # Features only in SYN-D2: SYN-D1 has none. The median of (0, 2) is 1.
    rows = [power("node/80", "tower", at(40000, 0)), power("node/81", "pole", at(41000, 0))]
    frame, stats = build(dirs["processed"], fconfig, power_rows=rows)
    assert frame.loc["cand-a", "grid_completeness_ratio"] == 0.0
    assert frame.loc["cand-b", "grid_completeness_ratio"] == pytest.approx(2.0)
    assert frame["dist_substation_m"].isna().all()
    assert frame["dist_line_m"].isna().all()


def test_a_zero_national_median_stops(fconfig, dirs):
    only_ignored = [power("node/53", "150kWh", at(0, 0))]
    with pytest.raises(GridError, match="national median"):
        build(dirs["processed"], fconfig, power_rows=only_ignored)


def test_no_substations_gives_a_null_distance(fconfig, dirs):
    rows = [row for row in feature_power() if row["power"] != "substation"]
    frame = build(dirs["processed"], fconfig, power_rows=rows)[0]
    assert frame["dist_substation_m"].isna().all()
    assert frame["dist_line_m"].notna().all()


def test_a_substation_area_counts_by_its_edge(fconfig, dirs):
    rows = [*feature_power(), power("way/74", "substation", box_at(0, 1000, 100))]
    frame = build(dirs["processed"], fconfig, power_rows=rows)[0]
    assert frame.loc["cand-a", "dist_substation_m"] == pytest.approx(900, abs=0.5)
