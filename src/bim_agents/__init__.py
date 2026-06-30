"""BIM-генератор. Фазы 0-1 по ТЗ."""
from .architect_agent import architect_agent
from .floorplan_agent import generate_floor_plan
from .bim_agent import generate_ifc
from .contracts import BuildingProgram, FloorPlan
from .router import router


def run_pipeline(description: str, output_dir: str = "output") -> tuple[str, dict]:
    """Сквозной пайплайн: текст → IFC."""
    # Фаза 1.1: ArchitectAgent — текст → BuildingProgram
    print("🧠 ArchitectAgent...", flush=True)
    program = architect_agent(description)
    print(f"   ✅ {program.project_name}, {len(program.rooms)} помещений", flush=True)

    # Фаза 1.2: FloorPlanAgent — BuildingProgram → FloorPlan
    print("📐 FloorPlanAgent (solver)...", flush=True)
    floor_plan = generate_floor_plan(program)
    total_rooms = sum(len(s.rooms) for s in floor_plan.storeys)
    total_walls = sum(len(s.walls) for s in floor_plan.storeys)
    print(f"   ✅ {total_rooms} комнат, {total_walls} стен", flush=True)

    # Фаза 1.3: BIMAgent — FloorPlan → IFC
    print("🏗️ BIMAgent (IFC)...", flush=True)
    path, stats = generate_ifc(floor_plan, output_dir)
    print(f"   ✅ IFC: {path}", flush=True)
    for k, v in stats.items():
        print(f"   {k}: {v}", flush=True)

    return path, stats
