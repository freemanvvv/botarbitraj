"""Регрессионный тест на src/bim_agents/bim_agent.py: раньше сгенерированный
IFC был структурно корректен (правильное число IfcWall/IfcSlab/IfcSpace/...),
но геометрия каждого элемента была нетесселируемой — три независимых бага:

1. _make_placement() передавал (axis3d) как единственный позиционный
   аргумент в IfcLocalPlacement, чьи атрибуты — (PlacementRelTo,
   RelativePlacement) — axis3d попадал в PlacementRelTo (неверный тип), а
   обязательный RelativePlacement оставался пустым.
2. IfcGeometricRepresentationContext создавался с нулём позиционных
   аргументов и никогда не получал обязательный WorldCoordinateSystem.
3. _create_space() строил профиль комнаты из IfcPolyLoop (для граней
   B-rep) вместо IfcPolyline (для кривых профиля), да ещё из 3D-точек
   вместо 2D, и без замыкания контура.

Ни один из них не ловится проверкой количества элементов — только реальный
прогон через ifcopenshell.geom.create_shape(), как здесь."""
import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.geom

from src.bim_agents.contracts import BuildingProgram, Room
from src.bim_agents.floorplan_agent import generate_floor_plan
from src.bim_agents.bim_agent import generate_ifc


def test_generated_house_ifc_geometry_tessellates(tmp_path):
    program = BuildingProgram(
        project_name="Т", storeys=2, footprint={"width_m": 10, "depth_m": 8},
        rooms=[
            Room(id="living_01", name="Гостиная", storey=0, area_m2=30, type="IfcSpace:LIVING"),
            Room(id="kitchen_01", name="Кухня", storey=0, area_m2=15, type="IfcSpace:KITCHEN"),
            Room(id="bed_01", name="Спальня", storey=1, area_m2=20, type="IfcSpace:BEDROOM"),
            Room(id="bed_02", name="Детская", storey=1, area_m2=15, type="IfcSpace:CHILD"),
        ],
    )
    floor_plan = generate_floor_plan(program)
    path, stats = generate_ifc(floor_plan, output_dir=str(tmp_path))
    assert stats["walls"] > 0 and stats["spaces"] > 0

    ifc = ifcopenshell.open(path)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)

    failures = []
    total = 0
    for ifc_type in ("IfcWall", "IfcSlab", "IfcSpace", "IfcWindow", "IfcDoor"):
        for product in ifc.by_type(ifc_type):
            total += 1
            try:
                shape = ifcopenshell.geom.create_shape(settings, product)
                if not list(shape.geometry.verts):
                    failures.append((ifc_type, product.Name, "empty geometry"))
            except Exception as e:
                failures.append((ifc_type, product.Name, repr(e)))

    assert total > 0
    assert failures == [], f"{len(failures)}/{total} elements failed to tessellate: {failures[:5]}"
