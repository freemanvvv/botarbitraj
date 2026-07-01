"""
Bridge-скрипт для интеграции ChatHouseDiffusion (github.com/ChatHouseDiffusion/
chathousediffusion, arXiv:2410.11908) в Construction AI Copilot.

ЭТОТ ФАЙЛ НЕ ЗАПУСКАЕТСЯ В РЕПОЗИТОРИИ ПРОЕКТА. Его нужно скопировать в
КОРЕНЬ вашего собственного локального checkout'а chathousediffusion (туда
же, где лежат predict.py, ui.py, prompt2json/, api_info.json, predict_model/
с распакованным чекпоинтом) — только там доступны их модули (predict,
prompt2json) и их окружение (torch/dgl/веса). Сам Construction AI Copilot
вызывает этот скрипт подпроцессом из
src/floorplan/chathousediffusion_adapter.py:generate_floorplan_chd(),
передавая путь до python-интерпретатора их venv (CHD_PYTHON) и путь до
этого скопированного файла (CHD_BRIDGE_SCRIPT).

Протокол: JSON-запрос читается из stdin, JSON-ответ пишется ПОСЛЕДНЕЙ
строкой в stdout (остальные строки stdout/stderr можно использовать для
диагностики — адаптер парсит только последнюю непустую строку).

Запрос (stdin):
    {"mask_path": str, "width_m": float, "depth_m": float,
     "px_per_meter": float, "entry_side": "west"|"east",
     "room_program": {"facade": [...], "wet": [...]}, "description": str}

Ответ (последняя строка stdout):
    {"label_grid": [[int, ...], ...], "px_per_meter": float}
    label_grid — индексы классов их таксономии room_label (см.
    denoising_diffusion_pytorch/image_process.py в их репо): 0 LivingRoom,
    1 MasterRoom, 2 Kitchen, 3 Bathroom, 4 DiningRoom, 5 ChildRoom,
    6 StudyRoom, 7 SecondRoom, 8 GuestRoom, 9 Balcony, 10 Entrance,
    11 Storage, 12 Wall-in, 13 External, 14 ExteriorWall, 15 FrontDoor,
    16 InteriorWall, 17 InteriorDoor.

ВАЖНОЕ ОГРАНИЧЕНИЕ (проверено чтением их исходников, не документации):
их Trainer.predict(mask, json_text) возвращает уже готовое RGB PIL-изображение
(см. ui.py: `prediction = self.trainer.predict(mask, new_text, repredict=repredict)`,
результат сразу показывается как картинка) — НЕ индексы классов. Обратной
функции rgb→class у них в коде нет (image_process.py содержит только
convert_gray_to_rgb/convert_mult_to_rgb — в одну сторону). Более того, их
собственная цветовая палитра (get_color_map()) НЕ инъективна:
классы 5/6/7/8 (ChildRoom/StudyRoom/SecondRoom/GuestRoom) все окрашены
в один и тот же цвет (255,215,0), а классы 0/10/12
(LivingRoom/Entrance/Wall-in) — тоже в один цвет (238,232,170). Обратное
сопоставление "по ближайшему цвету" в принципе не может различить эти
классы внутри своей группы.

Для нашего пайплайна это частично безобидно: наша таксономия
(vectorize.CHATHOUSEDIFFUSION_CLASS_TO_TYPE) и так сводит 5/6/7/8 к одному
типу "bedroom" — коллизия там не теряет информацию. Но 0 (LivingRoom) и
10 (Entrance/прихожая) у нас — РАЗНЫЕ типы ("living" и "hallway"), и их
цветовая коллизия — реальная потеря информации при работе только с
готовым RGB-кадром. Ниже это решается эвристикой (см. _split_living_entrance):
среди компонент цветовой группы 0/10/12 самая близкая к области цвета
FrontDoor (класс 15, свой уникальный цвет) или ближайшая к стороне входа
(entry_side) считается прихожей, остальные — гостиной/кладовой (гостиная
как более вероятный вариант по умолчанию).

Если у вас есть возможность подправить их Trainer.predict (или добавить
хук), чтобы вернуть argmax-класс ДО конвертации в RGB (см. их
convert_mult_to_rgb — argmax там уже вычисляется, просто не возвращается
наружу) — это ТОЧНЕЕ и предпочтительнее эвристики ниже. См. функцию
_predict_raw_classes(), которая пытается это сделать первым делом и
только при неудаче откатывается на _predict_via_rgb().
"""
import json
import os
import sys

