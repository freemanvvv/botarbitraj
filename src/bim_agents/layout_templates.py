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
import gzip
import json
import math
import os
from collections import Counter

_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "house_templates.json")


def _resolve_templates_path() -> str:
    """Путь к датасету. Если рядом лежит сжатый .json.gz (большой RPLAN-набор
    не влезает в git несжатым — лимит GitHub 100 МБ), он в приоритете; иначе
    обычный .json (рукописный seed на 6 шаблонов для разработки)."""
    gz = _TEMPLATES_PATH + ".gz"
    return gz if os.path.exists(gz) else _TEMPLATES_PATH


def _read_templates(path: str) -> list[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)["templates"]

_MIN_STRIP_M = 0.9   # минимальная ширина колонки/ряда сетки после подгонки


# Кэш загруженного датасета + индекс по набору категорий. Критично при
# больших датасетах (RPLAN даёт десятки тысяч шаблонов): без кэша
# load_templates перечитывал и парсил весь файл (~50 МБ = ~4 сек) на КАЖДЫЙ
# вызов match_template, т.е. по 3 раза на 3-этажный дом. Кэш инвалидируется
# по mtime файла (перегенерировал датасет → подхватится автоматически).
_CACHE: dict = {}
_CACHE_KEY = "current"  # единственный слот кэша (путь может меняться .json↔.json.gz)


def _build_catset_index(templates: list[dict]) -> dict:
    """{frozenset(категории комнат): [шаблоны]} — match_template рассматривает
    только шаблоны с ТЕМ ЖЕ набором типов комнат, поэтому индекс сужает
    перебор с десятков тысяч до единиц вместо линейного скана всего датасета."""
    idx: dict = {}
    for tpl in templates:
        key = frozenset(r["category"] for r in tpl["rooms"])
        idx.setdefault(key, []).append(tpl)
    return idx


def load_templates() -> list[dict]:
    """Список шаблонов из house_templates.json[.gz] (кэшируется по mtime)."""
    path = _resolve_templates_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    c = _CACHE.get(_CACHE_KEY)
    if c is not None and c["path"] == path and c["mtime"] == mtime:
        return c["templates"]
    templates = _read_templates(path)
    _CACHE[_CACHE_KEY] = {"path": path, "mtime": mtime, "templates": templates,
                          "index": _build_catset_index(templates)}
    return templates


def _catset_index() -> dict:
    """Индекс по набору категорий для актуального load_templates(). Для
    реального файла — из кэша; при monkeypatch load_templates в тестах
    (маленькие списки) строится на лету."""
    templates = load_templates()
    c = _CACHE.get(_CACHE_KEY)
    if c is not None and c["templates"] is templates:
        return c["index"]
    return _build_catset_index(templates)


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
    want_set = frozenset(want)
    best, best_key = None, None
    # И polygon- (tc==want), и grid-шаблоны (set(tc)==want_set) требуют ТОГО
    # ЖЕ набора категорий, что и запрос → берём только их из индекса, а не
    # линейно сканируем весь датасет (десятки тысяч шаблонов).
    for tpl in _catset_index().get(want_set, ()):
        role = tpl.get("storey_role", "any")
        if role == "ground" and level != 0:
            continue
        if role == "upper" and level == 0:
            continue
        tc = Counter(r["category"] for r in tpl["rooms"])
        is_poly = _is_polygon_template(tpl)
        if is_poly:
            # polygon-шаблон хранит РЕАЛЬНЫЕ формы; лишние комнаты в него не
            # доложить (произвольный полигон не делим) — нужен точный состав.
            if tc != want:
                continue
        else:
            if set(tc) != want_set:
                continue
            if any(tc[c] > want[c] for c in tc):
                continue
        coverage = sum(tc.values())  # сколько комнат запроса шаблон закрепляет напрямую
        aspect_score = abs(math.log(max(aspect, 1e-3) / max(tpl.get("aspect", 1.0), 1e-3)))
        # При равном покрытии polygon-шаблон (реальная форма) предпочтительнее
        # прямоугольного grid-шаблона — 0 бьёт 1 в сортировке ключа.
        key = (-coverage, 0 if is_poly else 1, aspect_score)
        if best_key is None or key < best_key:
            best, best_key = tpl, key
    return best


