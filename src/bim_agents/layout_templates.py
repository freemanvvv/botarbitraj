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
from collections import Counter

_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "house_templates.json")

_MIN_STRIP_M = 0.9   # минимальная ширина колонки/ряда сетки после подгонки


def load_templates() -> list[dict]:
    with open(_TEMPLATES_PATH, encoding="utf-8") as f:
        return json.load(f)["templates"]


def match_template(room_cats: list[str], aspect: float, level: int) -> dict | None:
    """Шаг 1-2: подбор ближайшего по составу шаблона.

    Раньше требовалось ТОЧНОЕ совпадение мультимножества категорий — при
    маленьком датасете (и даже при большом) это почти всегда мимо: «3
    спальни» не совпадали с шаблоном на «2 спальни». Теперь условие мягче,
    но безопасно для последующей подгонки:

      • тот же НАБОР типов комнат (set совпадает) — чтобы каждой комнате
        пользователя нашлась ячейка, а у шаблона не осталось «пустых»
        категорий;
      • шаблон содержит НЕ БОЛЬШЕ комнат каждого типа, чем в запросе
        (tpl[c] ≤ want[c]) — «лишние» комнаты запроса добавит apply_template
        делением ячеек, а вот убирать ячейки (склейка) мы не умеем.

    Из подошедших берётся покрывающий больше комнат напрямую (меньше
    деления) и с ближайшим aspect. Точное совпадение — частный случай (tpl==
    want), оно по-прежнему выигрывает. Если ничего не подошло — None (уходит
    в зонированный fallback)."""
    want = Counter(room_cats)
    want_set = set(want)
    best, best_key = None, None
    for tpl in load_templates():
        role = tpl.get("storey_role", "any")
        if role == "ground" and level != 0:
            continue
        if role == "upper" and level == 0:
            continue
        tc = Counter(r["category"] for r in tpl["rooms"])
        if set(tc) != want_set:
            continue
        if any(tc[c] > want[c] for c in tc):
            continue
        coverage = sum(tc.values())  # сколько комнат запроса шаблон закрепляет напрямую
        aspect_score = abs(math.log(max(aspect, 1e-3) / max(tpl.get("aspect", 1.0), 1e-3)))
        key = (-coverage, aspect_score)  # больше покрытие → потом ближе aspect
        if best_key is None or key < best_key:
            best, best_key = tpl, key
    return best


def _split_cell(x0: float, y0: float, x1: float, y1: float, areas: list[float]) -> list[list[list[float]]]:
    """Делит прямоугольник ячейки на len(areas) под-прямоугольников вдоль
    длинной стороны пропорционально площадям — так «лишние» комнаты одного
    типа (сверх ячеек шаблона) занимают одну ячейку, не ломая мозаику."""
    n = len(areas)
    if n <= 1:
        return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]
    total = sum(areas) or 1.0
    out = []
    if (x1 - x0) >= (y1 - y0):
        cur = x0
        for i, a in enumerate(areas):
            nx = x1 if i == n - 1 else round(cur + (x1 - x0) * a / total, 4)
            out.append([[round(cur, 4), y0], [nx, y0], [nx, y1], [round(cur, 4), y1]])
            cur = nx
    else:
        cur = y0
        for i, a in enumerate(areas):
            ny = y1 if i == n - 1 else round(cur + (y1 - y0) * a / total, 4)
            out.append([[x0, round(cur, 4)], [x1, round(cur, 4)], [x1, ny], [x0, ny]])
            cur = ny
    return out


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

    rooms_meta: [(Room, category)]. Комнаты той же категории назначаются на
    ячейки шаблона «большая комната → большая ячейка»; если комнат этой
    категории БОЛЬШЕ, чем ячеек (match_template это допускает), лишние
    докладываются в ту же ячейку и она делится (_split_cell). Сетка сперва
    refit'ится под суммарные площади ячеек, затем каждая ячейка режется между
    своими комнатами. Возвращает (polygons: id→polygon, cats: id→category).
    """
    xs01, ys01 = tpl["x_cuts"], tpl["y_cuts"]
    cells = tpl["rooms"]

    def cell_area01(c):
        return (xs01[c["cx1"]] - xs01[c["cx0"]]) * (ys01[c["cy1"]] - ys01[c["cy0"]])

    by_cat_cells: dict[str, list[dict]] = {}
    for cell in cells:
        by_cat_cells.setdefault(cell["category"], []).append(cell)
    for cl in by_cat_cells.values():
        cl.sort(key=cell_area01, reverse=True)

    by_cat_rooms: dict[str, list] = {}
    for rm, cat in rooms_meta:
        by_cat_rooms.setdefault(cat, []).append(rm)
    for rms in by_cat_rooms.values():
        rms.sort(key=lambda r: r.area_m2, reverse=True)

    # Комнаты каждой категории раскидываем по её ячейкам: первые (по убыванию
    # площади) — по одной в каждую ячейку (крупная комната → крупная ячейка),
    # дальше по кругу докладываем «лишние». Ячейка → список её комнат.
    cell_rooms: dict[int, list] = {id(c): [] for c in cells}
    for cat, cl in by_cat_cells.items():
        rms = by_cat_rooms.get(cat, [])
        if not rms:
            raise ValueError("категория шаблона отсутствует в программе")
        for i, rm in enumerate(rms):
            cell_rooms[id(cl[i % len(cl)])].append(rm)
    placed = sum(len(v) for v in cell_rooms.values())
    if placed != len(rooms_meta):
        # у программы есть категория, которой нет в шаблоне — подгонка невозможна
        raise ValueError("состав комнат не совпал с шаблоном")

    # refit сетки под суммарные площади комнат каждой ячейки
    def cell_target(c):
        return sum(r.area_m2 for r in cell_rooms[id(c)]) or 0.5
    x_spans = [(c["cx0"], c["cx1"], cell_target(c)) for c in cells]
    y_spans = [(c["cy0"], c["cy1"], cell_target(c)) for c in cells]
    xs_m = _refit_axis(list(xs01), x_spans, fw)
    ys_m = _refit_axis(list(ys01), y_spans, fd)

    polygons, cats = {}, {}
    for c in cells:
        group = cell_rooms[id(c)]
        x0, x1 = xs_m[c["cx0"]], xs_m[c["cx1"]]
        y0, y1 = ys_m[c["cy0"]], ys_m[c["cy1"]]
        subrects = _split_cell(x0, y0, x1, y1, [r.area_m2 for r in group])
        for rm, rect in zip(group, subrects):
            polygons[rm.id] = rect
            cats[rm.id] = c["category"]
    return polygons, cats
