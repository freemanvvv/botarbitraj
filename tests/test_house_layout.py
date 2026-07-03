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


def test_all_templates_tile_unit_square_without_overlap():
    """Валидация рукописного датасета: каждая мозаика полностью и без
    наложений покрывает единичный квадрат."""
    for tpl in load_templates():
        xs, ys = tpl["x_cuts"], tpl["y_cuts"]
        assert xs[0] == 0.0 and xs[-1] == 1.0 and xs == sorted(xs), tpl["id"]
        assert ys[0] == 0.0 and ys[-1] == 1.0 and ys == sorted(ys), tpl["id"]
        cells = []
        total = 0.0
        for r in tpl["rooms"]:
            x0, x1 = xs[r["cx0"]], xs[r["cx1"]]
            y0, y1 = ys[r["cy0"]], ys[r["cy1"]]
            assert x1 > x0 and y1 > y0, (tpl["id"], r["slot"])
            total += (x1 - x0) * (y1 - y0)
            cells.append((x0, y0, x1, y1, r["slot"]))
        assert abs(total - 1.0) < 1e-9, (tpl["id"], total)
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b = cells[i], cells[j]
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


def test_template_refit_respects_footprint_exactly():
    program = _family_program()
    fp = generate_floor_plan(program)
    for storey in fp.storeys:
        xs = [p[0] for rp in storey.rooms for p in rp.polygon]
        ys = [p[1] for rp in storey.rooms for p in rp.polygon]
        assert min(xs) == 0.0 and abs(max(xs) - 11.0) < 1e-6
        assert min(ys) == 0.0 and abs(max(ys) - 9.0) < 1e-6
