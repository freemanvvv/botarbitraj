"""
Фаза 1 — FloorPlanAgent.

BuildingProgram → FloorPlan в два уровня:

1. Шаблонный путь (основной): состав комнат этажа ищется в лёгком датасете
   проверенных топологий (house_templates.json, см. layout_templates.py) —
   «Поиск → Сравнение → Подгонка», как в CAD/AI-системах поверх RPLAN/
   CubiCasa5k. Найденный шаблон параметрически деформируется под габариты
   и целевые площади, топология (кто с кем граничит, где вход) сохраняется.

2. Зонированный солвер (fallback, когда состав комнат не совпал ни с одним
   шаблоном): этаж делится на две ленты по датасету зонирования
   (house_layout_dataset.json) — фронтальная (вход: прихожая, гостиная,
   кухня, мокрая зона) и тыльная (спальни). Внутри ленты — однорядная
   раскладка с гарантией Room.min_width_m. Прежний вариант — ОДНА лента на
   всю глубину footprint'а — давал комнаты-«кишки» (санузел 1.7×9 м);
   две ленты дают пропорции, близкие к реальным проектам.

Общая для обоих путей геометрия: сегментированные стены (общие грани комнат
режутся на участки по владельцам — иначе стык двух лент с разной нарезкой
не распознавался бы как внутренняя стена), двери от прихожей-хаба (а не
«дверь в каждой внутренней стене»), входная дверь на фасаде 1-го этажа,
окна только комнатам, которым нужен свет (needs_window в датасете).
"""
from __future__ import annotations
import json
import math
import os

from .contracts import BuildingProgram, FloorPlan, RoomPlan, WallPlan, OpeningPlan, StoreyPlan, StairPlan
from .layout_templates import match_template, match_templates, apply_template, _is_polygon_template

_DATASET_PATH = os.path.join(os.path.dirname(__file__), "house_layout_dataset.json")
with open(_DATASET_PATH, encoding="utf-8") as _f:
    _DATASET = json.load(_f)
_CATS = _DATASET["room_categories"]
_RULES = _DATASET["rules"]


def classify_room(type_str: str, name: str = "") -> str:
    """Свободные Room.type/name → категория зонирования из датасета."""
    haystack = f"{type_str} {name}".lower()
    for cat, spec in _CATS.items():
        for kw in spec.get("keywords", []):
            if kw in haystack:
                return cat
    return "other"


def _needs_window(cat: str) -> bool:
    return _CATS.get(cat, {}).get("needs_window", True)


# ─────────────────────────── зонированный fallback ───────────────────────────

def _row_widths(areas: list[float], mins: list[float], rect_w: float) -> list[float]:
    """Ширины комнат в одном ряду: каждой гарантируется её минимум, остаток
    делится пропорционально площади (тот же приём, что solver.py апартаментов).
    Если минимумы не влезают — пропорциональное сжатие, нарушение поймает
    house_norms."""
    total = sum(areas) or 1.0
    sum_min = sum(mins)
    if rect_w >= sum_min:
        rem = rect_w - sum_min
        return [mw + rem * (a / total) for mw, a in zip(mins, areas)]
    if sum_min > 0:
        return [mw * rect_w / sum_min for mw in mins]
    return [rect_w / len(areas)] * len(areas)


def _order_key(meta):
    rm, cat = meta
    return (_CATS.get(cat, {}).get("order", 9), -rm.area_m2, rm.id)


def _band_split(metas: list[tuple]) -> tuple[list, list]:
    """Распределение комнат этажа по двум лентам (front/back) по датасету +
    балансировка, чтобы в каждой ленте было ≥2 комнат (одна комната лентой
    на всю ширину дома — вырожденный случай). Пустая back — сигнал
    «раскладывать одним рядом»."""
    if len(metas) < _RULES["min_rooms_for_two_bands"]:
        return list(metas), []
    front = [m for m in metas if _CATS.get(m[1], {}).get("band", "any") != "back"]
    back = [m for m in metas if _CATS.get(m[1], {}).get("band", "any") == "back"]
    while len(back) < 2 and len(front) > 2:
        movable = [m for m in front if m[1] != "hall"]
        if not movable:
            break
        m = min(movable, key=lambda t: t[0].area_m2)
        front.remove(m)
        back.append(m)
    while len(front) < 2 and len(back) > 2:
        m = min(back, key=lambda t: t[0].area_m2)
        back.remove(m)
        front.append(m)
    if not front or not back:
        return list(metas), []
    front.sort(key=_order_key)
    back.sort(key=_order_key)
    return front, back


