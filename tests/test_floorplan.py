"""Тесты генеративной планировки квартир (Путь C, фазы 0-4)."""
import ifcopenshell

from src.floorplan import (
    generate_floorplan,
    validate_floorplan,
    floorplan_to_ifc,
    generate_floorplan_llm,
)
from src.ifc_generator import _g, _cp3, _d3, _make_placement


def test_solver_produces_valid_plan_for_reasonable_dimensions():
    fp = generate_floorplan(width=8.5, depth=10.24, room_count=2, entry_side="west")
    issues = validate_floorplan(fp)
    assert issues == [], issues
    assert fp.source == "solver"
    # входная дверь обязана присутствовать
    assert any(d.kind == "entry" for d in fp.doors)


def test_solver_every_room_has_a_door():
    fp = generate_floorplan(width=8.5, depth=10.24, room_count=3, entry_side="east")
    connected = {d.room_a for d in fp.doors} | {d.room_b for d in fp.doors}
    for i in range(len(fp.rooms)):
        assert i in connected, f"комната {fp.rooms[i].type} без двери"


def test_validator_flags_too_narrow_apartment():
    """Слишком узкая квартира для 2 комнат должна ловиться нормо-проверкой."""
    fp = generate_floorplan(width=5.0, depth=10.24, room_count=2, entry_side="west")
    issues = validate_floorplan(fp)
    assert any(i["severity"] == "error" for i in issues)


def test_validator_catches_room_overlap():
    from src.floorplan.ir import ApartmentFloorplan, RoomBox

    fp = ApartmentFloorplan(
        width=8.0, depth=8.0, entry_side="west",
        rooms=[
            RoomBox("living", 0, 0, 5, 8, name="Гостиная"),
            RoomBox("bedroom", 3, 0, 8, 8, name="Спальня"),  # пересекается с гостиной
        ],
        doors=[],
    )
    issues = validate_floorplan(fp)
    assert any("пересекается" in i["message"] for i in issues)


def test_floorplan_to_ifc_bridge_produces_valid_geometry(tmp_path):
    ifc = ifcopenshell.file(schema="IFC4")
    g = lambda: _g(ifc)
    ifc.create_entity("IfcProject", g(), None, "Test")
    wcs = ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0))
    ctx = ifc.create_entity("IfcGeometricRepresentationContext", "Model", "Model", 3, 1e-5, wcs, None)

    fp = generate_floorplan(width=8.5, depth=10.24, room_count=2, entry_side="west")
    elems = floorplan_to_ifc(ifc, ctx, fp, ox=0.0, oy=0.0, wz=0.0, floor_height=3.0)
    assert len(elems) > 0

    out = tmp_path / "bridge_test.ifc"
    ifc.write(str(out))
    reopened = ifcopenshell.open(str(out))

    assert len(reopened.by_type("IfcSpace")) == len(fp.rooms)
    assert len(reopened.by_type("IfcDoor")) > 0

    # каждый проём обязан быть привязан к стене (иначе integrity_checker его поймает)
    voided = {r.RelatedOpeningElement.id() for r in reopened.by_type("IfcRelVoidsElement")}
    openings = reopened.by_type("IfcOpeningElement")
    assert all(op.id() in voided for op in openings)


def test_neural_generator_falls_back_gracefully_when_lm_studio_unreachable(monkeypatch):
    """Не полагаемся на то, запущена ли LM Studio на машине с тестами — принудительно
    имитируем недоступность и проверяем, что функция возвращает None без исключений
    (вызывающий код обязан откатиться на generate_floorplan)."""
    import requests

    def fail_connect(*args, **kwargs):
        raise requests.exceptions.ConnectionError("mocked: LM Studio unreachable")

    monkeypatch.setattr(requests, "post", fail_connect)
    result = generate_floorplan_llm(width=8.5, depth=10.24, room_count=2, entry_side="west", timeout=3)
    assert result is None


def test_neural_generator_accepts_valid_llm_response(monkeypatch):
    import json as json_module

    import requests

    depth = 10.24
    good_rooms = {
        "rooms": [
            {"type": "hallway", "x0": 0, "y0": 0, "x1": 1.5, "y1": depth},
            {"type": "bathroom", "x0": 1.5, "y0": 0, "x1": 3.7, "y1": 1.5},
            {"type": "living", "x0": 1.5, "y0": 1.5, "x1": 4.1, "y1": depth},
            {"type": "bedroom", "x0": 4.1, "y0": 1.5, "x1": 6.7, "y1": depth},
            {"type": "kitchen", "x0": 6.7, "y0": 1.5, "x1": 8.5, "y1": depth},
        ]
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json_module.dumps(good_rooms)}}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse())
    fp = generate_floorplan_llm(width=8.5, depth=depth, room_count=2, entry_side="west")

    assert fp is not None
    assert fp.source == "neural"
    assert validate_floorplan(fp) == []
