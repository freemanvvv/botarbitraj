"""
Проверка норм КМК/ШНК для house-движка (src/bim_agents/, "частный сектор").

У bim_agents.contracts (BuildingProgram/FloorPlan) сейчас НЕТ никакой
нормо-проверки — это подтверждённый пробел (см. PLAN/аудит: "Быстрый эскиз"
работает свободно, без норм). Апартаментный движок (src/floorplan/) уже
строго проверяет по нормам через свою собственную ROOM_CONSTRAINTS/
validate_floorplan — этот модуль переиспользует те же числа
(src.floorplan.norms.get_room_constraints), не дублируя их, но работает
поверх ДРУГОЙ структуры данных: свободный текстовый Room.type
("IfcSpace:LIVING" и т.п.) вместо строгого перечисления, и RoomPlan.polygon
вместо RoomBox.

Возвращает issues в том же словарном формате, что и
src/floorplan/norms.py / src/integrity_checker.py
({severity, element_type, element_name, message}), чтобы фронт мог
рендерить обе панели одинаково.
"""
from src.floorplan.norms import get_room_constraints

# Ключевые слова (RU + фрагменты IFC-типа) → канонические категории норм
# (те же, что в src.floorplan.ir.ROOM_TYPES). "Детская" сознательно не
# заведена отдельным типом норм — ограничения как у спальни (та же норма
# КМК 2.08.01-89 п.2.2 на жилую комнату), но исходное имя комнаты сохраняется
# как есть в отчёте пользователю.
_KEYWORDS: list[tuple[str, str]] = [
    ("kitchen", "kitchen"), ("кухня", "kitchen"),
    ("bathroom", "bathroom"), ("ванная", "bathroom"), ("санузел", "bathroom"),
    ("wc", "wc"), ("туалет", "wc"), ("уборная", "wc"),
    ("hallway", "hallway"), ("entrance", "hallway"), ("прихожая", "hallway"), ("коридор", "hallway"),
    ("bedroom", "bedroom"), ("спальня", "bedroom"), ("детская", "bedroom"), ("child", "bedroom"),
    ("living", "living"), ("гостиная", "living"), ("зал", "living"),
]


def normalize_room_type(type_str: str, name: str = "") -> str:
    """Своб. Room.type/name → каноническая категория норм (living/bedroom/
    kitchen/bathroom/wc/hallway). При отсутствии совпадения — 'living'
    (более строгие требования: площадь/ширина/окно — безопаснее для
    консервативной проверки, чем полный молчаливый пропуск)."""
    haystack = f"{type_str} {name}".lower()
    for kw, canonical in _KEYWORDS:
        if kw in haystack:
            return canonical
    return "living"


def _issue(severity: str, element_name: str, message: str) -> dict:
    return {"severity": severity, "element_type": "Room", "element_name": element_name, "message": message}


def _room_bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _edge_key(p1, p2, tol=3):
    return tuple(sorted((tuple(round(c, tol) for c in p1), tuple(round(c, tol) for c in p2))))


def _walls_touching_room(polygon: list[list[float]], walls: list) -> list:
    n = len(polygon)
    edges = {_edge_key(polygon[i], polygon[(i + 1) % n]) for i in range(n)}
    touching = []
    for wall in walls:
        if _edge_key(wall.axis[0], wall.axis[1]) in edges:
            touching.append(wall)
    return touching


def validate_house_plan(program, floor_plan) -> list[dict]:
    """program: bim_agents.contracts.BuildingProgram, floor_plan: FloorPlan.
    Проверяет мин. площадь/ширину по типу, наличие окна у комнат, которым
    оно требуется (по норме — примыкание к внешней стене с окном), и что
    у каждой комнаты есть дверь (доступ)."""
    issues: list[dict] = []

    if not program.rooms:
        issues.append({"severity": "error", "element_type": "Floorplan", "element_name": "—",
                        "message": "Проект не содержит ни одного помещения."})
        return issues

    rooms_by_id = {r.id: r for r in program.rooms}

    for storey in floor_plan.storeys:
        wall_by_id = {w.id: w for w in storey.walls}
        doored_walls = {op.wall for op in storey.openings if op.kind == "door"}
        windowed_walls = {op.wall for op in storey.openings if op.kind == "window"}

        for rp in storey.rooms:
            meta = rooms_by_id.get(rp.id)
            label = meta.name if meta else rp.id
            canonical = normalize_room_type(meta.type if meta else "", meta.name if meta else "")
            c = get_room_constraints(canonical)

            _x, _y, w, h = _room_bbox(rp.polygon)
            area = w * h
            min_side = min(w, h)

            if area < c["min_area"] - 0.05:
                issues.append(_issue("error", label,
                    f"Площадь {area:.1f} м² < минимума {c['min_area']} м² ({c['norm_ref']})."))
            if min_side < c["min_width"] - 0.02:
                issues.append(_issue("error", label,
                    f"Ширина {min_side:.2f} м < минимума {c['min_width']} м ({c['norm_ref']})."))

            touching = _walls_touching_room(rp.polygon, storey.walls)
            touching_ids = {w.id for w in touching}

            if c["needs_window"]:
                has_exterior = any(wall_by_id[wid].type == "exterior" for wid in touching_ids if wid in wall_by_id)
                has_window = bool(touching_ids & windowed_walls)
                if not (has_exterior and has_window):
                    issues.append(_issue("error", label,
                        f"Комната типа «{canonical}» не примыкает к внешней стене с окном "
                        f"(КМК 2.08.01-89 п.3.1, световой коэффициент 1:8)."))

            if not (touching_ids & doored_walls):
                issues.append(_issue("error", label, f"Комната «{label}» не имеет двери — нет доступа."))

    return issues
