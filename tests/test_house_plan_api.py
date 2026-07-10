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


def _mock_llm_json_sequence(monkeypatch, payloads: list[dict]):
    """Каждый следующий вызов requests.post возвращает следующий payload —
    имитирует репэйр-цикл (LLM меняет ответ между попытками)."""
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(self._payload, ensure_ascii=False)}}]}

    calls = {"n": 0}

    def fake_post(*a, **k):
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return FakeResponse(payloads[idx])

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


_HOUSE_PROGRAM = {
    "project_name": "Дом Тест", "style": "modern", "site": {"width_m": 20, "depth_m": 25},
    "footprint": {"width_m": 10, "depth_m": 8}, "storeys": 2, "ceiling_height_m": 3.0,
    "rooms": [
        {"id": "living_01", "name": "Гостиная", "storey": 0, "area_m2": 30, "type": "IfcSpace:LIVING", "exterior_windows": True, "min_width_m": 3.5},
        {"id": "kitchen_01", "name": "Кухня", "storey": 0, "area_m2": 15, "type": "IfcSpace:KITCHEN", "exterior_windows": True, "min_width_m": 2.5},
        {"id": "bed_01", "name": "Спальня 1", "storey": 1, "area_m2": 16, "type": "IfcSpace:BEDROOM", "exterior_windows": True, "min_width_m": 3.0},
        # Детская: явно занижен min_width_m (ниже нормы КМК на спальню, 2.5 м) —
        # даже с солвером, теперь уважающим min_width_m, эта комната должна
        # остаться некомплаентной, чтобы тест продолжал проверять, что
        # реальные нарушения норм всё ещё долетают до конца пайплайна.
        {"id": "bed_02", "name": "Детская", "storey": 1, "area_m2": 1, "type": "IfcSpace:CHILD", "exterior_windows": True, "min_width_m": 1.0},
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
    # Детская: min_width_m занижен до 1.0 (норма для спальни — 2.5 м)
    assert any("Детская" in i["element_name"] for i in d["norms_issues"])
    assert d["raw_program"]["building_program"]["project_name"] == "Дом Тест"


def test_house_plan_parses_reasoning_model_think_block(monkeypatch):
    """Reasoning-модели (qwen3) выдают <think>…</think> перед JSON, иногда с
    фигурными скобками внутри. План должен собираться, а не падать в «Ошибка
    генерации плана»."""
    import requests as _rq

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            payload = json.dumps(_HOUSE_PROGRAM, ensure_ascii=False)
            content = ("<think>Прикинем состав: {спальни, кухня}. Площадь ~70 м².\n"
                       "Сделаю двухэтажный.</think>\n" + payload)
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(_rq, "post", lambda *a, **k: FakeResponse())
    r = client.post("/api/house/plan", json={
        "building_kind": "house", "description": "двухэтажный дом", "check_norms": False,
    })
    assert r.status_code == 200
    assert r.json()["raw_program"]["building_program"]["project_name"] == "Дом Тест"


def test_house_plan_truncated_response_gives_clear_error(monkeypatch):
    """Ответ оборвался по лимиту токенов (finish_reason=length, JSON неполный) →
    внятная 422 про обрыв, а не безликая ошибка."""
    import requests as _rq

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            # неполный JSON + finish_reason=length
            return {"choices": [{"finish_reason": "length",
                                 "message": {"content": '{"project_name": "Дом", "rooms": [{"id": "a"'}}]}

    monkeypatch.setattr(_rq, "post", lambda *a, **k: FakeResponse())
    r = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": False})
    assert r.status_code == 422
    assert "лимит" in r.json()["detail"].lower() or "оборвал" in r.json()["detail"].lower()


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
    # инженерный предел — это осмысленная причина (ValueError) → 422 с текстом,
    # а не безликая 500; главное, что запрос не проглатывается тихо.
    assert r.status_code == 422
    assert "помещени" in r.json()["detail"].lower()


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


def test_house_plan_repair_loop_retries_llm_on_norm_violations(monkeypatch):
    """Раньше единственным способом починить план было нажать
    «Перегенерировать» вслепую. Теперь при ошибках норм эндпоинт сам просит
    LLM переработать BuildingProgram перед тем, как отдать план пользователю."""
    bad_program = {
        "project_name": "Дом", "storeys": 1, "footprint": {"width_m": 10, "depth_m": 8},
        "rooms": [
            {"id": "living_01", "name": "Гостиная", "storey": 0, "area_m2": 30, "type": "IfcSpace:LIVING", "min_width_m": 3.5},
            {"id": "hall_01", "name": "Прихожая", "storey": 0, "area_m2": 1, "type": "IfcSpace:HALLWAY", "min_width_m": 0.5},
        ],
    }
    good_program = {
        "project_name": "Дом", "storeys": 1, "footprint": {"width_m": 10, "depth_m": 8},
        "rooms": [
            {"id": "living_01", "name": "Гостиная", "storey": 0, "area_m2": 30, "type": "IfcSpace:LIVING", "min_width_m": 3.5},
            {"id": "hall_01", "name": "Прихожая", "storey": 0, "area_m2": 8, "type": "IfcSpace:HALLWAY", "min_width_m": 1.5},
        ],
    }
    calls = _mock_llm_json_sequence(monkeypatch, [bad_program, good_program])

    r = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": True})
    assert r.status_code == 200
    d = r.json()
    assert calls["n"] == 2
    assert d["norms_issues"] == []


def test_house_plan_repair_loop_keeps_best_attempt_when_still_failing(monkeypatch):
    """Если и повторная попытка не проходит нормы — отдаём тот вариант,
    где ошибок меньше, а не просто последний по счёту."""
    # Крошечный footprint 5×3 — площади/ширины комнат ниже норм КМК при любой
    # раскладке (grid-fallback их не спасает), поэтому обе попытки заведомо
    # проваливают проверку, но "worse" (3 комнаты) даёт больше ошибок, чем
    # "better" (2 комнаты) → должен выбраться вариант с меньшим числом ошибок.
    worse = {
        "project_name": "Дом", "storeys": 1, "footprint": {"width_m": 5, "depth_m": 3},
        "rooms": [
            {"id": "a", "name": "А", "storey": 0, "area_m2": 1, "type": "IfcSpace:LIVING", "min_width_m": 2.0},
            {"id": "b", "name": "Б", "storey": 0, "area_m2": 1, "type": "IfcSpace:BEDROOM", "min_width_m": 2.0},
            {"id": "c", "name": "В", "storey": 0, "area_m2": 1, "type": "IfcSpace:BEDROOM", "min_width_m": 2.0},
        ],
    }
    better = {
        "project_name": "Дом", "storeys": 1, "footprint": {"width_m": 5, "depth_m": 3},
        "rooms": [
            {"id": "a", "name": "А", "storey": 0, "area_m2": 1, "type": "IfcSpace:LIVING", "min_width_m": 2.0},
            {"id": "b", "name": "Б", "storey": 0, "area_m2": 1, "type": "IfcSpace:BEDROOM", "min_width_m": 2.0},
        ],
    }
    calls = _mock_llm_json_sequence(monkeypatch, [worse, better])

    r = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": True})
    assert r.status_code == 200
    d = r.json()
    assert calls["n"] == 2
    # обе попытки провалены, но best-of-N должен вернуть "better" (меньше
    # ошибок) — проверяем по его отличительному признаку: 2 комнаты, не 3.
    kept_rooms = d["raw_program"]["building_program"]["rooms"]
    assert len(kept_rooms) == 2
    errors = [i for i in d["norms_issues"] if i["severity"] == "error"]
    assert 0 < len(errors) < 6  # ошибки "better" (их меньше, чем 6 у "worse")


def test_house_plans_list_endpoint_and_route_ordering(monkeypatch, cleanup_house_plans):
    """GET /api/house/plans (литеральный путь) должен резолвиться раньше
    GET /api/house/{plan_id} (int) — иначе FastAPI попытался бы распарсить
    'plans' как plan_id и вернул 422 вместо списка."""
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": False})
    d = gen.json()
    save = client.post("/api/house/save", json={
        "building_kind": "house", "name": "Список-тест", "description": "дом",
        "program": d["raw_program"], "floorplan": d["raw_floorplan"],
        "norms_issues": [], "norms_citations": "",
    })
    plan_id = save.json()["plan_id"]
    cleanup_house_plans.append(plan_id)

    r = client.get("/api/house/plans")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["plans"]]
    assert "Список-тест" in names


def test_house_plan_get_rerenders_floors_with_svg(monkeypatch, cleanup_house_plans):
    """GET /api/house/{id} должен вернуть тот же формат, что и
    /api/house/plan (floors с SVG), а не только сырые program/floorplan —
    иначе фронт не сможет напрямую подставить это в состояние мастера при
    открытии сохранённого проекта."""
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": True})
    d = gen.json()
    save = client.post("/api/house/save", json={
        "building_kind": "house", "name": "Реrender-тест", "description": "дом",
        "program": d["raw_program"], "floorplan": d["raw_floorplan"],
        "norms_issues": d["norms_issues"], "norms_citations": d["norms_citations"],
    })
    plan_id = save.json()["plan_id"]
    cleanup_house_plans.append(plan_id)

    got = client.get(f"/api/house/{plan_id}")
    assert got.status_code == 200
    body = got.json()
    assert len(body["floors"]) == len(d["floors"])
    for f in body["floors"]:
        assert "<svg" in f["svg"]
    assert body["raw_program"] == d["raw_program"]


def test_house_rerender_reflects_edited_geometry(monkeypatch):
    """POST /api/house/rerender (кнопка «Редактировать») принимает
    отредактированную вручную геометрию, перерисовывает SVG и перепроверяет
    нормы. Двигаем вершину комнаты и проверяем, что ответ считает площади/
    нормы по НОВОЙ геометрии, а не по исходной."""
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": True})
    d = gen.json()

    edited = json.loads(json.dumps(d["raw_floorplan"]))  # deep copy
    # раздвигаем первый этаж по X: все вершины/концы стен с максимальным x → +4 м
    st = edited["storeys"][0]
    xs = [p[0] for r in st["rooms"] for p in r["polygon"]]
    maxx = max(xs)
    for r in st["rooms"]:
        for p in r["polygon"]:
            if abs(p[0] - maxx) < 1e-6:
                p[0] = maxx + 4.0
    for w in st["walls"]:
        for e in w["axis"]:
            if abs(e[0] - maxx) < 1e-6:
                e[0] = maxx + 4.0

    r = client.post("/api/house/rerender", json={
        "building_kind": "house", "check_norms": True,
        "program": d["raw_program"], "floorplan": edited,
    })
    assert r.status_code == 200
    body = r.json()
    assert "<svg" in body["floors"][0]["svg"]
    # площадь первого этажа выросла после раздвижки
    assert body["floors"][0]["area_m2"] > d["floors"][0]["area_m2"]
    # геометрия в ответе — именно отредактированная
    rt = body["raw_floorplan"]["storeys"][0]["rooms"]
    assert max(p[0] for r_ in rt for p in r_["polygon"]) > maxx + 3.9


def test_house_dxf_export(monkeypatch):
    """POST /api/house/dxf отдаёт валидный DXF (кнопка «Скачать DXF»)."""
    import io
    import ezdxf
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "house", "description": "дом", "check_norms": False})
    d = gen.json()

    r = client.post("/api/house/dxf", json={
        "building_kind": "house", "program": d["raw_program"], "floorplan": d["raw_floorplan"],
    })
    assert r.status_code == 200
    assert r.content[:2] in (b"AC", b"  ") or b"SECTION" in r.content[:2000]  # DXF-заголовок
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8", "replace")))
    kinds = {e.dxftype() for e in doc.modelspace()}
    assert "DIMENSION" in kinds and "LWPOLYLINE" in kinds


