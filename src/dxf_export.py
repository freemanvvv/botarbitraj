"""
Экспорт этажей house-плана в DXF — «как настоящий проект» для САПР
(AutoCAD/nanoCAD/LibreCAD/BricsCAD): контуры комнат, тела стен, проёмы,
редактируемые РАЗМЕРНЫЕ ЛИНИИ (DIMENSION) по периметру, подписи комнат с
площадями и таблица-экспликация помещений.

Это тот самый «Шаг В: Auto-dimensioning» из связки AI+BIM, только полностью
в Python (ezdxf), без Revit/ArchiCAD и лицензий: на вход — тот же FloorPlan
(полигоны/оси/проёмы в метрах), что идёт в IFC и в веб-SVG.

Единицы DXF — миллиметры (координаты×1000), как принято в стройке.
"""
from __future__ import annotations

import ezdxf

M = 1000.0  # метры → миллиметры


def _area_m2(poly: list[list[float]]) -> float:
    """Площадь простого многоугольника (формула Гаусса), полигон в метрах."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def _centroid(poly: list[list[float]]) -> tuple[float, float]:
    n = len(poly)
    return sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n


def _unique_axis(vals: list[float], tol: float = 0.02) -> list[float]:
    """Уникальные координаты (для осей размерных цепочек), метры."""
    out: list[float] = []
    for v in sorted(vals):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
    return out


def _point_along(axis, offset_m: float) -> tuple[float, float]:
    (x1, y1), (x2, y2) = axis
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 or 1e-6
    t = max(0.0, min(1.0, offset_m / length))
    return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t


def _setup_dimstyle(doc):
    ds = doc.dimstyles.get("Standard")
    ds.dxf.dimtxt = 180      # высота текста размера, мм
    ds.dxf.dimasz = 180      # размер засечки/стрелки
    ds.dxf.dimexe = 120      # вынос за размерную линию
    ds.dxf.dimexo = 120      # отступ выносной от объекта
    ds.dxf.dimdec = 0        # без десятичных (мм целые)
    ds.dxf.dimlunit = 2


def export_floorplan_dxf(program, floor_plan, path: str) -> str:
    """program: bim_agents.contracts.BuildingProgram, floor_plan: FloorPlan.
    Пишет DXF со всеми этажами (каждый смещён по X) и возвращает путь."""
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for name, color in (("ROOMS", 8), ("WALLS", 7), ("OPENINGS", 5),
                        ("DIMS", 1), ("TEXT", 3), ("TABLE", 4)):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    _setup_dimstyle(doc)

    names = {r.id: r.name for r in program.rooms}
    x_cursor = 0.0  # смещение текущего этажа по X (мм)

    for storey in floor_plan.storeys:
        rooms = [rp for rp in storey.rooms if rp.polygon]
        if not rooms:
            continue

        xs = [p[0] for rp in rooms for p in rp.polygon]
        ys = [p[1] for rp in rooms for p in rp.polygon]
        minx, miny = min(xs), min(ys)
        span_x = (max(xs) - minx)

        # сдвиг: этаж к началу координат + курсор по X (мм)
        def T(pt):
            return ((pt[0] - minx) * M + x_cursor, (pt[1] - miny) * M)

        # ── комнаты: контур + подпись (имя, площадь) ──
        for rp in rooms:
            pts = [T(p) for p in rp.polygon]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "ROOMS"})
            cx, cy = _centroid(rp.polygon)
            tx, ty = T((cx, cy))
            label = names.get(rp.id, rp.id)
            area = _area_m2(rp.polygon)
            msp.add_text(label, height=200, dxfattribs={"layer": "TEXT"}).set_placement(
                (tx, ty + 130), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
            msp.add_text(f"{area:.1f} m2", height=150, dxfattribs={"layer": "TEXT"}).set_placement(
                (tx, ty - 130), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

        # ── стены: тело как полилиния постоянной ширины ──
        for w in storey.walls:
            p0, p1 = T(w.axis[0]), T(w.axis[1])
            pl = msp.add_lwpolyline([p0, p1], dxfattribs={"layer": "WALLS"})
            pl.dxf.const_width = w.thickness_m * M

        # ── проёмы: окно — линия, дверь — линия + дуга открывания ──
        wall_by_id = {w.id: w for w in storey.walls}
        for op in storey.openings:
            w = wall_by_id.get(op.wall)
            if not w:
                continue
            c = _point_along(w.axis, op.offset_m + op.width_m / 2)
            (wx1, wy1), (wx2, wy2) = w.axis
            horiz = abs(wy1 - wy2) < 1e-3
            half = op.width_m / 2
            if horiz:
                a, b = (c[0] - half, c[1]), (c[0] + half, c[1])
            else:
                a, b = (c[0], c[1] - half), (c[0], c[1] + half)
            msp.add_line(T(a), T(b), dxfattribs={"layer": "OPENINGS"})
            if op.kind == "door":
                ta = T(a)
                msp.add_arc(center=ta, radius=op.width_m * M,
                            start_angle=0, end_angle=90, dxfattribs={"layer": "OPENINGS"})

        # ── размерные цепочки (редактируемые DIMENSION) по низу и слева ──
        gx = _unique_axis([w.axis[0][0] for w in storey.walls if abs(w.axis[0][0] - w.axis[1][0]) < 1e-3]
                          or [p[0] for rp in rooms for p in rp.polygon])
        gy = _unique_axis([w.axis[0][1] for w in storey.walls if abs(w.axis[0][1] - w.axis[1][1]) < 1e-3]
                          or [p[1] for rp in rooms for p in rp.polygon])
        dim_y = -900.0   # линия размеров ниже плана, мм
        for i in range(len(gx) - 1):
            p1 = ((gx[i] - minx) * M + x_cursor, 0)
            p2 = ((gx[i + 1] - minx) * M + x_cursor, 0)
            msp.add_linear_dim(base=(0, dim_y), p1=p1, p2=p2, dxfattribs={"layer": "DIMS"}).render()
        dim_x = -900.0
        for j in range(len(gy) - 1):
            p1 = (x_cursor, (gy[j] - miny) * M)
            p2 = (x_cursor, (gy[j + 1] - miny) * M)
            msp.add_linear_dim(base=(x_cursor + dim_x, 0), p1=p1, p2=p2, angle=90,
                               dxfattribs={"layer": "DIMS"}).render()

        # ── экспликация помещений (таблица № / помещение / S) справа ──
        _draw_schedule(msp, storey, names, x_cursor + span_x * M + 1500,
                       (max(ys) - miny) * M)

        x_cursor += span_x * M + 6000  # следующий этаж правее с зазором 6 м

    doc.saveas(path)
    return path


def _draw_schedule(msp, storey, names, x0: float, y0: float):
    """Простая таблица-экспликация: № | Помещение | S, м²."""
    rows = [("№", "Помещение", "S, m2")]
    for i, rp in enumerate(storey.rooms, 1):
        if not rp.polygon:
            continue
        rows.append((str(i), names.get(rp.id, rp.id), f"{_area_m2(rp.polygon):.1f}"))
    rh, col = 500.0, (700.0, 3500.0, 1500.0)  # высота строки, ширины колонок (мм)
    total_w = sum(col)
    for r, row in enumerate(rows):
        y = y0 - r * rh
        msp.add_line((x0, y), (x0 + total_w, y), dxfattribs={"layer": "TABLE"})
        cx = x0
        for c, cell in enumerate(row):
            msp.add_text(cell, height=180, dxfattribs={"layer": "TABLE"}).set_placement(
                (cx + 100, y - rh / 2), align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)
            cx += col[c]
    # рамка
    bottom = y0 - len(rows) * rh
    msp.add_line((x0, y0), (x0, bottom), dxfattribs={"layer": "TABLE"})
    msp.add_line((x0 + total_w, y0), (x0 + total_w, bottom), dxfattribs={"layer": "TABLE"})
    msp.add_line((x0, bottom), (x0 + total_w, bottom), dxfattribs={"layer": "TABLE"})
    cx = x0
    for w in col[:-1]:
        cx += w
        msp.add_line((cx, y0), (cx, bottom), dxfattribs={"layer": "TABLE"})
