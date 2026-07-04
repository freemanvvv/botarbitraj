"""Тесты конвертера RPLAN → mosaic-шаблоны (src/bim_agents/rplan_convert.py).

RPLAN распространяется по заявке, самих данных в репозитории нет, поэтому
ядро алгоритма проверяется на СИНТЕТИЧЕСКИХ массивах, структурно идентичных
реальным RPLAN-каналам (попиксельный класс + экземпляр комнаты). Формат
такой же, как отдаёт load_rplan_png для настоящего PNG."""
import numpy as np
import pytest

from types import SimpleNamespace

from src.bim_agents.rplan_convert import (
    image_to_template, convert_dir, _signature,
    graph2plan_plan_to_template,
    RPLAN_FRONT_DOOR,
)
from src.bim_agents.layout_templates import load_templates


def _blank(h=64, w=64):
    # 13 = External (фон/не-комната) — как в RPLAN
    category = np.full((h, w), 13, dtype=np.int32)
    instance = np.zeros((h, w), dtype=np.int32)
    return category, instance


def _put(category, instance, cls, inst_id, x0, y0, x1, y1):
    category[y0:y1, x0:x1] = cls
    instance[y0:y1, x0:x1] = inst_id


def _clean_two_room_plan():
    """Две комнаты (гостиная слева | кухня справа) — идеальная мозаика 1×2.
    FrontDoor снизу → вход должен оказаться при y=0 после ориентации."""
    category, instance = _blank()
    _put(category, instance, 0, 1, 4, 4, 32, 60)   # LivingRoom -> living
    _put(category, instance, 2, 2, 32, 4, 60, 60)  # Kitchen -> kitchen
    # входная дверь у нижней грани (y большой)
    category[58:60, 16:20] = RPLAN_FRONT_DOOR
    return category, instance


def test_clean_mosaic_is_accepted_and_matches_seed_invariants():
    category, instance = _clean_two_room_plan()
    tpl = image_to_template(category, instance, source_id="synthA", min_iou=0.7)
    assert tpl is not None
    assert tpl["id"] == "rplan_synthA"
    assert sorted(r["category"] for r in tpl["rooms"]) == ["kitchen", "living"]

    # Те же структурные инварианты, что у рукописного датасета
    # (см. test_house_layout.test_all_templates_tile_unit_square_without_overlap).
    xs, ys = tpl["x_cuts"], tpl["y_cuts"]
    assert xs[0] == 0.0 and xs[-1] == 1.0 and xs == sorted(xs)
    assert ys[0] == 0.0 and ys[-1] == 1.0 and ys == sorted(ys)
    total = 0.0
    cells = []
    for r in tpl["rooms"]:
        rx0, rx1 = xs[r["cx0"]], xs[r["cx1"]]
        ry0, ry1 = ys[r["cy0"]], ys[r["cy1"]]
        assert rx1 > rx0 and ry1 > ry0
        total += (rx1 - rx0) * (ry1 - ry0)
        cells.append((rx0, ry0, rx1, ry1))
    assert abs(total - 1.0) < 1e-6
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            a, b = cells[i], cells[j]
            ow = min(a[2], b[2]) - max(a[0], b[0])
            oh = min(a[3], b[3]) - max(a[1], b[1])
            assert ow <= 1e-9 or oh <= 1e-9


def test_front_door_orients_entry_to_y0():
    category, instance = _clean_two_room_plan()  # дверь у нижней грани
    tpl = image_to_template(category, instance, source_id="orient", min_iou=0.7)
    assert tpl is not None
    # после ориентации вход (нижняя грань) уходит к y=0: план повёрнут на
    # 180°, значит категории слева/справа по x меняются местами, но состав
    # и мозаичность сохраняются — проверяем, что комнаты примыкают к y=0.
    assert any(r["cy0"] == 0 for r in tpl["rooms"])


