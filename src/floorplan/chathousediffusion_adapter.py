"""
Фаза 5 — ChatHouseDiffusion как ещё один "нейро"-генератор планировки
(floorplan_mode="chathousediffusion"), альтернатива LM Studio (neural.py).

ChatHouseDiffusion (github.com/ChatHouseDiffusion/chathousediffusion,
arXiv:2410.11908, Apache-2.0) — LLM превращает текстовое описание квартиры
в граф комнат, диффузионная модель (Graphormer + своя CUDA-заточенная
denoising_diffusion_pytorch) по этому графу и маске контура рисует
планировку. НО: несмотря на описание в статье, реально выпущенный код
возвращает не координаты, а растровую карту классов комнат (см.
vectorize.py) — векторизацию делаем мы сами после получения растра.

Мост реализован как ОТДЕЛЬНЫЙ ПРОЦЕСС, а не как вендоринг их кода в наш
бэкенд: ChatHouseDiffusion тянет PyTorch + DGL + свой форк
denoising_diffusion_pytorch с CUDA-заточенными местами, и требует свои
веса (несколько ГБ, скачиваются из Tsinghua Cloud — недоступно из этой
песочницы и не нужно тащить в облегчённый веб-бэкенд). Пользователь
разворачивает их репозиторий сам (свой venv, свои веса), кладёт в него
chd_bridge/predict_floorplan.py (см. соседнюю директорию в корне репо) и
указывает пути через CHD_PYTHON/CHD_BRIDGE_SCRIPT — тогда этот адаптер
просто вызывает его подпроцессом и передаёт/получает JSON через stdin/stdout.

Контракт с вызывающим кодом идентичен neural.generate_floorplan_llm(): при
ЛЮБОЙ неудаче (бридж не настроен, процесс упал, вернул мусор, планировка
не прошла нормы) — возвращает None, и вызывающий код обязан откатиться на
детерминированный солвер (generate_floorplan). Никакого repair-loop (в
отличие от neural.py) — для V1 это явное упрощение: полный проход через
LLM+диффузию в чужом процессе слишком дорог, чтобы гонять его в цикле
починки; при ошибке норм просто откатываемся на солвер.
"""
import json
import os
import subprocess
import tempfile
from collections import Counter

from .ir import ApartmentFloorplan
from .norms import validate_floorplan
from .geometry import connect_adjacent_rooms
from .vectorize import raster_to_rooms, CHATHOUSEDIFFUSION_CLASS_TO_TYPE
from .solver import _default_program

# Растровые маски ChatHouseDiffusion учат на данных, где между комнатами
# есть буферная полоса пикселей класса "InteriorWall" (16) — после
# векторизации это оставляет между полигонами комнат небольшой зазор,
# который не пройдёт дефолтный жёсткий допуск geometry.collinear_overlap
# (0.02 м). Расширяем допуск, чтобы такие "почти смежные" комнаты всё
# равно считались соединёнными дверью.
_WALL_GAP_TOLERANCE_M = 0.25


