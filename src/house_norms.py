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
    ("душ", "bathroom"), ("постироч", "bathroom"), ("laundry", "bathroom"), ("shower", "bathroom"),
    ("wc", "wc"), ("туалет", "wc"), ("уборная", "wc"), ("toilet", "wc"),
    ("hallway", "hallway"), ("entrance", "hallway"), ("прихожая", "hallway"), ("коридор", "hallway"),
    ("тамбур", "hallway"), ("холл", "hallway"), ("corridor", "hallway"),
    # Подсобные без собственной категории норм — ограничения как у
    # прихожей (малая мин. ширина, окно не требуется): раньше они падали в
    # 'living' и ложно требовали 8 м² и окно у котельной/кладовой.
    ("кладов", "hallway"), ("гардероб", "hallway"), ("котельн", "hallway"),
    ("топочн", "hallway"), ("гараж", "hallway"), ("garage", "hallway"),
    ("storage", "hallway"), ("boiler", "hallway"), ("pantry", "hallway"), ("wardrobe", "hallway"),
    ("bedroom", "bedroom"), ("спальня", "bedroom"), ("детская", "bedroom"), ("child", "bedroom"),
    ("кабинет", "bedroom"), ("office", "bedroom"), ("nursery", "bedroom"),
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


def _walls_touching_room(polygon: list[list[float]], walls: list, tol: float = 0.02) -> list:
    """Стены, лежащие на границе комнаты. Сравнение по коллинеарному
    перекрытию, а не по точному равенству рёбер: floorplan_agent режет стены
    на СЕГМЕНТЫ по владельцам (стык двух лент с разной нарезкой), поэтому
    участок стены — как правило, лишь часть ребра полигона комнаты, и точное
    сравнение целых рёбер (как здесь было раньше) не находило бы ни одной
    стены."""
    n = len(polygon)
    edges = [(polygon[i], polygon[(i + 1) % n]) for i in range(n)]
    touching = []
    for wall in walls:
        (wx1, wy1), (wx2, wy2) = wall.axis
        wall_horizontal = abs(wy1 - wy2) <= tol
        for p1, p2 in edges:
            edge_horizontal = abs(p1[1] - p2[1]) <= tol
            if wall_horizontal and edge_horizontal and abs(wy1 - p1[1]) <= tol:
                lo, hi = sorted((p1[0], p2[0]))
                a, b = sorted((wx1, wx2))
            elif not wall_horizontal and not edge_horizontal and abs(wx1 - p1[0]) <= tol:
                lo, hi = sorted((p1[1], p2[1]))
                a, b = sorted((wy1, wy2))
            else:
                continue
            if min(hi, b) - max(lo, a) > tol:
                touching.append(wall)
                break
    return touching


def snap_program_to_norms(program):
    """Готовит BuildingProgram под нормы для кнопки «Исправить по нормам»:
    поднимает площадь и мин. ширину КАЖДОЙ комнаты до нормативного минимума
    (с небольшим запасом, чтобы после укладки с учётом стен не свалиться
    обратно под минимум) и увеличивает footprint так, чтобы комнаты этажа
    поместились. Возвращает НОВЫЙ program (исходный не меняем).

    Так целенаправленно чинятся самые частые нарушения — «Кухня 2.8 м² <
    8 м²», «Санузел 0.99 м < 1.2 м»: причина в том, что LLM/шаблон назначил
    комнатам площади ниже нормы и/или тесный footprint. Оконные/дверные
    претензии геометрические — их снимает уже сама перегенерация."""
    prog = program.model_copy(deep=True)
    for r in prog.rooms:
        canon = normalize_room_type(r.type, r.name)
        c = get_room_constraints(canon)
        need_area = round(c["min_area"] * 1.1, 1)
        if r.area_m2 < need_area:
            r.area_m2 = need_area
        if (r.min_width_m or 0) < c["min_width"]:
            r.min_width_m = c["min_width"]

    # На этаже комнаты замащивают прямоугольник целиком → площадь этажа ≈
    # сумма площадей комнат. Берём максимум по этажам + запас на стены и
    # подгоняем ширину/глубину, сохраняя пропорции footprint.
    max_floor_area = 0.0
    for level in range(max(prog.storeys, 1)):
        s = sum(r.area_m2 for r in prog.rooms if r.storey == level)
        max_floor_area = max(max_floor_area, s)
    if max_floor_area > 0:
        # Запас 1.3: помимо стен/перегородок он даёт комнатам ширину сверх
        # минимальной площади. Многие ряды делят ширину дома между собой
        # (3 спальни в ряд), и без запаса каждая упирается ровно в min_width
        # или чуть ниже — с запасом ширины проходят.
        target = max_floor_area * 1.3
        fw = float(prog.footprint.get("width_m", 0) or 0)
        fd = float(prog.footprint.get("depth_m", 0) or 0)
        cur = fw * fd
        if fw > 0 and fd > 0 and cur < target:
            k = (target / cur) ** 0.5
            prog.footprint = {"width_m": round(fw * k, 2), "depth_m": round(fd * k, 2)}
        elif fw <= 0 or fd <= 0:
            side = round(target ** 0.5, 2)
            prog.footprint = {"width_m": side, "depth_m": side}
    return prog


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
