"""The energydata.info transmission network: a grid-evidence cross-check (SYNTHETIC)."""

import json

import pytest
import shapely

from sitescout.ingest import DataValidationError
from sitescout.ingest.grid import line_id, process_grid
from sitescout.ingest.layers import metadata_path, read_layer
from synthetic import install_raw, transmission_zip


def test_lines_are_stored_in_4326_with_metric_lengths_and_no_source_names(config, dirs):
    install_raw(config, dirs["raw"], "grid_transmission", transmission_zip(dirs["work"]))
    process_grid(config.settings, dirs["raw"], dirs["processed"])
    lines = read_layer("grid_transmission_lines", dirs["processed"], config.settings)
    assert len(lines) == 2
    assert lines.crs.to_epsg() == 4326
    assert set(lines.columns) == {
        "line_id",
        "voltage_kv",
        "status",
        "from_name",
        "to_name",
        "length_km",
        "geometry",
    }
    assert lines["to_name"].isna().sum() == 1  # unknown stays unknown
    first = lines.set_index("line_id").loc[
        line_id(shapely.LineString([(30.01, -1.91), (30.09, -1.99)]))
    ]
    assert first["length_km"] == pytest.approx(12.6, rel=0.01)
    metadata = json.loads(metadata_path(dirs["processed"], "grid_transmission_lines").read_text())
    assert any("Cross-check only" in note for note in metadata["notes"])
    assert "SOURCES" not in metadata["columns"]


def test_a_source_in_another_crs_is_reprojected_explicitly(config, dirs):
    install_raw(
        config, dirs["raw"], "grid_transmission", transmission_zip(dirs["work"], crs="EPSG:32735")
    )
    with pytest.raises(DataValidationError, match="expected EPSG:4326"):
        process_grid(config.settings, dirs["raw"], dirs["processed"])


def test_duplicate_lines_are_rejected_not_merged(config, dirs):
    line = shapely.LineString([(30.01, -1.91), (30.09, -1.99)])
    install_raw(
        config, dirs["raw"], "grid_transmission", transmission_zip(dirs["work"], lines=[line, line])
    )
    with pytest.raises(DataValidationError, match="duplicate 'line_id'"):
        process_grid(config.settings, dirs["raw"], dirs["processed"])


def test_lines_outside_rwanda_are_rejected(config, dirs):
    outside = shapely.LineString([(36.8, -1.2), (36.9, -1.3)])
    install_raw(
        config, dirs["raw"], "grid_transmission", transmission_zip(dirs["work"], lines=[outside])
    )
    with pytest.raises(DataValidationError, match="not inside the Rwanda envelope"):
        process_grid(config.settings, dirs["raw"], dirs["processed"])


def test_line_ids_depend_only_on_geometry():
    line = shapely.LineString([(30.0, -1.9), (30.1, -2.0)])
    assert line_id(line) == line_id(shapely.LineString([(30.0, -1.9), (30.1, -2.0)]))
    assert line_id(line) != line_id(line.reverse())
