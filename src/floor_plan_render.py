"""
Дименсированный SVG-план этажа "как настоящий проект" — подписи комнат,
площади, стены/двери/окна — для превью в новом мастере «Моделирование»
(частный дом / многоквартирный дом) до генерации 3D-модели.

Строится поверх src/svg_plans.py::SVGPlanGenerator (примитивы отрисовки уже
там есть — подписи площадей, легенда, — но раньше он был подключён только к
CLI (src/main.py:_cmd_plan), не к веб-бэкенду). Здесь — два адаптера,
переводящие два разных внутренних представления плана в вызовы генератора:

- render_storey_svg() — src/bim_agents/contracts.py (BuildingProgram/
  StoreyPlan) — используется для «частного сектора».
- render_apartment_floor_svg() — src/floorplan/ir.py (ApartmentFloorplan) —
  используется для «многоквартирного дома», переиспользует
  src.floorplan.to_ifc._collect_partition_walls для сегментов перегородок
  (та же геометрия, что идёт в IFC).
"""
import math

from src.svg_plans import SVGPlanGenerator


def _bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _point_along_wall(axis: list[list[float]], offset_m: float) -> tuple[float, float]:
    (x1, y1), (x2, y2) = axis
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1e-6:
        return x1, y1
    t = max(0.0, min(1.0, offset_m / length))
    return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t


def render_storey_svg(program, storey, title: str) -> str:
    """program: bim_agents.contracts.BuildingProgram, storey: StoreyPlan."""
    gen = SVGPlanGenerator(scale=40)
    rooms_by_id = {r.id: r for r in program.rooms}

    for rp in storey.rooms:
        x, y, w, h = _bbox(rp.polygon)
        meta = rooms_by_id.get(rp.id)
        name = meta.name if meta else rp.id
        # Полигон передаём только для непрямоугольных комнат (Г-образные
        # шаблоны датасета, >4 вершин) — для обычных rect-комнат bbox уже
        # даёт идентичный результат, полигон не нужен.
        poly = rp.polygon if len(rp.polygon) > 4 else None
        gen.add_room(name, x, y, w, h, polygon=poly)

    for wp in storey.walls:
        (x1, y1), (x2, y2) = wp.axis
        gen.add_wall(x1, y1, x2, y2, thickness=wp.thickness_m, loadbearing=(wp.type == "exterior"))

    wall_by_id = {w.id: w for w in storey.walls}
    for op in storey.openings:
        wall = wall_by_id.get(op.wall)
        if not wall:
            continue
        x, y = _point_along_wall(wall.axis, op.offset_m + op.width_m / 2)
        (wx1, wy1), (wx2, wy2) = wall.axis
        horizontal = abs(wy1 - wy2) < 1e-3
        if op.kind == "door":
            gen.add_door(x, y, width=op.width_m, horizontal=horizontal)
        else:
            gen.add_window(x, y, width=op.width_m, horizontal=horizontal)

    return gen.generate(title=title)


def render_apartment_floor_svg(fp, title: str) -> str:
    """fp: src.floorplan.ir.ApartmentFloorplan (прямоугольные комнаты — солвер/LLM)."""
    from src.floorplan.to_ifc import _collect_partition_walls

    gen = SVGPlanGenerator(scale=40)
    gen.add_outer_walls(fp.width, fp.depth, thickness=0.3)

    for room in fp.rooms:
        gen.add_room(room.name or room.type, room.x0, room.y0, room.width, room.depth)

    for p0, p1, _ra, _rb in _collect_partition_walls(fp):
        gen.add_wall(p0[0], p0[1], p1[0], p1[1], thickness=0.1, loadbearing=False)

    for d in fp.doors:
        if d.kind == "entry":
            x = 0.0 if fp.entry_side == "west" else fp.width
            gen.add_door(x, d.y, width=d.width, horizontal=False)
        else:
            gen.add_door(d.x, d.y, width=d.width, horizontal=(d.wall_axis == "x"))

    for room in fp.rooms:
        if room.touches_facade(fp.depth):
            gen.add_window(room.cx, fp.depth, width=min(1.5, room.width * 0.6), horizontal=True)

    return gen.generate(title=title)
