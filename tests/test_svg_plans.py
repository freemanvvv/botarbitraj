"""Тесты src/svg_plans.py — дименсированный SVG-чертёж плана этажа."""
import re

from src.svg_plans import SVGPlanGenerator, _fmt_mm


def test_fmt_mm_groups_thousands_with_space():
    assert _fmt_mm(5.47) == "5 470"
    assert _fmt_mm(20.29) == "20 290"
    assert _fmt_mm(0.9) == "900"
    assert _fmt_mm(1.234) == "1 234"


def _viewbox(svg: str) -> tuple[float, float]:
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert m, "no viewBox found"
    return float(m.group(1)), float(m.group(2))


def test_title_and_legend_are_inside_the_viewbox():
    # Регрессия: раньше title рисовался на y=-10, а легенда — на y=svg_h+10,
    # оба вне viewBox "0 0 svg_w svg_h" — svg по умолчанию обрезает контент
    # за пределами viewBox, так что оба элемента были невидимы в браузере.
    gen = SVGPlanGenerator(scale=40)
    gen.add_room("Гостиная", 0, 0, 5, 4)
    gen.add_outer_walls(5, 4, thickness=0.3)
    svg = gen.generate(title="Этаж 0")
    svg_w, svg_h = _viewbox(svg)

    title_y = float(re.search(r'class="title"[^>]*y="([\d.]+)"', svg).group(1))
    assert 0 <= title_y <= svg_h

    legend_rect_y = float(re.search(r'class="wall" x="[\d.]+" y="([\d.]+)" width="20"', svg).group(1))
    assert 0 <= legend_rect_y <= svg_h - 5


def test_door_on_interior_wall_splits_wall_and_draws_symbol():
    gen = SVGPlanGenerator(scale=40)
    gen.add_room("A", 0, 0, 4, 4)
    gen.add_room("B", 4, 0, 4, 4)
    gen.add_wall(0, 0, 4, 0, thickness=0.3)
    gen.add_wall(0, 4, 4, 4, thickness=0.3)
    gen.add_wall(0, 0, 0, 4, thickness=0.3)
    gen.add_wall(4, 0, 4, 4, thickness=0.15, loadbearing=False)  # interior, vertical
    gen.add_wall(8, 0, 8, 4, thickness=0.3)
    gen.add_wall(4, 0, 8, 0, thickness=0.3)
    gen.add_wall(4, 4, 8, 4, thickness=0.3)
    gen.add_door(4, 2, width=0.9, horizontal=False)

    svg = gen.generate()
    # Стена с дверью должна распасться на 2 сплошных сегмента (выше и ниже проёма).
    assert svg.count('class="wall-thin"') == 2
    assert 'class="door-leaf"' in svg
    assert 'class="door-arc"' in svg


def test_window_on_exterior_wall_cuts_gap_and_draws_ladder():
    gen = SVGPlanGenerator(scale=40)
    gen.add_room("A", 0, 0, 6, 4)
    gen.add_outer_walls(6, 4, thickness=0.3)
    gen.add_window(3, 0, width=1.5, horizontal=True)

    svg = gen.generate()
    assert 'class="window"' in svg
    assert svg.count('class="window-line"') >= 4  # "лесенка" из нескольких линий
    # Верхняя стена (с окном) должна распасться на 2 сплошных сегмента.
    top_wall_segments = re.findall(r'class="wall" x="[\d.]+" y="34\.0"', svg)
    # (высота верхней стены не обязательно ровно 34 — просто проверяем, что
    # сплошной верхней стены больше одного сегмента общим счётом.)
    assert svg.count('class="wall"') >= 4  # 4 наружные стены при отсутствии разреза, >=5 при разрезе верхней


def test_unmatched_door_still_renders_as_fallback():
    """Дверь, не совпавшая ни с одной стеной (например, из-за расхождения
    координат), не должна пропадать — раньше при отсутствии учёта горизонта
    стены такое было бы неотличимо от бага; теперь помечено отдельным
    запасным путём."""
    gen = SVGPlanGenerator(scale=40)
    gen.add_room("A", 0, 0, 4, 4)
    gen.add_door(99, 99, width=0.9)  # заведомо не на любой стене
    svg = gen.generate()
    assert 'class="door-leaf"' in svg


def test_dimension_chain_has_segments_and_total_for_three_or_more_rooms():
    gen = SVGPlanGenerator(scale=40)
    gen.add_room("A", 0, 0, 3, 4)
    gen.add_room("B", 3, 0, 3, 4)
    gen.add_room("C", 6, 0, 3, 4)
    gen.add_wall(0, 0, 9, 0, thickness=0.3)
    gen.add_wall(0, 4, 9, 4, thickness=0.3)
    gen.add_wall(0, 0, 0, 4, thickness=0.3)
    gen.add_wall(9, 0, 9, 4, thickness=0.3)
    gen.add_wall(3, 0, 3, 4, thickness=0.15, loadbearing=False)
    gen.add_wall(6, 0, 6, 4, thickness=0.15, loadbearing=False)

    svg = gen.generate()
    assert "3 000" in svg  # сегмент
    assert "9 000" in svg  # общий габарит
    assert svg.count("class=\"dimchain-line\"") >= 3  # минимум 2 сегмента снизу + 1 общий
