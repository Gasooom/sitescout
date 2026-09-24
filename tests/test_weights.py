"""config/weights.yaml: SPEC §5 feature weights, profile weights and bonus tables."""

import math

import pytest

from sitescout.config import ConfigError, build_config, load_config
from support import delete_path, flatten, set_path

COMPONENTS = ("demand", "access", "host_commercial", "charging_gap", "grid_evidence")

# docs/SPEC.md §5. Changing a value needs a change here and a docs/decisions.md entry.
EXPECTED_WEIGHTS = {
    "components.demand.pop_5km": 0.5,
    "components.demand.pop_1km": 0.25,
    "components.demand.pop_10km": 0.25,
    "components.access.dist_road_m": 0.5,
    "components.access.dist_trunk_m": 0.5,
    "components.host_commercial.poi_1km": 0.5,
    "components.host_commercial.poi_3km": 0.5,
    "components.charging_gap.dist_charger_m": 0.5,
    "components.charging_gap.chargers_10km": 0.25,
    "components.charging_gap.chargers_25km": 0.25,
    "components.grid_evidence.dist_substation_m": 0.5,
    "components.grid_evidence.dist_line_m": 0.5,
    "profiles.urban.demand": 0.30,
    "profiles.urban.host_commercial": 0.25,
    "profiles.urban.access": 0.20,
    "profiles.urban.charging_gap": 0.15,
    "profiles.urban.grid_evidence": 0.10,
    "profiles.corridor.demand": 0.15,
    "profiles.corridor.host_commercial": 0.15,
    "profiles.corridor.access": 0.30,
    "profiles.corridor.charging_gap": 0.25,
    "profiles.corridor.grid_evidence": 0.15,
    "bonuses.host_commercial.host_type.fuel": 10,
    "bonuses.host_commercial.host_type.mall": 10,
    "bonuses.host_commercial.host_type.supermarket": 5,
    "bonuses.host_commercial.host_type.logistics": 5,
    "bonuses.host_commercial.host_type.hotel": 5,
    "bonuses.host_commercial.host_type.industrial": 0,
    "bonuses.host_commercial.host_type.none": 0,
    "bonuses.access.road_class.trunk": 10,
    "bonuses.access.road_class.primary": 5,
}


def test_weights_match_spec():
    assert flatten(load_config().snapshot()["weights"]) == EXPECTED_WEIGHTS


@pytest.mark.parametrize("component", COMPONENTS)
def test_feature_weights_in_each_component_sum_to_one(component):
    group = getattr(load_config().weights.components, component)
    assert math.isclose(sum(group.model_dump().values()), 1.0, abs_tol=1e-9)


@pytest.mark.parametrize("profile", ["urban", "corridor"])
def test_profile_weights_sum_to_one(profile):
    weights = getattr(load_config().weights.profiles, profile)
    assert math.isclose(sum(weights.model_dump().values()), 1.0, abs_tol=1e-9)


def test_both_profiles_weight_the_same_five_components():
    profiles = load_config().weights.profiles
    assert set(profiles.urban.model_dump()) == set(COMPONENTS)
    assert set(profiles.corridor.model_dump()) == set(COMPONENTS)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("components.demand.pop_5km", 0.6),
        ("components.charging_gap.chargers_25km", 0.2),
        ("profiles.urban.grid_evidence", 0.05),
        ("profiles.corridor.access", 0.35),
    ],
)
def test_groups_that_do_not_sum_to_one_are_rejected(settings_data, weights_data, dotted, value):
    set_path(weights_data, dotted, value)
    with pytest.raises(ConfigError, match="must sum to 1"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("profiles.urban.demand", -0.1),
        ("profiles.urban.demand", 1.3),
        ("profiles.urban.demand", "0.30"),
        ("profiles.urban.demand", True),
        ("bonuses.host_commercial.host_type.fuel", -1),
        ("bonuses.host_commercial.host_type.fuel", 101),
        ("bonuses.access.road_class.trunk", 10.5),
    ],
)
def test_out_of_range_or_mistyped_weights_and_bonuses_are_rejected(
    settings_data, weights_data, dotted, value
):
    set_path(weights_data, dotted, value)
    with pytest.raises(ConfigError):
        build_config(settings_data, weights_data)


def test_bonus_table_rejects_host_types_outside_spec(settings_data, weights_data):
    weights_data["bonuses"]["host_commercial"]["host_type"]["restaurant"] = 0
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        build_config(settings_data, weights_data)


def test_every_host_type_needs_a_bonus(settings_data, weights_data):
    delete_path(weights_data, "bonuses.host_commercial.host_type.hotel")
    with pytest.raises(ConfigError, match="Field required"):
        build_config(settings_data, weights_data)


def test_settings_host_types_must_match_the_bonus_table(settings_data, weights_data):
    settings_data["candidates"]["host_types"].remove("hotel")
    settings_data["candidates"]["dedup"]["host_priority"].remove("hotel")
    with pytest.raises(ConfigError, match="host-type bonus table"):
        build_config(settings_data, weights_data)


@pytest.mark.parametrize(
    "dotted",
    ["bonuses.access.host_type", "bonuses.host_commercial.road_class", "bonuses.demand"],
)
def test_bonus_tables_attach_only_to_their_approved_components(settings_data, weights_data, dotted):
    set_path(weights_data, dotted, {"trunk": 10})
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        build_config(settings_data, weights_data)


def test_reported_only_features_are_never_weighted():
    config = load_config()
    reported_only = set(config.settings.features.reported_only)
    assert {"dist_town_m", "dist_kigali_cbd_m"} <= reported_only
    assert reported_only.isdisjoint(config.weights.weighted_features())


def test_a_weighted_feature_cannot_be_declared_reported_only(settings_data, weights_data):
    settings_data["features"]["reported_only"].append("pop_5km")
    with pytest.raises(ConfigError, match="must never be weighted"):
        build_config(settings_data, weights_data)
