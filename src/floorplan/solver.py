"""
Фаза 2 — детерминированный baseline-солвер планировки квартиры (без ML).

Топология: узкий коридор на всю глубину квартиры примыкает к стороне входа
(entry_side, граница с лестничной площадкой); остальная ширина делится на
"мокрую" зону (кухня/санузел/WC — ближе к площадке, окно не обязательно)
и жилую зону у фасада (гостиная/спальни/кухня — все требуют окна по
КМК 2.08.01-89 п.3.1).

Коридор физически граничит почти со всеми комнатами (общая грань на всю
глубину для первой комнаты в каждой зоне), что даёт связный план: каждая
комната получает дверь либо прямо в коридор, либо к соседней комнате в
своей зоне ("проходная" комната — исторически обычный приём в панельном
домостроении; здесь это осознанный компромисс baseline-солвера, который
в фазе 4 заменяется нейрогенератором с более гибкой топологией).
"""
from .ir import ApartmentFloorplan, RoomBox, DoorSpec
from .norms import get_room_constraints

FACADE_WEIGHTS = {"living": 1.3, "bedroom": 1.0, "kitchen": 0.9}
WET_WEIGHTS = {"bathroom": 1.0, "wc": 0.6}

_RU_NAMES = {
    "living": "Гостиная", "bedroom": "Спальня", "kitchen": "Кухня",
    "bathroom": "Санузел", "wc": "Туалет", "hallway": "Прихожая",
}


def _default_program(room_count: int) -> dict:
    """Программа помещений по комнатности квартиры (без прихожей — она добавляется отдельно)."""
    room_count = max(1, room_count)
    if room_count == 1:
        return {"facade": ["living", "kitchen"], "wet": ["bathroom"]}
    if room_count == 2:
        return {"facade": ["living", "bedroom", "kitchen"], "wet": ["bathroom"]}
    if room_count == 3:
        return {"facade": ["living", "bedroom", "bedroom", "kitchen"], "wet": ["bathroom", "wc"]}
    extra_bedrooms = room_count - 1
    return {"facade": ["living"] + ["bedroom"] * extra_bedrooms + ["kitchen"], "wet": ["bathroom", "wc"]}


def _split_widths(total_width: float, items: list[tuple[str, float, float]]) -> list[tuple[float, float]]:
    """items: [(type, weight, min_width)] → [(x0,x1), ...] той же длины и порядка.
    Каждому даётся хотя бы min_width; остаток распределяется по весам.
    Если total_width < суммы минимумов — минимумы масштабируются вниз
    (получившееся нарушение норм отловит validate_floorplan).
    """
    if not items:
        return []
    min_ws = [it[2] for it in items]
    weights = [it[1] for it in items]
    sum_min = sum(min_ws)
    if total_width >= sum_min:
        remaining = total_width - sum_min
        wsum = sum(weights) or 1.0
        widths = [mw + remaining * (w / wsum) for mw, w in zip(min_ws, weights)]
    else:
        scale = total_width / sum_min if sum_min > 0 else 1.0
        widths = [mw * scale for mw in min_ws]
    xs = []
    cursor = 0.0
    for w in widths:
        xs.append((cursor, cursor + w))
        cursor += w
    return xs


