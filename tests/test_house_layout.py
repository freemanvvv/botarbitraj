"""Тесты шаблонного и зонированного солвера house-движка
(src/bim_agents/layout_templates.py + floorplan_agent.py)."""
from src.bim_agents.contracts import BuildingProgram, Room
from src.bim_agents.floorplan_agent import generate_floor_plan, classify_room
from src.bim_agents.layout_templates import load_templates, match_template
from src.house_norms import validate_house_plan, _walls_touching_room


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return max(xs) - min(xs), max(ys) - min(ys)


def _family_program():
    return BuildingProgram(
        project_name="Т", storeys=2, footprint={"width_m": 11, "depth_m": 9},
        rooms=[
            Room(id="living_01", name="Гостиная", storey=0, area_m2=28, type="IfcSpace:LIVING", min_width_m=3.5),
            Room(id="kitchen_01", name="Кухня", storey=0, area_m2=14, type="IfcSpace:KITCHEN", min_width_m=2.5),
            Room(id="hall_01", name="Прихожая", storey=0, area_m2=6, type="IfcSpace:HALLWAY", min_width_m=1.5),
            Room(id="bath_01", name="Санузел 1", storey=0, area_m2=5, type="IfcSpace:BATHROOM", min_width_m=1.5),
            Room(id="bed_01", name="Спальня 1", storey=1, area_m2=16, type="IfcSpace:BEDROOM", min_width_m=3.0),
            Room(id="bed_02", name="Спальня 2", storey=1, area_m2=14, type="IfcSpace:BEDROOM", min_width_m=2.8),
            Room(id="child_01", name="Детская", storey=1, area_m2=12, type="IfcSpace:CHILD", min_width_m=2.8),
            Room(id="bath_02", name="Санузел 2", storey=1, area_m2=5, type="IfcSpace:BATHROOM", min_width_m=1.5),
        ],
    )


def _iter_cells(r):
    """Прямоугольники слота: одиночный (cx0..cy1) или Г-образный ("cells")."""
    if "cells" in r:
        return [tuple(c) for c in r["cells"]]
    return [(r["cx0"], r["cx1"], r["cy0"], r["cy1"])]


def test_all_templates_tile_unit_square_without_overlap():
    """Валидация датасета: каждая grid-мозаика полностью и без наложений
    покрывает единичный квадрат. polygon-шаблоны (реальные формы из RPLAN,
    _source=graph2plan_poly) — не сеточные, для них проверяются лишь
    нормировка контуров в 0..1 и невырожденность."""
    for tpl in load_templates():
        if tpl.get("_source") == "graph2plan_poly" or any("polygon" in r for r in tpl["rooms"]):
            for r in tpl["rooms"]:
                poly = r["polygon"]
                assert len(poly) >= 3, tpl["id"]
                assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in poly), tpl["id"]
            continue
        xs, ys = tpl["x_cuts"], tpl["y_cuts"]
        assert xs[0] == 0.0 and xs[-1] == 1.0 and xs == sorted(xs), tpl["id"]
        assert ys[0] == 0.0 and ys[-1] == 1.0 and ys == sorted(ys), tpl["id"]
        cells = []
        total = 0.0
        for r in tpl["rooms"]:
            for cx0, cx1, cy0, cy1 in _iter_cells(r):
                x0, x1 = xs[cx0], xs[cx1]
                y0, y1 = ys[cy0], ys[cy1]
                assert x1 > x0 and y1 > y0, (tpl["id"], r["slot"])
                total += (x1 - x0) * (y1 - y0)
                cells.append((x0, y0, x1, y1, r["slot"]))
        assert abs(total - 1.0) < 1e-9, (tpl["id"], total)
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b = cells[i], cells[j]
                if a[4] == b[4]:
                    continue  # разные прямоугольники одной Г-образной комнаты
                overlap_w = min(a[2], b[2]) - max(a[0], b[0])
                overlap_h = min(a[3], b[3]) - max(a[1], b[1])
                assert overlap_w <= 1e-9 or overlap_h <= 1e-9, (tpl["id"], a[4], b[4])