def test_front_door_from_boundary_channel_orients_entry():
    """Каноническая RPLAN-разметка кладёт переднюю дверь в канал boundary
    (=255), а не классом 15 в category (см. rplanpy). Конвертер должен
    ориентировать вход по boundary, когда он передан."""
    category, instance = _blank()
    # living сверху, kitchen снизу; front door в boundary у ВЕРХНЕЙ грани
    _put(category, instance, 0, 1, 4, 4, 60, 32)   # living, верх
    _put(category, instance, 2, 2, 4, 32, 60, 60)  # kitchen, низ
    boundary = np.zeros_like(category)
    boundary[4:6, 20:24] = 255  # вход у верхней грани → останется при y=0
    tpl = image_to_template(category, instance, source_id="bnd", boundary=boundary, min_iou=0.7)
    assert tpl is not None
    # вход уже сверху → без поворота: living должна остаться примыкающей к y=0
    living = [r for r in tpl["rooms"] if r["category"] == "living"][0]
    assert living["cy0"] == 0


def test_rplan_categories_map_to_our_vocabulary():
    category, instance = _blank()
    _put(category, instance, 1, 1, 4, 4, 32, 60)   # MasterRoom -> bedroom
    _put(category, instance, 3, 2, 32, 4, 60, 60)  # Bathroom -> wet
    tpl = image_to_template(category, instance, source_id="cats", min_iou=0.7)
    assert tpl is not None
    assert sorted(r["category"] for r in tpl["rooms"]) == ["bedroom", "wet"]
    # только спальня+санузел, без кухни/гостиной → верхний этаж
    assert tpl["storey_role"] == "upper"


def test_balcony_and_walls_are_dropped():
    category, instance = _blank()
    _put(category, instance, 0, 1, 4, 4, 40, 60)    # living
    _put(category, instance, 2, 2, 40, 4, 60, 60)   # kitchen
    _put(category, instance, 9, 3, 4, 60, 40, 64)   # Balcony -> должен выпасть
    tpl = image_to_template(category, instance, source_id="balc", min_iou=0.6)
    assert tpl is not None
    assert all(r["category"] in ("living", "kitchen") for r in tpl["rooms"])


def test_L_shaped_room_is_accepted_via_pixel_mask_ownership():
    """Г-образная комната на PNG-пути (маска instance — точная форма, без
    неоднозначности) теперь ПРИНИМАЕТСЯ, а не отклоняется: владение ячейками
    решается голосованием пикселей внутри каждой ячейки, поэтому комната
    может занимать несколько несмежных-по-прямоугольнику ячеек. Это и есть
    поддержка непрямоугольных форм — она возможна только для маскового
    (PNG) пути; box-путь (Graph2Plan) такой неоднозначности разрешить не
    может (см. _build_mosaic) и по-прежнему требует одного прямоугольника."""
    category, instance = _blank()
    # L-образная гостиная (инстанс 1) огибает угол
    _put(category, instance, 0, 1, 4, 4, 60, 34)    # верхняя широкая полоса
    _put(category, instance, 0, 1, 4, 34, 34, 60)   # + левая ножка вниз
    _put(category, instance, 2, 2, 34, 34, 60, 60)  # кухня в правом-нижнем углу
    tpl = image_to_template(category, instance, source_id="Lshape", min_iou=0.7)
    assert tpl is not None
    living = next(r for r in tpl["rooms"] if r["category"] == "living")
    kitchen = next(r for r in tpl["rooms"] if r["category"] == "kitchen")
    assert "cells" in living and len(living["cells"]) >= 2   # Г-форма — несколько прямоугольников
    assert "cx0" in kitchen and "cells" not in kitchen        # кухня осталась простым прямоугольником

    # мозаика по-прежнему без наложений и щелей: суммарная площадь ячеек = 1.0
    xs, ys = tpl["x_cuts"], tpl["y_cuts"]
    total = 0.0
    for r in tpl["rooms"]:
        rects = r["cells"] if "cells" in r else [[r["cx0"], r["cx1"], r["cy0"], r["cy1"]]]
        for cx0, cx1, cy0, cy1 in rects:
            total += (xs[cx1] - xs[cx0]) * (ys[cy1] - ys[cy0])
    assert abs(total - 1.0) < 1e-6


def test_gap_in_layout_is_rejected():
    """Явно незаполненная ячейка (крупнее допуска снапа) → щель → отказ.
    Левая комната во всю высоту + кухня только в правом-верхнем углу;
    правый-нижний угол пуст (y 30..60) — это настоящая дыра, а не зазор
    под стену."""
    category, instance = _blank()
    _put(category, instance, 0, 1, 4, 4, 30, 60)    # living, левая колонка во всю высоту
    _put(category, instance, 2, 2, 34, 4, 60, 30)   # kitchen, правый верх
    # правый низ (x 34..60, y 34..60) — пусто
    tpl = image_to_template(category, instance, source_id="gap", min_iou=0.7)
    assert tpl is None


