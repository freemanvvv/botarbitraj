"""Тесты src/floor_plan_render.py — дименсированный SVG-рендер этажа."""
from src.bim_agents.contracts import BuildingProgram, Room
from src.bim_agents.floorplan_agent import generate_floor_plan
from src.floor_plan_render import render_storey_svg, render_apartment_floor_svg
from src.floorplan import generate_floorplan


def test_render_storey_svg_contains_room_labels():
    program = BuildingProgram(
        project_name="T", storeys=1, footprint={"width_m": 10, "depth_m": 8},
        rooms=[
            Room(id="living_01", name="Гостиная", storey=0, area_m2=30, type="IfcSpace:LIVING"),
            Room(id="kitchen_01", name="Кухня", storey=0, area_m2=15, type="IfcSpace:KITCHEN"),
        ],
    )
    floor_plan = generate_floor_plan(program)
    svg = render_storey_svg(program, floor_plan.storeys[0], "Этаж 0")
    assert "<svg" in svg and "</svg>" in svg
    assert "Гостиная" in svg
    assert "Кухня" in svg
    assert "м²" in svg  # подпись площади


def test_render_apartment_floor_svg_contains_room_labels_and_walls():
    fp = generate_floorplan(width=8.5, depth=10.24, room_count=2, entry_side="west")
    svg = render_apartment_floor_svg(fp, "Квартира")
    assert "<svg" in svg and "</svg>" in svg
    for room in fp.rooms:
        assert (room.name or room.type) in svg
    assert svg.count('class="wall"') + svg.count('class="wall-thin"') > 0
