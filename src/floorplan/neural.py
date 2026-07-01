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
недоступна, вернула невалидный JSON, либо после одной попытки починки по
нормам всё ещё есть ошибки — эта функция возвращает None, и вызывающий
код обязан откатиться на детерминированный солвер. Здание должно
собираться всегда, даже без запущенной LLM.
"""
import json
import re

from .ir import ApartmentFloorplan, RoomBox, DoorSpec
from .norms import get_room_constraints, validate_floorplan
from .solver import _default_program, _connect_adjacent_rooms

_RU_NAMES = {
    "living": "Гостиная", "bedroom": "Спальня", "kitchen": "Кухня",
    "bathroom": "Санузел", "wc": "Туалет", "hallway": "Прихожая",
}


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


def _build_prompt(width: float, depth: float, entry_side: str, program: dict) -> tuple[str, str]:
    all_types = program.get("wet", []) + program.get("facade", []) + ["hallway"]
    lines = []
    for t in sorted(set(all_types)):
        c = get_room_constraints(t)
        window_note = "ОБЯЗАТЕЛЬНО окно (примыкать к грани y=depth)" if c["needs_window"] else "окно не обязательно"
        lines.append(f"- {t}: площадь ≥ {c['min_area']} м², ширина/глубина ≥ {c['min_width']} м, {window_note}")

    entry_x = 0.0 if entry_side == "west" else width
    sys_prompt = (
        "Ты — архитектор, расставляющий комнаты внутри прямоугольной квартиры на плане.\n"
        f"Границы квартиры: x от 0 до {width:.2f} м, y от 0 до {depth:.2f} м.\n"
        f"Сторона входа с лестничной площадки: x={entry_x:.2f} м.\n"
        f"Сторона фасада с окнами: y={depth:.2f} м.\n\n"
        "Нормы КМК 2.08.01-89 по комнатам:\n" + "\n".join(lines) + "\n\n"
        "Правила:\n"
        "1. Комнаты — прямоугольники, НЕ должны пересекаться друг с другом.\n"
        "2. Каждая комната обязана полностью помещаться в границы квартиры.\n"
        f"3. living/bedroom/kitchen обязаны примыкать к грани y={depth:.2f} (окно).\n"
        f"4. hallway (прихожая) обязана примыкать к грани x={entry_x:.2f} (вход с площадки) "
        "и граничить с максимальным числом других комнат — она распределяет доступ ко всем помещениям.\n"
        "5. Сумма площадей всех комнат должна примерно покрывать всю площадь квартиры без больших пустот.\n\n"
        "Верни ТОЛЬКО валидный JSON без markdown, без пояснений:\n"
        '{"rooms": [{"type": "hallway", "x0": 0, "y0": 0, "x1": 1.5, "y1": 9.0}, ...]}'
    )
    user_prompt = f"Расставь эти помещения: {', '.join(all_types)}."
    return sys_prompt, user_prompt


def generate_floorplan_llm(
    width: float,
    depth: float,
    room_count: int = 2,
    entry_side: str = "west",
    program: dict | None = None,
    model: str = "local-model",
    max_repair_attempts: int = 1,
    timeout: float = 60.0,
) -> ApartmentFloorplan | None:
    """
    Пытается сгенерировать планировку через локальную LLM (LM Studio).

    Возвращает готовый ApartmentFloorplan (source="neural") при успехе,
    либо None при ЛЮБОЙ неудаче (LM Studio недоступна, невалидный JSON,
    планировка не проходит нормы даже после починки) — вызывающий код
    обязан в этом случае откатиться на generate_floorplan().
    """
    try:
        import requests
        from ..config import LM_STUDIO_BASE_URL
    except Exception:
        return None

    prog = program or _default_program(room_count)
    sys_prompt, user_prompt = _build_prompt(width, depth, entry_side, prog)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_repair_attempts + 1):
        try:
            resp = requests.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1200},
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None

        match = re.search(r'\{[\s\S]*\}', raw)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None

        rooms = _rooms_from_llm_json(data.get("rooms", []))
        hallway_idx = next((i for i, r in enumerate(rooms) if r.type == "hallway"), None)
        if not rooms or hallway_idx is None:
            return None

        doors = _connect_adjacent_rooms(rooms)
        entry_x = 0.0 if entry_side == "west" else width
        doors.append(DoorSpec(x=entry_x, y=rooms[hallway_idx].cy, wall_axis="y",
                               room_a=-1, room_b=hallway_idx, width=0.9, kind="entry"))

        fp = ApartmentFloorplan(width=width, depth=depth, entry_side=entry_side,
                                 rooms=rooms, doors=doors, source="neural")
        issues = validate_floorplan(fp)
        errors = [i for i in issues if i["severity"] == "error"]
        if not errors:
            return fp

        if attempt < max_repair_attempts:
            fixes = "; ".join(i["message"] for i in errors[:6])
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": (
                f"В планировке есть нарушения норм, исправь координаты и верни JSON заново: {fixes}"
            )})
            continue

    return None
