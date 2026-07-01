"""
Общая вычислительная геометрия для планировок — единая логика для
прямоугольных комнат (солвер/LLM) и произвольных полигонов (после
векторизации растра ChatHouseDiffusion, см. vectorize.py).

connect_adjacent_rooms() обобщает то, что раньше делал только
solver._connect_adjacent_rooms() для прямоугольников: находит общие
границы между комнатами и ставит дверь на каждой — для полигонов это
работает через поиск коллинеарных перекрывающихся рёбер вместо сравнения
координат bounding box'ов. Для прямоугольных комнат даёт тот же результат,
что и старая box-only реализация (просто через более общий путь).
"""
import math

from .ir import RoomBox, DoorSpec

Point = tuple[float, float]
Edge = tuple[Point, Point]


def room_edges(room: RoomBox) -> list[Edge]:
    """Рёбра контура комнаты по порядку обхода (замкнутый контур)."""
    if room.polygon:
        pts = room.polygon
    else:
        pts = [(room.x0, room.y0), (room.x1, room.y0), (room.x1, room.y1), (room.x0, room.y1)]
    n = len(pts)
    return [(pts[i], pts[(i + 1) % n]) for i in range(n)]


def collinear_overlap(e1: Edge, e2: Edge, eps: float = 0.02) -> Edge | None:
    """
    Если два отрезка лежат на одной прямой и перекрываются по длине —
    возвращает общий подотрезок (p0, p1), иначе None.
    Работает для отрезков любого угла (не только осевых) — на прямоугольных
    комнатах ведёт себя как обычное сравнение сторон.
    """
    (ax0, ay0), (ax1, ay1) = e1
    (bx0, by0), (bx1, by1) = e2
    dax, day = ax1 - ax0, ay1 - ay0
    dbx, dby = bx1 - bx0, by1 - by0
    len_a = math.hypot(dax, day)
    len_b = math.hypot(dbx, dby)
    if len_a < eps or len_b < eps:
        return None

    cross = dax * dby - day * dbx
    if abs(cross) / (len_a * len_b) > 0.02:
        return None  # не параллельны

    # перпендикулярное расстояние от начала B до прямой A должно быть ~0
    nx, ny = -day / len_a, dax / len_a
    if abs((bx0 - ax0) * nx + (by0 - ay0) * ny) > eps:
        return None  # параллельны, но на разных прямых

    ux, uy = dax / len_a, day / len_a

    def proj(px: float, py: float) -> float:
        return (px - ax0) * ux + (py - ay0) * uy

    b0, b1 = sorted([proj(bx0, by0), proj(bx1, by1)])
    lo, hi = max(0.0, b0), min(len_a, b1)
    if hi - lo < eps:
        return None

    p0 = (ax0 + ux * lo, ay0 + uy * lo)
    p1 = (ax0 + ux * hi, ay0 + uy * hi)
    return (p0, p1)


def connect_adjacent_rooms(rooms: list[RoomBox], eps: float = 0.02) -> list[DoorSpec]:
    """Находит общие границы между комнатами (боксы или полигоны) и ставит
    дверь на каждой — даёт связный граф доступа между всеми комнатами."""
    doors: list[DoorSpec] = []
    n = len(rooms)
    for i in range(n):
        edges_a = room_edges(rooms[i])
        for j in range(i + 1, n):
            wet_pair = rooms[i].type in ("wc", "bathroom") or rooms[j].type in ("wc", "bathroom")
            dw = 0.7 if wet_pair else 0.8
            edges_b = room_edges(rooms[j])
            found = None
            for ea in edges_a:
                for eb in edges_b:
                    seg = collinear_overlap(ea, eb, eps)
                    if seg:
                        found = seg
                        break
                if found:
                    break
            if not found:
                continue
            (px0, py0), (px1, py1) = found
            mx, my = (px0 + px1) / 2, (py0 + py1) / 2
            axis = "x" if abs(py1 - py0) < abs(px1 - px0) else "y"
            doors.append(DoorSpec(x=mx, y=my, wall_axis=axis, room_a=i, room_b=j, width=dw))
    return doors


def polygons_intersect(a: RoomBox, b: RoomBox) -> bool:
    """Точная проверка пересечения (не просто bounding box) — используется,
    когда хотя бы одна из комнат имеет произвольный полигон, для которого
    пересечение bbox'ов не означает пересечение самих фигур (например,
    два L-образных помещения, соприкасающихся только в bbox-углу)."""
    if not a.polygon and not b.polygon:
        # обе — прямоугольники: обычная проверка пересечения интервалов
        ox = min(a.x1, b.x1) - max(a.x0, b.x0)
        oy = min(a.y1, b.y1) - max(a.y0, b.y0)
        return ox > 0.02 and oy > 0.02

    from shapely.geometry import Polygon, box

    def _poly(r: RoomBox):
        pts = r.polygon if r.polygon else [
            (r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1),
        ]
        return Polygon(pts)

    try:
        pa, pb = _poly(a), _poly(b)
        if not pa.is_valid:
            pa = pa.buffer(0)
        if not pb.is_valid:
            pb = pb.buffer(0)
        return pa.intersects(pb) and pa.intersection(pb).area > 1e-4
    except Exception:
        # деградация к bbox-проверке, если геометрия вырожденная
        ox = min(a.x1, b.x1) - max(a.x0, b.x0)
        oy = min(a.y1, b.y1) - max(a.y0, b.y0)
        return ox > 0.02 and oy > 0.02
