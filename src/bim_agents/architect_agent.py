"""
Фаза 1 — ArchitectAgent.
LLM (Qwen3-14B) → BuildingProgram.
Единственный агент, где LLM генерирует данные.
Выдаёт ТОЛЬКО семантику (помещения, площади, смежность) — без координат.
"""
import json
import re
from .router import router
from .contracts import BuildingProgram


ARCHITECT_PROMPT = """Ты — BIM-архитектор. Из описания дома составь BuildingProgram в JSON.

Правила:
- Извлеки размеры, этажи, помещения
- Каждое помещение: id, name, storey (0=первый этаж), area_m2
- adjacency: какие помещения граничат
- Верни ТОЛЬКО JSON, без пояснений

{"project_name":"string","style":"modern","site":{"width_m":20,"depth_m":30},"footprint":{"width_m":12,"depth_m":9},"storeys":2,"ceiling_height_m":3.0,"wall_material":"aerated_concrete_D500","foundation":"strip","roof":"flat","rooms":[{"id":"living_01","name":"Гостиная","storey":0,"area_m2":35,"type":"IfcSpace:LIVING","exterior_windows":true,"min_width_m":4.0}],"adjacency":[["hall_01","living_01"]],"requirements":[]}
"""


def architect_agent(description: str) -> BuildingProgram:
    """Текст заказчика → BuildingProgram."""
    model = router.router_for(description)
    response = router.chat(model, [
        {"role": "system", "content": ARCHITECT_PROMPT},
        {"role": "user", "content": description},
    ])

    # Извлекаем JSON из ответа
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
    if json_match:
        response = json_match.group(1)

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        raise ValueError(f"ArchitectAgent не смог распарсить ответ LLM")

    return BuildingProgram(**data)
