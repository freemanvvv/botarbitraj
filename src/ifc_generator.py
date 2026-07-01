"""
Генератор IFC-заготовок — расширенная версия.
Создаёт IFC4-модель здания:
- Наружные стены с оконными/дверными проёмами + 3D-геометрия рам
- Входная дверь и межкомнатные двери
- Колонны (угловые + пролётные)
- Ригели (поперечные балки) + прогоны (продольные балки)
- Перекрытия, крыша скатная/плоская, фундамент
- Балконы, внутренние перегородки
- Лестничный марш (IfcStairFlight)
"""
import os
import math
from datetime import datetime
from .config import OUTPUT_DIR

try:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.guid
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False

from .floorplan import generate_floorplan, floorplan_to_ifc, validate_floorplan
from .floorplan.ir import WINDOW_REQUIRED_TYPES


# ─── helpers ───────────────────────────────────────────────────────────────────

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

def _placement2d(ifc, x, y):
    return ifc.create_entity("IfcAxis2Placement2D", _cp2(ifc, x, y), _d2(ifc, 1, 0))

def _make_placement(ifc, x, y, z, x_axis=None, z_axis=None, relative_to=None):
    origin = _cp3(ifc, x, y, z)
    z_dir = z_axis or _d3(ifc, 0, 0, 1)
    x_dir = x_axis or _d3(ifc, 1, 0, 0)
    a3 = ifc.create_entity("IfcAxis2Placement3D", origin, z_dir, x_dir)
    return ifc.create_entity("IfcLocalPlacement", relative_to, a3)

def _rect_profile(ifc, w, h, cx=0.0, cy=0.0):
    return ifc.create_entity("IfcRectangleProfileDef", "AREA", None,
                              _placement2d(ifc, cx, cy), float(w), float(h))

def _triangle_profile(ifc, w, h):
    """Треугольный профиль: основание w, вершина на высоте h (для фронтонов)."""
    pts = [
        _cp2(ifc, 0.0, 0.0),
        _cp2(ifc, float(w), 0.0),
        _cp2(ifc, float(w) / 2.0, float(h)),
        _cp2(ifc, 0.0, 0.0),
    ]
    poly = ifc.create_entity("IfcPolyline", pts)
    return ifc.create_entity("IfcArbitraryClosedProfileDef", "AREA", None, poly)

def _extrude(ifc, profile, depth, dir_vec=None):
    d = dir_vec or _d3(ifc, 0, 0, 1)
    pos = ifc.create_entity("IfcAxis2Placement3D",
                             _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0))
    return ifc.create_entity("IfcExtrudedAreaSolid", profile, pos, d, float(depth))

def _shape_rep(ifc, ctx, items):
    return ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", items)

def _assign_material(ifc, product, material_name: str):
    try:
        mat = ifc.create_entity("IfcMaterial", material_name)
        ifc.create_entity("IfcRelAssociatesMaterial", _g(ifc), None, None,
                          RelatedObjects=[product], RelatingMaterial=mat)
    except Exception:
        pass


# ─── Геометрия окна ────────────────────────────────────────────────────────────

def _window_geometry(ifc, ctx, w, h, wt, frame_t=0.07):
    items = []
    glass_w = max(w - 2 * frame_t, 0.1)
    glass_h = max(h - 2 * frame_t, 0.1)
    glass_prof = _rect_profile(ifc, glass_w, wt * 0.05 + 0.01, w / 2, wt / 2)
    glass_ext = ifc.create_entity("IfcExtrudedAreaSolid", glass_prof,
        ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, frame_t),
                          _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
        _d3(ifc, 0, 0, 1), float(glass_h))
    items.append(glass_ext)

    def bar(bw, bh, ox, oy, oz, depth):
        p = _rect_profile(ifc, bw, depth)
        e = ifc.create_entity("IfcExtrudedAreaSolid", p,
            ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, ox, oy, oz),
                              _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
            _d3(ifc, 0, 0, 1), float(bh))
        return e

    items.append(bar(w, frame_t, w/2, wt/2, 0, wt))
    items.append(bar(w, frame_t, w/2, wt/2, h - frame_t, wt))
    items.append(bar(frame_t, h - 2 * frame_t, frame_t/2, wt/2, frame_t, wt))
    items.append(bar(frame_t, h - 2 * frame_t, w - frame_t/2, wt/2, frame_t, wt))
    return ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", items)


# ─── Геометрия двери ───────────────────────────────────────────────────────────

def _door_geometry(ifc, ctx, w, h, wt, frame_t=0.07):
    items = []

    def bar(bw, bh, ox, oy, oz, depth):
        p = _rect_profile(ifc, bw, depth)
        e = ifc.create_entity("IfcExtrudedAreaSolid", p,
            ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, ox, oy, oz),
                              _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
            _d3(ifc, 0, 0, 1), float(bh))
        return e

    items.append(bar(w, frame_t, w/2, wt/2, h - frame_t, wt))
    items.append(bar(frame_t, h - frame_t, frame_t/2, wt/2, 0, wt))
    items.append(bar(frame_t, h - frame_t, w - frame_t/2, wt/2, 0, wt))

    panel_w = w - 2 * frame_t - 0.01
    panel_t = 0.05
    panel_prof = _rect_profile(ifc, panel_w, panel_t, w / 2, frame_t + panel_t / 2)
    panel_ext = ifc.create_entity("IfcExtrudedAreaSolid", panel_prof,
        ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, 0),
                          _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
        _d3(ifc, 0, 0, 1), float(h - frame_t - 0.01))
    items.append(panel_ext)
    return ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", items)


# ─── Проёмы: окно/дверь в стене (общие для всех генераторов) ──────────────────

def _add_window_to_wall(ifc, ctx, wall, x_local, z_local, ww, wh, wall_pl, wt):
    op = ifc.create_entity("IfcOpeningElement", _g(ifc))
    op.Name = f"Оконный проём {x_local:.1f}"
    op.ObjectPlacement = _make_placement(ifc, x_local, 0, z_local, relative_to=wall_pl)
    op_prof = _rect_profile(ifc, ww, wt)
    op.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
        [_shape_rep(ifc, ctx, [_extrude(ifc, op_prof, wh)])])
    ifc.create_entity("IfcRelVoidsElement", _g(ifc), None, None,
                      RelatingBuildingElement=wall, RelatedOpeningElement=op)
    win = ifc.create_entity("IfcWindow", _g(ifc), None, f"Окно {ww:.1f}×{wh:.1f}")
    win.OverallWidth = ww
    win.OverallHeight = wh
    win.ObjectPlacement = _make_placement(ifc, x_local, 0, z_local, relative_to=wall_pl)
    win.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
        [_window_geometry(ifc, ctx, ww, wh, wt)])
    _assign_material(ifc, win, "стеклопакет")
    ifc.create_entity("IfcRelFillsElement", _g(ifc), None, None,
                      RelatingOpeningElement=op, RelatedBuildingElement=win)
    return op, win


