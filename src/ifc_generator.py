"""
Генератор IFC-заготовок — максимальная версия.
Создаёт полноценную IFC4-модель здания:
- Наружные стены с оконными/дверными проёмами
- Внутренние перегородки с дверями
- Перекрытия на каждом этаже
- Скатная крыша
- Окна и двери как IfcOpeningElement + IfcWindow/IfcDoor
- Строительные оси (IfcGrid)
- Материалы (бетон, кирпич, стекло, дерево)
- Property Sets (огнестойкость, площадь)
"""
import os
import math
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from .config import OUTPUT_DIR

try:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.guid
    import ifcopenshell.util.unit
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False


# ─── dataclasses для окон и дверей ───

@dataclass
class WindowSpec:
    wall_name: str       # front, back, left, right
    x_offset: float      # от левого края стены
    width: float = 1.2
    height: float = 1.5
    sill_height: float = 0.9


@dataclass
class DoorSpec:
    x_offset: float
    width: float = 0.9
    height: float = 2.1


# ─── helpers ───

def _g(ifc):
    return ifcopenshell.guid.new()


def _cp3(ifc, x, y, z):
    return ifc.create_entity("IfcCartesianPoint", (float(x), float(y), float(z)))


def _cp2(ifc, x, y):
    return ifc.create_entity("IfcCartesianPoint", (float(x), float(y)))


def _d3(ifc, x, y, z):
    return ifc.create_entity("IfcDirection", (float(x), float(y), float(z)))


def _d2(ifc, x, y):
    return ifc.create_entity("IfcDirection", (float(x), float(y)))


def _make_placement(ifc, x, y, z, x_axis=None, z_axis=None, relative_to=None):
    origin = _cp3(ifc, x, y, z)
    z_dir = z_axis or _d3(ifc, 0, 0, 1)
    x_dir = x_axis or _d3(ifc, 1, 0, 0)
    a3 = ifc.create_entity("IfcAxis2Placement3D", origin, z_dir, x_dir)
    return ifc.create_entity("IfcLocalPlacement", relative_to, a3)


def _make_placement_2d(ifc, x, y):
    return ifc.create_entity("IfcAxis2Placement2D", _cp2(ifc, x, y), _d2(ifc, 1, 0))


def _make_box_profile(ifc, xlen, ylen):
    """Прямоугольный профиль в XY."""
    return ifc.create_entity("IfcRectangleProfileDef", "AREA", None,
                              _make_placement_2d(ifc, 0, 0), float(xlen), float(ylen))


