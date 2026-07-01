"""Тесты нормоконтроля: и нижние границы (КМК/ШНК), и верхние инженерные
пределы (защита от DoS через аномальные параметры от LLM)."""
from src.normbase.validator import validate_and_fix_params, validate_building_meta


def test_low_floor_height_is_raised_to_norm_minimum():
    params = {"num_floors": 2, "height": 4.0, "wall_thickness": 0.4, "slab_thickness": 0.2}
    fixed, notes = validate_and_fix_params(params, "жилой")
    assert fixed["height"] / fixed["num_floors"] >= 2.7
    assert notes  # должно быть замечание


def test_thin_wall_is_raised_for_tall_building():
    params = {"num_floors": 12, "height": 36.0, "wall_thickness": 0.2, "slab_thickness": 0.2}
    fixed, notes = validate_and_fix_params(params, "жилой")
    assert fixed["wall_thickness"] >= 0.25


def test_normal_params_pass_through_mostly_unchanged():
    params = {"num_floors": 5, "height": 15.0, "wall_thickness": 0.51, "slab_thickness": 0.2}
    fixed, notes = validate_and_fix_params(params, "жилой")
    assert not any("превышает разумный предел" in n for n in notes)


def test_upper_bound_caps_extreme_num_floors():
    params = {"num_floors": 999999, "height": 999999 * 3.0, "wall_thickness": 0.4, "slab_thickness": 0.2}
    fixed, notes = validate_and_fix_params(params, "жилой")
    assert fixed["num_floors"] <= 120
    assert any("превышает разумный предел" in n for n in notes)


def test_upper_bound_caps_extreme_dimensions_and_windows():
    params = {
        "num_floors": 2, "height": 6.0, "wall_thickness": 0.4, "slab_thickness": 0.2,
        "length": 1e9, "width": 1e9,
        "windows_per_wall_long": 100000, "windows_per_wall_short": 100000,
    }
    fixed, notes = validate_and_fix_params(params, "жилой")
    assert fixed["length"] <= 500
    assert fixed["width"] <= 500
    assert fixed["windows_per_wall_long"] <= 50
    assert fixed["windows_per_wall_short"] <= 50


def test_building_meta_requires_elevator_above_5_floors():
    meta, notes = validate_building_meta({"has_elevator": False}, num_floors=9)
    assert meta["has_elevator"] is True
    assert notes


def test_building_meta_requires_two_elevators_above_9_floors():
    meta, notes = validate_building_meta(
        {"has_elevator": True, "elevators_per_entrance": 1}, num_floors=16
    )
    assert meta["elevators_per_entrance"] >= 2


def test_building_meta_upper_bound_caps():
    meta, notes = validate_building_meta(
        {"entrances": 500, "apartments_per_landing": 100, "apartment_rooms": 99,
         "elevators_per_entrance": 50},
        num_floors=10,
    )
    assert meta["entrances"] <= 20
    assert meta["apartments_per_landing"] <= 8
    assert meta["apartment_rooms"] <= 6
    assert meta["elevators_per_entrance"] <= 4
    assert any("превышает разумный предел" in n for n in notes)


def test_building_meta_normal_values_unaffected():
    meta, notes = validate_building_meta(
        {"entrances": 4, "apartments_per_landing": 3, "apartment_rooms": 2,
         "has_elevator": True, "elevators_per_entrance": 2, "elevator_capacity_kg": 630,
         "stair_width_m": 1.2},
        num_floors=20,
    )
    assert not any("превышает разумный предел" in n for n in notes)
