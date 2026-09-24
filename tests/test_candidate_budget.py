"""The candidate budget (D-031): district quotas and priority-ordered spacing (SYNTHETIC)."""

import hashlib
import inspect
import json

import numpy as np
import pytest

import sitescout.candidates as candidates_module
from sitescout.candidates import (
    INPUT_LAYERS,
    CandidateBudgetError,
    allocate_quotas,
    apply_budget,
    generate_candidates,
    greedy_spacing,
    load_inputs,
    run_candidates,
    spacing_select,
)
from sitescout.config import build_config
from sitescout.ingest.layers import (
    CANDIDATES,
    CANDIDATES_ELIGIBLE,
    layer_path,
    metadata_path,
    read_layer,
)
from synthetic import candidate_pois, synthetic_config, write_candidate_world

BUDGET = 5  # of the 8 eligible SYNTHETIC candidates: 7 in SYN-D1, 1 in SYN-D2


@pytest.fixture
def config(settings_data, weights_data):
    base = synthetic_config(settings_data, weights_data)
    return build_config(
        base.snapshot()["settings"],
        weights_data,
        overrides={
            "settings.candidates.target_count.min": 1,
            "settings.candidates.target_count.max": 1000,
            "settings.candidates.budget.size": BUDGET,
        },
    )


@pytest.fixture
def world(config, dirs):
    write_candidate_world(dirs["processed"], config.settings)
    return dirs["processed"]


@pytest.fixture
def eligible(config, world):
    frame, _ = generate_candidates(load_inputs(world, config.settings), config.settings)
    return frame


# --- Quotas ---------------------------------------------------------------------------------


def test_quotas_are_floors_plus_largest_remainders():
    # raw quotas 2.0, 1.2 and 0.8: floors 2, 1, 0; the one seat left goes to C (0.8).
    assert allocate_quotas({"A": 5, "B": 3, "C": 2}, 4) == {"A": 2, "B": 1, "C": 1}


def test_equal_remainders_go_to_the_lower_district_id():
    # raw quotas 1.33 each: floors 1, 1, 1; the seat left goes to "A".
    assert allocate_quotas({"C": 3, "A": 3, "B": 3}, 4) == {"A": 2, "B": 1, "C": 1}


def test_quotas_are_exactly_proportional_when_they_divide_evenly():
    assert allocate_quotas({"A": 40, "B": 20, "C": 60}, 60) == {"A": 20, "B": 10, "C": 30}


def test_quotas_sum_to_the_budget_and_follow_the_formula():
    counts = {f"D{i:02d}": n for i, n in enumerate([22, 14, 38, 35, 16, 8, 60, 8, 22, 26])}
    total = sum(counts.values())
    quotas = allocate_quotas(counts, 100)
    assert sum(quotas.values()) == 100
    for district, n in counts.items():
        raw = 100 * n / total
        assert quotas[district] in (int(np.floor(raw)), int(np.floor(raw)) + 1)
        assert quotas[district] <= n
    extra = [d for d in counts if quotas[d] > np.floor(100 * counts[d] / total)]
    rest = [d for d in counts if d not in extra]
    remainder = {d: 100 * counts[d] % total for d in counts}
    assert min(remainder[d] for d in extra) >= max(remainder[d] for d in rest)


def test_a_budget_larger_than_the_candidates_is_an_error():
    with pytest.raises(CandidateBudgetError, match="3 eligible candidates, fewer than"):
        allocate_quotas({"A": 2, "B": 1}, 4)


# --- Spacing within a district ----------------------------------------------------------------


def _line(*kilometres):
    return np.array([[km * 1000.0, 0.0] for km in kilometres])


def test_spacing_uses_the_largest_radius_that_keeps_the_quota():
    picks, radius = spacing_select(_line(0, 1, 2, 3, 4), 2)
    assert picks == [0, 4]
    assert radius == 3000
    # The next larger pairwise distance (4 km) would keep only one.
    distances = np.abs(_line(0, 1, 2, 3, 4)[:, None, 0] - _line(0, 1, 2, 3, 4)[None, :, 0])
    assert len(greedy_spacing(distances, 4000)) < 2


def test_higher_priority_wins_when_candidates_compete():
    # Priority order: the candidate at 2 km first (e.g. fuel), then 0, 1, 3 and 4 km.
    picks, radius = spacing_select(_line(2, 0, 1, 3, 4), 2)
    assert radius == 1000
    assert picks == [0, 1]  # 2 km (first in priority), then 0 km


def test_more_than_the_quota_is_cut_in_priority_order():
    # At the chosen 1 km radius three rows survive (2, 0 and 4 km); the quota is 2.
    distances = np.abs(_line(2, 0, 1, 3, 4)[:, None, 0] - _line(2, 0, 1, 3, 4)[None, :, 0])
    assert greedy_spacing(distances, 1000) == [0, 1, 4]
    assert spacing_select(_line(2, 0, 1, 3, 4), 2)[0] == [0, 1]


def test_spacing_drops_a_candidate_exactly_at_the_radius():
    distances = np.abs(_line(0, 1)[:, None, 0] - _line(0, 1)[None, :, 0])
    assert greedy_spacing(distances, 1000) == [0]
    assert greedy_spacing(distances, 999.999) == [0, 1]


def test_a_full_or_empty_quota():
    assert spacing_select(_line(0, 1, 2), 3) == ([0, 1, 2], 0.0)
    assert spacing_select(_line(0, 1, 2), 0) == ([], None)