import numpy as np
import numpy as np
from PIL import Image

# Классы их таксономии (room_label, image_process.py), для читаемости.
LIVING, MASTER, KITCHEN, BATHROOM, DINING = 0, 1, 2, 3, 4
CHILD, STUDY, SECOND, GUEST, BALCONY = 5, 6, 7, 8, 9
ENTRANCE, STORAGE, WALLIN, EXTERNAL, EXTWALL = 10, 11, 12, 13, 14
FRONTDOOR, INTWALL, INTDOOR = 15, 16, 17

# Итоговый cmap (get_color_map() применённый к 18 классам) — вычислено
# верно из их исходника: color[cIdx] где
#   color = [[238,232,170],[255,165,0],[240,128,128],[173,216,210],
#            [107,142,35],[218,112,214],[221,160,221],[255,215,0],
#            [0,0,0],[255,225,25],[128,128,128],[255,255,255]]
#   cIdx (0-based) = [0,1,2,3,5,7,7,7,7,4,0,6,0,11,8,9,11,11]
CMAP = [
    [238, 232, 170],  # 0  LivingRoom
    [255, 165, 0],    # 1  MasterRoom
    [240, 128, 128],  # 2  Kitchen
    [173, 216, 210],  # 3  Bathroom
    [218, 112, 214],  # 4  DiningRoom
    [255, 215, 0],    # 5  ChildRoom
    [255, 215, 0],    # 6  StudyRoom
    [255, 215, 0],    # 7  SecondRoom
    [255, 215, 0],    # 8  GuestRoom
    [107, 142, 35],   # 9  Balcony
    [238, 232, 170],  # 10 Entrance   (коллизия с LivingRoom/Wall-in)
    [221, 160, 221],  # 11 Storage
    [238, 232, 170],  # 12 Wall-in    (коллизия с LivingRoom/Entrance)
    [255, 255, 255],  # 13 External
    [0, 0, 0],        # 14 ExteriorWall
    [255, 225, 25],   # 15 FrontDoor
    [255, 255, 255],  # 16 InteriorWall (коллизия с External/InteriorDoor)
    [255, 255, 255],  # 17 InteriorDoor
]

# Классы, которые нас интересуют как "комнаты" (остальное — стены/двери/фон,
# топологию которых мы считаем сами через geometry.connect_adjacent_rooms).
_ROOM_CLASSES = [LIVING, MASTER, KITCHEN, BATHROOM, DINING, CHILD, STUDY, SECOND, GUEST, ENTRANCE]
# При коллизии цвета 0/10/12 берём по умолчанию LivingRoom — Entrance
# переопределяется эвристикой _split_living_entrance ниже.
#
# EXTERNAL обязателен в этом списке, даже хотя это не "комната": белый
# (255,255,255) — это цвет фона/InteriorWall/InteriorDoor (13/16/17 делят
# один цвет), и если не дать ему представителя, ближайший по евклидовому
# расстоянию цвет для белого — ЖИВАЯ КОМНАТА (238,232,170), а не что-то
# нейтральное (проверено численно: 8043 против ~10270 у следующего
# кандидата). Без этой записи весь фон/стены/двери на реальном
# предсказании превратились бы в один гигантский ложный LivingRoom.
_COLOR_GROUP_DEFAULT_CLASS = {tuple(CMAP[c]): c for c in [LIVING, MASTER, KITCHEN, BATHROOM,
                                                            DINING, CHILD, BALCONY, STORAGE,
                                                            EXTWALL, FRONTDOOR, EXTERNAL]}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Нормы КМК 2.08.01-89 — минимальные площади и ширины по типу помещения
