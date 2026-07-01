"""
Генеративная планировка квартир (Путь C, фазы 0-2).

Локальные координаты квартиры: x ∈ [0, width] вдоль фасада,
y ∈ [0, depth] от входа со стороны лестничной клетки (y=0)
к главному фасаду с окнами (y=depth).
"""
from .ir import ApartmentProgram, RoomBox, DoorSpec, ApartmentFloorplan
from .solver import generate_floorplan
from .norms import validate_floorplan, get_room_constraints
from .to_ifc import floorplan_to_ifc
from .neural import generate_floorplan_llm

__all__ = [
    "ApartmentProgram", "RoomBox", "DoorSpec", "ApartmentFloorplan",
    "generate_floorplan", "validate_floorplan", "get_room_constraints",
    "floorplan_to_ifc", "generate_floorplan_llm",
]
