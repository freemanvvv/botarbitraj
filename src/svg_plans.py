"""
Генератор SVG-планов этажей — дименсированный чертёж "как настоящий проект":
цепочки размеров по периметру, стены с вырезанными проёмами, дверные створки
с дугой открывания, оконные символы. Ориентир — типовой архитектурный план
1-го этажа (см. обсуждение), а не декоративные элементы вроде мебели/машин/
эркеров — тех сознательно нет, это чертёж, не рендер интерьера.
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
    """Дверь. horizontal — стена, в которой прорезана дверь, идёт вдоль X
    (True) или вдоль Y (False); нужно, чтобы рисовать створку и вырез в
    стене в верном направлении — раньше это поле (называлось wall_side)
    никогда не передавалось вызывающей стороной и дверь всегда рисовалась
    как горизонтальная чёрточка, даже поперёк вертикальной стены."""
    x: float
    y: float
    width: float = 0.9
    height: float = 2.1
    horizontal: bool = True


@dataclass
class Window:
    """Окно. horizontal — см. Door."""
    x: float
    y: float
    width: float = 1.5
    height: float = 1.5
    horizontal: bool = True


def _fmt_mm(dist_m: float) -> str:
    """5.47 м → "5 470" (мм, с пробелом-разделителем тысяч — как на реальных
    чертежах)."""
    mm = max(0, round(dist_m * 1000))
    s = str(mm)
    groups = []
    while len(s) > 3:
        groups.insert(0, s[-3:])
        s = s[:-3]
    groups.insert(0, s)
    return " ".join(groups)


class SVGPlanGenerator:
    """
    Генератор дименсированного плана этажа в SVG.
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

    def add_door(self, x, y, width=0.9, horizontal=True):
        self.doors.append(Door(x, y, width, horizontal=horizontal))

    def add_window(self, x, y, width=1.5, horizontal=True):
        self.windows.append(Window(x, y, width, horizontal=horizontal))

    def add_outer_walls(self, width: float, height: float, thickness: float = 0.3):
        """Добавляет наружные стены по периметру."""
        t = thickness
        self.walls.append(Wall(0, 0, width, 0, t, True))            # Верх
        self.walls.append(Wall(0, height, width, height, t, True))  # Низ
        self.walls.append(Wall(0, 0, 0, height, t, True))           # Лево
        self.walls.append(Wall(width, 0, width, height, t, True))   # Право

    def _s(self, value: float) -> float:
        """Масштабирование величины (без сдвига начала координат)."""
        return value * self.scale

    def _room_at(self, px: float, py: float) -> Room | None:
        for r in self.rooms:
            if r.x - 0.02 <= px <= r.x + r.width + 0.02 and r.y - 0.02 <= py <= r.y + r.height + 0.02:
                return r
        return None

    def _swing_sign(self, x: float, y: float, horizontal: bool) -> int:
        """+1/-1 — в какую сторону открывается дверь. Определяется пробой
        точки по обе стороны проёма и проверкой, какая из них лежит внутри
        комнаты (open всегда "внутрь"); если не удалось определить —
        открывание вниз/вправо по умолчанию."""
        probe = 0.35
        for s in (1, -1):
            px, py = (x, y + s * probe) if horizontal else (x + s * probe, y)
            if self._room_at(px, py):
                return s
        return 1

    def _grid_lines(self) -> tuple[list[float], list[float]]:
        """Уникальные координаты вертикальных/горизонтальных стен — оси
        размерных цепочек. Без стен (маловероятно — оба адаптера всегда их
        строят) — берём границы комнат как запасной вариант."""
        xs, ys = set(), set()
        for w in self.walls:
            if abs(w.x1 - w.x2) < 1e-3:
                xs.add(round(w.x1, 3))
            if abs(w.y1 - w.y2) < 1e-3:
                ys.add(round(w.y1, 3))
        if not xs:
            for r in self.rooms:
                xs.add(round(r.x, 3)); xs.add(round(r.x + r.width, 3))
        if not ys:
            for r in self.rooms:
                ys.add(round(r.y, 3)); ys.add(round(r.y + r.height, 3))
        return sorted(xs), sorted(ys)

    def _dim_segment(self, x0: float, y0: float, x1: float, y1: float, dist_m: float, vertical: bool = False) -> str:
        """Один отрезок размерной цепочки: линия + засечки на концах + подпись."""
        tick = 4
        svg = f'<line class="dimchain-line" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}"/>\n'
        for tx, ty in ((x0, y0), (x1, y1)):
            svg += f'<line class="dimchain-tick" x1="{tx-tick:.1f}" y1="{ty+tick:.1f}" x2="{tx+tick:.1f}" y2="{ty-tick:.1f}"/>\n'
        label = _fmt_mm(dist_m)
        if vertical:
            mx, my = x0 - 6, (y0 + y1) / 2
            svg += f'<text class="dimchain-label" x="{mx:.1f}" y="{my:.1f}" transform="rotate(-90 {mx:.1f} {my:.1f})">{label}</text>\n'
        else:
            mx, my = (x0 + x1) / 2, y0 - 4
            svg += f'<text class="dimchain-label" x="{mx:.1f}" y="{my:.1f}">{label}</text>\n'
        return svg

    def _bottom_dimensions(self, xs_world: list[float], sx, plan_bottom: float, row_h: int = 26) -> tuple[str, float]:
        if len(xs_world) < 2:
            return "", plan_bottom
        xs_px = [sx(x) for x in xs_world]
        row1_y = plan_bottom + row_h
        has_total = len(xs_world) >= 3
        bottom_y = row1_y + row_h if has_total else row1_y
        svg = ""
        for px in xs_px:
            svg += f'<line class="dimchain-ext" x1="{px:.1f}" y1="{plan_bottom:.1f}" x2="{px:.1f}" y2="{bottom_y:.1f}"/>\n'
        for i in range(len(xs_px) - 1):
            svg += self._dim_segment(xs_px[i], row1_y, xs_px[i + 1], row1_y, xs_world[i + 1] - xs_world[i])
        if has_total:
            row2_y = row1_y + row_h
            svg += self._dim_segment(xs_px[0], row2_y, xs_px[-1], row2_y, xs_world[-1] - xs_world[0])
        return svg, bottom_y

    def _left_dimensions(self, ys_world: list[float], sy, plan_left: float, row_w: int = 26) -> tuple[str, float]:
        if len(ys_world) < 2:
            return "", plan_left
        ys_px = [sy(y) for y in ys_world]
        row1_x = plan_left - row_w
        has_total = len(ys_world) >= 3
        left_x = row1_x - row_w if has_total else row1_x
        svg = ""
        for py in ys_px:
            svg += f'<line class="dimchain-ext" x1="{left_x:.1f}" y1="{py:.1f}" x2="{plan_left:.1f}" y2="{py:.1f}"/>\n'
        for i in range(len(ys_px) - 1):
            svg += self._dim_segment(row1_x, ys_px[i], row1_x, ys_px[i + 1], ys_world[i + 1] - ys_world[i], vertical=True)
        if has_total:
            row2_x = row1_x - row_w
            svg += self._dim_segment(row2_x, ys_px[0], row2_x, ys_px[-1], ys_world[-1] - ys_world[0], vertical=True)
        return svg, left_x

    def _door_symbol(self, cx: float, cy: float, width_px: float, horizontal: bool, swing: int) -> str:
        """Створка + дуга открывания — стандартный архитектурный символ двери."""
        half = width_px / 2
        if horizontal:
            hinge = (cx - half, cy)
            tip = (cx - half, cy + swing * width_px)
            arc_end = (cx + half, cy)
            sweep = 1 if swing > 0 else 0
        else:
            hinge = (cx, cy - half)
            tip = (cx + swing * width_px, cy - half)
            arc_end = (cx, cy + half)
            sweep = 0 if swing > 0 else 1
        svg = f'<line class="door-leaf" x1="{hinge[0]:.1f}" y1="{hinge[1]:.1f}" x2="{tip[0]:.1f}" y2="{tip[1]:.1f}"/>\n'
        svg += (f'<path class="door-arc" d="M {tip[0]:.1f} {tip[1]:.1f} '
                f'A {width_px:.1f} {width_px:.1f} 0 0 {sweep} {arc_end[0]:.1f} {arc_end[1]:.1f}"/>\n')
        return svg

    def _window_symbol(self, cx: float, cy: float, width_px: float, thickness_px: float, horizontal: bool) -> str:
        """Вырез в стене + "лесенка" из тонких линий — стандартный символ окна в плане."""
        half = width_px / 2
        n = 4
        svg = ""
        if horizontal:
            x0, y0 = cx - half, cy - thickness_px / 2
            svg += f'<rect class="window" x="{x0:.1f}" y="{y0:.1f}" width="{width_px:.1f}" height="{thickness_px:.1f}"/>\n'
            for i in range(n + 1):
                xi = x0 + width_px * i / n
                svg += f'<line class="window-line" x1="{xi:.1f}" y1="{y0:.1f}" x2="{xi:.1f}" y2="{y0+thickness_px:.1f}"/>\n'
        else:
            x0, y0 = cx - thickness_px / 2, cy - half
            svg += f'<rect class="window" x="{x0:.1f}" y="{y0:.1f}" width="{thickness_px:.1f}" height="{width_px:.1f}"/>\n'
            for i in range(n + 1):
                yi = y0 + width_px * i / n
                svg += f'<line class="window-line" x1="{x0:.1f}" y1="{yi:.1f}" x2="{x0+thickness_px:.1f}" y2="{yi:.1f}"/>\n'
        return svg

    def generate(self, title: str = "План этажа") -> str:
        """Генерирует SVG-код плана."""
        all_x = [r.x for r in self.rooms] + [r.x + r.width for r in self.rooms]
        all_y = [r.y for r in self.rooms] + [r.y + r.height for r in self.rooms]
        for w in self.walls:
            all_x.extend([w.x1, w.x2])
            all_y.extend([w.y1, w.y2])

        min_x, max_x = min(all_x) - 1, max(all_x) + 1
        min_y, max_y = min(all_y) - 1, max(all_y) + 1

        plan_w = self._s(max_x - min_x)
        plan_h = self._s(max_y - min_y)

        xs_grid, ys_grid = self._grid_lines()

        margin_top = 34
        margin_left = 12 + (2 * 26 + 10 if len(xs_grid) >= 3 else 26 + 10 if len(xs_grid) >= 2 else 0)
        margin_bottom_dims = 2 * 26 + 10 if len(ys_grid) >= 3 else 26 + 10 if len(ys_grid) >= 2 else 0
        margin_legend = 26
        margin_right = 16

        svg_w = margin_left + plan_w + margin_right
        svg_h = margin_top + plan_h + margin_bottom_dims + margin_legend

        def sx(x_world):
            return margin_left + self._s(x_world - min_x)

        def sy(y_world):
            return margin_top + self._s(y_world - min_y)

        plan_left = sx(min_x)
        plan_right = sx(max_x)
        plan_top = sy(min_y)
        plan_bottom = sy(max_y)

        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="{svg_w:.0f}" height="{svg_h:.0f}"
     viewBox="0 0 {svg_w:.0f} {svg_h:.0f}"
     style="font-family: 'DejaVu Sans', sans-serif;">

