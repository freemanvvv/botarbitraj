"""
Генератор IFC-заготовок — расширенная версия.
Создаёт IFC4-модель здания:
- Наружные стены с оконными/дверными проёмами + 3D-геометрия рам
- Входная дверь и межкомнатные двери с геометрией панели
- Колонны (угловые + пролётные)
- Перекрытия, крыша (скатная/плоская), фундамент
- Балконы
- Внутренние перегородки
- Строительные материалы
"""
import os
import math
from datetime import datetime
from dataclasses import dataclass
from .config import OUTPUT_DIR

try:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.guid
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False


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
    """
    3D-тело окна: внешняя рама + стекло.
    Система координат: X = ширина окна, Y = толщина стены, Z = высота.
    Происхождение — нижний-левый угол проёма.
    """
    items = []
    # Стекло: тонкая пластина посередине по Y, с отступом frame_t от краёв
    glass_w = max(w - 2 * frame_t, 0.1)
    glass_h = max(h - 2 * frame_t, 0.1)
    glass_cx = w / 2
    glass_cy = wt / 2
    glass_prof = _rect_profile(ifc, glass_w, wt * 0.05 + 0.01, glass_cx, glass_cy)
    glass_ext = ifc.create_entity("IfcExtrudedAreaSolid", glass_prof,
        ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, frame_t),
                          _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
        _d3(ifc, 0, 0, 1), float(glass_h))
    items.append(glass_ext)

    # Рама: 4 прямоугольных бруса (низ, верх, лево, право)
    def bar(bw, bh, ox, oy, oz, depth):
        p = _rect_profile(ifc, bw, depth)
        e = ifc.create_entity("IfcExtrudedAreaSolid", p,
            ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, ox, oy, oz),
                              _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
            _d3(ifc, 0, 0, 1), float(bh))
        return e

    # Низ
    items.append(bar(w, frame_t, w/2, wt/2, 0, wt))
    # Верх
    items.append(bar(w, frame_t, w/2, wt/2, h - frame_t, wt))
    # Лево
    items.append(bar(frame_t, h - 2 * frame_t, frame_t/2, wt/2, frame_t, wt))
    # Право
    items.append(bar(frame_t, h - 2 * frame_t, w - frame_t/2, wt/2, frame_t, wt))

    return ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", items)


# ─── Геометрия двери ───────────────────────────────────────────────────────────

def _door_geometry(ifc, ctx, w, h, wt, frame_t=0.07):
    """Дверная рама (3 бруса без порога) + дверная панель."""
    items = []

    def bar(bw, bh, ox, oy, oz, depth):
        p = _rect_profile(ifc, bw, depth)
        e = ifc.create_entity("IfcExtrudedAreaSolid", p,
            ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, ox, oy, oz),
                              _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
            _d3(ifc, 0, 0, 1), float(bh))
        return e

    # Верхняя перемычка
    items.append(bar(w, frame_t, w/2, wt/2, h - frame_t, wt))
    # Лево
    items.append(bar(frame_t, h - frame_t, frame_t/2, wt/2, 0, wt))
    # Право
    items.append(bar(frame_t, h - frame_t, w - frame_t/2, wt/2, 0, wt))

    # Дверная панель (закрытая, сдвинута к внутренней стороне стены)
    panel_w = w - 2 * frame_t - 0.01
    panel_t = 0.05  # 5 см панель
    panel_cx = w / 2
    panel_cy = frame_t + panel_t / 2
    panel_prof = _rect_profile(ifc, panel_w, panel_t, panel_cx, panel_cy)
    panel_ext = ifc.create_entity("IfcExtrudedAreaSolid", panel_prof,
        ifc.create_entity("IfcAxis2Placement3D", _cp3(ifc, 0, 0, 0),
                          _d3(ifc, 0, 0, 1), _d3(ifc, 1, 0, 0)),
        _d3(ifc, 0, 0, 1), float(h - frame_t - 0.01))
    items.append(panel_ext)

    return ifc.create_entity("IfcShapeRepresentation", ctx, "Body", "SweptSolid", items)