# (синхронизировано с src/floorplan/norms.py в Construction AI Copilot)
_ROOM_NORMS = {
    "living":   {"min_area": 8.0, "min_width": 2.5, "needs_window": True,  "zone": "facade"},
    "bedroom":  {"min_area": 8.0, "min_width": 2.5, "needs_window": True,  "zone": "facade"},
    "kitchen":  {"min_area": 8.0, "min_width": 1.7, "needs_window": True,  "zone": "wet"},
    "bathroom": {"min_area": 2.7, "min_width": 1.2, "needs_window": False, "zone": "wet"},
    "wc":       {"min_area": 1.2, "min_width": 0.8, "needs_window": False, "zone": "wet"},
    "hallway":  {"min_area": 1.8, "min_width": 1.4, "needs_window": False, "zone": "entry"},
    "dining":   {"min_area": 6.0, "min_width": 2.0, "needs_window": True,  "zone": "facade"},
    "storage":  {"min_area": 1.5, "min_width": 0.8, "needs_window": False, "zone": "wet"},
}


def _build_prompt_text(width_m: float, depth_m: float, entry_side: str,
                        room_program: dict, description: str) -> str:
    """Формирует текстовое описание квартиры для prompt2json() CHD.

    Включает габариты, нормы помещений, зонирование и требования к окнам —
    чтобы LLM (через prompt2json) построила граф, отражающий реальные
    строительные нормы КМК Узбекистана, а не абстрактную западную планировку.
    """
    rooms = list(room_program.get("facade", [])) + list(room_program.get("wet", []))

    # Добавляем нормативные требования по каждому типу комнаты
    norm_lines = []
    for r in sorted(set(rooms)):
        n = _ROOM_NORMS.get(r, _ROOM_NORMS["hallway"])
        parts = [f"{r}: мин. площадь {n['min_area']} м², мин. ширина {n['min_width']} м"]
        if n["needs_window"]:
            parts.append("обязательно окно на фасаде")
        if n["zone"] == "facade":
            parts.append("жилая зона, у фасада")
        elif n["zone"] == "wet":
            parts.append("мокрая зона, ближе к входу")
        norm_lines.append(", ".join(parts))

    norm_block = "\n".join(norm_lines)

    # Определяем зоны компактно
    wet_types = [r for r in sorted(set(rooms)) if _ROOM_NORMS.get(r, {}).get("zone") == "wet"]
    facade_types = [r for r in sorted(set(rooms)) if _ROOM_NORMS.get(r, {}).get("zone") == "facade"]

    zones_parts = []
    if wet_types:
        zones_parts.append(f"Мокрые помещения ({', '.join(wet_types)}) — компактно, рядом со стояком ВК, у стороны входа")
    if facade_types:
        zones_parts.append(f"Жилые помещения ({', '.join(facade_types)}) — все с окнами на фасад, у противоложной от входа стены")

    zones_block = "; ".join(zones_parts) if zones_parts else ""

    entry_label = "слева" if entry_side == "west" else "справа"

    parts = [
        f"Квартира {width_m:.1f}x{depth_m:.1f} м,",
        f"вход {entry_label} по длинной стороне, фасад с окнами — противоположная сторона.",
        f"Состав помещений: {', '.join(rooms)}." if rooms else "",
        f"Нормы:\n{norm_block}" if norm_lines else "",
        f"Зонирование: {zones_block}." if zones_block else "",
        description or "",
    ]
    return "\n\n".join(p for p in parts if p)