def _grid_rows(band: list[tuple], y0: float, y1: float, fw: float) -> list[tuple]:
    """Ленту с многими комнатами разбивает на несколько под-рядов (сетку),
    чтобы комнаты не вытягивались в «кишку». Один ряд из n комнат по ширине
    fw даёт ячейки (fw/n)×depth: если лента глубокая и комнат много, ячейки
    получаются высокими и узкими. Число под-рядов r≈√(n·depth/fw) уравнивает
    стороны ячейки; для широкой мелкой ленты (depth<fw) округляется к 1 —
    тогда прежнее поведение (один ряд) сохраняется.

    Глубина делится между под-рядами РАВНОМЕРНО, а не по площади: площадь
    подгоняется шириной комнат внутри ряда (см. _row_widths) и repair-loop'ом,
    а вот сильно неравные глубины как раз и рождают «кишки» (под-ряд с одной
    мелкой комнатой получал бы 1-метровую полосу во всю ширину). Комнаты
    раскладываются по под-рядам примерно поровну. Возвращает (под-ряд, sy0,
    sy1)."""
    n = len(band)
    depth = y1 - y0
    if n <= 1 or depth <= 1e-6:
        return [(band, y0, y1)]
    r = max(1, min(n, round(math.sqrt(n * depth / max(fw, 1e-6)))))
    if r == 1:
        return [(band, y0, y1)]
    # сбалансированное число комнат в под-рядах: n=5,r=2 → [3,2], а не [3,2]
    # с перекосом — первые (n mod r) рядов на одну комнату больше.
    base, extra = divmod(n, r)
    sizes = [base + 1 if i < extra else base for i in range(r)]
    out, idx, cur = [], 0, y0
    for k, size in enumerate(sizes):
        chunk = band[idx:idx + size]
        idx += size
        sy1 = y1 if k == r - 1 else round(y0 + depth * (k + 1) / r, 4)
        out.append((chunk, round(cur, 4), sy1))
        cur = sy1
    return out


def _zoned_polygons(metas: list[tuple], fw: float, fd: float) -> tuple[dict, dict]:
    """Fallback-раскладка: одна/две ленты, каждая при необходимости разбита
    на сетку под-рядов (см. _grid_rows). Возвращает (polygons, cats)."""
    front, back = _band_split(metas)
    if back and fd >= _RULES["min_depth_for_two_bands_m"]:
        a_front = sum(t[0].area_m2 for t in front) or 1.0
        a_back = sum(t[0].area_m2 for t in back) or 1.0
        d1 = fd * a_front / (a_front + a_back)
        mbd = _RULES["min_band_depth_m"]
        d1 = round(min(max(d1, mbd), fd - mbd), 4)
        bands = [(front, 0.0, d1), (back, d1, fd)]
    else:
        bands = [(sorted(front + back, key=_order_key), 0.0, fd)]

    rows = []
    for band, by0, by1 in bands:
        rows.extend(_grid_rows(band, by0, by1, fw))

    polygons, cats = {}, {}
    for row, y0, y1 in rows:
        widths = _row_widths([t[0].area_m2 for t in row],
                             [t[0].min_width_m for t in row], fw)
        cur = 0.0
        for (rm, cat), w in zip(row, widths):
            x0 = round(cur, 4)
            cur += w
            x1 = round(cur, 4)
            polygons[rm.id] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            cats[rm.id] = cat
        # накопленная float-ошибка: последняя комната упирается ровно в fw,
        # иначе стык рядов не совпадёт по владельцам сегментов
        polygons[row[-1][0].id][1][0] = polygons[row[-1][0].id][2][0] = round(fw, 4)
    return polygons, cats


# ───────────────────────── стены (сегментированные) ─────────────────────────

