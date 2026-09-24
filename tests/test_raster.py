"""The WorldPop raster: CRS, resolution, dimensions, nodata, and bad sources (SYNTHETIC)."""

import json

import numpy as np
import pytest
import rasterio

from sitescout.ingest import DataValidationError
from sitescout.ingest.raster import (
    POPULATION_LAYER,
    inspect_population_raster,
    population_path,
    process_population,
    read_population,
)
from synthetic import install_raw, population_tif


def test_a_valid_raster_is_described_exactly(config, dirs):
    info = inspect_population_raster(population_tif(dirs["work"] / "p.tif"), config.settings)
    assert info.crs == "EPSG:4326"
    assert (info.width, info.height) == (20, 10)
    assert info.pixel_size_arcsec == (3.0, 3.0)
    assert info.nodata == -99999.0
    assert info.valid_pixels == 100
    assert info.nodata_pixels == 100
    assert info.zero_pixels == 1
    assert info.population_sum == pytest.approx(sum(range(100)) / 10)


def test_processing_keeps_the_raster_byte_for_byte(config, dirs):
    source = population_tif(dirs["work"] / "p.tif")
    install_raw(config, dirs["raw"], "worldpop", source.read_bytes())
    result = process_population(config.settings, dirs["raw"], dirs["processed"])
    output = population_path(dirs["processed"])
    assert output.read_bytes() == source.read_bytes()
    with rasterio.open(output) as dataset:
        assert dataset.crs.to_epsg() == 4326
        assert dataset.nodata == -99999.0
        assert dataset.res == pytest.approx((3 / 3600, 3 / 3600))
    metadata = json.loads((dirs["processed"] / f"{POPULATION_LAYER}.meta.json").read_text())
    assert metadata["format"] == "GeoTIFF"
    assert metadata["raster"]["nodata"] == -99999.0
    assert metadata["raster"]["width"] == 20
    assert metadata["sources"][0]["source_id"] == "worldpop"
    assert any("not a census count" in item for item in metadata["limitations"])
    assert result.fingerprint == metadata["content_sha256"]
    path, info = read_population(dirs["processed"], config.settings)
    assert path == output and info.valid_pixels == 100


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"crs": "EPSG:32735"}, "expected EPSG:4326"),
        ({"crs": None}, "has no CRS"),
        ({"pixel_arcsec": 30.0}, "expected 3"),
        ({"nodata": None}, "no nodata value"),
        ({"west": 36.8, "north": -1.2}, "not inside the Rwanda envelope"),
    ],
    ids=["wrong-crs", "no-crs", "wrong-resolution", "no-nodata", "outside-rwanda"],
)
def test_wrong_rasters_are_rejected(config, dirs, options, message):
    path = population_tif(dirs["work"] / "p.tif", **options)
    with pytest.raises(DataValidationError, match=message):
        inspect_population_raster(path, config.settings)


def test_an_all_nodata_raster_is_empty(config, dirs):
    values = np.full((10, 20), -99999.0, dtype=np.float32)
    path = population_tif(dirs["work"] / "p.tif", values=values)
    with pytest.raises(DataValidationError, match="the raster is empty"):
        inspect_population_raster(path, config.settings)


def test_negative_population_is_rejected(config, dirs):
    values = np.ones((10, 20), dtype=np.float32)
    values[0, 0] = -5
    path = population_tif(dirs["work"] / "p.tif", values=values)
    with pytest.raises(DataValidationError, match="negative population"):
        inspect_population_raster(path, config.settings)


@pytest.mark.parametrize(
    "content", [b"", b"SYNTHETIC: this is not a GeoTIFF"], ids=["empty", "corrupt"]
)
def test_empty_or_corrupt_files_are_rejected(config, dirs, content):
    path = dirs["work"] / "p.tif"
    path.write_bytes(content)
    with pytest.raises(DataValidationError, match="cannot be read as a raster"):
        inspect_population_raster(path, config.settings)


def test_a_processed_raster_that_changed_is_detected(config, dirs):
    install_raw(
        config, dirs["raw"], "worldpop", population_tif(dirs["work"] / "p.tif").read_bytes()
    )
    process_population(config.settings, dirs["raw"], dirs["processed"])
    population_tif(population_path(dirs["processed"]), values=np.ones((10, 20), np.float32))
    with pytest.raises(DataValidationError, match="does not match its metadata"):
        read_population(dirs["processed"], config.settings)
