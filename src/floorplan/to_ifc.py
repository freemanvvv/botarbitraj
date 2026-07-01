"""
Фаза 0 — мост IR → IFC.

floorplan_to_ifc() строит по ApartmentFloorplan только ВНУТРЕННИЕ элементы
одной квартиры: перегородки (IfcWall, PARTITIONING), межкомнатные двери
(IfcDoor + IfcOpeningElement) и помещения (IfcSpace + Pset_SpaceCommon).

Входную дверь с лестничной площадки эта функция НЕ строит — её пробивает
вызывающий код (create_apartment_building) в уже существующей внешней или
межквартирной стене, т.к. только он знает, какая именно стена граничит
с площадкой для конкретной квартиры. fp.doors содержит DoorSpec с
kind="entry" — вызывающий код берёт из него (y, width) и сам вызывает
_add_door_to_wall на нужной стене.

Импорт хелперов геометрии из src.ifc_generator сделан внутри функции
(отложенный), чтобы не создавать цикл: ifc_generator импортирует этот
модуль на верхнем уровне, а этот модуль обращается к ifc_generator только
в момент вызова floorplan_to_ifc(), когда тот уже полностью загружен.
"""
import math

from .ir import ApartmentFloorplan
from .geometry import room_edges, collinear_overlap


def _collect_partition_walls(fp: ApartmentFloorplan, eps: float = 0.02):
    """Общие границы комнат → сегменты стен [(p0, p1, room_a, room_b), ...],
    p0/p1 — точки (x,y) начала/конца стены в локальных координатах квартиры.

    Работает и для прямоугольных комнат (солвер/LLM), и для произвольных
    полигонов (после векторизации растра ChatHouseDiffusion) через общий
    поиск коллинеарных перекрывающихся рёбер (geometry.py) — для
    прямоугольников даёт тот же результат, что и прежняя box-only версия.
    """
    segments = []
    n = len(fp.rooms)
    edges = [room_edges(r) for r in fp.rooms]
    for i in range(n):
        for j in range(i + 1, n):
            found = None
            for ea in edges[i]:
                for eb in edges[j]:
                    seg = collinear_overlap(ea, eb, eps)
                    if seg:
                        found = seg
                        break
                if found:
                    break
            if found:
                segments.append((found[0], found[1], i, j))
    return segments


def floorplan_to_ifc(
    ifc, ctx, fp: ApartmentFloorplan,
    ox: float, oy: float, wz: float,
    floor_height: float,
    partition_thickness: float = 0.1,
    door_height: float = 2.0,
    name_prefix: str = "",
) -> list:
    """
    Строит внутренние перегородки/двери/помещения квартиры в глобальных
    координатах здания.

    ox, oy — смещение локальной СК квартиры (левый нижний угол внутреннего
    контура, т.е. fp с координатами x∈[0,fp.width], y∈[0,fp.depth]) в
    глобальной СК этажа; wz — отметка пола этажа (верх перекрытия).

    Возвращает список созданных IFC-элементов (IfcSpace, IfcWall,
    IfcOpeningElement, IfcDoor) для добавления в IfcRelContainedInSpatialStructure.
    """
    from src.ifc_generator import (
        _make_placement, _rect_profile, _polygon_profile, _extrude, _shape_rep,
        _assign_material, _add_door_to_wall, _set_pset, _g, _d3,
    )

    g = lambda: _g(ifc)
    elems = []
    pt = partition_thickness

    # ── Помещения (IfcSpace) ────────────────────────────────────────────────
    for room in fp.rooms:
        space = ifc.create_entity("IfcSpace", g(), None, f"{name_prefix}{room.name or room.type}")
        space.PredefinedType = "INTERNAL"
        if room.polygon:
            # Полигон уже в локальных координатах квартиры — переносим точки
            # в СК помещения (относительно его же ObjectPlacement).
            space.ObjectPlacement = _make_placement(ifc, ox + room.x0, oy + room.y0, wz)
            local_pts = [(x - room.x0, y - room.y0) for x, y in room.polygon]
            prof = _polygon_profile(ifc, local_pts)
        else:
            space.ObjectPlacement = _make_placement(ifc, ox + room.x0, oy + room.y0, wz)
            prof = _rect_profile(ifc, max(room.width, 0.05), max(room.depth, 0.05),
                                  cx=room.width / 2, cy=room.depth / 2)
        space.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, prof, floor_height - 0.05)])])
        _set_pset(ifc, space, "Pset_SpaceCommon", {
            "Category": room.type,
            "GrossFloorArea": room.area,
        })
        elems.append(space)

    # ── Внутренние перегородки + межкомнатные двери ─────────────────────────
    door_map = {}
    for d in fp.doors:
        if d.kind != "interior":
            continue
        door_map[tuple(sorted((d.room_a, d.room_b)))] = d

    for p0, p1, ra, rb in _collect_partition_walls(fp):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        wall_len = math.hypot(dx, dy)
        if wall_len <= 0.05:
            continue
        ux, uy = dx / wall_len, dy / wall_len  # единичное направление стены (не только оси X/Y)

        wall = ifc.create_entity("IfcWall", g(), None, f"{name_prefix}Перегородка")
        wall.PredefinedType = "PARTITIONING"
        wall.ObjectPlacement = _make_placement(ifc, ox + p0[0], oy + p0[1], wz, x_axis=_d3(ifc, ux, uy, 0))
        prof = _rect_profile(ifc, wall_len, pt, cx=wall_len / 2, cy=pt / 2)
        wall.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, prof, floor_height - 0.1)])])
        _assign_material(ifc, wall, "гипсокартон")
        elems.append(wall)

        d = door_map.get(tuple(sorted((ra, rb))))
        if d is not None:
            # Проекция позиции двери на направление стены — корректно и для
            # осевых, и для произвольно направленных (полигональных) стен.
            door_x_local = (d.x - p0[0]) * ux + (d.y - p0[1]) * uy
            room_b = fp.room(rb) if rb >= 0 else fp.room(ra)
            dname = f"Дверь {room_b.name}" if room_b and room_b.name else "Межкомнатная дверь"
            op, door = _add_door_to_wall(ifc, ctx, wall, door_x_local, d.width, door_height,
                                          wall.ObjectPlacement, pt, dname)
            elems.extend([op, door])

    return elems
