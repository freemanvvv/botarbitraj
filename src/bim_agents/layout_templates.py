"""
Подбор и параметрическая подгонка шаблонов планировки («Поиск → Сравнение →
Подгонка») для house-движка.

Идея та же, что в CAD/AI-системах поверх датасетов планировок (RPLAN,
CubiCasa5k): не генерировать план с нуля, а взять проверенную топологию из
библиотеки (house_templates.json) и деформировать под габариты и площади
пользователя, сохранив взаимное расположение комнат:

1. Поиск: точное совпадение мультимножества категорий комнат этажа
   (living/kitchen/hall/wet/bedroom/utility — см. classify_room) с составом
   шаблона + фильтр по роли этажа (ground/upper/any).
2. Сравнение: из подошедших берётся шаблон с ближайшим соотношением сторон —
   чтобы последующая деформация была минимальной.
3. Подгонка: сетка разрезов шаблона перераспределяется под целевые площади
   комнат (итеративный перенос «спроса» площадей на колонки/ряды сетки),
   при этом топология (кто с кем граничит, где вход) не меняется, а толщины
   стен и ширины дверей/окон остаются нормативными константами — растягиваются
   только «пустоты» комнат, не сам чертёж.

Датасет расширяется без кода: новые шаблоны дописываются в
house_templates.json (вручную или конвертером из векторных датасетов вида
CubiCasa5k SVG — формат описан в _comment датасета).
"""
from __future__ import annotations
import json
import math
import os

_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "house_templates.json")

_MIN_STRIP_M = 0.9   # минимальная ширина колонки/ряда сетки после подгонки


def load_templates() -> list[dict]:
    with open(_TEMPLATES_PATH, encoding="utf-8") as f:
        return json.load(f)["templates"]


def _multiset(cats: list[str]) -> tuple:
    return tuple(sorted(cats))


def match_template(room_cats: list[str], aspect: float, level: int) -> dict | None:
    """Шаг 1-2: шаблон с тем же составом комнат и ближайшим aspect (w/d)."""
    want = _multiset(room_cats)
    best, best_score = None, None
    for tpl in load_templates():
        role = tpl.get("storey_role", "any")
        if role == "ground" and level != 0:
            continue
        if role == "upper" and level == 0:
            continue
        if _multiset([r["category"] for r in tpl["rooms"]]) != want:
            continue
        score = abs(math.log(max(aspect, 1e-3) / max(tpl.get("aspect", 1.0), 1e-3)))
        if best_score is None or score < best_score:
            best, best_score = tpl, score
    return best


def _refit_axis(cuts01: list[float], spans: list[tuple[int, int, float]], total_m: float) -> list[float]:
    """Перераспределяет ширины полос сетки под целевые площади комнат.

    spans: (i0, i1, target_area) — комната занимает полосы i0..i1-1 и «хочет»
    target_area. Спрос комнаты размазывается по её полосам пропорционально их
    текущим ширинам, потом ширины полос пересчитываются пропорционально
    суммарному спросу. Две итерации достаточно — процесс сжимающий, а точная
    сходимость не нужна: остаточные расхождения площадей ловит нормо-проверка
    и repair-loop.
    """
    n = len(cuts01) - 1
    widths = [(cuts01[i + 1] - cuts01[i]) * total_m for i in range(n)]
    for _ in range(2):
        demand = [0.0] * n
        for i0, i1, target in spans:
            span_w = sum(widths[i0:i1]) or 1e-6
            for j in range(i0, i1):
                demand[j] += max(target, 0.5) * (widths[j] / span_w)
        total_demand = sum(demand) or 1.0
        widths = [total_m * d / total_demand for d in demand]
        # Пол минимальной ширины полосы — иначе комната с крошечной целевой
        # площадью (напр. заведомо невалидный запрос) схлопывает полосу в ноль
        # и геометрия вырождается.
        deficit = 0.0
        for j in range(n):
            if widths[j] < _MIN_STRIP_M:
                deficit += _MIN_STRIP_M - widths[j]
                widths[j] = _MIN_STRIP_M
        if deficit > 0:
            shrinkable = [j for j in range(n) if widths[j] > _MIN_STRIP_M]
            pool = sum(widths[j] - _MIN_STRIP_M for j in shrinkable) or 1e-6
            for j in shrinkable:
                widths[j] -= deficit * (widths[j] - _MIN_STRIP_M) / pool
    cuts_m = [0.0]
    for w in widths:
        cuts_m.append(cuts_m[-1] + w)
    cuts_m[-1] = total_m  # накопленная ошибка float
    return [round(c, 4) for c in cuts_m]


def apply_template(tpl: dict, rooms_meta: list[tuple], fw: float, fd: float) -> tuple[dict, dict]:
    """Шаг 3: программа комнат + шаблон → полигоны в метрах.

    rooms_meta: [(Room, category)]. Комнаты назначаются на слоты той же
    категории «большая комната → большая ячейка» (по площади ячейки в
    исходной сетке), затем сетка refit'ится под целевые площади.
    Возвращает (polygons: id→polygon, cats: id→category).
    """
    xs01, ys01 = tpl["x_cuts"], tpl["y_cuts"]

    def cell_area01(r):
        return (xs01[r["cx1"]] - xs01[r["cx0"]]) * (ys01[r["cy1"]] - ys01[r["cy0"]])

    by_cat_cells: dict[str, list[dict]] = {}
    for cell in tpl["rooms"]:
        by_cat_cells.setdefault(cell["category"], []).append(cell)
    for cells in by_cat_cells.values():
        cells.sort(key=cell_area01, reverse=True)

    by_cat_rooms: dict[str, list] = {}
    for rm, cat in rooms_meta:
        by_cat_rooms.setdefault(cat, []).append(rm)
    for rooms in by_cat_rooms.values():
        rooms.sort(key=lambda r: r.area_m2, reverse=True)

    assignment = {}  # room.id -> cell
    for cat, cells in by_cat_cells.items():
        for rm, cell in zip(by_cat_rooms.get(cat, []), cells):
            assignment[rm.id] = cell
    if len(assignment) != len(rooms_meta):
        raise ValueError("состав комнат не совпал с шаблоном")

    rooms_by_id = {rm.id: rm for rm, _ in rooms_meta}
    x_spans = [(assignment[rid]["cx0"], assignment[rid]["cx1"], rooms_by_id[rid].area_m2)
               for rid in assignment]
    y_spans = [(assignment[rid]["cy0"], assignment[rid]["cy1"], rooms_by_id[rid].area_m2)
               for rid in assignment]
    xs_m = _refit_axis(list(xs01), x_spans, fw)
    ys_m = _refit_axis(list(ys01), y_spans, fd)

    polygons, cats = {}, {}
    for rm, cat in rooms_meta:
        cell = assignment[rm.id]
        x0, x1 = xs_m[cell["cx0"]], xs_m[cell["cx1"]]
        y0, y1 = ys_m[cell["cy0"]], ys_m[cell["cy1"]]
        polygons[rm.id] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        cats[rm.id] = cat
    return polygons, cats
