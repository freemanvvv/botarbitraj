"""
Фаза 1 — Контракты данных (JSON Schema) для BIM-пайплайна.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ─── BuildingProgram (выход ArchitectAgent) ───

class Room(BaseModel):
    id: str = Field(..., description="unique id, e.g. living_01")
    name: str = Field(..., description="user-facing name, e.g. Гостиная")
    storey: int = Field(1, ge=0)
    area_m2: float = Field(..., gt=0)
    type: str = "IfcSpace:LIVING"
    exterior_windows: bool = False
    min_width_m: float = 2.0

class BuildingProgram(BaseModel):
    project_name: str = "Building"
    style: str = "modern"
    site: dict = {"width_m": 20, "depth_m": 30}
    footprint: dict = {"width_m": 12, "depth_m": 9}
    storeys: int = 1
    ceiling_height_m: float = 3.0
    wall_material: str = "aerated_concrete_D500"
    foundation: str = "strip"
    roof: str = "flat"
    rooms: list[Room] = Field(default_factory=list)
    adjacency: list[list[str]] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


# ─── FloorPlan (выход FloorPlanAgent → вход BIMAgent) ───

class RoomPlan(BaseModel):
    id: str
    polygon: list[list[float]]

class WallPlan(BaseModel):
    id: str
    axis: list[list[float]]
    type: str = "exterior"
    thickness_m: float = 0.3

class OpeningPlan(BaseModel):
    wall: str
    kind: str  # window | door
    offset_m: float
    width_m: float
    height_m: float
    sill_m: float = 0.0

class StairPlan(BaseModel):
    from_level: int
    to_level: int
    shape: str = "L"
    footprint: list[list[float]] = Field(default_factory=list)

class StoreyPlan(BaseModel):
    level: int
    elevation_m: float = 0.0
    rooms: list[RoomPlan] = Field(default_factory=list)
    walls: list[WallPlan] = Field(default_factory=list)
    openings: list[OpeningPlan] = Field(default_factory=list)

class FloorPlan(BaseModel):
    storeys: list[StoreyPlan] = Field(default_factory=list)
    stairs: list[StairPlan] = Field(default_factory=list)