def _rasterize_footprint(width_m: float, depth_m: float, resolution: int = 64):
    """
    Строит бинарную маску контура квартиры в формате, ожидаемом их UI
    (ui.py: get_binary() — чёрный прямоугольник контура на белом фоне,
    квадратное изображение resolution×resolution с отступом от края).

    Возвращает (PIL.Image в режиме "L", px_per_meter) — масштаб связывает
    пиксели маски с метрами квартиры (тем самым же путём при обратной
    конвертации растра предсказания в метры в raster_to_rooms).
    """
    from PIL import Image, ImageDraw

    margin_frac = 0.06
    scale = (1.0 - 2 * margin_frac) * resolution / max(width_m, depth_m)
    px_w = width_m * scale
    px_h = depth_m * scale
    ox = (resolution - px_w) / 2
    oy = (resolution - px_h) / 2

    img = Image.new("L", (resolution, resolution), color=255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([ox, oy, ox + px_w, oy + px_h], fill=0)
    return img, scale


def _snap_to_footprint(rooms, width: float, depth: float, tol: float):
    """
    Контур, полученный трассировкой растра (cv2.findContours/approxPolyDP),
    систематически не дотягивается примерно на 1 пиксель до истинной границы
    маски (артефакт растровых пиксельных координат) — комната, которая по
    смыслу целиком примыкает к фасаду/входу, после векторизации может
    оказаться на доли пикселя внутри контура и не пройти строгую проверку
    touches_facade() (допуск 0.05 м) в norms.py. Подтягиваем вершины полигона,
    оказавшиеся в пределах tol от истинных границ квартиры (0/width/0/depth),
    ровно на границу — единственное место, где такое "растровое дребезжание"
    имеет смысл лечить, т.к. только здесь известны истинные метровые границы
    footprint'а (сама vectorize.raster_to_rooms работает с произвольным
    растром без этого контекста).
    """
    from .ir import RoomBox

    snapped = []
    for r in rooms:
        if not r.polygon:
            snapped.append(r)
            continue
        pts = []
        for x, y in r.polygon:
            if abs(x) <= tol:
                x = 0.0
            elif abs(x - width) <= tol:
                x = width
            if abs(y) <= tol:
                y = 0.0
            elif abs(y - depth) <= tol:
                y = depth
            pts.append((x, y))
        snapped.append(RoomBox.from_polygon(r.type, pts, name=r.name))
    return snapped


def _entry_hub_index(rooms, doors) -> int | None:
    """Комната с наибольшим числом соединений — туда ставим вход с площадки
    (тот же эвристический выбор "прихожей", что делает солвер явным типом)."""
    if not rooms:
        return None
    counts = Counter()
    for d in doors:
        if d.room_a >= 0:
            counts[d.room_a] += 1
        if d.room_b >= 0:
            counts[d.room_b] += 1
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def generate_floorplan_chd(
    width: float,
    depth: float,
    room_count: int = 2,
    entry_side: str = "west",
    program: dict | None = None,
    description: str = "",
    bridge_python: str | None = None,
    bridge_script: str | None = None,
    timeout: float = 120.0,
) -> ApartmentFloorplan | None:
    """
    Пытается сгенерировать планировку через ChatHouseDiffusion (в отдельном
    процессе пользовательского окружения). Возвращает готовый
    ApartmentFloorplan (source="chathousediffusion") при успехе, либо None
    при любой неудаче — вызывающий код обязан откатиться на солвер.

    bridge_python/bridge_script — путь к python-интерпретатору и к
    chd_bridge/predict_floorplan.py внутри окружения пользователя, где
    развёрнут ChatHouseDiffusion; если не переданы явно, берутся из
    переменных окружения CHD_PYTHON/CHD_BRIDGE_SCRIPT. Если ни там, ни там
    не заданы (или скрипта не существует) — считается, что интеграция не
    настроена, возвращается None без ошибки (тихий фолбэк на солвер).
    """
    bridge_python = bridge_python or os.environ.get("CHD_PYTHON")
    bridge_script = bridge_script or os.environ.get("CHD_BRIDGE_SCRIPT")
    if not bridge_python or not bridge_script or not os.path.isfile(bridge_script):
        return None

    prog = program or _default_program(room_count)
    mask_img, px_per_meter = _rasterize_footprint(width, depth)

    with tempfile.TemporaryDirectory() as tmpdir:
        mask_path = os.path.join(tmpdir, "mask.png")
        mask_img.save(mask_path)

        request = {
            "mask_path": mask_path,
            "width_m": width,
            "depth_m": depth,
            "px_per_meter": px_per_meter,
            "entry_side": entry_side,
            "room_program": prog,
            "description": description,
        }

        try:
            result = subprocess.run(
                [bridge_python, bridge_script],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None

        if result.returncode != 0:
            return None

        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            return None
        try:
            payload = json.loads(lines[-1])
            label_grid = payload["label_grid"]
            out_px_per_meter = float(payload.get("px_per_meter", px_per_meter))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    try:
        rooms = raster_to_rooms(
            label_grid, out_px_per_meter, class_to_type=CHATHOUSEDIFFUSION_CLASS_TO_TYPE,
        )
    except Exception:
        return None
    if not rooms:
        return None

    rooms = _snap_to_footprint(rooms, width, depth, tol=2.0 / out_px_per_meter)
    doors = connect_adjacent_rooms(rooms, eps=_WALL_GAP_TOLERANCE_M)

    hub_idx = _entry_hub_index(rooms, doors)
    if hub_idx is None:
        return None
    from .ir import DoorSpec
    entry_x = 0.0 if entry_side == "west" else width
    doors.append(DoorSpec(x=entry_x, y=rooms[hub_idx].cy, wall_axis="y",
                           room_a=-1, room_b=hub_idx, width=0.9, kind="entry"))

    fp = ApartmentFloorplan(width=width, depth=depth, entry_side=entry_side,
                             rooms=rooms, doors=doors, source="chathousediffusion")
    issues = validate_floorplan(fp)
    if any(i["severity"] == "error" for i in issues):
        return None

    return fp
