"""
Фаза 4 — локальная LLM как "нейро"-генератор планировки квартиры.

Вместо специализированной исследовательской сети (Graph2Plan/HouseDiffusion —
требуют отдельных весов, обычно писались под Linux+CUDA и часто не
переносятся на Apple Silicon без доработки) используем ЛОКАЛЬНУЮ LLM
пользователя через LM Studio — тот же канал, что и в /api/model/architect.
Не требует новых загрузок: если LM Studio уже запущена с любой моделью,
это просто работает.

LLM решает только геометрическую задачу (расстановка прямоугольников
комнат в границах квартиры). Топологию дверей и вход с площадки считает
тот же детерминированный код, что и baseline-солвер
(_connect_adjacent_rooms из solver.py) — так меньше шансов получить от
галлюцинирующей модели нерабочий граф связности.

Обязательный fallback на generate_floorplan() (фаза 2): если LM Studio
недоступна, вернула невалидный JSON, либо после всех попыток починки по
нормам всё ещё есть ошибки — эта функция возвращает None, и вызывающий
код обязан откатиться на детерминированный солвер. Здание должно
собираться всегда, даже без запущенной LLM.

Улучшения v2 (2026-07):
- Few-shot примеры в системном промпте — LLM видит 2-3 реальные раскладки
- Детальный repair-фидбек — список ошибок с координатами каждой комнаты
- Кэш удачных планировок (data/floorplan_cache.json) — повторные запросы
  с похожими параметрами не дёргают LLM
- Увеличен max_repair_attempts до 2 (3 попытки всего)
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

from .ir import ApartmentFloorplan, RoomBox, DoorSpec
from .norms import get_room_constraints, validate_floorplan
from .solver import _default_program, _connect_adjacent_rooms

_RU_NAMES = {
    "living": "Гостиная", "bedroom": "Спальня", "kitchen": "Кухня",
    "bathroom": "Санузел", "wc": "Туалет", "hallway": "Прихожая",
}

# ── Кэш планировок ─────────────────────────────────────────────────────────
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "floorplan_cache.json"

# Допуск при поиске по кэшу (± сколько метров считаем "той же" шириной/глубиной)
_CACHE_TOLERANCE = 0.5


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_key(width: float, depth: float, room_count: int) -> str:
    return f"{width:.1f}x{depth:.1f}__{room_count}r"


def _cached_plan(
    width: float, depth: float, room_count: int, entry_side: str,
) -> Optional[ApartmentFloorplan]:
    """Проверяет кэш: если для похожих габаритов есть валидная планировка — возвращает её."""
    cache = _load_cache()
    if not cache:
        return None
    best_key = None
    best_dist = float("inf")
    for key in cache:
        try:
            w_part, rest = key.split("x", 1)
            d_part, r_part = rest.split("__")
            cw, cd = float(w_part), float(d_part)
            cr = int(r_part.replace("r", ""))
        except (ValueError, IndexError):
            continue
        if cr != room_count:
            continue
        dist = max(abs(cw - width), abs(cd - depth))
        if dist < _CACHE_TOLERANCE and dist < best_dist:
            best_key = key
            best_dist = dist
    if best_key is None:
        return None
    entry = cache[best_key]
    try:
        rooms_raw = entry.get("rooms", [])
        doors_raw = entry.get("doors", [])
        rooms = _rooms_from_llm_json(rooms_raw)
        if not rooms:
            return None
        doors = []
        for d in doors_raw:
            doors.append(DoorSpec(
                x=d["x"], y=d["y"], wall_axis=d.get("wall_axis", "y"),
                room_a=d.get("room_a", -1), room_b=d.get("room_b", -1),
                width=d.get("width", 0.9), kind=d.get("kind", "interior"),
            ))
        fp = ApartmentFloorplan(
            width=width, depth=depth, entry_side=entry_side,
            rooms=rooms, doors=doors, source="neural",
        )
        # Проверяем, что кэш всё ещё валиден (геометрия могла измениться)
        issues = validate_floorplan(fp)
        if not [i for i in issues if i["severity"] == "error"]:
            return fp
    except Exception:
        pass
    return None


def _save_to_cache(
    width: float, depth: float, room_count: int, fp: ApartmentFloorplan,
) -> None:
    """Сохраняет успешную планировку в кэш."""
    key = _cache_key(width, depth, room_count)
    cache = _load_cache()
    cache[key] = {
        "rooms": [
            {"type": r.type, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
            for r in fp.rooms
        ],
        "doors": [
            {"x": d.x, "y": d.y, "wall_axis": d.wall_axis,
             "room_a": d.room_a, "room_b": d.room_b,
             "width": d.width, "kind": d.kind}
            for d in fp.doors
        ],
    }
    _save_cache(cache)


# ── Few-shot примеры ───────────────────────────────────────────────────────

_FEW_SHOT_EXAMPLES = """
ПРИМЕР 1: Квартира 5.0\xd79.0 м, 2-комнатная (спальня+гостиная+кухня+санузел), вход слева.
{"rooms": [
  {"type": "hallway",  "x0": 0,   "y0": 0,   "x1": 1.4, "y1": 9.0},
  {"type": "living",   "x0": 1.4, "y0": 4.0, "x1": 5.0, "y1": 9.0},
  {"type": "kitchen",  "x0": 1.4, "y0": 2.0, "x1": 3.2, "y1": 4.0},
  {"type": "bedroom",  "x0": 3.2, "y0": 2.0, "x1": 5.0, "y1": 4.0},
  {"type": "bathroom", "x0": 1.4, "y0": 0,   "x1": 3.2, "y1": 2.0}
]}