def _walls_from_rooms(polygons: dict[str, list[list[float]]]) -> tuple[list[WallPlan], dict]:
    """Грани всех комнат группируются по несущим линиям и режутся на участки
    в точках смены владельцев. Участок с одним владельцем — внешняя стена,
    с двумя — внутренняя. Прежний вариант сравнивал только ЦЕЛЫЕ рёбра —
    у двух лент с разной нарезкой общая граница не совпадает целиком, и
    внутренние стены ошибочно стали бы внешними (с окнами в межкомнатных
    перегородках)."""
    horiz: dict[float, list] = {}
    vert: dict[float, list] = {}
    for rid, poly in polygons.items():
        n = len(poly)
        for i in range(n):
            x1, y1 = round(poly[i][0], 4), round(poly[i][1], 4)
            x2, y2 = round(poly[(i + 1) % n][0], 4), round(poly[(i + 1) % n][1], 4)
            if abs(y1 - y2) < 1e-9 and abs(x1 - x2) > 1e-9:
                horiz.setdefault(y1, []).append((min(x1, x2), max(x1, x2), rid))
            elif abs(x1 - x2) < 1e-9 and abs(y1 - y2) > 1e-9:
                vert.setdefault(x1, []).append((min(y1, y2), max(y1, y2), rid))

    walls: list[WallPlan] = []
    owners: dict[str, frozenset] = {}
    wall_n = 0

    def emit(lines: dict[float, list], horizontal: bool):
        nonlocal wall_n
        for c, intervals in sorted(lines.items()):
            cuts = sorted({v for a, b, _ in intervals for v in (a, b)})
            segs: list[tuple[float, float, frozenset]] = []
            for i in range(len(cuts) - 1):
                a, b = cuts[i], cuts[i + 1]
                if b - a < 1e-6:
                    continue
                own = frozenset(r for x1, x2, r in intervals if x1 - 1e-6 <= a and b <= x2 + 1e-6)
                if not own:
                    continue
                if segs and segs[-1][2] == own and abs(segs[-1][1] - a) < 1e-6:
                    segs[-1] = (segs[-1][0], b, own)
                else:
                    segs.append((a, b, own))
            for a, b, own in segs:
                wall_n += 1
                wtype = "interior" if len(own) > 1 else "exterior"
                axis = [[a, c], [b, c]] if horizontal else [[c, a], [c, b]]
                wall = WallPlan(id=f"w{wall_n}", axis=axis, type=wtype,
                                thickness_m=0.4 if wtype == "exterior" else 0.15)
                walls.append(wall)
                owners[wall.id] = own

    emit(horiz, True)
    emit(vert, False)
    return walls, owners


# ──────────────────────────────── проёмы ────────────────────────────────────

def _seg_len(wall: WallPlan) -> float:
    (x1, y1), (x2, y2) = wall.axis
    return math.hypot(x2 - x1, y2 - y1)


