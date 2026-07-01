"""Smoke-тесты генератора IFC — фиксируют то, что раньше проверялось только вручную."""
from src.ifc_generator import create_max_building, create_apartment_building
from src.integrity_checker import validate_model_integrity


def test_create_max_building_basic(cleanup_ifc):
    path, stats = create_max_building(
        name="TestBuilding", length=12.0, width=9.0, height=6.0, num_floors=2,
        wall_thickness=0.4, slab_thickness=0.2, roof_type="gable",
    )
    cleanup_ifc.append(path)

    assert stats["walls"] > 0
    assert stats["slabs"] > 0
    assert stats["storeys"] >= 2

    result = validate_model_integrity(path)
    assert result["ok"], result["issues"]


def test_create_max_building_flat_roof(cleanup_ifc):
    path, stats = create_max_building(
        name="FlatRoofTest", length=12.0, width=9.0, height=6.0, num_floors=2,
        roof_type="flat",
    )
    cleanup_ifc.append(path)
    assert stats["walls"] > 0
    result = validate_model_integrity(path)
    assert result["ok"], result["issues"]


def test_create_apartment_building_basic(cleanup_ifc):
    """Многоподъездный дом: лифты, лестницы, инженерные шахты, квартиры-планировки."""
    path, stats = create_apartment_building(
        name="TestApartments", num_floors=6, floor_height=3.0,
        entrances=2, apartments_per_landing=2, apartment_rooms=2,
        has_elevator=True, elevators_per_entrance=1, elevator_capacity_kg=400.0,
        wall_thickness=0.38, slab_thickness=0.20, roof_type="flat",
    )
    cleanup_ifc.append(path)

    assert stats["entrances"] == 2
    assert stats["apartments"] == 2 * 2 * 6
    assert stats["elevators"] == 2  # один лифт на подъезд
    assert stats["spaces"] > 0  # реальные комнаты, не пустые коробки
    assert stats["floorplan_issues"] == []

    result = validate_model_integrity(path)
    assert result["ok"], result["issues"]


def test_create_apartment_building_elevator_required_above_5_floors(cleanup_ifc):
    """КМК 2.08.01-89 п.6.1: без лифта на 6 этажах — integrity_checker обязан
    предупредить (норма форсируется на уровне validate_building_meta в API;
    низкоуровневый генератор доверяет своему вызывающему коду, поэтому здесь
    это warning, а не hard error — ok остаётся True)."""
    path, stats = create_apartment_building(
        name="NoElevatorTest", num_floors=6, floor_height=3.0,
        entrances=1, apartments_per_landing=2, apartment_rooms=2,
        has_elevator=False, wall_thickness=0.38, slab_thickness=0.20, roof_type="flat",
    )
    cleanup_ifc.append(path)

    result = validate_model_integrity(path)
    assert any("лифт" in i["message"].lower() for i in result["issues"])


def test_apartment_building_multi_entrance_scaling(cleanup_ifc):
    """Крупная конфигурация не должна падать и должна оставаться целостной."""
    path, stats = create_apartment_building(
        name="ScaleTest", num_floors=10, floor_height=3.0,
        entrances=3, apartments_per_landing=3, apartment_rooms=2,
        has_elevator=True, elevators_per_entrance=1, elevator_capacity_kg=400.0,
        wall_thickness=0.4, slab_thickness=0.2, roof_type="flat",
    )
    cleanup_ifc.append(path)

    assert stats["apartments"] == 3 * 3 * 10
    result = validate_model_integrity(path)
    assert result["ok"], result["issues"]
