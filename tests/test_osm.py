"""OSM extraction with pyosmium from a tiny SYNTHETIC extract (tests/synthetic.py)."""

import json

import pytest

from sitescout.ingest import DataValidationError
from sitescout.ingest.layers import LAYERS, read_layer
from sitescout.ingest.metadata import read_json
from sitescout.ingest.osm import TagSpec, extract, osm_timestamp, process_osm
from synthetic import OSM_TIMESTAMP, install_raw, osm_pbf


@pytest.fixture
def layers(config, dirs):
    install_raw(config, dirs["raw"], "osm", osm_pbf(dirs["work"] / "s.osm.pbf").read_bytes())
    process_osm(config.settings, dirs["raw"], dirs["processed"])
    return {
        name: read_layer(name, dirs["processed"], config.settings).set_index("feature_id")
        for name in LAYERS
        if name.startswith("osm_")
    }


def test_roads_keep_every_way_as_a_line_including_closed_ones(layers):
    roads = layers["osm_roads"]
    assert sorted(roads.index) == ["way/100", "way/101"]
    assert (roads.geom_type == "LineString").all()
    assert roads.loc["way/100", "highway"] == "trunk"
    assert roads.loc["way/100", "ref"] == "SYN1"


def test_pois_are_nodes_and_areas_matching_the_configured_tags(layers):
    pois = layers["osm_pois"]
    assert sorted(pois.index) == [
        "node/1",
        "node/2",
        "node/3",
        "node/6",
        "way/102",
        "way/103",
    ]
    assert pois.loc["way/102", "shop"] == "mall"
    assert pois.loc["way/102"].geometry.geom_type == "MultiPolygon"
    assert pois.loc["way/103", "building"] == "commercial"
    assert "way/104" not in pois.index, "an untagged building=yes is not a POI"
    assert "way/112" not in pois.index, "an open way is not a POI"


def test_socket_tags_are_kept_for_backtest_leakage_checks(layers):
    assert json.loads(layers["osm_pois"].loc["node/1", "socket_tags"]) == {"socket:type2": "2"}
    assert json.loads(layers["osm_pois"].loc["node/3", "socket_tags"]) == {"socket:type2": "2"}
    assert layers["osm_pois"]["socket_tags"].isna().sum() == 4


def test_charging_stations(layers):
    stations = layers["osm_charging_stations"]
    assert list(stations.index) == ["node/3"]
    assert stations.loc["node/3", "capacity"] == "2"
    assert json.loads(stations.loc["node/3", "socket_tags"]) == {"socket:type2": "2"}


def test_power_lines_areas_and_nodes_follow_osm_conventions(layers):
    power = layers["osm_power"]
    assert power.loc["way/105"].geometry.geom_type == "LineString"
    assert power.loc["way/106"].geometry.geom_type == "MultiPolygon"  # substation area
    assert power.loc["way/107"].geometry.geom_type == "LineString"  # closed minor_line
    assert power.loc["node/4", "power"] == "tower"
    assert "node/5" not in power.index, "wholly outside the Rwanda envelope"
    assert "relation/300" not in power.index, "a route relation is not an area"


def test_water_and_protected_areas_come_from_ways_and_relations(layers):
    assert sorted(layers["osm_water"].index) == ["relation/301", "way/108"]
    assert layers["osm_water"].loc["way/108", "water"] == "lake"
    protected = layers["osm_protected_areas"]
    assert sorted(protected.index) == ["relation/302", "way/114"]
    assert protected.loc["relation/302", "protect_class"] == "2"
    assert protected["boundary"].to_dict() == {
        "relation/302": "protected_area",
        "way/114": "national_park",
    }


def test_places_are_city_and_town_nodes_only(layers):
    places = layers["osm_places"]
    assert places["place"].to_dict() == {"node/7": "city", "node/8": "town"}
    assert (places.geom_type == "Point").all()


def test_names_brands_and_operators_are_never_extracted(config, dirs, layers):
    for name in layers:
        content = (dirs["processed"] / f"{name}.parquet").read_bytes()
        assert b"SYNTHETIC Brand" not in content and b"SYNTHETIC Operator" not in content
        assert b"SYNTHETIC Mall" not in content and b"SYNTHETIC Park" not in content
        assert b"SYNTHETIC City" not in content and b"SYNTHETIC Town" not in content


def test_skipped_and_dropped_features_are_counted_in_metadata(config, dirs, layers):
    power = read_json(dirs["processed"] / "osm_power.meta.json")["stats"]
    assert power["outside_envelope_dropped"] == 1
    assert power["non_area_relations_skipped"] == 1
    assert power["osm_data_timestamp"] == OSM_TIMESTAMP
    pois = read_json(dirs["processed"] / "osm_pois.meta.json")["stats"]
    assert pois["open_ways_skipped"] == 1
    water = read_json(dirs["processed"] / "osm_water.meta.json")["stats"]
    # libosmium rejects the self-intersecting bow-tie way 113; it is counted, not hidden.
    assert water["geometry_errors"] == 1
    assert water["features"] == 2


def test_every_layer_is_stored_in_4326_with_unique_osm_ids(layers):
    for name, frame in layers.items():
        assert frame.crs.to_epsg() == 4326, name
        assert frame.index.is_unique, name


def test_the_extract_timestamp_is_read_from_the_pbf_header(dirs):
    assert osm_timestamp(osm_pbf(dirs["work"] / "s.osm.pbf")) == OSM_TIMESTAMP


def test_extraction_is_deterministic(config, dirs):
    path = osm_pbf(dirs["work"] / "s.osm.pbf")
    first, _ = extract(path, config.settings)
    second, _ = extract(path, config.settings)
    for name in first:
        assert (
            first[name]
            .sort_values("feature_id")
            .reset_index(drop=True)
            .equals(second[name].sort_values("feature_id").reset_index(drop=True))
        ), name


@pytest.mark.parametrize(
    "content", [b"SYNTHETIC: not a PBF file", b"\x00\x00"], ids=["text", "tiny"]
)
def test_a_corrupt_pbf_fails_clearly(config, dirs, content):
    install_raw(config, dirs["raw"], "osm", content)
    with pytest.raises(DataValidationError, match="not an OSM PBF"):
        process_osm(config.settings, dirs["raw"], dirs["processed"])


@pytest.mark.parametrize(
    ("text", "tags", "expected"),
    [
        ("amenity=*", {"amenity": "fuel"}, True),
        ("amenity=*", {"shop": "mall"}, False),
        ("landuse=industrial", {"landuse": "industrial"}, True),
        ("landuse=industrial", {"landuse": "farmland"}, False),
        ("socket:*", {"socket:type2": "2"}, True),
        ("socket:*", {"amenity": "fuel"}, False),
    ],
)
def test_tag_selectors(text, tags, expected):
    class Tags(dict):
        def __iter__(self):
            return iter(self.items())

    assert TagSpec.parse(text).matches(Tags(tags)) is expected
