"""
Детерминированная проверка целостности IFC-модели после генерации.

Пять проверок:
  1. Нет не-фундаментных элементов ниже уровня земли (Z < -0.1 м)
  2. Шахты (IfcFlowSegment, IfcBuildingElementProxy) вертикально выровнены — X,Y
     одинаковы на всех этажах в пределах секции (допуск 5 см)
  3. Лифт присутствует на каждый подъезд при числе этажей ≥ 5
     (КМК 2.08.01-89 п.6.1)
  4. Каждый IfcOpeningElement привязан к хост-элементу через IfcRelVoidsElement
  5. Этажи (IfcBuildingStorey) расположены строго по возрастающей высоте
"""
import re
import math
from typing import Any

_TOL = 0.05   # 5 cm alignment tolerance


# ─── Placement helper ────────────────────────────────────────────────────────

def _get_xyz(product) -> tuple[float, float, float] | None:
    """Накапливает локальные размещения и возвращает абсолютный (x, y, z).
    Работает для чисто-трансляционных размещений (без поворотов) — то, что
    генерирует наш ifc_generator.
    """
    try:
        coords = [0.0, 0.0, 0.0]
        pl = product.ObjectPlacement
        while pl:
            if not pl.is_a("IfcLocalPlacement"):
                break
            rel = pl.RelativePlacement
            if rel and rel.is_a("IfcAxis2Placement3D"):
                loc = rel.Location
                if loc:
                    c = loc.Coordinates
                    for i in range(min(3, len(c))):
                        coords[i] += float(c[i])
            pl = pl.PlacementRelTo
        return (coords[0], coords[1], coords[2])
    except Exception:
        return None


def _parse_section(name: str) -> int | None:
    """'Стояк_ВК 2-3' → 2;  'Лифт 1.2' → 1;  None если не удалось."""
    m = re.search(r'(\d+)\s*[-.]', name or "")
    return int(m.group(1)) if m else None


# ─── Check 1: No below-ground non-foundation elements ────────────────────────

def _check_below_ground(ifc, issues: list) -> None:
    non_footing_types = (
        "IfcWall", "IfcSlab", "IfcColumn", "IfcBeam",
        "IfcStairFlight", "IfcWindow", "IfcDoor",
        "IfcTransportElement", "IfcFlowSegment", "IfcBuildingElementProxy",
    )
    THRESHOLD = -0.10   # 10 cm below ground — error

    for ifc_type in non_footing_types:
        for elem in ifc.by_type(ifc_type):
            xyz = _get_xyz(elem)
            if xyz and xyz[2] < THRESHOLD:
                issues.append({
                    "severity": "error",
                    "element_type": ifc_type,
                    "element_name": getattr(elem, "Name", None) or elem.GlobalId,
                    "message": (
                        f"Элемент расположен ниже нуля: Z = {xyz[2]:.3f} м. "
                        f"Не-фундаментные конструкции не должны быть ниже отметки 0.000."
                    ),
                })


# ─── Check 2: Vertical shaft alignment ───────────────────────────────────────

def _check_shaft_alignment(ifc, issues: list) -> None:
    """Для каждой секции проверяем, что X и Y шахты одинаковы на всех этажах."""
    shaft_types = ("IfcFlowSegment", "IfcBuildingElementProxy")

    for ifc_type in shaft_types:
        by_section: dict[int, list[tuple[float, float, str]]] = {}
        for elem in ifc.by_type(ifc_type):
            sec = _parse_section(getattr(elem, "Name", None) or "")
            if sec is None:
                continue
            xyz = _get_xyz(elem)
            if xyz:
                name = getattr(elem, "Name", None) or elem.GlobalId
                by_section.setdefault(sec, []).append((xyz[0], xyz[1], name))

        for sec, pts in by_section.items():
            if len(pts) < 2:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x_spread = max(xs) - min(xs)
            y_spread = max(ys) - min(ys)
            if x_spread > _TOL or y_spread > _TOL:
                issues.append({
                    "severity": "error",
                    "element_type": ifc_type,
                    "element_name": f"Секция {sec}",
                    "message": (
                        f"Шахта секции {sec} не выровнена по вертикали: "
                        f"разброс X={x_spread*1000:.0f} мм, Y={y_spread*1000:.0f} мм "
                        f"(допуск {_TOL*1000:.0f} мм). "
                        f"Инженерные стояки должны идти строго вертикально."
                    ),
                })


# ─── Check 3: Elevator coverage per entrance ─────────────────────────────────

