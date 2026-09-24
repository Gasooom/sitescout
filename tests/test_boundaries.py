"""geoBoundaries: ADM2 is the master; provinces and country are dissolved from it (SYNTHETIC)."""

import pytest
import shapely

from sitescout.ingest import DataValidationError
from sitescout.ingest.boundaries import process_boundaries
from sitescout.ingest.layers import read_layer
from synthetic import districts_geojson, install_raw, provinces_geojson, square


def _install(config, dirs, adm2=None, adm1=None):
    install_raw(config, dirs["raw"], "boundaries_adm2", adm2 or districts_geojson())
    install_raw(config, dirs["raw"], "boundaries_adm1", adm1 or provinces_geojson())


def _read(config, dirs, name):
    return read_layer(name, dirs["processed"], config.settings)


def test_districts_are_kept_and_assigned_to_provinces_by_overlap(config, dirs):
    _install(config, dirs)
    process_boundaries(config.settings, dirs["raw"], dirs["processed"])
    districts = _read(config, dirs, "admin_districts").set_index("district_id")
    assert list(districts.index) == ["SYN-D1", "SYN-D2", "SYN-D3", "SYN-D4"]
    assert districts["province_code"].to_dict() == {
        "SYN-D1": "RW-91",
        "SYN-D2": "RW-92",
        "SYN-D3": "RW-91",
        "SYN-D4": "RW-92",
    }
    # The east province starts 0.002 degrees into districts 2 and 4.
    assert districts.loc["SYN-D2", "province_overlap_share"] == pytest.approx(0.98, abs=1e-3)
    assert districts.loc["SYN-D1", "province_overlap_share"] == pytest.approx(1.0)
    assert (districts.geom_type == "MultiPolygon").all()
    assert districts["area_km2"].iloc[0] == pytest.approx(123.4, rel=0.01)


def test_provinces_and_country_are_dissolved_from_districts_so_edges_match(config, dirs):
    _install(config, dirs)
    process_boundaries(config.settings, dirs["raw"], dirs["processed"])
    districts = _read(config, dirs, "admin_districts")
    provinces = _read(config, dirs, "admin_provinces").set_index("province_code")
    country = _read(config, dirs, "admin_country")

    west = shapely.union_all([square(30.0, -1.9), square(30.0, -2.0)])
    # The ADM1 polygon reaches 30.102; the derived province stops at the district edge 30.1.
    assert provinces.loc["RW-91"].geometry.equals(west)
    assert provinces["district_count"].to_dict() == {"RW-91": 2, "RW-92": 2}
    assert country.geometry.iloc[0].equals(shapely.union_all(districts.geometry.array))
    assert country["district_count"].iloc[0] == 4
    assert country["province_count"].iloc[0] == 2
    assert provinces["area_km2"].sum() == pytest.approx(country["area_km2"].iloc[0])


def test_an_unexpected_district_count_stops_ingestion(config, dirs):
    three = districts_geojson().decode().replace('"SYN-D4"', '"SYN-D3"')
    _install(config, dirs, adm2=three.encode())
    with pytest.raises(DataValidationError, match="duplicate shapeID"):
        process_boundaries(config.settings, dirs["raw"], dirs["processed"])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"shapeName": ["SYNTHETIC District"] * 4}, "duplicate shapeName"),
        ({"shapeGroup": "KEN"}, "shapeGroup values ['KEN']"),
        ({"shapeType": "ADM3"}, "shapeType values ['ADM3']"),
        ({"shapeID": ["a", None, "c", "d"]}, "blank shapeID"),
    ],
    ids=["duplicate-names", "wrong-country", "wrong-level", "blank-id"],
)
def test_source_identifier_problems_stop_ingestion(config, dirs, changes, message):
    _install(config, dirs, adm2=districts_geojson(**changes))
    with pytest.raises(DataValidationError, match=message.replace("[", r"\[").replace("]", r"\]")):
        process_boundaries(config.settings, dirs["raw"], dirs["processed"])


def test_invalid_district_geometry_is_not_repaired(config, dirs):
    bowtie = shapely.Polygon([(30.0, -1.9), (30.1, -2.0), (30.1, -1.9), (30.0, -2.0)])
    geometries = [bowtie, square(30.1, -1.9), square(30.0, -2.0), square(30.1, -2.0)]
    _install(config, dirs, adm2=districts_geojson(geometry=geometries))
    with pytest.raises(DataValidationError, match="invalid geometries"):
        process_boundaries(config.settings, dirs["raw"], dirs["processed"])


def test_a_district_without_a_clear_province_majority_stops_ingestion(config, dirs):
    # A gap from 30.14 to 30.16: districts 2 and 4 are only about 40% in either province.
    _install(config, dirs, adm1=provinces_geojson(west_edge=30.14, east_edge=30.16))
    with pytest.raises(DataValidationError, match=r"largest province overlap is 4\d\.\d%"):
        process_boundaries(config.settings, dirs["raw"], dirs["processed"])


def test_a_province_with_no_district_stops_ingestion(config, dirs):
    # The west province covers every district; the east one is a sliver at the edge.
    _install(config, dirs, adm1=provinces_geojson(west_edge=30.199, east_edge=30.199))
    with pytest.raises(DataValidationError, match=r"provinces with no district: \['RW-92'\]"):
        process_boundaries(config.settings, dirs["raw"], dirs["processed"])
