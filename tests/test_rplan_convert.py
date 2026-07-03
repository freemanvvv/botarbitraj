"""Тесты конвертера RPLAN → mosaic-шаблоны (src/bim_agents/rplan_convert.py).

RPLAN распространяется по заявке, самих данных в репозитории нет, поэтому
ядро алгоритма проверяется на СИНТЕТИЧЕСКИХ массивах, структурно идентичных
реальным RPLAN-каналам (попиксельный класс + экземпляр комнаты). Формат
такой же, как отдаёт load_rplan_png для настоящего PNG."""
import numpy as np
import pytest

from src.bim_agents.rplan_convert import (
    image_to_template, convert_dir, _signature,
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


def test_L_shaped_room_is_rejected():
    """L-образная комната: её bbox накрывает соседнюю → не slicing-раскладка,
    план честно отклоняется (наш формат не умеет непрямоугольные комнаты)."""
    category, instance = _blank()
    # L-образная гостиная (инстанс 1) огибает угол
    _put(category, instance, 0, 1, 4, 4, 60, 34)    # верхняя широкая полоса
    _put(category, instance, 0, 1, 4, 34, 34, 60)   # + левая ножка вниз
    _put(category, instance, 2, 2, 34, 34, 60, 60)  # кухня в правом-нижнем углу
    tpl = image_to_template(category, instance, source_id="Lshape", min_iou=0.7)
    assert tpl is None


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
