"""
Фаза 1 — FloorPlanAgent.
Treemap-солвер: размещает прямоугольные помещения внутри контура здания
по спецификации BuildingProgram. Гарантирует замкнутость, отсутствие наложений,
корректный axis-граф стен.
"""
from __future__ import annotations
import math
from .contracts import BuildingProgram, FloorPlan, RoomPlan, WallPlan, OpeningPlan, StoreyPlan, StairPlan


def _squarified_treemap(areas: list[float], rect_w: float, rect_h: float, min_widths: list[float] | None = None) -> list[list[float]]:
    """
    Однорядное разбиение rect на вертикальные полосы по площади — раньше
    называлось "упрощённый treemap" и пыталось заводить вторую строку при
    переполнении ширины, но поскольку cell_w всегда пропорциональна area/
    total, сумма cell_w математически равна rect_w и вторая строка
    фактически никогда не создавалась — на практике это ВСЕГДА была одна
    строка, только без учёта Room.min_width_m (LLM обязан его заполнять —
    ARCHITECT_PROMPT прямо просит min_width_m, — но он попросту
    игнорировался). Из-за этого узкие по площади комнаты (например,
    прихожая) получали ширину меньше нормы КМК ещё до всякой проверки.

    Здесь — тот же однорядный расклад, но каждая комната сперва получает
    min_widths[i] гарантированно, остаток ширины распределяется по area
    (тот же приём, что src/floorplan/solver.py:_split_widths). Каждая
    комната по-прежнему занимает всю глубину rect_h (примыкает и к
    передней, и к задней внешним стенам) — сознательное упрощение вместо
    настоящего 2D treemap, но оно даёт гарантированный доступ к внешней
    стене (и, значит, к окну) для КАЖДОЙ комнаты, что было основным
    источником нарушений норм в house_norms.py.
    """
    n = len(areas)
    if n == 0:
        return []
    total = sum(areas) or 1.0
    mins = min_widths or [0.0] * n

    sum_min = sum(mins)
    if rect_w >= sum_min:
        remaining = rect_w - sum_min
        widths = [mw + remaining * (a / total) for mw, a in zip(mins, areas)]
    else:
        # Не помещается даже по минимумам — сжимаем пропорционально;
        # получившееся нарушение нормы ширины поймает house_norms.py,
        # так же как аналогичный fallback в solver.py.
        scale = rect_w / sum_min if sum_min > 0 else 1.0 / n
        widths = [mw * scale if sum_min > 0 else rect_w / n for mw in mins]

    results = []
    cur_x = 0.0
    for w in widths:
        results.append([
            [cur_x, 0.0], [cur_x + w, 0.0], [cur_x + w, rect_h], [cur_x, rect_h],
        ])
        cur_x += w
    return results


def _walls_from_rooms(polygons: dict[str, list[list[float]]], thickness: float) -> list[WallPlan]:
    """Строит стены по общим граням между помещениями + внешний контур."""
    wall_id = 0
    walls = []
    room_list = list(polygons.keys())
    used_edges = set()

    # Каждое ребро: ((x1,y1),(x2,y2)) в отсортированном виде
    def edge_key(p1, p2):
        p1r = (round(p1[0], 4), round(p1[1], 4))
        p2r = (round(p2[0], 4), round(p2[1], 4))
        return tuple(sorted((p1r, p2r)))

    # Собираем все рёбра
    all_edges = {}
    for rid, poly in polygons.items():
        n = len(poly)
        for i in range(n):
            p1 = poly[i]
            p2 = poly[(i + 1) % n]
            ek = edge_key(p1, p2)
            if ek not in all_edges:
                all_edges[ek] = []
            all_edges[ek].append(rid)

    # Общие рёбра = внутренние стены, уникальные = внешние
    for ek, rids in all_edges.items():
        (x1, y1), (x2, y2) = ek
        if len(rids) == 1:
            wtype = "exterior"
        else:
            wtype = "interior"

        wall_id += 1
        walls.append(WallPlan(
            id=f"w{wall_id}",
            axis=[[x1, y1], [x2, y2]],
            type=wtype,
            thickness_m=0.4 if wtype == "exterior" else 0.15,
        ))

    return walls