def test_classify_room_uses_dataset_keywords():
    assert classify_room("IfcSpace:KITCHEN", "Кухня") == "kitchen"
    assert classify_room("", "Детская") == "bedroom"
    assert classify_room("", "Тамбур") == "hall"
    assert classify_room("", "Котельная") == "utility"
    assert classify_room("", "Постирочная") == "wet"
    assert classify_room("что-то странное", "???") == "other"


def test_template_matched_for_known_composition():
    tpl = match_template(["living", "kitchen", "hall", "wet"], aspect=1.2, level=0)
    assert tpl is not None and tpl["id"] == "ground_l-k-h-w"
    # upper-шаблон не должен подойти на этаж 0 и наоборот
    assert match_template(["bedroom", "bedroom", "wet"], aspect=1.2, level=0) is None
    assert match_template(["bedroom", "bedroom", "wet"], aspect=1.2, level=1) is not None
    # незнакомый состав → None (уйдёт в зонированный fallback)
    assert match_template(["other", "other", "other", "other", "other", "other"], 1.2, 0) is None


def test_rooms_are_not_elongated_anymore():
    """Регрессия на «комнаты-кишки»: раньше однорядная раскладка тянула
    КАЖДУЮ комнату на всю глубину footprint'а (санузел 1.7×9, aspect 5.3).
    Теперь шаблон/две ленты держат пропорции близко к реальным проектам."""
    fp = generate_floor_plan(_family_program())
    worst = 0.0
    for storey in fp.storeys:
        for rp in storey.rooms:
            w, h = _bbox(rp.polygon)
            worst = max(worst, max(w, h) / min(w, h))
    assert worst < 2.6, f"максимальный aspect {worst:.2f}"


def test_family_house_passes_norms_and_has_realistic_openings():
    program = _family_program()
    fp = generate_floor_plan(program)
    assert validate_house_plan(program, fp) == []

    s0 = fp.storeys[0]
    # входная дверь: kind=door на ВНЕШНЕЙ стене этажа 0
    wall_by_id = {w.id: w for w in s0.walls}
    entry = [o for o in s0.openings if o.kind == "door" and wall_by_id[o.wall].type == "exterior"]
    assert len(entry) == 1

    # у санузла и прихожей нет окон (свет им не нужен — окна не размещаем)
    rooms_by_id = {rp.id: rp for rp in s0.rooms}
    windowed_walls = {o.wall for o in s0.openings if o.kind == "window"}
    for rid in ("bath_01", "hall_01"):
        touching = {w.id for w in _walls_touching_room(rooms_by_id[rid].polygon, s0.walls)}
        own_windows = set()
        for wid in touching & windowed_walls:
            # окно «принадлежит» комнате, только если её стена внешняя и
            # никакая другая комната не делит этот сегмент
            others = [r for r in s0.rooms if r.id != rid
                      and wall_by_id[wid] in _walls_touching_room(r.polygon, [wall_by_id[wid]])]
            if not others:
                own_windows.add(wid)
        assert not own_windows, f"{rid} получил окно"


def test_unknown_composition_falls_back_to_two_bands():
    """6 неизвестных комнат → шаблона нет → зонированный fallback, и он
    делит этаж на две ленты (3 уровня y), а не тянет один ряд на всю глубину."""
    rooms = [Room(id=f"r{i}", name=f"Комната {i}", storey=0, area_m2=15,
                  type="IfcSpace:GENERIC", min_width_m=2.5) for i in range(6)]
    program = BuildingProgram(project_name="Т", storeys=1,
                              footprint={"width_m": 12, "depth_m": 9}, rooms=rooms)
    fp = generate_floor_plan(program)
    ys = {round(p[1], 2) for rp in fp.storeys[0].rooms for p in rp.polygon}
    assert len(ys) == 3  # 0, граница лент, 9