def _bbox_area(poly: list[list[float]]) -> float:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _place_openings(walls: list[WallPlan], owners: dict, polygons: dict,
                    cats: dict, level: int, fd: float) -> list[OpeningPlan]:
    """Двери и окна как на реальных планах: вход на фасаде (этаж 0), двери
    комнат — от прихожей/гостиной-хаба (fallback: любая внутренняя стена,
    чтобы у каждой комнаты гарантированно был доступ — это проверяет
    house_norms), окна — только комнатам с needs_window, на их собственном
    участке фасада."""
    openings: list[OpeningPlan] = []
    by_room: dict[str, list[WallPlan]] = {}
    for w in walls:
        for rid in owners[w.id]:
            by_room.setdefault(rid, []).append(w)

    hub = None
    for pref in ("hall", "living"):
        ids = sorted(rid for rid in polygons if cats.get(rid) == pref)
        if ids:
            hub = ids[0]
            break
    if hub is None:
        hub = max(sorted(polygons), key=lambda rid: _bbox_area(polygons[rid]))

    door_w = _RULES["door_width_m"]
    entry_wall_id = None

    if level == 0:
        exterior = [w for w in by_room.get(hub, []) if w.type == "exterior"]
        facade = [w for w in exterior
                  if abs(w.axis[0][1]) < 1e-6 and abs(w.axis[1][1]) < 1e-6]
        pick = facade or exterior
        if pick:
            w = max(pick, key=_seg_len)
            length = _seg_len(w)
            if length >= 1.0:
                dw = min(_RULES["entry_door_width_m"], max(0.8, length - 0.3))
                openings.append(OpeningPlan(wall=w.id, kind="door",
                                            offset_m=max(0.0, (length - dw) / 2),
                                            width_m=dw, height_m=2.1, sill_m=0.0))
                entry_wall_id = w.id

    for rid in sorted(polygons):
        if rid == hub:
            continue  # хаб получает двери со стороны соседей + входную
        cand = [w for w in by_room.get(rid, []) if w.type == "interior" and _seg_len(w) >= door_w + 0.2]
        if not cand:
            cand = [w for w in by_room.get(rid, []) if w.type == "interior" and _seg_len(w) >= 0.75]
        if not cand:
            continue

        def door_rank(w):
            others = owners[w.id] - {rid}
            if hub in others:
                rank = 2
            elif any(cats.get(o) not in ("wet", "utility") for o in others):
                rank = 1
            else:
                rank = 0
            return (rank, _seg_len(w))

        w = max(cand, key=door_rank)
        length = _seg_len(w)
        dw = door_w if length >= door_w + 0.2 else round(max(0.7, length - 0.15), 2)
        openings.append(OpeningPlan(wall=w.id, kind="door",
                                    offset_m=max(0.0, (length - dw) / 2),
                                    width_m=dw, height_m=2.1, sill_m=0.0))

    for rid in sorted(polygons):
        if not _needs_window(cats.get(rid, "other")):
            continue
        exterior = [w for w in by_room.get(rid, [])
                    if w.type == "exterior" and w.id != entry_wall_id]
        facade = [w for w in exterior
                  if abs(w.axis[0][1] - w.axis[1][1]) < 1e-9
                  and (abs(w.axis[0][1]) < 1e-6 or abs(w.axis[0][1] - fd) < 1e-6)]
        pick = facade or exterior
        if not pick:
            continue
        w = max(pick, key=_seg_len)
        length = _seg_len(w)
        if length <= 0.9:
            continue
        if length > 6.0:
            ww = min(2.4, length * 0.25)
            for center in (0.25, 0.75):
                openings.append(OpeningPlan(wall=w.id, kind="window",
                                            offset_m=max(0.0, length * center - ww / 2),
                                            width_m=ww, height_m=1.5, sill_m=0.9))
        else:
            ww = min(2.4, max(0.6, length * 0.5))
            openings.append(OpeningPlan(wall=w.id, kind="window",
                                        offset_m=max(0.0, (length - ww) / 2),
                                        width_m=ww, height_m=1.5, sill_m=0.9))
    return openings


# ─────────────── силуэт здания для многоэтажки (общий на все этажи) ───────────

# Формы застройки: прямоугольник + Г-образные (вырез в одном из 4 углов).
# ОДИН силуэт выбирается на ВСЁ здание (по variant) и заполняется на каждом
# этаже — так дом может быть непрямоугольным (частный сектор редко строго
# «коробкой»), но контур/габариты одинаковы на всех этажах и стены стыкуются
# по вертикали. variant «🔀 Другой вариант формы» листает эти силуэты.
_BUILDING_SHAPES = ["rect", "L_tr", "L_tl", "L_br", "L_bl"]
_NOTCH_FRAC = 0.42  # доля стороны, вырезаемая в углу Г-формы


def _shape_regions(shape: str, fw: float, fd: float) -> list[tuple]:
    """Силуэт → список прямоугольников (x0,y0,x1,y1), объединение которых и
    есть контур этажа. Г-форма = прямоугольник минус угловой вырез = два
    прямоугольника. bbox всегда [0,fw]×[0,fd] → габариты этажей совпадают."""
    nw, nd = round(fw * _NOTCH_FRAC, 4), round(fd * _NOTCH_FRAC, 4)
    if shape == "L_tr":   # вырез в верхнем-правом углу
        return [(0.0, 0.0, fw, fd - nd), (0.0, fd - nd, fw - nw, fd)]
    if shape == "L_tl":   # верхний-левый
        return [(0.0, 0.0, fw, fd - nd), (nw, fd - nd, fw, fd)]
    if shape == "L_br":   # нижний-правый
        return [(0.0, nd, fw, fd), (0.0, 0.0, fw - nw, nd)]
    if shape == "L_bl":   # нижний-левый
        return [(0.0, nd, fw, fd), (nw, 0.0, fw, nd)]
    return [(0.0, 0.0, fw, fd)]  # rect


