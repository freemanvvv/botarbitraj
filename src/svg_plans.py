"""
Генератор SVG-планов этажей.
"""
import os
from datetime import datetime
from dataclasses import dataclass

from .config import OUTPUT_DIR


@dataclass
class Room:
    """Помещение на плане."""
    name: str
    x: float
    y: float
    width: float
    height: float
    color: str = "#e8f4f8"


@dataclass
class Wall:
    """Стена."""
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float = 0.2
    is_loadbearing: bool = True


@dataclass
class Door:
    """Дверь."""
    x: float
    y: float
    width: float = 0.9
    height: float = 2.1
    wall_side: str = "bottom"  # top, bottom, left, right


@dataclass
class Window:
    """Окно."""
    x: float
    y: float
    width: float = 1.5
    height: float = 1.5
    wall_side: str = "bottom"


class SVGPlanGenerator:
    """
    Генератор схематичного плана этажа в SVG.
    Координаты в метрах, масштабируются автоматически.
    """

    def __init__(self, scale: float = 50):  # пикселей на метр
        self.scale = scale
        self.rooms: list[Room] = []
        self.walls: list[Wall] = []
        self.doors: list[Door] = []
        self.windows: list[Window] = []

    def add_room(self, name: str, x: float, y: float, w: float, h: float) -> Room:
        room = Room(name, x, y, w, h)
        self.rooms.append(room)
        return room

    def add_wall(self, x1, y1, x2, y2, thickness=0.2, loadbearing=True):
        self.walls.append(Wall(x1, y1, x2, y2, thickness, loadbearing))

    def add_door(self, x, y, width=0.9, wall_side="bottom"):
        self.doors.append(Door(x, y, width, wall_side=wall_side))

    def add_window(self, x, y, width=1.5, wall_side="bottom"):
        self.windows.append(Window(x, y, width, wall_side=wall_side))

    def add_outer_walls(self, width: float, height: float, thickness: float = 0.3):
        """Добавляет наружные стены по периметру."""
        t = thickness
        # Верх
        self.walls.append(Wall(0, 0, width, 0, t, True))
        # Низ
        self.walls.append(Wall(0, height, width, height, t, True))
        # Лево
        self.walls.append(Wall(0, 0, 0, height, t, True))
        # Право
        self.walls.append(Wall(width, 0, width, height, t, True))

    def _s(self, value: float) -> float:
        """Масштабирование координат."""
        return value * self.scale

    def generate(self, title: str = "План этажа") -> str:
        """Генерирует SVG-код плана."""
        # Определяем размеры
        all_x = [r.x for r in self.rooms] + [r.x + r.width for r in self.rooms]
        all_y = [r.y for r in self.rooms] + [r.y + r.height for r in self.rooms]
        for w in self.walls:
            all_x.extend([w.x1, w.x2])
            all_y.extend([w.y1, w.y2])

        min_x, max_x = min(all_x) - 1, max(all_x) + 1
        min_y, max_y = min(all_y) - 1, max(all_y) + 1

        svg_w = self._s(max_x - min_x)
        svg_h = self._s(max_y - min_y)

        # SVG building
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="{svg_w:.0f}" height="{svg_h:.0f}"
     viewBox="0 0 {svg_w:.0f} {svg_h:.0f}"
     style="font-family: 'DejaVu Sans', sans-serif;">