def test_single_room_returns_none():
    category, instance = _blank()
    _put(category, instance, 0, 1, 4, 4, 60, 60)
    assert image_to_template(category, instance, source_id="one") is None


def test_convert_dir_dedups_and_reports(tmp_path):
    from PIL import Image

    def save_rplan(path, category, instance):
        h, w = category.shape
        img = np.zeros((h, w, 4), dtype=np.uint8)
        img[..., 1] = category.astype(np.uint8)   # канал category = 1
        img[..., 2] = instance.astype(np.uint8)   # канал instance = 2
        Image.fromarray(img, mode="RGBA").save(path)

    a1, i1 = _clean_two_room_plan()
    a2, i2 = _clean_two_room_plan()  # тот же план → дубль
    save_rplan(tmp_path / "p1.png", a1, i1)
    save_rplan(tmp_path / "p2.png", a2, i2)

    templates, stats = convert_dir(str(tmp_path), min_iou=0.7)
    assert stats["total"] == 2
    assert stats["accepted"] == 1     # второй отсеян дедупом
    assert stats["duplicate"] == 1
    assert len(templates) == 1
    assert _signature(templates[0])  # сигнатура вычисляется без ошибок


def test_converter_output_is_consumable_by_matcher(monkeypatch):
    """Сгенерированный шаблон должен грузиться и подбираться штатным
    matcher'ом наравне с рукописными — т.е. быть 100% совместимым по схеме."""
    from src.bim_agents import layout_templates

    category, instance = _clean_two_room_plan()
    tpl = image_to_template(category, instance, source_id="consume", min_iou=0.7)
    assert tpl is not None

    fake = {"templates": load_templates() + [tpl]}
    monkeypatch.setattr(layout_templates, "load_templates", lambda: fake["templates"])
    got = layout_templates.match_template(["living", "kitchen"], aspect=tpl["aspect"], level=0)
    assert got is not None
    assert sorted(r["category"] for r in got["rooms"]) == ["kitchen", "living"]


# ───────────────────────── Graph2Plan .mat (боксы) ──────────────────────────
# Реальный .mat лежит только у пользователя (Kaggle-зеркало), поэтому struct-
# элемент имитируем SimpleNamespace с теми же полями (rType/gtBoxNew/boundary/
# name), что отдаёт scipy.io.loadmat(struct_as_record=False).

def _g2p_plan(rtypes, boxes, door_row=None, name="p1"):
    boundary = np.array([[10, 10, 0, 0], [20, 10, 0, 0]], dtype=np.int32)
    if door_row is not None:
        boundary = np.array(door_row, dtype=np.int32)
    return SimpleNamespace(
        rType=np.array(rtypes, dtype=np.int32),
        gtBoxNew=np.array(boxes, dtype=np.int32),
        boundary=boundary,
        name=name,
    )


def test_graph2plan_boxes_clean_mosaic_accepted():
    # living [0,0,50,100] | kitchen [50,0,100,100] — мозаика 1×2
    plan = _g2p_plan([0, 2], [[0, 0, 50, 100], [50, 0, 100, 100]],
                     door_row=[[10, 100, 0, 1], [30, 100, 0, 1]])  # дверь у y=100 (низ)
    tpl = graph2plan_plan_to_template(plan, "g1", min_iou=0.7)
    assert tpl is not None
    assert tpl["_source"] == "graph2plan"
    assert tpl["id"] == "graph2plan_g1"
    assert sorted(r["category"] for r in tpl["rooms"]) == ["kitchen", "living"]
    # инварианты мозаики (как у сид-датасета)
    xs, ys = tpl["x_cuts"], tpl["y_cuts"]
    assert xs[0] == 0.0 and xs[-1] == 1.0 and ys[0] == 0.0 and ys[-1] == 1.0
    total = sum((xs[r["cx1"]] - xs[r["cx0"]]) * (ys[r["cy1"]] - ys[r["cy0"]]) for r in tpl["rooms"])
    assert abs(total - 1.0) < 1e-6