def _make_llm_client():
    """OpenAI-совместимый клиент — та же конвенция api_info.json, что и у
    их собственных ui.py/predict.py (api_key/base_url/model в корне
    их checkout'а), плюс возможность переопределить через переменные
    окружения CHD_LLM_BASE_URL/CHD_LLM_API_KEY/CHD_LLM_MODEL (удобно для
    локальных LLM вроде LM Studio/Ollama, где ключ не нужен)."""
    from openai import OpenAI

    base_url = os.environ.get("CHD_LLM_BASE_URL")
    api_key = os.environ.get("CHD_LLM_API_KEY")
    model = os.environ.get("CHD_LLM_MODEL")

    if not base_url or not api_key:
        # Try CWD first, then script directory
        info = {}
        for _p in ["api_info.json", os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_info.json")]:
            try:
                with open(_p, encoding="utf-8") as f:
                    info = json.load(f)
                    break
            except (OSError, json.JSONDecodeError):
                continue
        base_url = base_url or info.get("base_url")
        api_key = api_key or info.get("api_key")
        model = model or info.get("model")

    client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)
    return client, (model or "llama3:instruct")


def _predict_raw_classes(trainer, mask_img, json_text: str):
    """Пытается получить индексы классов ДО конвертации в RGB — точнее
    эвристики по цвету. Их публичный Trainer.predict() этого не отдаёт,
    поэтому это best-effort: смотрим, нет ли на trainer каких-то более
    низкоуровневых методов (типичные имена для diffusion-моделей этого
    стиля). Если ничего не нашлось — возвращает None, и вызывающий код
    откатывается на _predict_via_rgb().
    """
    for attr in ("predict_classes", "predict_raw", "sample_classes"):
        fn = getattr(trainer, attr, None)
        if callable(fn):
            try:
                out = fn(mask_img, json_text)
                arr = np.asarray(out)
                if arr.ndim == 2:
                    return arr.astype(np.int64)
            except Exception as e:
                _log(f"_predict_raw_classes: {attr} не сработал: {e}")
    return None


def _nearest_class_for_pixel(rgb: np.ndarray) -> np.ndarray:
    """rgb: (H,W,3) uint8 → (H,W) индексы классов по ближайшему цвету CMAP.
    При коллизии цвета несколько классов делят один цвет — берётся
    представитель из _COLOR_GROUP_DEFAULT_CLASS (см. модульный докстринг)."""
    palette = np.array(list(_COLOR_GROUP_DEFAULT_CLASS.keys()), dtype=np.int64)
    class_ids = np.array(list(_COLOR_GROUP_DEFAULT_CLASS.values()), dtype=np.int64)

    flat = rgb.reshape(-1, 3).astype(np.int64)
    # (N,1,3) - (1,P,3) -> (N,P,3) -> расстояния (N,P)
    dists = np.sum((flat[:, None, :] - palette[None, :, :]) ** 2, axis=2)
    nearest = np.argmin(dists, axis=1)
    return class_ids[nearest].reshape(rgb.shape[0], rgb.shape[1])


def _split_living_entrance(class_grid: np.ndarray, entry_side: str) -> np.ndarray:
    """Цвет класса 0 (LivingRoom) после ближайшего сопоставления покрывает и
    Entrance(10)/Wall-in(12) — они неразличимы по цвету (см. докстринг файла).
    Эвристика: среди связных компонент этого цвета выбираем ближайшую к
    стороне входа (entry_side) и/или к пикселям FrontDoor (класс 15,
    уникальный цвет) — переклассифицируем её в Entrance(10). Остальные
    компоненты остаются LivingRoom(0)."""
    import cv2

    living_mask = (class_grid == LIVING).astype(np.uint8)
    if not living_mask.any():
        return class_grid

    num_labels, components = cv2.connectedComponents(living_mask, connectivity=4)
    if num_labels <= 2:
        return class_grid  # 0 или 1 компонента — делить нечего

    door_mask = (class_grid == FRONTDOOR)
    h, w = class_grid.shape
    entry_col = 0 if entry_side == "west" else w - 1

    best_comp, best_score = None, None
    for comp_id in range(1, num_labels):
        comp = components == comp_id
        ys, xs = np.nonzero(comp)
        if len(xs) == 0:
            continue
        if door_mask.any():
            dy, dx = np.nonzero(door_mask)
            score = float(np.min((xs[:, None] - dx[None, :]) ** 2 + (ys[:, None] - dy[None, :]) ** 2))
        else:
            score = float(np.min(np.abs(xs - entry_col)))
        if best_score is None or score < best_score:
            best_score, best_comp = score, comp_id

    if best_comp is not None:
        class_grid = class_grid.copy()
        class_grid[components == best_comp] = ENTRANCE
    return class_grid


