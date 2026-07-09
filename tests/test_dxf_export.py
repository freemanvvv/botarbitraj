"""Тест DXF-экспорта house-плана: контуры комнат, стены, проёмы, размерные
линии (DIMENSION), подписи, таблица-экспликация — валидный DXF."""
import os
import tempfile
from collections import Counter

import ezdxf

from src.bim_agents.contracts import BuildingProgram, Room
from src.bim_agents.floorplan_agent import generate_floor_plan
from src.dxf_export import export_floorplan_dxf, _area_m2


def _program():
    return BuildingProgram(
        project_name="Дом", storeys=1, footprint={"width_m": 11, "depth_m": 9},
        rooms=[
            Room(id="liv", name="Зал", storey=0, area_m2=28, type="IfcSpace:LIVING", min_width_m=3.5),
            Room(id="kit", name="Кухня", storey=0, area_m2=14, type="IfcSpace:KITCHEN", min_width_m=2.5),
            Room(id="bed", name="Спальня", storey=0, area_m2=16, type="IfcSpace:BEDROOM", min_width_m=3.0),
            Room(id="bath", name="Санузел", storey=0, area_m2=5, type="IfcSpace:BATHROOM", min_width_m=1.7),
        ])


def test_export_produces_valid_dxf_with_dimensions_and_layers():
    prog = _program()
    fp = generate_floor_plan(prog)
    path = tempfile.mktemp(suffix=".dxf")
    try:
        export_floorplan_dxf(prog, fp, path)
        assert os.path.getsize(path) > 1000
        doc = ezdxf.readfile(path)          # читается назад = валидный DXF
        msp = doc.modelspace()
        kinds = Counter(e.dxftype() for e in msp)
        assert kinds["DIMENSION"] >= 1      # редактируемые размерные линии
        assert kinds["LWPOLYLINE"] >= len(fp.storeys[0].rooms)  # контуры комнат
        assert kinds["TEXT"] >= 1           # подписи/экспликация
        layers = {l.dxf.name for l in doc.layers}
        for need in ("ROOMS", "WALLS", "DIMS", "TEXT", "TABLE"):
            assert need in layers
        assert doc.units == ezdxf.units.MM
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_area_shoelace():
    # квадрат 4×5 → 20 м²
    assert abs(_area_m2([[0, 0], [4, 0], [4, 5], [0, 5]]) - 20.0) < 1e-6