# --- The whole budget on the SYNTHETIC world ------------------------------------------------------


def test_the_budget_selects_exactly_its_size_with_district_quotas(config, eligible):
    selected, record = apply_budget(eligible, config.settings)
    assert int(selected.sum()) == BUDGET == record["selected_count"]
    assert record["eligible_count"] == 8
    # raw quotas 5*7/8 = 4.375 and 5*1/8 = 0.625: floors 4 and 0, the seat left goes to
    # SYN-D2 (remainder 0.625 > 0.375).
    assert {d: e["quota"] for d, e in record["districts"].items()} == {"SYN-D1": 4, "SYN-D2": 1}
    chosen = eligible.loc[selected]
    assert chosen["district_id"].value_counts().to_dict() == {"SYN-D1": 4, "SYN-D2": 1}
    assert record["districts"]["SYN-D2"]["radius_m"] == 0.0  # its only candidate


def test_the_budget_does_not_depend_on_row_order(config, eligible):
    selected, _ = apply_budget(eligible, config.settings)
    shuffled = eligible.sample(frac=1.0, random_state=7).reset_index(drop=True)
    again, _ = apply_budget(shuffled, config.settings)
    assert set(eligible.loc[selected, "candidate_id"]) == set(shuffled.loc[again, "candidate_id"])


def test_the_budget_prefers_higher_priority_hosts_in_a_district(config, eligible):
    selected, _ = apply_budget(eligible, config.settings)
    d1 = eligible[eligible["district_id"] == "SYN-D1"]
    kept_types = set(eligible.loc[selected & (eligible["district_id"] == "SYN-D1"), "host_type"])
    assert "fuel" in kept_types
    assert len(d1) == 7


def test_written_layers_hold_the_universe_and_exactly_the_selection(config, world):
    results = run_candidates(config.settings, world)
    assert [r.name for r in results] == ["candidates_eligible", "candidates"]
    universe = read_layer("candidates_eligible", world, config.settings)
    chosen = read_layer("candidates", world, config.settings)
    assert len(universe) == 8 and len(chosen) == BUDGET
    assert universe["selected"].sum() == BUDGET
    assert sorted(universe.loc[universe["selected"], "candidate_id"]) == sorted(
        chosen["candidate_id"]
    )
    assert list(chosen.columns) == [*CANDIDATES.column_names, "geometry"]
    assert list(universe.columns) == [*CANDIDATES_ELIGIBLE.column_names, "geometry"]
    assert universe.crs.to_epsg() == 4326 and chosen.crs.to_epsg() == 4326
    assert list(chosen["candidate_id"]) == sorted(chosen["candidate_id"])


def test_metadata_records_counts_quotas_radii_and_method(config, world):
    run_candidates(config.settings, world)
    for name in ("candidates", "candidates_eligible"):
        budget = json.loads(metadata_path(world, name).read_text(encoding="utf-8"))["stats"][
            "budget"
        ]
        assert budget["eligible_count"] == 8
        assert budget["selected_count"] == BUDGET
        assert budget["target_budget"] == BUDGET
        assert "largest remainder" in budget["method"]
        assert set(budget["districts"]) == {"SYN-D1", "SYN-D2"}
        for entry in budget["districts"].values():
            assert {"district", "eligible", "quota", "selected", "radius_m"} <= set(entry)


def test_repeated_runs_give_byte_identical_outputs(config, world):
    def digests():
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ("candidates", "candidates_eligible")
            for path in (layer_path(world, name), metadata_path(world, name))
        }

    run_candidates(config.settings, world)
    first = digests()
    run_candidates(config.settings, world)
    assert digests() == first


def test_fewer_eligible_than_the_budget_writes_nothing(settings_data, weights_data, dirs):
    config = build_config(
        synthetic_config(settings_data, weights_data).snapshot()["settings"],
        weights_data,
        overrides={
            "settings.candidates.target_count.min": 1,
            "settings.candidates.target_count.max": 1000,
            "settings.candidates.budget.size": 9,
        },
    )
    write_candidate_world(dirs["processed"], config.settings)
    with pytest.raises(CandidateBudgetError, match="8 eligible candidates, fewer than"):
        run_candidates(config.settings, dirs["processed"])
    for name in ("candidates", "candidates_eligible"):
        assert not layer_path(dirs["processed"], name).exists()


# --- No charger dependency ---------------------------------------------------------------------


def test_no_charger_layer_is_an_input():
    assert not any("charg" in name for name in INPUT_LAYERS)
    source = inspect.getsource(candidates_module)
    assert "osm_charging_stations" not in source
    assert "chargers_manual" not in source


def test_removing_a_charging_station_changes_nothing(config, dirs):
    write_candidate_world(dirs["processed"], config.settings)
    with_station, _ = generate_candidates(
        load_inputs(dirs["processed"], config.settings), config.settings
    )
    selected_with, _ = apply_budget(with_station, config.settings)
    without = [row for row in candidate_pois() if row["feature_id"] != "node/14"]
    write_candidate_world(dirs["processed"], config.settings, pois=without)
    no_station, _ = generate_candidates(
        load_inputs(dirs["processed"], config.settings), config.settings
    )
    selected_without, _ = apply_budget(no_station, config.settings)
    assert set(with_station.loc[selected_with, "candidate_id"]) == set(
        no_station.loc[selected_without, "candidate_id"]
    )