def _is_polygon_template(tpl: dict) -> bool:
    return tpl.get("_source") == "graph2plan_poly" or any("polygon" in r for r in tpl["rooms"])


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


def _slot_rects(slot: dict) -> list[tuple[int, int, int, int]]:
    """Индексные прямоугольники слота шаблона: обычный слот — один
    (cx0,cx1,cy0,cy1); Г-образный/ступенчатый ("cells") — несколько."""
    if "cells" in slot:
        return [tuple(r) for r in slot["cells"]]
    return [(slot["cx0"], slot["cx1"], slot["cy0"], slot["cy1"])]


def _cells_from_index_rects(rects_idx: list[tuple[int, int, int, int]]) -> set[tuple[int, int]]:
    """Прямоугольники слота в индексах сетки (cx0,cx1,cy0,cy1) → множество
    единичных ячеек (i,j), которые они покрывают."""
    cells: set[tuple[int, int]] = set()
    for cx0, cx1, cy0, cy1 in rects_idx:
        for i in range(cx0, cx1):
            for j in range(cy0, cy1):
                cells.add((i, j))
    return cells


def _merge_unit_cells_to_loop(cells: set[tuple[int, int]]) -> list[tuple[int, int]] | None:
    """Единичные ячейки сетки (i,j) → замкнутый контур из ВЕРШИН СЕТКИ
    (индексы, не метры) обходом границы.

    Работаем на уровне единичных ячеек, а не произвольных прямоугольников:
    рёбра соседних единичных ячеек всегда совпадают ЦЕЛИКОМ (длина строго 1
    шаг сетки), поэтому взаимное гашение общих рёбер корректно всегда — в
    отличие от попытки гасить рёбра исходных (разноразмерных) прямоугольников
    слота напрямую, где общая граница может быть лишь ЧАСТЬЮ более длинного
    ребра одного из них (внутренний угол Г-формы) и не отменяется точным
    сравнением. None — ячейки не образуют одну простую (без дыр, односвязную)
    rectilinear-область; вызывающая сторона должна считать это отказом."""
    if not cells:
        return None
    edge_count: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
    for i, j in cells:
        corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
        for k in range(4):
            p1, p2 = corners[k], corners[(k + 1) % 4]
            edge_count[(p1, p2)] = edge_count.get((p1, p2), 0) + 1

    boundary = []
    cancelled = set()
    for (p1, p2) in list(edge_count):
        if (p1, p2) in cancelled or (p2, p1) in cancelled:
            continue
        if (p2, p1) in edge_count:
            cancelled.add((p1, p2)); cancelled.add((p2, p1))
        else:
            boundary.append((p1, p2))
    if not boundary:
        return None

    adj: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for p1, p2 in boundary:
        adj.setdefault(p1, []).append(p2)

    start = boundary[0][0]
    loop = [start]
    cur = start
    used = set()
    while True:
        nxts = [p for p in adj.get(cur, []) if (cur, p) not in used]
        if not nxts:
            return None  # оборванный контур — не односвязная простая область
        nxt = nxts[0]
        used.add((cur, nxt))
        if nxt == start:
            break
        loop.append(nxt)
        cur = nxt
    if len(used) != len(boundary):
        return None  # остались непосещённые рёбра — несколько отдельных контуров
    return _drop_collinear(loop)