# ─── Основная функция генерации ────────────────────────────────────────────────

def create_max_building(
    name: str = "Building",
    length: float = 15.0,
    width: float = 12.0,
    height: float = 7.0,
    num_floors: int = 2,
    wall_thickness: float = 0.4,
    slab_thickness: float = 0.2,
    roof_type: str = "gable",
    add_internal_walls: bool = True,
    add_windows: bool = True,
    add_doors: bool = True,
    add_columns: bool = True,
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
    if add_foundation:
        found_overhang = 0.5
        found_depth = 0.6
        found = ifc.create_entity("IfcFooting", g(), None, "Фундамент")
        found.PredefinedType = "STRIP_FOOTING"
        found.ObjectPlacement = _make_placement(ifc, -found_overhang, -found_overhang, -found_depth)
        f_prof = _rect_profile(ifc, length + 2 * found_overhang, width + 2 * found_overhang)
        found.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, f_prof, found_depth)])])
        _assign_material(ifc, found, "бетон")
        found_storey = ifc.create_entity("IfcBuildingStorey", g(), None, "Фундамент")
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[found_storey])
        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=[found], RelatingStructure=found_storey)

    # ─── Вспомогательная: добавить проём + окно к стене ───────────────────────
    def add_window_to_wall(wall, x_local, z_local, ww, wh, wall_pl):
        """x_local, z_local — начало проёма в лок. системе стены."""
        # Проём
        op = ifc.create_entity("IfcOpeningElement", g())
        op.Name = f"Оконный проём {x_local:.1f}"
        op.ObjectPlacement = _make_placement(ifc, x_local, 0, z_local, relative_to=wall_pl)
        op_prof = _rect_profile(ifc, ww, wt)
        op.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, op_prof, wh)])])
        ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                          RelatingBuildingElement=wall, RelatedOpeningElement=op)
        # Окно с 3D геометрией
        win = ifc.create_entity("IfcWindow", g(), None, f"Окно {ww:.1f}×{wh:.1f}")
        win.OverallWidth = ww
        win.OverallHeight = wh
        win.ObjectPlacement = _make_placement(ifc, x_local, 0, z_local, relative_to=wall_pl)
        win.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_window_geometry(ifc, ctx, ww, wh, wt)])
        _assign_material(ifc, win, "стеклопакет")
        ifc.create_entity("IfcRelFillsElement", g(), None, None,
                          RelatingOpeningElement=op, RelatedBuildingElement=win)
        return op, win

    def add_door_to_wall(wall, x_local, dw, dh, wl_pl, door_name="Дверь"):
        op = ifc.create_entity("IfcOpeningElement", g())
        op.Name = f"Дверной проём {door_name}"
        op.ObjectPlacement = _make_placement(ifc, x_local, 0, 0, relative_to=wl_pl)
        op_prof = _rect_profile(ifc, dw, wt)
        op.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, op_prof, dh)])])
        ifc.create_entity("IfcRelVoidsElement", g(), None, None,
                          RelatingBuildingElement=wall, RelatedOpeningElement=op)
        door = ifc.create_entity("IfcDoor", g(), None, door_name)
        door.OverallWidth = dw
        door.OverallHeight = dh
        door.ObjectPlacement = _make_placement(ifc, x_local, 0, 0, relative_to=wl_pl)
        door.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_door_geometry(ifc, ctx, dw, dh, wt)])
        _assign_material(ifc, door, "дерево")
        ifc.create_entity("IfcRelFillsElement", g(), None, None,
                          RelatingOpeningElement=op, RelatedBuildingElement=door)
        return op, door

    def distribute_windows(wall_span, n_windows, ww):
        """Равномерно расставить n окон шириной ww по пролёту wall_span."""
        positions = []
        if n_windows <= 0 or not add_windows:
            return positions
        gap = (wall_span - n_windows * ww) / (n_windows + 1)
        if gap < 0.3:
            n_windows = max(1, int(wall_span // (ww + 0.5)))
            gap = (wall_span - n_windows * ww) / (n_windows + 1)
        for i in range(n_windows):
            positions.append(gap * (i + 1) + ww * i)
        return positions

    # ─── Этажи ────────────────────────────────────────────────────────────────
    last_storey = None
    for floor_i in range(num_floors):
        z0 = floor_i * floor_h
        wz = z0 + slab_thickness
        storey = ifc.create_entity("IfcBuildingStorey", g(), None, f"Этаж {floor_i+1}")
        ifc.create_entity("IfcRelAggregates", g(), None, None,
                          RelatingObject=bldg, RelatedObjects=[storey])
        last_storey = storey
        elems = []

        # Перекрытие
        slab = ifc.create_entity("IfcSlab", g(), None, f"Перекрытие эт.{floor_i+1}")
        slab.PredefinedType = "FLOOR"
        slab.ObjectPlacement = _make_placement(ifc, 0, 0, z0)
        sl_prof = _rect_profile(ifc, length, width)
        slab.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, sl_prof, slab_thickness)])])
        _assign_material(ifc, slab, "железобетон")
        elems.append(slab)

        # ── Фасадная стена (Y = width) ──
        wf = ifc.create_entity("IfcWall", g())
        wf.Name = f"Фасад эт.{floor_i+1}"
        wf.ObjectPlacement = _make_placement(ifc, 0, width, wz)
        wf_prof = _rect_profile(ifc, length, wt)
        wf.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wf_prof, floor_h)])])
        _assign_material(ifc, wf, "кирпич керамический")
        elems.append(wf)

        # Входная дверь (только 1-й этаж, по центру фасада)
        if add_doors and floor_i == 0:
            door_x = max(0.3, (length - door_width) / 2)
            op_d, d_elem = add_door_to_wall(wf, door_x, door_width, door_height,
                                            wf.ObjectPlacement, "Входная дверь")
            elems.extend([op_d, d_elem])

        # Окна фасада (оставляем место для двери на 1 этаже)
        if add_windows:
            skip_zone = (length / 2 - door_width, length / 2 + door_width) if floor_i == 0 and add_doors else (0, 0)
            xpositions = distribute_windows(length, windows_per_wall_long, window_width)
            for xp in xpositions:
                if not (skip_zone[0] - 0.2 < xp < skip_zone[1] + 0.2):
                    op_w, w_elem = add_window_to_wall(wf, xp, window_sill, window_width, window_height, wf.ObjectPlacement)
                    elems.extend([op_w, w_elem])

        # ── Задняя стена (Y = 0) ──
        wb = ifc.create_entity("IfcWall", g())
        wb.Name = f"Задняя стена эт.{floor_i+1}"
        wb.ObjectPlacement = _make_placement(ifc, 0, 0, wz)
        wb_prof = _rect_profile(ifc, length, wt)
        wb.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wb_prof, floor_h)])])
        _assign_material(ifc, wb, "кирпич керамический")
        elems.append(wb)

        if add_windows:
            for xp in distribute_windows(length, windows_per_wall_long, window_width):
                op_w, w_elem = add_window_to_wall(wb, xp, window_sill, window_width, window_height, wb.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # ── Левая стена (X = 0, повёрнута вдоль Y) ──
        wl = ifc.create_entity("IfcWall", g())
        wl.Name = f"Левая стена эт.{floor_i+1}"
        wl.ObjectPlacement = _make_placement(ifc, 0, 0, wz, _d3(ifc, 0, 1, 0))
        wl_prof = _rect_profile(ifc, width, wt)
        wl.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wl_prof, floor_h)])])
        _assign_material(ifc, wl, "кирпич керамический")
        elems.append(wl)

        if add_windows:
            for xp in distribute_windows(width, windows_per_wall_short, window_width):
                op_w, w_elem = add_window_to_wall(wl, xp, window_sill, window_width, window_height, wl.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # ── Правая стена (X = length, повёрнута вдоль Y) ──
        wr = ifc.create_entity("IfcWall", g())
        wr.Name = f"Правая стена эт.{floor_i+1}"
        wr.ObjectPlacement = _make_placement(ifc, length, 0, wz, _d3(ifc, 0, 1, 0))
        wr_prof = _rect_profile(ifc, width, wt)
        wr.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, wr_prof, floor_h)])])
        _assign_material(ifc, wr, "кирпич керамический")
        elems.append(wr)

        if add_windows:
            for xp in distribute_windows(width, windows_per_wall_short, window_width):
                op_w, w_elem = add_window_to_wall(wr, xp, window_sill, window_width, window_height, wr.ObjectPlacement)
                elems.extend([op_w, w_elem])

        # ── Колонны ──────────────────────────────────────────────────────────
        if add_columns:
            col_size = 0.3
            col_positions = [
                (0, 0), (length, 0), (0, width), (length, width),
            ]
            # Промежуточные колонны для длинных пролётов
            if length > 12:
                for mx in [length / 2]:
                    col_positions += [(mx, 0), (mx, width)]
            if width > 10:
                for my in [width / 2]:
                    col_positions += [(0, my), (length, my)]

            for cx, cy in col_positions:
                col = ifc.create_entity("IfcColumn", g(), None,
                                        f"Колонна {cx:.0f},{cy:.0f} эт.{floor_i+1}")
                col.PredefinedType = "COLUMN"
                col.ObjectPlacement = _make_placement(ifc, cx - col_size/2, cy - col_size/2, wz)
                col_prof = _rect_profile(ifc, col_size, col_size)
                col.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, col_prof, floor_h)])])
                _assign_material(ifc, col, "железобетон")
                elems.append(col)

        # ── Балкон (со 2-го этажа, на фасаде) ───────────────────────────────
        if add_balconies and floor_i >= 1:
            bal_depth = 1.4
            bal_w = min(length * 0.45, 4.0)
            bal_x = (length - bal_w) / 2
            bal_z = z0 + slab_thickness

            bal_slab = ifc.create_entity("IfcSlab", g(), None, f"Балкон эт.{floor_i+1}")
            bal_slab.PredefinedType = "FLOOR"
            bal_slab.ObjectPlacement = _make_placement(ifc, bal_x, width, bal_z)
            bal_prof = _rect_profile(ifc, bal_w, bal_depth)
            bal_slab.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, bal_prof, slab_thickness)])])
            _assign_material(ifc, bal_slab, "железобетон")
            elems.append(bal_slab)

            # Ограждение балкона: 3 стороны (фронт + 2 боковые)
            rail_h = 1.0
            rail_t = 0.08
            for rx, ry, rlen, rdir in [
                (bal_x, width + bal_depth, bal_w, (1,0,0)),       # перед
                (bal_x, width, rail_h, (0,1,0)),                    # лево
                (bal_x + bal_w - rail_t, width, bal_depth, (0,1,0)), # право (ширина=глубина балкона)
            ]:
                rail = ifc.create_entity("IfcRailing", g(), None, f"Перила балкона {floor_i+1}")
                rail.PredefinedType = "BALUSTRADE"
                rail.ObjectPlacement = _make_placement(ifc, rx, ry, bal_z + slab_thickness,
                    x_axis=_d3(ifc, *rdir))
                rl_prof = _rect_profile(ifc, rlen, rail_t)
                rail.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                    [_shape_rep(ifc, ctx, [_extrude(ifc, rl_prof, rail_h)])])
                _assign_material(ifc, rail, "сталь")
                elems.append(rail)

        # ── Внутренние перегородки ────────────────────────────────────────────
        if add_internal_walls:
            iw_t = 0.12
            # Продольная перегородка
            iw1 = ifc.create_entity("IfcWall", g())
            iw1.Name = f"Перегородка-1 эт.{floor_i+1}"
            iw1_x = length * 0.35
            iw1.ObjectPlacement = _make_placement(ifc, iw1_x, 0, wz, _d3(ifc, 0, 1, 0))
            iw1_prof = _rect_profile(ifc, width * 0.55, iw_t)
            iw1.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, iw1_prof, floor_h - 0.1)])])
            _assign_material(ifc, iw1, "гипсокартон")
            elems.append(iw1)

            # Поперечная перегородка
            iw2 = ifc.create_entity("IfcWall", g())
            iw2.Name = f"Перегородка-2 эт.{floor_i+1}"
            iw2.ObjectPlacement = _make_placement(ifc, length * 0.65, width * 0.5, wz)
            iw2_prof = _rect_profile(ifc, length * 0.35 - wt, iw_t)
            iw2.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, iw2_prof, floor_h - 0.1)])])
            _assign_material(ifc, iw2, "гипсокартон")
            elems.append(iw2)

            # Межкомнатная дверь
            if add_doors:
                op_id, id_door = add_door_to_wall(iw1, width * 0.25, 0.8, 2.0,
                                                   iw1.ObjectPlacement, "Межкомнатная дверь")
                elems.extend([op_id, id_door])

        ifc.create_entity("IfcRelContainedInSpatialStructure", g(), None, None,
                          RelatedElements=elems, RelatingStructure=storey)

    # ─── Крыша ────────────────────────────────────────────────────────────────
    roof_z = num_floors * floor_h
    roof_elems = []

    if roof_type == "gable":
        overhang = 0.6
        roof_length = length + 2 * overhang
        slope = 0.45
        ridge_h = (width / 2) * slope
        norm = math.sqrt(slope**2 + 1)

        for side_name, side_sign in [("Левый скат", 1), ("Правый скат", -1)]:
            rs = ifc.create_entity("IfcSlab", g(), None, side_name)
            rs.PredefinedType = "ROOF"
            rs.ObjectPlacement = _make_placement(
                ifc, -overhang, width / 2, roof_z,
                _d3(ifc, 1, 0, 0),
                _d3(ifc, 0, side_sign * slope / norm, 1.0 / norm),
            )
            slope_len = math.sqrt((width / 2) ** 2 + ridge_h ** 2) + 0.4
            rs_prof = _rect_profile(ifc, roof_length, slope_len)
            rs.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, rs_prof, slab_thickness)])])
            _assign_material(ifc, rs, "металлочерепица")
            roof_elems.append(rs)

        # Фронтоны (торцевые треугольники) — как вертикальные плиты
        for fx, fdir in [(0, _d3(ifc, -1, 0, 0)), (length, _d3(ifc, 1, 0, 0))]:
            fp = ifc.create_entity("IfcSlab", g(), None, f"Фронтон {fx:.0f}")
            fp.PredefinedType = "NOTDEFINED"
            fp.ObjectPlacement = _make_placement(ifc, fx, 0, roof_z,
                                                  x_axis=_d3(ifc, 0, 1, 0))
            fp_prof = _rect_profile(ifc, width, wt)
            fp.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
                [_shape_rep(ifc, ctx, [_extrude(ifc, fp_prof, ridge_h)])])
            _assign_material(ifc, fp, "кирпич керамический")
            roof_elems.append(fp)
    else:
        # Плоская крыша с парапетом
        flat = ifc.create_entity("IfcSlab", g(), None, "Плоская кровля")
        flat.PredefinedType = "ROOF"
        flat.ObjectPlacement = _make_placement(ifc, 0, 0, roof_z)
        flat.Representation = ifc.create_entity("IfcProductDefinitionShape", None, None,
            [_shape_rep(ifc, ctx, [_extrude(ifc, _rect_profile(ifc, length, width), slab_thickness)])])
        _assign_material(ifc, flat, "рубероид")
        roof_elems.append(flat)

        # Парапет (4 стороны)
        parapet_h = 0.6
        for px, py, plen, pdir in [
            (0, 0, length, (1,0,0)), (0, width, length, (1,0,0)),
            (0, 0, width, (0,1,0)), (length, 0, width, (0,1,0)),
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
        "openings": len(ifc.by_type("IfcOpeningElement")),
        "storeys":  len(ifc.by_type("IfcBuildingStorey")),
    }
    return out, stats


def create_simple_building(*args, **kwargs):
    path, _ = create_max_building(*args, **kwargs)
    return path