def _openings_from_adjacency(
    walls: list[WallPlan],
    adjacency: list[list[str]],
    polygons: dict[str, list[list[float]]],
) -> list[OpeningPlan]:
    """Размещает двери между смежными помещениями и окна по внешним стенам."""
    openings = []

    for wall in walls:
        (x1, y1), (x2, y2) = wall.axis
        length = math.sqrt((x2-x1)**2 + (y2-y1)**2)
        if length < 0.5:
            continue

        if wall.type == "interior":
            # Дверь в середине стены
            openings.append(OpeningPlan(
                wall=wall.id,
                kind="door",
                offset_m=length/2 - 0.45,
                width_m=0.9,
                height_m=2.1,
                sill_m=0.0,
            ))
        else:
            # Окно на внешней стене. Порог раньше был length > 3.0 м — с
            # прежним treemap (без учёта min_width_m) узкие комнаты почти
            # всегда получали короткий сегмент внешней стены и оставались
            # вовсе без окна (см. house_norms.py: "не примыкает к внешней
            # стене с окном"), даже физически её касаясь. Однорядная
            # раскладка с min_width_m делает сегменты стен более
            # предсказуемыми (обычно ≥ нормативной ширины комнаты), так что
            # порог снижен до практического минимума для оконного блока.
            if length > 1.0:
                win_w = min(2.4, max(0.6, length * 0.6))
                openings.append(OpeningPlan(
                    wall=wall.id,
                    kind="window",
                    offset_m=max(0.0, (length - win_w) / 2),
                    width_m=win_w,
                    height_m=1.5,
                    sill_m=0.9,
                ))
                if length > 6.0:
                    win_w2 = min(2.4, length * 0.3)
                    openings.append(OpeningPlan(
                        wall=wall.id,
                        kind="window",
                        offset_m=length - 1.0 - win_w2,
                        width_m=win_w2,
                        height_m=1.5,
                        sill_m=0.9,
                    ))

    return openings


def generate_floor_plan(program: BuildingProgram) -> FloorPlan:
    """Основная функция: BuildingProgram → FloorPlan."""
    fw = program.footprint["width_m"]
    fd = program.footprint["depth_m"]
    storeys_data = []

    for level in range(program.storeys):
        elevation = level * program.ceiling_height_m

        # Помещения этого этажа
        level_rooms = [r for r in program.rooms if r.storey == level]
        if not level_rooms:
            storeys_data.append(StoreyPlan(level=level, elevation_m=elevation))
            continue

        # Treemap
        areas = [r.area_m2 for r in level_rooms]
        min_widths = [r.min_width_m for r in level_rooms]
        rects = _squarified_treemap(areas, fw, fd, min_widths)

        # Строим полигоны
        polygons = {}
        for i, rect in enumerate(rects):
            if rect:
                polygons[level_rooms[i].id] = rect

        # Стены
        walls = _walls_from_rooms(polygons, 0.3)

        # Проёмы
        openings = _openings_from_adjacency(walls, program.adjacency, polygons)

        storeys_data.append(StoreyPlan(
            level=level,
            elevation_m=elevation,
            rooms=[RoomPlan(id=rid, polygon=poly) for rid, poly in polygons.items()],
            walls=walls,
            openings=openings,
        ))

    # Лестница (если >1 этаж)
    stairs = []
    if program.storeys > 1:
        stairs.append(StairPlan(
            from_level=0,
            to_level=1,
            shape="L",
            footprint=[[fw-3, 0], [fw, 0], [fw, 2.5], [fw-3, 2.5]],
        ))

    return FloorPlan(storeys=storeys_data, stairs=stairs)
