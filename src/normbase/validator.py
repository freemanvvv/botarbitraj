"""
Детерминированная проверка параметров здания на соответствие КМК/ШНК.

LLM-архитектор только предлагает параметры — он может ошибиться или
проигнорировать норму. Эта проверка работает уже на стороне Python
после ответа LLM и принудительно подгоняет значения под нормативные
минимумы/максимумы, независимо от того, что насчитала модель.
"""

BUILDING_TYPE_ALIASES = {
    "жилой": "residential", "жилое": "residential", "дом": "residential",
    "офис": "office", "офисное": "office", "административное": "office",
    "торговый": "retail", "торговое": "retail", "магазин": "retail",
}


def _classify(building_type: str) -> str:
    bt = (building_type or "").lower()
    for key, code in BUILDING_TYPE_ALIASES.items():
        if key in bt:
            return code
    return "residential"


def validate_and_fix_params(params: dict, building_type: str = "") -> tuple[dict, list[str]]:
    """
    Проверяет params (как для create_max_building) на соответствие нормам,
    при необходимости подправляет значения. Возвращает (исправленные params, список замечаний).
    """
    p = dict(params)
    notes: list[str] = []
    btype = _classify(building_type)

    # Инженерные пределы (не норматив) — параметры приходят из JSON, который
    # написала LLM, и могут быть аномально большими (случайно или намеренно).
    # Без верхней границы это превращается в DoS: генератор честно попытается
    # построить здание в сотни этажей/километр длиной.
    caps = {
        "num_floors": 120, "length": 500.0, "width": 500.0,
        "wall_thickness": 2.0, "slab_thickness": 1.0,
        "windows_per_wall_long": 50, "windows_per_wall_short": 50,
        "window_width": 10.0, "window_height": 10.0,
        "door_width": 5.0, "door_height": 5.0,
    }
    for key, cap in caps.items():
        val = p.get(key)
        if val is not None and val > cap:
            notes.append(f"Параметр «{key}»={val} превышает разумный предел {cap} — уменьшен до {cap}.")
            p[key] = cap
    if p.get("num_floors") and p.get("height"):
        # height обычно = floor_height * num_floors — пересчитываем после клампа этажности
        max_reasonable_height = caps["num_floors"] * 10.0  # ≤10 м на этаж
        if p["height"] > max_reasonable_height:
            p["height"] = max_reasonable_height

    n_floors = int(p.get("num_floors", 2))

    # КМК 2.08.01-89 п.2.1 / КМК 2.09.04 п.1.4 — высота этажа
    floor_h = p["height"] / n_floors if n_floors else p.get("height", 3.0)
    min_h = 2.7 if btype == "residential" else 3.0
    if floor_h < min_h:
        notes.append(
            f"Высота этажа {floor_h:.2f} м < минимума {min_h} м "
            f"({'КМК 2.08.01-89 п.2.1' if btype == 'residential' else 'КМК 2.09.04 п.1.4'}) — увеличено до {min_h} м."
        )
        floor_h = min_h
        p["height"] = round(floor_h * n_floors, 3)

    # ШНК 2.01.03-96 п.5.3 — сейсмика: кирпичное здание выше 5 этажей запрещено
    wall_mat = str(p.get("wall_material", "")).lower()
    is_brick = "кирпич" in wall_mat or p.get("wall_thickness", 0) <= 0.64
    if n_floors > 5 and not p.get("add_columns", False):
        notes.append(
            "ШНК 2.01.03-96 п.5.2-5.3: при сейсмичности 8+ баллов и этажности >5 "
            "обязателен каркас (колонны) — добавлены несущие колонны."
        )
        p["add_columns"] = True

    # КМК 2.03.06-01 п.2.2-2.3 — толщина несущих стен по этажности
    wt = float(p.get("wall_thickness", 0.38))
    if n_floors <= 3:
        min_wt = 0.38
        ref = "КМК 2.03.06-01 п.2.2"
    elif n_floors <= 9:
        min_wt = 0.51
        ref = "КМК 2.03.06-01 п.2.3"
    else:
        min_wt = 0.25  # каркас + заполнение
        ref = "КМК 2.03.06-01 п.2.4"
    if wt < min_wt:
        notes.append(
            f"Толщина стены {wt:.2f} м < минимума {min_wt} м для {n_floors}-этажного "
            f"здания ({ref}) — увеличена до {min_wt} м."
        )
        p["wall_thickness"] = min_wt

    # КМК 2.03.01-96 п.3.1 — толщина перекрытия
    st = float(p.get("slab_thickness", 0.20))
    min_st = 0.12
    if st < min_st:
        notes.append(
            f"Толщина перекрытия {st:.2f} м < минимума {min_st} м (КМК 2.03.01-96 п.3.1) — "
            f"увеличена до {min_st} м."
        )
        p["slab_thickness"] = min_st

    # КМК 2.08.01-89 п.4.1/4.2 — двери
    dw = float(p.get("door_width", 0.9))
    dh = float(p.get("door_height", 2.1))
    if dw < 0.8:
        notes.append(f"Ширина двери {dw:.2f} м < 0.8 м (КМК 2.08.01-89 п.4.2) — увеличена до 0.8 м.")
        p["door_width"] = 0.8
    if dh < 2.0:
        notes.append(f"Высота двери {dh:.2f} м < 2.0 м (КМК 2.08.01-89 п.4.2) — увеличена до 2.0 м.")
        p["door_height"] = 2.0

    # КМК 2.08.01-89 п.3.1/п.3.2 — подоконник
    sill = float(p.get("window_sill", 0.9))
    min_sill = 0.8 if btype == "residential" else 0.9
    if sill < min_sill:
        notes.append(
            f"Подоконник {sill:.2f} м < минимума {min_sill} м для типа здания — увеличен до {min_sill} м."
        )
        p["window_sill"] = min_sill

    # Световой коэффициент (приблизительно) 1:8 для жилых, 1:6 для офисных —
    # проверяем, что хотя бы минимальная площадь остекления на стену задана
    ww = float(p.get("window_width", 1.2))
    wh = float(p.get("window_height", 1.5))
    if ww * wh < 1.0:
        notes.append(
            f"Площадь окна {ww*wh:.2f} м² слишком мала для светового коэффициента 1:8 "
            f"(КМК 2.08.01-89 п.3.1) — увеличена до 1.2×1.5 м."
        )
        p["window_width"] = max(ww, 1.2)
        p["window_height"] = max(wh, 1.5)

    return p, notes