def test_match_template_is_tolerant_to_extra_same_type_rooms():
    """Ближайший подбор: состав с ЛИШНИМИ комнатами того же типа (4 спальни
    против шаблона на 3) всё равно находит шаблон, а не уходит в fallback."""
    # точное совпадение по-прежнему работает
    assert match_template(["bedroom", "bedroom", "bedroom", "wet"], 1.15, 1)["id"] == "upper_b-b-b-w"
    # 4 спальни + санузел → тот же шаблон (4-я добавится делением ячейки)
    assert match_template(["bedroom", "bedroom", "bedroom", "bedroom", "wet"], 1.15, 1)["id"] == "upper_b-b-b-w"
    # чужой тип, которого нет в шаблоне → None (уйдёт в fallback)
    assert match_template(["bedroom", "bedroom", "wet", "kitchen"], 1.15, 1) is None


def test_apply_template_places_all_rooms_when_program_has_extras():
    """apply_template должен разместить ВСЕ комнаты (включая лишние сверх
    ячеек шаблона) без наложений и щелей, покрыв footprint целиком."""
    program = BuildingProgram(
        project_name="Т", storeys=2, footprint={"width_m": 12, "depth_m": 10},
        rooms=[
            Room(id="liv", name="Гостиная", storey=0, area_m2=28, type="IfcSpace:LIVING", min_width_m=3.5),
            Room(id="kit", name="Кухня", storey=0, area_m2=14, type="IfcSpace:KITCHEN", min_width_m=2.5),
            Room(id="hall", name="Прихожая", storey=0, area_m2=6, type="IfcSpace:HALLWAY", min_width_m=1.5),
            Room(id="wc0", name="Санузел", storey=0, area_m2=5, type="IfcSpace:BATHROOM", min_width_m=1.5),
            Room(id="b1", name="Спальня 1", storey=1, area_m2=18, type="IfcSpace:BEDROOM", min_width_m=3.0),
            Room(id="b2", name="Спальня 2", storey=1, area_m2=15, type="IfcSpace:BEDROOM", min_width_m=2.8),
            Room(id="b3", name="Спальня 3", storey=1, area_m2=14, type="IfcSpace:BEDROOM", min_width_m=2.8),
            Room(id="b4", name="Спальня 4", storey=1, area_m2=12, type="IfcSpace:BEDROOM", min_width_m=2.8),
            Room(id="wc1", name="Санузел 2", storey=1, area_m2=5, type="IfcSpace:BATHROOM", min_width_m=1.5),
        ],
    )
    fp = generate_floor_plan(program)
    # все комнаты размещены
    placed = sum(len(s.rooms) for s in fp.storeys)
    assert placed == 9
    # этаж 1 (4 спальни + санузел = 5 комнат) собран из шаблона на 3 спальни
    upper = next(s for s in fp.storeys if s.level == 1)
    assert len(upper.rooms) == 5
    # мозаика без наложений/щелей: сумма площадей = footprint, нет вытянутых
    for storey in fp.storeys:
        total = sum((max(p[0] for p in rp.polygon) - min(p[0] for p in rp.polygon)) *
                    (max(p[1] for p in rp.polygon) - min(p[1] for p in rp.polygon))
                    for rp in storey.rooms)
        assert abs(total - 12.0 * 10.0) < 1e-2
    # нормы проходят и комнаты не «кишки»
    assert validate_house_plan(program, fp) == []
    worst = 0.0
    for storey in fp.storeys:
        for rp in storey.rooms:
            w, h = _bbox(rp.polygon)
            worst = max(worst, max(w, h) / min(w, h))
    assert worst < 3.0


def test_fallback_grids_deep_band_instead_of_corridor_rooms():
    """Регрессия: раньше зонированный fallback клал все комнаты ленты в ОДИН
    ряд на всю глубину — на глубоком узком участке комнаты вытягивались в
    «кишку» (aspect >10). Теперь глубокая лента с многими комнатами
    разбивается на сетку под-рядов (_grid_rows), и пропорции остаются
    близкими к реальным."""
    rooms = [Room(id=f"r{i}", name=f"Комн {i}", storey=0, area_m2=14,
                  type="IfcSpace:GENERIC", min_width_m=2.5) for i in range(6)]
    program = BuildingProgram(project_name="Т", storeys=1,
                              footprint={"width_m": 7.0, "depth_m": 12.0}, rooms=rooms)
    fp = generate_floor_plan(program)
    worst = 0.0
    for rp in fp.storeys[0].rooms:
        w, h = _bbox(rp.polygon)
        assert w > 0.01 and h > 0.01
        worst = max(worst, max(w, h) / min(w, h))
    assert worst < 3.5, f"комната-кишка не устранена, worst aspect {worst:.2f}"
    # сетка действительно образовалась: больше двух уровней y (не один ряд)
    ys = {round(p[1], 2) for rp in fp.storeys[0].rooms for p in rp.polygon}
    assert len(ys) >= 3

    # мозаика по-прежнему без наложений и щелей: суммарная площадь = footprint
    total = sum((max(p[0] for p in rp.polygon) - min(p[0] for p in rp.polygon)) *
                (max(p[1] for p in rp.polygon) - min(p[1] for p in rp.polygon))
                for rp in fp.storeys[0].rooms)
    assert abs(total - 7.0 * 12.0) < 1e-3