def test_house_dxf_rejects_broken_geometry():
    r = client.post("/api/house/dxf", json={"building_kind": "house", "program": {}, "floorplan": {"storeys": []}})
    assert r.status_code == 422


def test_house_rerender_rejects_broken_geometry():
    # program без ключа building_program → KeyError в эндпоинте → 422,
    # а не 500 (кривой ввод от фронта — это ошибка запроса, не сервера).
    r = client.post("/api/house/rerender", json={
        "building_kind": "house", "check_norms": False,
        "program": {}, "floorplan": {"storeys": []},
    })
    assert r.status_code == 422


def test_apartment_plan_get_rerenders_floors_with_svg(monkeypatch, cleanup_house_plans):
    _mock_llm_json(monkeypatch, _APARTMENT_PROGRAM)
    gen = client.post("/api/house/plan", json={"building_kind": "apartment", "description": "жк", "check_norms": True})
    d = gen.json()
    save = client.post("/api/house/save", json={
        "building_kind": "apartment", "name": "Апарт-render-тест", "description": "жк",
        "program": d["raw_program"], "floorplan": d["raw_floorplan"],
        "norms_issues": d["norms_issues"], "norms_citations": d["norms_citations"],
    })
    plan_id = save.json()["plan_id"]
    cleanup_house_plans.append(plan_id)

    got = client.get(f"/api/house/{plan_id}")
    assert got.status_code == 200
    body = got.json()
    assert len(body["floors"]) == 2
    for f in body["floors"]:
        assert "<svg" in f["svg"]


