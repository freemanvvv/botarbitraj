"""
MEP-раскладки — инженерные системы внутри квартиры.

После того как комнаты расставлены (solver или neural), LLM по тем же
координатам генерирует точки и трассы для:
- Электрики (розетки, выключатели, светильники, силовые линии)
- Водоснабжения и канализации (стояки, приборы, трассы)
- Слаботочки (RJ-45, TV, роутер)

Формат — JSON-слой поверх ApartmentFloorplan. Каждая система — свой
подмассив точек/приборов.

Правила и нормы (КМК + сложившаяся практика):
- КМК 2.04.01-98 «Внутренний водопровод и канализация»
- КМК 2.04.05-97 «Отопление, вентиляция и кондиционирование»
- КМК 2.04.16-2005 «Электрооборудование жилых зданий»
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .ir import ApartmentFloorplan
from .norms import get_room_constraints

# ── IR для MEP ──────────────────────────────────────────────────────────────

@dataclass
class Socket:
    """Электрическая точка (розетка/выключатель/светильник)."""
    kind: str          # "socket" | "switch" | "light" | "stove_plug" | "vent_fan"
    x: float
    y: float
    wall: str          # "north" | "south" | "east" | "west" | "ceiling"
    height_m: float    # высота от пола
    room_type: str     # для привязки
    note: str = ""

@dataclass
class PlumbingFixture:
    """Сантехнический прибор."""
    fixture: str       # "sink" | "toilet" | "bathtub" | "shower" | "washing_mc" | "kitchen_sink"
    x: float
    y: float
    room_type: str
    supplies: str      # "cold" | "hot+cold"
    need_drain: bool = True
    note: str = ""

@dataclass
class LowCurrentPoint:
    """Слаботочная точка (сеть, ТВ)."""
    kind: str          # "rj45" | "tv" | "router" | "patch_panel"
    x: float
    y: float
    wall: str
    height_m: float
    room_type: str
    note: str = ""

@dataclass
class MepLayout:
    """Полная MEP-раскладка для одной квартиры."""
    electrical: list[Socket] = field(default_factory=list)
    plumbing: list[PlumbingFixture] = field(default_factory=list)
    low_current: list[LowCurrentPoint] = field(default_factory=list)
    source: str = "neural"


# ── Few-shot примеры ───────────────────────────────────────────────────────

_ELECTRICAL_FEW_SHOT = """
ПРИМЕР 1 — 2-комнатная 5×9м (стандартная):
{"electrical": [
  {"kind": "light",    "x": 0.7,  "y": 4.5, "wall": "ceiling", "height_m": 2.5, "room_type": "hallway",   "note": "свет в прихожей"},
  {"kind": "switch",   "x": 0.0,  "y": 4.5, "wall": "west",    "height_m": 1.2, "room_type": "hallway",   "note": "проходной выключатель"},
  {"kind": "light",    "x": 3.2,  "y": 6.5, "wall": "ceiling", "height_m": 2.5, "room_type": "living",     "note": "основной свет"},
  {"kind": "switch",   "x": 1.4,  "y": 4.0, "wall": "west",    "height_m": 1.2, "room_type": "living",     "note": "у входа"},
  {"kind": "socket",   "x": 3.0,  "y": 9.0, "wall": "south",   "height_m": 0.3, "room_type": "living",     "note": "рабочая зона"},
  {"kind": "socket",   "x": 5.0,  "y": 7.5, "wall": "east",    "height_m": 0.3, "room_type": "living",     "note": "TV-зона"},
  {"kind": "light",    "x": 4.1,  "y": 3.0, "wall": "ceiling", "height_m": 2.5, "room_type": "bedroom",    "note": "основной свет"},
  {"kind": "switch",   "x": 3.2,  "y": 2.0, "wall": "west",    "height_m": 1.2, "room_type": "bedroom",    "note": "у входа"},
  {"kind": "socket",   "x": 3.2,  "y": 3.5, "wall": "west",    "height_m": 0.3, "room_type": "bedroom",    "note": "у кровати"},
  {"kind": "socket",   "x": 5.0,  "y": 3.0, "wall": "east",    "height_m": 0.3, "room_type": "bedroom",    "note": "рабочий стол"},
  {"kind": "light",    "x": 2.3,  "y": 3.0, "wall": "ceiling", "height_m": 2.5, "room_type": "kitchen",    "note": "свет"},
  {"kind": "socket",   "x": 1.4,  "y": 2.5, "wall": "west",    "height_m": 1.1, "room_type": "kitchen",    "note": "рабочая зона"},
  {"kind": "stove_plug","x": 2.5, "y": 3.8, "wall": "east",    "height_m": 0.3, "room_type": "kitchen",    "note": "электроплита"},
  {"kind": "light",    "x": 2.3,  "y": 1.0, "wall": "ceiling", "height_m": 2.5, "room_type": "bathroom",   "note": "влагозащищённый"},
  {"kind": "switch",   "x": 1.4,  "y": 0.0, "wall": "west",    "height_m": 1.2, "room_type": "bathroom",   "note": "снаружи"},
  {"kind": "socket",   "x": 2.7,  "y": 0.5, "wall": "east",    "height_m": 1.5, "room_type": "bathroom",   "note": "влагозащищённая, для фена"}
]}

