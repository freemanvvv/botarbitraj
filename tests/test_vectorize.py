"""Тесты растр→вектор извлечения комнат (интеграция ChatHouseDiffusion)."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from src.floorplan.vectorize import raster_to_rooms, DEFAULT_CLASS_TO_TYPE, CHATHOUSEDIFFUSION_CLASS_TO_TYPE


def _blank_grid(h, w):
    # 13 = "External" в таксономии ChatHouseDiffusion — не входит ни в один
    # class_to_type, в отличие от 0 (которое, что важно, само по себе валидный
    # класс LivingRoom) — иначе фон ошибочно распознаётся как комната.
    return np.full((h, w), 13, dtype=np.uint8)


def test_raster_to_rooms_extracts_rectangular_regions():
    grid = _blank_grid(40, 40)
    grid[2:18, 2:18] = 1     # living
    grid[2:18, 22:38] = 3    # kitchen
    # шумовой артефакт диффузии — должен быть отфильтрован
    grid[0:2, 0:2] = 2

    rooms = raster_to_rooms(grid, px_per_meter=4.0, class_to_type=DEFAULT_CLASS_TO_TYPE)
    types = sorted(r.type for r in rooms)
    assert types == ["kitchen", "living"]

    living = next(r for r in rooms if r.type == "living")
    # 16x16 px / 4 px-per-m = 4x4 m = 16 m², с допуском на артефакт контура
    assert abs(living.area - 16.0) < 2.0


def test_raster_to_rooms_filters_small_noise_components():
    grid = _blank_grid(30, 30)
    grid[5:15, 5:15] = 1  # настоящая комната, 100 px
    grid[0, 0] = 1        # 1px шум, отдельная компонента (не соединена с комнатой)

    rooms = raster_to_rooms(grid, px_per_meter=5.0, class_to_type=DEFAULT_CLASS_TO_TYPE, min_area_px=9)
    assert len(rooms) == 1


def test_raster_to_rooms_extracts_l_shaped_polygon():
    grid = _blank_grid(30, 30)
    grid[0:20, 0:20] = 1
    grid[0:10, 10:20] = 0  # вырезаем верхний правый угол -> L-форма

    rooms = raster_to_rooms(grid, px_per_meter=5.0, class_to_type=DEFAULT_CLASS_TO_TYPE)
    assert len(rooms) == 1
    assert rooms[0].polygon is not None
    assert len(rooms[0].polygon) >= 5  # не свёлся к прямоугольнику


def test_raster_to_rooms_chathousediffusion_taxonomy_maps_bedroom_variants():
    grid = _blank_grid(20, 40)
    grid[2:18, 2:18] = 5   # ChildRoom
    grid[2:18, 22:38] = 7  # SecondRoom

    rooms = raster_to_rooms(grid, px_per_meter=4.0, class_to_type=CHATHOUSEDIFFUSION_CLASS_TO_TYPE)
    assert all(r.type == "bedroom" for r in rooms)
    assert len(rooms) == 2


def test_raster_to_rooms_empty_grid_returns_no_rooms():
    grid = _blank_grid(20, 20)
    rooms = raster_to_rooms(grid, px_per_meter=4.0, class_to_type=DEFAULT_CLASS_TO_TYPE)
    assert rooms == []


def test_raster_to_rooms_rejects_non_2d_grid():
    grid = np.zeros((3, 20, 20), dtype=np.uint8)
    with pytest.raises(ValueError):
        raster_to_rooms(grid, px_per_meter=4.0)