def _tile_region(region_metas: list[tuple], x0: float, y0: float,
                 x1: float, y1: float) -> dict:
    """Замащивает прямоугольник региона комнатами (сетка под-рядов + деление
    ширины ряда, как в зонированном солвере), со смещением на (x0,y0).
    Возвращает {id: polygon}."""
    w = x1 - x0
    polygons: dict = {}
    for row, ry0, ry1 in _grid_rows(region_metas, y0, y1, w):
        widths = _row_widths([t[0].area_m2 for t in row],
                             [t[0].min_width_m for t in row], w)
        cur = x0
        for (rm, _cat), ww in zip(row, widths):
            nx = cur + ww
            polygons[rm.id] = [[round(cur, 4), round(ry0, 4)], [round(nx, 4), round(ry0, 4)],
                               [round(nx, 4), round(ry1, 4)], [round(cur, 4), round(ry1, 4)]]
            cur = nx
        # последняя комната ряда упирается ровно в x1 (стык рядов по владельцам)
        polygons[row[-1][0].id][1][0] = polygons[row[-1][0].id][2][0] = round(x1, 4)
    return polygons


def _assign_regions(metas: list[tuple], regions: list[tuple]) -> list[list]:
    """Раскидывает комнаты по регионам силуэта пропорционально их площади
    (жадно: комнату — в регион с наибольшим остатком ёмкости). Гарантирует,
    что ни один регион не пуст (иначе в контуре появится дыра)."""
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in regions]
    remaining = list(areas)
    buckets: list[list] = [[] for _ in regions]
    for m in sorted(metas, key=lambda t: -t[0].area_m2):
        i = max(range(len(regions)), key=lambda k: remaining[k])
        buckets[i].append(m)
        remaining[i] -= m[0].area_m2
    for i, b in enumerate(buckets):
        if b:
            continue
        j = max(range(len(buckets)), key=lambda k: len(buckets[k]))
        if len(buckets[j]) > 1:
            m = min(buckets[j], key=lambda t: t[0].area_m2)
            buckets[j].remove(m)
            b.append(m)
    return buckets


def _fill_shape(metas: list[tuple], shape: str, fw: float, fd: float, level: int = 0) -> tuple[dict, dict]:
    """Заполняет силуэт shape комнатами этажа. Для 'rect' — реальная форма из
    датасета шаблонов (заполняющая прямоугольник, не polygon → одинаковый
    контур на этажах), иначе зонированный солвер; для Г-форм — раскладка по
    прямоугольникам-регионам силуэта."""
    if shape == "rect":
        cats_list = [c for _, c in metas]
        cands = [t for t in match_templates(cats_list, fw / fd if fd else 1.0, level)
                 if not _is_polygon_template(t)]
        if cands:
            try:
                return apply_template(cands[0], metas, fw, fd)
            except ValueError:
                pass
        return _zoned_polygons(metas, fw, fd)
    regions = _shape_regions(shape, fw, fd)
    buckets = _assign_regions(metas, regions)
    polygons, cats = {}, {}
    for region, bucket in zip(regions, buckets):
        if not bucket:
            continue
        polygons.update(_tile_region(bucket, *region))
        for rm, cat in bucket:
            cats[rm.id] = cat
    return polygons, cats


def _building_shapes(program: BuildingProgram) -> list[str]:
    """Допустимые силуэты для здания. Г-форма требует ≥2 комнат на КАЖДОМ
    непустом этаже (два региона надо чем-то заполнить); иначе — только 'rect'."""
    min_rooms = None
    for lvl in range(program.storeys):
        n = len([r for r in program.rooms if r.storey == lvl])
        if n:
            min_rooms = n if min_rooms is None else min(min_rooms, n)
    if min_rooms is not None and min_rooms >= 2:
        return _BUILDING_SHAPES
    return ["rect"]