def validate_building_meta(building: dict, num_floors: int) -> tuple[dict, list[str]]:
    """
    Проверяет блок 'building' (подъезды/лифты/шахты) на соответствие
    КМК 2.08.01-89 п.6.1-6.8. LLM может забыть про лифт или занизить
    грузоподъёмность — здесь это подправляется детерминированно.
    """
    b = dict(building or {})
    notes: list[str] = []

    # Инженерные пределы (не норматив) — те же соображения, что и в
    # validate_and_fix_params: entrances/apartments_per_landing/
    # apartment_rooms/elevators_per_entrance приходят из JSON LLM без
    # проверки Pydantic и напрямую умножаются в create_apartment_building
    # (кол-во квартир = entrances × apartments_per_landing × num_floors).
    meta_caps = {
        "entrances": 20, "apartments_per_landing": 8,
        "apartment_rooms": 6, "elevators_per_entrance": 4,
    }
    for key, cap in meta_caps.items():
        val = b.get(key)
        if val is not None and val > cap:
            notes.append(f"Параметр «{key}»={val} превышает разумный предел {cap} — уменьшен до {cap}.")
            b[key] = cap

    if num_floors >= 5 and not b.get("has_elevator", False):
        notes.append(
            f"Здание {num_floors}-этажное — пассажирский лифт обязателен "
            f"(КМК 2.08.01-89 п.6.1) — добавлен лифт."
        )
        b["has_elevator"] = True
        b["elevators_per_entrance"] = max(1, int(b.get("elevators_per_entrance", 0) or 1))

    if b.get("has_elevator"):
        n_lifts = int(b.get("elevators_per_entrance", 1) or 1)
        if num_floors > 9 and n_lifts < 2:
            notes.append(
                f"Здание {num_floors}-этажное (>9) — требуется не менее 2 лифтов на подъезд, "
                f"один грузоподъёмностью ≥630 кг (КМК 2.08.01-89 п.6.2) — увеличено до 2."
            )
            b["elevators_per_entrance"] = 2

        cap = float(b.get("elevator_capacity_kg", 0) or 0)
        if cap < 400:
            notes.append(
                f"Грузоподъёмность лифта {cap:.0f} кг < 400 кг (КМК 2.08.01-89 п.6.3) — увеличена до 400 кг."
            )
            b["elevator_capacity_kg"] = 400

        stair_w = float(b.get("stair_width_m", 0) or 0)
        if stair_w < 1.2:
            notes.append(
                f"Ширина марша у лифтового холла {stair_w:.2f} м < 1.2 м "
                f"(КМК 2.08.01-89 п.6.7, путь эвакуации) — увеличена до 1.2 м."
            )
            b["stair_width_m"] = 1.2

        apt_per_landing = int(b.get("apartments_per_landing", 0) or 0)
        if apt_per_landing > 4 and n_lifts >= 1:
            notes.append(
                f"{apt_per_landing} квартир на лестничную площадку — допустимо не более 4 "
                f"на одну лестничную клетку с лифтом (КМК 2.08.01-89 п.6.8). "
                f"Требуется вторая лестничная клетка."
            )
    else:
        stair_w = float(b.get("stair_width_m", 0) or 0)
        if stair_w < 1.05:
            notes.append(
                f"Ширина марша {stair_w:.2f} м < 1.05 м (КМК 2.08.01-89) — увеличена до 1.05 м."
            )
            b["stair_width_m"] = 1.05

    return b, notes
