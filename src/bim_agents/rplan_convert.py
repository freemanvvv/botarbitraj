"""
Конвертер RPLAN → наш mosaic-формат шаблонов (house_templates.json).

RPLAN (Wu et al., "Data-driven Interior Plan Generation for Residential
Buildings", SIGGRAPH Asia 2019) — ~80k реальных планов квартир, каждый как
4-канальный PNG 256×256. Стандартная раскладка каналов (rplan-toolbox /
rplanpy): [boundary, category, instance, inside]. Каналы category/instance
дают попиксельный тип комнаты и её экземпляр; таксономия 18 классов — та же,
что уже задокументирована в src/floorplan/vectorize.py.

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

CLI:
    python -m src.bim_agents.rplan_convert <dir_с_png> --out out.json
        [--limit N] [--append src/bim_agents/house_templates.json]
        [--category-channel 1] [--instance-channel 2]
        [--min-iou 0.7] [--snap 4]
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


def _orient_entry_to_top(category: np.ndarray, instance: np.ndarray):
    """Поворотом на k·90° приводит фасад со входом (FrontDoor) к y=0 (верх).

    Возвращает (category, instance, k). Без FrontDoor — k=0."""
    fmask = category == RPLAN_FRONT_DOOR
    if not fmask.any():
        return category, instance, 0
    best_k, best_score = 0, None
    for k in range(4):
        cat_r = np.rot90(category, k)
        f_r = cat_r == RPLAN_FRONT_DOOR
        rows, _cols = np.where(f_r)
        # доля центроида двери от верха по высоте плана: чем меньше — тем
        # ближе вход к y=0.
        score = (rows.mean() - 0) / max(cat_r.shape[0], 1)
        if best_score is None or score < best_score:
            best_k, best_score = k, score
    if best_k == 0:
        return category, instance, 0
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
    """Средний IoU реконструированного прямоугольника ячеек с исходной маской."""
    ious = []
    for (_cat, mask, _bbox, _a), (ix0, ix1, iy0, iy1) in zip(rooms, cell_ranges):
        rx0, rx1 = x_cuts[ix0], x_cuts[ix1]
        ry0, ry1 = y_cuts[iy0], y_cuts[iy1]
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
    snap_tol_px: int = 4,
    min_room_px: int = 40,
    min_iou: float = 0.7,
) -> dict | None:
    """RPLAN (category+instance каналы) → шаблон mosaic-формата или None.

    None — план не выражается чистой прямоугольной мозаикой (L-образные
    комнаты, щели, слишком грубое приближение). Это ожидаемо для большой
    доли RPLAN и не является ошибкой."""
    category = np.asarray(category)
    instance = np.asarray(instance)
    category, instance, _k = _orient_entry_to_top(category, instance)

    rooms = _room_masks_by_instance(category, instance, min_room_px)
    if len(rooms) < 2:
        return None

    try:
        x_cuts, y_cuts, cell_ranges = _build_mosaic(rooms, snap_tol_px)
    except _Rejected:
        return None

    iou = _mean_iou(rooms, x_cuts, y_cuts, cell_ranges)
    if iou < min_iou:
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
        "id": f"rplan_{source_id}",
        "description": f"RPLAN {len(rooms)} комн.: {comp}",
        "storey_role": _storey_role(set(cats)),
        "aspect": round(span_x / span_y, 2),
        "x_cuts": xs01,
        "y_cuts": ys01,
        "rooms": out_rooms,
        "_source": "rplan",
        "_iou": round(iou, 3),
    }


def _signature(tpl: dict) -> tuple:
    """Ключ дедупликации: состав + округлённая геометрия мозаики."""
    cats = tuple(sorted(r["category"] for r in tpl["rooms"]))
    xs = tuple(round(v, 2) for v in tpl["x_cuts"])
    ys = tuple(round(v, 2) for v in tpl["y_cuts"])
    cells = frozenset((r["category"], r["cx0"], r["cx1"], r["cy0"], r["cy1"]) for r in tpl["rooms"])
    return (cats, xs, ys, cells)


def load_rplan_png(path: str, category_channel: int = 1, instance_channel: int = 2):
    """4-канальный RPLAN PNG → (category, instance) 2D-массивы.

    Дефолты каналов — стандартная раскладка rplan-toolbox
    [boundary, category, instance, inside]. Если ваша копия хранит каналы
    иначе — задайте индексы явно (флаги CLI --category-channel/--instance-
    channel)."""
    from PIL import Image

    arr = np.asarray(Image.open(path))
    if arr.ndim != 3 or arr.shape[2] <= max(category_channel, instance_channel):
        raise ValueError(f"{path}: ожидался многоканальный PNG, получено shape={arr.shape}")
    return arr[..., category_channel], arr[..., instance_channel]


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
    stats = {"total": 0, "accepted": 0, "rejected": 0, "duplicate": 0, "error": 0}
    for fname in files:
        stats["total"] += 1
        try:
            category, instance = load_rplan_png(
                os.path.join(input_dir, fname), category_channel, instance_channel)
            tpl = image_to_template(
                category, instance, source_id=os.path.splitext(fname)[0],
                snap_tol_px=snap_tol_px, min_iou=min_iou)
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
    return templates, stats


def _main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Конвертер RPLAN → house_templates.json")
    ap.add_argument("input_dir", help="каталог с RPLAN *.png")
    ap.add_argument("--out", required=True, help="куда записать датасет шаблонов")
    ap.add_argument("--append", help="существующий house_templates.json — дописать в него (с дедупом)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--category-channel", type=int, default=1)
    ap.add_argument("--instance-channel", type=int, default=2)
    ap.add_argument("--snap", type=int, default=4, help="допуск снапа рёбер, px")
    ap.add_argument("--min-iou", type=float, default=0.7)
    args = ap.parse_args(argv)

    templates, stats = convert_dir(
        args.input_dir, limit=args.limit,
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
    print(f"записано в {args.out}")


if __name__ == "__main__":
    _main()