def _add_door_to_wall(ifc, ctx, wall, x_local, dw, dh, wl_pl, wt, door_name="Дверь"):
    op = ifc.create_entity("IfcOpeningElement", _g(ifc))
    op.Name = f"Дверной проём {door_name}"
    op.ObjectPlacement = _make_placement(ifc, x_local, 0, 0, relative_to=wl_pl)
    op_prof = _rect_profile(ifc, dw, wt)
    op.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
        [_shape_rep(ifc, ctx, [_extrude(ifc, op_prof, dh)])])
    ifc.create_entity("IfcRelVoidsElement", _g(ifc), None, None,
                      RelatingBuildingElement=wall, RelatedOpeningElement=op)
    door = ifc.create_entity("IfcDoor", _g(ifc), None, door_name)
    door.OverallWidth = dw
    door.OverallHeight = dh
    door.ObjectPlacement = _make_placement(ifc, x_local, 0, 0, relative_to=wl_pl)
    door.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
        [_door_geometry(ifc, ctx, dw, dh, wt)])
    _assign_material(ifc, door, "дерево")
    ifc.create_entity("IfcRelFillsElement", _g(ifc), None, None,
                      RelatingOpeningElement=op, RelatedBuildingElement=door)
    return op, door


def _distribute_windows(wall_span, n_windows, ww, enabled=True):
    positions = []
    if n_windows <= 0 or not enabled:
        return positions
    gap = (wall_span - n_windows * ww) / (n_windows + 1)
    if gap < 0.3:
        n_windows = max(1, int(wall_span // (ww + 0.5)))
        gap = (wall_span - n_windows * ww) / (n_windows + 1)
    for i in range(n_windows):
        positions.append(gap * (i + 1) + ww * i)
    return positions


# ─── Крыша (общая для всех генераторов) ────────────────────────────────────────
#
# Стены верхнего этажа заканчиваются на Z = height + slab_thickness (= roof_z).
# Крыша начинается ровно с этого уровня — никакого зазора.
# Геометрия — см. подробные комментарии в create_max_building (история фикса
# "крыша не на стенах стоит").

def _build_roof(ifc, ctx, length, width, height, slab_thickness, wt, roof_type):
    g = lambda: _g(ifc)
    roof_z = height + slab_thickness
    roof_elems = []

    if roof_type == "gable":
        oh_x = 0.6
        oh_y = 0.45
        slope = 0.45
        ridge_h = (width / 2) * slope
        roof_length = length + 2 * oh_x

        etr_y = width / 2 + oh_y
        sl_d = math.sqrt(etr_y ** 2 + ridge_h ** 2)

        ls = ifc.create_entity("IfcSlab", g(), None, "Левый скат кровли")
        ls.PredefinedType = "ROOF"
        ls.ObjectPlacement = _make_placement(
            ifc, -oh_x, -oh_y, roof_z,
            x_axis=_d3(ifc, 1, 0, 0),
            z_axis=_d3(ifc, 0, -ridge_h / sl_d, etr_y / sl_d),
        )
        ls_prof = _rect_profile(ifc, roof_length, sl_d, cx=roof_length/2, cy=sl_d/2)
        ls.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, ls_prof, slab_thickness)])])
        _assign_material(ifc, ls, "металлочерепица")
        roof_elems.append(ls)

        rs = ifc.create_entity("IfcSlab", g(), None, "Правый скат кровли")
        rs.PredefinedType = "ROOF"
        rs.ObjectPlacement = _make_placement(
            ifc, -oh_x, width + oh_y, roof_z,
            x_axis=_d3(ifc, 1, 0, 0),
            z_axis=_d3(ifc, 0, -ridge_h / sl_d, -etr_y / sl_d),
        )
        rs_prof = _rect_profile(ifc, roof_length, sl_d, cx=roof_length/2, cy=sl_d/2)
        rs.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, rs_prof, slab_thickness)])])
        _assign_material(ifc, rs, "металлочерепица")
        roof_elems.append(rs)

        for fx in [-wt, length]:
            fp = ifc.create_entity("IfcSlab", g(), None, f"Фронтон x={fx:.0f}")
            fp.PredefinedType = "NOTDEFINED"
            fp.ObjectPlacement = _make_placement(
                ifc, fx, 0, roof_z,
                x_axis=_d3(ifc, 0, 1, 0),
                z_axis=_d3(ifc, 1, 0, 0),
            )
            fp.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, _triangle_profile(ifc, width, ridge_h), wt)])])
            _assign_material(ifc, fp, "кирпич керамический")
            roof_elems.append(fp)

    else:
        flat = ifc.create_entity("IfcSlab", g(), None, "Плоская кровля")
        flat.PredefinedType = "ROOF"
        flat.ObjectPlacement = _make_placement(ifc, 0, 0, roof_z)
        flat.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, length, width, cx=length/2, cy=width/2), slab_thickness)])])
        _assign_material(ifc, flat, "рубероид")
        roof_elems.append(flat)

        parapet_h = 0.6
        for px, py, plen, pdir in [
            (0, 0, length, (1, 0, 0)), (0, width, length, (1, 0, 0)),
            (0, 0, width, (0, 1, 0)), (length, 0, width, (0, 1, 0)),
        ]:
            par = ifc.create_entity("IfcWall", g())
            par.Name = "Парапет"
            par.ObjectPlacement = _make_placement(ifc, px, py, roof_z + slab_thickness,
                                                   x_axis=_d3(ifc, *pdir))
            par_prof = _rect_profile(ifc, plen, wt)
            par.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, par_prof, parapet_h)])])
            _assign_material(ifc, par, "кирпич керамический")
            roof_elems.append(par)

    return roof_elems


# ─── Основная функция генерации ────────────────────────────────────────────────