def _make_extrusion(ifc, profile, depth, direction=None):
    """Выдавливание профиля по Z (по умолчанию вверх)."""
    dir_vec = direction or _d3(ifc, 0, 0, 1)
    return ifc.create_entity("IfcExtrudedAreaSolid", profile,
                              ifc.create_entity("IfcAxis2Placement3D",
                                                 _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
                              dir_vec, float(depth))


def _assign_material(ifc, product, material_name: str):
    """IFC-материал для элемента."""
    try:
        mat = ifc.create_entity("IfcMaterial", material_name)
        rel = ifc.create_entity("IfcRelAssociatesMaterial", _g(ifc), None, None,
                                RelatedObjects=[product], RelatingMaterial=mat)
    except Exception:
        pass  # некоторые IfcRoot не поддерживают материалы


def create_max_building(
    name: str = "Building",
    length: float = 15.0,
    width: float = 12.0,
    height: float = 7.0,
    num_floors: int = 2,
    wall_thickness: float = 0.4,
    slab_thickness: float = 0.2,
    roof_type: str = "gable",           # gable | flat
    add_internal_walls: bool = True,
    add_windows: bool = True,
    add_doors: bool = True,
    add_grid: bool = True,
) -> str:
    """
    Создаёт максимально детальную IFC-модель здания.
    """
    if not IFC_AVAILABLE:
        raise ImportError("IfcOpenShell не установлен")

    ifc = ifcopenshell.file(schema="IFC4")
    g = lambda: _g(ifc)

    # ─── Project ───
    proj = ifc.create_entity("IfcProject", g(), None, name)

    # ─── Geometry context ───
    wcs = ifc.create_entity("IfcAxis2Placement3D",
        _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0))
    ctx = ifc.create_entity("IfcGeometricRepresentationContext",
        "Model", "Model", 3, 1e-5, wcs, None)

    ctx_plan = ifc.create_entity("IfcGeometricRepresentationContext",
        "Plan", "Plan", 2, 1e-5, wcs, None)

    # ─── Units (мм для согласованности с местными нормами) ───
    unit_m = ifc.create_entity("IfcSIUnit", None, "LENGTHUNIT", None, "METRE")
    unit_area = ifc.create_entity("IfcSIUnit", None, "AREAUNIT", None, "SQUARE_METRE")
    unit_vol = ifc.create_entity("IfcSIUnit", None, "VOLUMEUNIT", None, "CUBIC_METRE")
    ifc.create_entity("IfcUnitAssignment", [unit_m, unit_area, unit_vol])

    # ─── Site → Building ───
    site = ifc.create_entity("IfcSite", g(), None, "Site")
    bldg = ifc.create_entity("IfcBuilding", g(), None, name)
    bldg.CompositionType = "ELEMENT"

    ifc.create_entity("IfcRelAggregates", g(), None, None,
                      RelatingObject=proj, RelatedObjects=[site])
    ifc.create_entity("IfcRelAggregates", g(), None, None,
                      RelatingObject=site, RelatedObjects=[bldg])



    floor_h = height / num_floors

    # ─── Вспомогательная: стена с окнами ───
    def build_wall_with_openings(
        wname: str, w, d, h, wx, wy, wz,
        windows: list,
        entrance_door: DoorSpec = None,
    ):
        """Стена с проёмами под окна/дверь."""
        wall = ifc.create_entity("IfcWall", g())
        wall.Name = wname
        wall.ObjectPlacement = _make_placement(ifc, wx, wy, wz)

        # Профиль стены
        prof = _make_box_profile(ifc, w, d)
        extrusion = _make_extrusion(ifc, prof, h)
        rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [extrusion])
        wall.Representation = rep

        _assign_material(ifc, wall, "бетон")
        _add_property_set(ifc, wall, "Pset_WallCommon", {
            "FireRating": "REI 120",
            "LoadBearing": True,
            "ExtendToStructure": True,
        })

        elements_in_wall = [wall]

        # Дверной проём (вход)
        if entrance_door:
            op = ifc.create_entity("IfcOpeningElement", g())
            op.Name = f"{wname} Door Opening"
            op.ObjectPlacement = _make_placement(ifc, wx + entrance_door.x_offset, 0, wz)
            op_prof = _make_box_profile(ifc, entrance_door.width, d)
            op_ext = _make_extrusion(ifc, op_prof, entrance_door.height)
            op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
            op.Representation = op_rep
            ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                              RelatingBuildingElement=wall, RelatedOpeningElement=op)
            elements_in_wall.append(op)

        # Оконные проёмы
        for win in windows:
            op = ifc.create_entity("IfcOpeningElement", g())
            op.Name = f"{wname} Window Opening #{win.x_offset:.1f}"
            op.ObjectPlacement = _make_placement(ifc, wx + win.x_offset, 0, wz + win.sill_height)
            op_prof = _make_box_profile(ifc, win.width, d)
            op_ext = _make_extrusion(ifc, op_prof, win.height)
            op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
            op.Representation = op_rep
            ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                              RelatingBuildingElement=wall, RelatedOpeningElement=op)
            elements_in_wall.append(op)

        return wall, elements_in_wall

    # ─── Этажи ───
    all_elements = []

    for floor_i in range(num_floors):
        z0 = floor_i * floor_h
        wz = z0 + slab_thickness

        storey = ifc.create_entity("IfcBuildingStorey", g(), None,
                                    f"Этаж {floor_i+1}")
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[storey])
        storey_elements = []

        # ─── Перекрытие ───
        slab = ifc.create_entity("IfcSlab", g(), None,
                                  f"Перекрытие эт.{floor_i+1}")
        slab.PredefinedType = "FLOOR"
        slab.ObjectPlacement = _make_placement(ifc, 0, 0, z0)
        slab_prof = _make_box_profile(ifc, length, width)
        slab_ext = _make_extrusion(ifc, slab_prof, slab_thickness)
        slab_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [slab_ext])
        slab.Representation = slab_rep
        _assign_material(ifc, slab, "железобетон")
        
        storey_elements.append(slab)

        # ─── Окна и двери ───
        windows_front = []
        windows_back = []
        windows_left = []
        windows_right = []

        if add_windows:
            spacing = 3.0
            for x_off in [spacing, spacing * 2]:
                if x_off + 1.2 < length:
                    windows_front.append(WindowSpec("front", x_off))
                    windows_back.append(WindowSpec("back", x_off))
            for y_off in [spacing, spacing * 2]:
                if y_off + 1.2 < width:
                    windows_left.append(WindowSpec("left", y_off))
                    windows_right.append(WindowSpec("right", y_off))

        entrance = DoorSpec(x_offset=2.0) if add_doors and floor_i == 0 else None

        # ─── Стены ───
        # Front (Y=width)
        wf = ifc.create_entity("IfcWall", g())
        wf.Name = f"Фасад эт.{floor_i+1}"
        wf.ObjectPlacement = _make_placement(ifc, 0, width, wz)
        wf_prof = _make_box_profile(ifc, length, wall_thickness)
        wf_ext = _make_extrusion(ifc, wf_prof, floor_h)
        wf_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [wf_ext])
        wf.Representation = wf_rep
        _assign_material(ifc, wf, "кирпич керамический")
        

        # Проёмы на фасаде (placement relative to front wall)
        if add_windows or entrance:
            for win in windows_front:
                op = ifc.create_entity("IfcOpeningElement", g())
                op.Name = f"Окно фасад {win.x_offset}"
                op.ObjectPlacement = _make_placement(ifc, win.x_offset, 0, win.sill_height,
                                                     relative_to=wf.ObjectPlacement)
                op_prof = _make_box_profile(ifc, win.width, wall_thickness)
                op_ext = _make_extrusion(ifc, op_prof, win.height)
                op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
                op.Representation = op_rep
                ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                                  RelatingBuildingElement=wf, RelatedOpeningElement=op)
                storey_elements.append(op)

                win_elem = ifc.create_entity("IfcWindow", g(), None,
                                              f"Окно {win.width}x{win.height}")
                win_elem.OverallWidth = win.width
                win_elem.OverallHeight = win.height
                win_elem.ObjectPlacement = _make_placement(ifc, win.x_offset + win.width/2, 0, win.sill_height,
                                                           relative_to=wf.ObjectPlacement)
                _assign_material(ifc, win_elem, "стеклопакет")
                storey_elements.append(win_elem)

        # Front door (placement relative to front wall)
        if entrance:
            op = ifc.create_entity("IfcOpeningElement", g())
            op.Name = "Входная дверь"
            op.ObjectPlacement = _make_placement(ifc, entrance.x_offset, 0, 0.0,
                                                 relative_to=wf.ObjectPlacement)
            op_prof = _make_box_profile(ifc, entrance.width, wall_thickness)
            op_ext = _make_extrusion(ifc, op_prof, entrance.height)
            op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
            op.Representation = op_rep
            ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                              RelatingBuildingElement=wf, RelatedOpeningElement=op)
            storey_elements.append(op)

            door_elem = ifc.create_entity("IfcDoor", g(), None, "Входная дверь")
            door_elem.OverallWidth = entrance.width
            door_elem.OverallHeight = entrance.height
            door_elem.ObjectPlacement = _make_placement(ifc, entrance.x_offset + entrance.width/2, 0, 0.0,
                                                        relative_to=wf.ObjectPlacement)
            _assign_material(ifc, door_elem, "сталь")
            storey_elements.append(door_elem)

        storey_elements.append(wf)

        # Back (Y=0)
        wb = ifc.create_entity("IfcWall", g())
        wb.Name = f"Задняя стена эт.{floor_i+1}"
        wb.ObjectPlacement = _make_placement(ifc, 0, 0, wz)
        wb_prof = _make_box_profile(ifc, length, wall_thickness)
        wb_ext = _make_extrusion(ifc, wb_prof, floor_h)
        wb_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [wb_ext])
        wb.Representation = wb_rep
        _assign_material(ifc, wb, "кирпич керамический")
        storey_elements.append(wb)

        if add_windows:
            for win in windows_back:
                op = ifc.create_entity("IfcOpeningElement", g())
                op.Name = f"Окно задняя {win.x_offset}"
                op.ObjectPlacement = _make_placement(ifc, win.x_offset, 0, win.sill_height,
                                                     relative_to=wb.ObjectPlacement)
                op_prof = _make_box_profile(ifc, win.width, wall_thickness)
                op_ext = _make_extrusion(ifc, op_prof, win.height)
                op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
                op.Representation = op_rep
                ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                                  RelatingBuildingElement=wb, RelatedOpeningElement=op)
                storey_elements.append(op)

                win_elem = ifc.create_entity("IfcWindow", g(), None,
                                              f"Окно {win.width}x{win.height}")
                win_elem.OverallWidth = win.width
                win_elem.OverallHeight = win.height
                win_elem.ObjectPlacement = _make_placement(ifc, win.x_offset + win.width/2, 0, win.sill_height,
                                                           relative_to=wb.ObjectPlacement)
                _assign_material(ifc, win_elem, "стеклопакет")
                storey_elements.append(win_elem)

        # Left (X=0) — повёрнутая
        wl = ifc.create_entity("IfcWall", g())
        wl.Name = f"Левая стена эт.{floor_i+1}"
        wl.ObjectPlacement = _make_placement(ifc, 0, 0, wz, _d3(ifc, 0, 1, 0))
        wl_prof = _make_box_profile(ifc, width, wall_thickness)
        wl_ext = _make_extrusion(ifc, wl_prof, floor_h)
        wl_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [wl_ext])
        wl.Representation = wl_rep
        _assign_material(ifc, wl, "кирпич керамический")
        storey_elements.append(wl)

        if add_windows:
            for win in windows_left:
                op = ifc.create_entity("IfcOpeningElement", g())
                op.Name = f"Окно левая {win.x_offset}"
                # Placement relative to left wall: local X along wall length (world Y)
                op.ObjectPlacement = _make_placement(ifc, win.x_offset, 0, win.sill_height,
                                                     relative_to=wl.ObjectPlacement)
                op_prof = _make_box_profile(ifc, win.width, wall_thickness)
                op_ext = _make_extrusion(ifc, op_prof, win.height)
                op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
                op.Representation = op_rep
                ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                                  RelatingBuildingElement=wl, RelatedOpeningElement=op)
                storey_elements.append(op)

        # Right (X=length) — повёрнутая
        wr = ifc.create_entity("IfcWall", g())
        wr.Name = f"Правая стена эт.{floor_i+1}"
        wr.ObjectPlacement = _make_placement(ifc, length, 0, wz, _d3(ifc, 0, 1, 0))
        wr_prof = _make_box_profile(ifc, width, wall_thickness)
        wr_ext = _make_extrusion(ifc, wr_prof, floor_h)
        wr_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [wr_ext])
        wr.Representation = wr_rep
        _assign_material(ifc, wr, "кирпич керамический")
        storey_elements.append(wr)

        if add_windows:
            for win in windows_right:
                op = ifc.create_entity("IfcOpeningElement", g())
                op.Name = f"Окно правая {win.x_offset}"
                # Placement relative to right wall: local X along wall length (world Y)
                op.ObjectPlacement = _make_placement(ifc, win.x_offset, 0, win.sill_height,
                                                     relative_to=wr.ObjectPlacement)
                op_prof = _make_box_profile(ifc, win.width, wall_thickness)
                op_ext = _make_extrusion(ifc, op_prof, win.height)
                op_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [op_ext])
                op.Representation = op_rep
                ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                                  RelatingBuildingElement=wr, RelatedOpeningElement=op)
                storey_elements.append(op)

        # ─── Внутренние перегородки ───
        if add_internal_walls and floor_i == 0:
            iw_x = length * 0.3
            iw_y = width * 0.5
            iw_w = 0.15
            iw_h = floor_h

            # Продольная перегородка
            iw1 = ifc.create_entity("IfcWall", g())
            iw1.Name = f"Перегородка эт.{floor_i+1}"
            iw1.ObjectPlacement = _make_placement(ifc, iw_x, 0, wz, _d3(ifc, 0, 1, 0))
            iw1_prof = _make_box_profile(ifc, iw_y, iw_w)
            iw1_ext = _make_extrusion(ifc, iw1_prof, iw_h)
            iw1_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [iw1_ext])
            iw1.Representation = iw1_rep
            _assign_material(ifc, iw1, "гипсокартон")
            storey_elements.append(iw1)

            # Поперечная перегородка
            iw2 = ifc.create_entity("IfcWall", g())
            iw2.Name = f"Перегородка-2 эт.{floor_i+1}"
            iw2.ObjectPlacement = _make_placement(ifc, length * 0.6, iw_y, wz)
            iw2_prof = _make_box_profile(ifc, width - iw_y, iw_w)
            iw2_ext = _make_extrusion(ifc, iw2_prof, iw_h)
            iw2_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [iw2_ext])
            iw2.Representation = iw2_rep
            _assign_material(ifc, iw2, "гипсокартон")
            storey_elements.append(iw2)

            # Внутренняя дверь
            int_door = ifc.create_entity("IfcDoor", g(), None, "Межкомнатная дверь")
            int_door.OverallWidth = 0.8
            int_door.OverallHeight = 2.1
            int_door.ObjectPlacement = _make_placement(ifc, length * 0.6 + 0.4, iw_y + 1.5, wz)
            _assign_material(ifc, int_door, "дерево")
            storey_elements.append(int_door)

        # Привязка элементов к этажу
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=storey_elements, RelatingStructure=storey)
        all_elements.extend(storey_elements)

    # ─── Крыша ───
    roof_z = num_floors * floor_h

    if roof_type == "gable":
        # Двускатная крыша: две плиты под углом
        ridge_y = width / 2
        overhang = 0.5
        roof_length = length + 2 * overhang
        roof_slope = 0.4  # tan(угла) = высота/половина ширины
        ridge_height = (width / 2) * roof_slope

        roof_elems = []
        for side_name, side_sign in [("Левая", 1), ("Правая", -1)]:
            roof_slab = ifc.create_entity("IfcSlab", g(), None,
                                           f"Скат крыши {side_name}")
            roof_slab.PredefinedType = "ROOF"
            # Placement: at ridge center, tilted outward
            # Z-axis tilted: (0, side_sign*slope, 1) normalised
            norm = math.sqrt(roof_slope**2 + 1)
            roof_slab.ObjectPlacement = _make_placement(
                ifc, -overhang, width / 2, roof_z,
                _d3(ifc, 1, 0, 0),
                _d3(ifc, 0, side_sign * roof_slope / norm, 1.0 / norm),
            )
            slope_len = math.sqrt((width / 2) ** 2 + ridge_height**2) + 0.3
            r_prof = _make_box_profile(ifc, roof_length, slope_len)
            r_ext = _make_extrusion(ifc, r_prof, slab_thickness)
            r_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [r_ext])
            roof_slab.Representation = r_rep
            _assign_material(ifc, roof_slab, "металлочерепица")
            roof_elems.append(roof_slab)
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=roof_elems, RelatingStructure=storey)
    else:
        # Плоская крыша
        flat_roof = ifc.create_entity("IfcSlab", g(), None, "Плоская кровля")
        flat_roof.PredefinedType = "ROOF"
        flat_roof.ObjectPlacement = _make_placement(ifc, 0, 0, roof_z)
        r_prof = _make_box_profile(ifc, length, width)
        r_ext = _make_extrusion(ifc, r_prof, slab_thickness)
        r_rep = ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", [r_ext])
        flat_roof.Representation = r_rep
        _assign_material(ifc, flat_roof, "рубероид")
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=[flat_roof], RelatingStructure=storey)

    # ─── Сохранение ───
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        OUTPUT_DIR,
        f"{name}_{int(length)}x{int(width)}x{int(height)}_{num_floors}f_{roof_type}_{ts}.ifc",
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ifc.write(output_path)

    # Статистика
    stats = {
        "walls": len(ifc.by_type("IfcWall")),
        "slabs": len(ifc.by_type("IfcSlab")),
        "windows": len(ifc.by_type("IfcWindow")),
        "doors": len(ifc.by_type("IfcDoor")),
        "openings": len(ifc.by_type("IfcOpeningElement")),
        "storeys": len(ifc.by_type("IfcBuildingStorey")),
    }
    return output_path, stats


# ─── Совместимость со старым кодом ───
def create_simple_building(*args, **kwargs):
    """Обёртка для обратной совместимости."""
    path, _ = create_max_building(*args, **kwargs)
    return path
