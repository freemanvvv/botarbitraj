"""
Конвертер RPLAN → наш mosaic-формат шаблонов (house_templates.json).

RPLAN (Wu et al., "Data-driven Interior Plan Generation for Residential
Buildings", SIGGRAPH Asia 2019) — ~80k реальных планов квартир, каждый как
4-канальный PNG 256×256. Раскладка каналов и таксономия 18 классов сверены
с эталонным ридером rplanpy (rplanpy.data.RplanData / rplanpy.utils.
ROOM_CLASS): каналы [boundary, category, instance, inside]; комнаты —
regionprops по instance с типом = мода category внутри экземпляра; передняя
дверь — boundary==255. Тот же список классов задокументирован и в
src/floorplan/vectorize.py.

Датасет открыт (зеркала на Kaggle/Zenodo/GitHub-релизах, ридер
`pip install rplanpy`), но политика сети этого окружения пускает только
пакетные реестры, поэтому скачивание/прогон по реальным данным делается на
вашей машине (см. CLI ниже), а не здесь.

Наш движок (layout_templates.py) умеет работать только с ПРЯМОУГОЛЬНОЙ
мозаикой (x_cuts/y_cuts + комнаты-прямоугольники, без щелей и наложений).
Комнаты RPLAN — произвольные полигоны, поэтому конвертер не «векторизует всё
подряд», а честно ОТБИРАЕТ только те планы, которые выражаются как чистая
прямоугольная мозаика (slicing-раскладка), и отбрасывает L-образные/непрямо-
угольные. Это осознанный фильтр: качество шаблонов важнее их количества.

Пайплайн (image_to_template):
  1. Ориентация: по центроиду FrontDoor (класс 15) план поворачивается так,
     чтобы фасад со входом оказался при y=0 (наша конвенция).
  2. Сегментация комнат по каналу instance, тип — по мажоритарному классу
     category внутри экземпляра; классы-небудки (балкон/стены/двери/внешнее)
     отбрасываются, остальные мапятся в наши категории.
  3. Сетка разрезов из рёбер bounding-box'ов комнат со снапом близких рёбер.
  4. Проверка мозаичности: каждая ячейка сетки принадлежит ровно одной
     комнате, каждая комната — сплошной прямоугольник ячеек. Иначе — отказ.
  5. Проверка точности: средний IoU реконструированных прямоугольников с
     исходными пиксельными масками ≥ порога. Иначе — отказ.
  6. Нормировка разрезов в доли 0..1 и запись в формате house_templates.json.

RPLAN распространяется по заявке, поэтому в этом репозитории самих данных
нет и живого прогона по датасету здесь не было; проверяется ядро алгоритма
на синтетических RPLAN-подобных массивах (tests/test_rplan_convert.py),
структурно идентичных формату реальных PNG. CLI ниже — для запуска на вашей
локальной копии RPLAN.

Поддерживаются два входных формата:
  • канонический RPLAN — каталог 4-канальных PNG (image_to_template);
  • Graph2Plan .mat — struct-массив `data` с боксами комнат gtBoxNew + rType
    + boundary (graph2plan_mat_to_templates); распространённое зеркало на
    Kaggle (lkerkarabulut/rplan-dataset2025) — именно этот формат.

CLI:
    # RPLAN PNG:
    python -m src.bim_agents.rplan_convert <dir_с_png> --out out.json ...
    # Graph2Plan .mat:
    python -m src.bim_agents.rplan_convert <data_train.mat> --graph2plan --out out.json \
        [--limit N] [--append src/bim_agents/house_templates.json] [--min-iou 0.7] [--snap 4]
"""
from __future__ import annotations

import json
import os
from collections import Counter

import numpy as np

