"""CRS helpers: store in EPSG:4326, measure in EPSG:32735, never measure in degrees."""

import geopandas as gpd
import pyproj
import pytest
import shapely

from sitescout.config import build_config, load_config
from sitescout.crs import (
    CrsError,
    area_km2,
    check_crs,
    check_metric_crs,
    distance_m,
    length_km,
    parse_crs,
    same_crs,
    to_metric,
    to_storage,
)

CRS = load_config().settings.crs
GEOD = pyproj.Geod(ellps="WGS84")


def _points(*coords: tuple[float, float], crs: str = "EPSG:4326") -> gpd.GeoSeries:
    return gpd.GeoSeries([shapely.Point(xy) for xy in coords], crs=crs)


def test_configured_crs_are_storage_4326_and_metric_32735():
    assert CRS.storage == "EPSG:4326"
    assert CRS.metric == "EPSG:32735"
    assert parse_crs(CRS.storage).is_geographic
    assert check_metric_crs(CRS.metric).is_projected


def test_to_storage_reprojects_back_to_4326():
    original = _points((30.06, -1.95))
    projected = original.to_crs("EPSG:32735")
    restored = to_storage(projected, CRS)
    assert restored.crs.to_epsg() == 4326
    assert restored.iloc[0].distance(original.iloc[0]) < 1e-9


def test_geojson_crs84_counts_as_storage_crs():
    assert same_crs("OGC:CRS84", "EPSG:4326")
    assert to_storage(_points((30.0, -1.9), crs="OGC:CRS84"), CRS).crs.to_epsg() == 4326


def test_distance_is_computed_in_metres_not_degrees():
    # 0.01 degrees of longitude apart, in Kigali: about 1.1 km on the ground.
    a, b = _points((30.06, -1.95)), shapely.Point(30.07, -1.95)
    metres = distance_m(a, b, CRS)[0]
    _, _, geodesic = GEOD.inv(30.06, -1.95, 30.07, -1.95)
    degrees = a.iloc[0].distance(b)
    assert degrees == pytest.approx(0.01)
    assert metres > 1000, "a result near 0.01 would mean the distance was taken in degrees"
    assert metres == pytest.approx(geodesic, rel=3e-3)


def test_distance_between_aligned_series():
    a = _points((30.0, -1.9), (30.5, -2.5))
    b = _points((30.0, -1.91), (30.5, -2.5))
    result = distance_m(a, b, CRS)
    assert result[0] == pytest.approx(GEOD.inv(30.0, -1.9, 30.0, -1.91)[2], rel=3e-3)
    assert result[1] == 0


def test_utm35s_distortion_across_rwanda_is_below_a_quarter_percent():
    """Rwanda spans 28.9-30.9 E; EPSG:32735's central meridian is 27 E (zone 35).

    The scale error grows east of the zone; at Rwanda's eastern edge it is about 0.19%,
    i.e. about 19 m per 10 km. Documented in docs/decisions.md (D-018).
    """
    for lon in (28.9, 29.9, 30.9):
        a = _points((lon, -2.0))
        b = shapely.Point(lon + 0.09, -2.0)
        utm = distance_m(a, b, CRS)[0]
        geodesic = GEOD.inv(lon, -2.0, lon + 0.09, -2.0)[2]
        assert abs(utm / geodesic - 1) < 0.0025, lon


def test_area_and_length_are_metric():
    square = gpd.GeoSeries([shapely.box(30.0, -2.0, 30.1, -1.9)], crs="EPSG:4326")
    assert area_km2(square, CRS)[0] == pytest.approx(123.4, rel=0.01)
    line = gpd.GeoSeries([shapely.LineString([(30.0, -1.9), (30.0, -2.0)])], crs="EPSG:4326")
    assert length_km(line, CRS)[0] == pytest.approx(
        GEOD.inv(30, -1.9, 30, -2.0)[2] / 1000, rel=3e-3
    )


def test_metric_helpers_refuse_data_outside_the_storage_crs():
    web_mercator = _points((30.0, -1.9)).to_crs("EPSG:3857")
    with pytest.raises(CrsError, match="expected EPSG:4326"):
        to_metric(web_mercator, CRS)


def test_metric_helpers_refuse_data_without_a_crs():
    with pytest.raises(CrsError, match="no CRS"):
        to_metric(gpd.GeoSeries([shapely.Point(30.0, -1.9)]), CRS)
    with pytest.raises(CrsError, match="no CRS"):
        to_storage(gpd.GeoSeries([shapely.Point(30.0, -1.9)]), CRS)


@pytest.mark.parametrize(
    ("crs", "message"),
    [("EPSG:4326", "not projected"), ("EPSG:2263", "not metres")],
    ids=["degrees", "us-feet"],
)
def test_a_metric_crs_must_be_projected_in_metres(crs, message):
    with pytest.raises(CrsError, match=message):
        check_metric_crs(crs)


def test_distance_stops_if_config_sets_a_geographic_metric_crs(settings_data, weights_data):
    config = build_config(
        settings_data, weights_data, overrides={"settings.crs.metric": "EPSG:4326"}
    )
    with pytest.raises(CrsError, match="not projected"):
        distance_m(_points((30.0, -1.9)), shapely.Point(30.1, -1.9), config.settings.crs)


@pytest.mark.parametrize("value", [None, "EPSG:999999", "not a crs"])
def test_invalid_crs_values_raise(value):
    with pytest.raises(CrsError):
        parse_crs(value)


def test_check_crs_names_both_crs_in_the_error():
    with pytest.raises(CrsError, match=r"EPSG:32735.*expected EPSG:4326"):
        check_crs("EPSG:32735", "EPSG:4326", "Layer 'x'")
    with pytest.raises(CrsError, match="has no CRS"):
        check_crs(None, "EPSG:4326", "Layer 'x'")
