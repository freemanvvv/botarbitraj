"""Тесты src/house_norms.py — адаптер проверки норм для bim_agents-контрактов
(«частный сектор»)."""
from src.bim_agents.contracts import BuildingProgram, Room
from src.bim_agents.floorplan_agent import generate_floor_plan
from src.house_norms import normalize_room_type, validate_house_plan


def test_normalize_room_type_maps_keywords():
    assert normalize_room_type("IfcSpace:KITCHEN", "Кухня") == "kitchen"
    assert normalize_room_type("", "Детская") == "bedroom"
    assert normalize_room_type("IfcSpace:BATHROOM", "") == "bathroom"
    assert normalize_room_type("", "Прихожая") == "hallway"
    assert normalize_room_type("something unknown", "???") == "living"


def _program(rooms, storeys=1, footprint=(12, 10)):
    return BuildingProgram(
        project_name="T", storeys=storeys, footprint={"width_m": footprint[0], "depth_m": footprint[1]},
        rooms=rooms,
    )


def test_validate_house_plan_empty_program_is_an_error():
    program = _program([])
    floor_plan = generate_floor_plan(program)
    issues = validate_house_plan(program, floor_plan)
    assert any(i["severity"] == "error" for i in issues)


def test_validate_house_plan_flags_undersized_room():
    # Однокомнатный этаж треугольный солвер (при единственной комнате)
    # всегда растягивает на весь footprint независимо от area_m2 — берём
    # два помещения, чтобы деление по площади было пропорциональным и
    # маленькая комната реально осталась маленькой.
    program = _program([
        Room(id="tiny", name="Каморка", storey=0, area_m2=1, type="IfcSpace:LIVING"),
        Room(id="rest", name="Гостиная", storey=0, area_m2=99, type="IfcSpace:LIVING"),
    ])
    floor_plan = generate_floor_plan(program)
    issues = validate_house_plan(program, floor_plan)
    assert any(i["element_name"] == "Каморка" for i in issues)


def test_validate_house_plan_no_false_positive_for_compliant_rooms():
    program = _program([
        Room(id="living", name="Гостиная", storey=0, area_m2=50, type="IfcSpace:LIVING"),
        Room(id="kitchen", name="Кухня", storey=0, area_m2=50, type="IfcSpace:KITCHEN"),
    ], footprint=(12, 10))
    floor_plan = generate_floor_plan(program)
    issues = validate_house_plan(program, floor_plan)
    assert issues == []
