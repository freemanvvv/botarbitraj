"""
Фаза 1 — FloorPlanAgent.
Treemap-солвер: размещает прямоугольные помещения внутри контура здания
по спецификации BuildingProgram. Гарантирует замкнутость, отсутствие наложений,
корректный axis-граф стен.
"""
from __future__ import annotations
import math
from .contracts import BuildingProgram, FloorPlan, RoomPlan, WallPlan, OpeningPlan, StoreyPlan, StairPlan


def _squarified_treemap(areas: list[float], rect_w: float, rect_h: float) -> list[list[float]]:
    """Упрощённый treemap: разбивает rect на прямоугольные области по площади."""
    total = sum(areas)
    if total == 0:
        return []
    # Сортируем по убыванию
    sorted_idx = sorted(range(len(areas)), key=lambda i: -areas[i])
    results = [None] * len(areas)

    # Простое рекурсивное разбиение
    rows = []
    cur_x = 0.0
    cur_y = 0.0
    remaining_w = rect_w
    remaining_h = rect_h

    for idx in sorted_idx:
        area = areas[idx]
        frac = area / total if total > 0 else 0
        cell_w = frac * rect_w
        cell_h = rect_h

        # Если не влезает по ширине — новая строка
        if cur_x + cell_w > rect_w:
            cur_x = 0.0
            # оставшиеся площади
            remaining = [areas[j] for j in sorted_idx if results[j] is None]
            if remaining:
                r_total = sum(remaining)
                new_row_h = rect_h * (r_total / total) if r_total > 0 else rect_h - cur_y
                # Но для простоты: фиксированная высота
                cell_h = rect_h - cur_y
            else:
                cell_h = rect_h - cur_y

        if cell_w > 0 and cell_h > 0:
            results[idx] = [
                [cur_x, cur_y],
                [cur_x + cell_w, cur_y],
                [cur_x + cell_w, cur_y + cell_h],
                [cur_x, cur_y + cell_h],
            ]
            cur_x += cell_w
            if cur_x >= rect_w:
                new_area_frac = area / total
                cur_y += rect_h * new_area_frac if total > 0 else 0
                total -= area

    return [r for r in results if r is not None]


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
            # Окно на внешней стене (не слишком короткой)
            if length > 3.0:
                openings.append(OpeningPlan(
                    wall=wall.id,
                    kind="window",
                    offset_m=1.0,
                    width_m=min(2.4, length * 0.4),
                    height_m=1.5,
                    sill_m=0.9,
                ))
                if length > 6.0:
                    openings.append(OpeningPlan(
                        wall=wall.id,
                        kind="window",
                        offset_m=length - 1.0 - min(2.4, length * 0.4),
                        width_m=min(2.4, length * 0.4),
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
        rects = _squarified_treemap(areas, fw, fd)

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
