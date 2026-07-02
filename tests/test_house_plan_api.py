"""Тесты нового мастера «Моделирование» (/api/house/*) — частный дом и
многоквартирный дом. LLM мокается (requests.post), реальная LM Studio не
нужна. Использует общую SQLite-базу house_plans.db — тестовые записи
удаляются через cleanup_house_plans; сгенерированные IFC — через cleanup_ifc."""
import json
import os

import requests
from fastapi.testclient import TestClient

from webapp.backend.main import app, OUTPUT_DIR

client = TestClient(app)


def _mock_llm_json(monkeypatch, payload: dict):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse())


_HOUSE_PROGRAM = {
    "project_name": "Дом Тест", "style": "modern", "site": {"width_m": 20, "depth_m": 25},
    "footprint": {"width_m": 10, "depth_m": 8}, "storeys": 2, "ceiling_height_m": 3.0,
    "rooms": [
        {"id": "living_01", "name": "Гостиная", "storey": 0, "area_m2": 30, "type": "IfcSpace:LIVING", "exterior_windows": True},
        {"id": "kitchen_01", "name": "Кухня", "storey": 0, "area_m2": 15, "type": "IfcSpace:KITCHEN", "exterior_windows": True},
        {"id": "bed_01", "name": "Спальня 1", "storey": 1, "area_m2": 16, "type": "IfcSpace:BEDROOM", "exterior_windows": True},
        {"id": "bed_02", "name": "Детская", "storey": 1, "area_m2": 3, "type": "IfcSpace:CHILD", "exterior_windows": True},
    ],
    "adjacency": [],
}

_APARTMENT_PROGRAM = {
    "name": "ЖК Тест", "building_type": "жилой", "summary": "Тест", "norm_study": "Тест норм",
    "stages": [], "building": {"entrances": 1, "apartments_per_landing": 2, "apartment_rooms": 2, "has_elevator": False},
    "plan": {"floor_count": 3, "floor_height_m": 3.0, "wall_thickness_m": 0.38, "slab_thickness_m": 0.2},
    "reasoning": {}, "params": {"length": 17.0, "width": 11.0, "num_floors": 3, "floor_height": 3.0, "wall_thickness": 0.38, "roof_type": "flat"},
}


def test_house_plan_rejects_empty_description():
    # "" не проходит Field(min_length=1) на уровне pydantic (422);
    # непустая, но состоящая из пробелов строка проходит pydantic и
    # ловится уже в самом эндпоинте (.strip(), 400) — проверяем оба пути.
    r = client.post("/api/house/plan", json={"building_kind": "house", "description": ""})
    assert r.status_code == 422

    r2 = client.post("/api/house/plan", json={"building_kind": "house", "description": "   "})
    assert r2.status_code == 400


def test_house_plan_rejects_invalid_building_kind():
    r = client.post("/api/house/plan", json={"building_kind": "castle", "description": "x"})
    assert r.status_code == 422


def test_house_plan_generates_house_floors_and_flags_norm_violations(monkeypatch):
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    r = client.post("/api/house/plan", json={
        "building_kind": "house", "description": "двухэтажный дом", "check_norms": True,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["building_kind"] == "house"
    assert len(d["floors"]) == 2
    levels = sorted(f["level"] for f in d["floors"])
    assert levels == [0, 1]
    for f in d["floors"]:
        assert "<svg" in f["svg"]
    # Детская (3 м²) заведомо тоньше нормы (мин. ширина 2.5 м как у спальни)
    assert any("Детская" in i["element_name"] for i in d["norms_issues"])
    assert d["raw_program"]["building_program"]["project_name"] == "Дом Тест"


def test_house_plan_skips_norms_check_when_disabled(monkeypatch):
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    r = client.post("/api/house/plan", json={
        "building_kind": "house", "description": "дом", "check_norms": False,
    })
    assert r.status_code == 200
    assert r.json()["norms_issues"] == []


def test_house_plan_rejects_too_many_rooms(monkeypatch):
    huge = dict(_HOUSE_PROGRAM)
    huge["rooms"] = [
        {"id": f"r{i}", "name": f"Комната {i}", "storey": 0, "area_m2": 10, "type": "IfcSpace:LIVING"}
        for i in range(61)
    ]
    _mock_llm_json(monkeypatch, huge)
    r = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом"})
    assert r.status_code == 500  # _server_error — не проглатывается тихо


def test_apartment_plan_generates_two_floors(monkeypatch):
    _mock_llm_json(monkeypatch, _APARTMENT_PROGRAM)
    r = client.post("/api/house/plan", json={
        "building_kind": "apartment", "description": "жилой дом 1 подъезд 2 квартиры", "check_norms": True,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["building_kind"] == "apartment"
    assert len(d["floors"]) == 2
    assert d["norms_issues"] == []  # солвер уже строго проверен
    assert "building_params" in d["raw_program"]
    assert "west" in d["raw_floorplan"] and "east" in d["raw_floorplan"]


def test_house_plan_save_get_and_build_3d(monkeypatch, cleanup_house_plans, cleanup_ifc):
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": True})
    d = gen.json()

    save = client.post("/api/house/save", json={
        "building_kind": "house", "name": "Мой дом", "description": "дом",
        "program": d["raw_program"], "floorplan": d["raw_floorplan"],
        "norms_issues": d["norms_issues"], "norms_citations": d["norms_citations"],
    })
    assert save.status_code == 200
    plan_id = save.json()["plan_id"]
    cleanup_house_plans.append(plan_id)

    got = client.get(f"/api/house/{plan_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "Мой дом"
    assert got.json()["building_kind"] == "house"

    r404 = client.get("/api/house/999999999")
    assert r404.status_code == 404

    build = client.post(f"/api/house/{plan_id}/build-3d")
    assert build.status_code == 200
    b = build.json()
    assert b["filename"].endswith(".ifc")
    cleanup_ifc.append(str(OUTPUT_DIR / b["filename"]))
    assert b["stats"]["storeys"] == 2
    # Issues из /api/house/plan (некомплаентная «Детская») должны попасть
    # в итоговую сводку целостности — та же панель, что и для многоквартирных.
    assert not b["integrity"]["ok"]

    r404b = client.post("/api/house/999999999/build-3d")
    assert r404b.status_code == 404


def test_apartment_plan_save_and_build_3d(monkeypatch, cleanup_house_plans, cleanup_ifc):
    _mock_llm_json(monkeypatch, _APARTMENT_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "apartment", "description": "жк", "check_norms": True})
    d = gen.json()

    save = client.post("/api/house/save", json={
        "building_kind": "apartment", "name": "Мой ЖК", "description": "жк",
        "program": d["raw_program"], "floorplan": d["raw_floorplan"],
        "norms_issues": d["norms_issues"], "norms_citations": d["norms_citations"],
    })
    plan_id = save.json()["plan_id"]
    cleanup_house_plans.append(plan_id)

    build = client.post(f"/api/house/{plan_id}/build-3d")
    assert build.status_code == 200
    b = build.json()
    cleanup_ifc.append(str(OUTPUT_DIR / b["filename"]))
    assert b["stats"]["apartments"] == 6
    assert b["integrity"]["ok"]
