"""Тесты общей геометрии планировок (box + произвольный полигон)."""
from src.floorplan.ir import RoomBox
from src.floorplan.geometry import room_edges, collinear_overlap, connect_adjacent_rooms, polygons_intersect


def test_room_edges_box_has_four_edges_in_order():
    r = RoomBox("living", 0, 0, 4, 3)
    edges = room_edges(r)
    assert edges == [
        ((0, 0), (4, 0)),
        ((4, 0), (4, 3)),
        ((4, 3), (0, 3)),
        ((0, 3), (0, 0)),
    ]


def test_room_edges_polygon_uses_polygon_points():
    poly = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 3), (0, 3)]
    r = RoomBox.from_polygon("living", poly)
    edges = room_edges(r)
    assert len(edges) == len(poly)
    assert edges[0] == (poly[0], poly[1])


def test_collinear_overlap_finds_shared_axis_aligned_wall():
    a = ((0, 0), (0, 5))
    b = ((0, 2), (0, 6))
    seg = collinear_overlap(a, b)
    assert seg is not None
    (x0, y0), (x1, y1) = seg
    assert x0 == x1 == 0
    assert {round(y0, 2), round(y1, 2)} == {2.0, 5.0}


def test_collinear_overlap_none_when_not_parallel():
    a = ((0, 0), (0, 5))
    b = ((0, 0), (5, 0))
    assert collinear_overlap(a, b) is None


def test_collinear_overlap_none_when_parallel_but_far_apart():
    a = ((0, 0), (0, 5))
    b = ((3, 0), (3, 5))
    assert collinear_overlap(a, b) is None


def test_collinear_overlap_works_for_arbitrary_angle():
    # диагональная стена: (0,0)-(4,4), другая комната делит середину отрезка
    a = ((0, 0), (4, 4))
    b = ((1, 1), (5, 5))
    seg = collinear_overlap(a, b)
    assert seg is not None
    (x0, y0), (x1, y1) = seg
    assert round(x0, 2) == round(y0, 2) == 1.0
    assert round(x1, 2) == round(y1, 2) == 4.0


def test_connect_adjacent_rooms_matches_box_solver_behaviour():
    rooms = [
        RoomBox("hallway", 0, 0, 2, 8, name="Прихожая"),
        RoomBox("living", 2, 0, 6, 8, name="Гостиная"),
    ]
    doors = connect_adjacent_rooms(rooms)
    assert len(doors) == 1
    d = doors[0]
    assert {d.room_a, d.room_b} == {0, 1}
    assert d.x == 2


def test_connect_adjacent_rooms_wet_pair_gets_narrower_door():
    rooms = [
        RoomBox("hallway", 0, 0, 2, 8, name="Прихожая"),
        RoomBox("wc", 2, 0, 3.5, 2, name="Санузел"),
    ]
    doors = connect_adjacent_rooms(rooms)
    assert len(doors) == 1
    assert doors[0].width == 0.7


def test_connect_adjacent_rooms_l_shape_touches_rectangle():
    # L-образная гостиная с выемкой в верхнем правом углу; кухня примыкает
    # к правой грани нижней части L.
    living = RoomBox.from_polygon("living", [
        (0, 0), (5, 0), (5, 3), (3, 3), (3, 5), (0, 5),
    ], name="Гостиная")
    kitchen = RoomBox("kitchen", 5, 0, 8, 3, name="Кухня")
    doors = connect_adjacent_rooms([living, kitchen])
    assert len(doors) == 1
    assert {doors[0].room_a, doors[0].room_b} == {0, 1}


def test_polygons_intersect_box_case():
    a = RoomBox("living", 0, 0, 5, 5)
    b = RoomBox("bedroom", 3, 0, 8, 5)
    assert polygons_intersect(a, b) is True
    c = RoomBox("bedroom", 5, 0, 8, 5)
    assert polygons_intersect(a, c) is False


def test_polygons_intersect_bbox_overlap_but_shapes_dont():
    # L-образная комната с выемкой в правом верхнем углу; маленький квадрат
    # стоит именно в этой выемке — bbox пересекается, сами фигуры — нет.
    l_shape = RoomBox.from_polygon("living", [
        (0, 0), (5, 0), (5, 3), (3, 3), (3, 5), (0, 5),
    ])
    notch_square = RoomBox("storage", 3.5, 3.5, 4.5, 4.5)
    # bbox проверка (обе — не полигоны) была бы True, но у l_shape есть полигон
    bbox_ox = min(l_shape.x1, notch_square.x1) - max(l_shape.x0, notch_square.x0)
    bbox_oy = min(l_shape.y1, notch_square.y1) - max(l_shape.y0, notch_square.y0)
    assert bbox_ox > 0 and bbox_oy > 0  # bbox'ы пересекаются
    assert polygons_intersect(l_shape, notch_square) is False