# Таксономия RPLAN (18 классов) → наши категории зонирования
# (house_layout_dataset.json). Балкон/внешнее/стены/двери — не комнаты
# (None или отсутствие ключа = пропустить). Совпадает по смыслу с
# CHATHOUSEDIFFUSION_CLASS_TO_TYPE из vectorize.py, но целевой словарь —
# house-движка (utility вместо storage, wet вместо bathroom/wc).
RPLAN_CLASS_TO_CATEGORY: dict[int, str | None] = {
    0: "living",    # LivingRoom
    1: "bedroom",   # MasterRoom
    2: "kitchen",   # Kitchen
    3: "wet",       # Bathroom
    4: "living",    # DiningRoom
    5: "bedroom",   # ChildRoom
    6: "bedroom",   # StudyRoom
    7: "bedroom",   # SecondRoom
    8: "bedroom",   # GuestRoom
    9: None,        # Balcony — не отапливаемое помещение, пропускаем
    10: "hall",     # Entrance
    11: "utility",  # Storage
    12: "utility",  # Wall-in (гардеробная)
    # 13 External, 14 ExteriorWall, 15 FrontDoor, 16 InteriorWall,
    # 17 InteriorDoor — не комнаты
}
RPLAN_FRONT_DOOR = 15
# В канонической раскладке RPLAN (rplanpy.data.RplanData) передняя дверь
# кодируется в канале boundary значением 255, а не классом 15 в канале
# category. Ориентацию делаем по boundary==255, если он передан, иначе —
# запасной путь по category==15.
RPLAN_BOUNDARY_FRONT_DOOR = 255

_RU_CAT = {
    "living": "гостиная", "bedroom": "спальня", "kitchen": "кухня",
    "wet": "санузел", "hall": "прихожая", "utility": "подсобное",
}


class _Rejected(Exception):
    """Внутренний сигнал: план не выражается чистой мозаикой (с причиной)."""