<style>
    .room-fill {{ fill: #f0f4f8; stroke: none; }}
    .wall {{ fill: #4a5568; stroke: #2d3748; stroke-width: 0.5; }}
    .wall-thin {{ fill: #718096; stroke: #4a5568; stroke-width: 0.3; }}
    .door-leaf {{ stroke: #2d3748; stroke-width: 1.2; fill: none; }}
    .door-arc {{ stroke: #2d3748; stroke-width: 0.8; fill: none; stroke-dasharray: 2,2; }}
    .window {{ fill: #dceefb; stroke: #3182ce; stroke-width: 0.8; }}
    .window-line {{ stroke: #3182ce; stroke-width: 0.6; }}
    .label {{ font-size: 10px; text-anchor: middle; dominant-baseline: central; fill: #2d3748; }}
    .dim {{ font-size: 7px; text-anchor: middle; fill: #718096; }}
    .title {{ font-size: 14px; font-weight: bold; text-anchor: middle; fill: #2d3748; }}
    .grid {{ stroke: #e2e8f0; stroke-width: 0.3; }}
    .dimchain-line {{ stroke: #a0aec0; stroke-width: 0.6; }}
    .dimchain-ext {{ stroke: #cbd5e0; stroke-width: 0.4; stroke-dasharray: 1,2; }}
    .dimchain-tick {{ stroke: #4a5568; stroke-width: 1; }}
    .dimchain-label {{ font-size: 8px; text-anchor: middle; fill: #4a5568; }}
</style>

<title>{title}</title>
"""

        # Grid — только в области плана, не в полях с размерами
        for gx in range(int(min_x), int(max_x) + 1):
            svg += f'<line class="grid" x1="{sx(gx):.1f}" y1="{plan_top:.1f}" x2="{sx(gx):.1f}" y2="{plan_bottom:.1f}"/>\n'
        for gy in range(int(min_y), int(max_y) + 1):
            svg += f'<line class="grid" x1="{plan_left:.1f}" y1="{sy(gy):.1f}" x2="{plan_right:.1f}" y2="{sy(gy):.1f}"/>\n'

        # Rooms — подпись ближе к верху комнаты (не по центру), чтобы не
        # накладываться на дверные проёмы посреди стен.
        for room in self.rooms:
            rx, ry = sx(room.x), sy(room.y)
            rw, rh = self._s(room.width), self._s(room.height)
            svg += f'<rect class="room-fill" x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" rx="2"/>\n'
            label_y = ry + min(28, rh * 0.3)
            svg += f'<text class="label" x="{rx + rw / 2:.1f}" y="{label_y:.1f}">{room.name}</text>\n'
            area = room.width * room.height
            svg += f'<text class="dim" x="{rx + rw / 2:.1f}" y="{label_y + 12:.1f}">{area:.2f} м²</text>\n'

        # Walls — с вырезанными проёмами (дверь/окно), а не сплошной линией
        # поверх которой раньше просто рисовался маркер.
        consumed_doors: set[int] = set()
        consumed_windows: set[int] = set()

        for wall in self.walls:
            cls = "wall" if wall.is_loadbearing else "wall-thin"
            horizontal = abs(wall.y1 - wall.y2) < 1e-3
            if horizontal:
                axis = wall.y1
                lo, hi = sorted((wall.x1, wall.x2))
            else:
                axis = wall.x1
                lo, hi = sorted((wall.y1, wall.y2))
            if hi - lo < 1e-6:
                continue

            openings = []  # (a, b, kind, obj)
            for idx, d in enumerate(self.doors):
                if idx in consumed_doors or d.horizontal != horizontal:
                    continue
                pos = d.x if horizontal else d.y
                dax = d.y if horizontal else d.x
                if abs(dax - axis) < 0.25 and lo - 0.05 <= pos <= hi + 0.05:
                    openings.append((max(lo, pos - d.width / 2), min(hi, pos + d.width / 2), "door", d))
                    consumed_doors.add(idx)
            for idx, win in enumerate(self.windows):
                if idx in consumed_windows or win.horizontal != horizontal:
                    continue
                pos = win.x if horizontal else win.y
                wax = win.y if horizontal else win.x
                if abs(wax - axis) < 0.25 and lo - 0.05 <= pos <= hi + 0.05:
                    openings.append((max(lo, pos - win.width / 2), min(hi, pos + win.width / 2), "window", win))
                    consumed_windows.add(idx)
            openings.sort(key=lambda o: o[0])

            t_s = self._s(wall.thickness)
            axis_s = sy(axis) if horizontal else sx(axis)

            cur = lo
            solids = []
            for a, b, _kind, _obj in openings:
                if a > cur:
                    solids.append((cur, a))
                cur = max(cur, b)
            if cur < hi:
                solids.append((cur, hi))

            for a, b in solids:
                a_s, b_s = (sx(a), sx(b)) if horizontal else (sy(a), sy(b))
                length_s = b_s - a_s
                if length_s <= 0:
                    continue
                if horizontal:
                    svg += f'<rect class="{cls}" x="{a_s:.1f}" y="{axis_s - t_s/2:.1f}" width="{length_s:.1f}" height="{t_s:.1f}" rx="1"/>\n'
                else:
                    svg += f'<rect class="{cls}" x="{axis_s - t_s/2:.1f}" y="{a_s:.1f}" width="{t_s:.1f}" height="{length_s:.1f}" rx="1"/>\n'

            for a, b, kind, obj in openings:
                mid = (a + b) / 2
                cx, cy = (sx(mid), axis_s) if horizontal else (axis_s, sy(mid))
                width_s = self._s(b - a)
                if kind == "door":
                    swing = self._swing_sign(mid if horizontal else axis, axis if horizontal else mid, horizontal)
                    svg += self._door_symbol(cx, cy, width_s, horizontal, swing)
                else:
                    svg += self._window_symbol(cx, cy, width_s, t_s, horizontal)

        # Проёмы, не привязанные ни к одной стене (запасной вариант —
        # не теряем их, просто без выреза в стене).
        for idx, d in enumerate(self.doors):
            if idx in consumed_doors:
                continue
            cx, cy = sx(d.x), sy(d.y)
            swing = self._swing_sign(d.x, d.y, d.horizontal)
            svg += self._door_symbol(cx, cy, self._s(d.width), d.horizontal, swing)
        for idx, win in enumerate(self.windows):
            if idx in consumed_windows:
                continue
            cx, cy = sx(win.x), sy(win.y)
            svg += self._window_symbol(cx, cy, self._s(win.width), self._s(0.2), win.horizontal)

        # Размерные цепочки по периметру — сегменты + общий габарит.
        bottom_dims_svg, dims_bottom_y = self._bottom_dimensions(xs_grid, sx, plan_bottom)
        left_dims_svg, dims_left_x = self._left_dimensions(ys_grid, sy, plan_left)
        svg += bottom_dims_svg
        svg += left_dims_svg

        # Title
        svg += f'<text class="title" x="{svg_w / 2:.1f}" y="20">{title}</text>\n'

        # Legend
        legend_y = dims_bottom_y + 16
        svg += f'<rect class="wall" x="{plan_left:.0f}" y="{legend_y:.0f}" width="20" height="10" rx="1"/><text x="{plan_left+25:.0f}" y="{legend_y+8:.0f}" font-size="8">Несущие стены</text>\n'
        svg += f'<line class="door-leaf" x1="{plan_left+130:.0f}" y1="{legend_y+9:.0f}" x2="{plan_left+130:.0f}" y2="{legend_y-1:.0f}"/><text x="{plan_left+135:.0f}" y="{legend_y+8:.0f}" font-size="8">Дверь</text>\n'
        svg += f'<rect class="window" x="{plan_left+210:.0f}" y="{legend_y+3:.0f}" width="20" height="6" rx="1"/><text x="{plan_left+235:.0f}" y="{legend_y+8:.0f}" font-size="8">Окно</text>\n'

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
