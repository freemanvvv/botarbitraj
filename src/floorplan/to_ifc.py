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
from .ir import ApartmentFloorplan


def _collect_partition_walls(fp: ApartmentFloorplan, eps: float = 0.02):
    """Общие границы комнат → сегменты стен [(x0,y0,x1,y1,axis,room_a,room_b), ...].
    axis="x" — стена идёт вдоль X (разделяет комнаты, стоящие друг над другом по Y);
    axis="y" — стена идёт вдоль Y (разделяет комнаты, стоящие рядом по X).
    """
    segments = []
    n = len(fp.rooms)
    for i in range(n):
        a = fp.rooms[i]
        for j in range(i + 1, n):
            b = fp.rooms[j]
            if abs(a.x1 - b.x0) < eps:
                y0, y1 = max(a.y0, b.y0), min(a.y1, b.y1)
                if y1 - y0 > eps:
                    segments.append((a.x1, y0, a.x1, y1, "y", i, j))
                    continue
            if abs(b.x1 - a.x0) < eps:
                y0, y1 = max(a.y0, b.y0), min(a.y1, b.y1)
                if y1 - y0 > eps:
                    segments.append((a.x0, y0, a.x0, y1, "y", i, j))
                    continue
            if abs(a.y1 - b.y0) < eps:
                x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
                if x1 - x0 > eps:
                    segments.append((x0, a.y1, x1, a.y1, "x", i, j))
                    continue
            if abs(b.y1 - a.y0) < eps:
                x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
                if x1 - x0 > eps:
                    segments.append((x0, a.y0, x1, a.y0, "x", i, j))
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
        _make_placement, _rect_profile, _extrude, _shape_rep,
        _assign_material, _add_door_to_wall, _set_pset, _g, _d3,
    )

    g = lambda: _g(ifc)
    elems = []
    pt = partition_thickness

    # ── Помещения (IfcSpace) ────────────────────────────────────────────────
    for room in fp.rooms:
        space = ifc.create_entity("IfcSpace", g(), None, f"{name_prefix}{room.name or room.type}")
        space.PredefinedType = "INTERNAL"
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

    for x0, y0, x1, y1, axis, ra, rb in _collect_partition_walls(fp):
        wall_len = (x1 - x0) if axis == "x" else (y1 - y0)
        if wall_len <= 0.05:
            continue
        wall = ifc.create_entity("IfcWall", g(), None, f"{name_prefix}Перегородка")
        wall.PredefinedType = "PARTITIONING"
        if axis == "x":
            wall.ObjectPlacement = _make_placement(ifc, ox + x0, oy + y0, wz, x_axis=_d3(ifc, 1, 0, 0))
        else:
            wall.ObjectPlacement = _make_placement(ifc, ox + x0, oy + y0, wz, x_axis=_d3(ifc, 0, 1, 0))
        prof = _rect_profile(ifc, wall_len, pt, cx=wall_len / 2, cy=pt / 2)
        wall.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, prof, floor_height - 0.1)])])
        _assign_material(ifc, wall, "гипсокартон")
        elems.append(wall)

        d = door_map.get(tuple(sorted((ra, rb))))
        if d is not None:
            door_x_local = (d.x - x0) if axis == "x" else (d.y - y0)
            room_b = fp.room(rb) if rb >= 0 else fp.room(ra)
            dname = f"Дверь {room_b.name}" if room_b and room_b.name else "Межкомнатная дверь"
            op, door = _add_door_to_wall(ifc, ctx, wall, door_x_local, d.width, door_height,
                                          wall.ObjectPlacement, pt, dname)
            elems.extend([op, door])

    return elems
