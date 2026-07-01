"""
Фаза 1 — нормы КМК/ШНК как машиночитаемые ограничения на комнату.

Числа взяты из src/normbase/norms_knowledge.py (КМК 2.08.01-89 «Жилые здания»,
раздел 2-4) — той же базы, которую использует LLM-архитектор и validator.py.
Здесь она применяется не как текст для LLM, а как детерминированная проверка
уже сгенерированной планировки (солвером или нейросетью) — своего рода
"RAG-констрейнты", вычисленные один раз и переиспользуемые в каждой проверке.
"""
from .ir import ApartmentFloorplan, RoomBox, WINDOW_REQUIRED_TYPES

# КМК 2.08.01-89 п.2.2-2.5 — минимальные площади/ширины по типу помещения
ROOM_CONSTRAINTS: dict[str, dict] = {
    "living":   {"min_area": 8.0, "min_width": 2.5, "needs_window": True,  "norm_ref": "КМК 2.08.01-89 п.2.2"},
    "bedroom":  {"min_area": 8.0, "min_width": 2.5, "needs_window": True,  "norm_ref": "КМК 2.08.01-89 п.2.2"},
    "kitchen":  {"min_area": 8.0, "min_width": 1.7, "needs_window": True,  "norm_ref": "КМК 2.08.01-89 п.2.3"},
    "bathroom": {"min_area": 2.7, "min_width": 1.2, "needs_window": False, "norm_ref": "КМК 2.08.01-89 п.2.5"},
    "wc":       {"min_area": 1.2, "min_width": 0.8, "needs_window": False, "norm_ref": "КМК 2.08.01-89 п.2.5"},
    "hallway":  {"min_area": 1.8, "min_width": 1.4, "needs_window": False, "norm_ref": "КМК 2.08.01-89 п.2.4"},
}

# КМК 2.08.01-89 п.4.1-4.3 — двери
ENTRY_DOOR_MIN_WIDTH = 0.9    # п.4.1
INTERIOR_DOOR_MIN_WIDTH = 0.8  # п.4.2
WC_DOOR_MIN_WIDTH = 0.7        # п.4.3

# Световой коэффициент 1:8 (КМК 2.08.01-89 п.3.1) — минимальная площадь окна
LIGHT_COEFFICIENT = 8.0


def get_room_constraints(room_type: str) -> dict:
    """Возвращает нормативные ограничения для типа комнаты (с фолбэком)."""
    return ROOM_CONSTRAINTS.get(room_type, {
        "min_area": 4.0, "min_width": 1.4, "needs_window": False,
        "norm_ref": "КМК 2.08.01-89 (общее требование)",
    })


def _issue(severity: str, room: RoomBox | None, message: str) -> dict:
    return {
        "severity": severity,
        "element_type": "Room" if room else "Floorplan",
        "element_name": (room.name or room.type) if room else "—",
        "message": message,
    }


def validate_floorplan(fp: ApartmentFloorplan) -> list[dict]:
    """
    Проверяет планировку квартиры на соответствие КМК 2.08.01-89.
    Возвращает список issues в том же формате, что integrity_checker.
    """
    issues: list[dict] = []

    if not fp.rooms:
        issues.append(_issue("error", None, "Планировка не содержит ни одной комнаты."))
        return issues

    for room in fp.rooms:
        c = get_room_constraints(room.type)

        if room.area < c["min_area"] - 0.05:
            issues.append(_issue(
                "error", room,
                f"Площадь {room.area:.1f} м² < минимума {c['min_area']} м² ({c['norm_ref']})."
            ))

        if room.min_side < c["min_width"] - 0.02:
            issues.append(_issue(
                "error", room,
                f"Ширина {room.min_side:.2f} м < минимума {c['min_width']} м ({c['norm_ref']})."
            ))

        if c["needs_window"] and not room.touches_facade(fp.depth):
            issues.append(_issue(
                "error", room,
                f"Комната типа «{room.type}» не примыкает к фасаду — нет окна "
                f"(КМК 2.08.01-89 п.3.1, световой коэффициент 1:8)."
            ))

    # Проверка перекрытий (комнаты не должны пересекаться)
    for i, a in enumerate(fp.rooms):
        for b in fp.rooms[i + 1:]:
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ox > 0.02 and oy > 0.02:
                issues.append(_issue(
                    "error", a,
                    f"Комната «{a.type}» пересекается с «{b.type}» "
                    f"(наложение {ox:.2f}×{oy:.2f} м)."
                ))

    # Проверка, что комнаты не выходят за пределы footprint
    for room in fp.rooms:
        if room.x0 < -0.02 or room.y0 < -0.02 or room.x1 > fp.width + 0.02 or room.y1 > fp.depth + 0.02:
            issues.append(_issue(
                "error", room,
                f"Комната «{room.type}» выходит за границы квартиры "
                f"({room.x0:.2f},{room.y0:.2f})-({room.x1:.2f},{room.y1:.2f}) "
                f"при габаритах {fp.width:.2f}×{fp.depth:.2f} м."
            ))

    # Каждая комната (кроме прихожей) должна иметь хотя бы одну дверь
    connected = {d.room_a for d in fp.doors} | {d.room_b for d in fp.doors}
    for i, room in enumerate(fp.rooms):
        if i not in connected:
            issues.append(_issue(
                "error", room,
                f"Комната «{room.type}» не имеет двери — нет доступа."
            ))

    # Должен быть ровно один вход с площадки
    entry_doors = [d for d in fp.doors if d.kind == "entry"]
    if not entry_doors:
        issues.append(_issue("error", None, "В квартире нет входной двери с лестничной площадки."))
    else:
        for d in entry_doors:
            if d.width < ENTRY_DOOR_MIN_WIDTH - 0.01:
                issues.append(_issue(
                    "error", None,
                    f"Входная дверь {d.width:.2f} м < {ENTRY_DOOR_MIN_WIDTH} м (КМК 2.08.01-89 п.4.1)."
                ))

    for d in fp.doors:
        if d.kind == "interior":
            room_a = fp.room(d.room_a) if d.room_a >= 0 else None
            room_b = fp.room(d.room_b) if d.room_b >= 0 else None
            is_wet = (room_a is not None and room_a.type in ("wc", "bathroom")) or \
                     (room_b is not None and room_b.type in ("wc", "bathroom"))
            min_w = WC_DOOR_MIN_WIDTH if is_wet else INTERIOR_DOOR_MIN_WIDTH
            if d.width < min_w - 0.01:
                ref = "КМК 2.08.01-89 п.4.3" if is_wet else "КМК 2.08.01-89 п.4.2"
                issues.append(_issue(
                    "warning", None,
                    f"Межкомнатная дверь {d.width:.2f} м < {min_w} м ({ref})."
                ))

    return issues