def _room_masks_by_instance(category: np.ndarray, instance: np.ndarray, min_room_px: int):
    """[(our_category, mask, (x0,y0,x1,y1), area_px)] по каналу instance.

    x0,y0 — включительно, x1,y1 — исключительно (как срез numpy)."""
    rooms = []
    for inst_id in np.unique(instance):
        mask = instance == inst_id
        area = int(mask.sum())
        if area < min_room_px:
            continue
        # Тип комнаты — мажоритарный класс category внутри экземпляра.
        cls = int(np.bincount(category[mask].ravel()).argmax())
        cat = RPLAN_CLASS_TO_CATEGORY.get(cls)
        if cat is None:
            continue
        ys, xs = np.where(mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        rooms.append((cat, mask, bbox, area))
    return rooms


def _orient_entry_to_top(category: np.ndarray, instance: np.ndarray, boundary: np.ndarray | None = None):
    """Поворотом на k·90° приводит фасад со входом к y=0 (верх).

    Передняя дверь берётся из boundary==255 (канонический способ RPLAN,
    см. rplanpy.data.RplanData.get_front_door_mask), а при отсутствии
    boundary — из category==15. Возвращает (category, instance, k). Без
    двери — k=0."""
    if boundary is not None and (boundary == RPLAN_BOUNDARY_FRONT_DOOR).any():
        door_of = lambda c, b: b == RPLAN_BOUNDARY_FRONT_DOOR
        src = boundary
    elif (category == RPLAN_FRONT_DOOR).any():
        door_of = lambda c, b: c == RPLAN_FRONT_DOOR
        src = category
    else:
        return category, instance, 0

    best_k, best_score = 0, None
    for k in range(4):
        f_r = door_of(np.rot90(category, k), np.rot90(src, k))
        rows, _cols = np.where(f_r)
        # доля центроида двери от верха по высоте плана: чем меньше — тем
        # ближе вход к y=0.
        score = rows.mean() / max(f_r.shape[0], 1)
        if best_score is None or score < best_score:
            best_k, best_score = k, score
    if best_k == 0:
        return category, instance, 0
    # boundary дальше не нужен (использовался только для ориентации), поэтому
    # не поворачиваем и не возвращаем — экономим массив.
    return np.rot90(category, best_k), np.rot90(instance, best_k), best_k


def _snap_edges(values: list[int], tol: int) -> tuple[list[int], dict[int, int]]:
    """Кластеризует близкие (≤ tol) координаты рёбер в один разрез.

    Возвращает (отсортированные уникальные разрезы, map исходное→снапнутое)."""
    uniq = sorted(set(values))
    clusters: list[list[int]] = []
    for v in uniq:
        if clusters and v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    cuts = [int(round(sum(c) / len(c))) for c in clusters]
    mapping = {}
    for ci, c in enumerate(clusters):
        for v in c:
            mapping[v] = cuts[ci]
    return cuts, mapping


def _build_mosaic(rooms, snap_tol: int):
    """Рёбра bbox'ов → сетка разрезов + прямоугольник ячеек на комнату.

    Бросает _Rejected, если раскладка не мозаична (щели/наложения/непрямо-
    угольные комнаты). Возвращает (x_cuts_px, y_cuts_px, cell_ranges), где
    cell_ranges[i] = (ix0, ix1, iy0, iy1) — индексы разрезов комнаты i."""
    xs_edges = [b[0] for _c, _m, b, _a in rooms] + [b[2] for _c, _m, b, _a in rooms]
    ys_edges = [b[1] for _c, _m, b, _a in rooms] + [b[3] for _c, _m, b, _a in rooms]
    x_cuts, xmap = _snap_edges(xs_edges, snap_tol)
    y_cuts, ymap = _snap_edges(ys_edges, snap_tol)
    if len(x_cuts) < 2 or len(y_cuts) < 2:
        raise _Rejected("вырожденная сетка")

    xi = {v: i for i, v in enumerate(x_cuts)}
    yi = {v: i for i, v in enumerate(y_cuts)}
    nx, ny = len(x_cuts) - 1, len(y_cuts) - 1

    # grid[j][i] = индекс комнаты, владеющей ячейкой (i,j); -1 = свободна.
    grid = [[-1] * nx for _ in range(ny)]
    cell_ranges = []
    for ridx, (_cat, _mask, (x0, y0, x1, y1), _a) in enumerate(rooms):
        ix0, ix1 = xi[xmap[x0]], xi[xmap[x1]]
        iy0, iy1 = yi[ymap[y0]], yi[ymap[y1]]
        if ix1 <= ix0 or iy1 <= iy0:
            raise _Rejected("комната схлопнулась при снапе")
        for j in range(iy0, iy1):
            for i in range(ix0, ix1):
                if grid[j][i] != -1:
                    raise _Rejected("наложение комнат (не slicing-раскладка)")
                grid[j][i] = ridx
        cell_ranges.append((ix0, ix1, iy0, iy1))

    for j in range(ny):
        for i in range(nx):
            if grid[j][i] == -1:
                raise _Rejected("щель в раскладке (не покрыта ни одной комнатой)")

    return x_cuts, y_cuts, cell_ranges


def _mean_iou(rooms, x_cuts, y_cuts, cell_ranges) -> float:
    """Средний IoU реконструированного прямоугольника ячеек с исходной формой.

    Для PNG-пути форма — пиксельная маска (комната может быть непрямоугольной,
    IoU честно ловит грубость приближения). Для box-пути (Graph2Plan) маски
    нет, комната — уже прямоугольник bbox; IoU считается аналитически как
    пересечение/объединение двух прямоугольников (bbox и снапнутая ячейка) —
    он < 1, только если снап заметно сдвинул грани."""
    ious = []
    for (_cat, mask, bbox, _a), (ix0, ix1, iy0, iy1) in zip(rooms, cell_ranges):
        rx0, rx1 = x_cuts[ix0], x_cuts[ix1]
        ry0, ry1 = y_cuts[iy0], y_cuts[iy1]
        if mask is None:
            bx0, by0, bx1, by1 = bbox
            iw = max(0, min(bx1, rx1) - max(bx0, rx0))
            ih = max(0, min(by1, ry1) - max(by0, ry0))
            inter = iw * ih
            union = (bx1 - bx0) * (by1 - by0) + (rx1 - rx0) * (ry1 - ry0) - inter
            ious.append(inter / (union or 1))
        else:
            rect = np.zeros_like(mask, dtype=bool)
            rect[ry0:ry1, rx0:rx1] = True
            inter = int((mask & rect).sum())
            union = int((mask | rect).sum()) or 1
            ious.append(inter / union)
    return float(np.mean(ious)) if ious else 0.0


def _storey_role(categories: set[str]) -> str:
    has_bed = "bedroom" in categories
    has_public = "kitchen" in categories or "living" in categories
    if has_public:
        return "any" if has_bed else "ground"
    if has_bed:
        return "upper"
    return "any"


def image_to_template(
    category: np.ndarray,
    instance: np.ndarray,
    *,
    source_id: str,
    boundary: np.ndarray | None = None,
    snap_tol_px: int = 4,
    min_room_px: int = 40,
    min_iou: float = 0.7,
    reasons=None,
) -> dict | None:
    """RPLAN (category+instance каналы, опц. boundary) → шаблон mosaic-формата
    или None.

    boundary (канал 0) используется только для ориентации входа (front door
    = 255). None — план не выражается чистой прямоугольной мозаикой
    (L-образные комнаты, щели, слишком грубое приближение). Это ожидаемо для
    большой доли RPLAN и не является ошибкой. reasons (Counter) — для
    диагностики причин отказа."""
    category = np.asarray(category)
    instance = np.asarray(instance)
    boundary = np.asarray(boundary) if boundary is not None else None
    category, instance, _k = _orient_entry_to_top(category, instance, boundary)

    rooms = _room_masks_by_instance(category, instance, min_room_px)
    return _emit_template(rooms, source_id=source_id, min_iou=min_iou,
                          snap_tol_px=snap_tol_px, source_tag="rplan", reasons=reasons)


def _emit_template(rooms, *, source_id: str, min_iou: float, snap_tol_px: int,
                   source_tag: str, reasons=None) -> dict | None:
    """Общее ядро обоих путей (PNG и Graph2Plan .mat): список комнат
    (cat, mask|None, bbox, area) → шаблон mosaic-формата или None, если
    раскладка не мозаична или приближение слишком грубое (IoU < min_iou).

    reasons (Counter|None): если задан, причина отказа пишется в него —
    для диагностики выхода конвертера (сколько щелей/наложений и т.п.)."""
    if len(rooms) < 2:
        if reasons is not None:
            reasons["мало комнат (<2)"] += 1
        return None
    try:
        x_cuts, y_cuts, cell_ranges = _build_mosaic(rooms, snap_tol_px)
    except _Rejected as e:
        if reasons is not None:
            reasons[str(e)] += 1
        return None

    iou = _mean_iou(rooms, x_cuts, y_cuts, cell_ranges)
    if iou < min_iou:
        if reasons is not None:
            reasons["грубое приближение (IoU<порога)"] += 1
        return None

    x0, x1 = x_cuts[0], x_cuts[-1]
    y0, y1 = y_cuts[0], y_cuts[-1]
    span_x, span_y = (x1 - x0) or 1, (y1 - y0) or 1
    xs01 = [round((v - x0) / span_x, 4) for v in x_cuts]
    ys01 = [round((v - y0) / span_y, 4) for v in y_cuts]

    cats = [r[0] for r in rooms]
    seen: Counter = Counter()
    out_rooms = []
    for (cat, _m, _b, _a), (ix0, ix1, iy0, iy1) in zip(rooms, cell_ranges):
        seen[cat] += 1
        out_rooms.append({
            "slot": f"{cat}_{seen[cat]}" if cats.count(cat) > 1 else cat,
            "category": cat,
            "cx0": ix0, "cx1": ix1, "cy0": iy0, "cy1": iy1,
        })

    comp = ", ".join(f"{_RU_CAT.get(c, c)}×{n}" for c, n in sorted(Counter(cats).items()))
    return {
        "id": f"{source_tag}_{source_id}",
        "description": f"{source_tag.upper()} {len(rooms)} комн.: {comp}",
        "storey_role": _storey_role(set(cats)),
        "aspect": round(span_x / span_y, 2),
        "x_cuts": xs01,
        "y_cuts": ys01,
        "rooms": out_rooms,
        "_source": source_tag,
        "_iou": round(iou, 3),
    }


# ───────────────────────── Graph2Plan .mat (боксы) ──────────────────────────
#
# Дистрибутив Graph2Plan (HanHan55/Graph2plan) хранит RPLAN не как PNG, а как
# .mat со struct-массивом `data`; у каждого плана поля:
#   rType    (n,)   — тип каждой комнаты (та же таксономия 0..12, что PNG)
#   gtBoxNew (n,4)  — прямоугольник комнаты [x0,y0,x1,y1] в пикселях 256²
#   boundary (m,4)  — контур; строки с последним столбцом==1 задают ребро
#                     входной двери (по ним ориентируем фасад к y=0)
# Боксы — уже готовые прямоугольники, поэтому картинки обрабатывать не нужно:
# кормим (cat, None, bbox, area) в то же ядро _emit_template, что и PNG-путь.

GRAPH2PLAN_DOOR_FLAG_COL = 3  # индекс столбца-флага входной двери в boundary


def _rot90_boxes(boxes, door, W):
    """Один поворот раскладки на 90° CCW в кадре шириной W: точка (x,y)→(y,W−x)
    (та же геометрия, что np.rot90 для картинки). Боксы пересобираются по
    новым углам; дверь — тоже. Возвращает (боксы, дверь)."""
    def rp(x, y):
        return (y, W - x)
    nb = []
    for (x0, y0, x1, y1) in boxes:
        cs = [rp(x0, y0), rp(x1, y0), rp(x1, y1), rp(x0, y1)]
        xs = [c[0] for c in cs]; ys = [c[1] for c in cs]
        nb.append((min(xs), min(ys), max(xs), max(ys)))
    nd = rp(*door) if door is not None else None
    return nb, nd


def _orient_boxes_entry_to_top(rooms, door):
    """Поворачивает боксы комнат так, чтобы входная дверь оказалась у y=0.
    Без двери — как есть. rooms: [(cat, None, bbox, area)]."""
    boxes = [r[2] for r in rooms]
    if door is None:
        return rooms
    best_k, best_score, best_boxes = 0, None, boxes
    cur_boxes, cur_door = boxes, door
    for k in range(4):
        if k > 0:
            W = max(x1 for _x0, _y0, x1, _y1 in cur_boxes)
            cur_boxes, cur_door = _rot90_boxes(cur_boxes, cur_door, W)
        ys = [y for _x0, y0, _x1, y1 in cur_boxes for y in (y0, y1)]
        y_min, y_max = min(ys), max(ys)
        score = (cur_door[1] - y_min) / max(y_max - y_min, 1e-6)
        if best_score is None or score < best_score:
            best_k, best_score, best_boxes = k, score, cur_boxes
    if best_k == 0:
        return rooms
    return [(c, m, best_boxes[i], (best_boxes[i][2] - best_boxes[i][0]) * (best_boxes[i][3] - best_boxes[i][1]))
            for i, (c, m, _b, _a) in enumerate(rooms)]


def _graph2plan_plan_to_rooms(plan):
    """struct-элемент Graph2Plan → ([(cat, None, bbox, area)], door_xy|None)."""
    rtype = np.atleast_1d(np.asarray(plan.rType).ravel())
    boxes = np.asarray(plan.gtBoxNew)
    if boxes.ndim == 1:
        boxes = boxes.reshape(1, -1)
    rooms = []
    for i in range(min(len(rtype), len(boxes))):
        cat = RPLAN_CLASS_TO_CATEGORY.get(int(rtype[i]))
        if cat is None:
            continue
        x0, y0, x1, y1 = (int(v) for v in boxes[i][:4])
        if x1 <= x0 or y1 <= y0:
            continue
        rooms.append((cat, None, (x0, y0, x1, y1), (x1 - x0) * (y1 - y0)))

    door = None
    b = np.asarray(plan.boundary)
    if b.ndim == 2 and b.shape[1] > GRAPH2PLAN_DOOR_FLAG_COL:
        dm = b[b[:, GRAPH2PLAN_DOOR_FLAG_COL] == 1]
        if len(dm):
            door = (float(dm[:, 0].mean()), float(dm[:, 1].mean()))
    return rooms, door


def graph2plan_plan_to_template(plan, source_id: str, *, min_iou: float = 0.7,
                                snap_tol_px: int = 4, reasons=None) -> dict | None:
    """Один план Graph2Plan → шаблон mosaic-формата или None."""
    rooms, door = _graph2plan_plan_to_rooms(plan)
    if len(rooms) < 2:
        if reasons is not None:
            reasons["мало комнат (<2)"] += 1
        return None
    rooms = _orient_boxes_entry_to_top(rooms, door)
    return _emit_template(rooms, source_id=source_id, min_iou=min_iou,
                          snap_tol_px=snap_tol_px, source_tag="graph2plan", reasons=reasons)


def graph2plan_mat_to_templates(mat_path: str, *, limit: int | None = None,
                                min_iou: float = 0.7, snap_tol_px: int = 4) -> tuple[list[dict], dict]:
    """Graph2Plan .mat (struct-массив `data`) → (уникальные шаблоны, статистика)."""
    import scipy.io  # тяжёлая зависимость — грузим только когда реально нужен .mat

    m = scipy.io.loadmat(mat_path, squeeze_me=True, struct_as_record=False)
    if "data" not in m:
        raise ValueError(f"{mat_path}: нет ключа 'data' (это точно Graph2Plan .mat?)")
    data = np.atleast_1d(m["data"])
    if limit:
        data = data[:limit]

    templates, seen = [], set()
    reasons: Counter = Counter()
    stats = {"total": 0, "accepted": 0, "rejected": 0, "duplicate": 0, "error": 0}
    for i, plan in enumerate(data):
        stats["total"] += 1
        try:
            name = str(getattr(plan, "name", "")) or str(i)
            tpl = graph2plan_plan_to_template(plan, name, min_iou=min_iou,
                                              snap_tol_px=snap_tol_px, reasons=reasons)
        except Exception:
            stats["error"] += 1
            continue
        if tpl is None:
            stats["rejected"] += 1
            continue
        sig = _signature(tpl)
        if sig in seen:
            stats["duplicate"] += 1
            continue
        seen.add(sig)
        templates.append(tpl)
        stats["accepted"] += 1
    stats["reasons"] = dict(reasons)
    return templates, stats


def _signature(tpl: dict) -> tuple:
    """Ключ дедупликации: состав + округлённая геометрия мозаики."""
    cats = tuple(sorted(r["category"] for r in tpl["rooms"]))
    xs = tuple(round(v, 2) for v in tpl["x_cuts"])
    ys = tuple(round(v, 2) for v in tpl["y_cuts"])
    cells = frozenset((r["category"], r["cx0"], r["cx1"], r["cy0"], r["cy1"]) for r in tpl["rooms"])
    return (cats, xs, ys, cells)


def load_rplan_png(path: str, category_channel: int = 1, instance_channel: int = 2,
                   boundary_channel: int = 0):
    """4-канальный RPLAN PNG → (category, instance, boundary) 2D-массивы.

    Дефолты каналов — каноническая раскладка RPLAN (rplanpy.data.RplanData):
    [boundary, category, instance, inside]. Если ваша копия хранит каналы
    иначе — задайте индексы явно (флаги CLI --category-channel/--instance-
    channel). boundary нужен только для ориентации входа (front door = 255)."""
    from PIL import Image

    arr = np.asarray(Image.open(path))
    need = max(category_channel, instance_channel, boundary_channel)
    if arr.ndim != 3 or arr.shape[2] <= need:
        raise ValueError(f"{path}: ожидался многоканальный PNG, получено shape={arr.shape}")
    return arr[..., category_channel], arr[..., instance_channel], arr[..., boundary_channel]


def convert_dir(
    input_dir: str,
    *,
    limit: int | None = None,
    category_channel: int = 1,
    instance_channel: int = 2,
    snap_tol_px: int = 4,
    min_iou: float = 0.7,
) -> tuple[list[dict], dict]:
    """Каталог RPLAN PNG → (список уникальных шаблонов, статистика)."""
    files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".png"))
    if limit:
        files = files[:limit]

    templates, seen_sigs = [], set()
    reasons: Counter = Counter()
    stats = {"total": 0, "accepted": 0, "rejected": 0, "duplicate": 0, "error": 0}
    for fname in files:
        stats["total"] += 1
        try:
            category, instance, boundary = load_rplan_png(
                os.path.join(input_dir, fname), category_channel, instance_channel)
            tpl = image_to_template(
                category, instance, source_id=os.path.splitext(fname)[0],
                boundary=boundary, snap_tol_px=snap_tol_px, min_iou=min_iou, reasons=reasons)
        except Exception:
            stats["error"] += 1
            continue
        if tpl is None:
            stats["rejected"] += 1
            continue
        sig = _signature(tpl)
        if sig in seen_sigs:
            stats["duplicate"] += 1
            continue
        seen_sigs.add(sig)
        templates.append(tpl)
        stats["accepted"] += 1
    stats["reasons"] = dict(reasons)
    return templates, stats