def _drop_collinear(loop: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Убирает вершины контура, лежащие на прямой между соседями (стык двух
    единичных ячеек одной комнаты вдоль общей внешней стороны — по трассировке
    он не даёт настоящего угла). Не влияет на корректность (тот же простой
    полигон), но даёт компактный список вершин — как у остальных шаблонов."""
    n = len(loop)
    if n < 3:
        return loop
    out = []
    for k in range(n):
        p0, p1, p2 = loop[k - 1], loop[k], loop[(k + 1) % n]
        dx1, dy1 = p1[0] - p0[0], p1[1] - p0[1]
        dx2, dy2 = p2[0] - p1[0], p2[1] - p1[1]
        if dx1 * dy2 - dy1 * dx2 != 0:  # не коллинеарны — настоящий угол
            out.append(p1)
    return out or loop


def _apply_polygon_template(tpl: dict, rooms_meta: list[tuple], fw: float, fd: float) -> tuple[dict, dict]:
    """polygon-шаблон (rooms[i].polygon в 0..1, реальная форма комнаты) →
    полигоны в метрах масштабированием под габариты. Комнаты программы
    назначаются на слоты той же категории «большая комната → слот большей
    площади». Состав должен совпадать точно (match_template это гарантирует
    для polygon-шаблонов) — иначе ValueError и откат на fallback."""
    def poly_area01(slot):
        p = slot["polygon"]
        s = 0.0
        for i in range(len(p)):
            x1, y1 = p[i]; x2, y2 = p[(i + 1) % len(p)]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2

    by_cat_slots: dict[str, list[dict]] = {}
    for slot in tpl["rooms"]:
        by_cat_slots.setdefault(slot["category"], []).append(slot)
    for sl in by_cat_slots.values():
        sl.sort(key=poly_area01, reverse=True)

    by_cat_rooms: dict[str, list] = {}
    for rm, cat in rooms_meta:
        by_cat_rooms.setdefault(cat, []).append(rm)
    for rms in by_cat_rooms.values():
        rms.sort(key=lambda r: r.area_m2, reverse=True)

    polygons, cats = {}, {}
    for cat, slots in by_cat_slots.items():
        rms = by_cat_rooms.get(cat, [])
        if len(rms) != len(slots):
            raise ValueError("состав комнат не совпал с polygon-шаблоном")
        for rm, slot in zip(rms, slots):
            polygons[rm.id] = [[round(x * fw, 4), round(y * fd, 4)] for x, y in slot["polygon"]]
            cats[rm.id] = cat
    if len(polygons) != len(rooms_meta):
        raise ValueError("не все комнаты размещены polygon-шаблоном")
    return polygons, cats


def apply_template(tpl: dict, rooms_meta: list[tuple], fw: float, fd: float) -> tuple[dict, dict]:
    """Шаг 3: программа комнат + шаблон → полигоны в метрах.

    rooms_meta: [(Room, category)]. Комнаты той же категории назначаются на
    слоты шаблона «большая комната → большой слот» (площадь слота — сумма
    площадей его прямоугольников); если комнат этой категории БОЛЬШЕ, чем
    слотов (match_template это допускает), лишние докладываются в тот же
    слот. Обычный (однопрямоугольный) слот в этом случае делится
    (_split_cell); Г-образный ("cells") слот делить не умеем — несколько
    комнат в него не докладываем (ValueError → откат на зонированный
    fallback в floorplan_agent.py). Сетка сперва refit'ится под суммарные
    площади (каждый прямоугольник слота — пропорциональная доля площади
    слота), затем полигон каждой комнаты строится из её прямоугольника(ов)
    в метрах (Г-образные — через _merge_unit_cells_to_loop, обход на уровне единичных ячеек сетки).
    Возвращает (polygons: id→polygon, cats: id→category).

    polygon-шаблоны (реальные формы комнат из RPLAN/rBoundary) обрабатываются
    отдельной веткой _apply_polygon_template: контуры лишь масштабируются под
    габариты (сохраняя реальную форму и пропорции плана), без сеточного
    refit'а под точные площади.
    """
    if _is_polygon_template(tpl):
        return _apply_polygon_template(tpl, rooms_meta, fw, fd)

    xs01, ys01 = tpl["x_cuts"], tpl["y_cuts"]
    slots = tpl["rooms"]

    def slot_rects01(slot):
        return [(xs01[cx0], ys01[cy0], xs01[cx1], ys01[cy1]) for cx0, cx1, cy0, cy1 in _slot_rects(slot)]

    def slot_area01(slot):
        return sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in slot_rects01(slot))

    by_cat_slots: dict[str, list[dict]] = {}
    for slot in slots:
        by_cat_slots.setdefault(slot["category"], []).append(slot)
    for sl in by_cat_slots.values():
        sl.sort(key=slot_area01, reverse=True)

    by_cat_rooms: dict[str, list] = {}
    for rm, cat in rooms_meta:
        by_cat_rooms.setdefault(cat, []).append(rm)
    for rms in by_cat_rooms.values():
        rms.sort(key=lambda r: r.area_m2, reverse=True)

    # Комнаты каждой категории раскидываем по её слотам: первые (по убыванию
    # площади) — по одной в каждый слот (крупная комната → крупный слот),
    # дальше по кругу докладываем «лишние». Слот → список его комнат.
    slot_rooms: dict[int, list] = {id(s): [] for s in slots}
    for cat, sl in by_cat_slots.items():
        rms = by_cat_rooms.get(cat, [])
        if not rms:
            raise ValueError("категория шаблона отсутствует в программе")
        for i, rm in enumerate(rms):
            slot_rooms[id(sl[i % len(sl)])].append(rm)
    placed = sum(len(v) for v in slot_rooms.values())
    if placed != len(rooms_meta):
        # у программы есть категория, которой нет в шаблоне — подгонка невозможна
        raise ValueError("состав комнат не совпал с шаблоном")
    for slot in slots:
        if "cells" in slot and len(slot_rooms[id(slot)]) > 1:
            # Г-образный слот получить несколько «лишних» комнат не может —
            # делить непрямоугольную форму между ними не умеем.
            raise ValueError("Г-образный слот не может принять несколько комнат")

    # refit сетки: каждый прямоугольник слота вносит долю целевой площади
    # слота пропорционально своей доле в исходной (0..1) площади слота.
    def slot_target(slot):
        return sum(r.area_m2 for r in slot_rooms[id(slot)]) or 0.5

    x_spans, y_spans = [], []
    for slot in slots:
        target = slot_target(slot)
        rects_idx = _slot_rects(slot)
        area01 = slot_area01(slot) or 1e-9
        for (cx0, cx1, cy0, cy1), (x0, y0, x1, y1) in zip(rects_idx, slot_rects01(slot)):
            share = ((x1 - x0) * (y1 - y0)) / area01
            x_spans.append((cx0, cx1, target * share))
            y_spans.append((cy0, cy1, target * share))
    xs_m = _refit_axis(list(xs01), x_spans, fw)
    ys_m = _refit_axis(list(ys01), y_spans, fd)

    polygons, cats = {}, {}
    for slot in slots:
        group = slot_rooms[id(slot)]
        rects_idx = _slot_rects(slot)
        if len(rects_idx) == 1:
            cx0, cx1, cy0, cy1 = rects_idx[0]
            x0, y0, x1, y1 = xs_m[cx0], ys_m[cy0], xs_m[cx1], ys_m[cy1]
            subrects = _split_cell(x0, y0, x1, y1, [r.area_m2 for r in group])
            for rm, rect in zip(group, subrects):
                polygons[rm.id] = rect
                cats[rm.id] = slot["category"]
        else:
            loop_idx = _merge_unit_cells_to_loop(_cells_from_index_rects(rects_idx))
            polygon = [[xs_m[i], ys_m[j]] for i, j in loop_idx] if loop_idx is not None else None
            if polygon is None:
                raise ValueError("Г-образный слот дал несвязную геометрию после подгонки")
            polygons[group[0].id] = polygon
            cats[group[0].id] = slot["category"]
    return polygons, cats