def test_template_refit_respects_footprint_exactly():
    program = _family_program()
    fp = generate_floor_plan(program)
    for storey in fp.storeys:
        xs = [p[0] for rp in storey.rooms for p in rp.polygon]
        ys = [p[1] for rp in storey.rooms for p in rp.polygon]
        assert min(xs) == 0.0 and abs(max(xs) - 11.0) < 1e-6
        assert min(ys) == 0.0 and abs(max(ys) - 9.0) < 1e-6


def test_L_shaped_template_from_rplan_survives_full_pipeline(monkeypatch):
    """Г-образный шаблон (полученный конвертером с PNG-пути RPLAN — см.
    tests/test_rplan_convert.py::test_L_shaped_room_is_accepted_via_pixel_mask_ownership)
    должен пройти весь house-движок: apply_template → стены → проёмы → IFC,
    без вырожденной геометрии и с валидной тесселяцией."""
    import numpy as np
    from src.bim_agents.rplan_convert import image_to_template
    from src.bim_agents import layout_templates
    from src.bim_agents.bim_agent import generate_ifc

    category = np.full((64, 64), 13, dtype=np.int32)
    instance = np.zeros((64, 64), dtype=np.int32)
    category[4:34, 4:60] = 0; instance[4:34, 4:60] = 1     # living, верхняя полоса
    category[34:60, 4:34] = 0; instance[34:60, 4:34] = 1   # living, левая ножка вниз (та же комната)
    category[34:60, 34:60] = 2; instance[34:60, 34:60] = 2  # kitchen в правом-нижнем углу
    tpl = image_to_template(category, instance, source_id="Ltest", min_iou=0.7)
    assert tpl is not None
    assert any("cells" in r for r in tpl["rooms"])

    program = BuildingProgram(
        project_name="Т", storeys=1, footprint={"width_m": 10, "depth_m": 9},
        rooms=[
            Room(id="liv", name="Гостиная", storey=0, area_m2=50, type="IfcSpace:LIVING", min_width_m=3.0),
            Room(id="kit", name="Кухня", storey=0, area_m2=15, type="IfcSpace:KITCHEN", min_width_m=2.5),
        ],
    )
    monkeypatch.setattr(layout_templates, "load_templates", lambda: [tpl])
    fp = generate_floor_plan(program)

    liv_poly = next(rp.polygon for rp in fp.storeys[0].rooms if rp.id == "liv")
    kit_poly = next(rp.polygon for rp in fp.storeys[0].rooms if rp.id == "kit")
    assert len(liv_poly) == 6  # Г-форма — шестиугольник, не 4-угольник
    assert len(kit_poly) == 4

    # площадь Г-образного полигона (shoelace) не должна выродиться
    def shoelace(poly):
        s = 0.0
        for i in range(len(poly)):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % len(poly)]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2
    assert shoelace(liv_poly) > 20.0

    path, stats = generate_ifc(fp, output_dir="/tmp")
    assert stats["walls"] > 0 and stats["spaces"] == 2

    import ifcopenshell, ifcopenshell.geom
    ifc = ifcopenshell.open(path)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    for t in ("IfcWall", "IfcSlab", "IfcSpace", "IfcWindow", "IfcDoor"):
        for p in ifc.by_type(t):
            ifcopenshell.geom.create_shape(settings, p)  # не должно бросать
