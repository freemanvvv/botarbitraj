"""
Промежуточное представление (IR) планировки квартиры — фаза 0.

Формат не зависит от того, кто его заполнил: детерминированный солвер
(фаза 2) или нейрогенератор (фаза 4) — оба обязаны вернуть один и тот же
ApartmentFloorplan, который затем идёт в validate_floorplan() и floorplan_to_ifc().
"""
from dataclasses import dataclass, field

ROOM_TYPES = ("living", "bedroom", "kitchen", "bathroom", "wc", "hallway")

# Комнаты, которым обязательно требуется окно на фасаде (КМК 2.08.01-89 п.3.1)
WINDOW_REQUIRED_TYPES = {"living", "bedroom", "kitchen"}
# "Мокрые" зоны — тяготеют к инженерному стояку
WET_TYPES = {"kitchen", "bathroom", "wc"}


@dataclass
class ApartmentProgram:
    """Заказ на планировку: сколько комнат какого типа нужно уместить."""
    rooms: str = "1"          # "1", "2", "3" ... комнатность (не считая кухню/санузел)
    apartment_type: str = "1-комнатная"

    @staticmethod
    def for_room_count(n: int) -> "ApartmentProgram":
        names = {1: "1-комнатная", 2: "2-комнатная", 3: "3-комнатная", 4: "4-комнатная"}
        return ApartmentProgram(rooms=str(n), apartment_type=names.get(n, f"{n}-комнатная"))


def _shoelace_area(polygon: list[tuple[float, float]]) -> float:
    n = len(polygon)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


@dataclass
class RoomBox:
    """Комната в локальных координатах квартиры.

    По умолчанию — прямоугольник (x0,y0)-(x1,y1), как строит детерминированный
    солвер и LLM-планировщик. `polygon`, если задан (например, комнатой из
    ChatHouseDiffusion после векторизации растра), — произвольный замкнутый
    контур в том же локальном СК; x0..y1 в этом случае — его bounding box
    (используется для быстрых проверок норм/пересечений — см. geometry.py
    для точной проверки пересечения полигонов).
    """
    type: str
    x0: float
    y0: float
    x1: float
    y1: float
    name: str = ""
    polygon: list[tuple[float, float]] | None = None

    @property
    def width(self) -> float:
        """Ширина bounding box'а. Для полигона — приближение (см. min_side)."""
        return round(self.x1 - self.x0, 4)

    @property
    def depth(self) -> float:
        """Глубина bounding box'а. Для полигона — приближение (см. min_side)."""
        return round(self.y1 - self.y0, 4)

    @property
    def area(self) -> float:
        if self.polygon:
            return round(_shoelace_area(self.polygon), 3)
        return round(self.width * self.depth, 3)

    @property
    def min_side(self) -> float:
        """Минимальная сторона для норм-проверки ширины. Для произвольного
        полигона точная 'минимальная ширина' — задача повышенной сложности
        (rotating calipers); используем bounding box как консервативное
        приближение (для L-образных комнат может быть немного оптимистичным)."""
        return round(min(self.width, self.depth), 4)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def touches_facade(self, depth_total: float, tol: float = 0.05) -> bool:
        if self.polygon:
            return any(abs(y - depth_total) <= tol for _, y in self.polygon)
        return abs(self.y1 - depth_total) <= tol

    @classmethod
    def from_polygon(cls, type: str, polygon: list[tuple[float, float]], name: str = "") -> "RoomBox":
        """Строит RoomBox из произвольного полигона, вычисляя bounding box."""
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return cls(type=type, x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys),
                    name=name, polygon=list(polygon))


@dataclass
class DoorSpec:
    """Дверь между двумя комнатами (или входная — room_b=-1)."""
    x: float
    y: float
    wall_axis: str          # "y" — дверь пробивает стену, идущую вдоль Y (вертикальную в плане)
                             # "x" — дверь пробивает стену, идущую вдоль X (горизонтальную в плане)
    room_a: int = -1         # индекс комнаты в ApartmentFloorplan.rooms (или -1 = вход с площадки)
    room_b: int = -1
    width: float = 0.9
    kind: str = "interior"   # "entry" | "interior"


@dataclass
class ApartmentFloorplan:
    """Готовая планировка одной квартиры — общий формат для солвера и нейросети."""
    width: float
    depth: float
    entry_side: str = "west"     # "west" (x=0) или "east" (x=width) — сторона, смежная с площадкой
    rooms: list[RoomBox] = field(default_factory=list)
    doors: list[DoorSpec] = field(default_factory=list)
    source: str = "solver"       # "solver" | "neural" | "chathousediffusion" | "template"

    def room(self, idx: int) -> RoomBox | None:
        return self.rooms[idx] if 0 <= idx < len(self.rooms) else None

    def total_area(self) -> float:
        return round(sum(r.area for r in self.rooms), 2)