def test_graph2plan_rtype_maps_and_drops_balcony():
    # living, balcony(9→drop), kitchen, bathroom → 3 комнаты, без балкона,
    # но балкон оставил бы щель → план отклоняется (это ок и ожидаемо).
    plan = _g2p_plan([0, 9, 2], [[0, 0, 50, 100], [50, 90, 100, 100], [50, 0, 100, 90]])
    tpl = graph2plan_plan_to_template(plan, "g2", min_iou=0.7)
    # балкон выброшен → в правой колонке щель y=90..100 → отказ
    assert tpl is None


def test_graph2plan_balcony_free_three_room_accepted():
    # чистая мозаика без балкона: living | (kitchen над wet)
    plan = _g2p_plan([0, 2, 3],
                     [[0, 0, 50, 100], [50, 0, 100, 50], [50, 50, 100, 100]])
    tpl = graph2plan_plan_to_template(plan, "g3", min_iou=0.7)
    assert tpl is not None
    assert sorted(r["category"] for r in tpl["rooms"]) == ["kitchen", "living", "wet"]
    assert tpl["storey_role"] == "ground"  # есть кухня/гостиная, спален нет


def test_graph2plan_front_door_orients_entry_to_y0():
    # дверь у нижней грани (y=100) → после ориентации комнаты примыкают к y=0
    plan = _g2p_plan([0, 2], [[0, 0, 50, 100], [50, 0, 100, 100]],
                     door_row=[[10, 100, 0, 1], [30, 100, 0, 1]])
    tpl = graph2plan_plan_to_template(plan, "g4", min_iou=0.7)
    assert tpl is not None
    assert any(r["cy0"] == 0 for r in tpl["rooms"])


def test_graph2plan_output_consumable_by_matcher(monkeypatch):
    from src.bim_agents import layout_templates
    plan = _g2p_plan([0, 2, 3],
                     [[0, 0, 50, 100], [50, 0, 100, 50], [50, 50, 100, 100]])
    tpl = graph2plan_plan_to_template(plan, "g5", min_iou=0.7)
    assert tpl is not None
    monkeypatch.setattr(layout_templates, "load_templates", lambda: load_templates() + [tpl])
    got = layout_templates.match_template(["living", "kitchen", "wet"], aspect=tpl["aspect"], level=0)
    assert got is not None


def test_cli_append_writes_back_to_target_file(tmp_path):
    """Регрессия: --append должен дописывать В САМ файл датасета (in place),
    а не в --out. Раньше объединённый результат уходил в --out, а
    house_templates.json оставался нетронутым (симптом «ИТОГО: 6»)."""
    import json
    import scipy.io
    from src.bim_agents.rplan_convert import _main

    def plan(rt, bx, nm):
        return {"rType": np.array(rt, np.int32), "gtBoxNew": np.array(bx, np.int32),
                "boundary": np.array([[10, 100, 0, 1], [30, 100, 0, 1]], np.int32), "name": nm}
    mat = tmp_path / "g.mat"
    scipy.io.savemat(str(mat), {"data": np.array([
        plan([0, 2], [[0, 0, 50, 100], [50, 0, 100, 100]], "a"),
        plan([0, 2, 3], [[0, 0, 50, 100], [50, 0, 100, 50], [50, 50, 100, 100]], "b"),
    ], dtype=object)})

    target = tmp_path / "house_templates.json"
    target.write_text(json.dumps({"templates": [
        {"id": f"seed{i}", "rooms": [], "x_cuts": [0, 1], "y_cuts": [0, 1]} for i in range(6)
    ]}), encoding="utf-8")

    _main([str(mat), "--graph2plan", "--append", str(target), "--min-iou", "0.7"])

    after = json.loads(target.read_text(encoding="utf-8"))["templates"]
    assert len(after) == 8   # 6 сидов + 2 новых, записано В ЦЕЛЕВОЙ файл


# ─────────────── Graph2Plan polygon-путь (rBoundary, реальные формы) ─────────

def _g2p_poly_plan(rtypes, polys, name="p1", door=None):
    boundary = np.array(door if door is not None else [[10, 10, 0, 0], [20, 10, 0, 0]], dtype=np.int32)
    rb = np.empty(len(polys), dtype=object)
    for i, p in enumerate(polys):
        rb[i] = np.array(p, dtype=np.int32)
    return SimpleNamespace(rType=np.array(rtypes, dtype=np.int32), rBoundary=rb,
                           boundary=boundary, name=name)