def create_max_building(
    name: str = "Building",
    length: float = 15.0,
    width: float = 12.0,
    height: float = 7.0,
    num_floors: int = 2,
    floor_height: float = None,   # если задана, height = floor_height * num_floors
    wall_thickness: float = 0.4,
    slab_thickness: float = 0.2,
    roof_type: str = "gable",
    add_internal_walls: bool = True,
    add_windows: bool = True,
    add_doors: bool = True,
    add_columns: bool = True,
    add_beams: bool = True,
    add_stairs: bool = True,
    add_balconies: bool = False,
    add_foundation: bool = True,
    windows_per_wall_long: int = 3,
    windows_per_wall_short: int = 2,
    window_width: float = 1.2,
    window_height: float = 1.5,
    window_sill: float = 0.9,
    door_width: float = 0.9,
    door_height: float = 2.1,
) -> tuple:
    if not IFC_AVAILABLE:
        raise ImportError("IfcOpenShell не установлен")

    # Если задана высота этажа — пересчитываем общую высоту
    if floor_height is not None:
        height = floor_height * num_floors

    ifc = ifcopenshell.file(schema="IFC4")
    g = lambda: _g(ifc)

    # ─── Project & context ───
    proj = ifc.create_entity("IfcProject", g(), None, name)
    wcs = ifc.create_entity("IfcAxis2Placement3D",
        _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0))
    ctx = ifc.create_entity("IfcGeometricRepresentationContext",
        "Model", "Model", 3, 1e-5, wcs, None)
    ifc.create_entity("IfcGeometricRepresentationContext",
        "Plan", "Plan", 2, 1e-5, wcs, None)
    unit_m = ifc.create_entity("IfcSIUnit", None, "LENGTHUNIT", None, "METRE")
    unit_a = ifc.create_entity("IfcSIUnit", None, "AREAUNIT", None, "SQUARE_METRE")
    unit_v = ifc.create_entity("IfcSIUnit", None, "VOLUMEUNIT", None, "CUBIC_METRE")
    ifc.create_entity("IfcUnitAssignment", [unit_m, unit_a, unit_v])

    site = ifc.create_entity("IfcSite", g(), None, "Site")
    bldg = ifc.create_entity("IfcBuilding", g(), None, name)
    bldg.CompositionType = "ELEMENT"
    ifc.create_entity("IfcRelAggregates", g(), None, None, RelatingObject=proj, RelatedObjects=[site])
    ifc.create_entity("IfcRelAggregates", g(), None, None, RelatingObject=site, RelatedObjects=[bldg])

    floor_h = height / num_floors
    wt = wall_thickness

    # ─── Фундамент ────────────────────────────────────────────────────────────
    # КМК 2.02.01-98 п.4.2: ширина подошвы = толщина стены + 200 мм с каждой стороны
    if add_foundation:
        found_overhang = max(0.1, wt * 0.25)
        found_depth = 0.6  # КМК 2.02.01-98 п.4.1: ≥ 0.5 м
        found = ifc.create_entity("IfcFooting", g(), None, "Ленточный фундамент")
        found.PredefinedType = "STRIP_FOOTING"
        found.ObjectPlacement = _make_placement(ifc, -found_overhang, -found_overhang, -found_depth)
        f_prof = _rect_profile(ifc, length + 2 * found_overhang, width + 2 * found_overhang,
                                cx=(length + 2*found_overhang)/2, cy=(width + 2*found_overhang)/2)
        found.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, f_prof, found_depth)])])
        _assign_material(ifc, found, "бетон")
        found_storey = ifc.create_entity("IfcBuildingStorey", g(), None, "Фундамент")
        found_storey.Elevation = -found_depth
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[found_storey])
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=[found], RelatingStructure=found_storey)

    # ─── Вспомогательные: проём + окно/дверь (обёртки над модульными хелперами) ──
    def add_window_to_wall(wall, x_local, z_local, ww, wh, wall_pl):
        return _add_window_to_wall(ifc, ctx, wall, x_local, z_local, ww, wh, wall_pl, wt)

    def add_door_to_wall(wall, x_local, dw, dh, wl_pl, door_name="Дверь"):
        return _add_door_to_wall(ifc, ctx, wall, x_local, dw, dh, wl_pl, wt, door_name)

    def distribute_windows(wall_span, n_windows, ww):
        return _distribute_windows(wall_span, n_windows, ww, add_windows)

    # ─── Ригели и прогоны (балки) ─────────────────────────────────────────────
    # КМК 2.03.01-96 п.3.2: h_ригеля ≥ пролёт/10, ширина ≥ высота/2
    beam_h = max(0.25, min(0.60, min(length, width) / 14.0))
    beam_w = max(0.20, beam_h * 0.5)

    # Позиции колонн по X (шаг не более 6 м)
    col_xs = [0.0, float(length)]
    if length > 10.0:
        mid_x = round(length / round(length / 6.0))
        xs = [mid_x * i for i in range(1, round(length / mid_x))]
        col_xs = sorted(set([0.0, float(length)] + xs))

    col_ys = [0.0, float(width)]

    # Лестница: параметры (КМК 2.08.01-89: подъём ≤ 200 мм, ширина марша ≥ 1.05 м)
    step_rise = min(0.175, floor_h / max(8, round(floor_h / 0.175)))
    step_run_d = 0.28      # ширина проступи
    stair_w = 1.2          # ширина марша
    n_steps = max(8, round(floor_h / step_rise))
    stair_run = n_steps * step_run_d
    stair_slab_t = 0.18
    stair_x_start = length - stair_w - wt - 0.15
    stair_y_start = wt + 0.15

    # ─── Этажи ────────────────────────────────────────────────────────────────
    last_storey = None
    for floor_i in range(num_floors):
        z0 = floor_i * floor_h
        wz = z0 + slab_thickness        # стены начинаются выше перекрытия
        ceil_z = wz + floor_h           # уровень потолка (верх стен = низ ригелей)

        storey = ifc.create_entity("IfcBuildingStorey", g(), None, f"Этаж {floor_i+1}")
        storey.Elevation = float(z0)
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[storey])
        last_storey = storey
        elems = []

        # ── Перекрытие ──────────────────────────────────────────────────────
        slab = ifc.create_entity("IfcSlab", g(), None, f"Перекрытие эт.{floor_i+1}")
        slab.PredefinedType = "FLOOR"
        slab.ObjectPlacement = _make_placement(ifc, 0, 0, z0)
        sl_prof = _rect_profile(ifc, length, width, cx=length/2, cy=width/2)
        slab.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, sl_prof, slab_thickness)])])
        _assign_material(ifc, slab, "железобетон")
        elems.append(slab)

        # ── Стены: фасад и тыл по ПОЛНОЙ длине, боковые без угловых наложений ──
        #    Внутреннее пространство: X=[0..length], Y=[0..width]
        #    Фасад: Y=[width..width+wt]  Тыл: Y=[-wt..0]
        #    Лево: X=[-wt..0], Y=[wt..width-wt]  Право: X=[length..length+wt]
        inner_w = width - 2 * wt

        # Фасадная стена (Y = width, выходит наружу в +Y)
        wf = ifc.create_entity("IfcWall", g())
        wf.Name = f"Фасад эт.{floor_i+1}"
        wf.ObjectPlacement = _make_placement(ifc, 0, width, wz)
        wf_prof = _rect_profile(ifc, length, wt, cx=length/2, cy=wt/2)
        wf.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wf_prof, floor_h)])])
        _assign_material(ifc, wf, "кирпич керамический")
        elems.append(wf)

        # Входная дверь (1-й этаж, по центру фасада)
        if add_doors and floor_i == 0:
            door_x = (length - door_width) / 2
            op_d, d_elem = add_door_to_wall(wf, door_x, door_width, door_height,
                                            wf.ObjectPlacement, "Входная дверь")
            elems.extend([op_d, d_elem])

        # Окна фасада
        if add_windows:
            skip_zone = (length/2 - door_width, length/2 + door_width) if floor_i == 0 and add_doors else (0, 0)
            for xp in distribute_windows(length, windows_per_wall_long, window_width):
                if not (skip_zone[0] - 0.2 < xp < skip_zone[1] + 0.2):
                    op_w, w_elem = add_window_to_wall(wf, xp, window_sill, window_width, window_height, wf.ObjectPlacement)
                    elems.extend([op_w, w_elem])

        # Задняя стена (Y = 0, выходит наружу в -Y)
        wb = ifc.create_entity("IfcWall", g())
        wb.Name = f"Задняя стена эт.{floor_i+1}"
        wb.ObjectPlacement = _make_placement(ifc, 0, -wt, wz)
        wb_prof = _rect_profile(ifc, length, wt, cx=length/2, cy=wt/2)
        wb.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wb_prof, floor_h)])])
        _assign_material(ifc, wb, "кирпич керамический")
        elems.append(wb)

        if add_windows:
            for xp in distribute_windows(length, windows_per_wall_long, window_width):
                op_w, w_elem = add_window_to_wall(wb, xp, window_sill, window_width, window_height, wb.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # Левая стена (X = 0, повёрнута вдоль Y) — между фасадом и тылом без нахлёста
        wl = ifc.create_entity("IfcWall", g())
        wl.Name = f"Левая стена эт.{floor_i+1}"
        wl.ObjectPlacement = _make_placement(ifc, -wt, wt, wz, _d3(ifc, 0, 1, 0))
        wl_prof = _rect_profile(ifc, inner_w, wt, cx=inner_w/2, cy=wt/2)
        wl.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wl_prof, floor_h)])])
        _assign_material(ifc, wl, "кирпич керамический")
        elems.append(wl)

        if add_windows and inner_w > 2 * wt:
            for xp in distribute_windows(inner_w, windows_per_wall_short, window_width):
                op_w, w_elem = add_window_to_wall(wl, xp, window_sill, window_width, window_height, wl.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # Правая стена (X = length, повёрнута вдоль Y)
        wr = ifc.create_entity("IfcWall", g())
        wr.Name = f"Правая стена эт.{floor_i+1}"
        wr.ObjectPlacement = _make_placement(ifc, length, wt, wz, _d3(ifc, 0, 1, 0))
        wr_prof = _rect_profile(ifc, inner_w, wt, cx=inner_w/2, cy=wt/2)
        wr.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wr_prof, floor_h)])])
        _assign_material(ifc, wr, "кирпич керамический")
        elems.append(wr)

        if add_windows and inner_w > 2 * wt:
            for xp in distribute_windows(inner_w, windows_per_wall_short, window_width):
                op_w, w_elem = add_window_to_wall(wr, xp, window_sill, window_width, window_height, wr.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # ── Колонны (угловые + пролётные) ───────────────────────────────────
        if add_columns:
            col_size = max(0.3, wt)
            for cx in col_xs:
                for cy in col_ys:
                    col = ifc.create_entity("IfcColumn", g(), None,
                                            f"Колонна {cx:.0f},{cy:.0f} эт.{floor_i+1}")
                    col.PredefinedType = "COLUMN"
                    col.ObjectPlacement = _make_placement(ifc, cx - col_size/2, cy - col_size/2, wz)
                    col_prof = _rect_profile(ifc, col_size, col_size, cx=col_size/2, cy=col_size/2)
                    col.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                        [_shape_rep(ifc, ctx, [_extrude(ifc, col_prof, floor_h)])])
                    _assign_material(ifc, col, "железобетон")
                    elems.append(col)

        # ── Ригели и прогоны ─────────────────────────────────────────────────
        # КМК 2.03.01-96 п.3.2: h ≥ пролёт/10 (между колоннами), b ≥ h/2
        # Ригели (поперечные, вдоль Y между фасадом и тылом)
        #   Axis=(0,1,0), RefDir=(1,0,0) → Y_local=(0,0,-1)→-Z_world
        #   Профиль: beam_w × beam_h; Y_local: cy=beam_h/2 → world Z от ceil_z до ceil_z-beam_h
        if add_beams:
            for bx in col_xs:
                beam = ifc.create_entity("IfcBeam", g(), None,
                                         f"Ригель x={bx:.0f} эт.{floor_i+1}")
                beam.PredefinedType = "BEAM"
                beam.ObjectPlacement = _make_placement(
                    ifc, bx, 0, ceil_z,
                    x_axis=_d3(ifc, 1, 0, 0),
                    z_axis=_d3(ifc, 0, 1, 0),
                )
                b_prof = _rect_profile(ifc, beam_w, beam_h, cx=0, cy=beam_h/2)
                beam.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, b_prof, width)])])
                _assign_material(ifc, beam, "железобетон")
                elems.append(beam)

            # Прогоны (продольные, вдоль X)
            #   Axis=(1,0,0), RefDir=(0,1,0) → Y_local=(0,0,1)→+Z_world
            #   cy=-beam_h/2 → world Z от ceil_z-beam_h до ceil_z (вниз от потолка)
            for by in col_ys:
                beam = ifc.create_entity("IfcBeam", g(), None,
                                         f"Прогон y={by:.0f} эт.{floor_i+1}")
                beam.PredefinedType = "BEAM"
                beam.ObjectPlacement = _make_placement(
                    ifc, 0, by, ceil_z,
                    x_axis=_d3(ifc, 0, 1, 0),
                    z_axis=_d3(ifc, 1, 0, 0),
                )
                b_prof = _rect_profile(ifc, beam_w, beam_h, cx=0, cy=-beam_h/2)
                beam.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, b_prof, length)])])
                _assign_material(ifc, beam, "железобетон")
                elems.append(beam)

        # ── Лестничный марш ──────────────────────────────────────────────────
        # КМК 2.08.01-89: ширина марша ≥ 1.05 м, подъём ≤ 200 мм, проступь ≥ 250 мм
        if add_stairs and stair_y_start + stair_run + 0.5 < width - wt:
            sl_d = math.sqrt(stair_run**2 + floor_h**2)
            # Марш поднимается в +Y и +Z: Y_local = (0, stair_run/sl_d, floor_h/sl_d)
            # Y = Axis × RefDir → Axis = (0, -floor_h/sl_d, stair_run/sl_d)
            march = ifc.create_entity("IfcStairFlight", g(), None,
                                       f"Лестничный марш эт.{floor_i+1}")
            march.PredefinedType = "STRAIGHT"
            march.NumberOfRisers = n_steps
            march.NumberOfTreads = max(1, n_steps - 1)
            march.RiserHeight = floor_h / n_steps
            march.TreadLength = step_run_d
            march.ObjectPlacement = _make_placement(
                ifc, stair_x_start, stair_y_start, wz,
                x_axis=_d3(ifc, 1, 0, 0),
                z_axis=_d3(ifc, 0, -floor_h / sl_d, stair_run / sl_d),
            )
            march_prof = _rect_profile(ifc, stair_w, sl_d, cx=stair_w/2, cy=sl_d/2)
            march.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, march_prof, stair_slab_t)])])
            _assign_material(ifc, march, "железобетон")
            elems.append(march)

            # Площадка (landing) у верхней отметки марша
            landing_d = 1.2
            landing_y = stair_y_start + stair_run
            if landing_y + landing_d < width - wt:
                landing = ifc.create_entity("IfcSlab", g(), None,
                                             f"Площадка эт.{floor_i+1}")
                landing.PredefinedType = "LANDING"
                landing.ObjectPlacement = _make_placement(
                    ifc, stair_x_start, landing_y, wz + floor_h - slab_thickness)
                l_prof = _rect_profile(ifc, stair_w, landing_d, cx=stair_w/2, cy=landing_d/2)
                landing.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, l_prof, slab_thickness)])])
                _assign_material(ifc, landing, "железобетон")
                elems.append(landing)

        # ── Балкон ───────────────────────────────────────────────────────────
        if add_balconies and floor_i >= 1:
            bal_depth = 1.4
            bal_w = min(length * 0.45, 4.0)
            bal_x = (length - bal_w) / 2

            bal_slab = ifc.create_entity("IfcSlab", g(), None, f"Балкон эт.{floor_i+1}")
            bal_slab.PredefinedType = "FLOOR"
            bal_slab.ObjectPlacement = _make_placement(ifc, bal_x, width, z0 + slab_thickness)
            bal_prof = _rect_profile(ifc, bal_w, bal_depth, cx=bal_w/2, cy=bal_depth/2)
            bal_slab.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, bal_prof, slab_thickness)])])
            _assign_material(ifc, bal_slab, "железобетон")
            elems.append(bal_slab)

            rail_h = 1.0
            rail_t = 0.08
            for rx, ry, rlen, rdir in [
                (bal_x, width + bal_depth, bal_w, (1, 0, 0)),
                (bal_x, width, bal_depth, (0, 1, 0)),
                (bal_x + bal_w - rail_t, width, bal_depth, (0, 1, 0)),
            ]:
                rail = ifc.create_entity("IfcRailing", g(), None, f"Перила {floor_i+1}")
                rail.PredefinedType = "BALUSTRADE"
                rail.ObjectPlacement = _make_placement(ifc, rx, ry, z0 + slab_thickness * 2,
                    x_axis=_d3(ifc, *rdir))
                rl_prof = _rect_profile(ifc, rlen, rail_t)
                rail.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, rl_prof, rail_h)])])
                _assign_material(ifc, rail, "сталь")
                elems.append(rail)

        # ── Внутренние перегородки ────────────────────────────────────────────
        if add_internal_walls:
            iw_t = 0.12
            iw1 = ifc.create_entity("IfcWall", g())
            iw1.Name = f"Перегородка-1 эт.{floor_i+1}"
            iw1.ObjectPlacement = _make_placement(ifc, length * 0.35, 0, wz, _d3(ifc, 0, 1, 0))
            iw1_prof = _rect_profile(ifc, width * 0.55, iw_t)
            iw1.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, iw1_prof, floor_h - 0.1)])])
            _assign_material(ifc, iw1, "гипсокартон")
            elems.append(iw1)

            iw2 = ifc.create_entity("IfcWall", g())
            iw2.Name = f"Перегородка-2 эт.{floor_i+1}"
            iw2.ObjectPlacement = _make_placement(ifc, length * 0.65, width * 0.5, wz)
            iw2_prof = _rect_profile(ifc, length * 0.35 - wt, iw_t)
            iw2.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, iw2_prof, floor_h - 0.1)])])
            _assign_material(ifc, iw2, "гипсокартон")
            elems.append(iw2)

            if add_doors:
                op_id, id_door = add_door_to_wall(iw1, width * 0.25, 0.8, 2.0,
                                                   iw1.ObjectPlacement, "Межкомнатная дверь")
                elems.extend([op_id, id_door])

        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=elems, RelatingStructure=storey)

    # ─── Крыша ────────────────────────────────────────────────────────────────
    roof_elems = _build_roof(ifc, ctx, length, width, height, slab_thickness, wt, roof_type)

    if last_storey and roof_elems:
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=roof_elems, RelatingStructure=last_storey)

    # ─── Сохранение ──────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUTPUT_DIR,
        f"{name}_{int(length)}x{int(width)}x{int(height)}_{num_floors}f_{roof_type}_{ts}.ifc")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ifc.write(out)

    stats = {
        "walls":    len(ifc.by_type("IfcWall")),
        "slabs":    len(ifc.by_type("IfcSlab")),
        "windows":  len(ifc.by_type("IfcWindow")),
        "doors":    len(ifc.by_type("IfcDoor")),
        "columns":  len(ifc.by_type("IfcColumn")),
        "beams":    len(ifc.by_type("IfcBeam")),
        "stairs":   len(ifc.by_type("IfcStairFlight")),
        "openings": len(ifc.by_type("IfcOpeningElement")),
        "storeys":  len(ifc.by_type("IfcBuildingStorey")),
    }
    return out, stats