def _main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Конвертер RPLAN/Graph2Plan → house_templates.json")
    ap.add_argument("input", help="каталог с RPLAN *.png (PNG-режим) ИЛИ путь к Graph2Plan .mat (--graph2plan)")
    ap.add_argument("--graph2plan", action="store_true",
                    help="input — это Graph2Plan .mat (struct-массив data), а не каталог PNG")
    ap.add_argument("--out", required=True, help="куда записать датасет шаблонов")
    ap.add_argument("--append", help="существующий house_templates.json — дописать в него (с дедупом)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--category-channel", type=int, default=1)
    ap.add_argument("--instance-channel", type=int, default=2)
    ap.add_argument("--snap", type=int, default=4, help="допуск снапа рёбер, px")
    ap.add_argument("--min-iou", type=float, default=0.7)
    args = ap.parse_args(argv)

    if args.graph2plan:
        templates, stats = graph2plan_mat_to_templates(
            args.input, limit=args.limit, snap_tol_px=args.snap, min_iou=args.min_iou)
    else:
        templates, stats = convert_dir(
            args.input, limit=args.limit,
            category_channel=args.category_channel, instance_channel=args.instance_channel,
            snap_tol_px=args.snap, min_iou=args.min_iou)

    if args.append:
        with open(args.append, encoding="utf-8") as f:
            base = json.load(f)
        existing_sigs = {_signature(t) for t in base["templates"]}
        added = [t for t in templates if _signature(t) not in existing_sigs]
        base["templates"].extend(added)
        payload = base
        print(f"дописано {len(added)} новых (из {len(templates)} уникальных) в {args.append}")
    else:
        payload = {
            "_comment": "Сгенерировано из RPLAN через src/bim_agents/rplan_convert.py",
            "templates": templates,
        }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"файлов: {stats['total']}  принято: {stats['accepted']}  "
          f"отклонено: {stats['rejected']}  дублей: {stats['duplicate']}  ошибок: {stats['error']}")
    if stats.get("reasons"):
        print("причины отказа:")
        for reason, n in sorted(stats["reasons"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:>6}  {reason}")
    print(f"записано в {args.out}")


if __name__ == "__main__":
    _main()