ПРИМЕР 2: Квартира 6.0\xd79.0 м, 3-комнатная (2 спальни+гостиная+кухня+санузел+туалет), вход слева.
{"rooms": [
  {"type": "hallway",  "x0": 0,   "y0": 0,   "x1": 1.5, "y1": 9.0},
  {"type": "living",   "x0": 1.5, "y0": 5.0, "x1": 6.0, "y1": 9.0},
  {"type": "bedroom",  "x0": 4.0, "y0": 2.0, "x1": 6.0, "y1": 5.0},
  {"type": "bedroom",  "x0": 1.5, "y0": 2.0, "x1": 4.0, "y1": 5.0},
  {"type": "kitchen",  "x0": 1.5, "y0": 0.8, "x1": 3.5, "y1": 2.0},
  {"type": "wc",       "x0": 3.5, "y0": 0,   "x1": 4.8, "y1": 0.8},
  {"type": "bathroom", "x0": 4.8, "y0": 0,   "x1": 6.0, "y1": 0.8}
]}

ПРИМЕР 3: Квартира 7.0\xd710.0 м, 3-комнатная (2 спальни+living+kitchen+bathroom+WC), вход справа.
{"rooms": [
  {"type": "hallway",  "x0": 5.6, "y0": 0,   "x1": 7.0, "y1": 10.0},
  {"type": "living",   "x0": 0,   "y0": 6.0, "x1": 5.6, "y1": 10.0},
  {"type": "bedroom",  "x0": 0,   "y0": 3.0, "x1": 3.0, "y1": 6.0},
  {"type": "bedroom",  "x0": 3.0, "y0": 3.0, "x1": 5.6, "y1": 6.0},
  {"type": "kitchen",  "x0": 0,   "y0": 1.2, "x1": 2.5, "y1": 3.0},
  {"type": "wc",       "x0": 2.5, "y0": 0,   "x1": 4.0, "y1": 1.2},
  {"type": "bathroom", "x0": 4.0, "y0": 0,   "x1": 5.6, "y1": 1.2}
]}
"""

# Шаблоны типов застройки — дополнительный few-shot для разных конфигураций квартир
_BUILDING_PATTERNS = {
    "row": (  # рядовая (средняя) секция — кухня/санузел у площадки, жилые комнаты у фасада
        "ТИПОВАЯ РЯДОВАЯ (общая стена с двух сторон):\n"
        "Прихожая-коридор вдоль одной стены (ширина 1.4-1.6м) на всю глубину.\n"
        "Мокрые зоны (кухня, санузел) — ближе к входу (y=0), компактным блоком.\n"
        "Жилые комнаты (гостиная, спальни) — у фасада (y=depth), все с окнами.\n"
        "Кухня может быть как у фасада (окно на двор), так и ближе к площадке."
    ),
    "corner": (  # угловая секция — окна на две стороны
        "УГЛОВАЯ (окна на две стороны):\n"
        "Часть комнат может иметь окна по боковой стене (x=0 или x=width).\n"
        "Угловая гостиная (два окна) — преимущество.\n"
        "Прихожая — со стороны площадки, остальное — как рядовая."
    ),
    "duplex": (
        "ДВУХУРОВНЕВАЯ КВАРТИРА:\n"
        "Первый уровень: прихожая, кухня-гостиная, санузел, выход на террасу.\n"
        "Второй уровень (не моделируется здесь): спальни, ванная.\n"
        "Планировка первого уровня: открытая кухня-гостиная во всю ширину фасада."
    ),
}


# ── Парсинг и построение промпта ───────────────────────────────────────────

def _rooms_from_llm_json(rooms_raw: list) -> list[RoomBox]:
    rooms = []
    for r in rooms_raw:
        try:
            t = str(r.get("type", "")).strip().lower()
            if t not in _RU_NAMES:
                continue
            x0, y0, x1, y1 = float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"])
        except (KeyError, TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        rooms.append(RoomBox(t, x0, y0, x1, y1, name=_RU_NAMES[t]))
    return rooms


def _build_prompt(
    width: float, depth: float, entry_side: str, program: dict,
    repair_hints: str = "",
    building_pattern: str = "row",
) -> tuple[str, str]:
    """Строит системный и пользовательский промпты.

    If repair_hints непусто — это repair-раунд: LLM видит предыдущую ошибку
    и получает более жёсткие инструкции.

    building_pattern — тип секции: "row" (рядовая), "corner" (угловая), "duplex" (двухуровневая).
    """
    all_types = program.get("wet", []) + program.get("facade", []) + ["hallway"]
    lines = []
    for t in sorted(set(all_types)):
        c = get_room_constraints(t)
        window_note = "ОБЯЗАТЕЛЬНО окно (примыкать к грани y=depth)" if c["needs_window"] else "окно не обязательно"
        lines.append(f"- {t}: площадь ≥ {c['min_area']} м², ширина/глубина ≥ {c['min_width']} м, {window_note}")

    entry_x = 0.0 if entry_side == "west" else width

    repair_section = ""
    if repair_hints:
        repair_section = (
            "\n⚠️ В ПРЕДЫДУЩЕЙ ПОПЫТКЕ БЫЛИ ОШИБКИ:\n" + repair_hints + "\n\n"
            "Исправь их. Проверь в ответе каждую координату каждой комнаты — "
            "ни одна комната не должна пересекаться с соседней, все обязаны "
            "помещаться в границы квартиры. Верни ТОЛЬКО JSON."
        )

    sys_prompt = (
        "Ты — архитектор, расставляющий комнаты внутри прямоугольной квартиры на плане.\n"
        "Твоя задача — сгенерировать компактную, реалистичную планировку.\n\n"
        f"Границы квартиры: x от 0 до {width:.2f} м, y от 0 до {depth:.2f} м.\n"
        f"Сторона входа с лестничной площадки: x={entry_x:.2f} м.\n"
        f"Сторона фасада с окнами: y={depth:.2f} м.\n\n"
        "ПРИМЕРЫ РЕАЛЬНЫХ ПЛАНИРОВОК (используй их как образец расположения, "
        "но подставляй свои координаты под текущие габариты):\n"
        + _FEW_SHOT_EXAMPLES + "\n"
        + (_BUILDING_PATTERNS.get(building_pattern, "") + "\n\n" if building_pattern in _BUILDING_PATTERNS else "")
        + "Нормы КМК 2.08.01-89 по комнатам:\n" + "\n".join(lines) + "\n\n"
        "Правила:\n"
        "1. Комнаты — прямоугольники, НЕ должны пересекаться друг с другом.\n"
        "2. Каждая комната обязана полностью помещаться в границы квартиры "
        "(x >= 0, y >= 0, x <= width, y <= depth).\n"
        f"3. living/bedroom/kitchen обязаны примыкать к фасаду (y={depth:.2f}) — "
        "у них должно быть окно по КМК 2.08.01-89 п.3.1.\n"
        f"4. hallway (прихожая) обязана примыкать к площадке (x={entry_x:.2f}) "
        "и граничить с максимальным числом других комнат.\n"
        "5. Сумма площадей всех комнат должна примерно покрывать всю площадь квартиры "
        "без больших пустот. Минимальная ширина коридора-прихожей — 1.4 м.\n"
        "6. «Мокрые» зоны (kitchen, bathroom, wc) располагай ближе к площадке "
        "и друг к другу (стояк один). Жилые комнаты — у фасада.\n"
        "7. Комнаты одной «зоны» (все фасадные или все мокрые) не должны "
        "чередоваться — располагай их компактными блоками.\n\n"
        + repair_section +
        "Верни ТОЛЬКО валидный JSON без markdown, без пояснений, без комментариев:\n"
        '{"rooms": [{"type": "hallway", "x0": 0, "y0": 0, "x1": 1.5, "y1": 9.0}, ...]}'
    )
    user_prompt = f"Расставь эти помещения: {', '.join(all_types)}."
    return sys_prompt, user_prompt


def _rooms_to_issues_text(fp: ApartmentFloorplan, issues: list[dict]) -> str:
    """Формирует детальный фидбек по ошибкам для LLM."""
    errors = [i for i in issues if i["severity"] == "error"]
    if not errors:
        return ""

    room_details = ""
    for room in fp.rooms:
        c = get_room_constraints(room.type)
        violations = []
        if room.area < c["min_area"] - 0.05:
            violations.append(f"площадь {room.area:.1f} < {c['min_area']} м²")
        if room.min_side < c["min_width"] - 0.02:
            violations.append(f"ширина {room.min_side:.2f} < {c['min_width']} м")
        if c["needs_window"] and not room.touches_facade(fp.depth):
            violations.append("нет окна на фасаде")
        if room.x0 < -0.01 or room.y0 < -0.01 or room.x1 > fp.width + 0.01 or room.y1 > fp.depth + 0.01:
            violations.append(
                f"выходит за границы: ({room.x0:.1f},{room.y0:.1f})-({room.x1:.1f},{room.y1:.1f}) "
                f"при габарите {fp.width:.1f}×{fp.depth:.1f}"
            )

        if violations:
            room_details += (
                f"  • {room.name} ({room.type}) "
                f"({room.x0:.2f},{room.y0:.2f})-({room.x1:.2f},{room.y1:.2f}): "
                + "; ".join(violations) + "\n"
            )

    # Пересечения комнат
    overlaps = []
    for i, a in enumerate(fp.rooms):
        for b in fp.rooms[i + 1:]:
            ox = round(min(a.x1, b.x1) - max(a.x0, b.x0), 2)
            oy = round(min(a.y1, b.y1) - max(a.y0, b.y0), 2)
            if ox > 0.02 and oy > 0.02:
                overlaps.append(f"  • {a.type} пересекается с {b.type}: наложение {ox}×{oy} м")

    text = ""
    if room_details:
        text += "Ошибки по комнатам:\n" + room_details
    if overlaps:
        text += "Пересечения комнат:\n" + "".join(overlaps)
    if not text:
        text = "; ".join(i["message"] for i in errors[:6])

    return text.strip()


# ── Основная функция ────────────────────────────────────────────────────────

def generate_floorplan_llm(
    width: float,
    depth: float,
    room_count: int = 2,
    entry_side: str = "west",
    program: dict | None = None,
    model: str = "local-model",
    max_repair_attempts: int = 2,
    timeout: float = 60.0,
    building_pattern: str = "row",
) -> ApartmentFloorplan | None:
    """
    Пытается сгенерировать планировку через локальную LLM (LM Studio).

    Порядок:
    1. Проверить кэш — если есть валидная планировка для похожих габаритов,
       вернуть её (не дёргать LLM).
    2. Спросить LLM. Если ответ невалиден — повторить с детальным фидбеком
       (до max_repair_attempts повторов).
    3. При успехе — сохранить в кэш.
    4. При неудаче — вернуть None (вызывающий код откатывается на solver).

    building_pattern: "row" (рядовая), "corner" (угловая), "duplex" (двухуровневая).

    Возвращает ApartmentFloorplan (source="neural") или None.
    """
    try:
        import requests
        from ..config import LM_STUDIO_BASE_URL
    except Exception:
        return None

    prog = program or _default_program(room_count)

    # ── Шаг 1: проверка кэша ────────────────────────────────────────────────
    cached = _cached_plan(width, depth, room_count, entry_side)
    if cached is not None:
        return cached

    # ── Шаг 2: LLM-генерация ────────────────────────────────────────────────
    sys_prompt, user_prompt = _build_prompt(width, depth, entry_side, prog,
                                             building_pattern=building_pattern)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_repair_attempts + 1):
        try:
            resp = requests.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 1500,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None

        # Извлекаем JSON
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            if attempt < max_repair_attempts:
                messages.append({"role": "assistant", "content": raw[:500]})
                messages.append({
                    "role": "user",
                    "content": "Твой ответ не содержит JSON. Верни ТОЛЬКО валидный JSON "
                               f'вида: {{"rooms": [{{"type": "hallway", "x0": 0, "y0": 0, '
                               f'"x1": 1.5, "y1": 9.0}}, ...]}}'
                })
                continue
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            if attempt < max_repair_attempts:
                messages.append({"role": "assistant", "content": raw[:500]})
                messages.append({
                    "role": "user",
                    "content": "JSON невалиден — проверь скобки, кавычки и запятые. "
                               "Верни ТОЛЬКО исправленный JSON."
                })
                continue
            return None

        rooms = _rooms_from_llm_json(data.get("rooms", []))
        hallway_idx = next((i for i, r in enumerate(rooms) if r.type == "hallway"), None)
        if not rooms or hallway_idx is None:
            return None

        doors = _connect_adjacent_rooms(rooms)
        entry_x = 0.0 if entry_side == "west" else width
        doors.append(DoorSpec(
            x=entry_x, y=rooms[hallway_idx].cy, wall_axis="y",
            room_a=-1, room_b=hallway_idx, width=0.9, kind="entry",
        ))

        fp = ApartmentFloorplan(
            width=width, depth=depth, entry_side=entry_side,
            rooms=rooms, doors=doors, source="neural",
        )

        issues = validate_floorplan(fp)
        errors = [i for i in issues if i["severity"] == "error"]
        if not errors:
            # Успех — сохраняем в кэш
            _save_to_cache(width, depth, room_count, fp)
            return fp

        # ── repair-раунд ────────────────────────────────────────────────────
        if attempt < max_repair_attempts:
            repair_hints = _rooms_to_issues_text(fp, issues)
            # Перестраиваем системный промпт с repair-хинтами
            sys_prompt_r, _ = _build_prompt(
                width, depth, entry_side, prog, repair_hints=repair_hints,
                building_pattern=building_pattern,
            )
            messages = [
                {"role": "system", "content": sys_prompt_r},
                {"role": "user", "content": user_prompt},
            ]
            continue

    return None
