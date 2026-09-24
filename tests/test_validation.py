"""Schema and geometry checks: every problem is reported, nothing is converted or repaired."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from sitescout.config import load_config
from sitescout.ingest import DataValidationError
from sitescout.ingest.geometry import check_geometry, repair_polygons, to_multipolygon
from sitescout.ingest.layers import (
    CHARGERS_MANUAL,
    DISTRICTS,
    LAYERS,
    empty_frame,
    read_layer,
    validate_layer,
    write_layer,
)
from sitescout.ingest.schema import Column, LayerSchema, check_table, validate_table

SETTINGS = load_config().settings
BBOX = SETTINGS.ingest.rwanda_bbox

# A small SYNTHETIC schema and table used by the tests below.
SCHEMA = LayerSchema(
    name="synthetic_layer",
    description="SYNTHETIC",
    id_column="item_id",
    columns=(
        Column("item_id", "string", True, "id"),
        Column("count", "int64", True, "a count"),
        Column("share", "float64", False, "a share"),
        Column("flag", "bool", True, "a flag"),
        Column("note", "string", False, "optional text"),
    ),
    geometry_types=frozenset({"Point"}),
    sort_by=("item_id",),
)


def _frame(**changes) -> gpd.GeoDataFrame:
    data = {
        "item_id": ["a", "b"],
        "count": np.array([1, 2], dtype="int64"),
        "share": [0.5, np.nan],
        "flag": [True, False],
        "note": ["x", None],
        "geometry": [shapely.Point(30.0, -1.9), shapely.Point(30.1, -2.0)],
    }
    data.update(changes)
    return gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:4326")


# --- Schema -----------------------------------------------------------------------------------


def test_a_conforming_table_passes():
    assert check_table(_frame(), SCHEMA) == []


def test_missing_and_unexpected_columns_are_reported():
    frame = _frame().drop(columns=["share"]).assign(extra=1)
    problems = check_table(frame, SCHEMA)
    assert "missing columns: ['share']" in problems
    assert "unexpected columns: ['extra']" in problems


@pytest.mark.parametrize(
    ("column", "value", "kind"),
    [
        ("count", [1.0, 2.0], "int64"),
        ("count", ["1", "2"], "int64"),
        ("share", ["0.5", "0.1"], "float64"),
        ("flag", [1, 0], "bool"),
        ("item_id", [1, 2], "string"),
        ("note", ["x", 3], "string"),
    ],
)
def test_wrong_types_are_reported_not_converted(column, value, kind):
    problems = check_table(_frame(**{column: value}), SCHEMA)
    assert any(f"column {column!r}" in p and f"expected {kind}" in p for p in problems)


def test_nulls_in_required_columns_are_reported():
    problems = check_table(_frame(item_id=["a", None]), SCHEMA)
    assert "required column 'item_id' has 1 null value(s)" in problems


def test_nulls_in_optional_columns_are_allowed():
    assert check_table(_frame(note=[None, None], share=[np.nan, np.nan]), SCHEMA) == []


def test_duplicate_ids_are_reported():
    problems = check_table(_frame(item_id=["a", "a"]), SCHEMA)
    assert any("duplicate 'item_id'" in p and "['a']" in p for p in problems)


def test_an_empty_table_is_an_error_unless_allowed():
    empty = _frame().iloc[0:0]
    assert "the layer is empty" in check_table(empty, SCHEMA)
    assert "the layer is empty" not in check_table(empty, LAYERS["osm_charging_stations"])


def test_validate_table_raises_with_every_problem_listed():
    with pytest.raises(DataValidationError) as raised:
        validate_table(_frame(item_id=["a", "a"], count=[1.5, 2.5]), SCHEMA)
    message = str(raised.value)
    assert "duplicate 'item_id'" in message
    assert "column 'count'" in message


# --- Geometry --------------------------------------------------------------------------------


def _geom_problems(geoms, allowed=frozenset({"Point"}), mode="within"):
    frame = gpd.GeoDataFrame({"geometry": geoms}, geometry="geometry", crs="EPSG:4326")
    return check_geometry(frame, allowed_types=allowed, bbox=BBOX, bounds_mode=mode)


def test_valid_geometry_passes():
    assert _geom_problems([shapely.Point(30.0, -1.9)]) == []


def test_invalid_geometry_is_reported_with_the_reason():
    bowtie = shapely.Polygon([(30.0, -1.9), (30.1, -2.0), (30.1, -1.9), (30.0, -2.0)])
    problems = _geom_problems([bowtie], allowed=frozenset({"Polygon"}))
    assert any("1 invalid geometries" in p and "Self-intersection" in p for p in problems)


def test_unexpected_geometry_type_is_reported():
    line = shapely.LineString([(30.0, -1.9), (30.1, -1.9)])
    problems = _geom_problems([line])
    assert any("of type ['LineString']" in p for p in problems)


def test_empty_and_missing_geometries_are_reported():
    problems = _geom_problems([shapely.Point(), None])
    assert any("1 empty geometries" in p for p in problems)
    assert any("1 missing geometries" in p for p in problems)


@pytest.mark.parametrize(
    "point",
    [shapely.Point(28.861, -2.84), shapely.Point(30.899, -1.047), shapely.Point(29.74, -1.94)],
    ids=["south-west", "north-east", "centre"],
)
def test_coordinates_inside_rwanda_pass(point):
    assert _geom_problems([point]) == []


@pytest.mark.parametrize(
    ("point", "message"),
    [
        (shapely.Point(36.82, -1.29), "not inside the Rwanda envelope"),  # Nairobi
        (shapely.Point(-1.95, 30.06), "not inside the Rwanda envelope"),  # lat/lon swapped
        (shapely.Point(200.0, -1.9), "outside longitude/latitude range"),
        (shapely.Point(30.0, -95.0), "outside longitude/latitude range"),
    ],
    ids=["another-country", "swapped-axes", "longitude-out-of-range", "latitude-out-of-range"],
)
def test_out_of_range_or_outside_rwanda_coordinates_are_reported(point, message):
    assert any(message in p for p in _geom_problems([point]))


def test_intersects_mode_accepts_features_crossing_the_border_only():
    crossing = shapely.LineString([(30.8, -1.5), (31.2, -1.5)])
    outside = shapely.LineString([(31.5, -1.5), (31.9, -1.5)])
    allowed = frozenset({"LineString"})
    assert _geom_problems([crossing], allowed, mode="intersects") == []
    assert any("not inside" in p for p in _geom_problems([crossing], allowed, mode="within"))
    assert any("not touching" in p for p in _geom_problems([outside], allowed, mode="intersects"))


def test_repair_is_explicit_and_flags_rows():
    bowtie = shapely.Polygon([(30.0, -1.9), (30.1, -2.0), (30.1, -1.9), (30.0, -2.0)])
    valid = shapely.box(30.0, -2.0, 30.1, -1.9)
    geoms, repaired = repair_polygons(np.array([bowtie, valid], dtype=object))
    assert repaired.tolist() == [True, False]
    assert shapely.is_valid(geoms[0]) and geoms[0].geom_type == "MultiPolygon"
    assert geoms[1] is valid


def test_polygons_are_promoted_to_multipolygons():
    out = to_multipolygon(np.array([shapely.box(30, -2, 30.1, -1.9)], dtype=object))
    assert out[0].geom_type == "MultiPolygon"


# --- Whole layers ------------------------------------------------------------------------------


def test_layer_validation_checks_crs_schema_and_geometry_together():
    frame = gpd.GeoDataFrame(
        {
            "charger_id": ["csv-1"],
            "source_row": np.array([2], dtype="int64"),
            "source_url": ["https://example.org"],
            "date_retrieved": ["2026-01-01"],
        },
        geometry=[shapely.Point(30.0, -1.9)],
        crs="EPSG:4326",
    )
    validate_layer(frame, CHARGERS_MANUAL, SETTINGS)
    with pytest.raises(DataValidationError, match="expected EPSG:4326"):
        validate_layer(frame.to_crs("EPSG:32735"), CHARGERS_MANUAL, SETTINGS)


def test_every_layer_schema_is_consistent():
    for schema in LAYERS.values():
        names = schema.column_names
        assert len(set(names)) == len(names), schema.name
        assert schema.id_column in names, schema.name
        assert schema.column(schema.id_column).required, schema.name
        assert set(schema.sort_by) <= set(names), schema.name
        assert "geometry" not in names, schema.name
        assert schema.geometry_types, schema.name


def test_no_layer_carries_names_brands_or_operators():
    forbidden = {"name", "brand", "operator", "operator_public_name"}
    for schema in LAYERS.values():
        assert not forbidden & set(schema.column_names), schema.name


def test_district_layer_is_the_master_boundary():
    assert DISTRICTS.name == "admin_districts"
    assert DISTRICTS.geometry_types == frozenset({"MultiPolygon"})
    assert pd.Index(DISTRICTS.column_names).is_unique


# --- Writing and reading ----------------------------------------------------------------------


def test_an_allowed_empty_layer_round_trips_with_its_types(temp_dir):
    schema = LAYERS["osm_charging_stations"]
    empty = empty_frame(schema, "EPSG:4326")
    write_layer(empty, schema, temp_dir, SETTINGS, sources=[])
    back = read_layer(schema.name, temp_dir, SETTINGS)
    assert len(back) == 0
    assert str(back["osm_id"].dtype) == "int64"


def test_missing_optional_text_stays_missing_after_writing(temp_dir):
    schema = LAYERS["grid_transmission_lines"]
    lines = gpd.GeoDataFrame(
        {
            "line_id": ["tx-1", "tx-2"],
            "voltage_kv": [110.0, 30.0],
            "status": ["Existing", "Planned"],
            "from_name": ["SYNTHETIC A", None],
            "to_name": [None, None],
            "length_km": [1.0, 2.0],
        },
        geometry=[
            shapely.LineString([(30.0, -1.9), (30.1, -1.9)]),
            shapely.LineString([(30.0, -2.0), (30.1, -2.0)]),
        ],
        crs="EPSG:4326",
    )
    write_layer(lines, schema, temp_dir, SETTINGS, sources=[])
    back = read_layer(schema.name, temp_dir, SETTINGS)
    assert back["to_name"].isna().all()
    assert "None" not in back["from_name"].dropna().tolist()


def test_an_edited_layer_is_refused_on_read(temp_dir):
    schema = LAYERS["grid_transmission_lines"]
    lines = gpd.GeoDataFrame(
        {
            "line_id": ["tx-1"],
            "voltage_kv": [110.0],
            "status": ["Existing"],
            "from_name": [None],
            "to_name": [None],
            "length_km": [1.0],
        },
        geometry=[shapely.LineString([(30.0, -1.9), (30.1, -1.9)])],
        crs="EPSG:4326",
    )
    write_layer(lines, schema, temp_dir, SETTINGS, sources=[])
    lines.assign(voltage_kv=220.0).to_parquet(temp_dir / f"{schema.name}.parquet", index=False)
    with pytest.raises(DataValidationError, match="does not match its metadata"):
        read_layer(schema.name, temp_dir, SETTINGS)