<style>
    .room-fill {{ fill: #f0f4f8; stroke: none; }}
    .wall {{ fill: #4a5568; stroke: #2d3748; stroke-width: 0.5; }}
    .wall-thin {{ fill: #718096; stroke: #4a5568; stroke-width: 0.3; }}
    .door {{ fill: #fff; stroke: #e53e3e; stroke-width: 1.5; }}
    .window {{ fill: #bee3f8; stroke: #3182ce; stroke-width: 1; }}
    .label {{ font-size: 10px; text-anchor: middle; dominant-baseline: central; fill: #2d3748; }}
    .dim {{ font-size: 7px; text-anchor: middle; fill: #718096; }}
    .title {{ font-size: 14px; font-weight: bold; text-anchor: middle; fill: #2d3748; }}
    .grid {{ stroke: #e2e8f0; stroke-width: 0.3; }}
</style>

<title>{title}</title>
"""

        # Grid
        for gx in range(int(min_x), int(max_x) + 1):
            sx = self._s(gx - min_x)
            svg += f'<line class="grid" x1="{sx}" y1="0" x2="{sx}" y2="{svg_h}"/>\n'
        for gy in range(int(min_y), int(max_y) + 1):
            sy = self._s(gy - min_y)
            svg += f'<line class="grid" x1="0" y1="{sy}" x2="{svg_w}" y2="{sy}"/>\n'

        # Rooms
        for room in self.rooms:
            rx = self._s(room.x - min_x)
            ry = self._s(room.y - min_y)
            rw = self._s(room.width)
            rh = self._s(room.height)
            svg += f'<rect class="room-fill" x="{rx}" y="{ry}" width="{rw}" height="{rh}" rx="2"/>\n'
            svg += f'<text class="label" x="{rx + rw / 2}" y="{ry + rh / 2}">{room.name}</text>\n'
            # Area label
            area = room.width * room.height
            svg += f'<text class="dim" x="{rx + rw / 2}" y="{ry + rh / 2 + 12}">{area:.1f} м²</text>\n'

        # Walls
        for wall in self.walls:
            cls = "wall" if wall.is_loadbearing else "wall-thin"
            # Преобразуем отрезок в прямоугольник (толщина стены)
            dx = wall.x2 - wall.x1
            dy = wall.y2 - wall.y1
            length = (dx ** 2 + dy ** 2) ** 0.5
            if length == 0:
                continue

            cx = (wall.x1 + wall.x2) / 2
            cy = (wall.y1 + wall.y2) / 2
            angle = 0
            if dx != 0:
                angle = 0  # горизонтальная
            if dy != 0:
                angle = 90  # вертикальная

            w_s = self._s(length)
            t_s = self._s(wall.thickness)
            cx_s = self._s(cx - min_x)
            cy_s = self._s(cy - min_y)

            if angle == 0:  # горизонтальная
                svg += f'<rect class="{cls}" x="{cx_s - w_s/2}" y="{cy_s - t_s/2}" width="{w_s}" height="{t_s}" rx="1"/>\n'
            else:  # вертикальная
                svg += f'<rect class="{cls}" x="{cx_s - t_s/2}" y="{cy_s - w_s/2}" width="{t_s}" height="{w_s}" rx="1"/>\n'

        # Doors
        for door in self.doors:
            dx = self._s(door.x - min_x)
            dy = self._s(door.y - min_y)
            dw = self._s(door.width)
            svg += f'<rect class="door" x="{dx}" y="{dy - 2}" width="{dw}" height="4" rx="1"/>\n'

        # Windows
        for win in self.windows:
            wx = self._s(win.x - min_x)
            wy = self._s(win.y - min_y)
            ww = self._s(win.width)
            svg += f'<rect class="window" x="{wx}" y="{wy - 3}" width="{ww}" height="6" rx="1"/>\n'

        # Title
        svg += f'<text class="title" x="{svg_w / 2}" y="-10">{title}</text>\n'

        # Legend
        legend_y = svg_h + 10
        svg += f'<rect class="wall" x="10" y="{legend_y}" width="20" height="10" rx="1"/><text x="35" y="{legend_y + 8}" font-size="8">Несущие стены</text>\n'
        svg += f'<rect class="door" x="110" y="{legend_y}" width="20" height="10" rx="1"/><text x="135" y="{legend_y + 8}" font-size="8">Дверь</text>\n'
        svg += f'<rect class="window" x="210" y="{legend_y}" width="20" height="10" rx="1"/><text x="235" y="{legend_y + 8}" font-size="8">Окно</text>\n'

        svg += "</svg>"
        return svg

    def save(self, path: str | None = None) -> str:
        """Генерирует и сохраняет SVG в файл."""
        svg = self.generate()

        if path is None:
            path = os.path.join(OUTPUT_DIR, f"plan_{datetime.now():%Y%m%d_%H%M%S}.svg")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(svg)
        return path