def _normalize_footprint(polygons: dict, bw: float, bd: float) -> None:
    """Ин-плейс масштабирует полигоны этажа так, чтобы их общий bbox стал
    ровно [0,bw]×[0,bd] — тогда ВСЕ этажи имеют одинаковый внешний контур и
    размеры (стены стыкуются по этажам). Без этого этаж на polygon-шаблоне и
    этаж на зонированном солвере получают РАЗНЫЕ габариты (см. жалобу: «формы
    здания не совпадают, а значит и размеры»), т.к. polygon-шаблон
    пересчитывает fw/fd под свой aspect."""
    if not polygons:
        return
    xs = [p[0] for poly in polygons.values() for p in poly]
    ys = [p[1] for poly in polygons.values() for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    spanx, spany = maxx - minx, maxy - miny
    if spanx <= 1e-9 or spany <= 1e-9:
        return
    sx, sy = bw / spanx, bd / spany
    for poly in polygons.values():
        for pt in poly:
            pt[0] = round((pt[0] - minx) * sx, 4)
            pt[1] = round((pt[1] - miny) * sy, 4)


# ─────────────────────────────── основной вход ──────────────────────────────

def count_template_variants(program: BuildingProgram, limit: int = 10) -> int:
    """Сколько разных форм доступно под этот дом (для «Вариант k из N» и
    перелистывания). Для многоэтажного — число силуэтов здания (общий контур
    на все этажи). Для одноэтажного — число подходящих реальных форм из
    датасета шаблонов."""
    if program.storeys > 1:
        return len(_building_shapes(program))
    from .layout_templates import match_templates
    fw = program.footprint["width_m"]
    fd = program.footprint["depth_m"]
    best = 1
    for level in range(program.storeys):
        level_rooms = [r for r in program.rooms if r.storey == level]
        if not level_rooms:
            continue
        cats = [classify_room(r.type, r.name) for r in level_rooms]
        n = len(match_templates(cats, fw / fd if fd else 1.0, level, limit=limit))
        best = max(best, n)
    return min(best, limit)


def generate_floor_plan(program: BuildingProgram, variant: int = 0) -> FloorPlan:
    """Основная функция: BuildingProgram → FloorPlan.

    variant — какую из подходящих форм взять на каждом этаже (0 = лучшая);
    «перегенерировать» увеличивает variant → другая реальная планировка того
    же состава (см. layout_templates.match_template)."""
    fw = program.footprint["width_m"]
    fd = program.footprint["depth_m"]
    storeys_data = []
    # Многоэтажный дом: у всех этажей ОДИН внешний контур (стены стыкуются по
    # вертикали). Силуэт здания (прямоугольник или Г-форма) выбирается ОДИН
    # раз на всё здание по variant и заполняется на каждом этаже — форма может
    # быть непрямоугольной, но одинаковой на всех этажах. Одноэтажный дом
    # по-прежнему берёт реальные формы из датасета шаблонов (там консистентность
    # между этажами не нужна).
    multi = program.storeys > 1
    building_shape = None
    if multi:
        shapes = _building_shapes(program)
        building_shape = shapes[variant % len(shapes)]

    for level in range(program.storeys):
        elevation = level * program.ceiling_height_m
        level_rooms = [r for r in program.rooms if r.storey == level]
        if not level_rooms:
            storeys_data.append(StoreyPlan(level=level, elevation_m=elevation))
            continue

        metas = [(r, classify_room(r.type, r.name)) for r in level_rooms]
        cats_list = [c for _, c in metas]
        aspect = fw / fd if fd else 1.0

        polygons = cats = None
        if multi:
            # общий силуэт на всё здание → одинаковый контур на всех этажах
            polygons, cats = _fill_shape(metas, building_shape, fw, fd, level)
            _normalize_footprint(polygons, fw, fd)
        else:
            tpl = match_template(cats_list, aspect, level, variant=variant)
            if tpl is not None:
                try:
                    polygons, cats = apply_template(tpl, metas, fw, fd)
                except ValueError:
                    polygons = None
            if polygons is None:
                polygons, cats = _zoned_polygons(metas, fw, fd)

        walls, owners = _walls_from_rooms(polygons)
        openings = _place_openings(walls, owners, polygons, cats, level, fd)

        storeys_data.append(StoreyPlan(
            level=level,
            elevation_m=elevation,
            rooms=[RoomPlan(id=rid, polygon=poly) for rid, poly in polygons.items()],
            walls=walls,
            openings=openings,
        ))

    stairs = []
    if program.storeys > 1:
        stairs.append(StairPlan(
            from_level=0,
            to_level=1,
            shape="L",
            footprint=[[fw - 3, 0], [fw, 0], [fw, 2.5], [fw - 3, 2.5]],
        ))

    return FloorPlan(storeys=storeys_data, stairs=stairs)