def _parse_dims(s, default):
    """'1.8x1.8' → (1.8, 1.8); '0.6x0.9x0.2' → (0.6, 0.9, 0.2)."""
    try:
        parts = [float(p) for p in str(s).lower().replace("х", "x").split("x")]
        return tuple(parts) if parts else default
    except Exception:
        return default


def _set_pset(ifc, product, pset_name, props: dict):
    vals = []
    for k, v in props.items():
        if isinstance(v, (int, float)):
            iv = ifc.create_entity("IfcReal", float(v)) if isinstance(v, float) else ifc.create_entity("IfcInteger", int(v))
        else:
            iv = ifc.create_entity("IfcLabel", str(v))
        vals.append(ifc.create_entity("IfcPropertySingleValue", k, None, iv, None))
    pset = ifc.create_entity("IfcPropertySet", _g(ifc), None, pset_name, None, vals)
    ifc.create_entity("IfcRelDefinesByProperties", _g(ifc), None, None,
                      RelatedObjects=[product], RelatingPropertyDefinition=pset)


def create_apartment_building(
    name: str = "Жилой комплекс",
    num_floors: int = 9,
    floor_height: float = 3.0,
    entrances: int = 1,
    apartments_per_landing: int = 2,
    apt_width: float = 8.5,
    apt_depth: float = 11.0,
    apartment_rooms: int = 2,
    floorplan_mode: str = "solver",
    llm_model: str = "local-model",
    has_elevator: bool = True,
    elevators_per_entrance: int = 1,
    elevator_capacity_kg: float = 400,
    elevator_shaft_m: str = "1.8x1.8",
    stair_width_m: float = 1.2,
    riser_shaft_m: str = "0.4x0.6",
    electrical_niche_m: str = "0.6x0.9x0.2",
    wall_thickness: float = 0.4,
    slab_thickness: float = 0.2,
    roof_type: str = "flat",
    add_windows: bool = True,
    add_doors: bool = True,
    add_columns: bool = True,
    add_beams: bool = True,
    add_foundation: bool = True,
    window_width: float = 1.2,
    window_height: float = 1.5,
    window_sill: float = 0.9,
    door_width: float = 0.9,
    door_height: float = 2.1,
) -> tuple:
    """
    Многоподъездный жилой дом: каждый подъезд — лестнично-лифтовой узел
    (марш + лифт(ы) IfcTransportElement + инженерные шахты ВК/электрощит,
    повторённые по этажам с одинаковыми X,Y) и квартиры по обе стороны
    корпуса с генеративной внутренней перегородкой (КМК 2.08.01-89 п.6.1-6.8).
    """
    if not IFC_AVAILABLE:
        raise ImportError("IfcOpenShell не установлен")

    wt = wall_thickness
    elev_w, elev_d = _parse_dims(elevator_shaft_m, (1.8, 1.8))
    riser_w, riser_d = _parse_dims(riser_shaft_m, (0.4, 0.6))
    elec_w, elec_h, elec_t = _parse_dims(electrical_niche_m, (0.6, 0.9, 0.2))
    stair_w = max(1.05, stair_width_m)
    n_elev = elevators_per_entrance if has_elevator else 0

    core_w = stair_w + 0.3 + n_elev * (elev_w + 0.2) + riser_w + 0.2
    section_w = apartments_per_landing * apt_width + core_w
    length = entrances * section_w
    width = apt_depth
    height = floor_height * num_floors

    ifc = ifcopenshell.file(schema="IFC4")
    g = lambda: _g(ifc)

    proj = ifc.create_entity("IfcProject", g(), None, name)
    wcs = ifc.create_entity("IfcAxis2Placement3D",
        _cp3(ifc, 0, 0, 0), _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0))
    ctx = ifc.create_entity("IfcGeometricRepresentationContext",
        "Model", "Model", 3, 1e-5, wcs, None)
    ifc.create_entity("IfcGeometricRepresentationContext",
        "Plan", "Plan", 2, 1e-5, wcs, None)
    unit_m = ifc.create_entity("IfcSIUnit", None, "LENGTHUNIT", None, "METRE")
    unit_a = ifc.create_entity("IfcSIUnit", None, "AREAUNIT", None, "SQUARE_METRE")
    unit_v = ifc.create_entity("IfcSIUnit", None, "VOLUMEUNIT", None, "CUBIC_METRE")
    ifc.create_entity("IfcUnitAssignment", [unit_m, unit_a, unit_v])

    site = ifc.create_entity("IfcSite", g(), None, "Site")
    bldg = ifc.create_entity("IfcBuilding", g(), None, name)
    bldg.CompositionType = "ELEMENT"
    ifc.create_entity("IfcRelAggregates", g(), None, None, RelatingObject=proj, RelatedObjects=[site])
    ifc.create_entity("IfcRelAggregates", g(), None, None, RelatingObject=site, RelatedObjects=[bldg])

    # ─── Фундамент (КМК 2.02.01-98 п.4.1-4.2) ──────────────────────────────────
    if add_foundation:
        found_overhang = max(0.1, wt * 0.25)
        found_depth = 0.6
        found = ifc.create_entity("IfcFooting", g(), None, "Ленточный фундамент")
        found.PredefinedType = "STRIP_FOOTING"
        found.ObjectPlacement = _make_placement(ifc, -found_overhang, -found_overhang, -found_depth)
        f_prof = _rect_profile(ifc, length + 2 * found_overhang, width + 2 * found_overhang,
                                cx=(length + 2*found_overhang)/2, cy=(width + 2*found_overhang)/2)
        found.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, f_prof, found_depth)])])
        _assign_material(ifc, found, "бетон")
        found_storey = ifc.create_entity("IfcBuildingStorey", g(), None, "Фундамент")
        found_storey.Elevation = -found_depth
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[found_storey])
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=[found], RelatingStructure=found_storey)

    # ─── Колонны / балки: сетка по длине корпуса (шаг ≤ 6 м) + торцы ───────────
    beam_h = max(0.25, min(0.60, min(length, width) / 14.0))
    beam_w = max(0.20, beam_h * 0.5)
    col_xs = sorted(set([0.0, float(length)] + [i * section_w for i in range(1, entrances)]))
    if length / max(1, len(col_xs) - 1) > 6.0:
        step = length / round(length / 6.0)
        col_xs = sorted(set(col_xs + [round(step * i, 2) for i in range(1, round(length / step))]))
    col_ys = [0.0, float(width)]

    # Стена-разделитель шахта/лестница внутри core (КМК 2.08.01-89 п.6.7)
    stair_run_d = 0.28 * max(8, round(floor_height / 0.175))

    # ── Генеративная планировка квартиры (Путь C, фазы 0-2, 4) ──────────────
    # Одна и та же планировка переиспользуется для всех этажей/секций —
    # квартиры одного типа (side) одинаковы; строится один раз, проверяется
    # по нормам один раз, а IFC-элементы штампуются per-инстанс в floorplan_to_ifc.
    #
    # floorplan_mode="neural": планировку сначала пытается сгенерировать
    # локальная LLM (LM Studio, см. src/floorplan/neural.py) — та же
    # RAG-проверка по нормам решает, принять её или откатиться на
    # детерминированный солвер. Если LLM недоступна/вернула мусор — тихий
    # и безопасный fallback на generate_floorplan(), здание всегда соберётся.
    apt_inner_depth = max(1.0, width - 2 * wt)
    floorplan_sources = {}
    if floorplan_mode == "neural":
        from .floorplan import generate_floorplan_llm
        fp_west = generate_floorplan_llm(width=apt_width, depth=apt_inner_depth,
                                          room_count=apartment_rooms, entry_side="west",
                                          model=llm_model) or \
            generate_floorplan(width=apt_width, depth=apt_inner_depth,
                                room_count=apartment_rooms, entry_side="west")
        fp_east = generate_floorplan_llm(width=apt_width, depth=apt_inner_depth,
                                          room_count=apartment_rooms, entry_side="east",
                                          model=llm_model) or \
            generate_floorplan(width=apt_width, depth=apt_inner_depth,
                                room_count=apartment_rooms, entry_side="east")
    else:
        fp_west = generate_floorplan(width=apt_width, depth=apt_inner_depth,
                                      room_count=apartment_rooms, entry_side="west")
        fp_east = generate_floorplan(width=apt_width, depth=apt_inner_depth,
                                      room_count=apartment_rooms, entry_side="east")
    floorplan_sources = {"west": fp_west.source, "east": fp_east.source}
    floorplan_issues = validate_floorplan(fp_west) + validate_floorplan(fp_east)

    last_storey = None
    for floor_i in range(num_floors):
        z0 = floor_i * floor_height
        wz = z0 + slab_thickness
        ceil_z = wz + floor_height

        storey = ifc.create_entity("IfcBuildingStorey", g(), None, f"Этаж {floor_i+1}")
        storey.Elevation = float(z0)
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[storey])
        last_storey = storey
        elems = []

        # ── Перекрытие на весь корпус ───────────────────────────────────────
        slab = ifc.create_entity("IfcSlab", g(), None, f"Перекрытие эт.{floor_i+1}")
        slab.PredefinedType = "FLOOR"
        slab.ObjectPlacement = _make_placement(ifc, 0, 0, z0)
        sl_prof = _rect_profile(ifc, length, width, cx=length/2, cy=width/2)
        slab.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, sl_prof, slab_thickness)])])
        _assign_material(ifc, slab, "железобетон")
        elems.append(slab)

        # ── Наружные стены (фасад/тыл на всю длину, торцы по краям) ─────────
        inner_w = width - 2 * wt
        wf = ifc.create_entity("IfcWall", g(), None, f"Фасад эт.{floor_i+1}")
        wf.ObjectPlacement = _make_placement(ifc, 0, width, wz)
        wf.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, length, wt, cx=length/2, cy=wt/2), floor_height)])])
        _assign_material(ifc, wf, "кирпич керамический")
        elems.append(wf)

        wb = ifc.create_entity("IfcWall", g(), None, f"Задняя стена эт.{floor_i+1}")
        wb.ObjectPlacement = _make_placement(ifc, 0, -wt, wz)
        wb.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, length, wt, cx=length/2, cy=wt/2), floor_height)])])
        _assign_material(ifc, wb, "кирпич керамический")
        elems.append(wb)

        wl = ifc.create_entity("IfcWall", g(), None, f"Торец слева эт.{floor_i+1}")
        wl.ObjectPlacement = _make_placement(ifc, -wt, wt, wz, _d3(ifc, 0, 1, 0))
        wl.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, inner_w, wt, cx=inner_w/2, cy=wt/2), floor_height)])])
        _assign_material(ifc, wl, "кирпич керамический")
        elems.append(wl)

        wr = ifc.create_entity("IfcWall", g(), None, f"Торец справа эт.{floor_i+1}")
        wr.ObjectPlacement = _make_placement(ifc, length, wt, wz, _d3(ifc, 0, 1, 0))
        wr.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, inner_w, wt, cx=inner_w/2, cy=wt/2), floor_height)])])
        _assign_material(ifc, wr, "кирпич керамический")
        elems.append(wr)

        # ── Колонны и ригели/прогоны ─────────────────────────────────────────
        if add_columns:
            col_size = max(0.3, wt)
            for cx in col_xs:
                for cy in col_ys:
                    col = ifc.create_entity("IfcColumn", g(), None, f"Колонна {cx:.0f},{cy:.0f} эт.{floor_i+1}")
                    col.PredefinedType = "COLUMN"
                    col.ObjectPlacement = _make_placement(ifc, cx - col_size/2, cy - col_size/2, wz)
                    col.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                        [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, col_size, col_size, cx=col_size/2, cy=col_size/2), floor_height)])])
                    _assign_material(ifc, col, "железобетон")
                    elems.append(col)

        if add_beams:
            for bx in col_xs:
                beam = ifc.create_entity("IfcBeam", g(), None, f"Ригель x={bx:.0f} эт.{floor_i+1}")
                beam.PredefinedType = "BEAM"
                beam.ObjectPlacement = _make_placement(ifc, bx, 0, ceil_z, x_axis=_d3(ifc, 1, 0, 0), z_axis=_d3(ifc, 0, 1, 0))
                beam.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, beam_w, beam_h, cx=0, cy=beam_h/2), width)])])
                _assign_material(ifc, beam, "железобетон")
                elems.append(beam)
            for by in col_ys:
                beam = ifc.create_entity("IfcBeam", g(), None, f"Прогон y={by:.0f} эт.{floor_i+1}")
                beam.PredefinedType = "BEAM"
                beam.ObjectPlacement = _make_placement(ifc, 0, by, ceil_z, x_axis=_d3(ifc, 0, 1, 0), z_axis=_d3(ifc, 1, 0, 0))
                beam.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, beam_w, beam_h, cx=0, cy=-beam_h/2), length)])])
                _assign_material(ifc, beam, "железобетон")
                elems.append(beam)

        # ── Подъезды: лестнично-лифтовой узел + квартиры по обе стороны ──────
        for sec_i in range(entrances):
            sx0 = sec_i * section_w
            core_x0 = sx0 + (apartments_per_landing // 2) * apt_width
            n_left = apartments_per_landing // 2
            n_right = apartments_per_landing - n_left

            # Стены, отделяющие core от квартир и подъезд от подъезда
            core_walls_by_x = {}
            party_xs = [sx0] if sec_i > 0 else []
            for px in [core_x0, core_x0 + core_w] + party_xs:
                if px <= 0.01 or px >= length - 0.01:
                    continue
                pw = ifc.create_entity("IfcWall", g(), None, f"Стена x={px:.1f} эт.{floor_i+1}")
                pw.ObjectPlacement = _make_placement(ifc, px, wt, wz, _d3(ifc, 0, 1, 0))
                pw.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, inner_w, wt * 0.5, cx=inner_w/2, cy=wt*0.25), floor_height)])])
                _assign_material(ifc, pw, "кирпич керамический")
                elems.append(pw)
                core_walls_by_x[round(px, 3)] = pw

            # Входная дверь в подъезд (1-й этаж, по центру фасада core)
            if add_doors and floor_i == 0:
                door_x = core_x0 + core_w / 2 - door_width / 2
                op_d, d_elem = _add_door_to_wall(ifc, ctx, wf, door_x, door_width, door_height, wf.ObjectPlacement, wt, f"Вход в подъезд {sec_i+1}")
                elems.extend([op_d, d_elem])

            # ── Лестничный марш core ────────────────────────────────────────
            step_rise = min(0.175, floor_height / max(8, round(floor_height / 0.175)))
            n_steps = max(8, round(floor_height / step_rise))
            stair_run = n_steps * 0.28
            stair_x = core_x0 + 0.15
            stair_y = wt + 0.15
            if stair_y + stair_run + 0.5 < width - wt:
                sl_d = math.sqrt(stair_run**2 + floor_height**2)
                march = ifc.create_entity("IfcStairFlight", g(), None, f"Марш подъезд {sec_i+1} эт.{floor_i+1}")
                march.PredefinedType = "STRAIGHT"
                march.NumberOfRisers = n_steps
                march.NumberOfTreads = max(1, n_steps - 1)
                march.RiserHeight = floor_height / n_steps
                march.TreadLength = 0.28
                march.ObjectPlacement = _make_placement(
                    ifc, stair_x, stair_y, wz,
                    x_axis=_d3(ifc, 1, 0, 0),
                    z_axis=_d3(ifc, 0, -floor_height / sl_d, stair_run / sl_d),
                )
                march.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, stair_w, sl_d, cx=stair_w/2, cy=sl_d/2), 0.18)])])
                _assign_material(ifc, march, "железобетон")
                elems.append(march)

                landing_y = stair_y + stair_run
                if landing_y + 1.2 < width - wt:
                    landing = ifc.create_entity("IfcSlab", g(), None, f"Площадка подъезд {sec_i+1} эт.{floor_i+1}")
                    landing.PredefinedType = "LANDING"
                    landing.ObjectPlacement = _make_placement(ifc, stair_x, landing_y, wz + floor_height - slab_thickness)
                    landing.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                        [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, stair_w, 1.2, cx=stair_w/2, cy=0.6), slab_thickness)])])
                    _assign_material(ifc, landing, "железобетон")
                    elems.append(landing)

            # ── Лифт(ы): шахта + кабина IfcTransportElement (КМК 2.08.01-89 п.6.1-6.3) ──
            if has_elevator:
                for ei in range(n_elev):
                    ex = stair_x + stair_w + 0.3 + ei * (elev_w + 0.2)
                    ey = wt + 0.15
                    shaft_wall_t = 0.2
                    for wx, wy, wlen, wdir in [
                        (ex - shaft_wall_t, ey, elev_d, (0, 1, 0)),
                        (ex + elev_w, ey, elev_d, (0, 1, 0)),
                        (ex, ey + elev_d, elev_w, (1, 0, 0)),
                    ]:
                        sw = ifc.create_entity("IfcWall", g(), None, f"Стена шахты лифта {sec_i+1}.{ei+1} эт.{floor_i+1}")
                        sw.ObjectPlacement = _make_placement(ifc, wx, wy, wz, x_axis=_d3(ifc, *wdir))
                        sw.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, wlen, shaft_wall_t), floor_height)])])
                        _assign_material(ifc, sw, "железобетон")
                        elems.append(sw)

                    if floor_i == 0:
                        cabin = ifc.create_entity("IfcTransportElement", g(), None, f"Лифт {sec_i+1}.{ei+1}")
                        cabin.PredefinedType = "ELEVATOR"
                        cabin.ObjectPlacement = _make_placement(ifc, ex, ey, slab_thickness)
                        cab_w, cab_d, cab_h = elev_w * 0.85, elev_d * 0.85, 2.1
                        cabin.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, cab_w, cab_d), height)])])
                        _assign_material(ifc, cabin, "сталь")
                        _set_pset(ifc, cabin, "Pset_ElevatorCapacity", {
                            "RatedLoad_kg": elevator_capacity_kg,
                            "CabinWidth_m": round(cab_w, 2),
                            "CabinDepth_m": round(cab_d, 2),
                        })
                        elems.append(cabin)

            # ── Инженерные шахты: ВК + электрощит, по одной на этаж (КМК) ───
            riser_x = core_x0 + core_w - riser_w - 0.15
            riser_y = width - wt - riser_d - 0.15
            riser = ifc.create_entity("IfcFlowSegment", g(), None, f"Шахта ВК подъезд {sec_i+1} эт.{floor_i+1}")
            riser.ObjectPlacement = _make_placement(ifc, riser_x, riser_y, wz)
            riser.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, riser_w, riser_d), floor_height)])])
            _assign_material(ifc, riser, "чугун")
            elems.append(riser)

            elec_x = core_x0 + 0.1
            elec_y = width - wt - elec_t - 0.15
            elec = ifc.create_entity("IfcBuildingElementProxy", g(), None, f"Электрощит подъезд {sec_i+1} эт.{floor_i+1}")
            elec.PredefinedType = "USERDEFINED"
            elec.ObjectPlacement = _make_placement(ifc, elec_x, elec_y, wz)
            elec.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, elec_w, elec_t), elec_h)])])
            _assign_material(ifc, elec, "сталь")
            elems.append(elec)

            # ── Квартиры слева и справа от core ──────────────────────────────
            apt_idx = 1
            for side, n_side in [("left", n_left), ("right", n_right)]:
                for k in range(n_side):
                    if side == "left":
                        ax0 = core_x0 - (k + 1) * apt_width
                    else:
                        ax0 = core_x0 + core_w + k * apt_width

                    # перегородка между квартирами (кроме крайней внешней стены,
                    # которая уже построена отдельно как core-стена pw)
                    apw = None
                    if (side == "left" and k < n_side) or (side == "right" and k > 0):
                        px = ax0 if side == "right" else ax0 + apt_width
                        if 0.01 < px < length - 0.01 and abs(px - core_x0) > 0.01 and abs(px - (core_x0 + core_w)) > 0.01:
                            apw = ifc.create_entity("IfcWall", g(), None, f"Межквартирная стена x={px:.1f} эт.{floor_i+1}")
                            apw.ObjectPlacement = _make_placement(ifc, px, wt, wz, _d3(ifc, 0, 1, 0))
                            apw.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                                [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, inner_w, 0.2, cx=inner_w/2, cy=0.1), floor_height)])])
                            _assign_material(ifc, apw, "кирпич керамический")
                            elems.append(apw)

                    # ── Генеративная планировка квартиры (Путь C, фазы 0-2) ──────
                    # Готовый ApartmentFloorplan (общий для всех квартир этой
                    # стороны здания) размещается по месту: внутренние
                    # перегородки/двери/IfcSpace строит floorplan_to_ifc().
                    entry_side = "east" if side == "left" else "west"
                    px_entry = ax0 + apt_width if side == "left" else ax0
                    entry_wall = core_walls_by_x.get(round(px_entry, 3)) or apw

                    fp = fp_east if entry_side == "east" else fp_west
                    apt_elems = floorplan_to_ifc(
                        ifc, ctx, fp, ox=ax0, oy=wt, wz=wz,
                        floor_height=floor_height, door_height=door_height,
                        name_prefix=f"Кв.{apt_idx} п{sec_i+1} эт{floor_i+1}: ",
                    )
                    elems.extend(apt_elems)

                    # Входная дверь квартиры — пробивается в уже построенной
                    # core/межквартирной стене со стороны площадки (entry_side).
                    if add_doors and entry_wall is not None:
                        entry_door = next((d for d in fp.doors if d.kind == "entry"), None)
                        if entry_door is not None:
                            op_d, d_elem = _add_door_to_wall(
                                ifc, ctx, entry_wall, entry_door.y, entry_door.width, door_height,
                                entry_wall.ObjectPlacement, wt, f"Вход в кв.{apt_idx} эт.{floor_i+1}",
                            )
                            elems.extend([op_d, d_elem])

                    # окна квартиры — по фасадным комнатам плана, а не равномерно
                    # по всей ширине (окно там, где реально жилая комната/кухня)
                    if add_windows:
                        for room in fp.rooms:
                            if room.type not in WINDOW_REQUIRED_TYPES:
                                continue
                            if not room.touches_facade(fp.depth):
                                continue
                            n_win = max(1, round(room.width / 3.0))
                            for xp in _distribute_windows(room.width, n_win, window_width, True):
                                op_w, w_elem = _add_window_to_wall(
                                    ifc, ctx, wf, ax0 + room.x0 + xp, window_sill, window_width,
                                    window_height, wf.ObjectPlacement, wt,
                                )
                                elems.extend([op_w, w_elem])
                    apt_idx += 1

        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=elems, RelatingStructure=storey)

    # ─── Крыша на весь корпус ────────────────────────────────────────────────
    roof_elems = _build_roof(ifc, ctx, length, width, height, slab_thickness, wt, roof_type)
    if last_storey and roof_elems:
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=roof_elems, RelatingStructure=last_storey)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUTPUT_DIR,
        f"{name}_{entrances}sec_{apartments_per_landing}apt_{num_floors}f_{ts}.ifc")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ifc.write(out)

    stats = {
        "walls":     len(ifc.by_type("IfcWall")),
        "slabs":     len(ifc.by_type("IfcSlab")),
        "windows":   len(ifc.by_type("IfcWindow")),
        "doors":     len(ifc.by_type("IfcDoor")),
        "columns":   len(ifc.by_type("IfcColumn")),
        "beams":     len(ifc.by_type("IfcBeam")),
        "stairs":    len(ifc.by_type("IfcStairFlight")),
        "elevators": len(ifc.by_type("IfcTransportElement")),
        "openings":  len(ifc.by_type("IfcOpeningElement")),
        "storeys":   len(ifc.by_type("IfcBuildingStorey")),
        "entrances": entrances,
        "apartments": entrances * apartments_per_landing * num_floors,
        "spaces":    len(ifc.by_type("IfcSpace")),
        "floorplan_issues": floorplan_issues,
        "floorplan_sources": floorplan_sources,
    }
    return out, stats


def create_simple_building(*args, **kwargs):
    path, _ = create_max_building(*args, **kwargs)
    return path
