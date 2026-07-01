"""
Растр → векторные комнаты (интеграция ChatHouseDiffusion, Путь C).

ChatHouseDiffusion возвращает попиксельную карту классов комнат, а не
JSON/координаты — несмотря на то что статья (arXiv:2410.11908) описывает
диффузию как денойзинг координат углов, реально выпущенный код
(denoising_diffusion_pytorch/image_process.py в их репозитории) работает
с растровыми масками: каждый пиксель — индекс класса комнаты, раскраска —
только для визуализации в их UI. Векторных полигонов в их коде нет.

Эта функция строит их сама: связные компоненты по классу → внешний контур
(cv2.findContours) → упрощение полигона (cv2.approxPolyDP) → перевод из
пикселей в метры по известному масштабу маски (мы сами её растеризовали
под известные метры квартиры — см. chathousediffusion_adapter.py).
"""
import numpy as np

from .ir import RoomBox

# Соответствие класса разметки нашим типам комнат.
#
# Точная таксономия ChatHouseDiffusion (18 классов, room_label в их
# denoising_diffusion_pytorch/image_process.py, стандартная для RPLAN):
#   0 LivingRoom, 1 MasterRoom, 2 Kitchen, 3 Bathroom, 4 DiningRoom,
#   5 ChildRoom, 6 StudyRoom, 7 SecondRoom, 8 GuestRoom, 9 Balcony,
#   10 Entrance, 11 Storage, 12 Wall-in, 13 External, 14 ExteriorWall,
#   15 FrontDoor, 16 InteriorWall, 17 InteriorDoor.
#
# Наш IR (ir.ROOM_TYPES) знает только living/bedroom/kitchen/bathroom/wc/
# hallway — балкон/кладовку/гардеробную (9, 11, 12) явного соответствия не
# имеют и при векторизации пропускаются (см. raster_to_rooms). Двери/стены
# (14-17) — тоже не "комнаты"; топологию дверей между извлечёнными
# помещениями считаем сами через geometry.connect_adjacent_rooms (тот же
# путь, что и для солвера/LLM), а не пытаемся парсить их door-классы.
CHATHOUSEDIFFUSION_CLASS_TO_TYPE: dict[int, str] = {
    0: "living",
    1: "bedroom",
    2: "kitchen",
    3: "bathroom",
    4: "living",    # DiningRoom — нет отдельного типа в нашей норм-базе
    5: "bedroom",   # ChildRoom
    6: "bedroom",   # StudyRoom
    7: "bedroom",   # SecondRoom
    8: "bedroom",   # GuestRoom
    10: "hallway",  # Entrance
}

# Общий (не привязанный к конкретной модели) дефолт для тестов/других
# источников растра — простая нумерация 1..5.
DEFAULT_CLASS_TO_TYPE: dict[int, str] = {
    1: "living",
    2: "bedroom",
    3: "kitchen",
    4: "bathroom",
    5: "wc",
}

_RU_NAMES = {
    "living": "Гостиная", "bedroom": "Спальня", "kitchen": "Кухня",
    "bathroom": "Санузел", "wc": "Туалет", "hallway": "Прихожая",
}


def raster_to_rooms(
    label_grid,
    px_per_meter: float,
    class_to_type: dict[int, str] | None = None,
    min_area_px: int = 9,
    approx_epsilon_px: float = 1.5,
) -> list[RoomBox]:
    """
    label_grid: 2D-массив (numpy или вложенные списки) int — индекс класса
    комнаты на пиксель.
    px_per_meter: масштаб маски (пикселей на метр) — известен заранее, т.к.
    маску контура квартиры рисуем мы сами под известные размеры в метрах.
    min_area_px: компоненты меньше этого — шум диффузии, отбрасываются.
    approx_epsilon_px: точность упрощения контура (cv2.approxPolyDP) —
    больше значение → грубее (меньше углов), меньше → точнее повторяет
    "лестницу" растровых пикселей.

    Возвращает список RoomBox с заполненным .polygon (координаты в метрах,
    в той же локальной СК, что и маска: (0,0) — угол footprint'а).
    """
    import cv2

    class_to_type = class_to_type or DEFAULT_CLASS_TO_TYPE
    grid = np.asarray(label_grid, dtype=np.uint8)
    if grid.ndim != 2:
        raise ValueError(f"label_grid должен быть 2D, получено shape={grid.shape}")

    rooms: list[RoomBox] = []

    for class_id, room_type in class_to_type.items():
        mask = (grid == class_id).astype(np.uint8)
        if int(mask.sum()) < min_area_px:
            continue

        num_labels, components = cv2.connectedComponents(mask, connectivity=4)
        for comp_id in range(1, num_labels):
            comp_mask = (components == comp_id).astype(np.uint8)
            area_px = int(comp_mask.sum())
            if area_px < min_area_px:
                continue

            contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            approx = cv2.approxPolyDP(contour, approx_epsilon_px, closed=True)
            if len(approx) < 3:
                continue

            points_m = [(float(p[0][0]) / px_per_meter, float(p[0][1]) / px_per_meter) for p in approx]
            rooms.append(RoomBox.from_polygon(room_type, points_m, name=_RU_NAMES.get(room_type, room_type)))

    return rooms