def test_house_plan_labels_start_from_first_floor(monkeypatch):
    """Ярлык этажа нумеруется с 1 (level 0 → «1-й этаж»): этаж 0 в быту =
    подвал, поэтому «Этаж 0» вводил в заблуждение."""
    _mock_llm_json(monkeypatch, _HOUSE_PROGRAM)
    r = client.post("/api/house/plan", json={
        "building_kind": "house", "description": "двухэтажный дом", "check_norms": False,
    })
    assert r.status_code == 200
    labels = [f["label"] for f in r.json()["floors"]]
    assert labels == ["1-й этаж", "2-й этаж"]
    assert not any("Этаж 0" in l for l in labels)


def test_house_fix_norms_reduces_violations_without_llm(monkeypatch):
    """/api/house/fix-norms поднимает площади/габариты и перегенерирует план
    БЕЗ обращения к LLM (reuse готового BuildingProgram). Нарушений должно
    стать меньше, чем в исходном тесном плане."""
    # LLM специально роняем — эндпоинт не должен его звать.
    import requests as _rq

    def boom(*a, **k):
        raise AssertionError("fix-norms не должен вызывать LLM")
    monkeypatch.setattr(_rq, "post", boom)

    tight_program = {"building_program": {
        "project_name": "Тесный", "storeys": 1, "footprint": {"width_m": 6.0, "depth_m": 6.0},
        "ceiling_height_m": 3.0,
        "rooms": [
            {"id": "liv", "name": "Гостиная", "storey": 0, "area_m2": 23, "type": "IfcSpace:LIVING"},
            {"id": "kit", "name": "Кухня", "storey": 0, "area_m2": 2.8, "type": "IfcSpace:KITCHEN"},
            {"id": "hall", "name": "Прихожая", "storey": 0, "area_m2": 0.7, "type": "IfcSpace:HALLWAY"},
            {"id": "bath", "name": "Санузел", "storey": 0, "area_m2": 1.1, "type": "IfcSpace:BATHROOM"},
        ],
    }}

    # число нарушений исходного плана
    base = client.post("/api/house/plan", json={
        "building_kind": "house", "description": "тесный дом", "check_norms": True,
        "program": tight_program,
    })
    assert base.status_code == 200
    before = len([i for i in base.json()["norms_issues"] if i["severity"] == "error"])

    r = client.post("/api/house/fix-norms", json={"program": tight_program, "check_norms": True})
    assert r.status_code == 200
    d = r.json()
    after = len([i for i in d["norms_issues"] if i["severity"] == "error"])
    assert after < before
    # footprint увеличен относительно тесного 6×6
    fp = d["raw_program"]["building_program"]["footprint"]
    assert fp["width_m"] >= 6.0 and fp["depth_m"] >= 6.0
    # площади комнат подняты к минимумам (кухня ≥ 8 м²)
    kit = next(rm for rm in d["raw_program"]["building_program"]["rooms"] if rm["id"] == "kit")
    assert kit["area_m2"] >= 8.0
