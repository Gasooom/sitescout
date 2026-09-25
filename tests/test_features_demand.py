"""Demand features: population within 1, 5 and 10 km, and the border diagnostic (SYNTHETIC)."""

import numpy as np
import pyproj
import pytest
import shapely

from sitescout.crs import to_metric
from sitescout.features.demand import outside_share, population_within
from synthetic import ARCSEC, population_tif
from synthetic_features import FEATURE_POPULATION, at, feature_population

RADII = (1000, 5000, 10000)
TO_METRIC = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)


def _metric(config, *points):
    import geopandas as gpd

    return to_metric(gpd.GeoSeries(list(points), crs="EPSG:4326"), config.settings.crs)


def _pixel_centres(west, north, shape, size=3 * ARCSEC):
    """Every pixel centre of a raster, in metres (EPSG:32735), computed independently."""
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    lon = west + (cols + 0.5) * size
    lat = north - (rows + 0.5) * size
    return TO_METRIC.transform(lon, lat)


def test_hand_calculated_sums(config, temp_dir):
    # 10 people at 0.5 km, 20 at 3 km, 40 at 7 km and 80 at 20 km from the point.
    raster = feature_population(temp_dir / "pop.tif")
    result = population_within(_metric(config, at(0, 0)), raster, RADII, config.settings)
    assert FEATURE_POPULATION[(500, 0)] == 10
    assert result[1000][0] == 10.0
    assert result[5000][0] == 30.0
    assert result[10000][0] == 70.0


def test_nodata_contributes_zero(config, temp_dir):
    values = np.zeros((20, 20), dtype=np.float32)
    values[10, 10] = -99999.0  # nodata next to the point
    values[10, 11] = 7.0
    raster = population_tif(temp_dir / "pop.tif", values=values, west=30.0, north=-1.9)
    point = shapely.Point(30.0 + 10.5 * 3 * ARCSEC, -1.9 - 10.5 * 3 * ARCSEC)
    result = population_within(_metric(config, point), raster, (1000,), config.settings)
    assert result[1000][0] == 7.0


def test_a_pixel_centre_exactly_at_the_radius_is_included(config, temp_dir):
    values = np.zeros((40, 40), dtype=np.float32)
    values[5, 30] = 3.0
    raster = population_tif(temp_dir / "pop.tif", values=values, west=30.0, north=-1.9)
    point = shapely.Point(30.0 + 5.5 * 3 * ARCSEC, -1.9 - 5.5 * 3 * ARCSEC)  # pixel (5, 5)
    xs, ys = _pixel_centres(30.0, -1.9, values.shape)
    px, py = TO_METRIC.transform(point.x, point.y)
    exact = float(np.hypot(xs[5, 30] - px, ys[5, 30] - py))
    series = _metric(config, point)
    assert population_within(series, raster, (exact,), config.settings)[exact][0] == 3.0
    below = exact - 0.001
    assert population_within(series, raster, (below,), config.settings)[below][0] == 0.0


def test_circles_crossing_the_raster_edge_count_only_pixels_in_the_raster(config, temp_dir):
    values = np.ones((30, 30), dtype=np.float32)
    raster = population_tif(temp_dir / "pop.tif", values=values, west=30.0, north=-1.9)
    point = shapely.Point(30.0 + 2 * 3 * ARCSEC, -1.9 - 15 * 3 * ARCSEC)  # near the west edge
    xs, ys = _pixel_centres(30.0, -1.9, values.shape)
    px, py = TO_METRIC.transform(point.x, point.y)
    expected = float((np.hypot(xs - px, ys - py) <= 1000).sum())
    result = population_within(_metric(config, point), raster, (1000,), config.settings)
    assert result[1000][0] == expected
    assert expected < np.pi * 1000**2 / (92.5**2)  # part of the circle is off the raster
    far = shapely.Point(29.5, -1.9)
    assert population_within(_metric(config, far), raster, (1000,), config.settings)[1000][0] == 0


def test_the_circle_is_measured_in_metres(config, temp_dir):
    # A 3 arc-second pixel is 92.7 m east-west and 92.2 m north-south here, so a circle in
    # degrees would take different edge pixels. The count must equal the metric count.
    values = np.ones((140, 140), dtype=np.float32)
    raster = population_tif(temp_dir / "pop.tif", values=values, west=30.0, north=-1.9)
    point = shapely.Point(30.0 + 70 * 3 * ARCSEC, -1.9 - 70 * 3 * ARCSEC)
    xs, ys = _pixel_centres(30.0, -1.9, values.shape)
    px, py = TO_METRIC.transform(point.x, point.y)
    distance = np.hypot(xs - px, ys - py)
    result = population_within(_metric(config, point), raster, (1000, 5000), config.settings)
    assert result[1000][0] == float((distance <= 1000).sum()) == 372
    assert result[5000][0] == float((distance <= 5000).sum()) == 9184
    # The test can tell: a circle in degrees (111,320 m per degree on both axes) takes
    # different edge pixels (368 and 9,136).
    rows, cols = np.mgrid[0:140, 0:140]
    in_degrees = np.hypot(cols + 0.5 - 70, rows + 0.5 - 70) * 3 * ARCSEC * 111_320
    assert float((in_degrees <= 1000).sum()) != result[1000][0]
    assert float((in_degrees <= 5000).sum()) != result[5000][0]


def test_zero_population_is_zero_not_missing(config, temp_dir):
    raster = feature_population(temp_dir / "pop.tif", cells={})
    result = population_within(_metric(config, at(0, 0)), raster, RADII, config.settings)
    for radius in RADII:
        assert result[radius][0] == 0.0
        assert not np.isnan(result[radius][0])


def test_larger_radii_never_hold_fewer_people(config, temp_dir):
    rng = np.random.default_rng(0)
    values = rng.gamma(0.3, 5.0, size=(200, 200)).astype(np.float32)
    values[rng.random((200, 200)) < 0.3] = -99999.0
    raster = population_tif(temp_dir / "pop.tif", values=values, west=30.0, north=-1.9)
    points = [
        shapely.Point(30.0 + x * 3 * ARCSEC, -1.9 - y * 3 * ARCSEC)
        for x, y in [(5, 5), (100, 100), (150, 20), (199, 199), (60, 180)]
    ]
    result = population_within(_metric(config, *points), raster, RADII, config.settings)
    assert (result[1000] <= result[5000]).all()
    assert (result[5000] <= result[10000]).all()
    assert (result[1000] >= 0).all()


def test_the_raster_must_be_in_the_storage_crs(config, temp_dir):
    from sitescout.crs import CrsError

    raster = population_tif(temp_dir / "pop.tif", crs="EPSG:32735", west=30.0, north=-1.9)
    with pytest.raises(CrsError, match="Population raster"):
        population_within(_metric(config, at(0, 0)), raster, (1000,), config.settings)


# --- The border diagnostic -------------------------------------------------------------------


def test_outside_share(config):
    country = shapely.box(-50_000, -50_000, 0, 50_000)  # metric; the border is x = 0
    points = _metric(config, at(0, 0))  # only for its CRS
    import geopandas as gpd

    metric = gpd.GeoSeries(
        [shapely.Point(-30_000, 0), shapely.Point(0, 0), shapely.Point(30_000, 0)],
        crs=points.crs,
    )
    shares = outside_share(metric, country, 10_000)
    assert shares[0] == 0.0
    assert shares[1] == pytest.approx(0.5, abs=1e-9)
    assert shares[2] == 1.0