def _predict_via_rgb(trainer, mask_img, json_text: str, entry_side: str) -> np.ndarray:
    prediction = trainer.predict(mask_img, json_text, repredict=True)
    rgb = np.asarray(prediction.convert("RGB"), dtype=np.uint8)
    class_grid = _nearest_class_for_pixel(rgb)
    class_grid = _split_living_entrance(class_grid, entry_side)
    return class_grid


def main() -> int:
    request = json.loads(sys.stdin.read())
    mask_path = request["mask_path"]
    width_m = float(request["width_m"])
    depth_m = float(request["depth_m"])
    px_per_meter = float(request["px_per_meter"])
    entry_side = request.get("entry_side", "west")
    room_program = request.get("room_program", {})
    description = request.get("description", "")

    debug_dir = os.environ.get("CHD_DEBUG_DIR")

    try:
        from predict import predict_prepare
        from prompt2json import prompt2json
    except ImportError as e:
        _log(f"Не удалось импортировать модули ChatHouseDiffusion: {e}. "
             f"Убедитесь, что скрипт скопирован в корень их checkout'а.")
        return 1

    mask_img = Image.open(mask_path).convert("L")

    client, model = _make_llm_client()
    prompt_text = _build_prompt_text(width_m, depth_m, entry_side, room_program, description)
    try:
        json_text, _structured = prompt2json(prompt_text, client=client, model=model)
    except Exception as e:
        _log(f"prompt2json не сработал: {e}")
        return 1

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_text)
        with open(os.path.join(debug_dir, "graph.json"), "w", encoding="utf-8") as f:
            f.write(json_text)

    trainer = predict_prepare()

    class_grid = _predict_raw_classes(trainer, mask_img, json_text)
    if class_grid is None:
        class_grid = _predict_via_rgb(trainer, mask_img, json_text, entry_side)

    if debug_dir:
        np.save(os.path.join(debug_dir, "label_grid.npy"), class_grid)

    # Vectorize rooms inline (cv2 available in CHD venv)
    try:
        import cv2
        _grid_np = np.asarray(class_grid, dtype=np.uint8)
        _rooms_list = []
        _class_to_type = {0:"living",1:"bedroom",2:"kitchen",3:"bathroom",4:"dining",
                          5:"bedroom",6:"bedroom",7:"bedroom",8:"bedroom",9:"balcony",
                          10:"hallway",11:"storage",12:"wall",15:"door"}
        for _cid, _ctype in _class_to_type.items():
            _mask = (_grid_np == _cid).astype(np.uint8)
            if _mask.sum() < 10: continue
            _num, _comps = cv2.connectedComponents(_mask, connectivity=4)
            for _comp in range(1, _num):
                _cmask = (_comps == _comp).astype(np.uint8)
                if _cmask.sum() < 10: continue
                _contours, _ = cv2.findContours(_cmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not _contours: continue
                _cnt = max(_contours, key=cv2.contourArea)
                _approx = cv2.approxPolyDP(_cnt, 0.5, closed=True)
                _xs = [p[0][0] / px_per_meter for p in _approx]
                _ys = [p[0][1] / px_per_meter for p in _approx]
                _rooms_list.append({"type":_ctype,"x0":round(min(_xs),3),"y0":round(min(_ys),3),
                                    "x1":round(max(_xs),3),"y1":round(max(_ys),3),
                                    "area":round(abs(max(_xs)-min(_xs))*abs(max(_ys)-min(_ys)),2)})
        _log(f"Vectorized {len(_rooms_list)} rooms from bridge")
    except Exception as e:
        _log(f"bridge vectorize: {e}")
        _rooms_list = []

    print(json.dumps({
        "label_grid": class_grid.astype(int).tolist(),
        "px_per_meter": px_per_meter,
        "rooms": _rooms_list,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
