"""
Фаза 1 — BIMAgent.
FloorPlan → IFC4 файл через IfcOpenShell.
Строит стены, плиты, окна (IfcOpeningElement + IfcRelVoidsElement + IfcRelFillsElement),
двери, пространства, лестницы, материалы.
"""
from __future__ import annotations
import os
import math
from datetime import datetime
from typing import Optional

from .contracts import FloorPlan

try:
    import ifcopenshell
    import ifcopenshell.guid
    IFC_OK = True
except ImportError:
    IFC_OK = False


def _g(ifc):
    return ifcopenshell.guid.new()


def _cp3(ifc, x, y, z):
    return ifc.create_entity("IfcCartesianPoint", (float(x), float(y), float(z)))


def _d3(ifc, x, y, z):
    return ifc.create_entity("IfcDirection", (float(x), float(y), float(z)))


def _make_placement(ifc, x, y, z, x_axis=None):
    origin = _cp3(ifc, x, y, z)
    z_dir = _d3(ifc, 0, 0, 1)
    x_dir = x_axis or _d3(ifc, 1, 0, 0)
    return ifc.create_entity("IfcLocalPlacement",
                              ifc.create_entity("IfcAxis2Placement3D", origin, z_dir, x_dir))


def _make_extrusion(ifc, profile, depth):
    return ifc.create_entity("IfcExtrudedAreaSolid", profile,
                              ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, 0),
                                                 _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
                              _d3(ifc, 0, 0, 1), float(depth))


def _add_material(ifc, product, name: str):
    mat = ifc.create_entity("IfcMaterial", name)
    ifc.create_entity("IfcRelAssociatesMaterial", _g(ifc), None, None,
                       RelatedObjects=[product], RelatingMaterial=mat)


def _create_space(ifc, storey, room_id: str, polygon: list[list[float]], z: float, h: float, ctx) -> object:
    """Создаёт замкнутое IfcSpace."""
    space = ifc.create_entity("IfcSpace", _g(ifc), None, room_id)
    space.PredefinedType = "NOTDEFINED"
    space.ObjectPlacement = _make_placement(ifc, 0, 0, z)
    space.LongName = room_id

    # Профиль-многоугольник
    pts = [_cp3(ifc, p[0], p[1], 0) for p in polygon]
    if not pts:
        return space
    poly = ifc.create_entity("IfcPolyline", pts)
    closed = ifc.create_entity("IfcPolyLoop", [pi for pi in pts])
    area_def = ifc.create_entity("IfcArbitraryClosedProfileDef", "AREA", None, closed)
    ext = _make_extrusion(ifc, area_def, h)
    rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [ext])
    space.Representation = rep
    ifc.create_entity("IfcRelContainedInSpatialStructure", _g(ifc), None, None,
                       RelatedElements=[space], RelatingStructure=storey)
    return space


def _create_wall(ifc, wall_plan, ctx, z: float, h: float) -> object:
    """Стена с проёмами."""
    (x1, y1), (x2, y2) = wall_plan.axis
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx*dx + dy*dy)
    angle = math.atan2(dy, dx)
    thick = wall_plan.thickness_m

    wall = ifc.create_entity("IfcWall", _g(ifc), None, wall_plan.id)
    wall.ObjectPlacement = _make_placement(ifc, x1, y1, z,
                                            _d3(ifc, math.cos(angle), math.sin(angle), 0))
    prof = ifc.create_entity("IfcRectangleProfileDef", "AREA", None,
                              ifc.create_entity("IfcAxis2Placement2D", _cp3(ifc, 0, 0, 0)),
                              float(length), float(thick))
    ext = _make_extrusion(ifc, prof, h)
    rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [ext])
    wall.Representation = rep
    _add_material(ifc, wall, "бетон")
    return wall


def _create_opening_and_fill(ifc, wall, opening_plan, ctx, z: float) -> tuple:
    """IfcOpeningElement + IfcWindow/IfcDoor."""
    thick = 0.05
    # Opening
    op = ifc.create_entity("IfcOpeningElement", _g(ifc), None,
                            f"{wall.Name} opening {opening_plan.wall}")
    op.ObjectPlacement = _make_placement(ifc, opening_plan.offset_m, 0,
                                          z + opening_plan.sill_m)
    prof = ifc.create_entity("IfcRectangleProfileDef", "AREA", None,
                              ifc.create_entity("IfcAxis2Placement2D", _cp3(ifc, 0, 0, 0)),
                              float(opening_plan.width_m), float(thick))
    ext = _make_extrusion(ifc, prof, opening_plan.height_m)
    rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [ext])
    op.Representation = rep
    ifc.create_entity("IfcRelVoidsElement", _g(ifc), None, None,
                       RelatingBuildingElement=wall, RelatedOpeningElement=op)
    return op


def _create_window(ifc, wall, opening_plan, ctx, z: float):
    op = _create_opening_and_fill(ifc, wall, opening_plan, ctx, z)
    win = ifc.create_entity("IfcWindow", _g(ifc), None,
                             f"Window {opening_plan.wall}")
    win.OverallWidth = opening_plan.width_m
    win.OverallHeight = opening_plan.height_m
    win.ObjectPlacement = _make_placement(ifc, opening_plan.offset_m + opening_plan.width_m/2, -0.03,
                                           z + opening_plan.sill_m)
    _add_material(ifc, win, "стеклопакет")
    ifc.create_entity("IfcRelFillsElement", _g(ifc), None, None,
                       RelatingOpeningElement=op, RelatedBuildingElement=win)
    return win