def _check_elevator_coverage(ifc, issues: list) -> None:
    """Если здание ≥ 5 этажей, каждый подъезд должен иметь лифт."""
    storeys = ifc.by_type("IfcBuildingStorey")
    num_floors = len([s for s in storeys if s.Name != "Кровля"])
    if num_floors < 5:
        return

    # Секции по маршам лестниц
    stair_sections: set[int] = set()
    for sf in ifc.by_type("IfcStairFlight"):
        sec = _parse_section(getattr(sf, "Name", None) or "")
        if sec is not None:
            stair_sections.add(sec)

    # Секции по лифтам
    elevator_sections: set[int] = set()
    for te in ifc.by_type("IfcTransportElement"):
        pred = getattr(te, "PredefinedType", None)
        if pred == "ELEVATOR":
            sec = _parse_section(getattr(te, "Name", None) or "")
            if sec is not None:
                elevator_sections.add(sec)

    missing = stair_sections - elevator_sections
    if missing:
        for sec in sorted(missing):
            issues.append({
                "severity": "error",
                "element_type": "IfcTransportElement",
                "element_name": f"Секция {sec}",
                "message": (
                    f"Подъезд {sec}: нет лифта при {num_floors} этажах. "
                    f"КМК 2.08.01-89 п.6.1 — при этажности ≥ 5 лифт обязателен."
                ),
            })

    if stair_sections and not elevator_sections:
        # Possible single-entrance building generated by create_max_building
        elevators = ifc.by_type("IfcTransportElement")
        if not elevators:
            issues.append({
                "severity": "warning",
                "element_type": "IfcTransportElement",
                "element_name": "—",
                "message": (
                    f"Здание имеет {num_floors} этажей, но в IFC нет IfcTransportElement (лифта). "
                    f"КМК 2.08.01-89 п.6.1 требует лифт при этажности ≥ 5."
                ),
            })


# ─── Check 4: All openings have a host element ───────────────────────────────

def _check_openings_hosted(ifc, issues: list) -> None:
    """Каждый IfcOpeningElement должен быть связан с хостом через IfcRelVoidsElement."""
    hosted: set[int] = set()
    for rel in ifc.by_type("IfcRelVoidsElement"):
        hosted.add(rel.RelatedOpeningElement.id())

    orphans = 0
    for op in ifc.by_type("IfcOpeningElement"):
        if op.id() not in hosted:
            orphans += 1

    if orphans:
        issues.append({
            "severity": "error",
            "element_type": "IfcOpeningElement",
            "element_name": f"{orphans} проёмов",
            "message": (
                f"{orphans} IfcOpeningElement не привязаны к несущему элементу "
                f"через IfcRelVoidsElement. Такие проёмы не будут вычтены из стен "
                f"в BIM-программах и нарушают целостность модели."
            ),
        })


# ─── Check 5: Storey elevations are strictly ascending ───────────────────────

def _check_storey_sequence(ifc, issues: list) -> None:
    """Этажи должны идти строго снизу вверх (по атрибуту Elevation).
    Если все этажи имеют Elevation=None/0 — проверка пропускается
    (старые IFC без явного Elevation).
    """
    def storey_elev(s) -> float | None:
        try:
            v = s.Elevation
            return float(v) if v is not None else None
        except Exception:
            return None

    storeys = ifc.by_type("IfcBuildingStorey")
    elevs = [storey_elev(s) for s in storeys]

    # Skip if no storey has a non-zero Elevation (generator didn't set them)
    if not any(e is not None and e != 0.0 for e in elevs):
        return

    paired = sorted(
        [(e or 0.0, s) for s, e in zip(storeys, elevs) if e is not None],
        key=lambda x: x[0],
    )

    prev_name = ""
    prev_z = float("-inf")
    for z, s in paired:
        if z <= prev_z and z > -0.5:
            issues.append({
                "severity": "warning",
                "element_type": "IfcBuildingStorey",
                "element_name": s.Name or s.GlobalId,
                "message": (
                    f"Этаж «{s.Name}» (Elevation={z:.3f} м) расположен не выше "
                    f"предыдущего «{prev_name}» ({prev_z:.3f} м). Порядок этажей нарушен."
                ),
            })
        prev_name = s.Name or s.GlobalId
        prev_z = z


# ─── Public API ──────────────────────────────────────────────────────────────

def validate_model_integrity(ifc_path: str) -> dict:
    """
    Открывает IFC-файл и выполняет все проверки.

    Возвращает:
    {
      "ok": bool,
      "issues": [{"severity": "error"|"warning"|"info",
                  "element_type": str, "element_name": str, "message": str}],
      "summary": "X ошибок, Y предупреждений",
      "counts": {"errors": int, "warnings": int, "total_elements": int}
    }
    """
    try:
        import ifcopenshell
        ifc = ifcopenshell.open(ifc_path)
    except Exception as e:
        return {
            "ok": False,
            "issues": [{"severity": "error", "element_type": "IFC", "element_name": "файл", "message": f"Не удалось открыть IFC: {e}"}],
            "summary": "1 ошибок, 0 предупреждений",
            "counts": {"errors": 1, "warnings": 0, "total_elements": 0},
        }

    issues: list[dict] = []

    _check_below_ground(ifc, issues)
    _check_shaft_alignment(ifc, issues)
    _check_elevator_coverage(ifc, issues)
    _check_openings_hosted(ifc, issues)
    _check_storey_sequence(ifc, issues)

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")

    total_elements = len(ifc.by_type("IfcProduct"))

    return {
        "ok": errors == 0,
        "issues": issues,
        "summary": f"{errors} ошибок, {warnings} предупреждений",
        "counts": {"errors": errors, "warnings": warnings, "total_elements": total_elements},
    }
