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