def test_graph2plan_polygon_path_extracts_L_shape():
    from src.bim_agents.rplan_convert import graph2plan_plan_to_polygon_template
    # Г-образная гостиная (6 вершин) + прямоугольная кухня в вырезе
    L = [[0, 0], [60, 0], [60, 40], [100, 40], [100, 100], [0, 100]]
    K = [[60, 0], [100, 0], [100, 40], [60, 40]]
    plan = _g2p_poly_plan([0, 2], [L, K], "L1")
    tpl = graph2plan_plan_to_polygon_template(plan, "L1")
    assert tpl is not None
    assert tpl["_source"] == "graph2plan_poly"
    living = next(r for r in tpl["rooms"] if r["category"] == "living")
    assert "polygon" in living and len(living["polygon"]) == 6   # Г-форма сохранена
    # контур нормирован в 0..1
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in living["polygon"])


def test_polygon_template_survives_full_pipeline(monkeypatch):
    """polygon-шаблон Г-формы: match → apply (масштаб) → стены → IFC."""
    from src.bim_agents.rplan_convert import graph2plan_plan_to_polygon_template
    from src.bim_agents import layout_templates
    from src.bim_agents.contracts import BuildingProgram, Room
    from src.bim_agents.floorplan_agent import generate_floor_plan
    from src.bim_agents.bim_agent import generate_ifc

    L = [[0, 0], [60, 0], [60, 40], [100, 40], [100, 100], [0, 100]]
    K = [[60, 0], [100, 0], [100, 40], [60, 40]]
    tpl = graph2plan_plan_to_polygon_template(_g2p_poly_plan([0, 2], [L, K], "Lp"), "Lp")
    monkeypatch.setattr(layout_templates, "load_templates", lambda: [tpl])

    program = BuildingProgram(
        project_name="Т", storeys=1, footprint={"width_m": 10, "depth_m": 9},
        rooms=[
            Room(id="liv", name="Гостиная", storey=0, area_m2=60, type="IfcSpace:LIVING", min_width_m=3.0),
            Room(id="kit", name="Кухня", storey=0, area_m2=15, type="IfcSpace:KITCHEN", min_width_m=2.5),
        ],
    )
    fp = generate_floor_plan(program)
    liv = next(rp.polygon for rp in fp.storeys[0].rooms if rp.id == "liv")
    assert len(liv) == 6  # реальная Г-форма дошла до плана
    # масштабирована под footprint
    assert abs(max(x for x, _ in liv) - 10.0) < 0.2 and abs(max(y for _, y in liv) - 9.0) < 0.2

    path, stats = generate_ifc(fp, output_dir="/tmp")
    assert stats["spaces"] == 2
    import ifcopenshell, ifcopenshell.geom
    ifc = ifcopenshell.open(path)
    s = ifcopenshell.geom.settings(); s.set("use-world-coords", True)
    for t in ("IfcWall", "IfcSlab", "IfcSpace", "IfcWindow", "IfcDoor"):
        for p in ifc.by_type(t):
            ifcopenshell.geom.create_shape(s, p)


def test_graph2plan_polygon_dedup_and_signature():
    from src.bim_agents.rplan_convert import graph2plan_plan_to_polygon_template, _signature
    L = [[0, 0], [60, 0], [60, 40], [100, 40], [100, 100], [0, 100]]
    K = [[60, 0], [100, 0], [100, 40], [60, 40]]
    t1 = graph2plan_plan_to_polygon_template(_g2p_poly_plan([0, 2], [L, K], "a"), "a")
    t2 = graph2plan_plan_to_polygon_template(_g2p_poly_plan([0, 2], [L, K], "b"), "b")
    assert _signature(t1) == _signature(t2)   # одинаковая форма → один ключ дедупа


def test_cli_help_strings_are_well_formed():
    """Регрессия: help-строка с сырым '%' (было '~1%') ломает argparse —
    Python 3.14 падает уже при add_argument, 3.11 — при форматировании
    --help. Прогоняем --help: он экспандит все help-строки (help % params),
    поэтому ловит битые проценты на любой версии. Ожидаем SystemExit (норма
    для --help), а не ValueError('badly formed help string')."""
    import io
    import contextlib
    from src.bim_agents import rplan_convert
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            rplan_convert._main(["--help"])
        except SystemExit:
            pass  # так и должно быть