ПРИМЕР 2 — та же квартира, только стояк плиты на кухне:
{"electrical": [
  {"kind": "light",    "x": 0.5,  "y": 9.0, "wall": "ceiling", "height_m": 2.5, "room_type": "hallway",   "note": "свет"},
  {"kind": "switch",   "x": 0.0,  "y": 9.0, "wall": "west",    "height_m": 1.2, "room_type": "hallway",   "note": "проходной"},
  {"kind": "light",    "x": 5.5,  "y": 9.0, "wall": "ceiling", "height_m": 2.5, "room_type": "living",     "note": "основной"},
  {"kind": "switch",   "x": 1.4,  "y": 9.0, "wall": "west",    "height_m": 1.2, "room_type": "living",     "note": "у входа"},
  {"kind": "socket",   "x": 3.0,  "y": 5.0, "wall": "north",   "height_m": 0.3, "room_type": "living",     "note": "TV"},
  {"kind": "socket",   "x": 5.0,  "y": 5.0, "wall": "north",   "height_m": 0.3, "room_type": "living",     "note": "у дивана"},
  {"kind": "light",    "x": 6.0,  "y": 6.5, "wall": "ceiling", "height_m": 2.5, "room_type": "bedroom",    "note": "свет"},
  {"kind": "switch",   "x": 1.4,  "y": 6.5, "wall": "west",    "height_m": 1.2, "room_type": "bedroom",    "note": "у входа"},
  {"kind": "socket",   "x": 1.4,  "y": 6.5, "wall": "west",    "height_m": 0.3, "room_type": "bedroom",    "note": "у кровати"},
  {"kind": "light",    "x": 6.0,  "y": 4.0, "wall": "ceiling", "height_m": 2.5, "room_type": "kitchen",    "note": "свет"},
  {"kind": "stove_plug","x": 6.0, "y": 4.0, "wall": "north",   "height_m": 0.3, "room_type": "kitchen",    "note": "электроплита"},
  {"kind": "socket",   "x": 1.4,  "y": 4.0, "wall": "west",    "height_m": 1.1, "room_type": "kitchen",    "note": "рабочая"},
  {"kind": "light",    "x": 6.0,  "y": 1.0, "wall": "ceiling", "height_m": 2.5, "room_type": "bathroom",   "note": "влагозащищённый"},
  {"kind": "vent_fan", "x": 6.0,  "y": 0.0, "wall": "south",   "height_m": 2.4, "room_type": "bathroom",   "note": "вытяжка"}
]}
"""

_PLUMBING_FEW_SHOT = """
ПРИМЕР — санузлы+кухня 2-комнатной (5×9м, мокрые зоны слева внизу):
{"plumbing": [
  {"fixture": "sink",         "x": 1.5, "y": 1.0, "room_type": "bathroom", "supplies": "hot+cold", "need_drain": true,  "note": "раковина"},
  {"fixture": "bathtub",      "x": 3.2, "y": 0.5, "room_type": "bathroom", "supplies": "hot+cold", "need_drain": true,  "note": "ванна 170×70"},
  {"fixture": "washing_mc",   "x": 2.0, "y": 1.5, "room_type": "bathroom", "supplies": "cold",     "need_drain": true,  "note": "стиральная машина"},
  {"fixture": "kitchen_sink", "x": 2.3, "y": 2.8, "room_type": "kitchen",  "supplies": "hot+cold", "need_drain": true,  "note": "мойка кухонная"},
  {"fixture": "sink",         "x": 0.8, "y": 0.5, "room_type": "wc",       "supplies": "cold",     "need_drain": true,  "note": "рукомойник"}
]}
"""

_LOWCURRENT_FEW_SHOT = """
ПРИМЕР — слаботочка 2-комнатной:
{"low_current": [
  {"kind": "router",      "x": 0.0, "y": 4.5, "wall": "west", "height_m": 1.5, "room_type": "hallway", "note": "роутер+патч-панель"},
  {"kind": "rj45",        "x": 3.0, "y": 9.0, "wall": "south","height_m": 0.3, "room_type": "living",   "note": "TV-приставка"},
  {"kind": "rj45",        "x": 5.0, "y": 3.0, "wall": "east", "height_m": 0.3, "room_type": "bedroom",  "note": "рабочий стол"},
  {"kind": "tv",          "x": 3.0, "y": 8.0, "wall": "west", "height_m": 1.2, "room_type": "living",   "note": "антенна TV"}
]}
"""

# ── Построение промпта ─────────────────────────────────────────────────────

def _build_room_context(fp: ApartmentFloorplan) -> str:
    """Описывает комнаты для LLM."""
    lines = []
    for i, r in enumerate(fp.rooms):
        ax, ay = r.x0, r.y0
        bx, by = r.x1, r.y1
        constraints = get_room_constraints(r.type)
        window = "окно есть" if r.touches_facade(fp.depth) else "внутренняя"
        lines.append(
            f"  [{i}] {r.type} («{r.name}») "
            f"({ax:.2f},{ay:.2f})-({bx:.2f},{by:.2f}) "
            f"{r.area:.1f}м² {window}"
        )
    return "\n".join(lines)


def _build_mep_system_prompt(system: str, fp: ApartmentFloorplan) -> str:
    """Строит системный промпт для конкретной MEP-системы."""
    room_ctx = _build_room_context(fp)

    if system == "electrical":
        return (
            "Ты — инженер-электрик. Расставь электроточки внутри уже размещённых комнат.\n\n"
            "Координаты квартиры и комнат:\n" + room_ctx + "\n\n"
            f"Габариты квартиры: x 0–{fp.width:.1f}м, y 0–{fp.depth:.1f}м.\n\n"
            "ПРАВИЛА (КМК 2.04.16-2005 + практика):\n"
            "- В каждой жилой комнате: 1 светильник (центр потолка), 1 выключатель (у входа, h=1.2м), "
            "минимум 2 розетки на стенах (h=0.3м)\n"
            "- В кухне: 1 светильник, 2-3 розетки (h=1.1м, над рабочей поверхностью), "
            "1 силовая розетка для электроплиты (h=0.3м, отдельная линия)\n"
            "- В прихожей: 1 светильник, 1 проходной выключатель\n"
            "- В санузле/WC: 1 влагозащищённый светильник, 1 выключатель снаружи, "
            "1 влагозащищённая розетка (h=1.5м, для фена/бритвы)\n"
            "- Координаты точек — в метрах, внутри границ комнаты\n"
            "- wall: 'north'(y=y0), 'south'(y=depth), 'east'(x=width), 'west'(x=0), 'ceiling'(потолок)\n"
            "- Точка на стене должна лежать на линии стены (x или y = граница комнаты)\n\n"
            "ПРИМЕРЫ РЕАЛЬНЫХ РАСКЛАДОК (подставь свои координаты под текущие комнаты):\n"
            + _ELECTRICAL_FEW_SHOT + "\n"
            "Верни ТОЛЬКО JSON без markdown:\n"
            '{"electrical": [{"kind": "light", "x": 0, "y": 0, "wall": "ceiling", '
            '"height_m": 2.5, "room_type": "hallway", "note": "..."}, ...]}'
        )

    elif system == "plumbing":
        return (
            "Ты — инженер-сантехник. Расставь сантехнические приборы в мокрых зонах.\n\n"
            "Координаты квартиры и комнат:\n" + room_ctx + "\n\n"
            "ПРАВИЛА (КМК 2.04.01-98):\n"
            "- Ванна/Kitchen_sink — рядом со стояком ВК (обычно в углу мокрой зоны)\n"
            "- Унитаз — в WC или санузле, отдельно от ванны (раздельный санузел)\n"
            "- Стиральная машина — в санузле или кухне\n"
            "- Горячее водоснабжение только для sink/kitchen_sink/bathtub/shower\n"
            "- Координаты — в метрах, внутри комнаты\n\n"
            "ПРИМЕР\n" + _PLUMBING_FEW_SHOT + "\n"
            "Верни ТОЛЬКО JSON:\n"
            '{"plumbing": [{"fixture": "sink", "x": 1.5, "y": 1.0, '
            '"room_type": "bathroom", "supplies": "hot+cold", "need_drain": true, "note": ""}, ...]}'
        )

    elif system == "low_current":
        return (
            "Ты — инженер слаботочных систем. Расставь точки связи и ТВ.\n\n"
            "Координаты квартиры и комнат:\n" + room_ctx + "\n\n"
            "ПРАВИЛА:\n"
            "- Роутер+патч-панель — в прихожей (центральная точка)\n"
            "- RJ-45 розетка — в каждой жилой комнате (гостиная/спальня), h=0.3м\n"
            "- TV-розетка — в гостиной, рядом с местом для телевизора, h=1.2м\n"
            "- Координаты — в метрах, на стене\n\n"
            "ПРИМЕР\n" + _LOWCURRENT_FEW_SHOT + "\n"
            "Верни ТОЛЬКО JSON:\n"
            '{"low_current": [{"kind": "rj45", "x": 3, "y": 9, '
            '"wall": "south", "height_m": 0.3, "room_type": "living", "note": ""}, ...]}'
        )

    return ""


# ── Парсинг ответа LLM ─────────────────────────────────────────────────────

def _parse_electrical(raw: list) -> list[Socket]:
    points = []
    for r in raw:
        try:
            points.append(Socket(
                kind=str(r["kind"]),
                x=float(r["x"]),
                y=float(r["y"]),
                wall=str(r["wall"]),
                height_m=float(r.get("height_m", 2.5)),
                room_type=str(r.get("room_type", "")),
                note=str(r.get("note", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return points


def _parse_plumbing(raw: list) -> list[PlumbingFixture]:
    fixtures = []
    for r in raw:
        try:
            fixtures.append(PlumbingFixture(
                fixture=str(r["fixture"]),
                x=float(r["x"]),
                y=float(r["y"]),
                room_type=str(r.get("room_type", "")),
                supplies=str(r.get("supplies", "cold")),
                need_drain=bool(r.get("need_drain", True)),
                note=str(r.get("note", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return fixtures


def _parse_low_current(raw: list) -> list[LowCurrentPoint]:
    points = []
    for r in raw:
        try:
            points.append(LowCurrentPoint(
                kind=str(r["kind"]),
                x=float(r["x"]),
                y=float(r["y"]),
                wall=str(r.get("wall", "ceiling")),
                height_m=float(r.get("height_m", 0.3)),
                room_type=str(r.get("room_type", "")),
                note=str(r.get("note", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return points


# ── Основной вызов ─────────────────────────────────────────────────────────

def generate_mep_layout(
    fp: ApartmentFloorplan,
    systems: list[str] | None = None,
    model: str = "local-model",
    timeout: float = 60.0,
) -> MepLayout:
    """
    Генерирует MEP-раскладку для готовой планировки квартиры.

    systems: какие системы генерировать. По умолчанию ["electrical", "plumbing", "low_current"].

    Вызов для каждой системы — отдельный запрос к LM Studio.
    Если запрос не удался — возвращает пустой MepLayout(source="template").
    """
    try:
        import requests
        from ..config import LM_STUDIO_BASE_URL
    except Exception:
        return MepLayout(source="template")

    systems = systems or ["electrical", "plumbing", "low_current"]
    layout = MepLayout(source="neural")

    for system in systems:
        sys_prompt = _build_mep_system_prompt(system, fp)
        if not sys_prompt:
            continue

        user_prompt = (
            f"Расставь {system} в этой квартире. "
            f"Комнаты: {', '.join(r.type for r in fp.rooms)}."
        )

        # repair-цикл (2 попытки)
        for attempt in range(2):
            try:
                resp = requests.post(
                    f"{LM_STUDIO_BASE_URL}/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 2000,
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
            except Exception:
                break

            json_match = re.search(r'\{[\s\S]*\}', raw)
            if not json_match:
                if attempt == 0:
                    user_prompt = "Твой ответ не содержит JSON. Верни ТОЛЬКО JSON, без текста."
                    continue
                break

            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                if attempt == 0:
                    user_prompt = "JSON невалиден. Верни исправленный JSON без лишнего текста."
                    continue
                break

            if system == "electrical" and "electrical" in data:
                layout.electrical = _parse_electrical(data["electrical"])
                break
            elif system == "plumbing" and "plumbing" in data:
                layout.plumbing = _parse_plumbing(data["plumbing"])
                break
            elif system == "low_current" and "low_current" in data:
                layout.low_current = _parse_low_current(data["low_current"])
                break

    return layout