def _connect_adjacent_rooms(rooms: list[RoomBox], eps: float = 0.02) -> list[DoorSpec]:
    """Находит общие границы комнат и ставит дверь на каждой (связный граф доступа)."""
    doors: list[DoorSpec] = []
    n = len(rooms)
    for i in range(n):
        a = rooms[i]
        for j in range(i + 1, n):
            b = rooms[j]
            wet_pair = a.type in ("wc", "bathroom") or b.type in ("wc", "bathroom")
            dw = 0.7 if wet_pair else 0.8
            if abs(a.x1 - b.x0) < eps:
                y0, y1 = max(a.y0, b.y0), min(a.y1, b.y1)
                if y1 - y0 > eps:
                    doors.append(DoorSpec(x=a.x1, y=(y0 + y1) / 2, wall_axis="y", room_a=i, room_b=j, width=dw))
                    continue
            if abs(b.x1 - a.x0) < eps:
                y0, y1 = max(a.y0, b.y0), min(a.y1, b.y1)
                if y1 - y0 > eps:
                    doors.append(DoorSpec(x=a.x0, y=(y0 + y1) / 2, wall_axis="y", room_a=i, room_b=j, width=dw))
                    continue
            if abs(a.y1 - b.y0) < eps:
                x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
                if x1 - x0 > eps:
                    doors.append(DoorSpec(x=(x0 + x1) / 2, y=a.y1, wall_axis="x", room_a=i, room_b=j, width=dw))
                    continue
            if abs(b.y1 - a.y0) < eps:
                x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
                if x1 - x0 > eps:
                    doors.append(DoorSpec(x=(x0 + x1) / 2, y=a.y0, wall_axis="x", room_a=i, room_b=j, width=dw))
    return doors


def generate_floorplan(
    width: float,
    depth: float,
    room_count: int = 2,
    entry_side: str = "west",
    program: dict | None = None,
) -> ApartmentFloorplan:
    """
    Детерминированный baseline-генератор планировки квартиры (фаза 2, без ML).

    width/depth — внутренние (чистые) габариты квартиры в плане.
    entry_side — "west" (x=0) или "east" (x=width): сторона, смежная с
    лестничной площадкой — там будет вход.
    """
    prog = program or _default_program(room_count)
    facade_types = prog.get("facade", ["living"])
    wet_types = prog.get("wet", [])

    hallway_c = get_room_constraints("hallway")
    corridor_w = max(hallway_c["min_width"], min(1.6, width * 0.22))
    corridor_w = min(corridor_w, width * 0.4)

    rooms_band_w = max(0.5, width - corridor_w)
    rooms_x0 = corridor_w if entry_side == "west" else 0.0
    corridor_x0 = 0.0 if entry_side == "west" else width - corridor_w

    wet_c_list = [get_room_constraints(t) for t in wet_types]
    wet_depth = max((c["min_width"] for c in wet_c_list), default=0.0) if wet_types else 0.0
    facade_min = max((get_room_constraints(t)["min_width"] for t in facade_types), default=2.5)
    if wet_depth > 0 and depth - wet_depth < facade_min:
        wet_depth = max(0.0, depth - facade_min)

    rooms: list[RoomBox] = []
    hallway_idx = 0
    rooms.append(RoomBox("hallway", corridor_x0, 0.0, corridor_x0 + corridor_w, depth,
                          name=_RU_NAMES["hallway"]))

    if wet_types:
        wet_items = [(t, WET_WEIGHTS.get(t, 0.8), get_room_constraints(t)["min_width"]) for t in wet_types]
        for (t, _, _), (x0, x1) in zip(wet_items, _split_widths(rooms_band_w, wet_items)):
            rooms.append(RoomBox(t, rooms_x0 + x0, 0.0, rooms_x0 + x1, wet_depth, name=_RU_NAMES.get(t, t)))

    facade_y0 = wet_depth
    facade_items = [(t, FACADE_WEIGHTS.get(t, 1.0), get_room_constraints(t)["min_width"]) for t in facade_types]
    for (t, _, _), (x0, x1) in zip(facade_items, _split_widths(rooms_band_w, facade_items)):
        rooms.append(RoomBox(t, rooms_x0 + x0, facade_y0, rooms_x0 + x1, depth, name=_RU_NAMES.get(t, t)))

    doors = _connect_adjacent_rooms(rooms)

    entry_x = 0.0 if entry_side == "west" else width
    doors.append(DoorSpec(x=entry_x, y=depth / 2, wall_axis="y", room_a=-1, room_b=hallway_idx,
                           width=0.9, kind="entry"))

    return ApartmentFloorplan(width=width, depth=depth, entry_side=entry_side,
                               rooms=rooms, doors=doors, source="solver")