def _create_door(ifc, wall, opening_plan, ctx, z: float):
    op = _create_opening_and_fill(ifc, wall, opening_plan, ctx, z)
    door = ifc.create_entity("IfcDoor", _g(ifc), None,
                              f"Door {opening_plan.wall}")
    door.OverallWidth = opening_plan.width_m
    door.OverallHeight = opening_plan.height_m
    door.ObjectPlacement = _make_placement(ifc, opening_plan.offset_m + opening_plan.width_m/2, -0.03,
                                            z + opening_plan.sill_m)
    _add_material(ifc, door, "дерево")
    ifc.create_entity("IfcRelFillsElement", _g(ifc), None, None,
                       RelatingOpeningElement=op, RelatedBuildingElement=door)
    return door


def _create_slab(ifc, footprint, ctx, z: float, thick: float, name: str, ptype: str = "FLOOR") -> object:
    fw = footprint[0]
    fd = footprint[1]
    slab = ifc.create_entity("IfcSlab", _g(ifc), None, name)
    slab.PredefinedType = ptype
    slab.ObjectPlacement = _make_placement(ifc, 0, 0, z)
    prof = ifc.create_entity("IfcRectangleProfileDef", "AREA", None,
                              ifc.create_entity("IfcAxis2Placement2D", _cp3(ifc, 0, 0, 0)),
                              float(fw), float(fd))
    ext = _make_extrusion(ifc, prof, thick)
    rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [ext])
    slab.Representation = rep
    _add_material(ifc, slab, "железобетон")
    return slab


def generate_ifc(floor_plan: FloorPlan, output_dir: str = "output") -> str:
    """FloorPlan → IFC4 файл. Возвращает путь к файлу."""
    if not IFC_OK:
        raise ImportError("IfcOpenShell не установлен")

    ifc = ifcopenshell.file(schema="IFC4")

    # Project
    proj = ifc.create_entity("IfcProject", _g(ifc), None, "BIM Building")

    # Контекст
    ctx = ifc.create_entity("IfcGeometricRepresentationContext")
    ctx.ContextIdentifier = "Model"
    ctx.ContextType = "Model"
    ctx.CoordinateSpaceDimension = 3

    # Units
    unit_m = ifc.create_entity("IfcSIUnit", None, "LENGTHUNIT", None, "METRE")
    ifc.create_entity("IfcUnitAssignment", [unit_m])

    # Site + Building
    site = ifc.create_entity("IfcSite", _g(ifc), None, "Site")
    bldg = ifc.create_entity("IfcBuilding", _g(ifc), None, "BIM Building")
    ifc.create_entity("IfcRelAggregates", _g(ifc), None, None,
                       RelatingObject=proj, RelatedObjects=[site])
    ifc.create_entity("IfcRelAggregates", _g(ifc), None, None,
                       RelatingObject=site, RelatedObjects=[bldg])

    storey_ifc_objs = []

    for sdata in floor_plan.storeys:
        z = sdata.elevation_m
        h = 3.0

        storey = ifc.create_entity("IfcBuildingStorey", _g(ifc), None,
                                    f"Этаж {sdata.level}")
        ifc.create_entity("IfcRelAggregates", _g(ifc), None, None,
                           RelatingObject=bldg, RelatedObjects=[storey])

        storey_elements = []

        # Перекрытие
        if floor_plan.storeys:
            fw = 12  # default from footprint
            fd = 9
            # Try to compute from rooms
            all_x = []
            all_y = []
            for rp in sdata.rooms:
                for p in rp.polygon:
                    all_x.append(p[0])
                    all_y.append(p[1])
            if all_x:
                fw = max(all_x) - min(all_x) + 1
                fd = max(all_y) - min(all_y) + 1

            slab = _create_slab(ifc, (fw, fd), ctx, z, 0.2, f"Перекрытие эт.{sdata.level}")
            storey_elements.append(slab)

        # Пространства
        for rp in sdata.rooms:
            space = _create_space(ifc, storey, rp.id, rp.polygon, z, h, ctx)
            storey_elements.append(space)

        # Стены с проёмами
        for wp in sdata.walls:
            wall = _create_wall(ifc, wp, ctx, z, h)
            storey_elements.append(wall)

            # Проёмы
            for op in sdata.openings:
                if op.wall == wp.id:
                    if op.kind == "window":
                        win = _create_window(ifc, wall, op, ctx, z)
                        storey_elements.append(win)
                    elif op.kind == "door":
                        door = _create_door(ifc, wall, op, ctx, z)
                        storey_elements.append(door)

        ifc.create_entity("IfcRelContainedInSpatialStructure", _g(ifc), None, None,
                           RelatedElements=storey_elements, RelatingStructure=storey)
        storey_ifc_objs.append(storey)

    # Лестница
    for stair_data in floor_plan.stairs:
        try:
            stair = ifc.create_entity("IfcStair", _g(ifc), None,
                                       f"Stair {stair_data.from_level}→{stair_data.to_level}")
            stair.PredefinedType = "NOTDEFINED"
            from_z = stair_data.from_level * 3.0
            stair.ObjectPlacement = _make_placement(ifc, 1, 1, from_z)
            _add_material(ifc, stair, "дерево")
            if storey_ifc_objs:
                ifc.create_entity("IfcRelContainedInSpatialStructure", _g(ifc), None, None,
                                   RelatedElements=[stair], RelatingStructure=storey_ifc_objs[0])
        except Exception:
            pass

    # Сохранение
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"bim_model_{ts}.ifc")
    ifc.write(path)

    stats = {
        "walls": len(ifc.by_type("IfcWall")),
        "slabs": len(ifc.by_type("IfcSlab")),
        "windows": len(ifc.by_type("IfcWindow")),
        "doors": len(ifc.by_type("IfcDoor")),
        "spaces": len(ifc.by_type("IfcSpace")),
        "storeys": len(ifc.by_type("IfcBuildingStorey")),
        "stairs": len(ifc.by_type("IfcStair")),
    }
    return path, stats
